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
# Mở đầu «móc» (19/11): 3 giây đầu là ảnh cảnh biến cố + câu móc (câu hỏi/câu cảm) đọc ngay + chữ to, zoom nhanh + whoosh;
# thẻ tiêu đề lùi ra sau, ngắn lại; lời kể bắt đầu sớm hơn.
HOOK_LEAD, HOOK_PAD, HOOK_MIN = 0.25, 0.45, 2.6
HOOK_IMAGE = 2         # ảnh cảnh 3 (chuyện xảy ra) dùng cho mở đầu khi không chọn được theo lời (story.hook_scene)
HOOK_STRENGTH = 1.7    # zoom mạnh hơn cảnh thường
HOOK_ZOOM = 0.22       # clip móc cắt chặt hơn cảnh (zoom thêm chừng này): cùng ảnh nhưng không thấy y hệt khung lúc cảnh đó chiếu
STORY_LEAD = 0.9       # cảnh 1 (sau móc): lời bắt đầu sau chừng này
TITLE_LEN = 2.2        # thẻ tiêu đề hiện chừng này giây (từ lúc hết móc)
MAX_LEN = 87.0         # Reels nhận tới 90s, QA (job.DUR_MAX) 88s: kế hoạch tự co vào mức này (đo thật: 236 chữ -> 88,9s)
PAD_MIN, GAP_MIN = 0.4, 0.15   # đệm tối thiểu khi phải co
TEMPO_MAX = 1.15       # co đệm chưa đủ thì giọng đọc nhanh hơn, tối đa chừng này (atempo, nghe vẫn tự nhiên)
TEMPO_HARD = 1.22      # đường cùng (đo thật 19/11: 1,15 vẫn 91,6 s -> QA loại cả job 13 phút GPU): nhanh thêm chút còn hơn hỏng


def plan(durs: Sequence[float], rnd: random.Random | None = None, recap_dur: float = 0.0,
         max_len: float = MAX_LEN, hook_dur: float = 0.0) -> dict:
    """durs: độ dài TIẾNG NÓI từng cảnh -> {"scenes": [{start, end, voice_at, motion}], "transitions": [{start, dur,
    type}], "length", "outro_at", "recap_at", "factor", "pad", "gap"}. Chuyển cảnh j nằm giữa ranh giới cảnh j và
    j+1 (lấn mỗi bên XFADE/2). recap_dur: câu ôn từ đọc trong màn kết -> màn kết dài ra cho vừa.
    Dài quá max_len thì co đệm (PAD/GAP xuống PAD_MIN/GAP_MIN), vẫn quá thì giọng đọc nhanh hơn (factor ≤ TEMPO_MAX;
    compose_voice dùng atempo=factor, độ dài giọng trong kế hoạch là d/factor)."""
    seed = (rnd or random.Random(0)).random()   # cùng hạt -> cùng chuyển động dù xếp lại mấy lần
    voice = [float(d) for d in durs]
    hd = float(hook_dur or 0.0)
    pl = _lay(voice, seed, float(recap_dur), PAD, GAP, 1.0, hd)
    if pl["length"] > max_len:
        pl = _lay(voice, seed, float(recap_dur), PAD_MIN, GAP_MIN, 1.0, hd)
    f = 1.0
    for _ in range(4):                      # cảnh ngắn bị chặn dưới SCENE_MIN nên tính lặp vài vòng cho đúng
        if pl["length"] <= max_len or f >= TEMPO_MAX:
            break
        spoken = (sum(voice) + float(recap_dur) + hd) / f
        fixed = pl["length"] - spoken
        f = min(TEMPO_MAX, f * spoken / max(1.0, max_len - fixed) + 0.002)
        pl = _lay(voice, seed, float(recap_dur), PAD_MIN, GAP_MIN, f, hd)
    if pl["length"] > max_len:              # vẫn dài: nhanh tới TEMPO_HARD (một lần, đủ thì thôi)
        spoken = (sum(voice) + float(recap_dur) + hd) / f
        fixed = pl["length"] - spoken
        f = min(TEMPO_HARD, f * spoken / max(1.0, max_len - fixed) + 0.002)
        pl = _lay(voice, seed, float(recap_dur), PAD_MIN, GAP_MIN, f, hd)
    return pl


