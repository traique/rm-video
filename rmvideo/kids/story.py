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
# Đo thật 18-19/11/2026 (Qwen3.6-35B-A3B): ép từ tiếng Anh vào lời thoại thì truyện rời rạc, từ bị hô trống không; giờ
# model viết truyện THUẦN TIẾNG VIỆT cho bé 3-6 tuổi (câu ngắn, tiếng động, điệp khúc ba lần) và chỉ cần nhắc tới đồ vật /
# màu / con vật của hôm nay; mã chèn «Trái banh, tiếng Anh là ball. Bé nói theo nha: ball!» ngay cảnh đồ vật đó xuất hiện
# (cách các kênh dạy tiếng Anh cho bé làm), thẻ nghĩa + câu ôn từ + màn kết nhắc lại.
WORDS_MIN, WORDS_MAX = 130, 200        # chữ model viết (chưa kể ~12 chữ/từ mã chèn + câu ôn từ): đo 129 chữ -> 62 giây
WORDS_HARD = 95                        # dưới mức này thì không chấp nhận dù đã hết lượt
EN_ALSO_VI = {"run"}                   # chữ tiếng Anh trong kho trùng chữ tiếng Việt («run rẩy»): không coi là chen tiếng Anh
SCENE_MIN, SCENE_MAX = 10, 36          # chữ mỗi cảnh
QUESTIONS_MAX = 2                      # câu hỏi tối đa một cảnh (đo thật: model hỏi dồn 5 câu cho đủ chữ)
RECENT_DAYS = 30                       # không lặp bộ ba đề / bộ từ trong chừng này ngày
N_WORDS = (3, 3, 3, 4)                 # số từ tiếng Anh mỗi truyện: bé nhỏ nên 3 là vừa
TITLE_MAX = 70
SAD = ("buồn", "khóc", "giận", "sợ", "một mình", "bỏ đi")   # cảnh kết KHÔNG được còn những chữ này (đo thật 19/11: đòi phải có
                                                               # «vui/cười» thì «tặng hoa cho Mèo Cam», «cùng ăn trái cây» bị trượt)
# lời chào không bắt truyện phải nhắc tới (ép thì ra «Chào buổi sáng ngon ghê», đo thật 19/11): câu dạy từ đặt ở cảnh hợp
GREETING_SCENE = {"hello": 0, "good morning": 0, "bye bye": 7, "good night": 7}
BANNED = ("chết", "máu", "giết", "đánh nhau", "ma quỷ", "con ma", "súng", "con dao", "đâm", "ngu ngốc", "đồ ngốc",
          "ghét", "đồ ngu", "xấu xí")
# 8 nhịp của một truyện mầm non (học từ truyện kể cho bé 3-5 tuổi: mở bằng lời chào, một chuyện nhỏ, thử ba lần với một câu
# điệp khúc, bạn giúp, bài học nói nhẹ qua lời nhân vật, kết nhắn nhủ các bé)
SCENE_ROLES = (
    "chào các bé, giới thiệu nhân vật chính đang ở đâu, làm gì (vui vẻ)",
    "bạn ấy muốn làm một việc nhỏ (chơi, giúp, làm quà...) và bắt đầu làm",
    "chuyện xảy ra: có tiếng động (Bịch! Ối! Rầm!), có vấn đề",
    "thử cách thứ nhất — chưa được; nói câu điệp khúc lần 1",
    "thử cách thứ hai — vẫn chưa được, hơi buồn; câu điệp khúc lần 2",
    "một bạn tới giúp hoặc gợi ý, thử lần ba — được rồi! câu điệp khúc lần 3 (đổi kết vui)",
    "bài học: một bạn nói nhẹ nhàng một câu, bạn chính gật đầu",
    "kết vui: các bạn cùng chơi/cười; câu cuối nhắn nhủ «các bé» (hỏi bé hoặc dặn bé một câu)",
)


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

    def unused(cands: Sequence[str]) -> list[str]:
        return [w for w in bank if w in cands and (w not in last_use or last_use[w] < cutoff)]
    # 1-2 từ là đồ vật/con vật CÓ MẶT ở bối cảnh (truyện nhắc tới tự nhiên), 1 từ gắn với bài học (lời chào/cảm xúc/hành
    # động), còn lại từ kho theo lần dùng gần nhất
    words = unused(by_id(data["settings"], setting_id).get("words", []))[:2 if n >= 4 else 1]
    words += [w for w in unused(by_id(data["lessons"], lesson_id).get("words", [])) if w not in words][:1]
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
    return {"title": sentence_case(south_kids(re.sub(r"\s+", " ", str(story.get("title") or "")).strip().strip('"«»')[:TITLE_MAX]),
                                   chars),
            "scenes": scenes,
            "description": south_kids(re.sub(r"[ \t]+", " ", str(story.get("description") or "")).strip()[:1500]),
            "hashtags": tags[:4]}


