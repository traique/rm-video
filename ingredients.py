"""Kiểm tra lời mới giữ ĐỦ và ĐÚNG nguyên liệu của lời gốc.
Mỗi nhóm là MỘT nguyên liệu với các tên gọi tương đương (Bắc/Nam). Đổi sang tên cùng nhóm thì được
(mùi -> ngò, dứa -> khóm); bỏ mất nhóm, hoặc thêm nhóm không có trong gốc (gà -> vịt) là lỗi."""
import re
import unicodedata

GROUPS = [
    ["trứng gà", "hột gà", "trứng"], ["trứng vịt", "hột vịt"], ["trứng cút", "hột cút"], ["trứng muối", "hột vịt muối"],
    ["thịt heo", "thịt lợn", "heo", "lợn"], ["thịt bò", "bò"], ["thịt gà", "gà"], ["thịt vịt", "vịt"], ["ba chỉ", "ba rọi"], ["nạc vai"], ["sườn"],
    ["cánh gà"], ["đùi gà"], ["giò lụa", "chả lụa"], ["xúc xích"], ["lạp xưởng"],
    ["tôm"], ["mực"], ["cua"], ["nghêu", "ngao"], ["sò"], ["ốc"], ["cá"],
    ["đậu phụ", "đậu hũ", "tàu hũ"], ["đậu phộng", "lạc"], ["đậu xanh"], ["đậu đen"], ["đậu bắp"],
    ["dứa", "khóm", "trái thơm", "quả thơm", "thơm"], ["ngô", "bắp"], ["bắp cải"], ["cải thảo"], ["cải"], ["súp lơ", "bông cải"],
    ["rau muống"], ["mồng tơi"], ["rau ngót"], ["giá đỗ", "giá sống", "rau giá", "giá hột", "giá trụng", "đậu giá", "giá"], ["su hào"], ["bí đỏ", "bí rợ"], ["bí xanh", "bí đao"],
    ["mướp"], ["cà tím"], ["cà chua"], ["cà rốt"], ["khoai tây"], ["khoai lang"], ["khoai môn"],
    ["hành tây"], ["hành tím", "hành khô"], ["hành lá", "hành"], ["tỏi"], ["gừng"], ["sả"], ["riềng"], ["nghệ"],
    ["ớt chuông"], ["ớt"], ["tiêu", "hạt tiêu"], ["mùi", "ngò", "rau mùi"], ["rau răm"], ["húng quế", "rau quế"],
    ["tía tô"], ["thì là"], ["lá chanh"], ["chanh"], ["me"], ["dừa"], ["nước cốt dừa"],
    ["mộc nhĩ", "nấm mèo"], ["nấm hương", "nấm đông cô"], ["nấm"],
    ["miến"], ["bún"], ["phở"], ["mì"], ["nui"], ["bánh mì"], ["bột mì"], ["bột năng"], ["bột chiên"],
    ["dầu ăn"], ["dầu hào"], ["dầu mè"], ["nước mắm"], ["xì dầu", "nước tương"], ["muối"], ["đường"],
    ["hạt nêm"], ["bột ngọt", "mì chính"], ["giấm"], ["mật ong"], ["sữa"], ["bơ"], ["phô mai"],
    # --- mở rộng ---
    ["thịt dê"], ["thịt cừu"], ["thịt ếch", "ếch"], ["lươn"], ["cá lóc", "cá quả"], ["cá basa"], ["cá hồi"], ["cá ngừ"],
    ["cá thu"], ["cá diêu hồng"], ["cá rô"], ["cá trê"], ["cá kèo"], ["tôm khô"], ["tép riu", "tép khô", "con tép", "tép đồng", "tép rang"], ["ghẹ"], ["hàu"], ["bạch tuộc"],
    ["chả cá"], ["nem chua"], ["chả giò", "nem rán"], ["thịt xay", "thịt băm"], ["sườn non"], ["chân giò", "giò heo"],
    ["lòng heo", "lòng lợn"], ["gan heo", "gan gà", "gan bò", "gan lợn", "gan ngỗng"], ["tim heo", "tim gà", "tim lợn"], ["bao tử", "dạ dày"], ["da heo", "bì heo", "bì lợn"], ["mỡ heo", "mỡ lợn"], ["tóp mỡ"],
    ["xương ống"], ["xương gà"], ["ức gà"], ["lòng gà"], ["trứng bắc thảo"],
    ["đậu đỏ"], ["đậu nành"], ["đậu que", "đậu cô ve"], ["đậu hà lan"], ["đậu lăng"], ["hạt sen"], ["hạt điều"],
    ["mè", "vừng"], ["hạt dẻ"], ["óc chó"], ["hạnh nhân"],
    ["rau cải ngọt", "cải ngọt"], ["cải bẹ xanh", "cải xanh"], ["cải thìa"], ["cải xoong"], ["xà lách"], ["rau dền"],
    ["rau lang"], ["rau má"], ["rau đay"], ["rau cần"], ["rau tần ô", "cải cúc"], ["ngò gai", "mùi tàu"], ["ngò om", "rau ngổ"],
    ["húng lủi", "húng bạc hà"], ["lá lốt"], ["lá nếp", "lá dứa"], ["hành phi"], ["tỏi phi"], ["bông bí"], ["bông thiên lý"],
    ["măng"], ["măng tây"], ["ngó sen"], ["củ sen"], ["củ cải trắng", "cải trắng"], ["củ cải đỏ"], ["củ dền"], ["khoai mỡ"],
    ["sắn", "khoai mì"], ["bí ngòi"], ["dưa leo", "dưa chuột"], ["dưa cải", "dưa chua"], ["kim chi"], ["cà pháo"],
    ["ớt hiểm", "ớt chỉ thiên"], ["ớt bột"], ["ớt sừng"], ["sa tế"], ["tương ớt"], ["tương cà"], ["mayonnaise", "sốt mayonnaise"],
    ["tương đen", "tương hột"], ["mắm tôm"], ["mắm ruốc"], ["mắm nêm"], ["mắm cá"], ["nước cốt chanh"], ["nước dừa"],
    ["dầu điều"], ["dầu ô liu"], ["dầu dừa"], ["mỡ hành"], ["bơ lạt"], ["bơ đậu phộng"], ["sữa đặc"], ["sữa tươi"], ["whipping cream", "kem tươi"],
    ["kem"], ["sữa chua"], ["phô mai mozzarella", "mozzarella"], ["trứng cá"], ["rong biển"], ["tảo"],
    ["bánh tráng"], ["bánh phở"], ["bánh canh"], ["hủ tiếu"], ["mì gói", "mì tôm"], ["mì udon", "udon"], ["mì ý", "spaghetti", "mì spaghetti"],
    ["bột gạo"], ["bột nếp"], ["bột bắp", "bột ngô"], ["bột nở"], ["bột baking soda", "baking soda"], ["men nở"], ["bột cà ri"], ["ngũ vị hương"],
    ["quế"], ["hoa hồi", "đại hồi", "hồi khô", "hồi"], ["thảo quả"], ["đinh hương"], ["hạt mùi", "hạt ngò"], ["nghệ tươi"], ["lá chanh thái"], ["cà ri"],
    ["đường phèn"], ["đường thốt nốt"], ["đường nâu"], ["mạch nha"], ["siro"], ["cacao", "ca cao"], ["cà phê"], ["trà"], ["matcha"],
    ["gạo"], ["gạo nếp", "nếp"], ["cơm"], ["xôi"], ["bánh mì sandwich", "sandwich"], ["bột chiên giòn"], ["bột chiên xù"], ["bột bánh xèo"],
    ["chuối"], ["xoài"], ["táo"], ["quả lê", "trái lê"], ["nho khô", "quả nho", "trái nho", "chùm nho"], ["quả cam", "trái cam", "nước cam", "vỏ cam", "cam vắt", "cam"], ["quýt"], ["bưởi"], ["dâu tây", "quả dâu", "trái dâu"], ["sầu riêng"], ["mít"], ["ổi"],
    ["đu đủ"], ["thanh long"], ["dưa hấu"], ["dừa non"], ["bơ trái", "quả bơ", "trái bơ"], ["kiwi"], ["việt quất"], ["nhãn"], ["vải thiều", "quả vải", "trái vải"], ["chôm chôm"],
]

