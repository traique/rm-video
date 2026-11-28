"""Nhạc nền nhẹ dưới lời đọc (VOICE_BED, bản 1.0.0).

Video có lời đọc (nấu ăn / review) được lót một bài trong thư viện 🎵 Nhạc nền (bgm.py) thật nhỏ dưới giọng: vào 1 giây,
ra 2 giây, tự hạ thêm khi giọng đang đọc (sidechain), tắt trong các quãng giữ tiếng gốc. Video gốc ĐÃ có nhạc (tiếng nền
sau khi tách giọng vẫn còn nhạc của người gốc) thì không lót thêm: hai bài chồng nhau nghe rất rối.

Dò nhạc: nhạc có các nốt ngân (đỉnh phổ hẹp đứng yên vài trăm mili giây); tiếng xèo, dao thớt là tiếng ồn dải rộng,
lời nói đổi cao độ liên tục. Đo trên tiếng nền đã tách giọng nếu có, không thì trên tiếng gốc ngoài các quãng có người
nói (cổng tiếng nói đã biết từ bước bóc lời).
"""
from __future__ import annotations

import logging

import numpy as np

from rmvideo.media import proc

SR = 16000
NFFT = 2048                     # 7,8 Hz một ô tần số: phân biệt được các nốt gần nhau
HOP = 512                       # 32 ms một khung
LO_HZ, HI_HZ = 150.0, 2500.0    # vùng nốt nhạc chính (dưới: tiếng ù / bếp; trên: xèo xèo)
PEAK_DB = 9.0                   # đỉnh phổ nổi hơn nền xung quanh chừng này dB mới tính là nốt
HOLD = 8                        # nốt phải đứng yên ≥ 8 khung (~0,26 giây)
MUSIC_SCORE = 0.22              # tỉ lệ khung có nốt ngân ≥ mức này: video gốc có nhạc
LEVEL = 0.10                    # nhạc lót ≈ -20 dB dưới giọng đọc (trước khi tự hạ thêm lúc đang đọc)


def _decode(path: str, max_s: float) -> np.ndarray:
    out = proc.run_sync(["ffmpeg", "-v", "error", "-i", path, "-t", f"{max_s:.0f}", "-vn", "-ac", "1",
                         "-ar", str(SR), "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def tonal_score(x: np.ndarray, keep: np.ndarray | None = None) -> float:
    """Tỉ lệ khung (có tiếng) chứa ít nhất 2 nốt ngân. keep: mặt nạ khung được tính (bỏ quãng có người nói)."""
    n = 1 + (len(x) - NFFT) // HOP if len(x) >= NFFT else 0
    if n < HOLD * 4:
        return 0.0
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT)[None, :], axis=1))
    lo, hi = int(LO_HZ * NFFT / SR), int(HI_HZ * NFFT / SR)
    db = 20 * np.log10(spec[:, lo:hi] + 1e-9)
    # nền xung quanh mỗi ô: trung vị trượt ±12 ô (nhanh: lấy trung vị các bản dịch)
    shifts = np.stack([np.roll(db, s, axis=1) for s in range(-12, 13, 3)])
    floor = np.median(shifts, axis=0)
    peak = (db - floor > PEAK_DB) & (db >= np.roll(db, 1, axis=1)) & (db >= np.roll(db, -1, axis=1))
    peak = peak | np.roll(peak, 1, axis=1) | np.roll(peak, -1, axis=1)          # nốt rung nhẹ ±1 ô vẫn là một nốt
    held = peak.copy()
    for k in range(1, HOLD):                                                    # đứng yên đủ HOLD khung liên tiếp
        held[:-k] &= peak[k:]
    held[-HOLD:] = False
    loud = 20 * np.log10(np.sqrt((x[idx] ** 2).mean(axis=1)) + 1e-9) > -50      # bỏ khung im lặng
    mask = loud if keep is None else loud & keep[:n]
    if mask.sum() < HOLD * 4:
        return 0.0
    notes = held.sum(axis=1) >= 2 * 3                                           # ≥ 2 nốt (mỗi nốt nở thành 3 ô)
    return float(notes[mask].mean())


def music_score(path: str, speech: list | None = None, max_s: float = 120.0) -> float:
    """Điểm «có nhạc» của một file tiếng (đồng bộ). speech: [(giây bắt đầu, giây kết thúc)] có người nói -> bỏ qua."""
    x = _decode(path, max_s)
    keep = None
    if speech:
        n = max(0, 1 + (len(x) - NFFT) // HOP)
        t = (np.arange(n) * HOP + NFFT / 2) / SR
        keep = np.ones(n, dtype=bool)
        for a, b in speech:
            keep &= ~((t >= float(a) - 0.3) & (t <= float(b) + 0.3))
    return tonal_score(x, keep)


def source_has_music(path: str, speech: list | None = None) -> bool:
    try:
        s = music_score(path, speech)
    except Exception as e:                     # đo hỏng: coi như có nhạc (không lót thêm còn hơn chồng hai bài)
        logging.info("Không đo được nhạc của video gốc (%s): không lót nhạc nền", str(e)[:120])
        return True
    logging.info("Điểm nhạc của tiếng gốc / tiếng nền: %.2f (ngưỡng %.2f)", s, MUSIC_SCORE)
    return s >= MUSIC_SCORE
