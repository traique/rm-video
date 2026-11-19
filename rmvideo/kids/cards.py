"""(Kaggle) Thẻ chữ cho truyện bé vẽ bằng PIL (bảng trắng bo góc, bóng mềm) -> PNG có alpha, FFmpeg overlay lên video.

19/11: màn kết trước đây phủ tối 55% rồi mờ về đen — nhìn như màn hình đen, xấu trên Reels (khung cuối còn lộ khi lặp).
Giờ: nền sáng giữ nguyên, bảng trắng «Hôm nay bé học» với từng từ trong viên vàng, tên kênh; thẻ tiêu đề cùng kiểu.
Font: Be Vietnam Pro ExtraBold của bộ nhận diện (rmvideo/media/ff.FONT_PATH), có đủ dấu tiếng Việt."""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from rmvideo.media import ff

WHITE = (255, 255, 255, 236)
INK = (46, 38, 60, 255)            # chữ tím than đậm, dịu hơn đen
ORANGE = (255, 122, 48, 255)
YELLOW = (255, 212, 59, 255)
BLUE = (37, 122, 196, 255)
SHADOW = (0, 0, 0, 70)


def _font(size: int) -> Any:
    from PIL import ImageFont
    try:
        return ImageFont.truetype(ff.FONT_PATH, max(8, int(size)))
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: Any, text: str, font: Any, max_w: int) -> list[str]:
    """Xuống dòng theo bề rộng đo bằng font thật."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip()
        if draw.textlength(cand, font=font) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _panel(size: tuple[int, int], radius: int, fill: tuple = WHITE, shadow: int = 0) -> Any:
    """Ảnh RGBA có bảng bo góc (+ bóng mềm phía dưới nếu shadow > 0)."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    pad = shadow * 3
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((pad, pad + shadow, pad + w, pad + h + shadow), radius=radius, fill=SHADOW)
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(shadow)))
    ImageDraw.Draw(im).rounded_rectangle((pad, pad, pad + w, pad + h), radius=radius, fill=fill)
    return im, pad


def title_card(title: str, channel: dict, W: int, H: int, path: str) -> str:
    """Thẻ tiêu đề: bảng trắng bo góc, tiêu đề tím than (tối đa 3 dòng), dòng tên kênh cam."""
    from PIL import ImageDraw
    k = W / 1080
    pw = int(W * 0.84)
    fs = int(60 * k)
    font = _font(fs)
    tmp = _panel((10, 10), 4)[0]
    d0 = ImageDraw.Draw(tmp)
    lines = _wrap(d0, title.strip(), font, pw - int(80 * k))
    while len(lines) > 3 and fs > 30 * k:
        fs = int(fs * 0.9)
        font = _font(fs)
        lines = _wrap(d0, title.strip(), font, pw - int(80 * k))
    lh = int(fs * 1.22)
    name = " · ".join(x for x in (channel.get("name") or "", channel.get("world") or "") if x)
    sf = _font(int(fs * 0.5))
    ph = int(44 * k) + lh * len(lines) + (int(fs * 0.5 * 1.5) if name else 0) + int(36 * k)
    im, pad = _panel((pw, ph), int(40 * k), WHITE, int(10 * k))
    d = ImageDraw.Draw(im)
    y = pad + int(36 * k)
    for ln in lines:
        tw = d.textlength(ln, font=font)
        d.text((pad + (pw - tw) / 2, y), ln, font=font, fill=INK)
        y += lh
    if name:
        tw = d.textlength(name, font=sf)
        d.text((pad + (pw - tw) / 2, y + int(6 * k)), name, font=sf, fill=ORANGE)
    im.save(path)
    return path


