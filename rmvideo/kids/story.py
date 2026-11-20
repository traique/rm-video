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
# màu / con vật của hôm nay; mã chèn «À, trái banh đó tiếng Anh là ball. Bé nói theo nè: ball!» ngay cảnh đồ vật đó xuất hiện
# (cách các kênh dạy tiếng Anh cho bé làm), thẻ nghĩa + câu ôn từ + màn kết nhắc lại.
WORDS_MIN, WORDS_MAX = 130, 200       # đo thật 19/11: 221 chữ + 3 câu dạy -> 91,6 s dù đã đọc nhanh 1,15x (QA 88 s)        # chữ model viết (chưa kể ~12 chữ/từ mã chèn + câu ôn từ): đo 129 chữ -> 62 giây
WORDS_HARD = 95
WORDS_BLOCK = WORDS_MAX + 15           # dài hơn mức này thì CHẶN (video không vừa 88 giây dù đọc nhanh)                        # dưới mức này thì không chấp nhận dù đã hết lượt
EN_ALSO_VI = {"run"}                   # chữ tiếng Anh trong kho trùng chữ tiếng Việt («run rẩy»): không coi là chen tiếng Anh
SCENE_MIN, SCENE_MAX = 10, 38          # chữ mỗi cảnh
QUESTIONS_MAX = 2                      # câu hỏi tối đa một cảnh (đo thật: model hỏi dồn 5 câu cho đủ chữ)
HOOK_WORDS = (4, 14)                   # câu móc 3 giây đầu: 4-12 chữ (đo thật: 15 chữ -> móc kéo dài 4,3 giây)
TAIL_WORDS = ("nha", "hen", "nhé", "nghen")   # tiểu từ cuối câu: dùng ít thôi (phản hồi 19/11: «quá nhiều nha ở cuối câu»)
TAIL_MAX = 3                           # cả truyện + câu móc tối đa chừng này tiểu từ
REFRAIN_MAX = 3                        # câu điệp khúc lặp đúng 3; câu khác không được lặp
SPECIES = {"rabbit", "bear", "cat", "squirrel"}       # tên loài của 4 bạn: truyện nào cũng nhắc, chỉ dạy khi thiếu từ khác
GROUP_RE = re.compile(r"\b(các bạn|mấy bạn|cả nhóm|cả bọn|tụi bạn|bọn bạn|từng bạn|cả lớp)\b")   # nhóm đông mà hình chỉ 2 bạn
TITLE_STOP = {"và", "với", "của", "cho", "ở", "trong", "trên", "dưới", "một", "cái", "chiếc", "con", "trái", "cục", "bị",
              "lỡ", "làm", "rồi", "nào", "đây", "thế", "ơi", "à", "nha", "hen", "nhé", "chuyện", "câu", "bạn", "đi", "về",
              "ra", "vào", "lên", "xuống", "ai", "gì", "sao", "mà", "thì", "là", "có", "không", "đã", "sẽ", "đang", "hôm",
              "nay", "ngày", "lần", "đầu", "tiên", "nhỏ", "to", "bé", "mới", "cũ", "vui", "buồn", "ghê", "quá", "lắm"}
AVOID_BEAT_SCENES = {2, 5, 7}          # cảnh 3 (biến cố), 6 (cao trào), 8 (kết) không chen câu dạy từ (trừ lời chào)
RECENT_DAYS = 30                       # không lặp bộ ba đề / bộ từ trong chừng này ngày
N_WORDS = (3,)                         # số từ tiếng Anh mỗi truyện: bé nhỏ nên 3 là vừa (4 làm truyện hết chỗ kể)
TITLE_MAX = 70
SAD = ("buồn", "khóc", "giận", "sợ", "một mình", "bỏ đi")   # cảnh kết KHÔNG được còn những chữ này (đo thật 19/11: đòi phải có
                                                               # «vui/cười» thì «tặng hoa cho Mèo Cam», «cùng ăn trái cây» bị trượt)
# lời chào không bắt truyện phải nhắc tới (ép thì ra «Chào buổi sáng ngon ghê», đo thật 19/11): câu dạy từ đặt ở cảnh hợp
GREETING_SCENE = {"hello": 0, "good morning": 0, "bye bye": 7, "good night": 7}
BANNED = ("chết", "máu", "giết", "đánh nhau", "ma quỷ", "con ma", "súng", "con dao", "đâm", "ngu ngốc", "đồ ngốc",
          "ghét", "đồ ngu", "xấu xí")
