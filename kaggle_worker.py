"""Phía Botkeep: gửi phần nặng sang Kaggle rồi nhận video hoàn chỉnh về (xem kaggle_job.py).

Một lần chạy = một phiên notebook riêng tư `<user>/rmvideo-worker` (mỗi lần là một phiên bản mới). Notebook chứa code
cần thiết (nén + base64) và việc cần làm (JSON), KHÔNG chứa key API nào; file lớn (video tải lên, nhạc, ảnh) Kaggle tự
tải: link nền tảng gốc, hoặc link tạm có mã bí mật do web panel của Botkeep phát (cần PUBLIC_URL; notebook < 1MB nên
không nhúng file được). GPU chỉ khi có giọng VieNeu (còn giờ GPU trong tuần); việc khác chạy CPU 4 lõi, không tốn giờ GPU.
Lỗi ở bất kỳ bước nào -> ném KaggleError, pipeline làm tiếp trên Botkeep như cũ."""
import asyncio
import base64
import contextvars
import io
import json
import logging
import os
import re
import secrets
import subprocess
import time
import urllib.parse
import zipfile

import config
import kaggle_api
from kaggle_api import KaggleError

BASE = os.path.dirname(os.path.abspath(__file__))
SLUG = "rmvideo-worker"
SLUG_ANALYZE = "rmvideo-analyze"   # notebook riêng: phiên dựng gắn đầu ra của nó (tiếng nền đã tách) làm dữ liệu vào
SLUG_PHOWHISPER = "rmvideo-phowhisper"   # notebook giữ model PhoWhisper-large đã chuyển CTranslate2 (chạy một lần)
BUNDLE = ["kaggle_job.py", "core_video.py", "config.py", "subs.py", "align.py", "cache.py", "metrics.py",
          "url_guard.py", "potoken_setup.py", "vocal_sep.py", "queue_manager.py", "vi_text.py", "tts_capcut.py",
          "slideshow.py", "trendfx.py", "beat.py", "scenes.py", "textzone.py", "asr_local.py",
          "assets/BeVietnamPro-ExtraBold.ttf"]
BUNDLE_DIRS = ["capcut_tts_api"]
PIP = ("pip install -q --no-deps vieneu sea-g2p && pip install -q onnxruntime kaldi-native-fbank soxr "
       "'transformers==4.57.6' safetensors yt-dlp edge-tts")
PIP_LIGHT = "pip install -q yt-dlp edge-tts"         # không có giọng VieNeu: khỏi cài model giọng
# phiên phân tích: Whisper (faster-whisper) + tách giọng trên GPU (onnxruntime-gpu thay bản CPU mà faster-whisper kéo về)
PIP_ANALYZE = ("pip install -q faster-whisper yt-dlp edge-tts && (pip uninstall -y -q onnxruntime; "
               "pip install -q onnxruntime-gpu)")
GPU_MIN_LEFT = 20 * 60           # còn dưới 20 phút GPU trong tuần thì chạy CPU
TIMEOUT = 50 * 60                # cả hàng chờ máy + chạy (một việc)
TIMEOUT_PER_EXTRA = 12 * 60      # mỗi việc thêm trong lô
VOICE_REF = os.path.join(BASE, "assets", "voice_ref.mp3")
LINK_TTL = 4 * 3600
_LOCK = asyncio.Lock()
_ABORT = {"flag": False, "running": False, "slug": SLUG}


def abort():
    """Người dùng huỷ job đang chạy trên Kaggle: dừng chờ và yêu cầu Kaggle huỷ phiên (hết việc thì thôi)."""
    if not _ABORT["running"]:
        return
    _ABORT["flag"] = True
    try:
        kaggle_api.cancel(_ABORT.get("slug") or SLUG)
    except Exception as e:
        logging.info("Không huỷ được phiên Kaggle: %s", e)