_ALIASES = sorted(((a, gi) for gi, g in enumerate(GROUPS) for a in g), key=lambda x: -len(x[0].split()))


def _syllables(text: str) -> list:
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


# Từ đa nghĩa: KHÔNG tính là nguyên liệu khi từ đứng TRƯỚC / SAU thuộc các tập này
_CTX = {
    "thơm": ({"mùi", "rất", "thật", "nghe", "dậy", "bay", "cho", "càng", "hơi", "đã", "vừa", "sẽ", "để", "lên", "ra"},
             {"ngon", "phức", "lừng", "nức", "quá", "lắm", "ghê", "nồng", "dịu", "béo", "giòn", "ngát", "thơm", "hơn",
              "rồi", "mùi", "nhẹ", "đậm", "mát", "và", "nữa", "hết", "cả", "dễ", "ngất", "điếc"}),
    "giá": ({"mức", "cái", "đơn", "có", "tăng", "giảm", "rẻ", "hỏi", "về", "bảng", "trả", "ra"},
            {"đang", "rất", "tốt", "rẻ", "chỉ", "cả", "tiền", "bán", "là", "khoảng", "hời", "hợp", "siêu", "cực", "ưu",
             "sốc", "trị", "thành", "cao", "thấp", "mềm", "bình", "như", "nào", "bao", "mà", "có", "trên", "từ"}),
    "hồi": ({"lúc", "từ", "vào", "những", "cái", "thuở"},
            {"nãy", "xưa", "đó", "trước", "nhỏ", "sáng", "chiều", "tối", "hôm", "còn", "ấy", "này", "đầu", "sau", "giờ",
             "lâu", "tưởng", "trưa", "nào", "tết", "mới", "đấy", "phục"}),
    "cam": ({"màu", "vàng", "đỏ", "cam"}, {"kết", "chịu", "đoan", "go", "lòng", "tâm", "phận"}),
    "hành": ({"tiến", "thực", "thi", "ban", "vận", "lưu", "phát"},
             {"động", "trình", "khách", "lang", "lý", "chính", "quân", "xử", "nghề", "tinh", "tây"}),
    "cá": (set(), {"nhân", "biệt", "tính", "thể", "cược", "độ", "mập"}),
    "cơm": ({"với", "kèm", "cùng", "ăn", "bữa"}, set()),
    "mùi": ({"có", "bay", "át", "khử", "hết", "bớt", "đủ", "cái", "nặng", "đậm", "dậy", "toàn", "ra", "thơm"},
            {"thơm", "hôi", "vị", "tanh", "khét", "gì", "rất", "của", "đặc", "hấp", "lạ", "nồng", "hăng", "khó", "quá",
             "lắm", "ghê", "này", "đó", "bay", "dầu", "khói", "nhẹ"}),
}


