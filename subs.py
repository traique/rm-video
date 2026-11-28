"""Sinh file phụ đề .ass từ mốc thời gian từng từ mà edge-tts trả về.
Phụ đề khớp chính xác giọng đọc AI, style chữ to đậm viền đen kiểu video ngắn."""
import re

_STYLES = {
    # karaoke: chữ sáng dần vàng theo giọng (CapCut)
    "karaoke": "Style: Default,Be Vietnam Pro ExtraBold,58,&H0000E1FF,&H00FFFFFF,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,330,1",
    # box: chữ trắng trên hộp đen mờ (dễ đọc trên nền sáng)
    "box": "Style: Default,Be Vietnam Pro ExtraBold,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,6,0,2,60,60,330,1",
    # outline: chữ trắng viền đen dày, không hiệu ứng
    "outline": "Style: Default,Be Vietnam Pro ExtraBold,58,&H00FFFFFF,&H00FFFFFF,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,330,1",
    # plain: chữ vàng viền mảnh, gọn
    "plain": "Style: Default,Be Vietnam Pro ExtraBold,52,&H0000E1FF,&H0000E1FF,&H00141414,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,60,60,330,1",
    # kids: chữ trắng to, viền tối dày, cao hơn đáy một chút (kênh truyện bé; từ tiếng Anh tô màu qua `highlight`)
    "kids": "Style: Default,Be Vietnam Pro ExtraBold,62,&H00FFFFFF,&H00FFFFFF,&H00202020,&H96000000,-1,0,0,0,100,100,0,0,1,5,2,2,56,56,400,1",
}

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _tok(w: str) -> str:
    return re.sub(r"\W+", "", (w or "").lower())


def _brand_pairs() -> set:
    """Cặp chữ liền nhau của tên kênh («bếp đong», «đong đủ»): phụ đề không được ngắt dòng giữa chúng."""
    try:
        import config
        name = config.brand_text("BRAND_NAME")
    except Exception:
        return set()
    toks = [_tok(w) for w in (name or "").split() if _tok(w)]
    return set(zip(toks, toks[1:]))


def _keep_pairs(chunk: list, pairs: set) -> list:
    """Dời ranh giới dòng ra khỏi giữa tên kênh (bản 1.0.1: «cả nhà Đong | đủ là ngon» đọc thành «cả nhà đông»)."""
    for j in range(len(chunk) - 1):
        a, b = chunk[j], chunk[j + 1]
        if a and b and (_tok(a[-1][2]), _tok(b[0][2])) in pairs:
            if len(a) > 1:
                chunk[j], chunk[j + 1] = a[:-1], [a[-1]] + b
            elif len(b) > 1:
                chunk[j], chunk[j + 1] = a + [b[0]], b[1:]
    return chunk


# ---- chỗ ngắt dòng (1.4.2) ----
# Phản hồi 22/11: «ngắt câu chưa tự nhiên» — chia đều theo số chữ ra «Giờ mình lấy 1 | kg cà rốt gọt | vỏ rồi tới 1 | kg củ
# cải trắng» (bếp), «Tiếng | Anh là flower» (bé). Giờ chọn chỗ ngắt có điểm: không tách số khỏi đơn vị, không tách từ
# ghép / tên riêng, không để dòng kết bằng hư từ («của», «cho», «và»...), ưu tiên ngắt TRƯỚC từ nối («rồi», «thì»...);
# dòng vẫn cân đối, tối đa max_words (+1 khi cần để khỏi ngắt xấu).
_NUM_WORDS = {"một", "hai", "ba", "bốn", "tư", "năm", "sáu", "bảy", "tám", "chín", "mười", "mươi", "trăm", "nghìn", "ngàn",
              "triệu", "nửa", "vài", "mấy", "rưỡi"}
_UNIT_WORDS = {"g", "gam", "gram", "gr", "kg", "ký", "kí", "cân", "lạng", "ml", "lít", "l", "muỗng", "thìa", "chén", "bát",
               "ly", "cốc", "phút", "giây", "tiếng", "giờ", "ngày", "tháng", "năm", "lần", "củ", "quả", "trái", "cái", "con",
               "miếng", "lát", "nhánh", "tép", "cây", "bó", "gói", "hũ", "nước", "độ", "phần", "người", "chiếc", "bông"}
