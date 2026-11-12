"""Phía Botkeep: gửi phần nặng của một video sang Kaggle rồi nhận video hoàn chỉnh về (xem kaggle_job.py).

Một job = một phiên notebook riêng tư `<user>/rmvideo-worker` (mỗi lần chạy là một phiên bản mới). Notebook chứa sẵn
code cần thiết (nén + base64) và kịch bản đã chốt (JSON), KHÔNG chứa key API nào. Còn giờ GPU thì chạy GPU T4 (giọng
VieNeu đọc ~15 lần nhanh hơn thời gian thực), hết giờ GPU thì chạy CPU (ONNX, ~1x thời gian thực, không tốn hạn mức).
Lỗi ở bất kỳ bước nào -> ném KaggleError, pipeline làm tiếp trên Botkeep bằng giọng edge-tts."""
import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import zipfile

import config
import kaggle_api
from kaggle_api import KaggleError

BASE = os.path.dirname(os.path.abspath(__file__))
SLUG = "rmvideo-worker"
BUNDLE = ["kaggle_job.py", "core_video.py", "config.py", "subs.py", "align.py", "cache.py", "metrics.py",
          "url_guard.py", "potoken_setup.py", "vocal_sep.py", "queue_manager.py", "vi_text.py",
          "assets/BeVietnamPro-ExtraBold.ttf"]
PIP = ("pip install -q --no-deps vieneu sea-g2p && pip install -q onnxruntime kaldi-native-fbank soxr "
       "'transformers==4.57.6' safetensors yt-dlp edge-tts")
GPU_MIN_LEFT = 20 * 60           # còn dưới 20 phút GPU trong tuần thì chạy CPU
TIMEOUT = 50 * 60                # cả hàng chờ GPU + chạy
VOICE_REF = os.path.join(BASE, "assets", "voice_ref.mp3")
_LOCK = asyncio.Lock()

# Giọng VieNeu v3 Turbo miền Nam (nhãn của model) và tốc độ đọc đo thật (chữ/giây, chỉ phần tiếng nói, 12/11/2026)
SOUTH_FEMALE = ["Mỹ Duyên", "Thục Đoan", "Thùy Dung", "Kim Thanh"]
SOUTH_MALE = ["Adam", "Thái Sơn", "Minh Triết", "Đức Trí"]
RATE = {"Mỹ Duyên": 3.08, "Adam": 4.15, "Thùy Dung": 4.5}
CLONE = "clone"


def voice_name(voice: str) -> str:
    """'vieneu:Mỹ Duyên' -> 'Mỹ Duyên'; giọng khác -> ''."""
    return voice.split(":", 1)[1] if (voice or "").startswith("vieneu:") else ""


def is_male(voice: str) -> bool:
    return voice_name(voice) in SOUTH_MALE


def words_per_sec(voice: str) -> float | None:
    name = voice_name(voice)
    if not name:
        return None
    return RATE.get(name, 3.6 if name != CLONE else 3.4)


def usable(job) -> str:
    """'' nếu job này chạy được trên Kaggle; không thì lý do (để ghi chú cho người dùng)."""
    if not voice_name(job.voice):
        return "giọng không phải VieNeu"
    if not kaggle_api.configured():
        return "chưa điền KAGGLE_USERNAME / KAGGLE_API_TOKEN trong Cài đặt"
    if not job.url:
        return "video tải lên từ máy (Kaggle cần link để tự tải)"
    if voice_name(job.voice) == CLONE and not os.path.exists(VOICE_REF):
        return "chưa tải lên đoạn giọng mẫu"
    return ""


def bundle_b64() -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in BUNDLE:
            z.write(os.path.join(BASE, rel), rel)
    return base64.b64encode(buf.getvalue()).decode()