def _ctx_skip(alias: str, syl: list, i: int) -> bool:
    ex = _CTX.get(alias)
    if not ex:
        return False
    prev = syl[i - 1] if i > 0 else ""
    nxt = syl[i + 1] if i + 1 < len(syl) else ""
    return prev in ex[0] or nxt in ex[1] or any(ch.isdigit() for ch in nxt)


def counts(text: str) -> dict:
    """{chỉ số nhóm nguyên liệu: số lần nhắc} (khớp nguyên âm tiết, cụm dài ưu tiên trước)."""
    syl = _syllables(text)
    used = [False] * len(syl)
    found: dict = {}
    for alias, gi in _ALIASES:
        a = alias.split()
        n = len(a)
        for i in range(len(syl) - n + 1):
            if syl[i:i + n] == a and not any(used[i:i + n]) and not (n == 1 and _ctx_skip(alias, syl, i)):
                found[gi] = found.get(gi, 0) + 1
                for j in range(i, i + n):
                    used[j] = True
    return found


def find(text: str) -> set:
    """Tập chỉ số nhóm nguyên liệu xuất hiện (khớp nguyên âm tiết, cụm dài ưu tiên trước)."""
    return set(counts(text))


def _loose(text: str) -> str:
    """Bỏ dấu + gộp phụ âm hay bị máy nghe nhầm (s/x, d/gi/r, ch/tr) -> so khớp lỏng."""
    t = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\bgi", "d", t)
    t = re.sub(r"\b[sx]", "x", t)
    t = re.sub(r"\b[dr]", "d", t)
    t = re.sub(r"\b(ch|tr)", "ch", t)
    return " " + " ".join(t.split()) + " "


