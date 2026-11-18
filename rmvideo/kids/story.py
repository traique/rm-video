"""Dữ liệu và logic THUẦN của kênh truyện bé (chạy cả Botkeep lẫn Kaggle, không đọc cấu hình, không gọi mạng).

- data.json: thế giới, 4 nhân vật (character bible), bài học, bối cảnh, xung đột, kho từ tiếng Anh (bạn duyệt một lần).
- pick(): Botkeep chọn ĐỀ bằng mã (bài học + bối cảnh + xung đột + 3-5 từ + nhân vật chính) tránh những gì đã dùng
  30 ngày — model chỉ việc viết truyện theo đề, không còn vòng «sinh ra rồi thấy trùng thì bỏ».
- validate(): kiểm kịch bản model trả về bằng mã (số chữ, 8 cảnh, từ tiếng Anh ≥ 3 lần, đủ 4 nhân vật, có hook, có bài
  học, kết vui, không từ cấm) -> danh sách lỗi để model viết lại.
- south_kids(): từ miền Bắc -> miền Nam như kênh bếp (rmvideo/domain/dialect.py) + vài từ trẻ em.
"""
from __future__ import annotations

import functools
import json
import os
import random
import re
import time
from collections import Counter
from collections.abc import Sequence

from rmvideo.domain import dialect

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
N_SCENES = 8
# Đo thật 18/11/2026 (Qwen3.6-35B-A3B, grammar JSON): model viết NGẮN hơn yêu cầu (74 -> 146 -> 176 chữ qua 3 lượt) và hay bỏ
# sót từ tiếng Anh -> ngưỡng cứng thấp hơn, schema ép độ dài từng cảnh, từ thiếu chèn bằng mã, câu ôn từ cuối truyện do mã viết.
WORDS_MIN, WORDS_MAX = 160, 240        # chữ cả truyện: 160 chữ Thục Đoan ~47s + đệm + ôn từ + màn kết ≈ 65s; đo thật
                                       # 18/11: 236 chữ -> 88,9s (render.plan tự co đệm/đọc nhanh để ≤ 87s)
WORDS_HARD = 130                       # dưới mức này thì không chấp nhận dù đã hết lượt
SCENE_MIN, SCENE_MAX = 12, 40          # chữ mỗi cảnh
EN_REPEAT = 2                          # mỗi từ tiếng Anh nên xuất hiện ít nhất chừng này lần trong truyện (ôn từ cuối là lần nữa)
RECENT_DAYS = 30                       # không lặp bộ ba đề / bộ từ trong chừng này ngày
N_WORDS = (3, 4, 4, 5)                 # số từ tiếng Anh mỗi truyện (hay gặp nhất: 4)
TITLE_MAX = 70
HAPPY = ("vui", "cười", "hạnh phúc", "cùng nhau", "ôm", "mừng", "thích")
BANNED = ("chết", "máu", "giết", "đánh nhau", "ma quỷ", "con ma", "súng", "con dao", "đâm", "ngu ngốc", "đồ ngốc",
          "ghét", "đồ ngu", "xấu xí")
# ORDER: nội dung từng cảnh (số thứ tự cố định theo ý tưởng): hook -> vấn đề -> leo thang -> xung đột -> đỉnh -> giải
# quyết -> bài học -> kết vui
SCENE_ROLES = ("mở đầu gây tò mò", "giới thiệu vấn đề", "vấn đề lớn dần", "xung đột", "đỉnh điểm", "cách giải quyết",
               "bài học", "kết vui")


@functools.lru_cache(maxsize=1)
def load() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def by_id(items: Sequence[dict], key: str) -> dict:
    return next((x for x in items if x.get("id") == key), {})


def word_info(data: dict, en: str) -> dict:
    return next((w for w in data["words"] if w["en"] == en), {"en": en, "vi": ""})


# ---------------- chọn đề ----------------

def _created(s: dict) -> float:
    return float(s.get("created_at") or 0)


def recent(stories: Sequence[dict], now: float | None = None, days: int = RECENT_DAYS) -> list[dict]:
    """Các truyện đã làm trong `days` ngày gần nhất (bản ghi {created_at, lesson, setting, conflict, words, main})."""
    now = now or time.time()
    return [s for s in stories if now - float(s.get("created_at") or 0) <= days * 86400]