def sentence_case(title: str, chars: dict) -> str:
    """Tiêu đề model hay Viết Hoa Mỗi Chữ (đo thật 19/11) -> chỉ hoa chữ đầu và tên nhân vật."""
    words = title.split()
    if len(words) < 4 or sum(1 for w in words if w[:1].isupper()) < 0.7 * len(words):
        return title
    keep = {p.lower() for c in chars.values() for p in c["name_vi"].split()}
    out = [w if w.lower() in keep else w.lower() for w in words]
    out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def _ascii(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def validate(story: dict, brief: dict, data: dict, example_sentences: Sequence[str] = ()) -> list[str]:
    """Danh sách lỗi (rỗng = đạt) để model viết lại. story đã qua normalize(). example_sentences: câu của truyện mẫu
    trong lời nhắc, chép lại ≥ 2 câu là lỗi (model hay chép mẫu y nguyên)."""
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
        if not is_greeting(w, data) and not vi_mentioned(full, w, data):
            problems.append(f"truyện phải nhắc tới «{w['vi']}» (đồ vật / màu / con vật / việc bé học hôm nay), kể tự nhiên")
    foreign = [w["en"] for w in data["words"] if w["en"] not in EN_ALSO_VI
               and count_word(full + " " + story.get("title", ""), w["en"])]
    if foreign:
        problems.append("truyện và tiêu đề viết THUẦN tiếng Việt, không chen tiếng Anh (" + ", ".join(foreign[:5])
                        + "): phần tiếng Anh mã sẽ tự thêm")
    main = data["characters"].get(brief.get("main", ""), {})
    if main and main["name_vi"].lower() not in low:
        problems.append(f"thiếu nhân vật chính {main['name_vi']}")
    if sum(1 for c in data["characters"].values() if c["name_vi"].lower() in low) < 2:
        problems.append("cần ít nhất một bạn nữa ngoài nhân vật chính (Gấu Nâu, Mèo Cam, Sóc Nhỏ...)")
    if texts and not re.search(r"[?!]", texts[0]):
        problems.append("cảnh 1 phải có một câu hỏi hoặc câu cảm để gây tò mò")
    lesson = by_id(data["lessons"], brief.get("lesson", ""))
    if lesson and not any(k in low for k in lesson.get("keywords", [])):
        problems.append(f"cảnh 7 phải nói rõ bài học «{lesson['vi']}» (dùng chữ: {', '.join(lesson['keywords'])})")
    if len(texts) == N_SCENES and any(h in texts[-1].lower() for h in SAD):
        problems.append("cảnh 8 phải kết thúc vui, không còn buồn / khóc / giận / sợ")
    bad = [b for b in BANNED if b in low]
    if bad:
        problems.append("không dùng từ: " + ", ".join(bad))
    title = story.get("title", "")
    if not 6 <= len(title) <= TITLE_MAX:
        problems.append(f"tiêu đề {len(title)} ký tự, cần 6-{TITLE_MAX} ký tự")
    for i, s in enumerate(scenes, 1):
        act = s.get("action_en") or ""
        if len(act.split()) < 3:
            problems.append(f"cảnh {i} thiếu action_en (một câu tiếng Anh tả hành động trong hình)")
        elif not is_english(act):            # đo thật 18/11: model viết action_en bằng tiếng Việt -> ảnh không có hành động
            problems.append(f"cảnh {i}: action_en phải viết bằng TIẾNG ANH (English only), đang là «{act[:40]}»")
    for i, t in enumerate(texts, 1):
        if t.count("?") > QUESTIONS_MAX:
            problems.append(f"cảnh {i} hỏi dồn dập ({t.count('?')} câu hỏi): kể bằng hành động và lời thoại, tối đa "
                            f"{QUESTIONS_MAX} câu hỏi một cảnh")
    if example_sentences:
        copied = [x for x in example_sentences if x.lower() in low]
        if len(copied) >= 2:
            problems.append("không chép truyện mẫu (trùng câu: " + "; ".join(copied[:2]) + "): viết truyện mới theo đề")
    return problems


def is_greeting(w: dict, data: dict) -> bool:
    return word_info(data, w["en"]).get("group") == "greeting"


def must_mention(brief: dict, data: dict) -> list[dict]:
    """Từ mà truyện phải nhắc tới tiếng Việt (đồ vật / màu / con vật / hành động); lời chào thì không."""
    return [w for w in brief.get("words", []) if not is_greeting(w, data)]


def vi_forms(w: dict, data: dict) -> list[str]:
    """Các cách viết tiếng Việt tính là «có nhắc tới» từ đó: vi, các «match» trong kho, và chữ cuối của vi («trái banh» ->
    «banh»)."""
    info = word_info(data, w["en"])
    forms = [w.get("vi") or info.get("vi") or ""] + list(info.get("match") or [])
    vi = forms[0]
    if " " in vi and not info.get("match"):
        forms.append(vi.split()[-1])
    return [f for f in dict.fromkeys(x.strip().lower() for x in forms) if f]


def vi_mentioned(text: str, w: dict, data: dict) -> bool:
    low = (text or "").lower()
    return any(re.search(r"(?<![\wÀ-ỹ])" + re.escape(f) + r"(?![\wÀ-ỹ])", low) for f in vi_forms(w, data))


def first_scene_with(scenes: Sequence[dict], w: dict, data: dict) -> int:
    return next((i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), w, data)), -1)