def _lay(voice: list[float], seed: float, recap_dur: float, pad: float, gap: float, f: float,
         hook_dur: float = 0.0) -> dict:
    """clips = [móc?] + 8 cảnh; scenes = 8 cảnh truyện; hook = clip móc (ảnh cảnh 3, zoom nhanh) hoặc None."""
    rnd = random.Random(seed)
    outro = max(OUTRO, RECAP_GAP + recap_dur / f + 1.0) if recap_dur else OUTRO
    clips: list[dict] = []
    t = 0.0
    prev = ""
    hook: dict | None = None
    hook_end = 0.0
    if hook_dur > 0:
        hook_end = round(max(HOOK_MIN, HOOK_LEAD + hook_dur / f + HOOK_PAD), 3)
        hook = {"start": 0.0, "end": hook_end, "voice_at": HOOK_LEAD, "motion": "zoom_in", "hook": True}
        clips.append(hook)
        t = hook_end
        prev = "zoom_in"
    for i, d in enumerate(voice):
        lead = (STORY_LEAD if hook else INTRO) if i == 0 else gap
        length = max(SCENE_MIN, lead + d / f + pad)
        motion = rnd.choice([m for m in MOTIONS if m != prev])
        prev = motion
        clips.append({"start": round(t, 3), "end": round(t + length, 3), "voice_at": round(t + lead, 3),
                      "motion": motion})
        t += length
    clips[-1]["end"] = round(t + outro, 3)                       # màn kết đè lên cảnh cuối (ảnh cảnh 8 giữ nguyên)
    trans = [{"start": round(c["end"] - XFADE / 2, 3), "dur": XFADE, "type": rnd.choice(TRANSITIONS)}
             for c in clips[:-1]]
    if hook:
        trans[0] = {"start": round(hook_end - 0.15, 3), "dur": 0.3, "type": "smoothup"}   # vào truyện dứt khoát
    scenes = clips[1:] if hook else clips
    return {"scenes": scenes, "clips": clips, "hook": hook, "title_at": hook_end,
            "transitions": trans, "length": round(t + outro, 3), "outro_at": round(t, 3),
            "recap_at": round(t + RECAP_GAP, 3), "factor": round(f, 4), "pad": pad, "gap": gap}


def _clip_bounds(pl: dict) -> list[tuple[float, float]]:
    """[(đầu, cuối)] của từng clip ([móc?] + 8 cảnh) trên trục video: lấn sang chuyển cảnh hai bên."""
    out = []
    tr = pl["transitions"]
    clips = pl.get("clips") or pl["scenes"]
    for j, s in enumerate(clips):
        a = 0.0 if j == 0 else tr[j - 1]["start"]
        b = pl["length"] if j == len(clips) - 1 else tr[j]["start"] + tr[j]["dur"]
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
             W: int, H: int, work: str, hook_text: str = "") -> str:
    """Chuỗi bộ lọc chữ (drawtext): chữ móc to (3 giây đầu), thẻ nghĩa từ, sáng nhẹ nền lúc màn kết. Thẻ tiêu đề và
    bảng «Hôm nay bé học» là ảnh PNG (cards.py) overlay trong render(). glosses: [(giây, en, vi)]."""
    k = H / 1280
    out = ""
    hook = pl.get("hook")
    if hook and hook_text:
        hl, hfs = renderer.title_layout(hook_text, W, H)
        hfs = int(min(hfs * 1.25, 92 * k))
        hfiles = renderer.write_lines("\n".join(hl), work, "khk")
        a, b = 0.15, round(hook["end"] - 0.1, 2)
        al = _fade_alpha(a, b, 0.2)
        y0 = int(H * 0.17)
        for i, p in enumerate(hfiles):
            out += _dt(p, hfs, str(y0 + i * int(hfs * 1.2)), f"between(t,{a},{b})", "0xFFE082", "", al)
    # thẻ nghĩa «sorry = xin lỗi» là ảnh PNG (cards.gloss_pill) overlay trong render()
    # màn kết: KHÔNG phủ tối (19/11: nhìn như màn hình đen) — chỉ sáng nhẹ nền, bảng «Hôm nay bé học» là ảnh PNG overlay
    o = pl["outro_at"]
    out += f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.14:t=fill:enable='gte(t,{o:.2f})'"
    return out