def pick(data: dict, stories: Sequence[dict], rnd: random.Random | None = None, now: float | None = None) -> dict:
    """Đề cho một truyện mới: bài học ít dùng nhất, bộ ba (bài học, bối cảnh, xung đột) chưa dùng 30 ngày, bối cảnh và
    xung đột khác vài truyện gần nhất, 3-5 từ tiếng Anh (ưu tiên từ gắn với bài học) chưa dùng 30 ngày, nhân vật chính
    luân phiên. Trả {"lesson", "setting", "conflict", "words": [{"en", "vi"}], "main", "seed"}."""
    rnd = rnd or random.Random()
    rec = recent(stories, now)
    used = {(s.get("lesson"), s.get("setting"), s.get("conflict")) for s in rec}
    last = sorted(stories, key=_created)[-5:]
    count = Counter(s.get("lesson") for s in rec)
    lessons = sorted(data["lessons"], key=lambda ls: (count.get(ls["id"], 0), rnd.random()))
    settings = [s["id"] for s in data["settings"]]
    conflicts = [c["id"] for c in data["conflicts"]]
    chosen = None
    for ls in lessons:
        cands = [(st, cf) for st in settings for cf in conflicts if (ls["id"], st, cf) not in used]
        if not cands:
            continue
        fresh = [(st, cf) for st, cf in cands if cf not in {x.get("conflict") for x in last}
                 and st not in {x.get("setting") for x in last[-3:]}]
        chosen = (ls["id"], *rnd.choice(fresh or cands))
        break
    if chosen is None:                                  # 800 bộ ba đều đã dùng trong 30 ngày (không thể với 2 video/ngày)
        chosen = (lessons[0]["id"], rnd.choice(settings), rnd.choice(conflicts))
    lesson_id, setting_id, conflict_id = chosen
    # từ tiếng Anh: kho ~90 từ, 2 truyện/ngày x 4 từ nên không thể «chưa dùng 30 ngày» mãi -> xếp theo lần dùng gần nhất
    # (chưa dùng bao giờ đứng đầu, rồi lâu nhất), 2-3 từ gắn với bài học (không nằm trong 6 truyện vừa rồi) + phần còn lại
    # từ kho; cùng hạng thì ngẫu nhiên. Luôn đủ n từ.
    ordered = sorted(stories, key=_created)
    last_use: dict[str, int] = {}
    for k, s in enumerate(ordered):
        for w in s.get("words") or []:
            last_use[w] = k
    n = rnd.choice(N_WORDS)
    bank = [w["en"] for w in data["words"]]
    rnd.shuffle(bank)
    bank.sort(key=lambda w: last_use.get(w, -1))
    cutoff = len(ordered) - 6                                  # không nằm trong 6 truyện vừa rồi (chưa dùng = luôn được)
    lesson_words = by_id(data["lessons"], lesson_id).get("words", [])
    linked = [w for w in bank if w in lesson_words and (w not in last_use or last_use[w] < cutoff)]
    words = linked[:min(3, n)]
    words += [w for w in bank if w not in words][:n - len(words)]
    mains = list(data["characters"])
    seen = [str(s.get("main") or "") for s in sorted(stories, key=_created)]

    def last_main(m: str) -> int:                       # lần gần nhất làm nhân vật chính (-1 = chưa bao giờ)
        return (len(seen) - seen[::-1].index(m)) if m in seen else -1
    main = min(mains, key=last_main)
    return {"lesson": lesson_id, "setting": setting_id, "conflict": conflict_id,
            "words": [{"en": w, "vi": word_info(data, w)["vi"]} for w in words],
            "main": main, "seed": rnd.randrange(1, 2 ** 31)}


def describe(brief: dict, data: dict) -> str:
    """Một dòng cho người đọc: «Thỏ Trắng · xin lỗi · trong vườn · làm vỡ bình hoa · sorry, friend, help»."""
    ch = data["characters"].get(brief.get("main", ""), {})
    return " · ".join(x for x in (ch.get("name_vi", ""), by_id(data["lessons"], brief.get("lesson", "")).get("vi", ""),
                                  by_id(data["settings"], brief.get("setting", "")).get("vi", ""),
                                  by_id(data["conflicts"], brief.get("conflict", "")).get("vi", ""),
                                  ", ".join(w["en"] for w in brief.get("words", []))) if x)


# ---------------- giọng miền Nam cho trẻ em ----------------

