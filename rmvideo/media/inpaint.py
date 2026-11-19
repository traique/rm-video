"""(Kaggle, GPU) Xoá chữ in cứng trên video nguồn bằng LaMa (big-lama.pt, TorchScript) thay cho che mờ.

Dải chữ (bands, tỉ lệ 0..1 chiều cao, từ textzone / OCR) -> mỗi khung: cắt một dải ngang quanh chữ (kèm vài chục pixel
ngữ cảnh trên dưới), mặt nạ = phần có chữ (bỏ 3% hai mép), LaMa vẽ lại nền, dán vào khung -> video sạch cùng
kích thước, cùng fps, giữ nguyên tiếng. Chỉ chạy trên GPU (T4: ~0,03 giây/khung với dải 1080×300, video 90 giây ≈ 1,5-2
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
Log = Callable[[str], None]


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
    """LaMa trên một lô dải: crops (B, h, w, 3) uint8, mask (h, w) 0/1 -> (B, h, w, 3) uint8 (chỉ dùng chỗ mặt nạ).
    Dải rộng hơn half_over: thu nửa cỡ (area), vẽ, phóng lại (bilinear) — nhanh ~4 lần."""
    import torch
    F = torch.nn.functional
    dev = next(model.parameters()).device if hasattr(model, "parameters") else "cpu"
    B, h, w, _ = crops.shape
    img = torch.from_numpy(np.ascontiguousarray(crops)).to(dev).permute(0, 3, 1, 2).float() / 255.0
    msk = torch.from_numpy(mask.astype(np.float32)).to(dev)[None, None].expand(B, 1, h, w)
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


def _probe(path: str) -> tuple[int, int, float, bool]:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h, fr = p.stdout.strip().split("\n")[0].split(",")[:3]
    num, _, den = fr.partition("/")
    fps = float(num) / float(den or 1)
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return int(w), int(h), fps if 1 <= fps <= 120 else 30.0, "audio" in a.stdout


def _has_nvenc() -> bool:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "h264_nvenc" in p.stdout


def clean_video(inp: str, bands: Sequence[tuple[float, float]], out: str, log: Log = logging.info,
                device: str = "cuda", model: Any = None, batch: int = BATCH, nvenc: bool | None = None) -> dict:
    """Vẽ lại nền ở các dải chữ trên mọi khung -> out (cùng cỡ, cùng fps, tiếng chép nguyên). Trả {"frames", "secs",
    "regions"}. model: đã nạp (test); None thì tự nạp."""
    W, H, fps, has_audio = _probe(inp)
    regs = regions(bands, W, H)
    if not regs:
        raise ValueError("không có dải chữ để xoá")
    t0 = time.time()
    model = model if model is not None else load(device)
    masks = []
    for r in regs:
        m = np.zeros((r["cy1"] - r["cy0"], W), np.float32)
        m[r["my0"] - r["cy0"]:r["my1"] - r["cy0"], r["x0"]:r["x1"]] = 1.0
        masks.append(m)
    log(f"LaMa: {len(regs)} dải, khung {W}x{H} {fps:.2f}fps, nạp model {time.time() - t0:.0f}s")
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
    n, size = 0, W * H * 3
    t1 = time.time()
    try:
        assert dec.stdout is not None and enc.stdin is not None
        while True:
            chunk = dec.stdout.read(size * batch)
            if not chunk:
                break
            k = len(chunk) // size
            if k == 0:
                break
            frames = np.frombuffer(chunk[:k * size], np.uint8).reshape(k, H, W, 3).copy()
            for r, m in zip(regs, masks):
                crops = frames[:, r["cy0"]:r["cy1"], :, :]
                fixed = apply(model, crops, m)
                a, b = r["my0"] - r["cy0"], r["my1"] - r["cy0"]
                frames[:, r["my0"]:r["my1"], r["x0"]:r["x1"], :] = fixed[:, a:b, r["x0"]:r["x1"], :]
            enc.stdin.write(frames.tobytes())
            n += k
            if n % (batch * 50) == 0:
                log(f"LaMa: {n} khung, {n / max(1e-6, time.time() - t1):.0f} khung/giây")
    finally:
        if enc.stdin is not None:
            enc.stdin.close()
        dec.stdout.close() if dec.stdout is not None else None
        dec.wait()
        enc.wait()
    if enc.returncode or not os.path.exists(out) or os.path.getsize(out) < 10_000 or n == 0:
        raise RuntimeError(f"ffmpeg mã hoá video sạch lỗi (mã {enc.returncode}, {n} khung)")
    secs = round(time.time() - t0, 1)
    log(f"LaMa xong: {n} khung trong {secs}s")
    return {"frames": n, "secs": secs, "regions": regs}
