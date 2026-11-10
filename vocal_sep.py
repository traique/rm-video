"""Tách giọng người khỏi tiếng nền bằng model MDX-Net (UVR, Kim_Vocal_2) chạy ONNX trên CPU.
Không cần PyTorch: STFT/ISTFT viết bằng numpy. Model ~66MB tự tải lần đầu vào thư mục models/.
Dùng cho chế độ nấu ăn: giữ tiếng xèo xèo, bỏ hẳn giọng người nói gốc."""
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import urllib.request

import numpy as np

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_NAME = "Kim_Vocal_2.onnx"
MODEL_URL = ("https://github.com/TRvlvr/model_repo/releases/download/"
             "all_public_uvr_models/" + MODEL_NAME)
# SHA-256 của bản đã kiểm (66.759.214 byte, tải ngày 10/11/2026). File khác hash = bị thay/hỏng -> không chạy.
MODEL_SHA256 = "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"
_verified: set = set()
N_FFT, HOP, DIM_F, DIM_T = 7680, 1024, 3072, 256
SR = 44100
_session = None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _model_path() -> str:
    """Tải model (lần đầu) và kiểm SHA-256 mỗi lần khởi động: file bị tráo hay tải dở đều bị từ chối."""
    os.makedirs(_DIR, exist_ok=True)
    p = os.path.join(_DIR, MODEL_NAME)
    if p in _verified:
        return p
    if os.path.exists(p) and sha256_file(p) != MODEL_SHA256:
        logging.warning("Model %s sai checksum (hỏng hoặc bị thay), tải lại", MODEL_NAME)
        os.remove(p)
    if not os.path.exists(p):
        logging.info("Tải model tách giọng %s (~66MB, chỉ lần đầu)...", MODEL_NAME)
        tmp = p + ".part"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        got = sha256_file(tmp)
        if got != MODEL_SHA256:
            os.remove(tmp)
            raise RuntimeError(f"Model tải về sai checksum ({got[:12]}…), đã xoá; tách giọng tạm tắt.")
        os.replace(tmp, p)
    _verified.add(p)
    return p


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = int(os.environ.get("SEP_THREADS", "2"))   # Botkeep 1.5 vCPU
        opt.inter_op_num_threads = 1
        # Botkeep chỉ 2GB RAM: tắt arena + mem pattern (mặc định ORT giữ ~2.5GB bộ nhớ đệm)
        opt.enable_cpu_mem_arena = False
        opt.enable_mem_pattern = False
        opt.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opt.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC   # ~1.3GB đỉnh
        _session = ort.InferenceSession(_model_path(), opt, providers=["CPUExecutionProvider"])
    return _session


_WIN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)   # hann tuần hoàn (giống torch.hann_window)


def _stft(x: np.ndarray) -> np.ndarray:
    """x: (2, n) -> (2, bins, frames) complex, center=True, pad reflect."""
    pad = N_FFT // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    n_frames = 1 + (xp.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = xp[:, idx] * _WIN                          # (2, frames, n_fft)
    return np.fft.rfft(frames, axis=-1).transpose(0, 2, 1)


def _istft(spec: np.ndarray, length: int) -> np.ndarray:
    """spec: (2, bins, frames) -> (2, length)."""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WIN
    n_frames = frames.shape[1]
    total = N_FFT + HOP * (n_frames - 1)
    out = np.zeros((2, total), np.float32)
    norm = np.zeros(total, np.float32)
    for f in range(n_frames):
        s = f * HOP
        out[:, s:s + N_FFT] += frames[:, f]
        norm[s:s + N_FFT] += _WIN ** 2
    out /= np.maximum(norm, 1e-8)
    pad = N_FFT // 2
    return out[:, pad:pad + length]


def chunks_needed(n: int, gen: int, windows, sr: int = SR) -> list:
    """Chỉ số các khúc (mỗi khúc `gen` mẫu) có chạm vào khoảng người gốc nói. windows=None: mọi khúc."""
    starts = list(range(0, n + (gen - n % gen), gen))
    if windows is None:
        return [True] * len(starts)
    return [any(a * sr < i + gen and b * sr > i for a, b in windows) for i in starts]