def check(orig: str, new: str) -> tuple:
    """Trả về (thiếu, bịa): tên nguyên liệu gốc bị mất, và nguyên liệu mới không có trong gốc.
    "Bịa" có dung sai lỗi chính tả của máy nghe: gốc "xả", "la lốt" thì viết "sả", "lá lốt" là đúng."""
    a, b = find(orig), find(new)
    missing = [GROUPS[i][0] for i in sorted(a - b)]
    lo = _loose(orig)
    added = [GROUPS[i][0] for i in sorted(b - a)
             if not any(_loose(al) in lo for al in GROUPS[i])]
    return missing, added


_FORMS = ("dầu", "nước cốt", "nước")   # máy nghe hay làm mất chữ đầu: "dầu mè" -> "dòng mè" (chỉ còn "mè")


def _near(alias: str, orig: str) -> bool:
    """Tên nguyên liệu trong lời mới GẦN GIỐNG một cụm trong lời gốc (máy nghe sai một chữ cái: "rau găm" = "rau răm",
    "nước mắn" = "nước mắm"): AI đang SỬA lỗi bóc lời, không phải bịa. Chỉ cụm ≥ 2 âm tiết hoặc âm tiết ≥ 4 chữ."""
    import difflib
    a = alias.split()
    syl = _syllables(orig)
    if len(alias) < 4:
        return False
    for i in range(len(syl) - len(a) + 1):
        cand = " ".join(syl[i:i + len(a)])
        if cand != alias and difflib.SequenceMatcher(None, cand, alias).ratio() >= (0.85 if len(a) > 1 else 0.75) \
                and cand[:1] == alias[:1] or (len(a) > 1 and cand.split()[0] == a[0]
                                              and difflib.SequenceMatcher(None, cand, alias).ratio() >= 0.8):
            return True
    return False


def check_rewrite(orig: str, new: str) -> tuple:
    """Như check(), dùng để QUYẾT ĐỊNH bắt AI viết lại, bỏ hai kiểu báo động giả lần nào cũng gặp trong log thật
    (mỗi lần tốn 2 lượt AI và có nguy cơ AI nhét nguyên liệu lạ vào món):
    - gốc "mè" (máy nghe "dòng mè"), mới "dầu mè": cùng nguyên liệu khác dạng (dầu/nước/nước cốt) -> coi là một;
    - nguyên liệu chỉ nhắc ĐÚNG MỘT LẦN trong lời gốc hay là chữ máy nghe nhầm ("Các c. cua, cá kèo" trong món cá kèo,
      "ớt" của "ướp", "giá" của "giá đầm mì") -> không bắt phải có."""
    a, b = counts(orig), find(new)
    lo = _loose(orig)
    missing = [i for i in sorted(a) if i not in b]
    added = [i for i in sorted(b - set(a)) if not any(_loose(al) in lo or _near(al, orig) for al in GROUPS[i])]

    def form_of(x, y):          # y là x ở dạng khác: "mè" -> "dầu mè" (KHÔNG dùng "nằm trong": gà ⊂ trứng gà,
        return any(y == f"{f} {x}" for f in _FORMS)      # hồi ⊂ cá hồi, bắp ⊂ bắp cải là các thứ khác hẳn)
    for m in list(missing):
        for d in list(added):
            if any(form_of(p, q) or form_of(q, p) for p in GROUPS[m] for q in GROUPS[d]):
                missing.remove(m)
                added.remove(d)
                break
    return [GROUPS[i][0] for i in missing if a[i] >= 2], [GROUPS[i][0] for i in added]


def mask(text: str) -> list:
    """Âm tiết của câu, trong đó tên nguyên liệu và con số được thay bằng '§'
    (không tính vào đo mức chép lời, vì bắt buộc phải giữ nguyên)."""
    syl = _syllables(text)
    used = [False] * len(syl)
    for alias, gi in _ALIASES:
        a = alias.split()
        n = len(a)
        for k in range(len(syl) - n + 1):
            if syl[k:k + n] == a and not any(used[k:k + n]) and not (n == 1 and _ctx_skip(alias, syl, k)):
                for j in range(k, k + n):
                    used[j] = True
    return ["§" if (u or s.isdigit()) else s for s, u in zip(syl, used)]


