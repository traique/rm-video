"""Tải nguồn: yt-dlp (cookies, proxy, PO token YouTube, dừng khi job huỷ), tikwm dự phòng cho TikTok, mở link rút gọn Facebook, tải nhạc / file qua HTTP có kiểm chuyển hướng, chọn định dạng cho trình tải; câu báo lỗi dễ hiểu.

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import asyncio
import html
import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

import yt_dlp
from yt_dlp.utils.networking import std_headers

import config
import metrics
import url_guard
from rmvideo.media import proc

# TikTok/YouTube chặn request "trông như bot" từ IP máy chủ. curl_cffi cho yt-dlp giả lập
# chữ ký mạng của Chrome thật. Thiếu curl_cffi thì chạy như cũ, không crash.
try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _IMPERSONATE = ImpersonateTarget("chrome")
except Exception:
    _IMPERSONATE = None


class TooBig(RuntimeError):
    """Video vượt giới hạn thời lượng / dung lượng của chế độ đã chọn: tải được, chỉ là bot không nhận. Không thử
    nguồn dự phòng (tikwm cũng sẽ vượt), báo thẳng lý do + nút cần bấm."""


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
    low = msg.lower()
    if "javascript runtime" in low or "challenge solving failed" in low or ("youtube" in low and not potoken_setup.js_runtime()
                                                                            and "only images are available" in low):
        return RuntimeError("YouTube cần bộ giải JavaScript (deno) mà máy chưa cài: gõ  setup_youtube  vào console "
                            "Botkeep (~100MB), xong gõ  yt_test  để thử.")
    if "403" in msg and "forbidden" in msg.lower():
        fix = ("Đã bật PO token (" + pot_status() + ") mà vẫn bị chặn -> IP máy chủ bị YouTube gắn cờ nặng. "
               "Còn 2 cách: điền YTDLP_PROXY (proxy dân dụng) hoặc dán cookies tài khoản Google phụ vào YTDLP_COOKIES."
               if pot_status() else
               "Thử theo thứ tự: (1) gõ  setup_potoken  vào console Botkeep; (2) điền YTDLP_PROXY; "
               "(3) dán cookies tài khoản Google phụ vào YTDLP_COOKIES.")
        return RuntimeError("Máy chủ bị YouTube chặn tải (403). " + fix)
    if "no suitable extractor" in low or "unsupported url" in low:
        return RuntimeError("Bot không nhận ra link này là video (link trang/bài viết, link rút gọn lạ hoặc nền tảng "
                            "chưa hỗ trợ). Hãy mở video, bấm Sao chép liên kết của chính video đó (Facebook: dạng "
                            "facebook.com/reel/... hoặc .../videos/...) rồi gửi lại, hoặc tải về máy rồi upload file.")
    return RuntimeError("Tải video lỗi: " + msg.replace("ERROR: ", "")[:300])


import potoken_setup  # noqa: E402

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


def _stop_if_cancelled(_d: dict):
    """Hook tiến độ của yt-dlp (gọi mỗi khúc tải, trong luồng phụ): job đã huỷ / quá hạn thì dừng tải ngay (C8).
    Trước đây huỷ job chỉ dừng task; luồng yt-dlp vẫn tải/ghép tới 300MB vào thư mục tạm đã bị xoá."""
    if proc.cancelled():
        raise yt_dlp.utils.DownloadCancelled("job đã huỷ")


def ytdlp_base_options(opts: dict | None = None) -> dict:
    """Tuỳ chọn MỌI lời gọi yt-dlp phải có (tải video, xem định dạng, tải nhạc, liệt kê kênh TikTok): chỉ extractor
    của nền tảng — không bao giờ 'generic' (tải được URL bất kỳ: SSRF) — và proxy trong Cài đặt."""
    opts = dict(opts or {})
    opts["allowed_extractors"] = url_guard.YTDLP_EXTRACTORS
    if config.YTDLP_PROXY.strip():
        opts["proxy"] = config.YTDLP_PROXY.strip()
    return opts


def _add_cookies(opts: dict, tmpdir: str):
    """Tuỳ chọn cho lời gọi tải / xem video: tuỳ chọn gốc + giả lập trình duyệt + PO token YouTube + cookies + dừng
    khi job huỷ."""
    opts.update(ytdlp_base_options(opts))
    opts.setdefault("progress_hooks", []).append(_stop_if_cancelled)
    if _IMPERSONATE:
        opts["impersonate"] = _IMPERSONATE
    ea = opts.setdefault("extractor_args", {})
    if config.POT_SERVER.strip():
        ea["youtubepot-bgutilhttp"] = {"base_url": [config.POT_SERVER.strip().rstrip("/")]}
    elif _pot_script():
        ea["youtubepot-bgutilscript"] = {"script_path": [_pot_script()]}
    if config.YTDLP_COOKIES.strip():
        cookie_file = os.path.join(tmpdir, "cookies.txt")
        fd = os.open(cookie_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # phiên đăng nhập: chỉ bot đọc được
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(config.YTDLP_COOKIES)
        opts["cookiefile"] = cookie_file


MUSIC_MAX_BYTES = 20 * 1024 * 1024


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Link media do dịch vụ bên thứ ba (tikwm) trả về: kiểm cả từng lần chuyển hướng, không chỉ link đầu
    (trước đây urlopen tự đi theo redirect, có thể bị dẫn vào IP nội bộ)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url_guard.check_url(newurl, any_domain=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_GUARDED_OPENER = urllib.request.build_opener(_GuardedRedirect)


def fetch_media(url: str, dst: str, max_bytes: int, too_big: str, timeout: float = 120) -> int:
    """Tải link media bên thứ ba vào dst (đã kiểm link + mọi chuyển hướng). Ghi ra file tạm rồi mới đổi tên:
    lỗi giữa chừng không để lại file dở (lỗi cũ: file nhạc dở bị đường dự phòng nhặt nhầm). Trả số byte."""
    url = url_guard.check_url(url, any_domain=True)
    part = dst + ".part"
    size = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _GUARDED_OPENER.open(req, timeout=timeout) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise RuntimeError(too_big)
                f.write(chunk)
        os.replace(part, dst)
        return size
    finally:
        if os.path.exists(part):
            os.remove(part)


def is_fb_short(url: str) -> bool:
    """Link chia sẻ Facebook (facebook.com/share/r|v|p/..., fb.watch/...): yt-dlp không nhận dạng được trực tiếp
    (chỉ extractor "generic" — đã tắt để chặn SSRF — mới đi theo chuyển hướng), phải mở ra link reel/video thật."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if host == "fb.watch" or host.endswith(".fb.watch"):
        return True
    return (host == "facebook.com" or host.endswith(".facebook.com")) and parts.path.startswith("/share/")


