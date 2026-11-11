"""Dò dải phụ đề in cứng bằng model dò chữ PP-OCRv6 tiny (1.8MB, Apache-2.0) chạy thẳng bằng onnxruntime.

Tuỳ chọn (TEXT_DETECT=ocr), thay cho heuristic "pixel sáng cạnh pixel tối" của textzone.py: bắt được cả chữ màu
không viền trên nền sáng mà heuristic bỏ sót. Không dùng rapidocr/OpenCV (nặng thêm ~225MB): tự tiền xử lý
và chỉ cần bản đồ xác suất chữ -> các hàng có "dòng chữ" (đoạn chạy ngang đủ dài) -> dải xuất hiện ≥ 50% khung.
Đo trên 1 lõi: ~65ms/khung ở 352x640, video 60s lấy 2 khung/giây ≈ 8-10s (≈3% một job render 300s).
Model tải bằng lệnh console `setup_offline`, kiểm SHA-256.
"""
import logging
import os
import subprocess

import numpy as np

_DIR = os.environ.get("OFFLINE_MODELS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_NAME = "PP-OCRv6_det_tiny.onnx"
MODEL_URL = ("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv6/det/"
             + MODEL_NAME)
MODEL_SHA256 = "f42c0fbd294d95eac1a550e131b277dac97462c8025fa4b6c3cec1b7894bd3d5"

W, H = 352, 640          # bội số của 32 (yêu cầu của mạng), giữ tỉ lệ 9:16
FPS = 2
THRESH = 0.3             # ngưỡng xác suất chữ (mặc định của PP-OCR)
CENTER = (0.15, 0.85)    # bỏ 15% mỗi bên: logo/watermark góc
MIN_RUN = 0.18           # một dòng chữ dài ít nhất 18% bề ngang khung (khối chữ nhỏ hơn hay là hoa văn, logo)
MAX_BAND_H = 0.15        # dải phụ đề không cao quá 15% khung
PERSIST = 0.5            # có mặt ở ≥ 50% số khung (phụ đề cố định, không phải chữ thoáng qua)

_session = None


def model_path() -> str:
    return os.path.join(_DIR, MODEL_NAME)


def available() -> bool:
    return os.path.exists(model_path())


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = 1
        opt.inter_op_num_threads = 1
        _session = ort.InferenceSession(model_path(), opt, providers=["CPUExecutionProvider"])
    return _session


def prob_map(frame_bgr: np.ndarray) -> np.ndarray:
    """Khung BGR HxWx3 uint8 (đã đúng cỡ W x H) -> bản đồ xác suất chữ HxW."""
    x = (frame_bgr.astype(np.float32) / 255.0 - 0.5) / 0.5
    x = x.transpose(2, 0, 1)[None]
    sess = _get_session()
    return sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]


def longest_runs(mask: np.ndarray) -> np.ndarray:
    """Độ dài đoạn True liên tiếp dài nhất của từng hàng (mask H x W)."""
    run = np.zeros(mask.shape[0], np.int32)
    best = np.zeros(mask.shape[0], np.int32)
    for j in range(mask.shape[1]):
        run = (run + 1) * mask[:, j]
        np.maximum(best, run, out=best)
    return best


def bands_from_runs(runs: np.ndarray, width: int) -> list:
    """runs: (số khung, H) độ dài dòng chữ dài nhất mỗi hàng ở mỗi khung -> [(trên, dưới)] tỉ lệ 0..1."""
    if len(runs) == 0:
        return []
    n, h = runs.shape
    rows = (runs >= MIN_RUN * width).mean(axis=0) >= PERSIST
    bands, cur = [], None
    for y, v in enumerate(rows):
        if v:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur:
            bands.append(cur)
            cur = None
    if cur:
        bands.append(cur)
    out = []
    for a, b in bands:
        bh = b - a + 1
        bw = float(np.median(runs[:, a:b + 1].max(axis=1)))
        if bh <= MAX_BAND_H * h and bw >= 2 * bh and bh >= 3:       # hình dạng một dòng chữ, không phải ô bàn cờ
            out.append((a / h, (b + 1) / h))
    merged = []                                                     # gộp dải cách nhau < 3% (phụ đề 2 dòng)
    for a, b in out:
        if merged and a - merged[-1][1] < 0.03:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(round(a, 4), round(b, 4)) for a, b in merged]


def detect_sync(path: str, start: float = 0.0, dur: float | None = None) -> list:
    """Dải chữ in cứng của video (như textzone.detect_text_bands). Lỗi thì ném ra để caller dùng heuristic."""
    args = ["ffmpeg", "-v", "error", "-threads", "1"]
    if start:
        args += ["-ss", f"{start:.2f}"]
    args += ["-i", path]
    if dur:
        args += ["-t", f"{dur:.2f}"]
    args += ["-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    c0, c1 = int(W * CENTER[0]), int(W * CENTER[1])
    runs = []
    try:
        while True:
            buf = proc.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            mask = prob_map(frame)[:, c0:c1] > THRESH
            runs.append(longest_runs(mask))
    finally:
        proc.stdout.close()
        proc.wait()
    bands = bands_from_runs(np.array(runs), W)
    logging.info("OCR dò chữ: %d khung, dải %s", len(runs), bands)
    return bands