_KID_RULES = [
    (r"\bbố\b", "ba"),
    (r"quả bóng|trái bóng|cái bóng", "trái banh"),
    (r"(cái|chiếc) mũ", r"\1 nón"),
    (r"(cái|chiếc) ô(?![\wÀ-ỹ])", "cây dù"),
    (r"(đôi|chiếc) tất", "đôi vớ"),
    (r"bị ngã|ngã xuống|ngã nhào", "bị té"),
    (r"xem (phim|ti ?vi|sách|tranh)", r"coi \1"),
    (r"xấu hổ|ngượng ngùng", "mắc cỡ"),
    (r"\bạ\b", "nha"),                       # «cảm ơn ạ» -> «cảm ơn nha»
]
_KID_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _KID_RULES]


def south_kids(text: str) -> str:
    """Lời model viết -> giọng miền Nam: luật chung của kênh bếp (chén, muỗng, trái, heo...) + vài từ trẻ em."""
    out = dialect.south(text or "")
    for rx, rep in _KID_COMPILED:
        def one(m: re.Match, rep: str = rep) -> str:
            return _keep_case(m.group(0), m.expand(rep))
        out = rx.sub(one, out)
    return out


def _keep_case(src: str, new: str) -> str:
    return new[:1].upper() + new[1:] if src[:1].isupper() else new


# ---------------- kiểm kịch bản bằng mã ----------------

def word_count(text: str) -> int:
    return len((text or "").split())


def count_word(text: str, en: str) -> int:
    """Số lần từ tiếng Anh (có thể 2 chữ: «thank you») xuất hiện, không phân biệt hoa thường, đúng ranh giới từ."""
    pat = r"(?<![A-Za-z])" + r"\s+".join(re.escape(p) for p in en.split()) + r"(?![A-Za-z])"
    return len(re.findall(pat, text or "", re.IGNORECASE))


def normalize(story: dict, data: dict) -> dict:
    """Chuẩn hoá bản model trả: cắt khoảng trắng, giọng miền Nam, nhân vật mỗi cảnh là 1-2 khoá hợp lệ (không hợp lệ thì
    suy từ tên trong lời), hashtag là danh sách chữ không dấu bắt đầu bằng #."""
    chars = data["characters"]
    scenes = []
    for sc in (story.get("scenes") or [])[:N_SCENES]:
        text = south_kids(re.sub(r"\s+", " ", str(sc.get("text") or "")).strip())
        keys = [k for k in (sc.get("characters") or []) if k in chars]
        if not keys:
            keys = [k for k, c in chars.items() if c["name_vi"].lower() in text.lower()]
        scenes.append({"text": text, "characters": keys[:2] or [next(iter(chars))],
                       "action_en": re.sub(r"\s+", " ", str(sc.get("action_en") or "")).strip()[:300]})
    tags = []
    for t in (story.get("hashtags") or []):
        t = "#" + re.sub(r"[^0-9a-z]", "", _ascii(str(t)).lower().lstrip("#"))
        if len(t) > 2 and t not in tags:
            tags.append(t)
    return {"title": south_kids(re.sub(r"\s+", " ", str(story.get("title") or "")).strip().strip('"«»')[:TITLE_MAX]),
            "scenes": scenes,
            "description": south_kids(re.sub(r"[ \t]+", " ", str(story.get("description") or "")).strip()[:1500]),
            "hashtags": tags[:4]}