# hư từ / từ chỉ loại gắn với chữ SAU nó: dòng không nên kết bằng chúng
_BIND_NEXT = {"của", "cho", "và", "với", "là", "các", "những", "một", "cái", "con", "chiếc", "rất", "thật", "hơi", "đã",
              "sẽ", "đang", "không", "chưa", "bị", "được", "tới", "đến", "từ", "vô", "vào", "để", "thì", "mà", "khi", "nếu",
              "vì", "nên", "như", "trong", "trên", "dưới", "lúc", "cây", "củ", "quả", "trái", "bông", "tiếng", "hũ", "nồi",
              "chảo", "bạn", "cô", "chú", "bà", "ông", "anh", "chị", "em", "mình", "hãy", "đừng", "cùng", "thêm", "khoảng",
              "chừng", "tầm", "mỗi", "nhiều", "ít", "cả", "hết", "giúp", "làm", "bỏ", "đem", "sau", "trước"}
# từ nối: ngắt ngay TRƯỚC chúng là tự nhiên
_GOOD_START = {"rồi", "và", "thì", "là", "nhưng", "mà", "để", "cho", "khi", "nếu", "vì", "nên", "hay", "hoặc", "còn", "cũng",
               "với", "lúc", "sau", "trước", "giờ", "xong", "thế", "vậy", "bỗng", "ai", "ơi"}
_COMPOUND_WORDS = (
    "mọi người", "cả nhà", "các bạn", "hôm nay", "bây giờ", "lúc này", "tiếng anh", "gọt vỏ", "rửa sạch", "để ráo",
    "ăn kèm", "tủ lạnh", "tủ mát", "bảo quản", "tiện lợi", "giòn rụm", "giòn tan", "sần sật", "thơm ngon", "đơn giản",
    "đậm đà", "mềm mại", "khổng lồ", "giật mình", "nhúc nhích", "cẩn thận", "ngạc nhiên", "vui vẻ", "bạn bè", "xin lỗi",
    "cảm ơn", "cám ơn", "chuẩn bị", "nguyên liệu", "công thức", "thành phẩm", "lửa nhỏ", "nhỏ lửa", "vừa ăn", "nêm nếm",
    "gia vị", "cắt lát", "xắt sợi", "thái lát", "một chút", "chút xíu", "đồ chua", "như vậy", "thế này", "sau khi",
    "trước khi", "trong khi", "bởi vì", "cho nên", "nhưng mà", "ngon lành", "hấp dẫn", "bông hoa", "cái cây", "con diều",
    "chiếc bóng", "vườn rau", "cầu vồng", "mặt trời", "mặt trăng", "ngôi sao", "quả bóng", "trái bóng", "hộp quà",
    "bánh kem", "bánh quy", "đồ chơi", "xe đạp", "ôm chặt", "ăn được", "đẹp mắt", "đều tay", "sạch sẽ", "từ từ",
    "nhẹ nhàng", "nhanh chóng", "thật sự", "dễ dàng", "chắc chắn", "lo lắng", "sợ hãi", "buồn bã", "hạnh phúc",
    "tò mò", "hồi hộp", "bất ngờ", "dũng cảm", "chia sẻ", "giúp đỡ", "kiên nhẫn", "trung thực", "cả ngày", "ngày mai",
    "hôm qua", "buổi sáng", "buổi tối", "nước lọc", "nước sôi", "nước mắm", "dầu ăn", "bột ngọt", "hạt nêm", "đường cát",
    "muối hột", "giấm ăn", "chảo nóng", "nóng già", "để nguội", "sôi lên", "vớt ra", "đảo đều", "trộn đều", "khuấy đều",
    "trở nên", "thật là", "rất là", "chuẩn vị", "bánh mì", "cơm tấm", "bún thịt", "rau củ", "lượn sóng", "củ cải", "cà rốt",
    "luống hoa", "dịu dàng", "làm sao", "biết bao", "ghê chưa", "quá chừng", "luôn nha", "nha cả", "thật nhiều",
)