def is_english(text: str) -> bool:
    """action_en có phải tiếng Anh (toàn ký tự ASCII, không dấu tiếng Việt)."""
    return bool(text.strip()) and all(ord(c) < 128 for c in text)


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


def beat_text(w: dict) -> str:
    """Câu dạy từ (mã viết, người kể đọc): «Trái banh, tiếng Anh là ball. Bé nói theo nha: ball!»"""
    vi = (w.get("vi") or "").strip()
    return f"{vi[:1].upper()}{vi[1:]}, tiếng Anh là {w['en']}. Bé nói theo nha: {w['en']}!"


def add_beats(story: dict, brief: dict, data: dict) -> list[tuple[str, int]]:
    """Chèn câu dạy từ vào cuối cảnh ĐẦU TIÊN nhắc tới đồ vật đó (không cảnh nào nhắc thì cảnh 2/3/4 kèm câu dẫn). Mỗi cảnh
    tối đa 2 câu dạy để không nặng. Trả [(en, chỉ số cảnh)]."""
    scenes = story.get("scenes") or []
    if len(scenes) < N_SCENES:
        return []
    out: list[tuple[str, int]] = []
    load_count = [0] * len(scenes)

    def spread(cands: list[int]) -> int:              # ưu tiên cảnh chưa có câu dạy nào (đo thật: 3 câu dồn vào cảnh 1)
        return next((j for j in cands if load_count[j] == 0), next((j for j in cands if load_count[j] < 2), cands[0]))
    for k, w in enumerate(brief.get("words", [])):
        mention = [i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), w, data)]
        lead = ""
        if mention:
            i = spread(mention)
        elif is_greeting(w, data):                    # lời chào: cảnh mở đầu / cảnh kết, không cần câu dẫn
            g = GREETING_SCENE.get(w["en"], 6)
            i = spread([g, g + 1 if g < 7 else g - 1])
        else:
            i = spread([1 + k % 3, 2, 3, 4, 5, 1])
            lead = f"Ở đây có {w['vi']} nè. "
        scenes[i]["text"] = (scenes[i]["text"].rstrip() + " " + lead + beat_text(w)).strip()
        load_count[i] += 1
        out.append((w["en"], i))
    return out


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
