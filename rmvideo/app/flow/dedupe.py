"""Chống trùng bằng DẤU VÂN HÌNH (bản 42, S5): cùng một video bị nhiều trang đăng lại với chú thích khác nhau (hoặc
cùng kênh đăng cả Facebook lẫn TikTok, bản TikTok có logo TikTok chạy quanh khung) thì so độ dài + chú thích không bắt
được. Sau khi tải: 8 khung ở vị trí tương đối cố định, mỗi khung dHash 64 bit trên 80% giữa khung (bỏ logo góc), so cả
bản lật ngang. Trùng >= 6/8 khung (Hamming <= 10) = cùng video.

Tự đăng: trùng thì dừng ngay (trước khi tốn AI / Kaggle), video nguồn bị bỏ, không tính lỗi liên tiếp. Video bạn tự
gửi: chỉ ghi chú (không chặn)."""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from rmvideo.media import ff, proc

DUP_MARK = "trùng video đã làm (dấu vân hình)"
N = 8
MAX_HAMMING = 10
MIN_MATCH = 6
KEEP = 500                 # số dấu vân lưu tối đa trong autopost.json


async def _gray9x8(path: str, t: float) -> np.ndarray | None:
    try:
        r = await proc.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-an",
                            "-vf", "crop=iw*0.8:ih*0.8,scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
                           stdout=True, timeout=60)
    except Exception:
        return None
    return np.frombuffer(r.stdout[:72], dtype=np.uint8).reshape(8, 9) if len(r.stdout) >= 72 else None


def _dhash(px: np.ndarray) -> int:
    bits = (px[:, 1:] > px[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


async def fingerprint(path: str, dur: float | None = None) -> list[str]:
    """[hex dHash khung, hex dHash khung lật ngang] x 8 khung. Lỗi: [] (không so được)."""
    try:
        dur = dur or await ff.probe_duration(path)
    except Exception:
        return []
    if not dur or dur < 2:
        return []
    frames = await asyncio.gather(*[_gray9x8(path, dur * (k + 0.5) / N) for k in range(N)])
    out = []
    for px in frames:
        if px is None:
            return []
        out.append(f"{_dhash(px):016x}:{_dhash(px[:, ::-1]):016x}")
    return out


def same(a: list[str], b: list[str]) -> bool:
    if len(a) != N or len(b) != N:
        return False

    def close(x: str, y: str) -> bool:
        xn, xf = (int(h, 16) for h in x.split(":"))
        yn = int(y.split(":")[0], 16)
        return min(bin(xn ^ yn).count("1"), bin(xf ^ yn).count("1")) <= MAX_HAMMING
    return sum(close(x, y) for x, y in zip(a, b)) >= MIN_MATCH


def duplicate(fp: list[str], known: dict) -> str:
    """Khoá video đã làm có cùng dấu vân (hoặc "")."""
    if not fp:
        return ""
    return next((k for k, v in (known or {}).items() if same(fp, v)), "")


def known_fingerprints() -> dict:
    try:
        import autopost
        return dict(autopost.load_state().get("fps") or {})
    except Exception as e:
        logging.info("Không đọc được dấu vân video đã làm (%s)", str(e)[:100])
        return {}