def _compounds() -> set:
    out = {tuple(c.split()) for c in _COMPOUND_WORDS}
    try:
        import ingredients
        for g in ingredients.GROUPS:
            for name in g:
                ws = name.lower().split()
                out.update(zip(ws, ws[1:]))
    except Exception:
        pass
    return {c for c in out if len(c) == 2}


_PAIRS: set | None = None


def _break_cost(prev: str, nxt: str, pairs: set, before: str = "") -> float:
    """Điểm phạt khi ngắt dòng giữa chữ `prev` và chữ `nxt` (âm: ngắt đẹp); before: chữ ngay trước `prev`."""
    a, b, z = _tok(prev), _tok(nxt), _tok(before)
    if not a or not b:
        return 0.0
    cost = 0.0
    if prev.rstrip().endswith((".", "!", "?", ",", ";", ":", "…")):
        return -6.0
    if any(ch.isdigit() for ch in a) or a in _NUM_WORDS:
        cost += 40.0 if (b in _UNIT_WORDS or any(ch.isdigit() for ch in b) or b in _NUM_WORDS) else 25.0
    if (a, b) in pairs:
        cost += 22.0
    if a in _UNIT_WORDS and z and (any(ch.isdigit() for ch in z) or z in _NUM_WORDS):
        cost += 12.0                                          # «1 kg | cà rốt»: định lượng đi liền tên nguyên liệu
    if prev[:1].isupper() and nxt[:1].isupper():              # tên riêng hai chữ («Thỏ Trắng», «Gấu Nâu»)
        cost += 18.0
    if a in _BIND_NEXT:
        cost += 10.0
    if b in _GOOD_START:
        cost -= 4.0
    return cost