# Giọng VieNeu v3 Turbo miền Nam (nhãn của model) và tốc độ đọc đo thật (chữ/giây, chỉ phần tiếng nói, 12/11/2026)
SOUTH_FEMALE = ["Mỹ Duyên", "Thục Đoan", "Thùy Dung", "Kim Thanh"]
SOUTH_MALE = ["Adam", "Thái Sơn", "Minh Triết", "Đức Trí"]
RATE = {"Mỹ Duyên": 3.08, "Adam": 4.15, "Thùy Dung": 4.5}
RATE_EDGE = 3.6                  # edge Hoài My/Nam Minh +8%: 3.9 chữ/giây phần tiếng nói, chừa biên
RATE_CAPCUT = 3.8
CLONE = "clone"


def voice_name(voice: str) -> str:
    """'vieneu:Mỹ Duyên' -> 'Mỹ Duyên'; giọng khác -> ''."""
    return voice.split(":", 1)[1] if (voice or "").startswith("vieneu:") else ""


def is_male(voice: str) -> bool:
    return voice_name(voice) in SOUTH_MALE


def voice_engine(voice: str) -> str:
    if voice_name(voice):
        return "vieneu"
    if config.TTS_PROVIDER == "capcut" and voice not in (config.VOICE_FEMALE, config.VOICE_MALE):
        return "capcut"
    return "edge"


def words_per_sec(voice: str) -> float:
    name = voice_name(voice)
    if name:
        return RATE.get(name, 3.6 if name != CLONE else 3.4)
    return RATE_CAPCUT if voice_engine(voice) == "capcut" else RATE_EDGE


# ---------------- link tạm cho file trên Botkeep ----------------

_LINKS: dict = {}                # mã -> (đường dẫn, hết hạn)


def serve(path: str) -> str | None:
    """Link tải tạm (mã ngẫu nhiên 32 byte, hết hạn sau LINK_TTL) để Kaggle lấy file đang nằm trên Botkeep.
    Không có PUBLIC_URL thì None."""
    base = (config.PUBLIC_URL or "").strip().rstrip("/")
    if not base or not path or not os.path.isfile(path):
        return None
    now = time.time()
    for k in [k for k, (_, exp) in _LINKS.items() if exp < now]:
        _LINKS.pop(k, None)
    tok = secrets.token_urlsafe(32)
    _LINKS[tok] = (os.path.abspath(path), now + LINK_TTL)
    return f"{base}/kin/{tok}/{urllib.parse.quote(os.path.basename(path))}"


def served(token: str) -> str | None:
    hit = _LINKS.get(token)
    if not hit or hit[1] < time.time() or not os.path.isfile(hit[0]):
        return None
    return hit[0]


def release(spec: dict):
    """Huỷ các link tạm của một việc (Kaggle đã chạy xong)."""
    for tok in list(_LINKS):
        if tok in json.dumps(spec):
            _LINKS.pop(tok, None)


def ref(path: str | None = None, url: str = "", kind: str = "video", max_duration: int = 600) -> dict:
    """Đầu vào cho Kaggle: link nền tảng (Kaggle tự tải, không cần PUBLIC_URL) và/hoặc link tạm tới bản trên Botkeep.
    Ném KaggleError nếu Kaggle không có cách nào lấy được file."""
    r: dict = {}
    if url:
        r.update(url=url, kind=kind, max_duration=max_duration)
    link = serve(path) if path else None
    if link:
        r.update(http=link, name=os.path.basename(path))
    if not r:
        raise KaggleError("file tải lên từ máy cần PUBLIC_URL (link web panel) để Kaggle tải về")
    return r


def can_ref(path: str | None, url: str = "") -> bool:
    return bool(url) or bool(path and (config.PUBLIC_URL or "").strip())


def enabled() -> str:
    if config.KAGGLE_USE == "0":
        return "KAGGLE_USE=0"
    if not kaggle_api.configured():
        return "chưa điền KAGGLE_API_TOKEN trong Cài đặt"
    return ""


def usable(job, local_path: str | None = None) -> str:
    """'' nếu job remake này chạy được trên Kaggle; không thì lý do (để ghi chú cho người dùng)."""
    why = enabled()
    if why:
        return why
    if not job.remote_src and not can_ref(local_path or job.local, job.url):
        return "video tải lên từ máy: đặt PUBLIC_URL (link web panel) để Kaggle tải được"
    if voice_name(job.voice) == CLONE and not os.path.exists(VOICE_REF):
        return "chưa tải lên đoạn giọng mẫu"
    return ""


