"""(Botkeep) Dò quãng có tiếng NGƯỜI trong tiếng gốc bằng Silero VAD (ONNX, giấy phép MIT) — 1.5.0.

Chế độ bếp khi KHÔNG tách giọng được (Kaggle không cấp GPU, máy Botkeep quá chậm) thì tiếng nền là tiếng gốc và cổng tắt
tiếng chạy theo MỐC LỜI của máy nghe (filtergraph._gate_chain). Máy nghe (Groq Whisper) hay bỏ sót câu cuối, câu nói nhỏ:
job 13 ngày 26/11 câu «Và đây là series…» ở 4 giây cuối không có trong bản bóc lời nên giọng người gốc lọt nguyên vào video.
Silero VAD nghe thẳng sóng âm (không cần hiểu lời; 47 giây audio mất 0,2 giây CPU) nên bắt được cả câu máy nghe bỏ; quãng nó
thấy được GỘP với mốc lời rồi mới đưa vào cổng. Lỗi gì (mạng, model hỏng, ffmpeg) cũng trả [] — cổng theo mốc lời chạy như cũ.

Model silero_vad.onnx bản v5.1.2 (2,3MB) tải một lần về models/, kiểm SHA-256 như Kim_Vocal_2 (vocal_sep.py), chạy bằng
onnxruntime đã có sẵn. Đầu vào 16 kHz mono, mỗi khung 512 mẫu (32 ms) kèm 64 mẫu cuối của khung trước, trạng thái [2, 1, 128]
truyền qua các khung (đúng cách gói silero-vad gọi model).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import time
import urllib.request
from collections.abc import Sequence
from typing import Any

import numpy as np

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
MODEL_NAME = "silero_vad_v5.1.2.onnx"
MODEL_URLS = ("https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx",
              "https://cdn.jsdelivr.net/gh/snakers4/silero-vad@v5.1.2/src/silero_vad/data/silero_vad.onnx")
# SHA-256 của bản đã kiểm (2.327.524 byte, tải 26/11/2026 từ tag v5.1.2). File khác hash = bị thay / tải dở -> không chạy.
MODEL_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
SR = 16000
WIN = 512                   # mẫu mỗi khung (32 ms ở 16 kHz)
CTX = 64                    # mẫu ngữ cảnh của khung trước nối vào đầu khung
STEP = WIN / SR
ON, OFF = 0.5, 0.35         # ngưỡng vào / ra (trễ: tiếng nhỏ dần không bị cắt phụt)
MIN_SPEECH = 0.2            # quãng ngắn hơn chừng này giây thì bỏ (tiếng lách cách, hơi thở)
PAD = 0.15                  # đệm hai đầu mỗi quãng (cổng đã có đệm riêng, đây chỉ là mép khung 32 ms)
MERGE = 0.3                 # hai quãng cách nhau dưới chừng này thì gộp
MAX_SEC = 600.0             # nghe tối đa chừng này giây (video dài nhất bot nhận)
_verified: set = set()
_SESSION: dict[str, Any] = {}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def model_path() -> str:
    """Tải model (lần đầu, thử từng địa chỉ) và kiểm SHA-256 mỗi lần khởi động: file bị tráo hay tải dở đều bị từ chối."""
    os.makedirs(_DIR, exist_ok=True)
    p = os.path.join(_DIR, MODEL_NAME)
    if p in _verified:
        return p
    if os.path.exists(p) and sha256_file(p) != MODEL_SHA256:
        logging.warning("Model %s sai checksum (hỏng hoặc bị thay), tải lại", MODEL_NAME)
        os.remove(p)
    if not os.path.exists(p):
        tmp = p + ".part"
        last = ""
        for url in MODEL_URLS:
            try:
                logging.info("Tải model dò tiếng người %s (~2MB, chỉ lần đầu)...", MODEL_NAME)
                urllib.request.urlretrieve(url, tmp)
                got = sha256_file(tmp)
                if got == MODEL_SHA256:
                    os.replace(tmp, p)
                    break
                last = f"sai checksum ({got[:12]}…)"
                os.remove(tmp)
            except Exception as e:                 # mạng / 404: thử địa chỉ kế
                last = str(e)[:120]
                if os.path.exists(tmp):
                    os.remove(tmp)
        if not os.path.exists(p):
            raise RuntimeError(f"không tải được model {MODEL_NAME}: {last}")
    _verified.add(p)
    return p


def _session() -> Any:
    if "s" not in _SESSION:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1              # model nhỏ, một luồng là đủ; không tranh CPU với ffmpeg
        opts.inter_op_num_threads = 1
        _SESSION["s"] = ort.InferenceSession(model_path(), opts, providers=["CPUExecutionProvider"])
    return _SESSION["s"]


def decode(path: str, max_sec: float = MAX_SEC) -> np.ndarray:
    """Tiếng của video / audio -> mono 16 kHz float32 (ffmpeg)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", f"{max_sec:.2f}", "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def probabilities(samples: np.ndarray, session: Any = None) -> list[float]:
    """Xác suất có tiếng người của từng khung WIN mẫu (STEP giây), theo thứ tự."""
    sess = session or _session()
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if len(x) % WIN:
        x = np.concatenate([x, np.zeros(WIN - len(x) % WIN, dtype=np.float32)])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    ctx = np.zeros(CTX, dtype=np.float32)
    sr = np.array(SR, dtype=np.int64)
    out: list[float] = []
    for i in range(0, len(x), WIN):
        chunk = x[i:i + WIN]
        p, state = sess.run(None, {"input": np.concatenate([ctx, chunk])[None, :], "state": state, "sr": sr})
        out.append(float(p[0, 0]))
        ctx = chunk[-CTX:]
    return out


