"""Đổi từ miền Bắc sang từ miền Nam trong lời AI viết (giọng đọc miền Nam, kênh Bếp Đong Đủ).

Chỉ đổi khi chắc nghĩa: từ có hai nghĩa thì đổi theo ngữ cảnh ("trần qua rau" -> "trụng qua rau" nhưng "trần nhà" giữ
nguyên; "quả chanh" -> "trái chanh" nhưng "kết quả" giữ nguyên; "lá dứa" là lá nếp, không phải trái thơm). Các cặp
đổi đều cùng nhóm nguyên liệu / đơn vị trong ingredients.py nên bộ kiểm nguyên liệu, con số không báo nhầm.
Chạy ngay sau khi AI trả lời, TRƯỚC các bước kiểm. Không đụng lời người dùng tự sửa lúc duyệt."""
from __future__ import annotations

import re
from collections.abc import Callable

_FRUIT = ("chanh|cam|quýt|bưởi|ớt|cà chua|cà|trứng|dừa|bơ|xoài|táo|lê|chuối|me|mướp|bí|dưa|ổi|nho|dâu|đu đủ|mận|"
          "đào|vải|nhãn|khế|sấu|óc chó|thơm|khóm|mít|hồng|na|lựu|kiwi|dứa")

# (mẫu, thay bằng) — theo thứ tự: cụm dài trước từ đơn
_RULES: list[tuple[str, str | Callable[[re.Match], str]]] = [
    (r"nem rán", "chả giò"),
    (r"mì chính", "bột ngọt"),
    (r"hoa quả", "trái cây"),
    (r"rau mùi|lá mùi|mùi ta", "ngò"),
    (r"mùi tàu", "ngò gai"),
    (r"mộc nhĩ", "nấm mèo"),
    (r"dưa chuột", "dưa leo"),
    (r"xì dầu", "nước tương"),
    (r"đậu phụ", "đậu hũ"),
    (r"đỗ (xanh|đen|đỏ|tương)", r"đậu \1"),
    (r"(hạt|củ|dầu|bơ|kẹo|muối) lạc", r"\1 đậu phộng"),
    (r"lạc (rang|luộc|giã|xay|sống|tẩm)", r"đậu phộng \1"),
    (r"thìa(?! là)", "muỗng"),
    (r"bát(?! (?:ngát|giác|quái|hương|bửu|tiên|tràng|phố|nhã))", "chén"),
    (r"cái cốc|chiếc cốc", "cái ly"),
    (r"(\d+) cốc", r"\1 ly"),
    (r"cốc (nước|sữa|bột|đường|dầu)", r"ly \1"),
    (r"đĩa", "dĩa"),
    (r"xoong", "nồi"),
    (r"muôi", "vá"),
    (r"(đậy|mở|cái|nắp) vung|vung (nồi|chảo)", lambda m: m.group(0).replace("vung", "nắp").replace("nắp nắp", "nắp")),
    (r"vừng", "mè"),
    (r"ngô(?! đồng)", "bắp"),
    (r"thịt lợn|lợn", lambda m: m.group(0).replace("lợn", "heo")),
    (r"ninh(?= (?:xương|nhừ|mềm|kỹ|lửa|nhỏ|cho|trong|khoảng|tầm|thêm|tiếp|đến|tới|\d)\b)", "hầm"),   # "Ninh Bình" giữ
    (r"rán", "chiên"),
    (r"trần(?= (?:qua|sơ|nhanh|nước sôi|lại|cho|rau|thịt|tôm|mực|cá|giá|bún|mì)\b)", "trụng"),
    (r"(đem|mang|đi) trần", r"\1 trụng"),
    (r"quả(?= (?:" + _FRUIT + r")\b)", "trái"),
    (r"(\d+(?:[.,]\d+)?) quả", r"\1 trái"),
]
_COMPILED = [(re.compile(r"(?<![\w])(?:" + p + r")(?![\w])", re.IGNORECASE), r) for p, r in _RULES]


def _keep_case(src: str, out: str) -> str:
    return out[:1].upper() + out[1:] if src[:1].isupper() and out else out


def south(text: str) -> str:
    """Lời AI viết -> từ miền Nam (chữ đầu cụm viết hoa thì giữ hoa). Không có gì để đổi: trả nguyên."""
    out = text or ""
    for rx, rep in _COMPILED:
        def one(m: re.Match, rep: str | Callable[[re.Match], str] = rep) -> str:
            new = rep(m) if callable(rep) else m.expand(rep)
            return _keep_case(m.group(0), new[:1].lower() + new[1:] if not callable(rep) else new)
        out = rx.sub(one, out)
    return out
