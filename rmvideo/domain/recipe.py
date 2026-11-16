"""Món ăn + định lượng ĐÃ ĐỐI CHIẾU với lời gốc (bản 42) — hàm thuần, không I/O, không gọi AI.

AI (prompt_engine.recipe_facts) chỉ ĐỀ XUẤT: tên món, nguyên liệu kèm định lượng và chỗ nghe thấy (đoạn lời gốc số
mấy), các bước có nhãn, mẹo. Mã ở đây KIỂM từng định lượng: con số + đơn vị phải thật sự có trong lời gốc ở đúng đoạn
đó (± 1 đoạn kề) cùng với tên nguyên liệu, hoặc trong mô tả bài gốc. Không kiểm được thì không in số ("vừa đủ").

Đơn vị quy về chuẩn Việt Nam và MÃ tính lại số: lạng -> 100 g, ký / cân -> kg, < 1 kg -> g, ≥ 1000 g chẵn -> kg,
thìa -> muỗng, bát -> chén, quả -> trái. Nguồn nước ngoài (cup, oz, 克, 斤...) hoãn tới khi dùng (kế hoạch S7).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

import ingredients

NOT_RECIPE = "không phải video công thức"          # job.no_autopost bắt đầu bằng chuỗi này: tự đăng bỏ qua video
STEP_KINDS = ("intro", "ingredients", "prep", "cook", "wait", "plate", "taste", "reaction", "promo", "other")
MAX_STEPS = 8
MAX_AI_TIPS = 1

# ---------------- đọc số ----------------

_DIGIT_WORDS = {"không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "lăm": 5, "sáu": 6,
                "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9}
_DW = "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True))
_N = rf"(?:\d+|{_DW})"


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else _DIGIT_WORDS[tok]


def _spell_numbers(t: str) -> str:
    """Số đọc bằng chữ mà máy nghe để nguyên -> chữ số: "hai trăm" = 200, "một trăm rưỡi" = 150, "năm mươi" = 50,
    "hai mươi lăm" = 25, "1 ký rưỡi" = 1.5 ký, "kí" = "ký"."""
    t = re.sub(rf"\b({_N})\s+trăm\s+rưỡi\b", lambda m: str(_num(m.group(1)) * 100 + 50), t)
    t = re.sub(rf"\b({_N})\s+trăm(?:\s+({_N})\s+mươi(?:\s+({_N}))?)?\b",
               lambda m: str(_num(m.group(1)) * 100 + (_num(m.group(2)) * 10 if m.group(2) else 0)
                             + (_num(m.group(3)) if m.group(3) else 0)), t)
    t = re.sub(rf"\b({_N})\s+mươi(?:\s+({_N}))?\b",
               lambda m: str(_num(m.group(1)) * 10 + (_num(m.group(2)) if m.group(2) else 0)), t)
    t = re.sub(r"\bkí\b", "ký", t)
    t = re.sub(rf"\b({_DW})\s+(?=(ký|kg|cân|lít|muỗng|thìa|chén|bát|tiếng|giờ|lạng|ly|cốc)\s+rưỡi\b)",
               lambda m: f"{_DIGIT_WORDS[m.group(1)]} ", t)
    t = re.sub(r"\b(\d+)\s+(ký|kg|cân|lít|muỗng|thìa|chén|bát|tiếng|giờ|lạng|ly|cốc)\s+rưỡi\b",
               lambda m: f"{int(m.group(1)) + 0.5} {m.group(2)}", t)
    return t


_STD = {"g": "g", "gam": "g", "gram": "g", "gr": "g", "kg": "kg", "ký": "kg", "cân": "kg", "lạng": "lạng",
        "ml": "ml", "l": "lít", "lít": "lít", "muỗng": "muỗng", "thìa": "muỗng", "muỗng canh": "muỗng canh",
        "thìa canh": "muỗng canh", "muỗng cà phê": "muỗng cà phê", "thìa cà phê": "muỗng cà phê", "chén": "chén",
        "bát": "chén", "ly": "ly", "cốc": "ly", "quả": "trái", "trái": "trái", "tiếng": "giờ", "giờ": "giờ"}
MASS_VOL = {"g", "kg", "ml", "lít", "muỗng", "muỗng canh", "muỗng cà phê", "chén", "ly", "lạng"}
TIME_UNITS = {"phút", "giây", "giờ", "ngày"}
_NOT_QTY = TIME_UNITS | {"độ", "%", "nghìn", "triệu", "đồng", "tuổi", "người", "cm", "mm", "m", "phần"}
_SPOONS = {"muỗng", "muỗng canh", "muỗng cà phê"}


@dataclass(frozen=True)
class Qty:
    value: float
    unit: str

    def key(self) -> tuple:
        return round(self.value, 3), ("muỗng" if self.unit in _SPOONS else self.unit)


def _to_value(n: str) -> float:
    if "/" in n:
        a, b = n.split("/", 1)
        return float(a) / float(b) if float(b) else 0.0
    return float(n.replace(",", "."))


def standard(q: Qty) -> Qty:
    """Quy về đơn vị chuẩn Việt Nam (mã tính lại số)."""
    v, u = q.value, _STD.get(q.unit, q.unit)
    if u == "lạng":
        v, u = v * 100, "g"
    if u == "kg" and v < 1:
        v, u = v * 1000, "g"
    if u == "g" and v >= 1000 and abs(v / 100 - round(v / 100)) < 1e-9:
        v, u = v / 1000, "kg"
    if u == "lít" and v < 1:
        v, u = v * 1000, "ml"
    return Qty(round(v, 3), u)


def parse(text: str) -> list[Qty]:
    """Mọi số có đơn vị trong câu tiếng Việt (đã quy chuẩn): "nửa ký thịt, 2 thìa nước mắm" -> [500 g, 2 muỗng]."""
    t = _spell_numbers(unicodedata.normalize("NFC", (text or "").lower()))
    out = []
    for item in ingredients.numbers(t):
        n, u = item.split(" ", 1)
        try:
            out.append(standard(Qty(_to_value(n), u)))
        except (ValueError, ZeroDivisionError):
            continue
    return out


def fmt_number(v: float, unit: str = "") -> str:
    """Số kiểu Việt Nam: 1,5 / 500; muỗng, chén dưới 1 thì phân số (1/2 muỗng)."""
    if unit in _SPOONS | {"chén", "ly"} and 0 < v < 1:
        for frac, s in ((0.25, "1/4"), (1 / 3, "1/3"), (0.5, "1/2"), (2 / 3, "2/3"), (0.75, "3/4")):
            if abs(v - frac) < 0.02:
                return s
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def fmt(q: Qty) -> str:
    return f"{fmt_number(q.value, q.unit)} {q.unit}"


# ---------------- so khớp tên ----------------

def _loose(s: str) -> str:
    return ingredients._loose(s or "")


def _name_in(name: str, text: str) -> bool:
    """Tên nguyên liệu có mặt trong câu (cùng nhóm tên gọi, hoặc khớp lỏng — máy nghe sai dấu / phụ âm)."""
    g = ingredients.find(name)
    if g and g & ingredients.find(text):
        return True
    words = [w for w in _loose(name).split() if len(w) >= 2]
    lt = f" {_loose(text)} "
    return bool(words) and all(f" {w} " in lt for w in words)


def _new_ingredients(text: str, source: str) -> list[str]:
    """Nguyên liệu nhắc trong câu mà nguồn KHÔNG có (cùng nhóm tên gọi, hoặc tên viết lỏng nằm trong lời gốc: "gà" có
    trong "cánh gà" thì không tính là lạ)."""
    src = ingredients.find(source)
    lo = _loose(source)                                   # đã có dấu cách hai đầu
    return [ingredients.GROUPS[g][0] for g in sorted(ingredients.find(text) - src)
            if not any(_loose(a) in lo for a in ingredients.GROUPS[g])]


def _key(name: str) -> str:
    g = ingredients.find(name)
    return "g" + ",".join(map(str, sorted(g))) if g else _loose(name).strip()


# ---------------- kết quả ----------------

@dataclass
class Item:
    name: str
    qty: float | None = None
    unit: str = ""
    status: str = "free"          # ok | desc | unverified | conflict | free (không có số: "vừa đủ")
    seg: int = -1
    first_img: int = -1
    raw: str = ""                 # AI ghi định lượng thế nào (cho bảng đối chiếu)
    seen_at: float = -1.0         # giây (video gốc) thấy rõ nguyên liệu lần đầu: điểm danh nguyên liệu của kế hoạch dựng
    said_at: float = -1.0         # giây (video gốc) đoạn lời nói định lượng

    def quantity(self) -> Qty | None:
        return Qty(self.qty, self.unit) if self.qty is not None else None

    @property
    def verified(self) -> bool:
        return self.status in ("ok", "desc")


@dataclass
class Step:
    kind: str
    a: int                        # đoạn lời gốc đầu / cuối của bước
    b: int
    text: str = ""
    ingredients: list = field(default_factory=list)
    independent: bool = False
    t0: float = -1.0              # giây (video gốc) đầu / cuối của bước, flow điền từ mốc lời gốc
    t1: float = -1.0


@dataclass
class Facts:
    is_recipe: bool = True
    dish: str = ""
    servings: str = ""
    items: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    tips: list = field(default_factory=list)          # [{"text", "source": "src" | "ai", "seg"}]
    unassigned: list = field(default_factory=list)    # số có đơn vị trong lời gốc chưa gán cho nguyên liệu nào
    fixes: list = field(default_factory=list)         # câu "cách làm" mã đã sửa số / bỏ (để báo)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["v"] = 1
        d["report"] = report(self)
        return d

    @staticmethod
    def from_dict(d: dict | None) -> Facts | None:
        if not d or d.get("v") != 1:
            return None
        f = Facts(is_recipe=bool(d.get("is_recipe", True)), dish=d.get("dish") or "", servings=d.get("servings") or "",
                  tips=list(d.get("tips") or []), unassigned=list(d.get("unassigned") or []),
                  fixes=list(d.get("fixes") or []))
        f.items = [Item(**{k: v for k, v in x.items() if k in Item.__dataclass_fields__}) for x in d.get("items") or []]
        f.steps = [Step(**{k: v for k, v in x.items() if k in Step.__dataclass_fields__}) for x in d.get("steps") or []]
        return f

    @property
    def conflicts(self) -> list:
        return [x for x in self.items if x.status == "conflict"]


# ---------------- kiểm từng định lượng ----------------

def _window(texts: list[str], i: int) -> str:
    return " ".join(texts[max(0, i - 1): i + 2]) if 0 <= i < len(texts) else ""


def _evidence(name: str, want: Qty, text: str) -> bool:
    return bool(text) and _name_in(name, text) and any(q.key() == want.key() for q in parse(text))


def verify(ai: dict, texts: list[str], desc: str) -> Facts:
    """Bản AI đề xuất (dict theo RECIPE_PROMPT) + lời gốc từng đoạn + mô tả -> Facts đã kiểm."""
    texts = [t or "" for t in texts]
    f = Facts(is_recipe=bool(ai.get("is_recipe", True)), dish=str(ai.get("dish") or "").strip()[:80])
    serv = str(ai.get("servings") or "").strip()
    if serv and any(re.search(rf"\b{re.escape(n)}\b", " ".join(texts) + " " + (desc or ""))
                    for n in re.findall(r"\d+", serv)):
        f.servings = serv[:40]                         # khẩu phần chỉ khi con số có thật trong lời / mô tả
    for x in ai.get("ingredients") or []:
        name = re.sub(r"\s+", " ", str(x.get("name") or "")).strip()[:60]
        if not name:
            continue
        seg = int(x.get("seg", -1)) if str(x.get("seg", "")).lstrip("-").isdigit() else -1
        raw = f"{x.get('qty') or ''} {x.get('unit') or ''}".strip()
        it = Item(name=name, seg=seg, raw=raw,
                  first_img=int(x.get("first_img", -1)) if str(x.get("first_img", "")).lstrip("-").isdigit() else -1)
        qs = parse(raw) if raw else []
        if not qs or qs[0].unit in _NOT_QTY:
            f.items.append(it)                         # "vừa đủ", "một ít", không có số
            continue
        q = qs[0]
        it.qty, it.unit = q.value, q.unit
        if _evidence(name, q, _window(texts, seg)):
            it.status = "ok"
        elif _evidence(name, q, desc or ""):
            it.status = "desc"
        else:
            # AI ghi sai đoạn: tìm trong toàn bộ lời gốc (vẫn phải cùng câu với tên nguyên liệu)
            hit = next((k for k in range(len(texts)) if _evidence(name, q, texts[k])), -1)
            it.status, it.seg = ("ok", hit) if hit >= 0 else ("unverified", seg)
        f.items.append(it)
    # lời nói và mô tả cho hai số khác nhau của CÙNG một nguyên liệu (cùng đơn vị): không biết số nào đúng
    by: dict = {}
    for it in f.items:
        if it.verified:
            by.setdefault((_key(it.name), it.unit), []).append(it)
    for group in by.values():
        kinds = {x.status for x in group}
        if len(kinds) > 1 and len({x.qty for x in group}) > 1:
            for x in group:
                x.status = "conflict"
    _steps(f, ai, texts, desc)
    _tips(f, ai, texts, desc)
    covered = {x.quantity().key() for x in f.items if x.verified and x.quantity()}
    seen = set()
    for t in texts:
        for q in parse(t):
            if q.unit in MASS_VOL and q.key() not in covered and q.key() not in seen:
                seen.add(q.key())
                f.unassigned.append(fmt(q))
    return f


def _source_numbers(texts: list[str], desc: str) -> set:
    return {q.key() for t in [*texts, desc or ""] for q in parse(t)}


_QTY_IN_TEXT = re.compile(r"(\d+(?:[.,/]\d+)?)\s*(" + "|".join(sorted(map(re.escape, ingredients._UNITS), key=len,
                                                                          reverse=True)) + r")(?![\w])", re.I)


def clean_numbers(text: str, allowed: set) -> tuple[str, bool]:
    """Số có đơn vị KHÔNG có trong lời gốc -> "vừa đủ" (thời gian: "một lúc"). Trả (câu, đã sửa?)."""
    changed = False

    def sub(m: re.Match) -> str:
        nonlocal changed
        qs = parse(m.group(0))
        if not qs or qs[0].key() in allowed:
            return m.group(0)
        changed = True
        return "một lúc" if qs[0].unit in TIME_UNITS else "vừa đủ"
    out = _QTY_IN_TEXT.sub(sub, _spell_numbers(text))
    return (out if changed else text), changed


def _steps(f: Facts, ai: dict, texts: list[str], desc: str) -> None:
    """Các bước: sắp theo đoạn, kẹp trong khoảng, nhãn hợp lệ; câu "cách làm" chỉ được dùng số + nguyên liệu có trong
    nguồn (số lạ -> "vừa đủ"; nguyên liệu lạ -> bỏ câu)."""
    n = len(texts)
    allowed = _source_numbers(texts, desc)
    source = " ".join([*texts, desc or "", *(x.name for x in f.items)])
    raw = []
    for s in ai.get("steps") or []:
        try:
            a, b = int(s.get("from", 0)), int(s.get("to", s.get("from", 0)))
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        a, b = max(0, min(a, n - 1)), max(0, min(b, n - 1))
        kind = str(s.get("kind") or "other")
        raw.append(Step(kind=kind if kind in STEP_KINDS else "other", a=min(a, b), b=max(a, b),
                        text=re.sub(r"\s+", " ", str(s.get("text") or "")).strip()[:200],
                        ingredients=[str(x)[:40] for x in (s.get("ingredients") or [])][:12],
                        independent=bool(s.get("independent"))))
    raw.sort(key=lambda s: (s.a, s.b))
    steps: list[Step] = []
    for s in raw:                                     # bỏ chồng lấn: mỗi đoạn thuộc đúng một bước
        if steps and s.a <= steps[-1].b:
            s.a = steps[-1].b + 1
            if s.a > s.b:
                continue
        steps.append(s)
    for s in steps:
        if not s.text:
            continue
        added = _new_ingredients(s.text, source)
        if added:
            f.fixes.append(f"bỏ câu có nguyên liệu không có trong video: «{s.text[:60]}»")
            s.text = ""
            continue
        s.text, changed = clean_numbers(s.text, allowed)
        if changed:
            f.fixes.append(f"bỏ số không có trong lời gốc: «{s.text[:60]}»")
    f.steps = steps


_BANNED_TIP = re.compile(r"sức khỏe|sức khoẻ|chữa|bệnh|giảm cân|béo phì|bà bầu|mang thai|trẻ (em|nhỏ)|ung thư|"
                         r"tiểu đường|huyết áp|thuốc|detox|giải độc|thải độc|miễn dịch|đẹp da|collagen|bổ thận|"
                         r"tráng dương|cholesterol|mỡ máu", re.I)


def _tips(f: Facts, ai: dict, texts: list[str], desc: str) -> None:
    """Mẹo: ưu tiên mẹo người gốc nói (có số đoạn); mẹo AI tự viết tối đa 1, không số liệu lạ, không nói chuyện sức
    khoẻ / chữa bệnh, không nguyên liệu lạ."""
    allowed = _source_numbers(texts, desc)
    source = " ".join([*texts, desc or ""])
    src, own = [], []
    for t in ai.get("tips") or []:
        text = re.sub(r"\s+", " ", str(t.get("text") or "")).strip()[:220]
        try:
            seg = int(t.get("seg", -1))
        except (TypeError, ValueError):
            seg = -1
        if not text or _BANNED_TIP.search(text) or _new_ingredients(text, source):
            continue
        text, changed = clean_numbers(text, allowed)
        if changed:
            continue
        if 0 <= seg < len(texts):
            src.append({"text": text, "source": "src", "seg": seg})
        else:
            own.append({"text": text, "source": "ai", "seg": -1})
    f.tips = (src + own[:MAX_AI_TIPS])[:3]


# ---------------- công thức trong bài đăng ----------------

def ingredient_lines(f: Facts, strict: bool = True) -> list[str]:
    """Dòng nguyên liệu: cộng dồn cùng nguyên liệu cùng đơn vị (2 tép + 3 tép tỏi = 5 tép); có phần chưa đối chiếu
    được thì không cộng: ghi "vừa đủ" (QTY_CHECK=strict) hoặc kèm "(ước lượng)"."""
    order: list = []
    groups: dict = {}
    for it in f.items:
        k = _key(it.name)
        if k not in groups:
            order.append(k)
            groups[k] = []
        groups[k].append(it)
    lines = []
    for k in order:
        its = groups[k]
        name = its[0].name[:1].upper() + its[0].name[1:]
        numeric = [x for x in its if x.qty is not None]
        if not numeric:
            lines.append(f"{name}: vừa đủ")
            continue
        units = sorted({x.unit for x in numeric}, key=lambda u: [x.unit for x in numeric].index(u))
        parts = []
        for u in units:
            xs = [x for x in numeric if x.unit == u]
            if all(x.verified for x in xs):
                parts.append(fmt(standard(Qty(sum(x.qty or 0.0 for x in xs), u))))
            elif not strict:
                parts.append(fmt(standard(Qty(sum(x.qty or 0.0 for x in xs), u))) + " (ước lượng)")
        lines.append(f"{name}: {' + '.join(parts)}" if parts else f"{name}: vừa đủ")
    return lines


def recipe_block(f: Facts | None, *, strict: bool = True, tips: bool = True, steps: bool = True) -> str:
    """Phần công thức của bài đăng (Facebook / YouTube): nguyên liệu, cách làm, mẹo."""
    if not f or not f.is_recipe:
        return ""
    out = []
    lines = ingredient_lines(f, strict) if f.items else []
    if lines:
        out.append("🧾 NGUYÊN LIỆU" + (f" ({f.servings})" if f.servings else ""))
        out += [f"• {ln}" for ln in lines]
    how = [s.text for s in f.steps if s.text and s.kind in ("ingredients", "prep", "cook", "wait", "plate")] if steps else []
    if how:
        out.append("👩‍🍳 CÁCH LÀM")
        out += [f"{k}. {t}" for k, t in enumerate(how[:MAX_STEPS], 1)]
    if tips and f.tips:
        out += [f"💡 MẸO: {t['text']}" for t in f.tips[:2]]
    return "\n".join(out)


def compact_ingredients(f: Facts | None, strict: bool = True, limit: int = 150) -> str:
    """Một dòng cho TikTok: "🧾 Cánh gà 1 kg, tỏi 5 tép, nước mắm 3 muỗng canh"."""
    if not f or not f.is_recipe or not f.items:
        return ""
    parts = []
    for ln in ingredient_lines(f, strict):
        name, _, q = ln.partition(": ")
        parts.append(name.lower() if q == "vừa đủ" else f"{name.lower()} {q}")
    body = ", ".join(parts)
    s = "🧾 " + body[:1].upper() + body[1:]
    return s if len(s) <= limit else s[:limit].rsplit(",", 1)[0] + "…"


def report(f: Facts) -> str:
    """Một dòng cho ghi chú / màn duyệt: "Định lượng: 7/8 khớp lời gốc, 1 chưa đối chiếu được (bỏ số)..."."""
    nums = [x for x in f.items if x.qty is not None]
    if not f.is_recipe:
        return "AI xác định đây không phải video công thức."
    if not nums:
        return "Định lượng: lời gốc không có số cụ thể."
    ok = sum(1 for x in nums if x.status == "ok")
    desc = sum(1 for x in nums if x.status == "desc")
    bad = [x for x in nums if x.status == "unverified"]
    con = [x for x in nums if x.status == "conflict"]
    parts = [f"{ok}/{len(nums)} khớp lời gốc"]
    if desc:
        parts.append(f"{desc} khớp mô tả bài gốc")
    if bad:
        parts.append(f"{len(bad)} chưa đối chiếu được ({', '.join(f'{x.name} {x.raw}' for x in bad[:3])})")
    if con:
        parts.append(f"{len(con)} mâu thuẫn ({', '.join(f'{x.name} {x.raw}' for x in con[:3])})")
    s = "Định lượng: " + ", ".join(parts) + "."
    if f.unassigned:
        s += f" Số trong lời gốc chưa gán nguyên liệu: {', '.join(f.unassigned[:4])}."
    return s


def table(f: Facts) -> list[dict[str, Any]]:
    """Bảng đối chiếu cho màn duyệt web: tên, AI nghe được, đã quy chuẩn, trạng thái, đoạn lời gốc."""
    mark = {"ok": "✅ lời gốc", "desc": "✅ mô tả", "unverified": "⚠️ chưa đối chiếu", "conflict": "❌ mâu thuẫn",
            "free": "—"}
    return [{"name": x.name, "raw": x.raw, "std": fmt(Qty(x.qty, x.unit)) if x.qty is not None else "vừa đủ",
             "status": mark.get(x.status, x.status), "seg": x.seg} for x in f.items]


# ---------------- video quảng cáo của nguồn (tự động đăng) ----------------

_AD_TITLE = re.compile(r"kh[oó]a (h[oọ]c|b[eế]p|d[aạ]y|ti[eệ]c)|h[oọ]c vi[eê]n|tuy[eể]n (sinh|d[uụ]ng|nh[aâ]n)|"
                       r"ti[eệ]c (tr[oọ]n g[oó]i|c[uư][oớ]i)|đ[aặ]t ti[eệ]c|nh[aậ]n (đ[aặ]t|n[aấ]u) ti[eệ]c|khai gi[aả]ng|"
                       r"ra tr[uư][oờ]ng|hotline|zalo|s[đd]t\b|li[eê]n h[eệ] (qu[aá]n|em|m[iì]nh|s[oố])", re.I)


def looks_like_ad(title: str, duration: float) -> str:
    """Lý do bỏ video nguồn KHÔNG phải công thức (quảng cáo khóa học, tiệc, tuyển dụng...) hoặc "" nếu dùng được."""
    t = unicodedata.normalize("NFC", (title or "").strip())
    m = _AD_TITLE.search(t)
    if m:
        return f"video quảng cáo («{m.group(0)}»)"
    if not t and 0 < duration < 60:
        return "video ngắn không tiêu đề (thường là quảng cáo / hậu trường)"
    return ""