# 8 nhịp của một truyện mầm non (học từ truyện kể cho bé 3-5 tuổi: mở bằng lời chào, một chuyện nhỏ, thử ba lần với một câu
# điệp khúc, bạn giúp, bài học nói nhẹ qua lời nhân vật, kết bằng hình ảnh vòng lại mở đầu)
SCENE_ROLES = (
    "vào thẳng chuyện: nhân vật chính đang ở đâu, làm gì, đang vui (1-2 câu, KHÔNG chào hỏi dài; câu móc đã nói trước đó)",
    "bạn ấy muốn làm một việc nhỏ (chơi, giúp, làm quà...) và bắt đầu làm",
    "chuyện xảy ra: có tiếng động (Bịch! Ối! Rầm!), có vấn đề",
    "VÌ chuyện đó, bạn ấy thử cách thứ nhất (cụ thể) — chưa được, có chút hài; câu điệp khúc lần 1",
    "thử cách thứ hai KHÁC hẳn cách một, to tát hơn — vẫn chưa được, hơi buồn; câu điệp khúc lần 2",
    "một bạn tới gợi ý cách thứ ba (nhờ bạn / nghĩ khác đi) — được rồi! câu điệp khúc lần 3 (đổi kết vui)",
    "bài học rút ra từ chính chuyện vừa xảy ra: một bạn nói nhẹ nhàng một câu, bạn chính gật đầu",
    "kết vui: các bạn cùng chơi/cười; câu cuối là hình ảnh ấm vòng lại mở đầu (không nhắn nhủ «các bé»)",
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
    plot_id = pick_plot(data, lesson_id, conflict_id, [str(s.get("plot") or "") for s in sorted(stories, key=_created)], rnd)
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
    # 2-3 từ là đồ vật/con vật CÓ MẶT ở bối cảnh (truyện nhắc tới tự nhiên), 1 từ gắn với bài học (lời chào/cảm xúc/hành
    # động); chỉ khi thiếu mới lấy từ kho chung (đo thật 19/11: «car» lấy từ kho bị ép vào cảnh cuối sân chơi)
    at_place = unused(by_id(data["settings"], setting_id).get("words", []))
    words = at_place[:n - 1]
    words += [w for w in unused(by_id(data["lessons"], lesson_id).get("words", [])) if w not in words][:1]
    words += [w for w in at_place if w not in words][:n - len(words)]
    words += [w for w in bank if w not in words][:n - len(words)]
    # kho gợi ý cho model (phản hồi 19/11: «xe hơi bóp còi» bị ép vào cuối truyện): đồ vật/con vật của bối cảnh + từ theo
    # bài học; truyện viết xong, choose_words() lấy đúng những thứ đã xuất hiện trong truyện làm từ hôm nay
    pool = list(dict.fromkeys(at_place + by_id(data["lessons"], lesson_id).get("words", []) + words))
    mains = list(data["characters"])
    seen = [str(s.get("main") or "") for s in sorted(stories, key=_created)]

    def last_main(m: str) -> int:                       # lần gần nhất làm nhân vật chính (-1 = chưa bao giờ)
        return (len(seen) - seen[::-1].index(m)) if m in seen else -1
    main = min(mains, key=last_main)
    recent_en = sorted(w for w, k in last_use.items() if k >= cutoff)          # choose_words xếp các từ này sau
    return {"lesson": lesson_id, "setting": setting_id, "conflict": conflict_id, "plot": plot_id, "recent_en": recent_en,
            "words": [{"en": w, "vi": word_info(data, w)["vi"]} for w in words],
            "word_pool": [{"en": w, "vi": word_info(data, w)["vi"]} for w in pool],
            "main": main, "seed": rnd.randrange(1, 2 ** 31)}


def choose_words(story: dict, brief: dict, data: dict) -> list[dict]:
    """Từ tiếng Anh của hôm nay = những từ trong kho mà TRUYỆN ĐÃ NHẮC TỚI (tiếng Việt), ưu tiên từ trong kho gợi ý của đề,
    rồi từ được nhắc ở nhiều cảnh, rồi xuất hiện sớm. Thiếu thì bù bằng từ đề đã chọn (sẽ chèn kèm câu dẫn)."""
    scenes = story.get("scenes") or []
    n = max(1, len(brief.get("words") or []))
    pool = [w["en"] for w in brief.get("word_pool") or []]
    recent = set(brief.get("recent_en") or [])           # từ đã dạy 30 ngày qua xếp sau (pick ghi vào đề)
    found = []
    for w in data["words"]:
        hits = [i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), w, data)]
        if hits:
            rank = 0 if w["en"] in pool else (2 if w["en"] in SPECIES else 1)      # loài của nhân vật: luôn có mặt, để sau cùng
            found.append((rank, 1 if w["en"] in recent else 0, -len(hits), hits[0], w["en"]))
    found.sort()
    chosen: list[str] = []
    taken: set[int] = set()        # câu dạy rải ra các cảnh êm khác nhau (đo thật 19/11: hai câu dồn cảnh 1 ngay sau câu móc)
    for strict in (True, False):   # vòng 1: chỉ từ có cảnh êm còn trống; vòng 2: phần còn lại (vẫn ưu tiên kho gợi ý, nhiều cảnh)
        for *_, en in found:
            if en in chosen:
                continue
            hits = [i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), word_info(data, en), data)]
            calm = [i for i in hits if i not in AVOID_BEAT_SCENES]
            free = [i for i in calm if i not in taken]
            if strict and not free:
                continue
            taken.add(free[0] if free else (calm or hits)[0])
            chosen.append(en)
            if len(chosen) >= n:
                break
        if len(chosen) >= n:
            break
    for w in brief.get("words") or []:
        if len(chosen) >= n:
            break
        if w["en"] not in chosen:
            chosen.append(w["en"])
    return [{"en": en, "vi": word_info(data, en)["vi"]} for en in chosen]


