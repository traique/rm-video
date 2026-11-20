"""(Kaggle, GPU) Xoá chữ in cứng trên video nguồn bằng LaMa (big-lama.pt, TorchScript) thay cho che mờ.

Dải chữ (bands, tỉ lệ 0..1 chiều cao, từ textzone / OCR) -> mỗi khung: cắt một dải ngang quanh chữ (kèm vài chục pixel
ngữ cảnh trên dưới), mặt nạ = nét chữ thật của khung đó nới ra phủ viền/bóng (MASK="text"; "band" = cả dải bỏ 3% hai
mép như cũ), LaMa vẽ lại nền, dán vào khung -> video sạch cùng kích thước, cùng fps, giữ nguyên tiếng. Chỗ không có chữ
trong dải (tay, đồ ăn, nồi đi ngang) giữ nguyên pixel gốc; cụm chữ nằm trong dải mà tràn ra ngoài (phụ đề 2 dòng) được
xoá trọn trong phần ngữ cảnh; mặt nạ giữ thêm vài khung trước/sau chữ, nền đứng yên thì trộn vùng vẽ với khung trước cho
khỏi nhấp nháy. Không dùng STTN (mạng xoá theo thời gian của video-subtitle-remover): kho researchmm/STTN không ghi giấy
phép cho mã/trọng số (file LICENSE là MIT của theme web), trọng số chỉ trên Google Drive, cỡ cố định 432x240. Chỉ chạy trên GPU (T4: ~0,03 giây/khung với dải 1080×300, video 90 giây ≈ 1,5-2
phút); Botkeep tải video sạch về làm nguồn dựng, không che mờ nữa và vẫn đặt phụ đề mới đúng chỗ.
Model: https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt (bản TorchScript của LaMa, dùng bởi
IOPaint / simple-lama-inpainting, ~196MB). Tìm trước trong /kaggle/input (dataset gắn vào), không có thì tải về cache.
Tiền xử lý theo advimman/lama: ảnh RGB float 0..1, đệm cho chia hết 8 (symmetric), mặt nạ 1 = chỗ cần vẽ lại.
"""
from __future__ import annotations

import glob
import logging
import os
import subprocess
import time
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

MODEL_URL = "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt"
MODEL_NAME = "big-lama.pt"
MODEL_MIN_BYTES = 150 << 20
MARGIN = 0.012          # nới mặt nạ mỗi bên chừng này chiều cao (viền/bóng chữ)
SIDE = (0.03, 0.97)     # mặt nạ chỉ phủ phần giữa (đo thật: dòng phụ đề dài chạm 95% bề ngang); logo góc nằm ngoài dải
CONTEXT = 48            # pixel ngữ cảnh trên/dưới dải cho LaMa nhìn thấy nền
BATCH = 8               # khung xử lý một lượt trên GPU
HALF_OVER = 720         # dải rộng hơn chừng này pixel thì LaMa chạy ở nửa cỡ rồi phóng lại (đo T4 19/11: 1080×320 full cỡ
                        # 0,28 giây/khung = 12 phút cho video 90 giây; nửa cỡ nhanh ~4 lần, nền dưới phụ đề mới vẫn mịn)
MASK = "text"           # "text": mặt nạ = nét chữ thật từng khung; "band": cả dải như cũ (TEXT_INPAINT=band). Đo 20/11
                        # (video 720x1280 có đáp án, tay + mực trắng đi qua dải): lệch so với gốc ngoài chữ MAE 12,8 -> 5,6,
                        # trên chữ 31,4 -> 14,4; chữ sót 2,9% -> 0
PAD = 0.008             # nới nét chữ mỗi bên chừng này chiều cao khung: viền đen, bóng, nhoè nén quanh nét sáng (đo 20/11
                        # trên video 720x1280 chữ 42px viền 4px: nới 10px phủ 99,98% pixel chữ thật, 6px chỉ 99,3%)
STROKES = 3.0           # cụm chữ: trung bình mỗi hàng cắt qua ≥ chừng này nét (mép mực trắng trên thớt tối chỉ 1-2)
HOLD = 3                # giữ mặt nạ thêm chừng này khung trước/sau khi thấy chữ (chữ hiện/tắt dần, nhoè nén)
BLEND = 0.6             # nền quanh vùng vẽ đứng yên: trộn chừng này phần vùng vẽ của khung trước (LaMa đỡ nhấp nháy;
                        # đo 20/11: đổi giữa hai khung lệch gốc ở vùng chữ 10,6 -> 9,6, gốc nén lại 3,1)