def hits_from_words(words: Sequence[tuple], gap: float = HIT_GAP, limit: int = HIT_MAX) -> list[float]:
    """Mốc giây các từ tượng thanh / thán từ trong phụ đề (Bịch! Rầm! Ối!) -> nảy zoom + rung máy lúc đó."""
    import re
    out: list[float] = []
    for start, _end, tok, *_ in words:
        t = re.sub(r"[^\w]", "", str(tok).lower())            # chữ phụ đề còn nguyên dấu: chỉ bỏ dấu câu, hạ chữ thường
        if t in SOUND_WORDS and (not out or start - out[-1] >= gap):
            out.append(round(float(start), 3))
    return out[:limit]


def video_graph(pl: dict, sizes: Sequence[tuple[int, int]], W: int, H: int, fps: int, avail: set,
                hits: Sequence[float] = (), fx: str = "full") -> str:
    """[0..n-1:v] ảnh -> zoompan từng cảnh (Ken Burns + thở + nảy/rung theo tiếng động) -> xfade chuỗi -> [vx]."""
    parts = []
    bounds = _clip_bounds(pl)
    motion_on = fx != "off"
    clips = pl.get("clips") or pl["scenes"]
    for j, (a, b) in enumerate(bounds):
        w, h = sizes[j]
        nf = max(2, int(round((b - a) * fps)) + 1)
        pulses = list(hits) if motion_on else []
        strength = HOOK_STRENGTH if clips[j].get("hook") else (1.25 if motion_on else 1.0)
        z, x, y = slideshow.zoompan_expr(clips[j]["motion"], "calm", nf, a, pulses, pulses, b, strength)
        if clips[j].get("hook"):
            z = f"({z})+{HOOK_ZOOM}"
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