# ---------------- notebook ----------------

def bundle_b64() -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in BUNDLE:
            z.write(os.path.join(BASE, rel), rel)
        for d in BUNDLE_DIRS:
            for root, _, files in os.walk(os.path.join(BASE, d)):
                if "__pycache__" in root:
                    continue
                for f in files:
                    p = os.path.join(root, f)
                    z.write(p, os.path.relpath(p, BASE))
    return base64.b64encode(buf.getvalue()).decode()


def _jobs(spec: dict) -> list:
    return spec.get("jobs") if spec.get("kind") == "batch" else [spec]


def needs_vieneu(spec: dict) -> bool:
    return any((j.get("voice") or {}).get("engine") == "vieneu" for j in _jobs(spec)
               if (j.get("kind") or "remake") == "remake")


def needs_gpu(spec: dict) -> bool:
    return needs_vieneu(spec) or any(j.get("kind") == "analyze" for j in _jobs(spec))


def pip_for(spec: dict) -> str:
    if any(j.get("kind") == "analyze" for j in _jobs(spec)):
        return PIP_ANALYZE
    return PIP if needs_vieneu(spec) else PIP_LIGHT


def kernel_text(spec: dict) -> str:
    spec_b64 = base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode()
    pip = pip_for(spec)
    text = f'''# rm-video: job tự động (bot tạo, đừng sửa tay). Không chứa key API.
import base64, io, json, subprocess, sys, zipfile
APP = "/tmp/rm_app"
zipfile.ZipFile(io.BytesIO(base64.b64decode("{bundle_b64()}"))).extractall(APP)
r = subprocess.run({pip!r}, shell=True, capture_output=True, text=True)
print(r.stdout[-1500:], r.stderr[-1500:])
sys.path.insert(0, APP)
import kaggle_job
kaggle_job.main(json.loads(base64.b64decode("{spec_b64}").decode()))
'''
    if len(text.encode()) >= 990_000:
        raise KaggleError(f"việc quá lớn cho một notebook Kaggle ({len(text) // 1024}KB, tối đa 1MB)")
    return text


def settings_snapshot() -> dict:
    return {k: getattr(config, k) for k in config.EDITABLE if k not in config.SECRET_KEYS
            and not k.startswith(("KAGGLE_", "FB_", "AUTO_", "ALLOWED_", "ADMIN_"))}


def _common(spec: dict) -> dict:
    logo = os.path.join(BASE, "assets", "logo.png")
    if config.BRAND_LOGO != "0" and os.path.exists(logo):
        spec["logo_b64"] = base64.b64encode(open(logo, "rb").read()).decode()
    spec["settings"] = settings_snapshot()
    spec.setdefault("token", secrets.token_hex(8))     # mã riêng từng việc: không nhận nhầm kết quả phiên trước
    return spec


def build_spec(job, segments: list, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str,
               bands, pos_y, sep: dict, speech: list, max_duration: int, local_path: str | None = None,
               src: dict | None = None, amb_token: str = "", teaser: dict | None = None) -> dict:
    """Việc remake. src: đầu vào đã dựng sẵn (vd. một phần cắt từ video dài); không có thì từ job.url / file trên Botkeep."""
    engine = voice_engine(job.voice)
    spec = {"kind": "remake", "job": job.id, "mode": job.mode,
            "src": src or ref(local_path or job.local or None, job.url, "video", max_duration),
            "segments": [{k: s.get(k) for k in ("i", "start", "slot", "span", "text", "ost")} for s in segments],
            "out_dur": out_dur, "render_dur": render_dur, "stretch": stretch, "allow_hd": allow_hd, "title": title,
            "bands": bands or [], "pos_y": pos_y, "subs": bool(job.subs),
            "sep": {"ok": bool(sep.get("ok_remote")), "windows": sep.get("windows")},
            "speech": [list(x) for x in speech or []],
            "voice": {"engine": engine, "name": voice_name(job.voice), "id": job.voice}}
    if amb_token:
        spec["amb_token"] = amb_token
    if teaser:                                   # đoạn mở đầu: cảnh món đã xong chiếu trước (mốc đã lùi theo)
        spec["teaser"] = {k: teaser[k] for k in ("a", "dur", "still")}
    if voice_name(job.voice) == CLONE:
        spec["ref_b64"] = base64.b64encode(open(VOICE_REF, "rb").read()).decode()
        spec["ref_ext"] = ".mp3"
    return _common(spec)