STILL = 4.0             # chênh trung bình giữa hai khung (mức xám) dưới ngưỡng này thì coi nền là đứng yên
Log = Callable[[str], None]
Box = tuple[int, int, int, int]         # (y0, y1, x0, x1) trong dải cắt


def model_path(root: str = "/kaggle/input", cache: str | None = None) -> str:
    """Đường dẫn big-lama.pt: trong nguồn gắn vào phiên, hoặc đã tải về cache, hoặc tải mới."""
    if os.path.isdir(root):
        for p in glob.glob(os.path.join(root, "**", MODEL_NAME), recursive=True):
            if os.path.getsize(p) >= MODEL_MIN_BYTES:
                return p
    cache = cache or os.path.join(os.path.expanduser("~"), ".cache", "rm_lama")
    os.makedirs(cache, exist_ok=True)
    p = os.path.join(cache, MODEL_NAME)
    if os.path.exists(p) and os.path.getsize(p) >= MODEL_MIN_BYTES:
        return p
    tmp = p + ".part"
    with urllib.request.urlopen(MODEL_URL, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if os.path.getsize(tmp) < MODEL_MIN_BYTES:
        raise RuntimeError(f"big-lama.pt tải về chỉ {os.path.getsize(tmp) >> 20}MB")
    os.replace(tmp, p)
    return p


def load(device: str = "cuda", path: str | None = None) -> Any:
    import torch
    dev = device if (device == "cpu" or torch.cuda.is_available()) else "cpu"
    m = torch.jit.load(path or model_path(), map_location=dev)
    m.eval()
    return m.to(dev)


def regions(bands: Sequence[tuple[float, float]], W: int, H: int) -> list[dict]:
    """Dải chữ -> vùng cắt/mặt nạ pixel: {"cy0","cy1"} dải cắt (có ngữ cảnh, cao chia hết 8), {"my0","my1"} hàng có
    chữ, {"x0","x1"} cột mặt nạ. Dải chồng nhau gộp làm một."""
    rows = []
    for top, bottom in bands:
        y0 = max(0, int((min(top, bottom) - MARGIN) * H))
        y1 = min(H, int((max(top, bottom) + MARGIN) * H) + 1)
        if y1 - y0 >= 4:
            rows.append([y0, y1])
    rows.sort()
    merged: list[list[int]] = []
    for y0, y1 in rows:
        if merged and y0 <= merged[-1][1] + CONTEXT:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    out = []
    for my0, my1 in merged:
        cy0 = max(0, my0 - CONTEXT)
        cy1 = min(H, my1 + CONTEXT)
        h = -(-(cy1 - cy0) // 8) * 8                      # cao chia hết 8 (LaMa), nới xuống dưới rồi lên trên
        cy1 = min(H, cy0 + h)
        cy0 = max(0, cy1 - h)
        out.append({"cy0": cy0, "cy1": cy1, "my0": my0, "my1": my1, "x0": int(SIDE[0] * W), "x1": int(SIDE[1] * W)})
    return out


def apply(model: Any, crops: np.ndarray, mask: np.ndarray, half_over: int = HALF_OVER) -> np.ndarray:
    """LaMa trên một lô dải: crops (B, h, w, 3) uint8, mask (h, w) hay (B, h, w) 0/1 -> (B, h, w, 3) uint8 (chỉ dùng chỗ
    mặt nạ). Dải rộng hơn half_over: thu nửa cỡ (area), vẽ, phóng lại (bilinear) — nhanh ~4 lần."""
    import torch
    F = torch.nn.functional
    dev = next(model.parameters()).device if hasattr(model, "parameters") else "cpu"
    B, h, w, _ = crops.shape
    img = torch.from_numpy(np.ascontiguousarray(crops)).to(dev).permute(0, 3, 1, 2).float() / 255.0
    msk = torch.from_numpy(mask.astype(np.float32)).to(dev)
    msk = msk[None, None].expand(B, 1, h, w) if msk.ndim == 2 else msk[:, None]
    if w > half_over:
        h2, w2 = -(-h // 16) * 8, -(-w // 16) * 8          # nửa cỡ, chia hết 8
        img = F.interpolate(img, size=(h2, w2), mode="area")
        msk = (F.interpolate(msk, size=(h2, w2), mode="area") > 0).float()
    ph, pw = -(-img.shape[2] // 8) * 8, -(-img.shape[3] // 8) * 8
    if (ph, pw) != tuple(img.shape[2:]):
        img = F.pad(img, (0, pw - img.shape[3], 0, ph - img.shape[2]), mode="reflect")
        msk = F.pad(msk, (0, pw - msk.shape[3], 0, ph - msk.shape[2]), mode="reflect")
    with torch.inference_mode():
        out = model(img, msk)
    if out.shape[2] != h or out.shape[3] != w:
        out = F.interpolate(out[:, :, :ph, :pw], size=(h, w), mode="bilinear", align_corners=False)
    out = out[:, :, :h, :w].clamp(0, 1).mul(255).round().byte().permute(0, 2, 3, 1).cpu().numpy()
    return out


def text_px(crop: np.ndarray) -> np.ndarray:
    """Pixel chữ (sáng > 165 sát pixel tối < 90 trong ô 3x3, như textzone): (B, h, w, 3) uint8 -> (B, h, w) bool.
    So trên tổng 3 kênh (số nguyên, = 3 × mức xám) cho nhanh."""
    g = crop.sum(axis=3, dtype=np.int16)
    P = np.pad(g, ((0, 0), (1, 1), (1, 1)), mode="edge")
    h, w = g.shape[1:]
    mn = P[:, :h, :w].copy()
    for dy in range(3):
        for dx in range(3):
            np.minimum(mn, P[:, dy:dy + h, dx:dx + w], out=mn)
    return (g > 3 * 165) & (mn < 3 * 90)


def has_text(crop: np.ndarray, mask: np.ndarray, min_frac: float = 0.0015) -> np.ndarray:
    """Khung nào trong lô thật sự có chữ ở vùng mặt nạ (pixel sáng sát pixel tối, như textzone): (B,) bool. Khung không
    có chữ thì giữ nguyên, khỏi vẽ lại (nhanh hơn, không làm mờ nền khi phụ đề tạm tắt)."""
    text = text_px(crop) & (mask[None] > 0)
    return text.reshape(crop.shape[0], -1).mean(axis=1) > min_frac


def _runs(v: np.ndarray, gap: int) -> list[tuple[int, int]]:
    """Đoạn True liên tiếp của mảng 1 chiều -> [(đầu, cuối + 1)], gộp hai đoạn cách nhau không quá gap ô."""
    idx = np.flatnonzero(v)
    if not len(idx):
        return []
    cut = np.flatnonzero(np.diff(idx) > gap + 1)
    return list(zip(np.r_[idx[0], idx[cut + 1]].tolist(), (np.r_[idx[cut], idx[-1]] + 1).tolist()))


def text_boxes(text: np.ndarray, H: int) -> list[Box]:
    """Pixel chữ một khung (h, w) bool -> hộp từng dòng/cụm chữ (y0, y1, x0, x1), chưa nới. Cắt XY: hàng có chữ -> dòng,
    cột có chữ trong dòng (gộp khoảng trống giữa chữ/từ) -> cụm, hàng trong cụm -> dòng sát. Bỏ cụm không giống chữ:
    thấp hơn 1% chiều cao khung, hay mỗi hàng cắt qua trung bình dưới STROKES nét (mép vật sáng trên nền tối)."""
    min_h, gy, gx = max(3, round(0.01 * H)), max(2, round(0.006 * H)), max(4, round(0.03 * H))
    out = []
    for y0, y1 in _runs(text.any(axis=1), gy):
        for x0, x1 in _runs(text[y0:y1].any(axis=0), gx):
            for a, b in _runs(text[y0:y1, x0:x1].any(axis=1), gy):
                sub = text[y0 + a:y0 + b, x0:x1]
                rows = int(sub.any(axis=1).sum())
                cross = np.count_nonzero(sub[:, 1:] & ~sub[:, :-1]) + np.count_nonzero(sub[:, 0])
                if b - a >= min_h and cross >= STROKES * rows:
                    out.append((y0 + a, y0 + b, x0, x1))
    return out


def _overlap(p: Box, q: Box) -> float:
    """Phần giao của hai hộp / diện tích hộp nhỏ hơn."""
    ih, iw = min(p[1], q[1]) - max(p[0], q[0]), min(p[3], q[3]) - max(p[2], q[2])
    if ih <= 0 or iw <= 0:
        return 0.0
    return ih * iw / max(1, min((p[1] - p[0]) * (p[3] - p[2]), (q[1] - q[0]) * (q[3] - q[2])))


def held(dets: dict[int, list[Box]], i: int, hold: int = HOLD) -> list[tuple[int, Box]]:
    """Hộp chữ cho khung i, kèm khung lấy hộp: hộp "đứng yên" của các khung i-hold..i+hold (khung kề có hộp chồng
    ≥ 50%: phụ đề in cứng đứng yên, mép tay/đồ ăn đang chạy thì không). Giữ trước/sau vài khung: chữ hiện/tắt dần hay
    nhoè nén vẫn được xoá, khung dò sót giữa chừng không bật tắt."""
    out: list[tuple[int, Box]] = []
    for s in range(i - hold, i + hold + 1):
        near = dets.get(s - 1, []) + dets.get(s + 1, [])
        out += [(s, b) for b in dets.get(s, []) if any(_overlap(b, c) >= 0.5 for c in near)]
    return out


def _grow(m: np.ndarray, r: int) -> np.ndarray:
    """Nới vùng True của mặt nạ (h, w) r pixel mỗi phía (ô vuông; cộng dồn nên không phụ thuộc r, chỉ tính trong khung
    bao quanh vùng True)."""
    ys, xs = np.flatnonzero(m.any(axis=1)), np.flatnonzero(m.any(axis=0))
    out = np.zeros_like(m)
    if not len(ys):
        return out
    y0, y1 = max(0, ys[0] - r), min(m.shape[0], ys[-1] + r + 1)
    x0, x1 = max(0, xs[0] - r), min(m.shape[1], xs[-1] + r + 1)
    sub = m[y0:y1, x0:x1]
    for ax in (0, 1):
        n = sub.shape[ax]
        c = np.insert(np.cumsum(sub, axis=ax, dtype=np.int32), 0, 0, axis=ax)
        idx = np.arange(n)
        sub = (c.take(np.minimum(idx + r + 1, n), axis=ax) - c.take(np.maximum(idx - r, 0), axis=ax)) > 0
    out[y0:y1, x0:x1] = sub
    return out


def _steady(out: np.ndarray, crop: np.ndarray, m: np.ndarray, prev: tuple | None, H: int) -> np.ndarray:
    """Vùng vẽ khung này (out, mặt nạ m) trộn với vùng vẽ khung trước ở những cột mà nền quanh (pixel không vẽ, kể cả
    ngữ cảnh trên dưới) gần như không đổi: LaMa vẽ mỗi khung hơi khác nhau, nền đứng yên thì trộn cho khỏi nhấp nháy.
    prev = (crop, out, mặt nạ) khung liền trước có vẽ, hoặc None."""
    if prev is None or BLEND <= 0:
        return out
    pc, po, pm = prev
    both = m & pm
    if not both.any():
        return out
    ctx = ~(m | pm)[::2, ::2]                                      # đo trên lưới thưa 2 pixel cho nhanh
    d = np.abs(crop[::2, ::2, 1].astype(np.int16) - pc[::2, ::2, 1])             # kênh G ~ độ sáng
    k = np.ones(2 * max(1, round(0.01 * H)) + 1)                   # gom theo cột, trải ngang ~4% chiều cao khung
    num = np.convolve(np.where(ctx, d, 0).sum(axis=0), k, "same")
    den = np.convolve(ctx.sum(axis=0).astype(np.float64), k, "same")
    a = np.where(den > 0, BLEND * np.clip(2 - num / np.maximum(den, 1) / STILL, 0, 1), 0).astype(np.float32)
    if not a.any():                                                # nền chạy cả dải: giữ kết quả LaMa khung này
        return out
    a = np.repeat(a, 2)[:out.shape[1]][None, :, None]
    rows = np.flatnonzero(both.any(axis=1))
    y0, y1 = rows[0], rows[-1] + 1
    cur = out[y0:y1].astype(np.float32)
    mix = (cur + a * (po[y0:y1] - cur)).round().astype(np.uint8)
    res = out.copy()
    res[y0:y1] = np.where(both[y0:y1, :, None], mix, out[y0:y1])
    return res


def _probe(path: str) -> tuple[int, int, float, bool]:
    """(rộng, cao, fps, có tiếng) theo khung HIỂN THỊ: video có cờ xoay 90°/270° (quay dọc bằng điện thoại) thì FFmpeg tự
    xoay khi giải mã, khung ra là cao x rộng của khung lưu trong file -> đổi chỗ, không thì cắt khung sai chiều (ảnh méo)."""
    import json
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate:stream_tags=rotate:stream_side_data=rotation", "-of", "json",
                        path], capture_output=True, text=True)
    st = (json.loads(p.stdout or "{}").get("streams") or [{}])[0]
    w, h = int(st["width"]), int(st["height"])
    rot = st.get("tags", {}).get("rotate") or next((d.get("rotation") for d in st.get("side_data_list") or []
                                                    if "rotation" in d), 0)
    if int(float(rot or 0)) % 180:
        w, h = h, w
    num, _, den = str(st.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return w, h, fps if 1 <= fps <= 120 else 30.0, "audio" in a.stdout


def _has_nvenc() -> bool:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "h264_nvenc" in p.stdout


class _Band:
    """Một dải khi chạy luồng khung: hộp + pixel chữ đã dò theo số khung, vùng vẽ khung trước (để trộn)."""

    def __init__(self, r: dict, W: int, H: int, mode: str) -> None:
        self.r, self.H, self.mode = r, H, mode
        h, self.a, self.b = r["cy1"] - r["cy0"], r["my0"] - r["cy0"], r["my1"] - r["cy0"]
        self.rect = np.zeros((h, W), bool)                               # cả dải (mặt nạ cũ)
        self.rect[self.a:self.b, r["x0"]:r["x1"]] = True
        self.zone = np.zeros((h, W), bool)          # mặt nạ theo chữ được lấn sang ngữ cảnh, chừa CONTEXT/3 cho LaMa nhìn nền
        self.zone[min(self.a, CONTEXT // 3):max(self.b, h - CONTEXT // 3), r["x0"]:r["x1"]] = True
        self.dets: dict[int, list[Box]] = {}
        self.px: dict[int, np.ndarray] = {}
        self.prev: tuple | None = None

    def detect(self, frames: np.ndarray, base: int) -> None:
        crops = frames[:, self.r["cy0"]:self.r["cy1"]]
        if self.mode == "band":
            for f in np.flatnonzero(has_text(crops, self.rect)).tolist():
                self.dets[base + f] = [(self.a, self.b, self.r["x0"], self.r["x1"])]
            return
        tp = text_px(crops) & self.zone[None]
        for j in range(len(tp)):
            # chỉ cụm chữ nằm ít nhất 1/3 trong dải: phụ đề 2 dòng lấn ra ngoài dải textzone (dải theo hàng có chữ ở
            # ≥ 50% khung) vẫn xoá trọn, dòng chữ khác hẳn ở phần ngữ cảnh thì không
            bx = [q for q in text_boxes(tp[j], self.H) if 3 * (min(q[1], self.b) - max(q[0], self.a)) >= q[1] - q[0]]
            if bx:
                self.dets[base + j], self.px[base + j] = bx, tp[j]

    def mask(self, i: int) -> np.ndarray | None:
        """Mặt nạ khung i: cả dải (band) hay pixel chữ trong các hộp giữ quanh khung i, nới PAD (text); None = để
        nguyên. Theo nét chứ không cả hộp: đo 20/11 vẽ thừa ngoài chữ 14% dải thay vì 27% (hộp), 44% (cả dải)."""
        if self.mode == "band":
            return self.rect if i in self.dets else None
        acc = np.zeros_like(self.rect)
        for s, (y0, y1, x0, x1) in held(self.dets, i):
            acc[y0:y1, x0:x1] |= self.px[s][y0:y1, x0:x1]
        return _grow(acc, max(2, round(PAD * self.H))) & self.zone if acc.any() else None

    def paint(self, model: Any, frames: np.ndarray, base: int, batch: int) -> tuple[int, float]:
        """Vẽ lại dải trên lô khung base.. (sửa tại chỗ) -> (số khung vẽ, tổng tỉ lệ diện tích vẽ / dải)."""
        crops = frames[:, self.r["cy0"]:self.r["cy1"]]
        ms = [self.mask(base + j) for j in range(len(frames))]
        todo = {j: m for j, m in enumerate(ms) if m is not None}
        sel = list(todo)
        fixed: dict[int, np.ndarray] = {}
        for c in range(0, len(sel), batch):
            idx = sel[c:c + batch]
            mk = self.rect if self.mode == "band" else np.stack([todo[j] for j in idx])
            fixed.update(zip(idx, apply(model, crops[idx], mk.astype(np.float32))))
        area = 0.0
        for j, m in enumerate(ms):
            if m is None:
                self.prev = None
                continue
            src = crops[j].copy()
            out = np.where(m[..., None], fixed[j], src)
            if self.mode == "text":
                out = _steady(out, src, m, self.prev, self.H)
            self.prev = (src, out, m)
            crops[j] = out
            area += m.sum() / self.rect.sum()
        keep = base + len(frames) - HOLD - 1
        for d in (self.dets, self.px):
            for i in [i for i in d if i < keep]:
                del d[i]
        return len(sel), area


def clean_video(inp: str, bands: Sequence[tuple[float, float]], out: str, log: Log = logging.info,
                device: str = "cuda", model: Any = None, batch: int = BATCH, nvenc: bool | None = None,
                mask: str | None = None) -> dict:
    """Vẽ lại nền ở các dải chữ trên mọi khung -> out (cùng cỡ, cùng fps, tiếng chép nguyên). Trả {"frames", "painted",
    "secs", "regions", "mask"}. model: đã nạp (test); None thì tự nạp. mask: "text" (chỉ chỗ có chữ, mặc định MASK)
    hay "band" (cả dải như cũ)."""
    mode = mask or MASK
    W, H, fps, has_audio = _probe(inp)
    regs = regions(bands, W, H)
    if not regs:
        raise ValueError("không có dải chữ để xoá")
    t0 = time.time()
    model = model if model is not None else load(device)
    log(f"LaMa: {len(regs)} dải, mặt nạ {mode}, khung {W}x{H} {fps:.2f}fps, nạp model {time.time() - t0:.0f}s")
    use_nvenc = _has_nvenc() if nvenc is None else nvenc
    vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"] if use_nvenc \
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", inp, "-fps_mode", "cfr", "-r", f"{fps:.5f}", "-an",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, bufsize=10 << 20)
    enc_in = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps:.5f}",
              "-i", "-"]
    enc_in += ["-i", inp, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"] if has_audio else ["-map", "0:v:0"]
    enc = subprocess.Popen(enc_in + vcodec + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", out],
                           stdin=subprocess.PIPE)
    parts = [_Band(r, W, H, mode) for r in regs]
    look = HOLD + 1 if mode == "text" else 0          # chờ đọc trước chừng này khung (giữ mặt nạ trước khi chữ hiện)
    buf: list[np.ndarray] = []
    n, got, done, area, size, eof, next_log = 0, 0, 0, 0.0, W * H * 3, False, batch * 50
    t1 = time.time()
    try:
        assert dec.stdout is not None and enc.stdin is not None
        while not eof or buf:
            chunk = b"" if eof else dec.stdout.read(size * batch)
            k = len(chunk) // size
            if k:
                frames = np.frombuffer(chunk[:k * size], np.uint8).reshape(k, H, W, 3)
                for p in parts:
                    p.detect(frames, got)
                buf.extend(frames)
                got += k
            eof = eof or not k
            ready = len(buf) if eof else got - look - n
            if ready <= 0:
                continue
            frames = np.stack(buf[:ready])
            del buf[:ready]
            for p in parts:
                d, a = p.paint(model, frames, n, batch)
                done, area = done + d, area + a
            enc.stdin.write(frames.tobytes())
            n += ready
            if n >= next_log:
                next_log += batch * 50
                log(f"LaMa: {n} khung, {n / max(1e-6, time.time() - t1):.0f} khung/giây")
    finally:
        if enc.stdin is not None:
            enc.stdin.close()
        dec.stdout.close() if dec.stdout is not None else None
        dec.wait()
        enc.wait()
    if enc.returncode or not os.path.exists(out) or os.path.getsize(out) < 1_000 or n == 0:
        raise RuntimeError(f"ffmpeg mã hoá video sạch lỗi (mã {enc.returncode}, {n} khung)")
    secs = round(time.time() - t0, 1)
    log(f"LaMa xong: {n} khung ({done} khung có chữ được vẽ lại, mặt nạ trung bình {100 * area / max(1, done):.0f}% dải) "
        f"trong {secs}s")
    return {"frames": n, "painted": done, "secs": secs, "regions": regs, "mask": mode}
