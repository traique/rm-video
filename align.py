"""Gán CHỮ của kịch bản (đúng chính tả, dấu câu) lên THỜI GIAN đo được (mốc từ của TTS/STT).

Một hàm dùng chung cho mọi đường (trước đây có 2 bản: bản ở tts_capcut ghép theo chỉ số khi lệch ≤ 2 từ
nên mọi từ sau chỗ lệch đều trượt mốc). Cách làm:
1. Số từ khớp -> dùng mốc từng từ.
2. Lệch -> căn bằng difflib trên từ đã bỏ dấu/viết thường; từ kịch bản khớp được lấy mốc của từ STT tương
   ứng, các từ không khớp được rải theo độ dài âm tiết trong khoảng giữa hai từ khớp kề nó.
3. Không có mốc nào -> rải theo độ dài âm tiết trong [start, end].

Giọng không có mốc từng từ (CapCut, Piper): spread_voiced() rải chữ CHỈ trên các khoảng có tiếng của chính
giọng đã tạo (đo năng lượng), mỗi cụm giữa hai dấu câu ứng với một khúc giọng giữa hai chỗ ngắt hơi, âm tiết
cuối cụm được dành thêm thời gian (giọng đọc kéo dài âm cuối câu). Đo trên 7 câu edge-tts có mốc thật (105 từ):
lệch đầu từ trung bình 271ms -> 69ms, 90% số từ lệch dưới 124ms (cách cũ 551ms); STT CapCut nghe lại cũng chỉ
được 85ms / 164ms và mất 7-11 giây mỗi lần gọi mạng.
"""
import difflib
import re
import unicodedata

import numpy as np


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


# ---------- rải chữ theo khoảng CÓ TIẾNG của giọng đã tạo ----------

PUNCT = (",", ".", "!", "?", ";", ":", "…")
PAUSE = 0.15              # khoảng lặng ≥ 0.15s là chỗ ngắt hơi giữa hai cụm
FINAL_BONUS = 3.0         # âm tiết cuối cụm dài hơn (đo: lệch trung bình 107ms -> 69ms)


def voiced_intervals(x: "np.ndarray", sr: int, hop: float = 0.01, rel_db: float = 30.0,
                     gap_fill: float = 0.06, min_on: float = 0.04) -> list:
    """Khoảng có tiếng [(đầu, cuối)] (giây, tính từ đầu mảng x) của một đoạn giọng mono float.
    Ngưỡng: thấp hơn mức to (phân vị 95) 30 dB; khe < 60ms giữa hai âm tiết được lấp; tiếng lách tách < 40ms bỏ."""
    n = max(1, int(sr * hop))
    m = len(x) // n
    if m < 3:
        return []
    rms = np.sqrt((x[:m * n].reshape(m, n).astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    top = float(np.percentile(db, 95))
    if top < -60:                                   # gần như câm
        return []
    on = db > top - rel_db
    raw, i = [], 0
    while i < m:
        if on[i]:
            j = i
            while j < m and on[j]:
                j += 1
            raw.append([i * hop, j * hop])
            i = j
        else:
            i += 1
    merged = []
    for a, b in raw:
        if merged and a - merged[-1][1] < gap_fill:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_on]


def _phrases(words: list) -> list:
    out = [[]]
    for w in words:
        out[-1].append(w)
        if w.endswith(PUNCT):
            out.append([])
    return [p for p in out if p]


def _warp(phrases: list, ivs: list) -> list:
    """Rải chữ của các cụm trên tổng thời gian CÓ TIẾNG của ivs (bỏ qua khoảng lặng) rồi đổi về thời gian thật.
    Trọng số mỗi âm tiết = số chữ cái + 1, âm tiết cuối mỗi cụm cộng FINAL_BONUS."""
    words = [w for p in phrases for w in p]
    last, k = set(), -1
    for p in phrases:
        k += len(p)
        last.add(k)
    tot = sum(b - a for a, b in ivs)
    weights = [len(norm(w)) + 1 + (FINAL_BONUS if i in last else 0) for i, w in enumerate(words)]
    wsum = sum(weights) or 1

    def at(v):
        for a, b in ivs:
            if v <= b - a + 1e-9:
                return a + v
            v -= b - a
        return ivs[-1][1]
    out, acc = [], 0.0
    for w, k in zip(words, weights):
        s = acc / wsum * tot
        acc += k
        out.append((at(s + 1e-6), at(acc / wsum * tot), w))
    return out


def spread_voiced(words: list, ivs: list, a0: float, a1: float) -> list:
    """words rải theo khoảng có tiếng ivs (thời gian tuyệt đối) trong [a0, a1]. Số cụm (theo dấu câu) bằng số
    khúc giọng (theo chỗ ngắt ≥ 0.15s) thì mỗi cụm vào đúng khúc của nó; lệch thì rải chung trên mọi khoảng có
    tiếng. Không đo được tiếng nào thì rải đều như spread()."""
    if not words:
        return []
    ivs = [(max(a, a0), min(b, a1)) for a, b in ivs if b > a0 and a < a1]
    ivs = [(a, b) for a, b in ivs if b - a > 0.02]
    if not ivs or sum(b - a for a, b in ivs) < 0.1 * len(words):
        return spread(words, a0, a1)
    groups = []
    for a, b in ivs:
        if groups and a - groups[-1][-1][1] < PAUSE:
            groups[-1].append((a, b))
        else:
            groups.append([(a, b)])
    ph = _phrases(words)
    if len(ph) == len(groups):
        out = [x for p, g in zip(ph, groups) for x in _warp([p], g)]
    else:
        out = _warp(ph, ivs)
    return [(round(a, 3), round(max(b, a + 0.05), 3), w) for a, b, w in out]
