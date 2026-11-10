"""Dò dải phụ đề/chữ in cứng trong video để che đi và đặt phụ đề mới lên đúng chỗ.
Nguyên lý: chữ phụ đề là pixel sáng nằm sát pixel tối (viền/bóng), cố định ở một dải ngang
qua phần lớn video, trong khi cảnh quay thay đổi. numpy thuần, ~1s cho video 90s."""
import asyncio
import subprocess

import numpy as np

_W, _H, _N = 270, 480, 16


def _detect_sync(path: str) -> list[tuple[float, float]]:
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout or 0)
    if dur <= 0:
        return []
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={_N / dur:.5f},scale={_W}:{_H},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    return bands_from_raw(raw)


def bands_from_raw(raw: bytes) -> list[tuple[float, float]]:
    """Dò dải chữ từ khung xám 270x480 đã trích sẵn (scenes.analyze trích cùng lượt với dò cảnh)."""
    n = len(raw) // (_W * _H)
    if n < 4:
        return []
    F = np.frombuffer(raw[: n * _W * _H], np.uint8).reshape(n, _H, _W).astype(np.float32)
    P = np.pad(F, ((0, 0), (1, 1), (1, 1)), mode="edge")
    mn = np.min(np.stack([P[:, dy:dy + _H, dx:dx + _W] for dy in range(3) for dx in range(3)]), axis=0)
    text = (F > 165) & (mn < 90)
    c0, c1 = int(_W * 0.15), int(_W * 0.85)       # bỏ 15% mỗi bên: watermark/logo góc
    per_frame = text[:, :, c0:c1].mean(axis=2)     # (khung, hàng)
    score = per_frame.mean(axis=0)
    persist = (per_frame > 0.02).mean(axis=0)
    ok = (score > 0.03) & (persist >= 0.5)

    bands, cur = [], None
    for y, v in enumerate(ok):
        if v:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur:
            bands.append(cur)
            cur = None
    if cur:
        bands.append(cur)
    bands = [(a / _H, (b + 1) / _H) for a, b in bands if b - a >= 2]

    merged = []  # gộp các dải cách nhau < 3% (phụ đề 2 dòng)
    for a, b in bands:
        if merged and a - merged[-1][1] < 0.03:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


async def detect_text_bands(path: str) -> list[tuple[float, float]]:
    """Trả về danh sách (top, bottom) theo tỉ lệ chiều cao 0..1, rỗng nếu không có chữ in cứng."""
    try:
        return await asyncio.to_thread(_detect_sync, path)
    except Exception:
        return []