def pick_plot(data: dict, lesson_id: str, conflict_id: str, used: Sequence[str], rnd: random.Random) -> str:
    """Khung truyện (data.json «plots», rút từ truyện mầm non / sách tranh / podcast kể chuyện) hợp bài học và chuyện xảy
    ra; ưu tiên khung lâu chưa dùng. Không khung nào hợp cả hai thì hợp bài học; không nữa thì rỗng (vai cảnh chung)."""
    plots = data.get("plots") or []
    both = [p for p in plots if lesson_id in p.get("lessons", []) and conflict_id in p.get("conflicts", [])]
    cands = both or [p for p in plots if lesson_id in p.get("lessons", [])] or []
    if not cands:
        return ""
    last = {pid: k for k, pid in enumerate(used)}
    order = sorted(cands, key=lambda p: (last.get(p["id"], -1), rnd.random()))
    return str(order[0]["id"])


def plot_beats(brief: dict, data: dict) -> list[str]:
    """8 nhịp cho lời nhắc: theo khung truyện của đề (điền tên nhân vật chính / bạn giúp), không có thì SCENE_ROLES."""
    plot = by_id(data.get("plots") or [], brief.get("plot") or "")
    chars = data["characters"]
    main = chars.get(brief.get("main", ""), {}).get("name_vi", "Thỏ Trắng")
    others = [c["name_vi"] for k, c in chars.items() if k != brief.get("main")]
    friend = others[int(brief.get("seed") or 0) % len(others)] if others else "Gấu Nâu"
    beats = plot.get("beats") if plot else None
    return [b.replace("{main}", main).replace("{friend}", friend) for b in beats] if beats else list(SCENE_ROLES)