# ---------- Kiểm CON SỐ (chỉ số có đơn vị đo: định lượng, thời gian, nhiệt độ, giá) ----------
_UNITS = ("g", "gam", "gram", "gr", "kg", "ký", "cân", "lạng", "ml", "lít", "l", "muỗng", "thìa", "muỗng canh",
          "muỗng cà phê", "thìa canh", "thìa cà phê", "chén", "bát", "ly", "cốc", "tô", "quả", "trái", "củ", "tép",
          "nhánh", "cây", "lá", "miếng", "lát", "con", "cái", "gói", "hộp", "lon", "chai", "phút", "giây", "tiếng",
          "giờ", "ngày", "độ", "%", "phần", "k", "nghìn", "ngàn", "triệu", "đồng", "đ", "tuổi", "người", "cm", "mm", "m")
_NUM_RE = re.compile(r"(\d+/\d+|\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True))
                     + r")(?![\w])", re.IGNORECASE)


_WORDNUM = {"một": "1", "hai": "2", "ba": "3", "bốn": "4", "tư": "4", "năm": "5", "sáu": "6", "bảy": "7",
            "tám": "8", "chín": "9", "mười": "10", "nửa": "0.5", "rưỡi": "0.5"}
_WORDNUM_RE = re.compile(r"\b(" + "|".join(_WORDNUM) + r")\s+(?=(" +
                         "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True)) + r")\b)")


def numbers(text: str) -> list:
    """Danh sách số có đơn vị: ['2 quả', '1 muỗng', '200 g', '15 phút'] (chuẩn hóa số).
    Số viết bằng chữ ("hai muỗng") cũng tính. Bỏ "1 cái/1 chiếc" vì đó là mạo từ ("cho vào 1 cái tô")."""
    t = _WORDNUM_RE.sub(lambda m: _WORDNUM[m.group(1)] + " ", (text or "").lower())
    t = re.sub(r"\b(\d+)\s+phần\s+(\d+)\b", r"\1/\2", t)                 # "1 phần 3 muỗng" = "1/3 muỗng"
    # "đợi một lát", "cắn một miếng", "thử một xíu": không phải định lượng (trước đây báo bịa "1 lát")
    t = re.sub(r"\b(đợi|chờ|cắn|ăn|thử|nếm|nghỉ)\s+(thêm\s+)?1\s+(lát|miếng|chút|xíu|tí)\b", r"\1 ", t)
    t = re.sub(r"\b1\s+(lát|chút|xíu|tí|hồi)\s+(là|sau|nữa|thôi|rồi|cho)\b", r"\2 ", t)     # "một lát là cá tự bung"
    out = []
    for n, u in _NUM_RE.findall(t):
        if n == "1" and u in ("cái", "chiếc", "con"):
            continue
        n = n.replace(",", ".").rstrip("0").rstrip(".") if "." in n.replace(",", ".") else n
        out.append(f"{n} {u}")
    return out


_UNIT_EQ = {"thìa": "muỗng", "thìa canh": "muỗng canh", "thìa cà phê": "muỗng cà phê", "bát": "chén", "quả": "trái",
            "gam": "g", "gram": "g", "gr": "g", "ký": "kg", "cân": "kg", "lít": "l", "cốc": "ly", "ngàn": "nghìn", "k": "nghìn",
            "đ": "đồng", "tiếng": "giờ"}


def _norm_num(s: str) -> str:
    n, u = s.split(" ", 1)
    u = _UNIT_EQ.get(u, u)
    if u == "giờ" and "." in n:                 # "nửa tiếng" = "30 phút" (trước đây báo thiếu "0.5 giờ")
        return f"{int(round(float(n) * 60))} phút"
    return f"{n} {u}"


def check_numbers(orig: str, new: str) -> tuple:
    """(số bị mất, số bị bịa/đổi). So theo CON SỐ; đơn vị tương đương Bắc/Nam coi như nhau."""
    a = {_norm_num(x) for x in numbers(orig)}
    b = {_norm_num(x) for x in numbers(new)}
    an, bn = {x.split(" ")[0] for x in a}, {x.split(" ")[0] for x in b}
    missing = sorted(x for x in a if x.split(" ")[0] not in bn)
    added = sorted(x for x in b if x.split(" ")[0] not in an)
    return missing, added
