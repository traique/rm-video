"""Chuẩn hoá chữ tiếng Việt trước khi đưa vào giọng VieNeu (model không tự đọc số/ký hiệu như edge-tts):
"100g" -> "một trăm gam", "1/3 muỗng" -> "một phần ba muỗng", "60%" -> "sáu mươi phần trăm", "5-7 phút" -> "năm đến
bảy phút", "1,5kg" -> "một phẩy năm ký". Đọc kiểu miền Nam: "ngàn", "lẻ", "ký"."""
import re

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_UNITS = {"g": "gam", "gr": "gam", "gam": "gam", "kg": "ký", "mg": "mi li gam", "ml": "mi li lít", "l": "lít",
          "lit": "lít", "cm": "xen ti mét", "mm": "mi li mét", "m": "mét", "km": "ki lô mét", "h": "giờ",
          "p": "phút", "s": "giây", "°c": "độ", "°": "độ", "k": "ngàn", "đ": "đồng", "vnd": "đồng", "vnđ": "đồng"}


def _two(n: int, full: bool) -> str:
    """0-99. full: số đứng sau hàng trăm (5 -> 'lẻ năm')."""
    t, u = divmod(n, 10)
    if t == 0:
        return ("lẻ " + _DIGITS[u]) if full and u else (_DIGITS[u] if not full else "")
    head = "mười" if t == 1 else _DIGITS[t] + " mươi"
    if u == 0:
        return head
    if u == 1 and t > 1:
        tail = "mốt"
    elif u == 5:
        tail = "lăm"
    elif u == 4 and t > 1:
        tail = "bốn"
    else:
        tail = _DIGITS[u]
    return f"{head} {tail}"


def _three(n: int, full: bool) -> str:
    h, r = divmod(n, 100)
    if h == 0 and not full:
        return _two(r, False)
    out = f"{_DIGITS[h]} trăm"
    rest = _two(r, True)
    return f"{out} {rest}".strip()


def number_words(n: int) -> str:
    """Số nguyên -> chữ (miền Nam): 105 -> một trăm lẻ năm, 1500 -> một ngàn năm trăm, 21 -> hai mươi mốt."""
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + number_words(-n)
    parts = []
    for size, name in ((10 ** 9, "tỷ"), (10 ** 6, "triệu"), (10 ** 3, "ngàn"), (1, "")):
        q, n = divmod(n, size)
        if q:
            words = number_words(q) if q >= 1000 else _three(q, bool(parts))
            parts.append(f"{words} {name}".strip())
        elif parts and size == 1 and n:
            pass
    return " ".join(parts)


def _decimal(s: str) -> str:
    a, b = re.split(r"[.,]", s, maxsplit=1)
    tail = " ".join(_DIGITS[int(c)] for c in b) if len(b) > 2 or b.startswith("0") else number_words(int(b))
    return f"{number_words(int(a))} phẩy {tail}"


def _num(s: str) -> str:
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):                 # 1.000.000: dấu chấm phân cách hàng ngàn
        return number_words(int(s.replace(".", "")))
    if re.fullmatch(r"\d+[.,]\d+", s):
        return _decimal(s)
    if s.isdigit():
        return number_words(int(s))
    # "12.5.2026", "1.000,5", phiên bản...: đọc từng cụm (không bao giờ làm hỏng cả job vì một con số lạ)
    return " chấm ".join(number_words(int(p)) if p else "" for p in re.split(r"[.,]", s)).strip()


_NUM = r"\d+(?:[.,]\d+)*"
_UNIT_RX = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))


def normalize(text: str) -> str:
    t = text or ""
    # ngày (12/11, 1/2/2026) và giờ (10:30) đọc đúng kiểu, trước khi xét phân số / khoảng
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", lambda m: f"ngày {number_words(int(m.group(1)))} tháng "
               f"{number_words(int(m.group(2)))} năm {number_words(int(m.group(3)))}", t)
    t = re.sub(r"(?i)(ngày|hôm|mùng|sáng|trưa|chiều|tối)\s+(\d{1,2})/(\d{1,2})(?![\d/])",
               lambda m: f"{m.group(1)} {number_words(int(m.group(2)))} tháng {number_words(int(m.group(3)))}", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{number_words(int(m.group(1)))} giờ"
               + (f" {number_words(int(m.group(2)))}" if int(m.group(2)) else ""), t)
    t = re.sub(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", lambda m: "nửa" if m.group(0).replace(" ", "") == "1/2" else
               f"{number_words(int(m.group(1)))} phần {number_words(int(m.group(2)))}", t)
    # khoảng + đơn vị/%: "5-7kg" -> "năm đến bảy ký", "50-60%" -> "năm mươi đến sáu mươi phần trăm"
    t = re.sub(rf"({_NUM})\s*[-–~]\s*({_NUM})\s*%", lambda m: f"{_num(m.group(1))} đến {_num(m.group(2))} phần trăm", t)
    t = re.sub(rf"({_NUM})\s*[-–~]\s*({_NUM})\s*({_UNIT_RX})(?![\wÀ-ỹ])",
               lambda m: f"{_num(m.group(1))} đến {_num(m.group(2))} {_UNITS[m.group(3).lower()]}", t, flags=re.I)
    t = re.sub(rf"({_NUM})\s*%", lambda m: f"{_num(m.group(1))} phần trăm", t)
    t = re.sub(rf"({_NUM})\s*({_UNIT_RX})(?![\wÀ-ỹ])", lambda m: f"{_num(m.group(1))} {_UNITS[m.group(2).lower()]}", t,
               flags=re.I)
    t = re.sub(rf"({_NUM})\s*[-–~]\s*({_NUM})(?=\s|$|[^\d])", lambda m: f"{_num(m.group(1))} đến {_num(m.group(2))}", t)
    t = re.sub(_NUM, lambda m: _num(m.group(0)), t)
    t = t.replace("&", " và ").replace("+", " cộng ")
    return re.sub(r"\s+", " ", t).strip()