def _separate(mix: np.ndarray, windows=None) -> np.ndarray:
    """mix: (2, n) float32 -> vocals (2, n). windows=[(giây đầu, giây cuối)] lúc người gốc nói: các khúc ~5.7s
    không chạm khoảng nào thì bỏ qua (giọng = 0), vì chỗ đó không có lời để tách — nhanh hơn hẳn với video
    nấu ăn nhiều đoạn chỉ có tiếng xèo."""
    sess = _get_session()
    chunk = HOP * (DIM_T - 1)
    trim = N_FFT // 2
    gen = chunk - 2 * trim
    n = mix.shape[1]
    pad = gen - n % gen
    mp = np.concatenate([np.zeros((2, trim), np.float32), mix, np.zeros((2, pad + trim), np.float32)], 1)
    outs = []
    starts = list(range(0, n + pad, gen))
    need = chunks_needed(n, gen, windows)
    todo = sum(need)
    budget = float(os.environ.get("SEP_BUDGET", "180"))
    import time
    t0, done = time.time(), 0
    for idx, i in enumerate(starts):
        if not need[idx]:
            outs.append(np.zeros((2, gen), np.float32))
            continue
        tc = time.time()
        w = mp[:, i:i + chunk]
        sp = _stft(w)[:, :DIM_F]                        # (2, 3072, 256)
        x = np.stack([sp[0].real, sp[0].imag, sp[1].real, sp[1].imag])[None].astype(np.float32)
        y = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
        full = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
        full[0, :DIM_F] = y[0] + 1j * y[1]
        full[1, :DIM_F] = y[2] + 1j * y[3]
        outs.append(_istft(full, chunk)[:, trim:-trim])
        dt = time.time() - tc
        done += 1
        print(f"khúc {idx + 1}/{len(starts)}: {dt:.1f}s", file=sys.stderr, flush=True)
        if done == 1 and dt * todo > budget:     # đo khúc đầu: quá chậm thì dừng ngay, không chờ vô ích
            raise RuntimeError(f"máy quá chậm: {dt:.0f}s/khúc x {todo} khúc > ngân sách {budget:.0f}s")
    print(f"xong {todo}/{len(starts)} khúc có lời trong {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    return np.concatenate(outs, 1)[:, :n]


def _decode(path: str, max_sec: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", f"{max_sec:.2f}", "-vn", "-ac", "2",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()


def _write_wav(path: str, x: np.ndarray):
    pcm = (np.clip(x.T, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "-", path],
                   input=pcm, check=True)


def _split_sync(src: str, ambient_wav: str, max_sec: float = 320.0, windows=None):
    mix = _decode(src, max_sec)
    if mix.shape[1] < SR:
        raise RuntimeError("audio quá ngắn")
    vocals = _separate(mix, windows) * 1.035           # hệ số bù chuẩn của Kim_Vocal_2
    _write_wav(ambient_wav, mix - vocals)


NEED_MB = 1450


def free_mb() -> int:
    """RAM còn trống thật sự của container (đọc giới hạn cgroup, không phải RAM máy chủ)."""
    try:
        for base in ("/sys/fs/cgroup", "/sys/fs/cgroup/memory"):
            for lim_f, cur_f in (("memory.max", "memory.current"),
                                 ("memory.limit_in_bytes", "memory.usage_in_bytes")):
                lim_p, cur_p = os.path.join(base, lim_f), os.path.join(base, cur_f)
                if os.path.exists(lim_p) and os.path.exists(cur_p):
                    lim = open(lim_p).read().strip()
                    if lim != "max" and int(lim) < 1 << 50:
                        return (int(lim) - int(open(cur_p).read())) // (1 << 20)
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 99999


async def remove_voice(src: str, ambient_wav: str, timeout: float = 0, windows=None) -> bool:
    """Ghi tiếng nền ĐÃ BỎ GIỌNG NGƯỜI ra ambient_wav. Chạy trong TIẾN TRÌNH RIÊNG để ~1.3GB RAM
    của model được trả lại ngay khi xong. False nếu thiếu RAM/lỗi -> caller dùng cách dự phòng."""
    free = free_mb()
    if free < NEED_MB:
        logging.warning("RAM trống %dMB < %dMB, bỏ qua tách giọng AI", free, NEED_MB)
        return False
    try:
        await asyncio.to_thread(_model_path)            # tải model (lần đầu) ngoài tiến trình con
        proc = await asyncio.create_subprocess_exec(
            sys.executable, os.path.abspath(__file__), src, ambient_wav, json.dumps(windows),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        import core_video
        core_video.RUNNING.add(proc)
        timeout = timeout or float(os.environ.get("SEP_BUDGET", "180")) + 90
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            core_video.RUNNING.discard(proc)
        log = err.decode(errors="ignore").strip().splitlines()
        if proc.returncode == 0 and os.path.exists(ambient_wav):
            logging.info("Tách giọng AI: %s", log[-1] if log else "xong")
            return True
        logging.warning("Tách giọng bỏ qua (mã %s): %s", proc.returncode, (log[-1] if log else "")[-200:])
        return False
    except asyncio.TimeoutError:
        proc.kill()
        logging.warning("Tách giọng quá %ss, bỏ qua", timeout)
    except Exception:
        logging.exception("Tách giọng lỗi")
    return False


if __name__ == "__main__":
    _split_sync(sys.argv[1], sys.argv[2], windows=json.loads(sys.argv[3]) if len(sys.argv) > 3 else None)