def card_graph(src: str, dst: str, title_idx: int, end_idx: int, pl: dict, W: int, H: int,
               pills: Sequence[tuple[int, float]] = ()) -> str:
    """[src] + thẻ tiêu đề (hiện t0..t1, mờ vào/ra) + thẻ nghĩa từng từ (input, mốc giây; hiện GLOSS giây ở 11% chiều cao)
    + bảng kết (từ outro_at, mờ vào) -> [dst]. Ảnh thẻ là input loop."""
    hook = pl.get("hook")
    t0 = float(pl.get("title_at") or 0.0)
    t1 = round(t0 + (TITLE_LEN if hook else TITLE_UNTIL), 2)
    o = float(pl["outro_at"])
    parts = [f"[{title_idx}:v]format=rgba,fade=t=in:st={t0:.2f}:d=0.3:alpha=1,fade=t=out:st={t1 - 0.3:.2f}:d=0.3:alpha=1[ct]",
             f"{src}[ct]overlay=x=(W-w)/2:y={int(H * 0.27)}-h/2:enable='between(t,{t0:.2f},{t1:.2f})':format=auto[cv1]"]
    cur = "[cv1]"
    for j, (idx, at) in enumerate(pills):
        a, b = round(at, 2), round(at + GLOSS, 2)
        parts.append(f"[{idx}:v]format=rgba,fade=t=in:st={a}:d=0.25:alpha=1,fade=t=out:st={b - 0.25:.2f}:d=0.25:alpha=1[cg{j}]")
        parts.append(f"{cur}[cg{j}]overlay=x=(W-w)/2:y={int(H * 0.11)}-h/2:enable='between(t,{a},{b})':format=auto[cgv{j}]")
        cur = f"[cgv{j}]"
    parts.append(f"[{end_idx}:v]format=rgba,fade=t=in:st={o:.2f}:d=0.45:alpha=1[ce]")
    parts.append(f"{cur}[ce]overlay=x=(W-w)/2:y=(H-h)/2-{int(H * 0.04)}:enable='gte(t,{o:.2f})':format=auto{dst}")
    return ";".join(parts)


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
                         recap: tuple[dict, str] | None = None, hook: tuple | None = None) -> list:
    """[(start, end, chữ, cảnh)] rải theo khoảng có tiếng của từng đoạn giọng (đo năng lượng, không cần mốc từ). recap:
    giữ tham số cho tương thích, không rải phụ đề cho câu ôn từ."""
    out = []
    items = [(cl, sc["voice_at"], text) for cl, sc, text in zip(clips, pl["scenes"], texts)]
    # câu ôn từ trong màn kết KHÔNG có phụ đề (bảng «Hôm nay bé học» đã đủ; phản hồi 19/11) — chỉ đọc
    if hook and hook[0].get("path") and pl.get("hook"):
        items.append((hook[0], pl["hook"]["voice_at"], hook[1]))
    f = float(pl.get("factor") or 1.0)        # giọng đọc nhanh hơn: mốc có tiếng cũng dồn lại theo
    for k, (cl, at, text) in enumerate(items):
        if not cl.get("path") or not text.strip():
            continue
        ivs = await voiceplan.voiced_intervals(cl["path"]) or []
        dur = float(cl.get("dur") or 0.0) or (ivs[-1][1] / f if ivs else 2.0)
        shifted = [(a / f + at, b / f + at) for a, b in ivs]
        out += [(a, b, w, k) for a, b, w in align.spread_voiced(sub_tokens(text), shifted, at, at + dur + 0.05)]
    out.sort(key=lambda w: (w[0], w[1]))       # câu móc đọc ở giây 0 nhưng thêm sau cùng: SRT và mốc tiếng động theo thời gian
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
                 fx: str = "full", hook: tuple[dict, str, str] | None = None, hook_scene: int | None = None) -> dict:
    """clips: [{"path": wav}] theo thứ tự cảnh (độ dài tiếng nói tự đo). recap: (clip, lời) câu ôn từ đọc trong màn
    kết. fx: full (chuyển động + hạt + tiếng động) | motion (chỉ chuyển động, phụ đề bật) | off (như 1.1.0).
    hook: (clip, câu móc đọc, chữ to) -> 3 giây đầu là ảnh cảnh hook_scene (mặc định cảnh 3) zoom chặt + câu móc + chữ to,
    tiêu đề lùi ra sau. Trả {"length", "plan", "srt", "ass", "hits", "sfx"}."""
    channel = channel or {}
    rnd = rnd or random.Random(0)
    fx = fx if fx in ("full", "motion", "off") else "full"
    work = os.path.join(tmp, "kids_render")
    os.makedirs(work, exist_ok=True)
    durs = [await voiceplan.speech_duration(c["path"]) if c.get("path") else 0.0 for c in clips]
    recap_dur = 0.0
    if recap and recap[0].get("path"):
        recap_dur = await voiceplan.speech_duration(recap[0]["path"])
    hook_dur = 0.0
    if hook and hook[0].get("path"):
        hook_dur = await voiceplan.speech_duration(hook[0]["path"])
    else:
        hook = None
    pl = plan(durs, rnd, recap_dur, hook_dur=hook_dur)
    f = pl["factor"]
    for c, d in zip(clips, durs):
        c["dur"] = d / f                     # độ dài sau khi đọc nhanh hơn (nếu phải co)
    if recap_dur:
        recap[0]["dur"] = recap_dur / f      # type: ignore[index]
    if hook:
        hook[0]["dur"] = hook_dur / f
    total = pl["length"]
    if f > 1.0 or pl["pad"] < PAD:
        logging.info("Truyện bé: lời dài, co đệm còn %.2fs và đọc nhanh hơn x%.3f để vừa %.0fs", pl["pad"], f, MAX_LEN)
    vplan = [{"start": s["voice_at"], "factor": f, "end": s["voice_at"] + d / f} for s, d in zip(pl["scenes"], durs)]
    all_clips = list(clips)
    if recap_dur:
        all_clips.append(recap[0])  # type: ignore[index]
        vplan.append({"start": pl["recap_at"], "factor": f, "end": pl["recap_at"] + recap_dur / f})
    if hook:
        all_clips.append(hook[0])
        vplan.append({"start": pl["hook"]["voice_at"], "factor": f, "end": pl["hook"]["voice_at"] + hook_dur / f})
    voice = os.path.join(work, "voice.wav")
    await voiceplan.compose_voice(all_clips, vplan, voice, total)
    words = await subtitle_words(clips, pl, texts, recap, hook)
    from rmvideo.kids import story as story_mod
    ass = os.path.join(work, "subs.ass")
    hi = story_mod.english_tokens(brief)
    subs_mod.write_ass(words, ass, None, STYLE, None, highlight=hi, pop=fx != "off")
    srt = subs_mod.to_srt(words)
    glosses = [g for g in gloss_times(words, brief.get("words", [])) if g[0] < pl["outro_at"]]   # màn kết đã có bảng từ
    sizes = [await asyncio.to_thread(slideshow.image_size, p) for p in images]
    avail = await asyncio.to_thread(slideshow.xfade_transitions)
    hits = hits_from_words(words) if fx != "off" else []
    imgs_all = list(images)
    if hook:                                            # clip móc dùng ảnh cảnh có chuyện xảy ra (story.hook_scene / cảnh 3)
        hidx = min(HOOK_IMAGE if hook_scene is None else max(0, int(hook_scene)), len(images) - 1)
        pl["hook"]["scene"] = hidx
        imgs_all = [images[hidx]] + imgs_all
        sizes = [sizes[hidx]] + list(sizes)
        if fx != "off":
            hits = sorted(set([0.05] + hits))           # nảy + whoosh ngay giây đầu
    fc = video_graph(pl, sizes, W, H, fps, avail, hits, fx)
    inputs: list[str] = []
    for p in imgs_all:
        inputs += ["-i", p]
    voice_idx = len(imgs_all)
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
    from rmvideo.kids import cards
    cd = await asyncio.to_thread(cards.make_cards, title or "", brief.get("words", []), channel, W, H, work)
    inputs += ["-loop", "1", "-framerate", str(fps), "-i", cd["title"], "-loop", "1", "-framerate", str(fps), "-i", cd["end"]]
    title_idx, end_idx = next_idx, next_idx + 1
    next_idx += 2
    pills: list[tuple[int, float]] = []
    for j, (at, en, vi) in enumerate(glosses):
        pp = await asyncio.to_thread(cards.gloss_pill, en, vi, W, H, os.path.join(work, f"pill_{j}.png"))
        inputs += ["-loop", "1", "-framerate", str(fps), "-i", pp]
        pills.append((next_idx, at))
        next_idx += 1
    fc += ";" + card_graph(top, "[vc]", title_idx, end_idx, pl, W, H, pills)
    fc += ";[vc]" + overlays(pl, title, brief.get("words", []), glosses, channel, W, H, work,
                             hook[2] if hook else "").lstrip(",")
    # không mờ từ đen / về đen: khung đầu là cảnh móc, khung cuối là bảng kết sáng (Reels lặp lại không lộ màn đen)
    fc += f",subtitles=filename={ass}:fontsdir={ff.ASSETS_DIR},format=yuv420p[v];"
    events: dict[str, list[float]] = {}
    if fx == "full":                                   # whoosh lúc chuyển cảnh, ting lúc dạy từ
        sf = await sfx_files(work)
        sidx = {}
        for name, p in sf.items():
            inputs += ["-i", p]
            sidx[name] = next_idx
            next_idx += 1
        events = {"whoosh": ([0.0] if hook else []) + [tr["start"] for tr in pl["transitions"]],
                  "ding": [g[0] for g in glosses]}
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
    logging.info("Truyện bé: dựng %d cảnh, %.1fs, nhạc nền %s, hiệu ứng %s (%d cú nảy, %d tiếng động)", len(imgs_all),
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