def bed_spec(job, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str, bands,
             max_duration: int, music: str | None, music_url: str, trim: float, orig: str,
             local_path: str | None = None, src: dict | None = None) -> dict:
    """Video KHÔNG LỜI (NO_SPEECH=music): dựng như remake (tiêu đề, logo, che chữ, màu) nhưng tiếng là nhạc nền (+ tiếng
    gốc theo BGM_ORIG) thay cho giọng đọc. Không đọc giọng, không tách giọng: chạy CPU, không tốn giờ GPU.
    music: file nhạc trên Botkeep (Kaggle lấy qua PUBLIC_URL); music_url: link gốc (Kaggle tự tải). Không có cả hai:
    chỉ tiếng gốc."""
    return _common({"kind": "bed", "job": job.id, "mode": job.mode,
                    "src": src or ref(local_path or job.local or None, job.url, "video", max_duration),
                    "music": ref(music, music_url, "audio") if (music or music_url) else None,
                    "trim": round(float(trim), 3), "orig": orig, "out_dur": out_dur, "render_dur": render_dur,
                    "stretch": stretch, "allow_hd": allow_hd, "title": title, "bands": bands or []})


def analyze_spec(job, local_path: str | None, title: str, desc: str, want_gray: bool, max_duration: int) -> dict:
    """Việc bóc lời + phân tích hình (chạy TRƯỚC khi AI viết lời). Khoá ngôn ngữ tiếng Việt khi tiêu đề/mô tả có dấu
    tiếng Việt (Whisper đoán ngôn ngữ trên quãng nhạc dạo hay sai); không thì để Whisper tự đoán (nguồn nước ngoài)."""
    import vocab
    vi = bool(re.search(r"[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", f"{title} {desc}".lower()))
    return _common({"kind": "analyze", "job": job.id, "src": ref(local_path or job.local or None, job.url, "video",
                                                                  max_duration),
                    "want_gray": bool(want_gray), "language": "vi" if vi else "", "token": secrets.token_hex(8),
                    "prompt": vocab.groq_prompt(title, desc), "keep_ambient": job.mode == "cook",
                    "asr": config.KAGGLE_ASR if vi else "large-v3"})   # PhoWhisper chỉ tiếng Việt


def music_spec(job, inp: str, music: str, music_args: dict) -> dict:
    return _common({"kind": "music", "job": job.id, "duration": job.duration, "music_args": music_args,
                    "src": ref(inp, job.url, "video", 600),
                    "music": ref(music, job.music_url if not job.music_local and not job.music_tg_file_id else "",
                                 "audio")})


def photo_spec(job, images: list, music: str, seed: int, recent: list) -> dict:
    return _common({"kind": "photo", "job": job.id, "duration": job.duration, "seed": seed, "recent": recent,
                    "images": [ref(p) for p in images],
                    "music": ref(music, job.music_url if not job.music_local and not job.music_tg_file_id else "",
                                 "audio")})


# ---------------- chạy ----------------

