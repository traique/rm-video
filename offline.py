"""Gói tuỳ chọn chạy offline (cài bằng lệnh console `setup_offline`, gỡ bằng `remove_offline`), ~185MB:

- Tách giọng nhanh Spleeter 2 nguồn int8 (sherpa-onnx, MIT): nhanh hơn Kim_Vocal_2 ~30 lần trên 1 lõi (RTF 0.06
  so với 1.8), nên dùng được cho video có > 45 giây lời — trước đây những video này phải TẮT hẳn tiếng nền lúc
  người gốc nói. Tiếng nền sạch kém Kim_Vocal_2 một chút, nên video ngắn vẫn dùng Kim.
- Giọng đọc offline Piper vi_VN vais1000 (sherpa-onnx): tầng dự phòng CUỐI khi cả CapCut và edge-tts đều lỗi
  (TTS_OFFLINE=1). Đo: nhanh hơn thời gian thực ~10 lần, Gemini nghe lại đúng 36/36 từ. LƯU Ý GIẤY PHÉP: trọng số
  gắn MIT nhưng tinh chỉnh từ giọng lessac (dữ liệu Blizzard 2011 chỉ cho nghiên cứu) — cân nhắc trước khi dùng
  cho video kiếm tiền.
- Model dò chữ PP-OCRv6 tiny (1.8MB, Apache-2.0) cho TEXT_DETECT=ocr (xem ocr_text.py).

Mọi file tải về đều kiểm SHA-256; sherpa-onnx ghim < 2 (bản 2.0 bỏ espeak-ng mà giọng Piper cần).
Tách giọng và đọc giọng chạy trong TIẾN TRÌNH RIÊNG để RAM được trả lại ngay khi xong.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.environ.get("OFFLINE_MODELS") or os.path.join(BASE, "models")   # biến môi trường: đổi chỗ để model
REL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
PACKS = {
    "spleeter": (f"{REL}/source-separation-models/sherpa-onnx-spleeter-2stems-int8.tar.bz2",
                 "c5dc8edfa08cc9a6d82670fcca15cfe671e446385e1a28c9da8c1816d238ea17", "sherpa-onnx-spleeter-2stems-int8"),
    "piper": (f"{REL}/tts-models/vits-piper-vi_VN-vais1000-medium.tar.bz2",
              "fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a", "vits-piper-vi_VN-vais1000-medium"),
}
SHERPA_SPEC = "sherpa-onnx>=1.12,<2"
SR = 44100
CHUNK_S = 10.0            # Spleeter cả bài 300s cần 4.6GB RAM; từng khúc 10s chỉ ~600MB
CONTEXT_S = 0.5           # mỗi khúc tách kèm 0.5s hai bên rồi cắt bỏ: chỗ nối hai khúc liền mạch hơn
XFADE = 2205              # 50ms vuốt giữa khúc đã tách và khúc giữ nguyên


def _dir(name: str) -> str:
    return os.path.join(MODELS, PACKS[name][2])


def sherpa_ok() -> bool:
    """Có sherpa-onnx chưa — chỉ tìm, KHÔNG nạp vào tiến trình chính (nạp tốn ~18MB RAM mãi mãi)."""
    import importlib.util
    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def spleeter_available() -> bool:
    return os.path.exists(os.path.join(_dir("spleeter"), "vocals.int8.onnx")) and sherpa_ok()


def piper_available() -> bool:
    return os.path.exists(os.path.join(_dir("piper"), "vi_VN-vais1000-medium.onnx")) and sherpa_ok()


def status() -> str:
    import ocr_text
    have = [n for n, ok in (("tách giọng nhanh", spleeter_available()), ("giọng offline", piper_available()),
                            ("dò chữ OCR", ocr_text.available())) if ok]
    return ", ".join(have) or "chưa cài"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, sha: str, dst: str):
    tmp = dst + ".part"
    urllib.request.urlretrieve(url, tmp)
    got = _sha256(tmp)
    if got != sha:
        os.remove(tmp)
        raise RuntimeError(f"{os.path.basename(dst)} sai checksum ({got[:12]}…), đã xoá")
    os.replace(tmp, dst)


async def install(log=logging.info) -> bool:
    import ocr_text
    import potoken_setup
    free = shutil.disk_usage(BASE).free >> 20
    if free < 500:
        log(f"❌ Ổ đĩa còn {free}MB, cần ≥ 500MB trống (gói chiếm ~185MB).")
        return False
    os.makedirs(MODELS, exist_ok=True)
    log("1/3 Cài sherpa-onnx (< 2)...")
    if not await potoken_setup._pip([SHERPA_SPEC], log, 600):
        return False
    for i, (name, (url, sha, d)) in enumerate(PACKS.items(), 2):
        if os.path.isdir(os.path.join(MODELS, d)):
            continue
        log(f"{i}/3 Tải {name}...")
        arc = os.path.join(MODELS, os.path.basename(url))
        tmpdir = os.path.join(MODELS, f".{d}.part")
        try:
            await asyncio.to_thread(_download, url, sha, arc)
            shutil.rmtree(tmpdir, ignore_errors=True)
            with tarfile.open(arc, "r:bz2") as tf:      # giải nén vào thư mục tạm rồi đổi tên: không để gói dở dang
                tf.extractall(tmpdir, filter="data") if sys.version_info >= (3, 12) else tf.extractall(tmpdir)
            os.replace(os.path.join(tmpdir, d), os.path.join(MODELS, d))
        except Exception as e:
            log(f"❌ {name}: {e}")
            return False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            if os.path.exists(arc):
                os.remove(arc)
    if not ocr_text.available():
        log("Tải model dò chữ PP-OCRv6 tiny (1.8MB)...")
        try:
            await asyncio.to_thread(_download, ocr_text.MODEL_URL, ocr_text.MODEL_SHA256, ocr_text.model_path())
        except Exception as e:
            log(f"❌ model dò chữ: {e}")
            return False
    log("✅ Xong gói offline (khởi động lại bot để nạp sherpa-onnx). Bật trong Cài đặt: TEXT_DETECT=ocr, "
        "TTS_OFFLINE=1; tách giọng nhanh tự dùng khi người gốc nói > 45s.")
    return True


def remove(log=logging.info):
    import ocr_text
    for name in PACKS:
        shutil.rmtree(_dir(name), ignore_errors=True)
    if os.path.exists(ocr_text.model_path()):
        os.remove(ocr_text.model_path())
    log("Đã gỡ model offline (sherpa-onnx vẫn còn trong pip, gỡ bằng pip uninstall sherpa-onnx nếu cần).")


# ---------------- tách giọng nhanh (chạy trong tiến trình con) ----------------
def _decode(path: str, max_sec: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", f"{max_sec:.2f}", "-vn", "-ac", "2", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()


def accompaniment(mix: np.ndarray, windows, separate) -> np.ndarray:
    """Tiếng nền = Spleeter chỉ trên các khúc 10s có lời gốc; khúc không có lời giữ NGUYÊN tiếng gốc (Spleeter làm
    tiếng nền kém tự nhiên hơn), vuốt 50ms ở chỗ nối. separate((2, n) float32) -> (2, n) tiếng nền."""
    n = mix.shape[1]
    step, ctx = int(CHUNK_S * SR), int(CONTEXT_S * SR)
    out = mix.copy()
    need = [windows is None or any(a * SR < i + step and b * SR > i for a, b in windows) for i in range(0, n, step)]
    for k, i in enumerate(range(0, n, step)):
        if not need[k]:
            continue
        lo, hi = max(0, i - ctx), min(n, i + step + ctx)
        seg = separate(np.ascontiguousarray(mix[:, lo:hi]))[:, i - lo:i - lo + min(step, n - i)]
        if k > 0 and not need[k - 1]:                 # vào từ khúc giữ nguyên: vuốt từ gốc sang đã tách
            r = np.linspace(0, 1, min(XFADE, seg.shape[1]), dtype=np.float32)
            seg[:, :len(r)] = seg[:, :len(r)] * r + mix[:, i:i + len(r)] * (1 - r)
        if k + 1 < len(need) and not need[k + 1]:     # ra khúc giữ nguyên
            r = np.linspace(1, 0, min(XFADE, seg.shape[1]), dtype=np.float32)
            j = i + seg.shape[1] - len(r)
            seg[:, -len(r):] = seg[:, -len(r):] * r + mix[:, j:j + len(r)] * (1 - r)
        out[:, i:i + seg.shape[1]] = seg
    return out


def _spleeter():
    import sherpa_onnx
    d = _dir("spleeter")
    cfg = sherpa_onnx.OfflineSourceSeparationConfig(model=sherpa_onnx.OfflineSourceSeparationModelConfig(
        spleeter=sherpa_onnx.OfflineSourceSeparationSpleeterModelConfig(
            vocals=f"{d}/vocals.int8.onnx", accompaniment=f"{d}/accompaniment.int8.onnx"),
        num_threads=1, provider="cpu"))
    sp = sherpa_onnx.OfflineSourceSeparation(cfg)

    def separate(x):
        """Spleeter cắt đầu ra xuống bội số 1024 mẫu (đuôi mỗi khúc còn nguyên giọng gốc) và lỗi với đầu vào
        < 4096 mẫu: đệm 0 cho đủ rồi cắt về đúng độ dài."""
        n = x.shape[1]
        size = max(4096, -(-n // 1024) * 1024 + 1024)
        xp = np.zeros((x.shape[0], size), np.float32)
        xp[:, :n] = x
        o = sp.process(sample_rate=SR, samples=xp)
        if o.sample_rate != SR:
            raise RuntimeError(f"Spleeter trả {o.sample_rate}Hz")
        acc = np.asarray(o.stems[1].data, np.float32)
        if acc.shape[1] < n:
            raise RuntimeError(f"Spleeter trả thiếu mẫu ({acc.shape[1]} < {n})")
        return acc[:, :n]
    return separate


def _write_wav(path: str, x: np.ndarray, sr: int, ch: int):
    pcm = (np.clip(x.T if ch == 2 else x, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(sr), "-ac", str(ch), "-i", "-", path],
                   input=pcm, check=True)


def _sep_main(src: str, out: str, windows, max_sec: float = 320.0):
    mix = _decode(src, max_sec)
    _write_wav(out, accompaniment(mix, windows, _spleeter()), SR, 2)


async def remove_voice_fast(src: str, out_wav: str, windows=None, timeout: float = 600) -> bool:
    """Như vocal_sep.remove_voice nhưng bằng Spleeter. False nếu chưa cài/lỗi -> caller dùng cổng tắt tiếng nền."""
    if not spleeter_available():
        return False
    import core_video
    proc = await asyncio.create_subprocess_exec(sys.executable, os.path.abspath(__file__), "sep", src, out_wav,
                                                json.dumps(windows), stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    core_video.RUNNING.add(proc)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logging.warning("Tách giọng nhanh quá %ss, bỏ qua", timeout)
        return False
    except asyncio.CancelledError:
        proc.kill()
        raise
    finally:
        core_video.RUNNING.discard(proc)
    if proc.returncode == 0 and os.path.exists(out_wav):
        return True
    logging.warning("Tách giọng nhanh lỗi: %s", err.decode(errors="ignore").strip()[-200:])
    return False


# ---------------- giọng đọc offline ----------------
def _tts_main(text: str, out: str):
    import sherpa_onnx
    d = _dir("piper")
    tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=f"{d}/vi_VN-vais1000-medium.onnx", tokens=f"{d}/tokens.txt",
                                                      data_dir=f"{d}/espeak-ng-data"),
            num_threads=1, provider="cpu"),
        max_num_sentences=1))
    a = tts.generate(text, sid=0, speed=1.0)
    s = np.asarray(a.samples, np.float32)
    if not len(s):
        raise RuntimeError("Piper trả về rỗng")
    _write_wav(out, s, a.sample_rate, 1)


async def speak(text: str, out_path: str) -> None:
    """Đọc `text` bằng Piper vào out_path (định dạng theo đuôi file, vd .mp3). Ném lỗi nếu không được."""
    if not piper_available():
        raise RuntimeError("chưa cài giọng offline (lệnh setup_offline)")
    import core_video
    proc = await asyncio.create_subprocess_exec(sys.executable, os.path.abspath(__file__), "tts", text, out_path,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    core_video.RUNNING.add(proc)                 # huỷ job / quá hạn là giết được
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 120)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        raise
    finally:
        core_video.RUNNING.discard(proc)
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("Piper lỗi: " + err.decode(errors="ignore").strip()[-200:])


if __name__ == "__main__":
    if sys.argv[1] == "sep":
        _sep_main(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]) if len(sys.argv) > 4 else None)
    elif sys.argv[1] == "tts":
        _tts_main(sys.argv[2], sys.argv[3])
