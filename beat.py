"""Dò BPM + mốc beat đầu tiên bằng numpy thuần (không cần librosa/aubio nặng nề).
Đủ chính xác cho nhạc có beat rõ (dance/pop/EDM) — đúng thể loại video nhảy."""
import asyncio
import subprocess

import numpy as np

SR = 22050
HOP = 512
_FPS = SR / HOP  # ~43 khung onset / giây


def _decode(path: str) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-t", "90", "-vn",
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32)


def _onset_env(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    if n < int(_FPS * 5):
        raise RuntimeError("audio quá ngắn để dò beat (< 5s)")
    e = np.sqrt((x[: n * HOP].reshape(n, HOP) ** 2).mean(1))
    d = np.diff(e, prepend=e[0])
    d[d < 0] = 0
    return d / d.max() if d.max() > 0 else d


def _analyze_sync(path: str) -> tuple[float, float]:
    env = _onset_env(_decode(path))
    env0 = env - env.mean()
    ac = np.correlate(env0, env0, "full")[len(env0) - 1:]
    lo, hi = int(_FPS * 60 / 180), int(_FPS * 60 / 60) + 1  # 180..60 BPM
    lag = lo + int(np.argmax(ac[lo:hi]))

    # autocorr của tín hiệu tuần hoàn đỉnh đều ở mọi bội số chu kỳ -> hay bắt nhầm nửa BPM.
    # Chia đôi lag chừng nào nửa đó vẫn là đỉnh đủ mạnh (>=60% đỉnh gốc) và còn trong dải.
    def _local_peak(c):
        a, b = max(lo, c - 2), min(hi, c + 3)
        return a + int(np.argmax(ac[a:b]))
    while lag // 2 >= lo:
        half = _local_peak(lag // 2)
        if ac[half] >= 0.6 * ac[lag]:
            lag = half
        else:
            break

    # nội suy parabol quanh đỉnh: lag nguyên (~43fps) gây lệch tới ±3 BPM ở tempo cao
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-9:
            lag = lag + 0.5 * (a - c) / denom
    bpm = 60.0 * _FPS / lag

    # pha: dịch lược impulse theo chu kỳ, chỗ nào trúng nhiều onset nhất là beat đầu
    period = 60.0 * _FPS / bpm
    scores = [
        env[np.arange(p, len(env), period).astype(int)].sum()
        for p in range(max(1, int(period)))
    ]
    first_beat = int(np.argmax(scores)) / _FPS
    return float(bpm), float(first_beat)


async def analyze(path: str) -> tuple[float, float]:
    """Trả về (bpm, thời điểm beat đầu tiên tính bằng giây)."""
    return await asyncio.to_thread(_analyze_sync, path)


def compute_sync(bpm_v: float, beat_v: float, bpm_m: float, beat_m: float) -> dict:
    """Tính tốc độ video + cắt/trễ nhạc để beat nhạc mới rơi đúng nhịp nhảy.
    Thử cả tỉ lệ x2 / :2 (autocorr hay bắt nhầm bội số BPM), kẹp 0.8-1.25 cho tự nhiên."""
    cands = [bpm_m / bpm_v, bpm_m / (2 * bpm_v), (2 * bpm_m) / bpm_v]
    speed = min(cands, key=lambda r: abs(np.log(r)))
    speed = float(np.clip(speed, 0.8, 1.25))
    offset = beat_m - beat_v / speed
    return {
        "speed": round(speed, 4),
        "trim": round(max(0.0, offset), 3),    # cắt đầu nhạc
        "delay": round(max(0.0, -offset), 3),  # hoặc đẩy nhạc vào trễ
    }