def _run_sync(spec: dict, tmp: str, on_step) -> list:
    """Chạy một notebook cho spec (một việc hoặc một lô). Trả danh sách kết quả theo thứ tự việc."""
    jobs = spec["jobs"] if spec.get("kind") == "batch" else [spec]
    gpu = False
    if needs_gpu(spec):
        try:
            gpu = kaggle_api.quota()["gpu_left"] >= GPU_MIN_LEFT
        except KaggleError:
            raise
        except Exception:
            gpu = True
    n = len(jobs)
    on_step(f"Kaggle: gửi {'lô ' + str(n) + ' việc' if n > 1 else 'job'} ({'GPU' if gpu else 'CPU'})")
    reuse = any(j.get("amb_token") for j in jobs)        # tiếng nền đã tách ở phiên phân tích: gắn vào, khỏi tách lại
    kaggle_api.push(SLUG, "rmvideo worker", kernel_text(spec), gpu=gpu,
                    kernel_sources=[SLUG_ANALYZE] if reuse else None)

    def st(s, secs):
        on_step(f"Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} ({secs / 60:.0f} phút)")
    _ABORT.update(flag=False, running=True, slug=SLUG)
    try:
        kaggle_api.wait(SLUG, TIMEOUT + TIMEOUT_PER_EXTRA * (n - 1), on_status=st, stop=lambda: _ABORT["flag"])
    finally:
        _ABORT["running"] = False
    d = kaggle_api.outputs(SLUG)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    out = []
    for k in range(n):
        tag = f"_{k}" if spec.get("kind") == "batch" else ""
        try:
            if f"result{tag}.json" not in files:
                raise KaggleError("Kaggle không trả kết quả. Log: " + kaggle_api.log_tail(d, 600))
            rj = os.path.join(tmp, f"kaggle_result{tag}.json")
            kaggle_api.download(files[f"result{tag}.json"], rj, 5 << 20)
            res = json.load(open(rj, encoding="utf-8"))
            if not res.get("ok"):
                raise KaggleError("Việc trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
            if "token" in res and jobs[k].get("token") and res["token"] != jobs[k]["token"]:
                raise KaggleError("kết quả trên Kaggle không phải của việc này (notebook bị việc khác ghi đè)")
            if f"output{tag}.mp4" not in files:
                raise KaggleError("Kaggle không trả video")
            on_step(f"Kaggle: tải video về{f' ({k + 1}/{n})' if n > 1 else ''}")
            vid = os.path.join(tmp, f"output{tag}.mp4")
            kaggle_api.download(files[f"output{tag}.mp4"], vid)
            p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", vid],
                               capture_output=True, text=True, timeout=60)
            try:
                dur = float(p.stdout.strip())
            except ValueError:
                dur = 0.0
            if dur < 1:
                raise KaggleError("video Kaggle trả về hỏng")
            res["video"], res["gpu"] = vid, gpu
            out.append(res)
        except Exception as e:
            out.append(e if isinstance(e, KaggleError) else KaggleError(str(e)))
    return out


TIMEOUT_ANALYZE = 30 * 60        # phân tích: hàng chờ máy + cài thư viện + tải model Whisper + chạy


