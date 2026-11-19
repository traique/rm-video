"""(Kaggle) Dựng video truyện bé: 8 ảnh + 8 đoạn giọng -> 1080x1920, 30 fps.

- Độ dài mỗi cảnh THEO GIỌNG (thời gian đọc đoạn đó + đệm, tối thiểu SCENE_MIN): lời và hình luôn đổi cùng lúc.
- Ken Burns (zoom / lia) và chuyển cảnh xfade êm, dùng lại biểu thức của chế độ 📸 (slideshow.zoompan_expr, _prep).
- Thẻ tiêu đề 3 giây đầu, thẻ «sorry = xin lỗi» 2,6 giây khi từ đó được đọc lần đầu, màn kết «Hôm nay bé học» 3 giây.
- Phụ đề rải theo khoảng có tiếng của từng đoạn giọng (align.spread_voiced, VieNeu không trả mốc từ), từ tiếng Anh tô màu.
- Nhạc nền (nếu có) ~16 dB dưới giọng, tự hạ thêm khi đang đọc (sidechain như nhạc lót kênh bếp), loudness -14 LUFS.
MỘT lệnh FFmpeg cho cả hình lẫn tiếng (8 cảnh = đúng cỡ một khúc của 📸); track giọng ghép trước bằng voiceplan.compose_voice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Sequence

import align
import slideshow
import subs as subs_mod
from rmvideo.media import ff, filtergraph, renderer, voiceplan

FPS = 30
PAD = 0.7              # giây lặng sau mỗi đoạn giọng trước khi sang cảnh kế
GAP = 0.25             # giọng bắt đầu sau khi vào cảnh chừng này
INTRO = 1.4            # cảnh 1: giọng bắt đầu muộn hơn (thẻ tiêu đề đang hiện)
TITLE_UNTIL = 3.2      # thẻ tiêu đề hiện tới giây này
OUTRO = 3.4            # màn kết: các từ hôm nay + tagline
SCENE_MIN = 5.0
XFADE = 0.5
GLOSS = 2.6            # thẻ «sorry = xin lỗi» hiện chừng này giây
BGM_BELOW = 16.0       # nhạc nền thấp hơn giọng chừng này dB (trước khi tự hạ thêm lúc đang đọc)
MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up")
TRANSITIONS = ("fade", "dissolve", "smoothleft", "smoothright", "smoothup", "circleopen")
STYLE = "kids"
# Hiệu ứng «động» (19/11, FFmpeg thuần): Ken Burns mạnh hơn + nhịp thở, nảy zoom + rung máy khi có tiếng động trong lời
# (Bịch! Rầm!), hạt bokeh bay nhẹ, vignette, phụ đề bật lên (subs), tiếng whoosh khi chuyển cảnh + «ting» khi dạy từ.
SOUND_WORDS = ("bịch", "rầm", "ầm", "bùm", "đùng", "choang", "phịch", "rắc", "bốp", "xoạch", "tùm", "cốc", "ối", "oái",
               "vù", "xoẹt", "bộp", "cạch")
HIT_GAP = 0.45          # hai cú nảy cách nhau ít nhất chừng này giây
HIT_MAX = 14
BREATH = (0.010, 4.0)   # biên độ zoom «thở», chu kỳ giây
PARTICLES = 7           # hạt bokeh bay
SFX_WHOOSH_DB, SFX_DING_DB = -21.0, -17.0


RECAP_GAP = 0.5        # câu ôn từ bắt đầu sau khi vào màn kết chừng này
MAX_LEN = 87.0         # Reels nhận tới 90s, QA (job.DUR_MAX) 88s: kế hoạch tự co vào mức này (đo thật: 236 chữ -> 88,9s)
PAD_MIN, GAP_MIN = 0.4, 0.15   # đệm tối thiểu khi phải co
TEMPO_MAX = 1.15       # co đệm chưa đủ thì giọng đọc nhanh hơn, tối đa chừng này (atempo, nghe vẫn tự nhiên)


def plan(durs: Sequence[float], rnd: random.Random | None = None, recap_dur: float = 0.0,
         max_len: float = MAX_LEN) -> dict:
    """durs: độ dài TIẾNG NÓI từng cảnh -> {"scenes": [{start, end, voice_at, motion}], "transitions": [{start, dur,
    type}], "length", "outro_at", "recap_at", "factor", "pad", "gap"}. Chuyển cảnh j nằm giữa ranh giới cảnh j và
    j+1 (lấn mỗi bên XFADE/2). recap_dur: câu ôn từ đọc trong màn kết -> màn kết dài ra cho vừa.
    Dài quá max_len thì co đệm (PAD/GAP xuống PAD_MIN/GAP_MIN), vẫn quá thì giọng đọc nhanh hơn (factor ≤ TEMPO_MAX;
    compose_voice dùng atempo=factor, độ dài giọng trong kế hoạch là d/factor)."""
    seed = (rnd or random.Random(0)).random()   # cùng hạt -> cùng chuyển động dù xếp lại mấy lần
    voice = [float(d) for d in durs]
    pl = _lay(voice, seed, float(recap_dur), PAD, GAP, 1.0)
    if pl["length"] > max_len:
        pl = _lay(voice, seed, float(recap_dur), PAD_MIN, GAP_MIN, 1.0)
    f = 1.0
    for _ in range(4):                      # cảnh ngắn bị chặn dưới SCENE_MIN nên tính lặp vài vòng cho đúng
        if pl["length"] <= max_len or f >= TEMPO_MAX:
            break
        spoken = (sum(voice) + float(recap_dur)) / f
        fixed = pl["length"] - spoken
        f = min(TEMPO_MAX, f * spoken / max(1.0, max_len - fixed) + 0.002)
        pl = _lay(voice, seed, float(recap_dur), PAD_MIN, GAP_MIN, f)
    return pl


def _lay(voice: list[float], seed: float, recap_dur: float, pad: float, gap: float, f: float) -> dict:
    rnd = random.Random(seed)
    outro = max(OUTRO, RECAP_GAP + recap_dur / f + 1.0) if recap_dur else OUTRO
    scenes: list[dict] = []
    t = 0.0
    prev = ""
    for i, d in enumerate(voice):
        lead = INTRO if i == 0 else gap
        length = max(SCENE_MIN, lead + d / f + pad)
        motion = rnd.choice([m for m in MOTIONS if m != prev])
        prev = motion
        scenes.append({"start": round(t, 3), "end": round(t + length, 3), "voice_at": round(t + lead, 3),
                       "motion": motion})
        t += length
    scenes[-1]["end"] = round(t + outro, 3)                      # màn kết đè lên cảnh cuối (ảnh cảnh 8 giữ nguyên)
    trans = [{"start": round(s["end"] - XFADE / 2, 3), "dur": XFADE, "type": rnd.choice(TRANSITIONS)}
             for s in scenes[:-1]]
    return {"scenes": scenes, "transitions": trans, "length": round(t + outro, 3), "outro_at": round(t, 3),
            "recap_at": round(t + RECAP_GAP, 3), "factor": round(f, 4), "pad": pad, "gap": gap}


def _clip_bounds(pl: dict) -> list[tuple[float, float]]:
    """[(đầu, cuối)] của từng clip trên trục video: lấn sang chuyển cảnh hai bên."""
    out = []
    tr = pl["transitions"]
    for j, s in enumerate(pl["scenes"]):
        a = 0.0 if j == 0 else tr[j - 1]["start"]
        b = pl["length"] if j == len(pl["scenes"]) - 1 else tr[j]["start"] + tr[j]["dur"]
        out.append((a, b))
    return out


def _dt(textfile: str, fontsize: int, y: str, enable: str, color: str = "white", box: str = "",
        alpha: str = "") -> str:
    return (f",drawtext=fontfile={ff.FONT_PATH}:textfile={textfile}:expansion=none:fontsize={fontsize}:fontcolor={color}:"
            f"borderw={max(2, fontsize // 16)}:bordercolor=black@0.55{box}:x=(w-text_w)/2:y={y}:enable='{enable}'"
            + (f":alpha='{alpha}'" if alpha else ""))


def _fade_alpha(t0: float, t1: float, d: float = 0.35) -> str:
    return f"if(lt(t-{t0:.2f},{d}),(t-{t0:.2f})/{d},if(gt(t,{t1 - d:.2f}),({t1:.2f}-t)/{d},1))"


def overlays(pl: dict, title: str, words: Sequence[dict], glosses: Sequence[tuple[float, str, str]], channel: dict,
             W: int, H: int, work: str) -> str:
    """Chuỗi bộ lọc chữ: thẻ tiêu đề, thẻ nghĩa từ, màn kết. words: [{en, vi}] của đề; glosses: [(giây, en, vi)]."""
    k = H / 1280
    out = ""
    T = pl["length"]
    # thẻ tiêu đề 0..TITLE_UNTIL: dải tối mờ + tiêu đề (tối đa 3 dòng, cỡ theo font thật) + tên kênh nhỏ
    lines, fs = renderer.title_layout(title or "", W, H)
    if lines:
        lh = int(fs * 1.25)
        files = renderer.write_lines("\n".join(lines), work, "kt")
        top = int(H * 0.30)
        out += (f",drawbox=x=0:y={top - int(28 * k)}:w=iw:h={lh * len(files) + int(56 * k)}:color=black@0.38:t=fill:"
                f"enable='between(t,0,{TITLE_UNTIL})'")
        al = _fade_alpha(0.0, TITLE_UNTIL)
        for i, p in enumerate(files):
            out += _dt(p, fs, str(top + i * lh), f"between(t,0,{TITLE_UNTIL})", "white", "", al)
        name = channel.get("name") or ""
        if name:
            nf = renderer.write_lines(f"{name} · {channel.get('world', '')}".strip(" ·"), work, "kn")
            out += _dt(nf[0], int(fs * 0.55), str(top + len(files) * lh + int(6 * k)), f"between(t,0,{TITLE_UNTIL})",
                       "0xFFE082", "", al)
    # thẻ nghĩa: «sorry = xin lỗi» ở 11% chiều cao, hộp xanh, khi từ được đọc lần đầu
    for j, (at, en, vi) in enumerate(glosses):
        gf = renderer.write_lines(f"{en}  =  {vi}", work, f"kg{j}")
        a, b = round(at, 2), round(min(T, at + GLOSS), 2)
        out += _dt(gf[0], int(44 * k), f"{int(H * 0.11)}", f"between(t,{a},{b})", "white",
                   f":box=1:boxcolor=0x1B7F3B@0.88:boxborderw={int(18 * k)}", _fade_alpha(a, b, 0.25))
    # màn kết: tối mờ + «Hôm nay bé học» + từng từ + tagline
    o = pl["outro_at"]
    out += f",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.55:t=fill:enable='gte(t,{o:.2f})'"
    al = _fade_alpha(o, T, 0.4)
    hf = renderer.write_lines("Hôm nay bé học", work, "kh")
    y0 = int(H * 0.26)
    out += _dt(hf[0], int(50 * k), str(y0), f"gte(t,{o:.2f})", "0xFFE082", "", al)
    wl = int(78 * k)
    for i, w in enumerate(words):
        wf = renderer.write_lines(f"{w['en']}  =  {w['vi']}", work, f"kw{i}")
        out += _dt(wf[0], int(60 * k), str(y0 + int(90 * k) + i * wl), f"gte(t,{o:.2f})", "white", "", al)
    tag = channel.get("tagline") or ""
    if tag:
        tl, tfs = renderer.title_layout(tag, W, H)
        tfiles = renderer.write_lines("\n".join(tl), work, "kz")
        tfs = int(min(tfs, 36 * k))
        for i, p in enumerate(tfiles):
            out += _dt(p, tfs, str(int(H * 0.80) + i * int(tfs * 1.3)), f"gte(t,{o:.2f})", "0xB3E5FC", "", al)
    return out


def hits_from_words(words: Sequence[tuple], gap: float = HIT_GAP, limit: int = HIT_MAX) -> list[float]:
    """Mốc giây các từ tượng thanh / thán từ trong phụ đề (Bịch! Rầm! Ối!) -> nảy zoom + rung máy lúc đó."""
    import re
    out: list[float] = []
    for start, _end, tok, *_ in words:
        t = re.sub(r"[^\w]", "", str(tok).lower())            # giữ dấu tiếng Việt (align.norm bỏ dấu)
        if t in SOUND_WORDS and (not out or start - out[-1] >= gap):
            out.append(round(float(start), 3))
    return out[:limit]


def video_graph(pl: dict, sizes: Sequence[tuple[int, int]], W: int, H: int, fps: int, avail: set,
                hits: Sequence[float] = (), fx: str = "full") -> str:
    """[0..n-1:v] ảnh -> zoompan từng cảnh (Ken Burns + thở + nảy/rung theo tiếng động) -> xfade chuỗi -> [vx]."""
    parts = []
    bounds = _clip_bounds(pl)
    motion_on = fx != "off"
    for j, (a, b) in enumerate(bounds):
        w, h = sizes[j]
        nf = max(2, int(round((b - a) * fps)) + 1)
        pulses = list(hits) if motion_on else []
        z, x, y = slideshow.zoompan_expr(pl["scenes"][j]["motion"], "calm", nf, a, pulses, pulses, b,
                                         1.25 if motion_on else 1.0)
        if motion_on:
            z = f"({z})+{BREATH[0]}*sin(2*PI*({a:.4f}+on/{fps})/{BREATH[1]})"
        parts.append(f"{slideshow._prep(j, w, h, W, H)},zoompan=z='{z}':x='{x}':y='{y}':d={nf}:s={W}x{H}:fps={fps},"
                     f"trim=duration={b - a:.4f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[c{j}]")
    cur = "[c0]"
    for j, tr in enumerate(pl["transitions"]):
        typ = tr["type"] if tr["type"] in avail else "fade"
        parts.append(f"{cur}[c{j + 1}]xfade=transition={typ}:duration={tr['dur']:.3f}:offset={tr['start']:.3f}[x{j}]")
        cur = f"[x{j}]"
    tail = ",vignette=angle=PI/5.5" if motion_on else ""
    return ";".join(parts) + f";{cur}null{tail}[vx]"


def sprites(work: str) -> list[str]:
    """Hai hạt bokeh (PNG có alpha, PIL): đĩa mềm và đĩa nhỏ sáng hơn."""
    from PIL import Image, ImageDraw, ImageFilter
    out = []
    for name, size, alpha, blur in (("bokeh_a", 96, 150, 10), ("bokeh_b", 56, 200, 5)):
        im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        d = ImageDraw.Draw(im)
        m = size // 6
        d.ellipse((m, m, size - m, size - m), fill=(255, 250, 225, alpha))
        im = im.filter(ImageFilter.GaussianBlur(blur))
        p = os.path.join(work, f"{name}.png")
        im.save(p)
        out.append(p)
    return out


def particle_graph(first_input: int, n_sprites: int, W: int, H: int, rnd: random.Random, n: int = PARTICLES) -> str:
    """[vx] + hạt bokeh bay lên lững lờ, đung đưa ngang -> [vp]. Ảnh hạt là input first_input.. (loop)."""
    k = H / 1920
    parts = []
    per = [0] * n_sprites
    for i in range(n):
        per[i % n_sprites] += 1
    for si in range(n_sprites):
        labels = "".join(f"[s{si}_{m}]" for m in range(per[si]))
        parts.append(f"[{first_input + si}:v]format=rgba,split={per[si]}{labels}")
    cur = "[vx]"
    for i in range(n):
        si, m = i % n_sprites, i // n_sprites
        sc = round(rnd.uniform(0.5, 1.3) * k, 3)
        v = rnd.uniform(22, 48) * k                    # px/giây bay lên
        x0 = rnd.uniform(0.05, 0.9) * W
        amp = rnd.uniform(18, 46) * k
        period = rnd.uniform(3.5, 7.0)
        off = rnd.uniform(0, H)
        ph = rnd.uniform(0, 6.28)
        parts.append(f"[s{si}_{m}]scale=iw*{sc}:ih*{sc}[q{i}]")
        parts.append(f"{cur}[q{i}]overlay=x='{x0:.0f}+{amp:.0f}*sin(2*PI*t/{period:.2f}+{ph:.2f})':"
                     f"y='{H}-mod(t*{v:.1f}+{off:.0f},{H + 160})':eval=frame:format=auto[o{i}]")
        cur = f"[o{i}]"
    return ";".join(parts) + f";{cur}null[vp]"


async def sfx_files(work: str) -> dict[str, str]:
    """Tiếng động tự tạo (không cần file bản quyền): whoosh (nhiễu hồng vuốt) và ting (sine tắt dần + bội âm)."""
    wh = os.path.join(work, "sfx_whoosh.wav")
    await ff._run_ffmpeg("-y", "-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:d=0.6", "-af",
                         "lowpass=f=1600,highpass=f=180,volume='if(lt(t,0.22),t/0.22,max(0,(0.6-t)/0.38))':eval=frame", wh)
    dg = os.path.join(work, "sfx_ding.wav")
    await ff._run_ffmpeg("-y", "-f", "lavfi", "-i", "sine=f=1318:r=44100:d=0.8", "-f", "lavfi", "-i",
                         "sine=f=2637:r=44100:d=0.8", "-filter_complex",
                         "[0:a]volume='0.6*exp(-4.5*t)':eval=frame[a];[1:a]volume='0.18*exp(-7*t)':eval=frame[b];"
                         "[a][b]amix=inputs=2:normalize=0[o]", "-map", "[o]", dg)
    return {"whoosh": wh, "ding": dg}


def sfx_graph(idx: dict[str, int], events: dict[str, list[float]]) -> str:
    """Các input tiếng động -> [sfx]: mỗi mốc một bản adelay, trộn lại."""
    parts, labels = [], []
    for name, times in events.items():
        if not times or name not in idx:
            continue
        db = SFX_WHOOSH_DB if name == "whoosh" else SFX_DING_DB
        outs = "".join(f"[{name}{i}]" for i in range(len(times)))
        parts.append(f"[{idx[name]}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={db:.1f}dB,"
                     f"asplit={len(times)}{outs}")
        for i, t in enumerate(times):
            ms = int(max(0.0, t) * 1000)
            parts.append(f"[{name}{i}]adelay={ms}|{ms}[{name}d{i}]")
            labels.append(f"[{name}d{i}]")
    if not labels:
        return ""
    return ";".join(parts) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[sfx]"


def audio_graph(voice_idx: int, music_idx: int | None, gain_db: float, total: float, sfx: bool = False) -> str:
    """[vx] đã có; thêm phần tiếng -> [a]: giọng (+ tiếng động [sfx]) (+ nhạc nền tự hạ khi đang đọc) -> loudnorm."""
    vo = f"[{voice_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo"
    if sfx:
        vo = f"{vo}[vo0];[vo0][sfx]amix=inputs=2:duration=first:normalize=0"
    if music_idx is None:
        return f"{vo},{filtergraph.LOUDNORM},{filtergraph.AOUT},apad=whole_dur={total:.2f},atrim=duration={total:.2f}[a]"
    return (f"{vo},asplit=2[vo][key];"
            f"[{music_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{total:.2f},asetpts=PTS-STARTPTS,"
            f"highpass=f=150,lowpass=f=9000,volume={gain_db:.1f}dB,afade=t=in:st=0:d=1.5,"
            f"afade=t=out:st={max(0.0, total - 2.5):.2f}:d=2.5[bed0];"
            f"[bed0][key]{filtergraph.VOICE_BED_DUCK}[bed];"
            f"[vo][bed]amix=inputs=2:duration=first:normalize=0,{filtergraph.LOUDNORM},{filtergraph.AOUT},"
            f"apad=whole_dur={total:.2f},atrim=duration={total:.2f}[a]")


async def subtitle_words(clips: Sequence[dict], pl: dict, texts: Sequence[str],
                         recap: tuple[dict, str] | None = None) -> list:
    """[(start, end, chữ, cảnh)] rải theo khoảng có tiếng của từng đoạn giọng (đo năng lượng, không cần mốc từ)."""
    out = []
    items = [(cl, sc["voice_at"], text) for cl, sc, text in zip(clips, pl["scenes"], texts)]
    if recap and recap[0].get("path"):
        items.append((recap[0], pl["recap_at"], recap[1]))
    f = float(pl.get("factor") or 1.0)        # giọng đọc nhanh hơn: mốc có tiếng cũng dồn lại theo
    for k, (cl, at, text) in enumerate(items):
        if not cl.get("path") or not text.strip():
            continue
        ivs = await voiceplan.voiced_intervals(cl["path"]) or []
        dur = float(cl.get("dur") or 0.0) or (ivs[-1][1] / f if ivs else 2.0)
        shifted = [(a / f + at, b / f + at) for a, b in ivs]
        out += [(a, b, w, k) for a, b, w in align.spread_voiced(sub_tokens(text), shifted, at, at + dur + 0.05)]
    return out


QUOTES = "«»\"“”‘’'"


def sub_tokens(text: str) -> list[str]:
    """Chữ để rải phụ đề: bỏ dấu ngoặc kép (model hay viết «...» quanh lời thoại; đo thật: chữ «đi.»» lạc sang cụm sau)."""
    out = []
    for w in text.split():
        w = w.strip(QUOTES)
        if w.strip(".,;:!?…"):
            out.append(w)
        elif out:
            out[-1] += w                  # dấu câu lẻ sau ngoặc («nha.» -> nha.) dính vào chữ trước
    return out


def gloss_times(words_timed: Sequence[tuple], brief_words: Sequence[dict]) -> list[tuple[float, str, str]]:
    """Lúc mỗi từ tiếng Anh được đọc lần đầu (theo mốc phụ đề) -> thẻ nghĩa; không thấy thì không hiện thẻ."""
    out = []
    toks = [(w, [align.norm(p) for p in w["en"].split()]) for w in brief_words]
    seq = [(a, align.norm(t)) for a, _, t, *_ in words_timed]
    for w, parts in toks:
        for i in range(len(seq) - len(parts) + 1):
            if [x for _, x in seq[i:i + len(parts)]] == parts:
                out.append((seq[i][0], w["en"], w["vi"]))
                break
    return sorted(out)


async def render(images: Sequence[str], clips: Sequence[dict], texts: Sequence[str], brief: dict, out: str, tmp: str,
                 music: str | None = None, title: str = "", channel: dict | None = None, W: int = 1080, H: int = 1920,
                 fps: int = FPS, rnd: random.Random | None = None, recap: tuple[dict, str] | None = None,
                 fx: str = "full") -> dict:
    """clips: [{"path": wav}] theo thứ tự cảnh (độ dài tiếng nói tự đo). recap: (clip, lời) câu ôn từ đọc trong màn
    kết. fx: full (chuyển động + hạt + tiếng động) | motion (chỉ chuyển động, phụ đề bật) | off (như 1.1.0).
    Trả {"length", "plan", "srt", "ass", "hits", "sfx"}."""
    channel = channel or {}
    rnd = rnd or random.Random(0)
    fx = fx if fx in ("full", "motion", "off") else "full"
    work = os.path.join(tmp, "kids_render")
    os.makedirs(work, exist_ok=True)
    durs = [await voiceplan.speech_duration(c["path"]) if c.get("path") else 0.0 for c in clips]
    recap_dur = 0.0
    if recap and recap[0].get("path"):
        recap_dur = await voiceplan.speech_duration(recap[0]["path"])
    pl = plan(durs, rnd, recap_dur)
    f = pl["factor"]
    for c, d in zip(clips, durs):
        c["dur"] = d / f                     # độ dài sau khi đọc nhanh hơn (nếu phải co)
    if recap_dur:
        recap[0]["dur"] = recap_dur / f      # type: ignore[index]
    total = pl["length"]
    if f > 1.0 or pl["pad"] < PAD:
        logging.info("Truyện bé: lời dài, co đệm còn %.2fs và đọc nhanh hơn x%.3f để vừa %.0fs", pl["pad"], f, MAX_LEN)
    vplan = [{"start": s["voice_at"], "factor": f, "end": s["voice_at"] + d / f} for s, d in zip(pl["scenes"], durs)]
    all_clips = list(clips)
    if recap_dur:
        all_clips.append(recap[0])  # type: ignore[index]
        vplan.append({"start": pl["recap_at"], "factor": f, "end": pl["recap_at"] + recap_dur / f})
    voice = os.path.join(work, "voice.wav")
    await voiceplan.compose_voice(all_clips, vplan, voice, total)
    words = await subtitle_words(clips, pl, texts, recap)
    from rmvideo.kids import story as story_mod
    ass = os.path.join(work, "subs.ass")
    hi = story_mod.english_tokens(brief)
    subs_mod.write_ass(words, ass, None, STYLE, None, highlight=hi, pop=fx != "off")
    srt = subs_mod.to_srt(words)
    glosses = gloss_times(words, brief.get("words", []))
    sizes = [await asyncio.to_thread(slideshow.image_size, p) for p in images]
    avail = await asyncio.to_thread(slideshow.xfade_transitions)
    hits = hits_from_words(words) if fx != "off" else []
    fc = video_graph(pl, sizes, W, H, fps, avail, hits, fx)
    inputs: list[str] = []
    for p in images:
        inputs += ["-i", p]
    voice_idx = len(images)
    inputs += ["-i", voice]
    next_idx = voice_idx + 1
    top = "[vx]"
    if fx == "full":                                   # hạt bokeh bay (ảnh PNG loop làm input)
        sp = await asyncio.to_thread(sprites, work)
        for p in sp:
            inputs += ["-loop", "1", "-framerate", str(fps), "-i", p]
        fc += ";" + particle_graph(next_idx, len(sp), W, H, rnd)
        next_idx += len(sp)
        top = "[vp]"
    fc += f";{top}" + overlays(pl, title, brief.get("words", []), glosses, channel, W, H, work).lstrip(",")
    fc += (f",subtitles=filename={ass}:fontsdir={ff.ASSETS_DIR},fade=t=in:st=0:d=0.3,"
           f"fade=t=out:st={max(0.0, total - 0.5):.2f}:d=0.5,format=yuv420p[v];")
    events: dict[str, list[float]] = {}
    if fx == "full":                                   # whoosh lúc chuyển cảnh, ting lúc dạy từ
        sf = await sfx_files(work)
        sidx = {}
        for name, p in sf.items():
            inputs += ["-i", p]
            sidx[name] = next_idx
            next_idx += 1
        events = {"whoosh": [tr["start"] for tr in pl["transitions"]], "ding": [g[0] for g in glosses]}
        sg = sfx_graph(sidx, events)
        if sg:
            fc += sg + ";"
        else:
            events = {}
    music_idx = None
    gain = 0.0
    if music and os.path.exists(music):
        music_idx = next_idx
        inputs += ["-stream_loop", "-1", "-i", music]
        vo_db = await renderer._mean_db(["-i", voice])
        mu_db = await renderer._mean_db(["-t", f"{total:.2f}", "-i", music], "highpass=f=150,lowpass=f=9000,")
        if mu_db is None or mu_db < -70:
            music_idx = None
            inputs = inputs[:-4]
        else:
            gain = max(-45.0, min(6.0, ((vo_db if vo_db is not None and vo_db > -70 else -20.0) - BGM_BELOW) - mu_db))
    fc += audio_graph(voice_idx, music_idx, gain, total, sfx=bool(events))
    fc = await renderer.two_pass_loudness(fc, inputs, total)
    logging.info("Truyện bé: dựng %d cảnh, %.1fs, nhạc nền %s, hiệu ứng %s (%d cú nảy, %d tiếng động)", len(images),
                 total, f"{gain:+.0f} dB" if music_idx is not None else "không", fx, len(hits),
                 sum(len(v) for v in events.values()))
    await ff._run_ffmpeg("-y", *ff.thread_args(), *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                         *renderer.encode_args(H, fps), "-t", f"{total:.3f}", out)
    return {"length": total, "plan": pl, "srt": srt, "ass": ass, "music_gain_db": gain if music_idx is not None else None,
            "hits": hits, "sfx": {k: len(v) for k, v in events.items()}, "fx": fx}


async def cover(video: str, out_dir: str, at: float = 1.6) -> tuple[str, str]:
    """Ảnh bìa (khung lúc thẻ tiêu đề đang hiện) + ảnh thu nhỏ cho Telegram; cùng tên file mà delivery tìm."""
    cov, thumb = os.path.join(out_dir, "cover.jpg"), os.path.join(out_dir, "thumb.jpg")
    await ff._run_ffmpeg("-y", "-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-q:v", "3", cov)
    await ff._run_ffmpeg("-y", "-i", cov, "-vf", "scale=360:-2", "-q:v", "5", thumb)
    return cov, thumb
