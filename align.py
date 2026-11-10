"""Gán CHỮ của kịch bản (đúng chính tả, dấu câu) lên THỜI GIAN đo được (mốc từ của TTS/STT).

Một hàm dùng chung cho mọi đường (trước đây có 2 bản: bản ở tts_capcut ghép theo chỉ số khi lệch ≤ 2 từ
nên mọi từ sau chỗ lệch đều trượt mốc). Cách làm:
1. Số từ khớp -> dùng mốc từng từ.
2. Lệch -> căn bằng difflib trên từ đã bỏ dấu/viết thường; từ kịch bản khớp được lấy mốc của từ STT tương
   ứng, các từ không khớp được rải theo độ dài âm tiết trong khoảng giữa hai từ khớp kề nó.
3. Không có mốc nào -> rải theo độ dài âm tiết trong [start, end].
"""
import difflib
import re
import unicodedata


def norm(word: str) -> str:
    w = unicodedata.normalize("NFD", (word or "").lower()).replace("đ", "d")
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", w)


def _weight(w: str) -> float:
    return len(w) + (2.5 if w.endswith((",", ".", "!", "?")) else 0) + 1


def spread(words: list, a0: float, a1: float, tail: float = 0.92) -> list:
    """Rải từ theo độ dài (dấu câu được thêm thời gian ngắt hơi) trong [a0, a1]. Trả [(start, end, word)]."""
    if not words:
        return []
    weights = [_weight(w) for w in words]
    tot, t, out = sum(weights), a0, []
    for w, k in zip(words, weights):
        d = (a1 - a0) * k / tot
        out.append((t, t + d * tail, w))
        t += d
    return out


def map_script(script: str, timed: list, start: float, end: float) -> list:
    """script: lời kịch bản; timed: [(s, e, word)] đo được (có thể rỗng, lệch số từ, sai chính tả).
    Trả [(s, e, word_kịch_bản)] theo đúng thứ tự kịch bản, thời gian tăng dần."""
    words = (script or "").split()
    if not words:
        return []
    timed = [t for t in (timed or []) if t[1] >= t[0]]
    if len(timed) == len(words):
        return [(a, b, w) for (a, b, _), w in zip(timed, words)]
    if not timed:
        return spread(words, start, end)
    a0, a1 = timed[0][0], timed[-1][1]
    if a1 - a0 < 0.3:
        return spread(words, start, end)
    sm = difflib.SequenceMatcher(None, [norm(w) for w in words], [norm(t[2]) for t in timed], autojunk=False)
    times: list = [None] * len(words)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            ta, tb, _ = timed[blk.b + k]
            times[blk.a + k] = (ta, tb)
    # điền các đoạn từ không khớp bằng cách rải trong khe giữa hai từ đã có mốc
    out, i = [], 0
    while i < len(words):
        if times[i] is not None:
            out.append((times[i][0], times[i][1], words[i]))
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        lo = out[-1][1] if out else a0
        hi = times[j][0] if j < len(words) else a1
        if hi - lo < 0.05 * (j - i):            # khe quá hẹp: nới ra một chút sau từ trước
            hi = lo + 0.12 * (j - i)
        out += spread(words[i:j], lo, hi)
        i = j
    # bảo đảm tăng dần (STT đôi khi trả mốc chồng nhau)
    fixed, prev = [], 0.0
    for a, b, w in out:
        a = max(a, prev)
        b = max(b, a + 0.05)
        fixed.append((round(a, 3), round(b, 3), w))
        prev = a
    return fixed