def _run_analysis_sync(spec: dict, tmp: str, on_step) -> dict:
    """Phiên phân tích: trả kết quả (đã đọc result.json) + đường dẫn gói ảnh / tiếng nền đã tách nếu có."""
    gpu = True
    try:
        gpu = kaggle_api.quota()["gpu_left"] >= GPU_MIN_LEFT
    except KaggleError:
        raise
    except Exception:
        pass
    if not gpu:
        raise KaggleError("hết giờ GPU tuần này (Whisper trên CPU quá chậm): bóc lời trên Botkeep")
    on_step("Bóc lời + phân tích hình trên Kaggle: gửi việc (GPU)")
    src = [SLUG_PHOWHISPER] if spec.get("asr") == "phowhisper" else None
    try:
        kaggle_api.push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(spec), gpu=True, kernel_sources=src)
    except KaggleError as e:
        if not src:
            raise
        on_step("Kaggle: chưa có notebook PhoWhisper, dùng large-v3")   # tài khoản chưa chạy rmvideo-phowhisper
        logging.warning("Không gắn được notebook %s (%s): bóc lời bằng large-v3", SLUG_PHOWHISPER, str(e)[:120])
        spec = {**spec, "asr": "large-v3"}
        kaggle_api.push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(spec), gpu=True)

    def st(s, secs):
        on_step(f"Bóc lời + phân tích hình trên Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} "
                f"({secs / 60:.0f} phút)")
    _ABORT.update(flag=False, running=True, slug=SLUG_ANALYZE)
    try:
        kaggle_api.wait(SLUG_ANALYZE, TIMEOUT_ANALYZE, every=15, on_status=st, stop=lambda: _ABORT["flag"])
    finally:
        _ABORT["running"] = False
    d = kaggle_api.outputs(SLUG_ANALYZE)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    if "result.json" not in files:
        raise KaggleError("Kaggle không trả kết quả phân tích. Log: " + kaggle_api.log_tail(d, 600))
    rj = os.path.join(tmp, "kaggle_analysis.json")
    kaggle_api.download(files["result.json"], rj, 20 << 20)
    res = json.load(open(rj, encoding="utf-8"))
    if not res.get("ok"):
        raise KaggleError("Phân tích trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
    if res.get("token") != spec.get("token"):
        raise KaggleError("kết quả phân tích không phải của job này (notebook bị việc khác ghi đè)")
    on_step("Bóc lời + phân tích hình trên Kaggle: tải kết quả về")
    for name, key in (("thumbs.zip", "thumbs_zip"), ("ambient.flac", "ambient")):
        if name in files:
            p = os.path.join(tmp, f"kaggle_{name}")
            try:
                kaggle_api.download(files[name], p, 400 << 20)
                res[key] = p
            except Exception as e:                  # thiếu ảnh / tiếng nền: vẫn dùng được lời đã bóc
                logging.warning("Không tải được %s từ Kaggle: %s", name, e)
    return res


async def analyze(spec: dict, tmp: str, on_step=None) -> dict:
    """Bóc lời + phân tích hình trên Kaggle. Trả kết quả thô (whisper, cuts, bands, ...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = await asyncio.to_thread(_run_analysis_sync, spec, tmp, on_step)
    finally:
        release(spec)
    logging.info("Kaggle phân tích xong: %s", res.get("timings"))
    return res


async def produce(spec: dict, tmp: str, on_step=None) -> dict:
    """Một việc trên Kaggle (mỗi lúc một phiên). Trả kết quả (video, plan, srt, notes...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = (await asyncio.to_thread(_run_sync, spec, tmp, on_step))[0]
    finally:
        release(spec)
    if isinstance(res, Exception):
        raise res
    logging.info("Kaggle xong: %s", res.get("timings"))
    return res


async def produce_batch(specs: list, tmp: str, on_step=None) -> list:
    """Nhiều việc trong MỘT phiên (nạp model + tải nguồn một lần). Trả danh sách: kết quả hoặc KaggleError từng việc."""
    on_step = on_step or (lambda s: None)
    shared = ("settings", "logo_b64", "ref_b64", "ref_ext")       # giống nhau ở mọi việc: gói một lần (notebook < 1MB)
    spec = {"kind": "batch", "jobs": [{k: v for k, v in s.items() if k not in shared} for s in specs],
            **{k: specs[0].get(k) for k in shared if specs[0].get(k)}}
    try:
        async with _LOCK:
            return await asyncio.to_thread(_run_sync, spec, tmp, on_step)
    except Exception as e:                       # mọi lỗi (kể cả lỗi gói notebook) -> từng phần làm trên Botkeep
        return [e if isinstance(e, KaggleError) else KaggleError(str(e)[:300])] * len(specs)
    finally:
        release(spec)


# ---------------- gom lô (video dài cắt nhiều phần) ----------------

class Deferred:
    """Phần đã chuẩn bị xong (lời đã chốt), chờ chạy chung một phiên Kaggle với các phần khác."""
    def __init__(self, spec: dict, finish, approved: list | None = None):
        self.spec, self.finish, self.approved = spec, finish, approved or []


_COLLECT = contextvars.ContextVar("kaggle_collect", default=None)


def collecting() -> bool:
    return _COLLECT.get() is not None


class collect:
    """with collect(): process() (cùng ngữ cảnh asyncio) trả Deferred thay vì chạy Kaggle ngay."""
    def __enter__(self):
        self._tok = _COLLECT.set([])
        return self

    def __exit__(self, *a):
        _COLLECT.reset(self._tok)
        return False