def end_card(words: Sequence[dict], channel: dict, W: int, H: int, path: str) -> str:
    """Màn kết: bảng trắng «Hôm nay bé học», mỗi từ: viên vàng chữ Anh + «=» + nghĩa; dưới là tagline / tên kênh."""
    from PIL import ImageDraw
    k = W / 1080
    pw = int(W * 0.86)
    hf = _font(int(56 * k))
    wf = _font(int(58 * k))
    vf = _font(int(50 * k))
    tf = _font(int(32 * k))
    tmp = _panel((10, 10), 4)[0]
    d0 = ImageDraw.Draw(tmp)
    tag = (channel.get("tagline") or channel.get("name") or "").strip()
    tag_lines = _wrap(d0, tag, tf, pw - int(80 * k))[:2] if tag else []
    row_h = int(96 * k)
    ph = (int(40 * k) + int(56 * k * 1.3) + int(20 * k) + row_h * max(1, len(words)) + int(16 * k)
          + (len(tag_lines) * int(32 * k * 1.35) + int(16 * k) if tag_lines else 0) + int(28 * k))
    im, pad = _panel((pw, ph), int(44 * k), WHITE, int(12 * k))
    d = ImageDraw.Draw(im)
    y = pad + int(40 * k)
    head = "Hôm nay bé học"
    tw = d.textlength(head, font=hf)
    d.text((pad + (pw - tw) / 2, y), head, font=hf, fill=ORANGE)
    y += int(56 * k * 1.3) + int(20 * k)
    for w in words:
        en, vi = str(w.get("en", "")), str(w.get("vi", ""))
        en_w = d.textlength(en, font=wf)
        eq_w = d.textlength("=", font=vf)
        vi_w = d.textlength(vi, font=vf)
        pill_w = en_w + int(44 * k)
        gap = int(22 * k)
        total = pill_w + gap + eq_w + gap + vi_w
        x = pad + (pw - total) / 2
        pill_h = int(74 * k)
        py = y + (row_h - pill_h) / 2
        d.rounded_rectangle((x, py, x + pill_w, py + pill_h), radius=int(pill_h / 2), fill=YELLOW)
        d.text((x + int(22 * k), py + (pill_h - wf.size) / 2 - int(4 * k)), en, font=wf, fill=INK)
        x2 = x + pill_w + gap
        d.text((x2, y + (row_h - vf.size) / 2 - int(2 * k)), "=", font=vf, fill=BLUE)
        d.text((x2 + eq_w + gap, y + (row_h - vf.size) / 2 - int(2 * k)), vi, font=vf, fill=INK)
        y += row_h
    if tag_lines:
        y += int(16 * k)
        for ln in tag_lines:
            tw = d.textlength(ln, font=tf)
            d.text((pad + (pw - tw) / 2, y), ln, font=tf, fill=BLUE)
            y += int(32 * k * 1.35)
    im.save(path)
    return path


def gloss_pill(en: str, vi: str, W: int, H: int, path: str) -> str:
    """Thẻ nghĩa lúc từ được đọc: viên vàng chữ Anh + bảng trắng «= nghĩa», cùng kiểu với bảng kết (thay hộp xanh drawtext)."""
    from PIL import Image, ImageDraw
    k = W / 1080
    wf, vf = _font(int(60 * k)), _font(int(52 * k))
    tmp = Image.new("RGBA", (10, 10))
    d0 = ImageDraw.Draw(tmp)
    en_w, vi_w = d0.textlength(en, font=wf), d0.textlength(f"=  {vi}", font=vf)
    pad_x, gap = int(30 * k), int(20 * k)
    ph = int(96 * k)
    pw = int(en_w + 2 * pad_x + gap + vi_w + pad_x)
    im, pad = _panel((pw, ph), int(ph / 2), WHITE, int(8 * k))
    d = ImageDraw.Draw(im)
    pill_w = en_w + 2 * pad_x - int(6 * k)
    d.rounded_rectangle((pad + int(6 * k), pad + int(8 * k), pad + int(6 * k) + pill_w, pad + ph - int(8 * k)),
                        radius=int(ph / 2), fill=YELLOW)
    d.text((pad + pad_x, pad + (ph - wf.size) / 2 - int(4 * k)), en, font=wf, fill=INK)
    d.text((pad + pad_x + en_w + pad_x + gap - int(6 * k), pad + (ph - vf.size) / 2 - int(2 * k)), f"=  {vi}", font=vf,
           fill=INK)
    im.save(path)
    return path


def make_cards(title: str, words: Sequence[dict], channel: dict, W: int, H: int, work: str) -> dict[str, str]:
    os.makedirs(work, exist_ok=True)
    return {"title": title_card(title, channel, W, H, os.path.join(work, "card_title.png")),
            "end": end_card(words, channel, W, H, os.path.join(work, "card_end.png"))}
