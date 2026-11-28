"""(Kaggle GPU) Tách tiếng nền thành NHẠC và TIẾNG ĐỘNG (1.2.0): bỏ nhạc gốc, giữ tiếng xèo xèo / dao thớt / nước sôi.

Model Bandit Plus «cocktail party» (Watcharasupat và cộng sự; DnR v3: lời nói / nhạc / tiếng động), mạng band-split RNN
44,1 kHz mono. Mã suy luận: rmvideo/media/bandit/ (Apache-2.0, bản rút gọn của FreeVideo). TRỌNG SỐ CC BY-NC 4.0 — chỉ
dùng khi kênh CHƯA kiếm tiền (cài đặt SFX_SEP; người dùng xác nhận 21/11/2026), ghi công tác giả (bandit/WEIGHTS-…).

Cách dùng trong vocal_sep: tiếng nền = gốc − giọng (RoFormer / Kim_Vocal_2 như cũ, đã lọc giọng sót) -> Bandit -> chỉ giữ
phần «effects». Nhạc gốc bị bỏ nên renderer đo tiếng nền không còn nhạc và tự lót nhạc Lyria dưới lời. Cắt khúc 6 giây
chồng 3 giây (cửa sổ Hann), mỗi kênh tách riêng: bộ nhớ GPU không phụ thuộc độ dài bài (cách làm của FreeVideo,
freevideo_engine/stems/separate.py, Apache-2.0)."""
from __future__ import annotations

import hashlib
import os
import urllib.request
from typing import Any

import numpy as np

SR = 44100
CHUNK, HOP = 6 * SR, 3 * SR
BATCH = 4
WEIGHTS = "bandit_plus_cocktail.pt"
# bản FreeVideo chuyển checkpoint Zenodo 10160698 sang state dict thuần (không đổi giá trị), ghim revision + SHA-256
WEIGHTS_URL = ("https://huggingface.co/BowenXue/FreeVideo-models/resolve/2ed0b3b98ea92dec01699dc97d96e15cfe208d69/"
               "stems/bandit_plus_cocktail.pt")
WEIGHTS_SHA256 = "02ad94875ae060e03428af0e667a5a5cbfa1a6c2355f07f32c499be8d615ba1c"
WEIGHTS_BYTES = 148724733
STEMS = ("speech", "music", "effects")
# bài quá nhỏ: model để lời / tiếng động lẫn vào nhạc (FreeVideo đo: bản thu cũ -37 dBFS ra phần lời rỗng) -> nâng lên
QUIET_RMS, TARGET_RMS, MAX_LIFT = 0.04, 0.08, 12.0
MODEL_KWARGS: dict[str, Any] = dict(
    in_channel=1, stems=list(STEMS), band_specs="musical", n_bands=64, fs=SR, require_no_overlap=False,
    require_no_gap=True, normalize_channel_independently=False, treat_channel_as_feature=True, n_sqm_modules=8,
    emb_dim=128, rnn_dim=256, bidirectional=True, rnn_type="GRU", mlp_dim=512, hidden_activation="Tanh",
    complex_mask=True)
_MODEL: dict[str, Any] = {}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def weights_path(models_dir: str) -> str:
    """Tải trọng số (148MB, lần đầu) vào models_dir, kiểm SHA-256; file sai thì tải lại, vẫn sai thì báo lỗi."""
    os.makedirs(models_dir, exist_ok=True)
    p = os.path.join(models_dir, WEIGHTS)
    if os.path.exists(p) and (os.path.getsize(p) != WEIGHTS_BYTES or _sha256(p) != WEIGHTS_SHA256):
        os.remove(p)
    if not os.path.exists(p):
        tmp = p + ".part"
        urllib.request.urlretrieve(WEIGHTS_URL, tmp)
        if _sha256(tmp) != WEIGHTS_SHA256:
            os.remove(tmp)
            raise RuntimeError("trọng số Bandit sai SHA-256 (tải hỏng hoặc bị thay)")
        os.replace(tmp, p)
    return p


def load(weights: str, device: str = "cuda") -> Any:
    if "m" not in _MODEL:
        import torch

        from rmvideo.media.bandit.wrapper import MultiMaskMultiSourceBandSplitRNN
        model = MultiMaskMultiSourceBandSplitRNN(**MODEL_KWARGS)
        model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
        _MODEL["m"] = model.to(device).eval()
    return _MODEL["m"]


def lift(audio: np.ndarray) -> float:
    level = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0
    return 1.0 if level <= 1e-5 or level >= QUIET_RMS else min(TARGET_RMS / level, MAX_LIFT)


def separate(audio: np.ndarray, model: Any, device: str = "cuda") -> dict[str, np.ndarray]:
    """audio (kênh, mẫu) float32 44,1 kHz -> {"speech", "music", "effects": (kênh, mẫu)} cùng độ dài; mỗi kênh tách riêng."""
    import torch
    ch, n = audio.shape
    g = lift(audio)
    x = torch.from_numpy(np.ascontiguousarray(audio * np.float32(g)))
    edge = CHUNK - HOP
    count = -(-(max(n + 2 * edge, CHUNK) - CHUNK) // HOP) + 1
    total = (count - 1) * HOP + CHUNK
    pad = torch.zeros(ch, total)
    pad[:, edge:edge + n] = x
    m = min(edge, n - 1)
    if m > 0:                                        # soi gương nửa khúc ở hai đầu: mép được phủ như giữa bài
        pad[:, edge - m:edge] = torch.flip(x[:, 1:m + 1], dims=[1])
        pad[:, edge + n:edge + n + m] = torch.flip(x[:, n - 1 - m:n - 1], dims=[1])
    win = torch.hann_window(CHUNK, periodic=True)
    out = {s: torch.zeros(ch, total) for s in STEMS}
    jobs = [(c, k * HOP) for c in range(ch) for k in range(count)]
    for i in range(0, len(jobs), BATCH):
        part = jobs[i:i + BATCH]
        pieces = torch.stack([pad[c, s:s + CHUNK] for c, s in part])[:, None, :]
        if not torch.any(pieces != 0):
            continue
        with torch.inference_mode():
            _, res = model({"audio": {"mixture": pieces.to(device)}})
        for s in STEMS:
            y = res["audio"][s].float().cpu()
            for r, (c, st) in enumerate(part):
                out[s][c, st:st + CHUNK] += y[r, 0] * win
    return {s: (v[:, edge:edge + n] / g).numpy() for s, v in out.items()}


def effects_only(amb: np.ndarray, models_dir: str, device: str = "cuda") -> tuple[np.ndarray, dict[str, float]]:
    """Tiếng nền (đã bỏ giọng) -> chỉ tiếng động, bỏ nhạc. Trả (tiếng động, {mức dB từng phần}) để ghi log."""
    model = load(weights_path(models_dir), device)
    parts = separate(amb.astype(np.float32), model, device)

    def db(x: np.ndarray) -> float:
        return round(20 * float(np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-9)), 1)
    return parts["effects"], {s: db(parts[s]) for s in STEMS}
