"""Danh mục giọng VieNeu (chạy trên Kaggle) và tốc độ đọc đo thật — hàm thuần, không đọc cấu hình.

Trước đây nằm trong kaggle_worker; core_video phải import trễ kaggle_worker (vốn không được gửi lên Kaggle) chỉ để
biết giọng nam hay nữ — một cạnh import chỉ an toàn vì may mắn không chạy trên Kaggle.
"""
from __future__ import annotations

# Giọng VieNeu v3 Turbo miền Nam (nhãn của model) và tốc độ đọc đo thật (chữ/giây, chỉ phần tiếng nói, 12/11/2026)
SOUTH_FEMALE = ["Mỹ Duyên", "Thục Đoan", "Thùy Dung", "Kim Thanh"]
SOUTH_MALE = ["Adam", "Thái Sơn", "Minh Triết", "Đức Trí"]
RATE = {"Mỹ Duyên": 3.08, "Adam": 4.15, "Thùy Dung": 4.5}
RATE_EDGE = 3.6                  # edge Hoài My/Nam Minh +8%: 3.9 chữ/giây phần tiếng nói, chừa biên
RATE_CAPCUT = 3.8
CLONE = "clone"
PREFIX = "vieneu:"


def voice_name(voice: str) -> str:
    """'vieneu:Mỹ Duyên' -> 'Mỹ Duyên'; giọng khác -> ''."""
    return voice.split(":", 1)[1] if (voice or "").startswith(PREFIX) else ""


def is_male(voice: str) -> bool:
    return voice_name(voice) in SOUTH_MALE


def vieneu_rate(name: str) -> float:
    """Chữ/giây của một giọng VieNeu (giọng chưa đo: 3.6; giọng nhân bản: 3.4)."""
    return RATE.get(name, 3.6 if name != CLONE else 3.4)