class _ShortResolved(Exception):
    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


class _ShortLinkRedirect(urllib.request.HTTPRedirectHandler):
    """Đi theo từng bước chuyển hướng của link rút gọn: bước nào cũng phải còn trong nền tảng được phép (không IP
    nội bộ); tới link không còn là link rút gọn thì dừng luôn, không tải trang (trang Facebook nặng vài trăm KB)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = url_guard.check_url(newurl)
        if not is_fb_short(newurl):
            fp.close()
            raise _ShortResolved(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SHORT_OPENER = urllib.request.build_opener(_ShortLinkRedirect)


_FB_JUNK_QS = {"rdid", "share_url", "mibextid", "sfnsn", "_rdr", "s", "fs", "extid"}


_FB_CANON = re.compile(r'<link rel="canonical" href="([^"]+)"|property="og:url" content="([^"]+)"')


def _clean_fb_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if "/login" in parts.path or parts.path.startswith("/checkpoint"):
        raise RuntimeError("Video Facebook này cần đăng nhập mới xem được (riêng tư, nhóm kín hoặc giới hạn tuổi). "
                           "Dán cookies Facebook vào YTDLP_COOKIES trong Cài đặt, hoặc tải video về máy rồi upload file.")
    m = re.match(r"/reel/(\d+)", parts.path)
    if m:
        return f"https://www.facebook.com/reel/{m.group(1)}/"
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k not in _FB_JUNK_QS]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(qs), ""))


def expand_short_url(url: str, opener=None, timeout: float = 20) -> str:
    """facebook.com/share/r/xxx, fb.watch/xxx -> https://www.facebook.com/reel/<id>/ (hoặc link video/bài viết thật).
    Link khác trả nguyên. Dùng đúng bộ header chuẩn của yt-dlp (như khi yt-dlp tự mở link)."""
    if not is_fb_short(url):
        return url
    req = urllib.request.Request(url, headers=dict(std_headers))
    try:
        with (opener or _SHORT_OPENER).open(req, timeout=timeout) as r:
            # không chuyển hướng mà trả thẳng trang: lấy link gốc trong thẻ canonical / og:url
            body = r.read(1 << 20).decode("utf-8", "replace")
        m = _FB_CANON.search(body)
        if not m:
            raise RuntimeError("Không đọc được link video thật từ link chia sẻ Facebook. "
                               "Hãy mở link trên máy, bấm Sao chép liên kết của video (dạng facebook.com/reel/...) "
                               "rồi gửi lại.")
        new = url_guard.check_url(html.unescape(m.group(1) or m.group(2)))
    except _ShortResolved as r:
        new = r.url
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Facebook trả lỗi {e.code} khi mở link chia sẻ. Link có thể đã bị xoá hoặc riêng tư; "
                           "thử gửi link dạng facebook.com/reel/... hoặc tải video về máy rồi upload file.") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Không mở được link chia sẻ Facebook ({e.reason}). Thử lại sau ít phút.") from None
    new = _clean_fb_url(new)
    logging.info("Link chia sẻ Facebook %s -> %s", url, new)
    return new


def resolve_url(url: str) -> str:
    """Kiểm link (chặn SSRF) rồi mở link rút gọn ra link mà yt-dlp nhận được. Mọi lối tải bằng yt-dlp gọi hàm này."""
    return expand_short_url(url_guard.check_url(url))


_FB_TITLE_STATS = re.compile(r"^\s*[\d.,]+\s*[KMBkmb]?\s+(?:views|lượt xem)\s*·\s*[\d.,]+\s*[KMBkmb]?\s+"
                             r"(?:reactions|cảm xúc|lượt bày tỏ cảm xúc)\s*\|\s*")


def clean_title(title: str) -> str:
    """Facebook ghép số liệu vào đầu tiêu đề ("2.6M views · 29K reactions | ...") — bỏ đi, để AI/Whisper không đọc nhầm."""
    return _FB_TITLE_STATS.sub("", title or "").strip()


def _tiktok_sound_sync(url: str, tmpdir: str) -> tuple:
    """Link VIDEO TikTok -> bản nhạc gốc (sound) mà video dùng, qua tikwm: là file nhạc sạch (bài hát trong thư viện
    TikTok) chứ không lẫn giọng người quay như tiếng của video. Trả (đường dẫn, tên nhạc)."""
    api = "https://www.tikwm.com/api/?url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    data = d.get("data") or {}
    mi = data.get("music_info") or {}
    play = mi.get("play") or data.get("music")
    if d.get("code") != 0 or not play:
        raise RuntimeError(f"tikwm: {d.get('msg') or 'không có nhạc'}")
    if (mi.get("duration") or 0) > 600:
        raise RuntimeError("Nhạc quá 10 phút.")
    path = os.path.join(tmpdir, "music.mp3")
    if fetch_media(play, path, MUSIC_MAX_BYTES, "File nhạc quá 20MB.") < 1000:
        os.remove(path)
        raise RuntimeError("tikwm trả file nhạc rỗng")
    name = " - ".join(x for x in (mi.get("title"), mi.get("author")) if x)
    return path, name


def is_tiktok(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def _download_audio_sync(url: str, tmpdir: str) -> str:
    url = resolve_url(url)
    if is_tiktok(url) and "/music/" not in url:
        # TikTok: lấy bản nhạc gốc của video (sạch); lỗi thì lấy tiếng của video qua yt-dlp như cũ
        try:
            path, name = _tiktok_sound_sync(url, tmpdir)
            logging.info("Nhạc TikTok: %s", name or "(không tên)")
            return path
        except Exception as e:
            logging.warning("Lấy nhạc gốc TikTok qua tikwm lỗi (%s), lấy tiếng video bằng yt-dlp", str(e)[:120])
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
        if is_tiktok(url) and "/music/" in url:
            raise RuntimeError("Link trang nhạc TikTok (tiktok.com/music/...) không tải trực tiếp được: hãy gửi link "
                               "MỘT VIDEO TikTok có dùng nhạc đó, bot sẽ lấy bản nhạc gốc của video.") from None
        raise _friendly(e) from None
    found = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.startswith("music.") and not f.endswith(".part")]
    if not found:
        raise RuntimeError("Tải nhạc thất bại (quá 10 phút, quá 20MB hoặc link hỏng).")
    return max(found, key=os.path.getmtime)                      # file yt-dlp vừa tải


async def download_audio(url: str, tmpdir: str) -> str:
    return await asyncio.to_thread(_download_audio_sync, url, tmpdir)


DL_MAX_DUR = 3600          # trình tải cho phép tới 60 phút


DL_MAX_SIZE = 450 * 1024 * 1024


_DL_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]


def _probe_formats_sync(url: str, tmpdir: str) -> dict:
    url = resolve_url(url)
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
    return {"title": clean_title(info.get("title") or ""), "thumbnail": info.get("thumbnail") or "",
            "duration": int(info.get("duration") or 0), "uploader": info.get("uploader") or "",
            "uploader_id": str(info.get("uploader_id") or info.get("channel_id") or ""),
            "max_height": max_h, "max_short": max_short, "options": options}


async def probe_formats(url: str, tmpdir: str) -> dict:
    return await asyncio.to_thread(_probe_formats_sync, url, tmpdir)


def _download_format_sync(url: str, tmpdir: str, quality: str, on_pct=None) -> tuple:
    """quality: 'mp3' | '2160'...'360' | '' (tốt nhất). Trả (đường dẫn, tiêu đề)."""
    url = resolve_url(url)
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
    return path, clean_title(info.get("title") or "")


async def download_format(url: str, tmpdir: str, quality: str, on_pct=None) -> tuple:
    return await asyncio.to_thread(_download_format_sync, url, tmpdir, quality, on_pct)


def _size_limit(max_duration: int) -> int:
    """Video thường 50MB; chế độ video dài/cắt phần cho phép tới 300MB."""
    return config.MAX_FILESIZE if max_duration <= config.MAX_DURATION else config.LONG_FILESIZE


def _has_video(path: str) -> bool:
    out = proc.run_sync(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path], capture_output=True, text=True, timeout=120).stdout
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
        dur = float(data["duration"])
        raise TooBig(f"Video dài {dur:.0f}s, vượt giới hạn {max_duration}s. " + _long_hint(dur, max_duration))
    path = os.path.join(tmpdir, "input.mp4")
    fetch_media(data.get("hdplay") or data.get("play"), path, _size_limit(max_duration),     # không tin link bên thứ ba
                f"Video quá {_size_limit(max_duration) // 1048576}MB.")
    title = data.get("title") or ""
    author = data.get("author") or {}
    meta = {"uploader": author.get("unique_id") or author.get("nickname") or "", "webpage_url": url}
    return path, title, title, meta


def _long_hint(dur: float, max_duration: int) -> str:
    """Chỉ đúng nút cần bấm lại (bot có sẵn chế độ video dài, trước đây lời báo chỉ bảo cắt ngắn rồi upload)."""
    if max_duration < config.LONG_MAX and dur <= config.LONG_MAX:
        return ("Gửi lại link và ở bước chọn thời lượng bấm «🎞 Video dài: remake nguyên (≤5 phút)», "
                "hoặc «✂️ Cắt thành phần 60s/90s» / «✨ Đoạn hay nhất».")
    if dur <= 600:
        return "Gửi lại link và chọn «✂️ Cắt thành phần 60s/90s» hoặc «✨ Đoạn hay nhất» (nguồn ≤10 phút)."
    return "Hãy cắt ngắn rồi upload file, hoặc dùng chế độ ⬇️ Chỉ tải về (tối đa 10 phút)."


def _download_sync(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    """Trả (đường dẫn, title, description, meta). meta: uploader/kênh/link gốc để ghi nguồn."""
    url = resolve_url(url)
    try:
        path, title, desc, meta = _ytdlp_video_sync(url, tmpdir, max_duration)
        if _has_video(path):
            metrics.record("download", "yt-dlp")
            return path, title, desc, meta
        os.remove(path)
        reason = "TikTok chỉ trả về nhạc, giấu video"
    except TooBig as e:
        metrics.record("download", "yt-dlp", ok=False, detail=str(e))
        raise
    except RuntimeError as e:
        reason = str(e)
    metrics.record("download", "yt-dlp", ok=False, detail=reason)
    if "tiktok.com" in url.lower():
        try:
            out = _tikwm_sync(url, tmpdir, max_duration)
            metrics.record("download", "tikwm", fallback=True)
            return out
        except TooBig as e2:
            metrics.record("download", "tikwm", ok=False, fallback=True, detail=str(e2))
            raise
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
            raise TooBig(f"Video dài {dur:.0f}s, vượt giới hạn {max_duration}s. " + _long_hint(dur, max_duration))
        if size and size > _size_limit(max_duration):
            raise TooBig(f"Video nặng {size / 1048576:.0f}MB, vượt giới hạn {_size_limit(max_duration) // 1048576}MB.")
        raise RuntimeError("Tải video thất bại (link riêng tư, đã xóa, hoặc nền tảng chặn). "
                           "Hãy tải video về máy rồi upload file.")
    meta = {"uploader": info.get("uploader") or info.get("channel") or "",
            "uploader_id": info.get("uploader_id") or info.get("channel_id") or "",
            "webpage_url": info.get("webpage_url") or url}
    return path, clean_title(info.get("title") or ""), (info.get("description") or ""), meta


async def download(url: str, tmpdir: str, max_duration: int = config.MAX_DURATION):
    """Trả về (đường dẫn file, title, description, meta). Chạy trong thread vì yt-dlp blocking."""
    return await asyncio.to_thread(_download_sync, url, tmpdir, max_duration)
