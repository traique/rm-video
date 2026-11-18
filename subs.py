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


def group_words(words, max_words=4, max_chars=22, until: float | None = None):
    """Chia dòng phụ đề CÂN ĐỐI: tách cụm theo câu/dấu phẩy/ngắt hơi/đoạn giọng, rồi chia mỗi cụm
    thành các dòng đều nhau (tránh dòng chỉ còn 1 chữ). words: [(start, end, text[, clip])]."""
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
    pairs = _brand_pairs()
    for ph in phrases:
        n = len(ph)
        chars = len(" ".join(x[2] for x in ph))
        k = max(-(-n // max_words), -(-chars // max_chars))     # số dòng tối thiểu
        k = min(k, n)
        base, extra = divmod(n, k)
        i = 0
        chunk = []
        for j in range(k):                                     # chia đều: 5 từ -> 3+2, không 4+1
            size = base + (1 if j < extra else 0)
            chunk.append(ph[i:i + size])
            i += size
        lines.extend(_keep_pairs(chunk, pairs) if pairs else chunk)
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
              highlight: set | None = None, highlight_color: str = "#FFD43B"):
    """Phụ đề kiểu CapCut: mỗi dòng 3-4 từ, chữ đang đọc sáng dần sang vàng đúng nhịp giọng (\\kf).
    pos_y: tâm phụ đề theo trục dọc (0-1280) để đè lên dải chữ gốc; None = đáy màn hình.
    highlight: các chữ (đã qua _tok, viết thường) in màu `highlight_color` — từ tiếng Anh bé học (kênh truyện bé)."""
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
