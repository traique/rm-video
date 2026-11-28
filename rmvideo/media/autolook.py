"""Tự chọn tông màu theo từng video (BRAND_LOOK=auto, bản 1.0.0).

Đo 8 khung rải đều video gốc (bỏ 5% đầu / cuối: thẻ mở, màn kết của kênh gốc), thu nhỏ còn 64 điểm ngang: độ sáng
trung bình, độ tươi (bão hoà HSV), độ ấm (đỏ trừ xanh lam). Dải đen viền trên / dưới (video ngang đặt trong khung dọc)
không tính, không thì video sáng bị coi là tối.

Luật (video nấu ăn):
- ánh sáng lạnh / ám xanh            -> Ấm mộc (kéo ấm lại, món trông ngon hơn)
- tối, hoặc màu nhạt / xỉn           -> Tươi sáng (sáng + tươi, trắng sạch)
- đã ám vàng ấm sẵn (đèn vàng)       -> Tươi sáng (không làm vàng thêm như Gốc / Ấm mộc)
- còn lại (đủ sáng, màu ổn)          -> Gốc (công thức màu đã duyệt)
Không đọc được khung nào -> Tươi sáng (mặc định trước 1.0.0).

Chọn MỘT lần mỗi job (filtergraph.AUTO_LOOK, theo ngữ cảnh của job): video, đoạn mở đầu, đoạn chen, ảnh bìa cùng tông.
Botkeep chọn trên file nguồn và gửi kèm việc Kaggle (spec["look"]); Kaggle tự chọn khi việc không mang sẵn.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Callable

import config
from rmvideo.media import filtergraph

FALLBACK = "tuoi_sang"
NAMES = {"goc": "Gốc", "am_moc": "Ấm mộc", "tuoi_sang": "Tươi sáng", "dien_anh": "Điện ảnh"}
SAMPLES = 8
WIDTH = 64
# ngưỡng (thang 0-255 cho sáng / ấm, 0-1 cho tươi), chỉnh trên video nấu ăn thật (xem tests/test_v100.py)
DARK = 92.0          # sáng trung bình dưới mức này: tối
DULL = 0.20          # độ tươi trung bình dưới mức này: màu xỉn
COLD = 3.0           # đỏ - xanh lam dưới mức này: trắng lạnh / ám xanh (món ăn nhợt)
WARM = 38.0          # đỏ - xanh lam trên mức này: đã ám vàng ấm


def pick(stats: dict) -> tuple[str, str]:
    """{luma, sat, warmth} -> (tông, lý do ngắn)."""
    luma, sat, warm = float(stats["luma"]), float(stats["sat"]), float(stats["warmth"])
    if warm < COLD:
        return "am_moc", "ánh sáng gốc ám xanh / lạnh"
    if luma < DARK:
        return "tuoi_sang", "video gốc hơi tối"
    if sat < DULL:
        return "tuoi_sang", "màu gốc nhạt"
    if warm > WARM:
        return "tuoi_sang", "video gốc đã ám vàng ấm, không làm vàng thêm"
    return "goc", "ánh sáng và màu gốc ổn"


def _frame(path: str, t: float) -> bytes:
    # -skip_frame nokey: chỉ giải mã khung khoá (lấy khung khoá kế tiếp sau giây t, đủ để đo màu): 8 khung của nguồn
    # 1080p HEVC 197 giây mất 1,4 giây thay vì 3,4 giây. Video khung khoá thưa (sau t không còn khung khoá nào) thì
    # giải mã thường.
    for fast in (True, False):
        cmd = ["ffmpeg", "-v", "error", "-threads", "2", *(["-skip_frame", "nokey"] if fast else []),
               "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-an",
               "-vf", f"scale={WIDTH}:-2:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    return b""


def frame_stats(path: str, dur: float, samples: int = SAMPLES) -> dict | None:
    """Đo các khung (đồng bộ: gọi qua asyncio.to_thread). None = không đọc được khung nào."""
    import numpy as np
    if dur <= 0:
        return None
    lumas, sats, warms = [], [], []
    for k in range(samples):
        raw = _frame(path, dur * (0.05 + 0.9 * (k + 0.5) / samples))
        if len(raw) < WIDTH * 3 * 4:
            continue
        rows = len(raw) // (WIDTH * 3)
        px = np.frombuffer(raw[:rows * WIDTH * 3], dtype=np.uint8).reshape(rows, WIDTH, 3).astype(np.float32)
        y = px @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        keep = ~((y.mean(axis=1) < 16) & (y.std(axis=1) < 6))       # bỏ dải đen viền (hàng tối đều)
        if keep.sum() < rows * 0.3:
            continue                                                # khung gần như đen hẳn (chuyển cảnh): bỏ
        px, y = px[keep], y[keep]
        mx, mn = px.max(axis=2), px.min(axis=2)
        sat = np.where(mx > 8, (mx - mn) / np.maximum(mx, 1), 0.0)
        lumas.append(float(y.mean()))
        sats.append(float(sat.mean()))
        warms.append(float((px[..., 0] - px[..., 2]).mean()))
    if not lumas:
        return None
    med = lambda v: float(np.median(v))                             # noqa: E731 — trung vị: một cảnh lạ không kéo lệch
    return {"luma": round(med(lumas), 1), "sat": round(med(sats), 3), "warmth": round(med(warms), 1),
            "frames": len(lumas)}


async def ensure(path: str, dur: float = 0.0, note: Callable[[str], None] | None = None) -> str:
    """BRAND_LOOK=auto: chọn tông cho job này (một lần). Trả tông đang dùng ('' khi không ở chế độ tự động)."""
    if (config.BRAND_LOOK or "") != "auto":
        return ""
    have = filtergraph.AUTO_LOOK.get()
    if have:
        return have
    stats = None
    try:
        if not dur:
            from rmvideo.media import ff
            dur = await ff.probe_duration(path)
        stats = await asyncio.to_thread(frame_stats, path, dur)
    except Exception as e:                                          # đo hỏng không được làm hỏng job
        logging.warning("Không đo được màu video để chọn tông: %s", str(e)[:150])
    name, why = pick(stats) if stats else (FALLBACK, "không đo được khung hình")
    filtergraph.AUTO_LOOK.set(name)
    logging.info("Tông màu tự chọn: %s (%s) %s", name, why, stats or "")
    if note:
        note(f"🎨 Tông màu tự chọn: {NAMES.get(name, name)} ({why}). ")
    return name