def _ascii(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def validate(story: dict, brief: dict, data: dict) -> list[str]:
    """Danh sách lỗi (rỗng = đạt) để model viết lại. story đã qua normalize()."""
    problems = []
    scenes = story.get("scenes") or []
    texts = [s.get("text", "") for s in scenes]
    if len(scenes) != N_SCENES:
        problems.append(f"cần đúng {N_SCENES} cảnh, nhận được {len(scenes)}")
    total = sum(word_count(t) for t in texts)
    if total < WORDS_MIN:
        problems.append(f"cả truyện {total} chữ, cần từ {WORDS_MIN} chữ (viết mỗi cảnh dài hơn)")
    elif total > WORDS_MAX:
        problems.append(f"cả truyện {total} chữ, tối đa {WORDS_MAX} chữ (viết mỗi cảnh ngắn lại)")
    for i, t in enumerate(texts, 1):
        n = word_count(t)
        if n < SCENE_MIN or n > SCENE_MAX:
            problems.append(f"cảnh {i} có {n} chữ, cần {SCENE_MIN}-{SCENE_MAX} chữ")
    full = " ".join(texts)
    low = full.lower()
    for w in brief.get("words", []):
        n = count_word(full, w["en"])
        if n < EN_REPEAT:
            problems.append(f"từ tiếng Anh «{w['en']}» chỉ xuất hiện {n} lần, cần ít nhất {EN_REPEAT} lần")
    for c in data["characters"].values():
        if c["name_vi"].lower() not in low:
            problems.append(f"thiếu nhân vật {c['name_vi']} (cả bốn bạn đều phải được nhắc tên)")
    if texts and not re.search(r"[?!]", texts[0]):
        problems.append("cảnh 1 phải có một câu hỏi hoặc câu cảm để gây tò mò")
    lesson = by_id(data["lessons"], brief.get("lesson", ""))
    if lesson and not any(k in low for k in lesson.get("keywords", [])):
        problems.append(f"cảnh 7 phải nói rõ bài học «{lesson['vi']}» (dùng chữ: {', '.join(lesson['keywords'])})")
    if len(texts) == N_SCENES and not any(h in texts[-1].lower() for h in HAPPY):
        problems.append("cảnh 8 phải kết thúc vui (các bạn vui, cười, ôm nhau, chơi cùng nhau)")
    bad = [b for b in BANNED if b in low]
    if bad:
        problems.append("không dùng từ: " + ", ".join(bad))
    title = story.get("title", "")
    if not 6 <= len(title) <= TITLE_MAX:
        problems.append(f"tiêu đề {len(title)} ký tự, cần 6-{TITLE_MAX} ký tự")
    for i, s in enumerate(scenes, 1):
        if len((s.get("action_en") or "").split()) < 3:
            problems.append(f"cảnh {i} thiếu action_en (một câu tiếng Anh tả hành động trong hình)")
    return problems


def blocking(story: dict, brief: dict, data: dict) -> list[str]:
    """Lỗi CHẶN (không chấp nhận bản này dù đã hết lượt viết lại): thiếu/thừa cảnh, từ cấm, quá ngắn, cảnh không có
    action_en. Lỗi khác trong validate() là lỗi mềm: hết lượt thì chấp nhận bản ít lỗi nhất kèm ghi chú."""
    scenes = story.get("scenes") or []
    texts = [s.get("text", "") for s in scenes]
    out = []
    if len(scenes) != N_SCENES:
        out.append(f"cần đúng {N_SCENES} cảnh, nhận được {len(scenes)}")
    total = sum(word_count(t) for t in texts)
    if total < WORDS_HARD:
        out.append(f"cả truyện {total} chữ, quá ngắn (dưới {WORDS_HARD})")
    low = " ".join(texts).lower()
    bad = [b for b in BANNED if b in low]
    if bad:
        out.append("không dùng từ: " + ", ".join(bad))
    if not story.get("title"):
        out.append("thiếu tiêu đề")
    if any(len((s.get("action_en") or "").split()) < 3 for s in scenes):
        out.append("có cảnh thiếu action_en")
    return out


def inject_words(story: dict, brief: dict, data: dict) -> list[str]:
    """Từ tiếng Anh model bỏ sót hẳn (0 lần): chèn một câu thoại của nhân vật chính vào cảnh 6 (giải quyết) hoặc 7 (bài
    học) để bé vẫn nghe từ đó trong truyện. Trả các từ đã chèn."""
    scenes = story.get("scenes") or []
    if len(scenes) < N_SCENES:
        return []
    main = data["characters"].get(brief.get("main", ""), {}).get("name_vi", "Thỏ Trắng")
    full = " ".join(s.get("text", "") for s in scenes)
    added = []
    for k, w in enumerate(brief.get("words", [])):
        if count_word(full, w["en"]) == 0:
            sc = scenes[5 if k % 2 == 0 else 6]
            sc["text"] = (sc["text"].rstrip() + f" {main} nói: «{w['en']}»! {w['en']} nghĩa là {w['vi']} đó nha.").strip()
            added.append(w["en"])
    return added


def recap_text(brief: dict) -> str:
    """Câu ôn từ cuối truyện (mã viết, đọc trong màn kết): «Hôm nay mình học: turn là lượt, wait là chờ... Bé nhớ nha!»"""
    ws = brief.get("words", [])
    if not ws:
        return ""
    return "Hôm nay mình học: " + ", ".join(f"{w['en']} là {w['vi']}" for w in ws) + ". Bé nhớ nha!"


def script_text(story: dict) -> str:
    return "\n".join(s.get("text", "") for s in story.get("scenes") or [])


def english_tokens(brief: dict) -> set[str]:
    """Các chữ (viết thường) của từ tiếng Anh trong đề, để tô màu phụ đề («thank you» -> {thank, you})."""
    return {p.lower() for w in brief.get("words", []) for p in w["en"].split()}
