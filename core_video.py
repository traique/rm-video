import asyncio
import json
import logging
import math
import os
import shutil
import subprocess
import urllib.parse
import urllib.request

import yt_dlp
import edge_tts

import align
import cache
import config
import metrics
import subs as subs_mod
import url_guard


def _ensure_ffmpeg():
    """Ưu tiên ffmpeg/ffprobe có sẵn trên máy; thiếu mới nhờ static-ffmpeg tải (~70MB, một lần).
    Tải lỗi thì chỉ cảnh báo: bot vẫn khởi động được, job sẽ báo lỗi rõ ràng khi cần ffmpeg."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception as e:  # mạng chặn GitHub, ổ chỉ đọc...
        logging.error("Không có ffmpeg trên máy và không tải được static-ffmpeg: %s", e)


_ensure_ffmpeg()

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# TikTok/YouTube chặn request "trông như bot" từ IP máy chủ. curl_cffi cho yt-dlp giả lập
# chữ ký mạng của Chrome thật. Thiếu curl_cffi thì chạy như cũ, không crash.
try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _IMPERSONATE = ImpersonateTarget("chrome")
except Exception:
    _IMPERSONATE = None


def _friendly(e: Exception) -> RuntimeError:
    msg = str(e)
    if "tiktok" in msg.lower():
        return RuntimeError(
            "TikTok chặn tải từ máy chủ. Cách xử lý: dán cookies TikTok vào YTDLP_COOKIES "
            "trong Cài đặt, hoặc tải video về máy rồi dùng ô upload file.")
    if "youtube" in msg.lower() and ("sign in" in msg.lower() or "not a bot" in msg.lower()):
        return RuntimeError(
            "YouTube chặn IP máy chủ (đòi xác minh không phải bot). Cách gỡ, theo thứ tự: "
            + ("" if pot_status() else "(1) gõ  setup_potoken  vào console Botkeep để cài PO token (miễn phí, có thể đủ); ")
            + "(2) điền YTDLP_PROXY (proxy dân dụng/proxy nhà) trong Cài đặt; "
            "(3) dán cookies của một tài khoản Google phụ vào YTDLP_COOKIES.")
    if "403" in msg and "forbidden" in msg.lower():
        fix = ("Đã bật PO token (" + pot_status() + ") mà vẫn bị chặn -> IP máy chủ bị YouTube gắn cờ nặng. "
               "Còn 2 cách: điền YTDLP_PROXY (proxy dân dụng) hoặc dán cookies tài khoản Google phụ vào YTDLP_COOKIES."
               if pot_status() else
               "Thử theo thứ tự: (1) gõ  setup_potoken  vào console Botkeep; (2) điền YTDLP_PROXY; "
               "(3) dán cookies tài khoản Google phụ vào YTDLP_COOKIES.")
        return RuntimeError("Máy chủ bị YouTube chặn tải (403). " + fix)
    return RuntimeError("Tải video lỗi: " + msg.replace("ERROR: ", "")[:300])


# PO token (bgutil): tải YouTube không cần cookies. Tự bật khi đã cài (lệnh console  setup_potoken )
# hoặc khi đặt POT_SERVER (server token chạy ở máy khác).
import potoken_setup
potoken_setup.ensure_path()


def _pot_script() -> str:
    """Tìm lại mỗi lần gọi: cài xong lúc bot đang chạy là dùng được ngay."""
    for p in (os.path.join(potoken_setup.POT_DIR, "build", "generate_once.js"),
              os.path.expanduser("~/bgutil/server/build/generate_once.js")):
        if os.path.exists(p) and shutil.which("node"):
            return p
    return ""


def pot_status() -> str:
    if config.POT_SERVER.strip():
        return f"server ({config.POT_SERVER.strip()})"
    return "script (node)" if _pot_script() else ""


def _add_cookies(opts: dict, tmpdir: str):
    opts["allowed_extractors"] = url_guard.YTDLP_EXTRACTORS     # không bao giờ dùng extractor generic (SSRF)
    if _IMPERSONATE:
        opts["impersonate"] = _IMPERSONATE
    ea = opts.setdefault("extractor_args", {})
    if config.POT_SERVER.strip():
        ea["youtubepot-bgutilhttp"] = {"base_url": [config.POT_SERVER.strip().rstrip("/")]}
    elif _pot_script():
        ea["youtubepot-bgutilscript"] = {"script_path": [_pot_script()]}
    if config.YTDLP_PROXY.strip():
        opts["proxy"] = config.YTDLP_PROXY.strip()
    if config.YTDLP_COOKIES.strip():
        cookie_file = os.path.join(tmpdir, "cookies.txt")
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write(config.YTDLP_COOKIES)
        opts["cookiefile"] = cookie_file


def _download_audio_sync(url: str, tmpdir: str) -> str:
    url = url_guard.check_url(url)
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "music.%(ext)s"),
        "max_filesize": 20 * 1024 * 1024,
        "match_filter": yt_dlp.utils.match_filter_func("duration <= 600"),
        "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
    }
    _add_cookies(opts, tmpdir)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from None
    path = next((os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith("music.")), None)
    if not path:
        raise RuntimeError("Tải nhạc thất bại (quá 10 phút, quá 20MB hoặc link hỏng).")
    return path


async def download_audio(url: str, tmpdir: str) -> str:
    return await asyncio.to_thread(_download_audio_sync, url, tmpdir)


# ---------- Trình tải kiểu vidssave: xem định dạng -> chọn chất lượng ----------
DL_MAX_DUR = 3600          # trình tải cho phép tới 60 phút
DL_MAX_SIZE = 450 * 1024 * 1024
_DL_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]


def _probe_formats_sync(url: str, tmpdir: str) -> dict:
    url = url_guard.check_url(url)
    opts = {"noplaylist": True, "quiet": True, "noprogress": True, "no_warnings": True, "skip_download": True}
    _add_cookies(opts, tmpdir)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from None
    fmts = info.get("formats") or []
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    auds = [f for f in fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]

    def _sz(f):
        return f.get("filesize") or f.get("filesize_approx") or 0

    best_audio = max(auds, key=_sz, default=None)
    a_sz = _sz(best_audio) if best_audio else 0
    max_h = max((f["height"] for f in vids), default=0)
    max_short = max((min(f.get("width") or f["height"], f["height"]) for f in vids), default=0)
    options = []
    for h in _DL_HEIGHTS:
        cand = [f for f in vids if f["height"] <= h]
        if not cand or (options and max(f["height"] for f in cand) == options[-1]["height"]):
            continue
        pick = max(cand, key=lambda f: (f["height"], f.get("ext") == "mp4", _sz(f)))
        size = _sz(pick) + (0 if pick.get("acodec") not in (None, "none") else a_sz)
        short = min(pick.get("width") or pick["height"], pick["height"])   # video dọc: 1080x1920 là "1080p"
        options.append({"key": str(pick["height"]), "label": f"MP4 {short}p",
                        "height": pick["height"], "size_mb": round(size / 1048576) or None})
    if best_audio or vids:
        options.append({"key": "mp3", "label": "MP3 (chỉ âm thanh)", "height": 0,
                        "size_mb": round(a_sz / 1048576) or None})
    return {"title": info.get("title") or "", "thumbnail": info.get("thumbnail") or "",
            "duration": int(info.get("duration") or 0), "uploader": info.get("uploader") or "",
            "max_height": max_h, "max_short": max_short, "options": options}


async def probe_formats(url: str, tmpdir: str) -> dict:
    return await asyncio.to_thread(_probe_formats_sync, url, tmpdir)


def _download_format_sync(url: str, tmpdir: str, quality: str, on_pct=None) -> tuple:
    """quality: 'mp3' | '2160'...'360' | '' (tốt nhất). Trả (đường dẫn, tiêu đề)."""
    url = url_guard.check_url(url)
    opts = {"outtmpl": os.path.join(tmpdir, "dl.%(ext)s"), "noplaylist": True, "quiet": True, "noprogress": True,
            "no_warnings": True, "max_filesize": DL_MAX_SIZE,
            "retries": 3, "fragment_retries": 5,
            "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {DL_MAX_DUR}")}
    if on_pct:
        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    on_pct(min(99, int(100 * (d.get("downloaded_bytes") or 0) / total)))
        opts["progress_hooks"] = [_hook]
    if quality == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        h = f"[height<={quality}]" if quality.isdigit() else ""
        opts["format"] = f"bv*{h}[ext=mp4]+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
        opts["merge_output_format"] = "mp4"
    _add_cookies(opts, tmpdir)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from None
    want = "dl.mp3" if quality == "mp3" else None
    path = next((os.path.join(tmpdir, f) for f in sorted(os.listdir(tmpdir))
                 if f.startswith("dl.") and (f == want if want else not f.endswith((".part", ".ytdl")))), None)
    if not path:
        raise RuntimeError("Tải thất bại (video quá 60 phút, quá 450MB hoặc link hỏng).")
    return path, (info.get("title") or "")


async def download_format(url: str, tmpdir: str, quality: str, on_pct=None) -> tuple:
    return await asyncio.to_thread(_download_format_sync, url, tmpdir, quality, on_pct)


def _size_limit(max_duration: int) -> int:
    """Video thường 50MB; chế độ video dài/cắt phần cho phép tới 300MB."""
    return config.MAX_FILESIZE if max_duration <= config.MAX_DURATION else config.LONG_FILESIZE


def _has_video(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return "video" in out


def _tikwm_sync(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    """Dự phòng cho TikTok: TikTok chỉ trả file nhạc cho IP máy chủ, giấu video.
    tikwm.com là dịch vụ trung gian (giống snaptik) trả link video HD không logo.
    Dịch vụ bên thứ ba: có thể đổi/ngừng bất cứ lúc nào."""
    api = "https://www.tikwm.com/api/?hd=1&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    data = d.get("data") or {}
    if d.get("code") != 0 or not (data.get("hdplay") or data.get("play")):
        raise RuntimeError(f"tikwm: {d.get('msg') or 'không có video'}")
    if data.get("images"):
        raise RuntimeError("Bài TikTok này là ảnh trượt, không có video.")
    if (data.get("duration") or 0) > max_duration:
        raise RuntimeError(f"Video quá dài (> {max_duration}s).")
    path = os.path.join(tmpdir, "input.mp4")
    media = url_guard.check_url(data.get("hdplay") or data.get("play"), any_domain=True)   # không tin link bên thứ ba
    req = urllib.request.Request(media, headers={"User-Agent": "Mozilla/5.0"})
    size = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 20):
            size += len(chunk)
            if size > _size_limit(max_duration):
                raise RuntimeError(f"Video quá {_size_limit(max_duration) // 1048576}MB.")
            f.write(chunk)
    title = data.get("title") or ""
    author = data.get("author") or {}
    meta = {"uploader": author.get("unique_id") or author.get("nickname") or "", "webpage_url": url}
    return path, title, title, meta


def _download_sync(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    """Trả (đường dẫn, title, description, meta). meta: uploader/kênh/link gốc để ghi nguồn."""
    url = url_guard.check_url(url)
    try:
        path, title, desc, meta = _ytdlp_video_sync(url, tmpdir, max_duration)
        if _has_video(path):
            metrics.record("download", "yt-dlp")
            return path, title, desc, meta
        os.remove(path)
        reason = "TikTok chỉ trả về nhạc, giấu video"
    except RuntimeError as e:
        reason = str(e)
    metrics.record("download", "yt-dlp", ok=False, detail=reason)
    if "tiktok.com" in url.lower():
        try:
            out = _tikwm_sync(url, tmpdir, max_duration)
            metrics.record("download", "tikwm", fallback=True)
            return out
        except Exception as e2:
            metrics.record("download", "tikwm", ok=False, fallback=True, detail=str(e2))
            raise RuntimeError(
                f"Không tải được video TikTok ({reason}; dự phòng tikwm: {e2}). "
                "Hãy tải video về máy rồi dùng ô upload file.") from None
    raise RuntimeError(reason)


def _ytdlp_video_sync(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    opts = {
        # giới hạn theo CẠNH NGẮN (<=1080) để video dọc 1080x1920 không bị loại như khi lọc height<=720
        "format": ("bv*[width<=1080][height<=1920][ext=mp4]+ba[ext=m4a]/"
                   "bv*[width<=1080][height<=1920]+ba/b[width<=1080][height<=1920]/b"),
        "outtmpl": os.path.join(tmpdir, "input.%(ext)s"),
        "max_filesize": _size_limit(max_duration),
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {max_duration}"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True, "noprogress": True,
        "no_warnings": True,
    }
    _add_cookies(opts, tmpdir)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from None

    path = next(
        (os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith("input.")),
        None,
    )
    if not path:
        dur = (info or {}).get("duration") or 0
        size = (info or {}).get("filesize") or (info or {}).get("filesize_approx") or 0
        if dur and dur > max_duration:
            raise RuntimeError(f"Video dài {dur:.0f}s, vượt giới hạn {max_duration}s. "
                               "Hãy cắt ngắn rồi upload file, hoặc dùng chế độ ⬇️ Chỉ tải về (tối đa 10 phút).")
        if size and size > _size_limit(max_duration):
            raise RuntimeError(f"Video nặng {size / 1048576:.0f}MB, vượt giới hạn {_size_limit(max_duration) // 1048576}MB.")
        raise RuntimeError("Tải video thất bại (link riêng tư, đã xóa, hoặc nền tảng chặn). "
                           "Hãy tải video về máy rồi upload file.")
    meta = {"uploader": info.get("uploader") or info.get("channel") or "",
            "uploader_id": info.get("uploader_id") or info.get("channel_id") or "",
            "webpage_url": info.get("webpage_url") or url}
    return path, (info.get("title") or ""), (info.get("description") or ""), meta


async def download(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    """Trả về (đường dẫn file, title, description, meta). Chạy trong thread vì yt-dlp blocking."""
    return await asyncio.to_thread(_download_sync, url, tmpdir, max_duration)


async def keyframe_times(path: str) -> list:
    """Mốc các khung khoá (I-frame) của luồng hình, tính từ đầu file. Chỉ giải mã khung khoá nên nhanh."""
    try:
        out = await _run_ffmpeg("-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
                                "frame=pts_time", "-of", "csv=p=0", path, tool="ffprobe")
        st = await _run_ffmpeg("-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", path,
                               tool="ffprobe")
        t0 = float(st.strip() or 0)
    except (RuntimeError, ValueError):
        return []
    out_t = []
    for x in out.split():
        try:
            out_t.append(round(float(x.strip(",")) - t0, 6))    # đủ chính xác: làm tròn 3 số có thể rơi trước khung khoá
        except ValueError:
            continue
    return sorted(set(out_t))


def snap_to_keyframes(bounds: list, kfs: list, tol: float = 0.4) -> list:
    """Dời các điểm cắt (trừ đầu/cuối) về khung khoá gần nhất nếu lệch ≤ tol giây: cắt đúng khung khoá thì
    chép luồng được, không phải mã hoá lại. Điểm cắt chọn ở chỗ chuyển cảnh/ngưng nói, mà bộ mã hoá thường đặt
    khung khoá ngay chỗ chuyển cảnh, nên phần lớn điểm cắt dời rất ít."""
    if not kfs or len(bounds) < 3:
        return list(bounds)
    out = [bounds[0]]
    for b in bounds[1:-1]:
        k = min(kfs, key=lambda x: abs(x - b))
        out.append(k if abs(k - b) <= tol and out[-1] < k < bounds[-1] else b)
    out.append(bounds[-1])
    return out


def is_keyframe(t: float, kfs: list, tol: float = 0.0005) -> bool:
    return t <= tol or any(abs(t - k) <= tol for k in kfs)


async def cut(src: str, dst: str, start: float, end: float, copy: bool = False) -> str:
    """Cắt đoạn [start, end]. copy=True (start là khung khoá): chép luồng, không mã hoá lại — nhanh và không mất
    thêm một thế hệ nén; kiểm độ dài, lệch thì mã hoá lại như cũ. Trả "copy" hoặc "encode"."""
    if copy:
        try:
            # -ss nhích 0.5ms SAU khung khoá: ffmpeg lùi về đúng khung khoá đó, không lùi nhầm về khung khoá trước
            await _run_ffmpeg("-y", "-ss", f"{start + 0.0005:.4f}", "-i", src, "-t", f"{end - start:.4f}", "-map", "0:v:0",
                              "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero",
                              "-movflags", "+faststart", dst)
            if abs(await probe_duration(dst) - (end - start)) < 0.15:
                return "copy"
            logging.warning("Cắt chép luồng lệch độ dài, mã hoá lại đoạn %.1f-%.1f", start, end)
        except (RuntimeError, ValueError) as e:
            logging.warning("Cắt chép luồng lỗi (%s), mã hoá lại", str(e)[:80])
    await _run_ffmpeg("-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k",
                      "-movflags", "+faststart", dst)
    return "encode"


async def silences(path: str, noise_db: int = -35, min_len: float = 0.35) -> list:
    """Các khoảng im lặng [(start, end)] — chỗ người nói ngưng, hợp để cắt phần."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_len}",
        "-f", "null", "-", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    import re as _re
    st = [float(x) for x in _re.findall(r"silence_start: ([0-9.]+)", err.decode(errors="ignore"))]
    en = [float(x) for x in _re.findall(r"silence_end: ([0-9.]+)", err.decode(errors="ignore"))]
    return list(zip(st, en))


