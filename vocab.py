"""Từ điển cho bước bóc lời: tên món, nguyên liệu, tên riêng của kênh — để máy nghe viết ĐÚNG chính tả.

Ba chỗ dùng:
1. Groq Whisper: tham số `prompt` (≤ 224 token, cùng ngôn ngữ với audio) — Whisper viết theo chính tả trong prompt.
   gemini-3.5-transcribe có `custom_vocabulary` nhưng KHÔNG dùng được cùng mốc từng từ (API trả 400), nên Gemini
   không nhận từ điển ở bước này.
2. Sửa lỗi sau khi nghe (mọi nguồn): cụm ≥ 2 âm tiết nghe GẦN giống một cụm trong từ điển (chỉ khác dấu hoặc
   phụ âm đầu hay bị nhầm s/x, d/gi/r, ch/tr) thì thay bằng chính tả đúng. Rất thận trọng: chỉ cụm từ người dùng
   khai báo hoặc tên nguyên liệu ≥ 3 âm tiết, và không quá nửa số âm tiết được khác (tránh "đâu đó" -> "đậu đỏ").
3. Lời nhắc AI viết kịch bản: danh sách tên cần viết đúng.

Nguồn từ: ASR_VOCAB (Cài đặt, cách nhau dấu phẩy) + tiêu đề video + tên nguyên liệu thấy trong tiêu đề/mô tả.
"""
import math
import re
import unicodedata

import config
import ingredients

PROMPT_MAX_BYTES = 380        # ~190 token với chữ Việt có dấu (Groq giới hạn 224 token)
MAX_TERMS = 200


def _clean(text: str) -> str:
    """Bỏ hashtag, link, emoji, ký tự trang trí; giữ chữ, số, dấu câu cơ bản."""
    t = re.sub(r"https?://\S+|#\S+|@\S+", " ", text or "")
    t = "".join(ch if (ch.isalnum() or ch in " ,.-'/&") else " " for ch in t)
    return " ".join(t.split())


def user_terms() -> list:
    """Từ người dùng khai báo trong ASR_VOCAB (dấu phẩy, chấm phẩy hoặc xuống dòng)."""
    out, seen = [], set()
    for x in re.split(r"[,;\n]", getattr(config, "ASR_VOCAB", "") or ""):
        x = " ".join(x.split())
        if x and x.lower() not in seen and len(x) <= 60:
            seen.add(x.lower())
            out.append(x)
    return out[:MAX_TERMS]


def context_terms(title: str, desc: str = "") -> list:
    """Tên nguyên liệu (tên gọi chuẩn của nhóm) thấy trong tiêu đề + mô tả video."""
    found = ingredients.find(_clean(f"{title} {desc}"))
    return [ingredients.GROUPS[i][0] for i in sorted(found)]


def groq_prompt(title: str, desc: str = "") -> str:
    """Prompt cho Whisper: một câu tiếng Việt có tiêu đề + từ cần viết đúng, cắt cho vừa giới hạn token."""
    head = _clean(title)[:120]
    terms = user_terms() + [t for t in context_terms(title, desc) if t.lower() not in {u.lower() for u in user_terms()}]
    if not head and not terms:
        return ""
    out = (f"{head}. " if head else "")
    added = []
    for t in terms:
        cand = out + "Từ khóa: " + ", ".join(added + [t]) + "."
        if len(cand.encode("utf-8")) > PROMPT_MAX_BYTES:
            break
        added.append(t)
    if added:
        out += "Từ khóa: " + ", ".join(added) + "."
    while len(out.encode("utf-8")) > PROMPT_MAX_BYTES:          # tiêu đề quá dài
        out = out[: len(out) - 10]
    return out.strip()