def _split_phrase(ph: list, max_words: int, max_chars: int, pairs: set) -> list:
    """Chia một cụm (không có dấu câu ở giữa) thành các dòng: quy hoạch động trên chỗ ngắt."""
    n = len(ph)
    texts = [w[2] for w in ph]
    chars = len(" ".join(texts))
    if n <= max_words and chars <= max_chars:
        return [ph]
    k_min = max(-(-n // max_words), -(-chars // max_chars))
    target = chars / max(1, k_min)
    inf = float("inf")
    best = [0.0] + [inf] * n
    back = [0] * (n + 1)
    for i in range(1, n + 1):
        for j in range(max(0, i - max_words - 1), i):
            if best[j] == inf:
                continue
            cw, cc = i - j, len(" ".join(texts[j:i]))
            if cw > max_words + 1 or (cc > max_chars + 8 and cw > 1):
                continue
            c = 6.0 + (cc - target) ** 2 / 12.0
            if cw == 1:
                c += 14.0                                    # dòng một chữ
            if cw > max_words:
                c += 9.0 * (cw - max_words)
            if cc > max_chars:
                c += 2.0 * (cc - max_chars)
            if j > 0:
                c += _break_cost(texts[j - 1], texts[j], pairs, texts[j - 2] if j >= 2 else "")
            if best[j] + c < best[i]:
                best[i], back[i] = best[j] + c, j
    if best[n] == inf:                                       # không chia được theo luật: chia đều như cũ
        base, extra = divmod(n, k_min)
        out, i = [], 0
        for j in range(k_min):
            size = base + (1 if j < extra else 0)
            out.append(ph[i:i + size])
            i += size
        return [x for x in out if x]
    cuts, i = [], n
    while i > 0:
        cuts.append((back[i], i))
        i = back[i]
    return [ph[a:b] for a, b in reversed(cuts)]


def group_words(words, max_words=4, max_chars=22, until: float | None = None):
    """Chia dòng phụ đề: tách cụm theo câu/dấu phẩy/ngắt hơi/đoạn giọng, rồi chia mỗi cụm thành các dòng cân đối ở chỗ
    ngắt tự nhiên (_split_phrase; tránh dòng chỉ còn 1 chữ). words: [(start, end, text[, clip])]."""
    global _PAIRS
    phrases, cur = [], []
    for w in words:
        brk = cur and (w[0] - cur[-1][1] > 0.6
                       or (len(w) > 3 and len(cur[-1]) > 3 and w[3] != cur[-1][3])
                       or cur[-1][2].rstrip().endswith((".", "!", "?", ",")))
        if brk:
            phrases.append(cur)
            cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    lines = []
    if _PAIRS is None:
        _PAIRS = _compounds()
    pairs = _PAIRS | _brand_pairs()
    for ph in phrases:
        lines.extend(_split_phrase(ph, max_words, max_chars, pairs))
    out = []
    for i, ln in enumerate(lines):
        start = ln[0][0]
        end = ln[-1][1] + 0.35
        if i + 1 < len(lines):
            end = min(end, lines[i + 1][0][0])
        end = max(end, start + 0.3)
        if until is not None:                      # không hiện xuyên qua màn kết nối thêm (bản 47)
            end = max(min(end, until), start + 0.05)
        out.append((start, end, ln))
    return out


def ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> màu ASS &H00BBGGRR."""
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def style_line(style: str, brand_color: str = "") -> str:
    """Dòng Style của phụ đề; có màu kênh (BRAND_COLOR) thì chữ đang đọc (karaoke) / chữ (plain) theo màu kênh."""
    line = _STYLES.get(style, _STYLES["karaoke"])
    if brand_color and style in ("karaoke", "plain"):
        line = line.replace("&H0000E1FF", ass_color(brand_color))
    return line


def write_ass(words, path: str, pos_y: int | None = None, style: str = "karaoke", until: float | None = None,
              highlight: set | None = None, highlight_color: str = "#FFD43B", pop: bool = False):
    """Phụ đề kiểu CapCut: mỗi dòng 3-4 từ, chữ đang đọc sáng dần sang vàng đúng nhịp giọng (\\kf).
    pos_y: tâm phụ đề theo trục dọc (0-1280) để đè lên dải chữ gốc; None = đáy màn hình.
    highlight: các chữ (đã qua _tok, viết thường) in màu `highlight_color` — từ tiếng Anh bé học (kênh truyện bé).
    pop: mỗi cụm chữ bật lên khi hiện (\\t scale) — kênh truyện bé."""
    bs = chr(92)
    lines = group_words(words, until=until)
    pos = ("%san5%spos(360,%d)" % (bs, bs, int(pos_y))) if pos_y else ""
    with open(path, "w", encoding="utf-8") as f:
        import config
        sline = style_line(style, config.brand_text("BRAND_COLOR"))
        f.write(_HEADER.replace("{style}", sline))
        karaoke = style == "karaoke"
        primary = sline.split(",")[3].strip()                         # màu chữ của style, để trả lại sau chữ tô màu
        hi = ("{%sc%s&}" % (bs, ass_color(highlight_color)), "{%sc%s&}" % (bs, primary))
        for start, end, ln in lines:
            parts = []
            for j, wd in enumerate(ln):
                ws, we, wt = wd[0], wd[1], wd[2]
                nxt = ln[j + 1][0] if j + 1 < len(ln) else we
                cs = max(1, int(round((max(nxt, we) - ws) * 100)))
                raw = wt
                wt = wt.replace("\\", "/").replace("{", "(").replace("}", ")")   # \N, {\...} là lệnh ASS
                part = ("{%skf%d}%s" % (bs, cs, wt)) if karaoke else wt
                if highlight and _tok(raw) in highlight:
                    part = hi[0] + part + hi[1]
                parts.append(part)
            head = "{%s%sfad(60,40)}" % (pos, bs) if pos else "{%sfad(60,40)}" % bs
            if pop:                                   # kênh truyện bé: cụm chữ bật lên (86% -> 100% trong 110ms)
                head = head[:-1] + "%sfscx86%sfscy86%st(0,110,%sfscx100%sfscy100)}" % (bs, bs, bs, bs, bs)
            f.write(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{head}" + " ".join(parts) + "\n")
    return len(lines)


def _srt_ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(words, until: float | None = None) -> str:
    """Phụ đề SRT (cùng cách chia dòng với bản burn-in) để nhập vào CapCut/Premiere sửa tay."""
    out = []
    for k, (start, end, ln) in enumerate(group_words(words, until=until), 1):
        out.append(f"{k}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{' '.join(w[2] for w in ln)}\n")
    return "\n".join(out)
