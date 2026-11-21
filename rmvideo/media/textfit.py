"""Đo bề rộng chữ theo font của kênh (Be Vietnam Pro ExtraBold) và xếp dòng cho VỪA KHUNG.

drawtext của FFmpeg không tự xuống dòng hay thu chữ: trước đây tiêu đề xuống dòng theo SỐ KÝ TỰ (22 ký tự / dòng) nên
"MỰC HẤP GỪNG GIÒN NGỌT" (22 ký tự, chữ in hoa rất rộng) tràn ra hai mép video (bản 43). Giờ đo bằng bảng độ rộng
từng chữ của chính font đó (đo một lần bằng Pillow; Botkeep không cài Pillow) rồi chọn số dòng + cỡ chữ.
Chữ có dấu rộng bằng chữ gốc (Ư, Ơ rộng hơn U, O chút ít, đã tính)."""
from __future__ import annotations

import unicodedata

# độ rộng (phần nghìn cỡ chữ) — BeVietnamPro-ExtraBold.ttf
_ADV = {
    "A": 780, "B": 721, "C": 781, "D": 758, "E": 651, "F": 647, "G": 793, "H": 740, "I": 305, "J": 499, "K": 728,
    "L": 650, "M": 900, "N": 754, "O": 829, "P": 690, "Q": 828, "R": 726, "S": 677, "T": 698, "U": 746, "V": 758,
    "W": 1074, "X": 786, "Y": 736, "Z": 669, "Đ": 783, "Ư": 746, "Ơ": 829,
    "a": 663, "b": 673, "c": 635, "d": 673, "e": 624, "f": 416, "g": 665, "h": 617, "i": 306, "j": 310, "k": 613,
    "l": 299, "m": 897, "n": 607, "o": 663, "p": 673, "q": 673, "r": 441, "s": 546, "t": 441, "u": 598, "v": 600,
    "w": 888, "x": 624, "y": 601, "z": 527, "đ": 692, "ư": 598, "ơ": 678,
    "0": 708, "1": 446, "2": 669, "3": 687, "4": 723, "5": 689, "6": 706, "7": 599, "8": 679, "9": 706,
    " ": 230, "%": 878, ",": 361, "!": 361, "?": 637, "'": 237, "-": 606, ".": 352, ":": 352, ";": 361, "/": 553,
    "(": 458, ")": 458, "&": 770, "+": 666, "–": 711, "…": 908, '"': 416, "“": 469, "”": 473,
}
_WIDE = 1000          # ký tự lạ (emoji, ký hiệu): tính rộng cho chắc


def _adv(ch: str) -> int:
    if ch in _ADV:
        return _ADV[ch]
    d = unicodedata.normalize("NFD", ch)
    if d and d[0] in _ADV:                     # chữ có dấu: rộng như chữ gốc (Ư/Ơ đã có riêng)
        base = d[0]
        if "̛" in d:                      # dấu móc (ư, ơ viết dạng tổ hợp)
            base = {"U": "Ư", "O": "Ơ", "u": "ư", "o": "ơ"}.get(base, base)
        return _ADV[base]
    return _WIDE


def em(text: str) -> float:
    """Bề rộng chuỗi tính theo cỡ chữ (1.0 = bằng cỡ chữ)."""
    return sum(_adv(c) for c in text) / 1000


def _best_split(words: list, n: int) -> list:
    """Chia dãy từ thành n dòng (theo thứ tự) sao cho dòng rộng nhất hẹp nhất (quy hoạch động, vài chục từ)."""
    m = len(words)
    w = [em(x) for x in words]
    sp = em(" ")

    def width(i: int, j: int) -> float:        # dòng gồm từ i..j-1
        return sum(w[i:j]) + sp * (j - i - 1)
    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    cut = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for k in range(1, n + 1):
        for j in range(1, m + 1):
            for i in range(k - 1, j):
                v = max(best[k - 1][i], width(i, j))
                if v < best[k][j]:
                    best[k][j], cut[k][j] = v, i
    lines, j = [], m
    for k in range(n, 0, -1):
        i = cut[k][j]
        lines.append(" ".join(words[i:j]))
        j = i
    return lines[::-1]


def fit(text: str, avail_px: float, fs_max: int, max_lines: int = 3) -> tuple[list, int]:
    """(các dòng, cỡ chữ) để `text` nằm gọn trong bề ngang `avail_px`. Ưu tiên ÍT dòng nhất mà vẫn giữ cỡ chữ lớn
    nhất; không vừa thì chọn cách xếp cho chữ to nhất (cỡ chữ luôn tính từ bề rộng nên không bao giờ tràn).
    Một từ dài hơn cả khung (hiếm) thì thu chữ, không cắt từ."""
    words = (text or "").split()
    if not words:
        return [], fs_max
    best: tuple[list, int] | None = None
    for n in range(1, min(max_lines, len(words)) + 1):
        lines = _best_split(words, n)
        widest = max(em(ln) for ln in lines)
        fs = int(min(fs_max, avail_px / widest)) if widest else fs_max
        if fs >= fs_max:
            return lines, fs_max
        if best is None or fs > best[1]:
            best = (lines, fs)
    assert best is not None
    return best[0], max(best[1], 1)