async def remux(src: str, dst: str):
    """Chế độ chỉ tải về: không encode lại, chỉ đóng gói mp4 + faststart để xem/gửi được ngay."""
    await _run_ffmpeg("-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst)


_EDGE_VOICES = {config.VOICE_FEMALE, config.VOICE_MALE}


def _edge_fallback_voice(voice: str) -> str:
    if voice in _EDGE_VOICES:
        return voice
    return config.VOICE_MALE if ("male" in voice and "female" not in voice) else config.VOICE_FEMALE


async def tts(text: str, mp3_path: str, voice: str, ass_path: str | None = None,
              pos_y: int | None = None) -> bool:
    """Đọc giọng AI + mốc từng từ cho phụ đề. Giọng CapCut (API không chính thức) đi trước,
    lỗi gì cũng rơi về edge-tts để job không bao giờ hỏng. Trả về True nếu ghi được phụ đề."""
    if config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES:
        try:
            import tts_capcut
            words = await tts_capcut.speak(text, voice, mp3_path, want_words=bool(ass_path))
            metrics.record("tts", "capcut")
            if ass_path and words:
                subs_mod.write_ass(words, ass_path, pos_y, config.SUB_STYLE)
                return True
            return False
        except Exception as e:
            metrics.record("tts", "capcut", ok=False, detail=str(e))
            logging.warning("Giọng CapCut lỗi (%s), chuyển sang edge-tts", str(e)[:150])
    return await _tts_edge(text, mp3_path, _edge_fallback_voice(voice), ass_path, pos_y)


EDGE_TRIES = 3


async def _edge_once(text: str, mp3_path: str, voice: str) -> list:
    com = edge_tts.Communicate(text, voice, rate=config.TTS_RATE, pitch=config.TTS_PITCH,
                               boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


async def _edge_words(text: str, mp3_path: str, voice: str, fallback: bool = False) -> list:
    """edge-tts có lỗi NoAudioReceived chập chờn (edge-tts 7.3.x, 10/2026): thử lại tối đa 3 lần."""
    for attempt in range(EDGE_TRIES):
        try:
            words = await _edge_once(text, mp3_path, voice)
            metrics.record("tts", "edge", fallback=fallback)
            return words
        except Exception as e:
            if attempt == EDGE_TRIES - 1:
                metrics.record("tts", "edge", ok=False, fallback=fallback, detail=str(e))
                raise
            logging.warning("edge-tts lỗi (%s), thử lại", str(e)[:80])
            await asyncio.sleep(1.5 * (attempt + 1))
    return []


async def _tts_edge(text: str, mp3_path: str, voice: str, ass_path: str | None,
                    pos_y: int | None) -> bool:
    words = await _edge_words(text, mp3_path, voice)
    if ass_path and words:
        subs_mod.write_ass(words, ass_path, pos_y, config.SUB_STYLE)
        return True
    return False


# ---------- Giọng khớp mốc thời gian: đọc từng đoạn, đặt đúng chỗ ----------

async def synth_clip_ex(text: str, voice: str, path: str) -> tuple:
    """TTS 1 đoạn. Trả (mốc từ hoặc None, provider đã đọc: capcut | edge | piper). CapCut/Piper không có mốc từ:
    phụ đề rải theo âm tiết."""
    capcut = config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES
    if capcut:
        try:
            import tts_capcut
            await tts_capcut.speak(text, voice, path, want_words=False)
            metrics.record("tts", "capcut")
            return None, "capcut"
        except Exception as e:
            metrics.record("tts", "capcut", ok=False, detail=str(e))
            logging.warning("CapCut lỗi đoạn (%s), dùng edge-tts", str(e)[:100])
    try:
        return await _edge_words(text, path, _edge_fallback_voice(voice), fallback=capcut), "edge"
    except Exception:
        if config.TTS_OFFLINE != "1":
            raise
    import offline                             # cả hai dịch vụ ngoài đều lỗi: giọng offline Piper (nếu đã cài)
    try:
        await offline.speak(text, path)
        metrics.record("tts", "piper", fallback=True)
        return None, "piper"
    except Exception as e:
        metrics.record("tts", "piper", ok=False, fallback=True, detail=str(e))
        raise


async def synth_clip(text: str, voice: str, path: str):
    """Như synth_clip_ex nhưng chỉ trả mốc từ (giữ tương thích)."""
    return (await synth_clip_ex(text, voice, path))[0]


async def synth_clip_cached(text: str, voice: str, path: str):
    """synth_clip có cache: cùng chữ + giọng + tốc độ + cao độ thì dùng lại file cũ (render lại sau khi duyệt
    chỉ đọc lại các đoạn đã sửa). Chỉ cache khi provider CHÍNH đọc được: đoạn rơi về edge-tts vì CapCut lỗi
    sẽ được đọc lại bằng CapCut ở lần sau, không bị kẹt giọng dự phòng."""
    primary = "capcut" if (config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES) else "edge"
    tune = (config.TTS_RATE, config.TTS_PITCH) if primary == "edge" else ()
    k = cache.key("tts", primary, voice, tune, text)
    hit, meta = cache.get("tts", k, ".mp3"), cache.get_json("tts", k)
    if hit and meta is not None:
        await asyncio.to_thread(shutil.copyfile, hit, path)
        return meta.get("words")
    words, used = await synth_clip_ex(text, voice, path)
    if used == primary and os.path.exists(path) and os.path.getsize(path) > 0:     # Piper/edge dự phòng: không cache
        try:
            cache.put("tts", k, path, ".mp3")
            cache.put_json("tts", k, {"words": words})
        except OSError:
            pass
    return words


def speech_windows(speech: list, total: float, pad: float = 0.3, merge: float = 0.8) -> list:
    """Khoảng người gốc nói (đệm `pad`, gộp các khoảng cách nhau < `merge`), cắt trong [0, total]."""
    out = []
    for a, b in sorted((max(0.0, a - pad), min(total, b + pad)) for a, b in speech):
        if b <= a:
            continue
        if out and a - out[-1][1] < merge:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def plan_voice(durs: list, segs: list, total_dur: float):
    """Xếp các đoạn giọng lên trục thời gian. durs: độ dài giọng gốc từng đoạn;
    segs: [{start, slot, span}]. Không bao giờ cắt cụt chữ: thứ tự ưu tiên khi đoạn dài hơn khe:
    (1) bắt đầu sớm hơn tối đa 0.5s nếu đoạn trước đã xong, (2) nói nhanh hơn tới 1.25x,
    (3) lấn sang khe sau tối đa 0.35s (đoạn sau lùi theo), (4) báo "quá dài" để AI viết ngắn lại.
    Trả về (placements [{start, factor, end}], danh sách chỉ số đoạn quá dài)."""
    plan, too_long, prev_end = [], [], 0.0
    for i, (d, s) in enumerate(zip(durs, segs)):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else total_dur
        if s.get("ost") == "orig":           # đoạn giữ nguyên tiếng gốc: không đọc, đoạn sau không được mượn lùi vào
            plan.append({"start": round(s["start"], 3), "factor": 1.0, "end": round(s["start"], 3)})
            prev_end = max(prev_end, nxt - 0.12)
            continue
        into_orig = i + 1 < len(segs) and segs[i + 1].get("ost") == "orig"   # không lấn sang đoạn tiếng gốc
        start = max(s["start"], prev_end + 0.12)
        want = max(0.5, min(s.get("span", s["slot"]), nxt - start))
        f = max(0.92, min(d / want, 1.1))          # vùng tự nhiên: hầu như không nghe ra co giãn
        if start + d / f > nxt:                     # (1) mượn khoảng lặng phía trước
            start = max(prev_end + 0.12, s["start"] - 0.5, min(start, nxt - d / f))
        if start + d / f > nxt:                     # (2) nói nhanh hơn
            f = min(d / max(0.3, nxt - start), 1.25)
        limit = (nxt + (0.0 if into_orig else 0.35)) if i + 1 < len(segs) else total_dur - 0.05
        if start + d / f > limit:                   # (3) vẫn tràn quá giới hạn lấn
            too_long.append(i)
            f = min(d / max(0.3, limit - start), 1.35)
        end = start + d / f
        plan.append({"start": round(start, 3), "factor": round(f, 4), "end": round(end, 3)})
        prev_end = end
    return plan, too_long


async def compose_voice(clips: list, plan: list, out_wav: str, total_dur: float):
    """Ghép giọng theo plan. Chỉ cắt ở cuối video (đoạn cuối), có fade để không cụt gắt.
    Đoạn giữ tiếng gốc (không có file giọng) được bỏ qua; không còn đoạn nào thì ra track im lặng."""
    pairs = [(cl, p) for cl, p in zip(clips, plan) if cl.get("path")]
    if not pairs:
        await _run_ffmpeg("-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{total_dur:.2f}", out_wav)
        return
    inputs, filters, labels = [], [], []
    for i, (cl, p) in enumerate(pairs):
        inputs += ["-i", cl["path"]]
        trim = max(0.3, total_dur - p["start"])
        filters.append(
            f"[{i}:a]aformat=sample_rates=44100:channel_layouts=mono,atempo={p['factor']:.4f},"
            f"atrim=0:{trim:.2f},adelay={int(p['start'] * 1000)}[c{i}]")
        labels.append(f"[c{i}]")
    fc = ";".join(filters) + ";" + "".join(labels) + \
        f"amix=inputs={len(pairs)}:normalize=0,apad=whole_dur={total_dur:.2f}," \
        f"afade=t=out:st={max(0.0, total_dur - 0.25):.2f}:d=0.25[a]"
    await _run_ffmpeg("-y", *inputs, "-filter_complex", fc, "-map", "[a]",
                      "-ar", "44100", "-ac", "1", "-t", f"{total_dur:.2f}", out_wav)


def _map_script(script: str, timed: list, s: float, e: float, clip: int) -> list:
    """Chữ kịch bản + mốc đo được của MỘT đoạn giọng -> [(start, end, word, clip)] (xem align.map_script)."""
    return [(a, b, w, clip) for a, b, w in align.map_script(script, timed, s, e)]


async def words_for_track(clips: list, plan: list, track_path: str, texts: list) -> list:
    """Mốc từng từ cho phụ đề, chữ luôn lấy nguyên văn kịch bản (texts[i] của đoạn i).
    Đoạn đọc bằng edge-tts có mốc từng từ thật. Đoạn CapCut (không có mốc): mặc định rải chữ theo âm tiết
    trong chính đoạn đó (1-4 giây nên đủ khớp, không tốn thêm lượt mạng); SUB_TIMING=stt thì nghe lại
    cả track bằng STT CapCut như bản cũ."""
    per_clip = [[] for _ in clips]
    for i, (cl, p) in enumerate(zip(clips, plan)):
        if cl.get("words"):
            per_clip[i] = [(p["start"] + a / p["factor"], p["start"] + b / p["factor"], w) for a, b, w in cl["words"]]
    missing = {i for i, cl in enumerate(clips) if cl.get("words") is None}
    if missing and config.SUB_TIMING == "stt":
        try:
            import tts_capcut
            heard = await asyncio.to_thread(tts_capcut.stt_words, track_path)
        except Exception as ex:
            logging.warning("Không nghe lại được track, rải phụ đề theo âm tiết: %s", str(ex)[:100])
            heard = []
        for a, b, w in heard:
            mid = (a + b) / 2
            for i, p in enumerate(plan):
                if p["start"] - 0.15 <= mid <= p["end"] + 0.15:
                    if i in missing:
                        per_clip[i].append((a, b, w))
                    break
    out = []
    for i, p in enumerate(plan):
        out += _map_script(texts[i], per_clip[i], p["start"], p["end"], i)
    return out


RUNNING: set = set()      # tiến trình con đang chạy (ffmpeg/ffprobe) để hủy job thì giết được ngay


def kill_running():
    for p in list(RUNNING):
        try:
            p.kill()
        except Exception:
            pass
    RUNNING.clear()


async def _run_ffmpeg(*args: str, tool: str = "ffmpeg"):
    proc = await asyncio.create_subprocess_exec(
        tool, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    RUNNING.add(proc)
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        raise
    finally:
        RUNNING.discard(proc)
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} lỗi: " + err.decode(errors="ignore")[-400:])
    return out.decode(errors="ignore")


async def probe_duration(path: str) -> float:
    out = await _run_ffmpeg(
        "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
        tool="ffprobe",
    )
    return float(out.strip())


async def has_audio(path: str) -> bool:
    out = await _run_ffmpeg(
        "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", path, tool="ffprobe",
    )
    return "audio" in out


async def extract_audio(input_mp4: str, out_mp3: str):
    """Bóc audio 16kHz mono 32kbps cho Whisper: 90s ≈ 0.4MB, xa hạn 25MB của Groq."""
    await _run_ffmpeg(
        "-y", "-i", input_mp4, "-t", str(config.HARD_MAX), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", out_mp3
    )


def band_to_output(top: float, bottom: float) -> tuple[int, int]:
    """Dải (tỉ lệ 0..1 trên video gốc) -> (y, h) pixel trên khung 720x1280 sau crop 4%.
    Nới mỗi bên 1.2% và cao tối thiểu 96px để che trọn chữ gốc + đủ chỗ cho chữ mới."""
    y0 = int(((top - 0.012) - 0.02) / 0.96 * 1280)
    y1 = int(((bottom + 0.012) - 0.02) / 0.96 * 1280)
    if y1 - y0 < 96:
        mid = (y0 + y1) // 2
        y0, y1 = mid - 48, mid + 48
    y0, y1 = max(0, y0), min(1280, y1)
    return y0, max(2, y1 - y0)


def _cover_filter(bands, W: int = 720, H: int = 1280) -> str:
    """Che từng dải chữ gốc: làm mờ mạnh + phủ tối 45% (nhìn như nền phụ đề, không lộ chữ cũ)."""
    out, k = "", H / 1280
    for i, (top, bottom) in enumerate(bands):
        y, h = band_to_output(top, bottom)
        y, h = int(y * k), int(h * k)
        out += (f",split=2[cv{i}a][cv{i}b];[cv{i}b]crop={W}:{h}:0:{y},boxblur={int(20 * k)}:4[cv{i}bl];"
                f"[cv{i}a][cv{i}bl]overlay=0:{y},drawbox=x=0:y={y}:w={W}:h={h}:color=black@0.45:t=fill")
    return out


# Giọng TTS nghe "máy" vì: quá khô (như phòng cách âm), phụ âm s/x chói, âm lượng đều tăm tắp,
# tách rời không gian cảnh quay. Chuỗi này: bỏ ù thấp -> ấm vùng 230Hz -> bớt chói 3.8kHz ->
# khử xì (de-ess) -> nén nhẹ cho đều như thu micro thật -> phản xạ phòng bếp rất ngắn.
VOICE_FX = ("highpass=f=75,equalizer=f=230:t=q:w=1.1:g=1.8,equalizer=f=3800:t=q:w=1.6:g=-2.2,"
            "deesser=i=0.35:m=0.5:f=0.5,acompressor=threshold=0.1:ratio=2.5:attack=12:release=140:makeup=1.3,"
            "aecho=0.9:0.55:13|22|34:0.10|0.07|0.04,")


def _voice_fx() -> str:
    return VOICE_FX if config.VOICE_HUMANIZE != "0" else ""


GATE_PAD, GATE_MERGE, GATE_RAMP = 0.2, 0.8, 0.15
GATE_FLOOR = 0.0   # KHÔNG giữ lớp nền khi người gốc nói: đo được lọc 3kHz ở 12% vẫn để máy nghe chép nguyên câu gốc


def _gate_windows(speech: list) -> list:
    """Khoảng người gốc nói (đệm 0.2s), gộp các khoảng cách nhau < 0.8s để tiếng nền không chập chờn."""
    out = []
    for a, b in sorted((max(0.0, a - GATE_PAD), b + GATE_PAD) for a, b in speech[:2000]):
        if out and a - out[-1][1] < GATE_MERGE:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _gate_w(speech: list) -> str:
    """Biểu thức 0..1 theo thời gian: 1 khi người gốc nói, lên/xuống dần trong 0.15s (không cắt phụt)."""
    r = GATE_RAMP
    terms = "+".join(f"clip((t-{a:.2f}+{r})/{r},0,1)*clip(({b:.2f}+{r}-t)/{r},0,1)" for a, b in _gate_windows(speech))
    return f"min(1,{terms or '0'})"


def _gate_chain(speech: list, src: str) -> str:
    """Tiếng nền khi KHÔNG tách giọng AI: lúc người gốc nói thì tắt dần phần đầy đủ, chỉ giữ lớp tiếng xèo đã lọc
    dưới 3kHz (dải giọng người) ở mức GATE_FLOOR; hết nói thì hiện lại dần. Bản cũ tắt/bật 0<->1 tức thì:
    đo được 202 lần nhảy ≥15dB/0.1s trong 95s, có quãng im bặt -> nghe khựng như video bị lag."""
    w = _gate_w(speech)
    return (f"{src}aformat=sample_rates=44100:channel_layouts=stereo,asplit=2[gf][gh];"
            f"[gf]volume='1-{w}':eval=frame[gf2];"
            f"[gh]highpass=f=3000,highpass=f=3000,volume='{GATE_FLOOR}*{w}':eval=frame[gh2];"
            "[gf2][gh2]amix=inputs=2:normalize=0,")


OST_LEVEL = {"orig": 1.0, "both": 0.35}     # mức tiếng gốc: giữ nguyên / lót nhỏ dưới giọng đọc mới
OST_RAMP = 0.2


def ost_windows(segs: list, out_dur: float) -> list:
    """Đoạn đánh dấu giữ tiếng gốc -> [(đầu, cuối, mức)]: 'orig' = chỉ tiếng gốc (cả lời người gốc), không đọc
    lời mới; 'both' = đọc lời mới, tiếng gốc lót nhỏ phía dưới. Cuối = đầu đoạn kế (hoặc hết video)."""
    out = []
    for k, sg in enumerate(segs):
        lvl = OST_LEVEL.get(sg.get("ost", "new"))
        if lvl is None:
            continue
        a = float(sg["start"])
        b = float(segs[k + 1]["start"]) if k + 1 < len(segs) else out_dur
        if min(b, out_dur) > a:
            out.append((round(a, 2), round(min(b, out_dur), 2), lvl))
    return out


def _trap(a: float, b: float, r: float = OST_RAMP) -> str:
    """1 trong [a, b], vuốt lên/xuống r giây NẰM TRONG cửa sổ (không lấn sang đoạn giọng mới hai bên)."""
    r = max(0.01, min(r, (b - a) / 2))
    return f"clip((t-{a:.2f})/{r:.3f},0,1)*clip(({b:.2f}-t)/{r:.3f},0,1)"


def _ost_level_expr(wins: list) -> str:
    """Biểu thức âm lượng tiếng gốc theo thời gian (vuốt 0.2s ở hai đầu mỗi cửa sổ)."""
    return "min(1," + ("+".join(f"{lvl}*{_trap(a, b)}" for a, b, lvl in wins) or "0") + ")"


def _ost_any_expr(wins: list) -> str:
    return "min(1," + ("+".join(_trap(a, b) for a, b, _ in wins) or "0") + ")"


def out_fps(src_fps: float) -> float:
    """Tốc độ khung đầu ra: nguồn ≤30 giữ nguyên (không chèn khung lặp); >30 chia ĐỀU
    (50->25, 60->30, 48->24). Ép 50->30 phải lấy khung nhịp 2-1-2-2-1 -> chuyển động giật."""
    if not src_fps or src_fps <= 0 or src_fps > 240:
        return 30.0
    if src_fps <= 30.5:
        return round(src_fps, 3)
    n = math.ceil(src_fps / 30.0 - 0.02)
    return round(src_fps / n, 3)


def kelvin_gains(kelvin: float, mix: float) -> tuple:
    """Hệ số nhân từng kênh R, G, B của filter colortemperature (công thức kelvin2rgb của FFmpeg, pl=0):
    out = c * (1 - mix + mix * k_c). Với pl=0 nó chỉ là nhân hằng số từng kênh."""
    k = kelvin / 100.0
    sat = lambda v: min(1.0, max(0.0, v))   # noqa: E731
    if k <= 66:
        rgb = [1.0, sat(0.39008157876901960784 * math.log(k) - 0.63184144378862745098)]
    else:
        t = max(k - 60.0, 0.0)
        rgb = [sat(1.29293618606274509804 * t ** -0.1332047592), sat(1.12989086089529411765 * t ** -0.0755148492)]
    rgb.append(1.0 if k >= 66 else 0.0 if k <= 19 else sat(0.54320678911019607843 * math.log(k - 10) - 1.19625408914))
    return tuple(1 - mix + mix * c for c in rgb)


def warm_filter(kelvin: float = 6000, mix: float = 0.35) -> str:
    """Làm ấm màu: KẾT QUẢ NHƯ colortemperature=temperature=6000:mix=0.35 (đo SSIM 0.99987, PSNR 55dB) nhưng bằng
    bảng tra 1 chiều lutrgb thay vì tính số thực từng điểm ảnh: phần lọc hình 1080p nhanh hơn ~20% trên 1 lõi."""
    gains = kelvin_gains(kelvin, mix)
    parts = [f"{ch}='clip(val*{g:.8f}+0.5\\,0\\,255)'" for ch, g in zip("rgb", gains) if abs(g - 1) > 1e-6]
    return ("lutrgb=" + ":".join(parts) + ",") if parts else ""


def build_filter(mode: str, out_dur: float, ambient: bool, ass_path: str | None,
                 cover_bands=None, W: int = 720, H: int = 1280,
                 ambient_src: str = "[0:a]", speech_gate=None,
                 fps: float = 30.0, src_w: int = 0, src_h: int = 0, stretch: float = 1.0,
                 ost=None, orig_audio: bool = True) -> str:
    """Hình đẹp hơn bản gốc: khử nhiễu nén (hqdn3d) -> scale lanczos -> tương phản/sáng nhẹ ->
    vibrance (tăng màu nhạt, không làm cháy màu da) -> ấm nhẹ cho món ăn -> làm nét thích ứng (cas)."""
    cook = mode == "cook"
    # fps=30 ĐẦU TIÊN: nguồn TikTok HD thường 50-60fps, lọc trước khi bớt khung là phí 67-100%
    # Công thức màu GIỮ NGUYÊN bản người dùng đã duyệt: vibrance chỉ tăng màu vùng còn nhạt, chừa vùng đã đậm
    # (món chiên vàng cam). Bản thử eq saturation=1.2 tăng đều mọi thứ -> gà rang muối cam loang, bị chê -> bỏ.
    look = ("hqdn3d=1.2:1.2:3:3,"
            f"eq=contrast=1.05:brightness=0.012:saturation={'1.06' if cook else '1.03'},"
            f"vibrance=intensity={'0.2' if cook else '0.1'},"
            + (warm_filter(6000, 0.35) if cook else ""))
    size = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
    # Nguồn NHỎ hơn đầu ra (vd 360x640 -> 720x1280): khử nhiễu + chỉnh màu ở kích thước gốc rồi mới phóng to
    # (ít điểm ảnh hơn 4 lần; khử nhiễu trên điểm ảnh thật tốt hơn trên điểm ảnh nội suy).
    upscale = bool(src_w and src_h) and max(W / (src_w * 0.96), H / (src_h * 0.96)) > 1.05
    # stretch > 1: làm chậm hình ngay trong lần render này (trước đây stretch() mã hoá cả video một lần riêng)
    slow = f"setpts=PTS*{stretch:.4f}," if abs(stretch - 1) > 1e-3 else ""
    orig_src = "[0:a]"
    if slow:
        ambient_src += f"atempo={1 / stretch:.4f},"          # tiếng nền chậm theo hình (tiếng gốc hoặc đã tách giọng)
        orig_src += f"atempo={1 / stretch:.4f},"
    ost = [w for w in (ost or []) if w[1] > w[0]] if orig_audio else []
    # tiếng gốc NGUYÊN BẢN (cả lời người gốc) chỉ trong các cửa sổ được đánh dấu giữ tiếng gốc
    ost_chain = (f"{orig_src}aformat=sample_rates=44100:channel_layouts=stereo,"
                 f"volume='{_ost_level_expr(ost)}':eval=frame[ost];") if ost else ""
    v = f"[0:v]{slow}fps={fps:g},crop=in_w*0.96:in_h*0.96," + (look + size if upscale else size + look)
    if config.QUALITY == "high":
        v += "cas=strength=0.5"              # làm nét thích ứng: đẹp nhất, tốn ~2.7x unsharp
    else:
        v += "unsharp=3:3:0.6:3:3:0"         # làm nét kênh sáng; đo: SSIM 0.984-0.988 so với bản cas
    v += _cover_filter(cover_bands or [], W, H)
    if ass_path:
        v += f",subtitles=filename={ass_path}:fontsdir={ASSETS_DIR}"
    v += "[v];"
    if mode == "cook" and ambient:
        # highpass 3kHz giữ tiếng xèo xèo; sidechaincompress tự hạ tiếng nền khi giọng đọc
        # vang lên (ducking như video chuyên nghiệp); loudnorm chuẩn hóa âm lượng -16 LUFS
        a = (
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo," + _voice_fx() + "asplit=2[vo][sc];"
            + (_gate_chain(speech_gate, ambient_src) if speech_gate else
               f"{ambient_src}aformat=sample_rates=44100:channel_layouts=stereo,")
            + "volume=0.8"
            # trong cửa sổ giữ tiếng gốc thì tắt lớp tiếng nền (tiếng gốc đã có tiếng nền, tránh nghe gấp đôi)
            + (f",volume='1-{_ost_any_expr(ost)}':eval=frame" if ost else "") + "[amb];"
            "[amb][sc]sidechaincompress=threshold=0.02:ratio=6:attack=100:release=500[ambd];"
            + ost_chain
            + ("[vo][ambd][ost]amix=inputs=3:duration=longest:weights='1 0.5 1'," if ost else
               "[vo][ambd]amix=inputs=2:duration=longest:weights='1 0.5',")
            + "loudnorm=I=-14:TP=-1.0:LRA=9,aresample=48000[a]"
        )
    elif ost:
        # giọng mới + tiếng gốc trong các cửa sổ giữ tiếng gốc
        fx = _voice_fx().rstrip(",") or "anull"
        a = (
            f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,{fx}[vo];" + ost_chain
            + "[vo][ost]amix=inputs=2:duration=longest:normalize=0,"
            f"loudnorm=I=-14:TP=-1.0:LRA=9,aresample=48000,apad=whole_dur={out_dur:.2f}[a]"
        )
    else:
        # chỉ giọng AI; apad hữu hạn đệm im lặng tới hết video
        # (apad vô hạn + -shortest từng gây treo/OOM, nên luôn pad có giới hạn và cắt bằng -t)
        a = (
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo," + _voice_fx() +
            f"loudnorm=I=-14:TP=-1.0:LRA=9,aresample=48000,apad=whole_dur={out_dur:.2f}[a]"
        )
    return v + a


def cgroup_cpus() -> float:
    """Số lõi CPU container thật sự được dùng (cgroup v2 cpu.max / v1 cfs_quota), không phải lõi máy chủ."""
    try:
        q, per = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
        if q != "max":
            return max(0.1, int(q) / int(per))
    except (OSError, ValueError):
        pass
    try:
        q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
        per = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if q > 0:
            return max(0.1, q / per)
    except (OSError, ValueError):
        pass
    return float(os.cpu_count() or 1)


def ffmpeg_threads() -> int:
    """FFMPEG_THREADS > 0 thì dùng đúng số đó; 0 = theo số lõi container, làm tròn lên (1.5 vCPU -> 2), tối đa 8.
    Trước đây viết cứng -threads 1: sang máy nhiều lõi vẫn chạy một lõi."""
    if config.FFMPEG_THREADS > 0:
        return config.FFMPEG_THREADS
    return max(1, min(8, math.ceil(cgroup_cpus() - 0.01)))


def thread_args() -> list:
    """Đặt TRƯỚC -i: số luồng bộ lọc (toàn cục) + luồng giải mã đầu vào chính."""
    n = str(ffmpeg_threads())
    return ["-filter_complex_threads", n, "-threads", n]


def encode_args(H: int, fps: float = 30.0) -> list:
    """Thông số x264/AAC theo QUALITY và độ phân giải. high = chuẩn TVC: preset medium, crf 18,
    1080p ~10 Mbps; balanced = veryfast/crf 20; fast = superfast/crf 23. Luôn: High 4.1, bt709,
    GOP 2s (60 khung) cho phát trực tuyến, AAC 192k 48 kHz."""
    q = config.QUALITY
    preset, crf = {"fast": ("superfast", 22), "high": ("medium", 18)}.get(q, ("veryfast", 20))
    if H >= 1920:
        maxrate, buf = {"fast": ("5000k", "8000k"), "high": ("12000k", "20000k")}.get(q, ("9000k", "14000k"))
    else:
        maxrate, buf = {"fast": ("2500k", "4000k"), "high": ("6000k", "10000k")}.get(q, ("4500k", "7000k"))
    # -threads ở đây là luồng của bộ mã hoá x264 (tuỳ chọn đầu ra); để mặc định x264 tự lấy theo số lõi MÁY CHỦ
    # (container 1.5 vCPU vẫn thấy hàng chục lõi -> tốn RAM lookahead, tranh CPU)
    return ["-threads", str(ffmpeg_threads()),
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-maxrate", maxrate, "-bufsize", buf,
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-g", str(round(2 * fps)), "-keyint_min", str(round(2 * fps)),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]


def _wrap(text: str, width: int) -> str:
    """Xuống dòng tiêu đề cho vừa màn hình dọc (tối đa 3 dòng)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])


async def source_info(path: str) -> tuple:
    """(rộng, cao, fps) của luồng hình; fps lấy avg_frame_rate (đúng cả với video tốc độ khung thay đổi)."""
    out = await _run_ffmpeg("-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,avg_frame_rate,r_frame_rate", "-of", "default=nw=1", path, tool="ffprobe")
    d = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)

    def _rate(s):
        try:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        except Exception:
            return 0.0
    fps = _rate(d.get("avg_frame_rate", "")) or _rate(d.get("r_frame_rate", ""))
    try:
        return int(d.get("width", 0)), int(d.get("height", 0)), fps
    except ValueError:
        return 0, 0, fps


LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
FONT_PATH = os.path.join(ASSETS_DIR, "BeVietnamPro-ExtraBold.ttf")


def _brand_filter(W: int, H: int, out_dur: float, title_file: str | None, outro_file: str | None,
                  logo_idx: int | None) -> str:
    """Chuỗi filter gắn lên [v]: logo góc phải trên, tiêu đề 3s đầu, màn kết 2.2s cuối."""
    k = H / 1280
    f = ""
    if title_file:
        f += (f",drawtext=fontfile={FONT_PATH}:textfile={title_file}:fontsize={int(50 * k)}:fontcolor=white:"
              f"borderw={int(3 * k)}:bordercolor=black@0.9:box=1:boxcolor=black@0.45:boxborderw={int(16 * k)}:"
              f"x=(w-text_w)/2:y=h*0.36-text_h/2:text_align=center:line_spacing={int(8 * k)}:enable='between(t,0.3,3.3)':"
              "alpha='if(lt(t,0.7),(t-0.3)/0.4,if(gt(t,2.9),(3.3-t)/0.4,1))'")
    if outro_file and out_dur > 6:
        st = out_dur - 2.2
        f += (f",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.72:t=fill:enable='gte(t,{st:.2f})'"
              f",drawtext=fontfile={FONT_PATH}:textfile={outro_file}:fontsize={int(62 * k)}:fontcolor=white:"
              f"borderw={int(2 * k)}:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-{int(30 * k)}:"
              f"enable='gte(t,{st:.2f})':alpha='min(1,(t-{st:.2f})/0.4)'"
              f",drawtext=fontfile={FONT_PATH}:text='Theo dõi để xem thêm':fontsize={int(34 * k)}:fontcolor=0xFFE100:"
              f"x=(w-text_w)/2:y=(h-text_h)/2+{int(50 * k)}:enable='gte(t,{st + 0.3:.2f})'")
    if logo_idx is not None:
        f += (f"[vb];[{logo_idx}:v]scale={int(W * 0.16)}:-1,format=rgba,colorchannelmixer=aa=0.85[lg];"
              f"[vb][lg]overlay=W-w-{int(24 * k)}:{int(24 * k)}")
    return f


async def render(input_mp4: str, voice_mp3: str, output_mp4: str, mode: str,
                 duration: int = 0, ass_path: str | None = None, cover_bands=None,
                 ambient_wav: str | None = None, speech_gate=None, allow_hd: bool = True,
                 title: str = "", tmp: str | None = None, stretch: float = 1.0, ost=None):
    """Chế độ cook: ambient_wav = tiếng nền đã tách giọng AI; không có thì speech_gate =
    [(start, end)] lúc người gốc nói để tắt tiếng nền khi đó (không bao giờ để lọt giọng gốc).
    duration: 0 = giữ nguyên thời lượng gốc; 30/60/90 = cắt đúng ngần đó giây.
    stretch: làm chậm hình + tiếng nền (1.4 = dài thêm 40%) NGAY TRONG lần mã hoá này; mọi mốc thời gian
    (phụ đề, cổng tiếng nền, giọng) là theo trục thời gian ĐÃ làm chậm.
    Video nguồn câm tiếng vẫn chạy được chế độ cook (tự bỏ lớp tiếng nền)."""
    vid_dur = await probe_duration(input_mp4) * stretch
    out_dur = vid_dur if duration <= 0 else min(duration, vid_dur)
    out_dur = min(out_dur, config.HARD_MAX)       # trần tuyệt đối; giới hạn theo chế độ do caller truyền vào
    ambient = await has_audio(input_mp4)
    # nguồn đủ nét (>=1600px) thì xuất 1080x1920, không thì 720x1280 (không phóng to giả nét)
    src_w, src_h, src_fps = await source_info(input_mp4)
    W, H = (1080, 1920) if (config.OUTPUT_HD and allow_hd and src_h >= 1600) else (720, 1280)
    fps = out_fps(src_fps)
    logging.info("Render %dx%d %gfps | nguồn %dx%d %.2ffps%s | chất lượng %s", W, H, fps, src_w, src_h, src_fps,
                 " (nguồn <1600px nên ra 720p; gửi link TikTok để bot lấy bản HD)" if src_h < 1600 else "", config.QUALITY)
    # thương hiệu: tiêu đề mở đầu, màn kết, logo (tất cả tùy chọn trong Cài đặt)
    tmp = tmp or os.path.dirname(output_mp4)
    title_file = outro_file = None
    if title and config.BRAND_INTRO != "0":
        title_file = os.path.join(tmp, "title.txt")
        open(title_file, "w", encoding="utf-8").write(_wrap(title.strip(), 22))
    if config.BRAND_NAME.strip():
        outro_file = os.path.join(tmp, "outro.txt")
        open(outro_file, "w", encoding="utf-8").write(config.BRAND_NAME.strip())
    use_logo = config.BRAND_LOGO != "0" and os.path.exists(LOGO_PATH)
    logo_idx = (3 if ambient_wav else 2) if use_logo else None
    fc = build_filter(mode, out_dur, ambient or bool(ambient_wav), ass_path, cover_bands, W, H,
                      "[2:a]" if ambient_wav else "[0:a]", None if ambient_wav else speech_gate,
                      fps=fps, src_w=src_w, src_h=src_h, stretch=stretch, ost=ost, orig_audio=ambient)
    brand = _brand_filter(W, H, out_dur, title_file, outro_file, logo_idx)
    fades = f",fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, out_dur - 0.4):.2f}:d=0.4"
    fc = fc.replace("[v];", brand + fades + "[v];", 1)
    fc = fc.replace("[a]", f",afade=t=in:st=0:d=0.15,afade=t=out:st={max(0, out_dur - 0.4):.2f}:d=0.4,alimiter=limit=0.89:level=false[a]", 1)
    await _run_ffmpeg(
        "-y", *thread_args(),
        "-i", input_mp4,
        "-i", voice_mp3,
        *(["-i", ambient_wav] if ambient_wav else []),
        *(["-i", LOGO_PATH] if use_logo else []),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        *encode_args(H, fps),
        "-t", f"{out_dur:.2f}",
        output_mp4,
    )
    return out_dur


async def render_music(input_mp4: str, music_path: str, output_mp4: str,
                       duration: int = 0, speed: float = 1.0,
                       trim: float = 0.0, delay: float = 0.0):
    """Chế độ nhảy/nhạc: bỏ tiếng gốc, lồng nhạc mới, video đổi tốc độ nhẹ cho khớp beat."""
    vid_dur = await probe_duration(input_mp4)
    music_len = max(0.0, await probe_duration(music_path) - trim) + delay
    out_dur = min(vid_dur / speed, music_len, config.HARD_MAX)
    if duration > 0:
        out_dur = min(out_dur, duration)
    a = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
    if delay > 0.01:
        ms = int(delay * 1000)
        a += f"adelay={ms}|{ms},"
    a += ("loudnorm=I=-14:TP=-1.0:LRA=9,aresample=48000,"           # nhạc chuẩn -14 LUFS như nền tảng nhạc
          f"afade=t=out:st={max(0.0, out_dur - 0.8):.2f}:d=0.8,"
          f"apad=whole_dur={out_dur:.2f}[a]")
    v = ("[0:v]crop=in_w*0.96:in_h*0.96,"
         "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
         f"setpts=PTS/{speed:.4f},fps=30,hqdn3d=1.2:1.2:3:3,eq=contrast=1.05:brightness=0.012:saturation=1.04,vibrance=intensity=0.12,cas=strength=0.5[v];")
    await _run_ffmpeg(
        "-y", *thread_args(),
        "-i", input_mp4,
        "-ss", f"{trim:.3f}", "-i", music_path,
        "-filter_complex", v + a,
        "-map", "[v]", "-map", "[a]",
        *encode_args(1280),
        "-t", f"{out_dur:.2f}",
        output_mp4,
    )
    return out_dur