def kernel_text(spec: dict) -> str:
    spec_b64 = base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode()
    return f'''# rm-video: job tự động (bot tạo, đừng sửa tay). Không chứa key API.
import base64, io, json, subprocess, sys, zipfile
APP = "/tmp/rm_app"
zipfile.ZipFile(io.BytesIO(base64.b64decode("{bundle_b64()}"))).extractall(APP)
r = subprocess.run({PIP!r}, shell=True, capture_output=True, text=True)
print(r.stdout[-1500:], r.stderr[-1500:])
sys.path.insert(0, APP)
import kaggle_job
kaggle_job.main(json.loads(base64.b64decode("{spec_b64}").decode()))
'''


def settings_snapshot() -> dict:
    return {k: getattr(config, k) for k in config.EDITABLE if k not in config.SECRET_KEYS
            and not k.startswith(("KAGGLE_", "FB_", "AUTO_", "ALLOWED_", "ADMIN_"))}


def build_spec(job, segments: list, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str,
               bands, pos_y, sep: dict, speech: list, max_duration: int) -> dict:
    spec = {"job": job.id, "url": job.url, "mode": job.mode, "max_duration": max_duration,
            "segments": [{k: s.get(k) for k in ("i", "start", "slot", "span", "text", "ost")} for s in segments],
            "out_dur": out_dur, "render_dur": render_dur, "stretch": stretch, "allow_hd": allow_hd, "title": title,
            "bands": bands or [], "pos_y": pos_y, "subs": bool(job.subs),
            "sep": {"ok": sep.get("ok") and "kim" in (sep.get("engines") or []), "windows": sep.get("windows")},
            "speech": [list(x) for x in speech or []], "voice": {"name": voice_name(job.voice)},
            "settings": settings_snapshot()}
    logo = os.path.join(BASE, "assets", "logo.png")
    if config.BRAND_LOGO != "0" and os.path.exists(logo):
        spec["logo_b64"] = base64.b64encode(open(logo, "rb").read()).decode()
    if voice_name(job.voice) == CLONE:
        spec["ref_b64"] = base64.b64encode(open(VOICE_REF, "rb").read()).decode()
        spec["ref_ext"] = ".mp3"
    return spec


def _run_sync(spec: dict, tmp: str, on_step) -> dict:
    try:
        q = kaggle_api.quota()
        gpu = q["gpu_left"] >= GPU_MIN_LEFT
    except KaggleError:
        raise
    except Exception:
        gpu = True
    on_step(f"Kaggle: gửi job ({'GPU' if gpu else 'CPU, hết giờ GPU tuần này'})")
    kaggle_api.push(SLUG, "rmvideo worker", kernel_text(spec), gpu=gpu)

    def st(s, secs):
        on_step(f"Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đọc giọng + tách giọng + dựng'} "
                f"({secs / 60:.0f} phút)")
    kaggle_api.wait(SLUG, TIMEOUT, on_status=st)
    d = kaggle_api.outputs(SLUG)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    if "result.json" not in files:
        raise KaggleError("Kaggle không trả kết quả. Log: " + kaggle_api.log_tail(d, 600))
    rj = os.path.join(tmp, "kaggle_result.json")
    kaggle_api.download(files["result.json"], rj, 5 << 20)
    res = json.load(open(rj, encoding="utf-8"))
    if not res.get("ok"):
        raise KaggleError("Job trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
    if "output.mp4" not in files:
        raise KaggleError("Kaggle không trả video")
    on_step("Kaggle: tải video về")
    out = os.path.join(tmp, "output.mp4")
    kaggle_api.download(files["output.mp4"], out)
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                       capture_output=True, text=True, timeout=60)
    try:
        dur = float(p.stdout.strip())
    except ValueError:
        dur = 0.0
    if dur < 1:
        raise KaggleError("video Kaggle trả về hỏng")
    res["video"] = out
    res["gpu"] = gpu
    return res


async def produce(spec: dict, tmp: str, on_step=None) -> dict:
    """Chạy job trên Kaggle (mỗi lúc một job). Trả result (video, plan, srt, notes...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    async with _LOCK:
        res = await asyncio.to_thread(_run_sync, spec, tmp, on_step)
    logging.info("Kaggle xong: %s", res.get("timings"))
    return res
