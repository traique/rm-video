"""Khung dọc theo nội dung (1.1.2, đề xuất «khung dọc thông minh»): trước đây mọi nguồn đều phóng to rồi cắt CHÍNH GIỮA
(filtergraph.look_chain). Nguồn ngang 16:9 (YouTube, máy quay ngang) chỉ còn ~32% bề ngang ở giữa — chảo nằm lệch là mất;
nguồn dọc có sẵn dải đen trên/dưới (reup video ngang) giữ nguyên dải đen trong video ra.

Chọn MỘT lần mỗi job trên Botkeep (rẻ: ~12 cặp khung xám 240px), ghi vào FRAMING (theo ngữ cảnh của job, như AUTO_LOOK) và
gửi kèm việc Kaggle (spec["framing"]):
- dải đen: hàng / cột tối đều ở MỌI khung mẫu -> cắt bỏ (vùng nội dung `box`);
- nội dung dọc (rộng/cao ≤ TALL_MAX): như cũ, cắt giữa;
- nội dung ngang: đo chuyển động theo cột (tay, đũa, chảo). Chuyển động dồn trong một cửa sổ 9:16 (≥ FOCUS) -> cắt cửa sổ đó
  thay vì cắt giữa; rải rộng khắp khung (hoặc không đo được) mà nguồn rất ngang (≥ PAD_MIN) -> đặt nguyên khung lên nền mờ
  của chính nó (không mất gì), nguồn hơi ngang -> cắt quanh tâm chuyển động.
Dải chữ gốc (tỉ lệ trên khung gốc) dùng để che / đặt phụ đề được đổi sang khung ra bằng `map_bands`."""
from __future__ import annotations

import asyncio
import itertools
import logging
import subprocess
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import numpy as np

FRAMING: ContextVar[dict | None] = ContextVar("framing", default=None)
OUT_ASPECT = 9 / 16
TALL_MAX = 0.66          # rộng/cao ≤ chừng này (gần 9:16, kể cả 3:5): cắt giữa như cũ, chỉ bỏ dải đen
PAD_MIN = 1.2            # nguồn rộng hơn chừng này mà chuyển động rải khắp khung: nền mờ thay vì cắt
FOCUS = 0.55             # phần năng lượng chuyển động lọt trong cửa sổ 9:16 tốt nhất: từ chừng này là «có chủ thể»
SAMPLES = 8
WIDTH = 240              # khung xám để đo (rẻ; dải đen sai số ~±1/240 bề ngang, nới vào trong thêm 1 điểm)
BAR_LUMA = 22            # hàng / cột tối hơn chừng này ở mọi khung mẫu là dải đen
BAR_MIN = 0.03           # dải đen mỏng hơn 3% thì bỏ qua (viền nén, cắt 4% viền đã lo)
MOTION_MIN = 1.0         # năng lượng chuyển động trung bình (mức xám / điểm ảnh) dưới chừng này: coi như tĩnh
_LABEL = itertools.count()
Note = Callable[[str], None]


def _pair(path: str, t: float, w: int, h: int) -> np.ndarray | None:
    """Hai khung xám (w x h) cách nhau ~0,25 giây quanh giây t: (2, h, w) uint8; đọc được một khung thì nhân đôi (không
    đo được chuyển động ở đó); không đọc được thì None."""
    cmd = ["ffmpeg", "-v", "error", "-threads", "2", "-ss", f"{max(0.0, t):.2f}", "-i", path, "-an",
           "-vf", f"fps=4,scale={w}:{h}:flags=area,format=gray", "-frames:v", "2", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=40).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    k = len(raw) // (w * h)
    if k < 1:
        return None
    a = np.frombuffer(raw[:k * w * h], np.uint8).reshape(k, h, w)
    return a[:2] if k >= 2 else np.stack([a[0], a[0]])