def windows_from_probs(probs: Sequence[float], step: float = STEP, on: float = ON, off: float = OFF,
                       min_len: float = MIN_SPEECH, pad: float = PAD, merge_gap: float = MERGE) -> list[tuple[float, float]]:
    """Xác suất từng khung -> [(đầu, cuối)] giây có tiếng người: vào khi ≥ on, ra khi < off (trễ), bỏ quãng ngắn, đệm hai
    đầu, gộp quãng sát nhau."""
    wins: list[list[float]] = []
    start: float | None = None
    for k, p in enumerate(probs):
        t = k * step
        if start is None and p >= on:
            start = t
        elif start is not None and p < off:
            wins.append([start, t])
            start = None
    if start is not None:
        wins.append([start, len(probs) * step])
    out: list[list[float]] = []
    for a, b in wins:
        if b - a < min_len:
            continue
        a, b = max(0.0, a - pad), b + pad
        if out and a - out[-1][1] < merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def merge(*lists: Sequence[tuple[float, float]], gap: float = 0.05) -> list[tuple[float, float]]:
    """Hợp các danh sách quãng [(a, b)] (gộp quãng chồng / sát nhau)."""
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for ws in lists for a, b in ws if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def speech_windows_sync(path: str, max_sec: float = MAX_SEC) -> list[tuple[float, float]]:
    t0 = time.time()
    x = decode(path, max_sec)
    if len(x) < WIN:
        return []
    wins = windows_from_probs(probabilities(x))
    logging.info("VAD: %d quãng có tiếng người, tổng %.1fs / %.1fs audio (%.1fs)", len(wins),
                 sum(b - a for a, b in wins), len(x) / SR, time.time() - t0)
    return wins


async def speech_windows(path: str, max_sec: float = MAX_SEC) -> list[tuple[float, float]]:
    """[(đầu, cuối)] giây có tiếng người trên trục của file `path` (video gốc). Lỗi gì cũng trả [] (ghi cảnh báo)."""
    try:
        return await asyncio.to_thread(speech_windows_sync, path, max_sec)
    except Exception as e:
        logging.warning("VAD lỗi (%s: %s): cổng tiếng gốc chỉ theo mốc lời máy nghe", type(e).__name__, str(e)[:120])
        return []