def describe(brief: dict, data: dict) -> str:
    """Một dòng cho người đọc: «Thỏ Trắng · xin lỗi · trong vườn · làm vỡ bình hoa · sorry, friend, help»."""
    ch = data["characters"].get(brief.get("main", ""), {})
    return " · ".join(x for x in (ch.get("name_vi", ""), by_id(data["lessons"], brief.get("lesson", "")).get("vi", ""),
                                  by_id(data["settings"], brief.get("setting", "")).get("vi", ""),
                                  by_id(data["conflicts"], brief.get("conflict", "")).get("vi", ""),
                                  ("khung: " + by_id(data.get("plots") or [], brief.get("plot") or "").get("vi", ""))
                                  if brief.get("plot") else "",
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
        text = south_kids(re.sub(r"\s+", " ", re.sub(r"\s*[—–]\s*", ", ", str(sc.get("text") or ""))).strip())
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
    hook = south_kids(re.sub(r"\s+", " ", str(story.get("hook") or "")).strip().strip('"«»'))[:120]
    hook_text = re.sub(r"\s+", " ", str(story.get("hook_text") or "")).strip().strip('"«»!.').upper()[:36]
    prop_vi = re.sub(r"\s+", " ", str(story.get("prop_vi") or "")).strip().strip('"«».!').lower()[:40]
    prop_en = re.sub(r"\s+", " ", str(story.get("prop_en") or "")).strip().strip('"«».!').lower()[:40]
    return {"title": sentence_case(south_kids(re.sub(r"\s+", " ", str(story.get("title") or "")).strip().strip('"«»')[:TITLE_MAX]),
                                   chars),
            "hook": hook, "hook_text": (hook_text + "!") if hook_text else "",
            "prop_vi": prop_vi, "prop_en": prop_en,           # đồ vật chính xuyên suốt (túi kẹo / candy bag) -> phải có trong hình
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
        longest = sorted(((word_count(t), i + 1) for i, t in enumerate(texts)), reverse=True)[:3]
        problems.append(f"cả truyện {total} chữ, tối đa {WORDS_MAX} chữ: cắt câu tả cảnh / câu đệm, giữ lời thoại, điệp khúc, câu "
                        f"hỏi bé; cảnh dài nhất: {', '.join(f'cảnh {i} ({n} chữ)' for n, i in longest)} -> mỗi cảnh "
                        f"{SCENE_MIN + 6}-{SCENE_MAX - 10} chữ")
    for i, t in enumerate(texts, 1):
        n = word_count(t)
        if n < SCENE_MIN or n > SCENE_MAX:
            problems.append(f"cảnh {i} có {n} chữ, cần {SCENE_MIN}-{SCENE_MAX} chữ")
    full = " ".join(texts)
    low = full.lower()
    pool = must_mention(brief, data)
    need = min(len(brief.get("words") or []) or 3, len(pool)) if pool else 0
    have = [w for w in pool if vi_mentioned(full, w, data)]
    if need and len(have) < need:
        problems.append(f"truyện mới nhắc tới {len(have)} trong các thứ gợi ý ({', '.join('«' + w['vi'] + '»' for w in pool)}); "
                        f"cần ít nhất {need} thứ xuất hiện TỰ NHIÊN như một phần của chuyện (không ép vào cho có)")
    foreign = [w["en"] for w in data["words"] if w["en"] not in EN_ALSO_VI
               and count_word(" ".join([full, story.get("title", ""), story.get("hook", ""), story.get("hook_text", "")]),
                              w["en"])]
    if foreign:
        problems.append("truyện và tiêu đề viết THUẦN tiếng Việt, không chen tiếng Anh (" + ", ".join(foreign[:5])
                        + "): phần tiếng Anh mã sẽ tự thêm")
    main = data["characters"].get(brief.get("main", ""), {})
    if main and main["name_vi"].lower() not in low:
        problems.append(f"thiếu nhân vật chính {main['name_vi']}")
    if sum(1 for c in data["characters"].values() if c["name_vi"].lower() in low) < 2:
        problems.append("cần ít nhất một bạn nữa ngoài nhân vật chính (Gấu Nâu, Mèo Cam, Sóc Nhỏ...)")
    hook = story.get("hook") or ""
    nh = word_count(hook)
    if not (HOOK_WORDS[0] <= nh <= HOOK_WORDS[1]) or not re.search(r"[?!]", hook):
        problems.append(f"`hook` phải là MỘT câu hỏi/câu cảm {HOOK_WORDS[0]}-{HOOK_WORDS[1]} chữ gây tò mò về chuyện sắp "
                        f"xảy ra (đang {nh} chữ)")
    nt = word_count((story.get("hook_text") or "").rstrip("!"))
    if not (2 <= nt <= 5):
        problems.append(f"`hook_text` phải 2-5 chữ in hoa hiện to trên màn hình (đang {nt} chữ)")
    prop = story.get("prop_vi") or ""
    if prop and not any(prop_mentioned(prop, t) for t in texts[:2]):
        problems.append(f"đồ vật chính `prop_vi` «{prop}» phải xuất hiện trong lời kể cảnh 1 hoặc 2 (sợi chỉ xuyên suốt truyện)")
    if prop and not re.fullmatch(r"[a-z][a-z' \-]{1,39}", story.get("prop_en") or ""):
        problems.append("`prop_en` phải là tên tiếng Anh của đồ vật chính (1-3 từ, ví dụ «candy bag»)")
    lesson = by_id(data["lessons"], brief.get("lesson", ""))
    if lesson and not any(k in low for k in lesson.get("keywords", [])):
        problems.append(f"cảnh 7 phải nói rõ bài học «{lesson['vi']}» (dùng chữ: {', '.join(lesson['keywords'])})")
    if len(texts) == N_SCENES and any(h in texts[-1].lower() for h in SAD):
        problems.append("cảnh 8 phải kết thúc vui, không còn buồn / khóc / giận / sợ")
    if len(texts) == N_SCENES and re.search(r"các bé|bé ơi|các con|các em", texts[-1].lower()):     # phản hồi 19/11: câu kết «trớt»
        problems.append("câu kết không nói với «các bé» / không dặn dò lần nữa (nghe giảng): kết bằng hình ảnh ấm vòng lại cảnh 1")
    bad = [b for b in BANNED if b in low]
    if bad:
        problems.append("không dùng từ: " + ", ".join(bad))
    title = story.get("title", "")
    if not 6 <= len(title) <= TITLE_MAX:
        problems.append(f"tiêu đề {len(title)} ký tự, cần 6-{TITLE_MAX} ký tự")
    spoil = [k for k in (lesson.get("keywords", []) if lesson else []) if k in title.lower()]
    if spoil or re.search(r"\b(biết|học được|bài học)\b", title.lower()):
        problems.append(f"tiêu đề lộ bài học / kết truyện («{title}»): đặt tên theo chuyện xảy ra để gây tò mò, ví dụ «Thỏ Trắng "
                        "và chiếc bình hoa vỡ»")
    missing = title_mismatch(title, full, data)
    if missing:
        problems.append(f"tiêu đề nhắc tới thứ không có trong truyện ({', '.join('«' + m + '»' for m in missing)}): đặt tên theo "
                        "đồ vật / chuyện thật sự xảy ra trong truyện")
    for i, (s, t) in enumerate(zip(scenes, texts), 1):
        grp = GROUP_RE.search(t.lower())
        if grp and len(s.get("characters") or []) <= 2:
            problems.append(f"cảnh {i} nói «{grp.group(1)}» nhưng hình chỉ có {len(s.get('characters') or [])} bạn: gọi tên bạn "
                            "có mặt trong cảnh (hình mỗi cảnh tối đa 2 bạn)")
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
    n_tail = sum(len(re.findall(r"(?<![\wÀ-ỹ])" + t + r"(?![\wÀ-ỹ])", (full + " " + hook).lower())) for t in TAIL_WORDS)
    if n_tail > TAIL_MAX:
        problems.append(f"«nha/hen» ở cuối câu {n_tail} lần, tối đa {TAIL_MAX} cả truyện: bỏ bớt, kết câu bằng dấu chấm / "
                        "chấm than bình thường")
    if re.search(r"\b(nha|hen|nhé)\s*[?!.]?$", hook.strip().lower()):
        problems.append("câu móc không kết bằng «nha/hen» (ví dụ: «phải làm sao đây?» là đủ)")
    mid = " ".join(texts[1:6]).lower()                         # cảnh 2-6: có câu hỏi cho bé tham gia (sách tranh)
    if len(texts) == N_SCENES and not re.search(r"\bbé\b[^.!?]*\?", mid):
        problems.append("thiếu câu hỏi cho bé giữa truyện (cảnh 2-6, ví dụ «Bé đoán xem Thỏ Trắng giấu ở đâu?») để bé tham gia")
    rep = repeated_sentences(texts)
    if rep:
        problems.append("lặp câu/cụm: " + "; ".join(f"«{x}» ×{n}" for x, n in rep[:3])
                        + " — chỉ câu điệp khúc được lặp (đúng 3 lần), câu khác viết khác đi")
    if example_sentences:
        copied = [x for x in example_sentences if x.lower() in low]
        if len(copied) >= 2:
            problems.append("không chép truyện mẫu (trùng câu: " + "; ".join(copied[:2]) + "): viết truyện mới theo đề")
    return problems


def title_mismatch(title: str, text: str, data: dict) -> list[str]:
    """Chữ có nghĩa trong tiêu đề (bỏ tên nhân vật, từ nối) mà truyện không nhắc: quá nửa thiếu thì tiêu đề lạc đề
    (đo thật 19/11: truyện về trái táo, tiêu đề «chiếc bánh ngọt ngào»)."""
    low = title.lower()
    for c in data["characters"].values():
        low = low.replace(c["name_vi"].lower(), " ")
    words = [w for w in re.findall(r"[\wÀ-ỹ]+", low) if len(w) >= 2 and w not in TITLE_STOP]
    if not words:
        return []
    body = text.lower()
    miss = [w for w in words if not re.search(r"(?<![\wÀ-ỹ])" + re.escape(w) + r"(?![\wÀ-ỹ])", body)]
    return miss if len(miss) * 2 > len(words) else []


def is_greeting(w: dict, data: dict) -> bool:
    return word_info(data, w["en"]).get("group") == "greeting"


def must_mention(brief: dict, data: dict) -> list[dict]:
    """Kho gợi ý cho model: đồ vật / màu / con vật / hành động (không lời chào) — truyện cần lồng ít nhất 3 thứ trong đó một
    cách tự nhiên; mã sẽ chọn từ tiếng Anh từ những thứ đã xuất hiện (choose_words)."""
    pool = brief.get("word_pool") or brief.get("words", [])
    return [w for w in pool if not is_greeting(w, data)]


def repeated_sentences(texts: Sequence[str]) -> list[tuple[str, int]]:
    """Câu (≥ 3 chữ, bỏ dấu câu/ngoặc) lặp lại ngoài mức cho phép: câu lặp nhiều nhất là điệp khúc (≤ REFRAIN_MAX lần),
    câu khác không được lặp. Trả [(câu, số lần)] vi phạm."""
    cnt: Counter[str] = Counter()
    for t in texts:
        parts = re.split(r"([.!?…:]+)", t)                      # [câu, dấu, câu, dấu, ...]
        for i in range(0, len(parts), 2):
            sent, delim = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
            if ":" in delim:                                    # «Gấu Nâu nói:» là lời dẫn, lặp là bình thường
                continue
            key = re.sub(r"\s+", " ", re.sub(r"[«»\"“”,;()]", " ", sent)).strip().lower()
            if word_count(key) >= 4:
                cnt[key] += 1
    if not cnt:
        return []
    refrain = max(cnt, key=lambda k: cnt[k])
    out = [(k, n) for k, n in cnt.items() if (k == refrain and n > REFRAIN_MAX) or (k != refrain and n >= 2)]
    return sorted(out, key=lambda x: -x[1])


def vi_forms(w: dict, data: dict) -> list[str]:
    """Các cách viết tiếng Việt tính là «có nhắc tới» từ đó: vi, các «match» trong kho, và chữ cuối của vi («trái banh» ->
    «banh»)."""
    info = word_info(data, w["en"])
    forms = [w.get("vi") or info.get("vi") or ""] + list(info.get("match") or [])
    if info.get("only_match"):                    # «mới», «một»: chữ gốc quá đa nghĩa, chỉ tính các cụm trong «match»
        forms = list(info.get("match") or [])
    vi = forms[0] if forms else ""
    if " " in vi and not info.get("match"):
        forms.append(vi.split()[-1])
    return [f for f in dict.fromkeys(x.strip().lower() for x in forms) if f]


def vi_mentioned(text: str, w: dict, data: dict) -> bool:
    """Truyện có nhắc tới từ (tiếng Việt) không; các cụm trong «not» của kho bị xoá trước («nôn nóng» không phải hot,
    «nói to» không phải big, «Sóc Nhỏ» không phải small)."""
    low = (text or "").lower()
    for bad in word_info(data, w["en"]).get("not") or []:
        low = re.sub(r"(?<![\wÀ-ỹ])" + re.escape(bad.lower()) + r"(?![\wÀ-ỹ])", " ", low)
    return any(re.search(r"(?<![\wÀ-ỹ])" + re.escape(f) + r"(?![\wÀ-ỹ])", low) for f in vi_forms(w, data))


def first_scene_with(scenes: Sequence[dict], w: dict, data: dict) -> int:
    return next((i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), w, data)), -1)


def is_english(text: str) -> bool:
    """action_en có phải tiếng Anh (toàn ký tự ASCII, không dấu tiếng Việt)."""
    return bool(text.strip()) and all(ord(c) < 128 for c in text)


PROP_CLASSIFIERS = {"cái", "con", "chiếc", "quả", "trái", "cục", "một", "tấm", "cây"}   # loại từ: «con diều» -> diều


def prop_tokens(prop_vi: str) -> list[str]:
    toks = re.findall(r"[\wÀ-ỹ]+", prop_vi.lower())
    return [t for t in toks if t not in PROP_CLASSIFIERS] or toks


def prop_mentioned(prop_vi: str, text: str) -> bool:
    """Lời cảnh có nhắc đồ vật chính không (khớp lỏng: một chữ của tên đồ vật, bỏ loại từ — «túi kẹo» khớp «mang kẹo qua»)."""
    low = text.lower()
    return any(re.search(r"(?<![\wÀ-ỹ])" + re.escape(t) + r"(?![\wÀ-ỹ])", low) for t in prop_tokens(prop_vi))


def beat_words(text: str, brief: dict, data: dict | None = None) -> list[dict]:
    """Từ trong kho (brief["words"]) đã được chèn câu dạy vào lời cảnh này (lời thuần Việt, chữ tiếng Anh chỉ do add_beats).
    data: bổ sung trường mới của kho (pose/contact) cho đề đã bốc trước khi kho có trường đó."""
    bank = {w["en"]: w for w in (data or {}).get("words") or []}
    out = []
    for w in brief.get("words") or []:
        en = str(w.get("en") or "").strip()
        if en and re.search(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", text):
            out.append({**bank.get(en, {}), **w})
    return out


# dấu hiệu «rắc rối» trong lời cảnh: cảnh có nhiều nhất làm ảnh câu móc (bé thấy ngay chuyện gì xảy ra)
PROBLEM_VI = ("buồn", "khóc", "rưng rưng", "giật mình", "hết hồn", "sợ", "lo", "hỏng", "vỡ", "bể", "rớt", "rơi", "quên mất",
              "mất", "quên", "té", "ngã", "đau", "ướt", "lạc", "hư", "kẹt", "trễ", "muộn", "cãi", "giận", "tủi", "thút thít", "cụp",
              "rũ", "ối", "thôi rồi", "làm sao")        # «quên mất» đứng trước «mất»: đếm một
_PROBLEM_RE = re.compile(r"(?<![\wÀ-ỹ])(?:" + "|".join(re.escape(k) for k in PROBLEM_VI) + r")(?![\wÀ-ỹ])")


def hook_scene(story: dict, lo: int = 1, hi: int = 4, default: int = 2) -> int:
    """Chỉ số (0-based) ảnh cảnh dùng cho câu móc: trong cảnh 2-5, cảnh có nhiều dấu hiệu rắc rối nhất (buồn, rớt, mất, giật
    mình…); hoà thì cảnh 3 (chuyện xảy ra) như trước. Đo thật mẫu 32: móc cố định cảnh 3 là Gấu ngồi chờ, trong khi cảnh 4
    (Thỏ giật mình, tai cụp) nói đúng «QUÊN LỜI HỨA!» hơn."""
    scenes = story.get("scenes") or []
    best, best_n = default, 0
    for i in range(lo, min(hi, len(scenes) - 1) + 1):
        n = len(_PROBLEM_RE.findall(scenes[i].get("text", "").lower()))
        if n > best_n or (n == best_n and n > 0 and i != default):     # hoà: cảnh sau (chuyện đã leo thang) hơn cảnh trước
            best, best_n = i, n
    return best


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
    elif total > WORDS_BLOCK:
        out.append(f"cả truyện {total} chữ, quá dài (trên {WORDS_BLOCK}: video không vừa 88 giây)")
    low = " ".join(texts).lower()
    bad = [b for b in BANNED if b in low]
    if bad:
        out.append("không dùng từ: " + ", ".join(bad))
    if not story.get("title"):
        out.append("thiếu tiêu đề")
    if any(len((s.get("action_en") or "").split()) < 3 for s in scenes):
        out.append("có cảnh thiếu action_en")
    return out


# Câu dạy từ xoay vòng, không «nha». Phản hồi 19/11 (mẫu 28): «Đố bé biết, trái banh tiếng Anh là ball» nghe sai vì «đố» mà
# nói luôn đáp án -> dạng đố phải hỏi rồi mới trả lời; đồ vật vừa xuất hiện thì gọi «trái banh đó» cho đúng chuyện đang kể.
BEAT_TEMPLATES = ("À, {vi} tiếng Anh là {en}. Bé nói theo nè: {en}!",
                  "Mà nè, {vi} tiếng Anh là {en}. Cùng đọc to nào: {en}!",
                  "Đố bé biết {vi} tiếng Anh là gì? Là {en}! Bé đọc theo mình: {en}!",
                  "À này, {vi} tiếng Anh là {en}. Nói thật to nào: {en}!")
POINT_GROUPS = {"thing", "animal", "nature", "color"}        # nhóm từ chỉ vật đang có trong cảnh -> «trái banh đó»
CHILD_Q_RE = re.compile(r"(\s*)([^.!?…]*(?<![\wÀ-ỹ])bé(?![\wÀ-ỹ])[^.!?…]*\?)\s*$", re.I)   # câu cuối cảnh hỏi bé


def beat_text(w: dict, k: int = 0, data: dict | None = None) -> str:
    """Câu dạy từ (mã viết, người kể đọc): «À, trái banh đó tiếng Anh là ball. Bé nói theo nè: ball!»; mẫu xoay vòng theo k,
    mẫu 2 là câu đố (hỏi rồi mới đáp). data: để biết nhóm từ (vật trong cảnh thì thêm «đó»)."""
    vi = (w.get("vi") or "").strip()
    if data is not None and word_info(data, w["en"]).get("group") in POINT_GROUPS:
        vi += " đó"
    return BEAT_TEMPLATES[k % len(BEAT_TEMPLATES)].format(vi=vi, en=w["en"])


def ends_with_child_question(text: str) -> bool:
    return bool(CHILD_Q_RE.search(text or ""))


def insert_beat(text: str, beat: str) -> str:
    """Chèn câu dạy từ vào cuối cảnh; cảnh kết bằng câu hỏi cho bé («Bé đoán xem…?») thì chèn TRƯỚC câu hỏi để câu hỏi vẫn
    là câu cuối (lật trang sách), không dồn hai lời gọi bé sát nhau (phản hồi 19/11)."""
    m = CHILD_Q_RE.search(text or "")
    if m:
        return (text[:m.start()].rstrip() + " " + beat + " " + m.group(2).strip()).strip()
    return (text.rstrip() + " " + beat).strip()


def add_beats(story: dict, brief: dict, data: dict) -> list[tuple[str, int]]:
    """Chèn câu dạy từ vào cuối cảnh ĐẦU TIÊN nhắc tới đồ vật đó (không cảnh nào nhắc thì cảnh 2/3/4 kèm câu dẫn). Mỗi cảnh
    tối đa 2 câu dạy để không nặng. Trả [(en, chỉ số cảnh)]."""
    scenes = story.get("scenes") or []
    if len(scenes) < N_SCENES:
        return []
    out: list[tuple[str, int]] = []
    load_count = [0] * len(scenes)

    asking = {i for i, sc in enumerate(scenes) if ends_with_child_question(sc.get("text", ""))}

    def spread(cands: list[int], strict: bool = True) -> int:
        """Cảnh êm (không biến cố / cao trào / kết, không kết bằng câu hỏi bé) chưa có câu dạy -> cảnh êm có 1 câu -> cảnh khác
        có nhắc tới (đo thật 19/11: ba từ cùng xuất hiện ở cảnh 2 thì dồn cả ba câu dạy vào đó) -> đành cảnh êm đầu."""
        calm = [j for j in cands if j not in AVOID_BEAT_SCENES] if strict else list(cands)
        quiet = [j for j in calm if j not in asking]
        for pool in (quiet, calm, cands):
            for lim in (0, 1):
                for j in pool:
                    if load_count[j] <= lim and not (j == 0 and lim):      # cảnh mở đầu chỉ chứa 1 câu dạy (giữ 3 giây đầu)
                        return j
        return (calm or cands)[0]
    for k, w in enumerate(brief.get("words", [])):
        mention = [i for i, sc in enumerate(scenes) if vi_mentioned(sc.get("text", ""), w, data)]
        lead = ""
        if mention:
            i = spread(mention)
        elif is_greeting(w, data):                    # lời chào: cảnh mở đầu / cảnh kết, không cần câu dẫn
            g = GREETING_SCENE.get(w["en"], 6)
            i = spread([g, g + 1 if g < 7 else g - 1], strict=False)
        else:
            i = spread([1 + k % 3, 2, 3, 4, 5, 1])
            lead = f"Ở đây có {w['vi']} nè. "
        scenes[i]["text"] = insert_beat(scenes[i]["text"], lead + beat_text(w, k, data))
        load_count[i] += 1
        out.append((w["en"], i))
    return out


def recap_text(brief: dict) -> str:
    """Câu ôn từ cuối truyện (mã viết, đọc trong màn kết): «Hôm nay mình học: turn là lượt, wait là chờ... Bé nhớ nha!»"""
    ws = brief.get("words", [])
    if not ws:
        return ""
    return "Hôm nay mình học: " + ", ".join(f"{w['en']} là {w['vi']}" for w in ws) + ". Hẹn gặp lại bé ở chuyện sau!"


def script_text(story: dict) -> str:
    return "\n".join(s.get("text", "") for s in story.get("scenes") or [])


def english_tokens(brief: dict) -> set[str]:
    """Các chữ (viết thường) của từ tiếng Anh trong đề, để tô màu phụ đề («thank you» -> {thank, you})."""
    return {p.lower() for w in brief.get("words", []) for p in w["en"].split()}