def bars(frames: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) vùng nội dung (toạ độ khung đo) sau khi bỏ dải đen có ở MỌI khung: frames (n, h, w) xám."""
    n, h, w = frames.shape
    rows = frames.max(axis=0).astype(np.float32)               # sáng nhất qua mọi khung: cảnh tối không bị nhận là dải
    dark_r = (rows.mean(axis=1) < BAR_LUMA) & (rows.std(axis=1) < 8)
    dark_c = (rows.mean(axis=0) < BAR_LUMA) & (rows.std(axis=0) < 8)

    def run(mask: np.ndarray) -> int:
        k = 0
        while k < len(mask) and mask[k]:
            k += 1
        return k
    y0, y1 = run(dark_r), h - run(dark_r[::-1])
    x0, x1 = run(dark_c), w - run(dark_c[::-1])
    if y1 - y0 < h * 0.2 or x1 - x0 < w * 0.2:                 # gần như đen hẳn: không tin
        return 0, 0, w, h
    if y0 < h * BAR_MIN:
        y0 = 0
    if h - y1 < h * BAR_MIN:
        y1 = h
    if x0 < w * BAR_MIN:
        x0 = 0
    if w - x1 < w * BAR_MIN:
        x1 = w
    # nới vào trong 1 điểm đo mỗi bên có dải (mép dải đen hay nhoè vì nén)
    return (x0 + (x0 > 0), y0 + (y0 > 0), x1 - (x1 < w), y1 - (y1 < h))


def motion_profile(pairs: list[np.ndarray], box: tuple[int, int, int, int]) -> tuple[np.ndarray, float]:
    """(năng lượng chuyển động theo cột trong vùng nội dung, chuẩn hoá tổng 1; mức chuyển động trung bình). Mỗi cặp khung
    chuẩn hoá riêng: một cảnh lắc máy không lấn hết các cảnh khác."""
    x0, y0, x1, y1 = box
    prof = np.zeros(x1 - x0, np.float64)
    levels = []
    for p in pairs:
        d = np.abs(p[1, y0:y1, x0:x1].astype(np.int16) - p[0, y0:y1, x0:x1].astype(np.int16)).astype(np.float32)
        d[d < 8] = 0                                           # nhiễu nén
        levels.append(float(d.mean()))
        col = d.sum(axis=0)
        if col.sum() > 0:
            prof += col / col.sum()
    s = prof.sum()
    return (prof / s if s > 0 else prof), (float(np.median(levels)) if levels else 0.0)


def best_window(prof: np.ndarray, width: int) -> tuple[int, float]:
    """(cột bắt đầu, phần năng lượng lọt trong) của cửa sổ rộng `width` chứa nhiều chuyển động nhất."""
    n = len(prof)
    width = max(1, min(width, n))
    if prof.sum() <= 0:
        return (n - width) // 2, 0.0
    c = np.concatenate([[0.0], np.cumsum(prof)])
    sums = c[width:] - c[:-width]
    k = int(np.argmax(sums))
    return k, float(sums[k] / c[-1])


def decide(src_w: int, src_h: int, frames: list[np.ndarray]) -> dict:
    """Quyết định khung từ các cặp khung đo (toạ độ khung đo, rộng WIDTH): {} = như cũ (cắt giữa), hoặc {"box": [x, y, w,
    h] (pixel nguồn), "pad": bool, "why": str}."""
    if not frames:
        return {}
    fh, fw = frames[0].shape[1:]
    k = src_w / fw
    allf = np.concatenate(frames)
    x0, y0, x1, y1 = bars(allf)
    cw, ch = x1 - x0, y1 - y0
    has_bars = (x0, y0, x1, y1) != (0, 0, fw, fh)
    aspect = (cw * k) / max(1.0, ch * (src_h / fh))
    why = []
    if has_bars:
        why.append(f"bỏ dải đen ({round(100 * (1 - cw * ch / (fw * fh)))}% khung)")
    box = [x0, y0, cw, ch]
    pad = False
    if aspect > TALL_MAX:
        prof, level = motion_profile(frames, (x0, y0, x1, y1))
        win = int(round(ch * (src_h / fh) * OUT_ASPECT / k))
        start, frac = best_window(prof, win)
        if level < MOTION_MIN or frac < FOCUS:
            if aspect >= PAD_MIN:
                pad = True
                why.append("nguồn ngang, hoạt động rải khắp khung: đặt nguyên khung lên nền mờ"
                           if level >= MOTION_MIN else "nguồn ngang, gần như tĩnh: đặt nguyên khung lên nền mờ")
            else:                                              # hơi ngang (vuông, 3:4): cắt giữa như cũ mất ít
                start = (cw - win) // 2
        else:
            why.append(f"nguồn ngang: cắt khung 9:16 theo vùng có tay / chảo ({round(100 * frac)}% chuyển động lọt khung)")
        if not pad:
            box = [x0 + max(0, min(cw - win, start)), y0, min(win, cw), ch]
    if not has_bars and not pad and box == [0, 0, fw, fh]:
        return {}
    bx = [int(round(box[0] * k)), int(round(box[1] * src_h / fh)), int(round(box[2] * k)), int(round(box[3] * src_h / fh))]
    bx[2] -= bx[2] % 2                                         # chẵn cho yuv420
    bx[3] -= bx[3] % 2
    bx[2], bx[3] = min(bx[2], src_w - bx[0]), min(bx[3], src_h - bx[1])
    if bx == [0, 0, src_w - src_w % 2, src_h - src_h % 2] and not pad:
        return {}
    return {"box": bx, "pad": pad, "src": [src_w, src_h], "why": "; ".join(why)}


def analyze(path: str, dur: float, src_w: int, src_h: int, samples: int = SAMPLES) -> dict:
    """Đo + quyết định (đồng bộ, gọi qua asyncio.to_thread)."""
    if dur <= 0 or not src_w or not src_h:
        return {}
    w = WIDTH
    h = max(2, int(round(WIDTH * src_h / src_w / 2)) * 2)
    pairs = [p for k in range(samples)
             if (p := _pair(path, dur * (0.04 + 0.92 * (k + 0.5) / samples), w, h)) is not None]
    return decide(src_w, src_h, pairs)


async def ensure(path: str, dur: float, src_w: int, src_h: int, note: Note | None = None) -> dict:
    """Chọn khung cho job này (một lần; FRAMING_AUTO=0 thì thôi). Lỗi đo không được làm hỏng job: như cũ."""
    import config
    if (getattr(config, "FRAMING_AUTO", "1") or "1") == "0":
        return {}
    have = FRAMING.get()
    if have:
        return have
    try:
        fr = await asyncio.to_thread(analyze, path, dur, src_w, src_h)
    except Exception as e:
        logging.warning("Không đo được khung hình để chọn khung dọc: %s", str(e)[:150])
        fr = {}
    if fr:
        FRAMING.set(fr)
        logging.info("Khung dọc: %s", fr)
        if note:
            note(f"🖼️ Khung dọc: {fr['why']}. ")
    return fr


def current(src_w: int = 0, src_h: int = 0) -> dict:
    """Khung của job đang chạy, chỉ khi đúng nguồn đã đo (cùng cỡ): đầu vào khác cỡ (ảnh, clip lạ) thì như cũ."""
    fr = FRAMING.get() or {}
    if not fr.get("box"):
        return {}
    if src_w and src_h and fr.get("src") and [src_w, src_h] != list(fr["src"]):
        return {}
    return fr


def pre_crop(fr: dict) -> str:
    """Chuỗi cắt vùng nội dung / cửa sổ 9:16 (đặt TRƯỚC cắt 4% viền)."""
    if not fr:
        return ""
    x, y, w, h = fr["box"]
    return f"crop={w}:{h}:{x}:{y},"


def pad_graph(W: int, H: int) -> str:
    """Nguyên khung (vừa bề ngang) đặt giữa nền mờ của chính nó. Nền làm ở 1/8 cỡ rồi phóng lại: mờ đủ mà gần như không tốn
    (Botkeep 1,5 vCPU). Nhãn riêng mỗi lần gọi (một đồ thị có nhiều khúc dùng chung chuỗi này)."""
    n = next(_LABEL)
    w8, h8 = max(2, W // 8 // 2 * 2), max(2, H // 8 // 2 * 2)
    return (f"split=2[fra{n}][frb{n}];"
            f"[fra{n}]scale={w8}:{h8}:force_original_aspect_ratio=increase:flags=area,crop={w8}:{h8},boxblur=6:2,"
            f"eq=brightness=-0.06:saturation=0.85,scale={W}:{H}:flags=bilinear[frg{n}];"
            f"[frb{n}]scale={W}:-2:flags=lanczos[frf{n}];"
            f"[frg{n}][frf{n}]overlay=0:(H-h)/2,")


def map_bands(bands: list, fr: dict | None = None, W: int = 1080, H: int = 1920) -> list:
    """Dải chữ (tỉ lệ 0..1 trên khung gốc) -> tỉ lệ «tương đương khung gốc» sau khi đổi khung, để band_to_output /
    zoomed_bands (vốn tính cho cắt giữa 4% viền) đặt đúng chỗ trên khung ra. Không đổi khung thì giữ nguyên."""
    fr = current() if fr is None else fr
    if not fr or not bands:
        return list(bands or [])
    sw, sh = fr["src"]
    x, y, w, h = fr["box"]
    out = []
    for top, bottom in bands:
        a, b = (top * sh - y) / h, (bottom * sh - y) / h          # theo vùng nội dung
        if fr.get("pad"):
            fit = W * h / w / H                                   # chiều cao khung vừa bề ngang, theo tỉ lệ khung ra
            a, b = [(1 - fit) / 2 + fit * ((v - 0.02) / 0.96) for v in (a, b)]
            a, b = 0.02 + 0.96 * a, 0.02 + 0.96 * b              # đổi lại về «tỉ lệ gốc» cho band_to_output
        a, b = max(0.0, min(1.0, a)), max(0.0, min(1.0, b))
        if b - a > 0.005:
            out.append((round(a, 4), round(b, 4)))
    return out


def spec_value() -> dict:
    """Khung gửi kèm việc Kaggle (rỗng: Kaggle cắt giữa như cũ)."""
    fr = FRAMING.get() or {}
    return {k: fr[k] for k in ("box", "pad", "src") if k in fr} if fr.get("box") else {}


def apply_spec(spec: dict[str, Any]) -> None:
    """(Kaggle) dùng đúng khung Botkeep đã chọn."""
    fr = spec.get("framing")
    if isinstance(fr, dict) and fr.get("box") and len(fr["box"]) == 4:
        FRAMING.set({"box": [int(v) for v in fr["box"]], "pad": bool(fr.get("pad")),
                     "src": [int(v) for v in fr.get("src") or [0, 0]]})