def prompt_hint() -> str:
    """Dòng thêm vào lời nhắc AI viết kịch bản (rỗng nếu người dùng chưa khai báo từ điển)."""
    terms = user_terms()
    if not terms:
        return ""
    return ("\n\nTỪ ĐIỂN CỦA KÊNH (tên món, nguyên liệu, tên riêng — lời gốc do máy nghe có thể viết sai, "
            "khi nhắc tới thì viết ĐÚNG như sau): " + ", ".join(terms[:80]))


# ---------- sửa lỗi chính tả máy nghe theo từ điển ----------

def _loose_syl(s: str) -> str:
    """Một âm tiết -> dạng lỏng: bỏ dấu, gộp phụ âm đầu hay bị nhầm (cùng luật với ingredients._loose)."""
    t = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = re.sub(r"[^a-z0-9]", "", t)
    t = re.sub(r"^gi", "d", t)
    t = re.sub(r"^[sx]", "x", t)
    t = re.sub(r"^[dr]", "d", t)
    t = re.sub(r"^(ch|tr)", "ch", t)
    return t


def _core(tok: str) -> tuple:
    """'(mấm,' -> ('(', 'mấm', ',')"""
    m = re.match(r"^(\W*)(.*?)(\W*)$", tok, re.UNICODE)
    return m.group(1), m.group(2), m.group(3)


def _terms_for_fix() -> list:
    """[(âm tiết đúng, dạng lỏng, số âm tiết được phép khác)] — từ người dùng ≥ 2 âm tiết (khác tối đa nửa số
    âm tiết, làm tròn lên), tên nguyên liệu ≥ 3 âm tiết (khác tối đa 1 âm tiết)."""
    out = []
    for t in user_terms():
        syl = t.split()
        if len(syl) >= 2:
            out.append((syl, [_loose_syl(x) for x in syl], math.ceil(len(syl) / 2)))
    for g in ingredients.GROUPS:
        for alias in g:
            syl = alias.split()
            if len(syl) >= 3:
                out.append((syl, [_loose_syl(x) for x in syl], 1))
    return sorted(out, key=lambda x: -len(x[0]))                 # cụm dài trước


def fix_text(text: str, terms: list | None = None) -> str:
    """Thay cụm nghe gần đúng bằng chính tả trong từ điển. Giữ dấu câu dính hai đầu và chữ hoa đầu câu."""
    if not text:
        return text
    terms = _terms_for_fix() if terms is None else terms
    if not terms:
        return text
    toks = text.split(" ")
    parts = [_core(t) for t in toks]
    low = [p[1].lower() for p in parts]
    loose = [_loose_syl(p[1]) for p in parts]
    done = [False] * len(toks)
    for syl, lsyl, max_diff in terms:
        n = len(syl)
        want = [x.lower() for x in syl]
        for i in range(len(toks) - n + 1):
            if any(done[i:i + n]) or loose[i:i + n] != lsyl:
                continue
            # dấu câu GIỮA cụm (vd "nước, mấm") nghĩa là hai ý khác nhau: không ghép
            if any(parts[j][2] for j in range(i, i + n - 1)) or any(parts[j][0] for j in range(i + 1, i + n)):
                continue
            diff = sum(1 for a, b in zip(low[i:i + n], want) if unicodedata.normalize("NFC", a) != unicodedata.normalize("NFC", b))
            if diff > max_diff:
                continue
            for k, j in enumerate(range(i, i + n)):          # diff == 0: đã đúng, chỉ đánh dấu cho cụm ngắn hơn khỏi đụng
                pre, core, post = parts[j]
                new = syl[k]
                if core[:1].isupper() and new[:1].islower():
                    new = new[:1].upper() + new[1:]
                toks[j] = pre + new + post
                done[j] = True
    return " ".join(toks)


def fix_segments(text: str, segs: list) -> tuple:
    """Áp fix_text cho cả bản lời liền và từng đoạn [(start, end, text)]."""
    terms = _terms_for_fix()
    return fix_text(text, terms), [(a, b, fix_text(t, terms)) for a, b, t in segs]
