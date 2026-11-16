"""Dựng chuỗi bộ lọc ffmpeg (hàm thuần chuỗi, không chạy gì): màu / khung hình, che dải chữ gốc, cổng tiếng nền lúc người gốc nói, cửa sổ giữ tiếng gốc, đoạn mở đầu, chuẩn độ to.

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import math
import re

import config
from rmvideo.media import ff

# loudnorm một lượt (động) đoán độ to khi chưa nghe hết bài: đầu video nhỏ rồi to dần, tự nén lên xuống theo cửa
# sổ 3 giây (nghe "thở"), và độ to cuối cùng lệch mục tiêu. Hai lượt: đo độ to tích hợp EBU R128 của chính bản
# trộn (chỉ phần tiếng, không giải mã hình) rồi nhân MỘT hệ số cố định; bộ chặn đỉnh giữ đỉnh dưới -1.4 dBFS.
LOUDNORM = "loudnorm=I=-14:TP=-1.0:LRA=9"


# Chốt đầu ra tiếng 48 kHz STEREO ngay ở aresample. FFmpeg 8 (bản static-ffmpeg 3.0 tải trên Botkeep): aresample
# không ghi ochl thì cho mọi số kênh ở đầu ra và lỗi "Cannot select channel layout for the link between filters
# ... aresample ... and ... apad" khi nối sang apad (FFmpeg 6.1 thì chạy).
AOUT = "aresample=48000:ochl=stereo"


TARGET_LUFS = -14.0


PEAK_LIMIT = 0.85                 # -1.4 dBFS: đỉnh thật (giữa hai mẫu) vẫn dưới -1 dBTP


_LUFS_RE = re.compile(r"Integrated loudness:\s*I:\s*(-?[\d.]+|-inf) LUFS")


def _linear_gain_filter(gain_db: float) -> str:
    return f"volume={gain_db:.2f}dB,alimiter=limit={PEAK_LIMIT}:attack=5:release=60:level=false"


def band_to_output(top: float, bottom: float) -> tuple[int, int]:
    """Dải (tỉ lệ 0..1 trên video gốc) -> (y, h) pixel trên khung 720x1280 sau crop 4%.
    Nới mỗi bên 1.2% và cao tối thiểu 96px để che trọn chữ gốc + đủ chỗ cho chữ mới."""
    y0 = int(((top - 0.012) - 0.02) / 0.96 * 1280)
    y1 = int(((bottom + 0.012) - 0.02) / 0.96 * 1280)
    if y1 - y0 < 96:
        mid = (y0 + y1) // 2
        y0, y1 = mid - 48, mid + 48
    if y0 < 0:                       # dải sát mép trên/dưới: trượt cửa sổ vào trong (cắt bớt thì dải < 80px, boxblur
        y0, y1 = 0, y1 - y0          # chroma 4:2:0 từ chối bán kính 20 -> cả lần dựng lỗi, rà soát 13/11/2026)
    if y1 > 1280:
        y0, y1 = max(0, y0 - (y1 - 1280)), 1280
    return y0, max(2, y1 - y0)


def _cover_filter(bands, W: int = 720, H: int = 1280) -> str:
    """Che từng dải chữ gốc: làm mờ mạnh + phủ tối 45% (nhìn như nền phụ đề, không lộ chữ cũ)."""
    out, k = "", H / 1280
    for i, (top, bottom) in enumerate(bands):
        y, h = band_to_output(top, bottom)
        y, h = int(y * k), int(h * k)
        r = max(1, min(int(20 * k), h // 4 - 1))        # boxblur: bán kính sắc độ (h/2 chiều cao) phải ≤ h/4 - 1
        out += (f",split=2[cv{i}a][cv{i}b];[cv{i}b]crop={W}:{h}:0:{y},boxblur={r}:4[cv{i}bl];"
                f"[cv{i}a][cv{i}bl]overlay=0:{y},drawbox=x=0:y={y}:w={W}:h={h}:color=black@0.45:t=fill")
    return out


# Giọng TTS nghe "máy" vì: quá khô (như phòng cách âm), phụ âm s/x chói, âm lượng đều tăm tắp,
# tách rời không gian cảnh quay. Chuỗi này: bỏ ù thấp -> ấm vùng 230Hz -> bớt chói 3.8kHz ->
# khử xì (de-ess) -> nén nhẹ cho đều như thu micro thật -> phản xạ phòng bếp rất ngắn.
VOICE_FX = ("highpass=f=75,equalizer=f=230:t=q:w=1.1:g=1.8,equalizer=f=3800:t=q:w=1.6:g=-2.2,"
            "deesser=i=0.35:m=0.5:f=0.5,acompressor=threshold=0.1:ratio=2.5:attack=12:release=140:makeup=1.3,"
            "aecho=0.9:0.55:13|22|34:0.10|0.07|0.04,")


def _voice_fx() -> str:
    return VOICE_FX if config.VOICE_HUMANIZE != "0" else ""


GATE_PAD, GATE_MERGE, GATE_RAMP = 0.2, 0.8, 0.15


GATE_FLOOR = 0.0   # KHÔNG giữ lớp nền khi người gốc nói: đo được lọc 3kHz ở 12% vẫn để máy nghe chép nguyên câu gốc


def _gate_windows(speech: list) -> list:
    """Khoảng người gốc nói (đệm 0.2s), gộp các khoảng cách nhau < 0.8s để tiếng nền không chập chờn."""
    out = []
    for a, b in sorted((max(0.0, a - GATE_PAD), b + GATE_PAD) for a, b in speech[:2000]):
        if out and a - out[-1][1] < GATE_MERGE:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _gate_w(speech: list) -> str:
    """Biểu thức 0..1 theo thời gian: 1 khi người gốc nói, lên/xuống dần trong 0.15s (không cắt phụt)."""
    r = GATE_RAMP
    terms = "+".join(f"clip((t-{a:.2f}+{r})/{r},0,1)*clip(({b:.2f}+{r}-t)/{r},0,1)" for a, b in _gate_windows(speech))
    return f"min(1,{terms or '0'})"


def _gate_chain(speech: list, src: str) -> str:
    """Tiếng nền khi KHÔNG tách giọng AI: lúc người gốc nói thì tắt dần phần đầy đủ, chỉ giữ lớp tiếng xèo đã lọc
    dưới 3kHz (dải giọng người) ở mức GATE_FLOOR; hết nói thì hiện lại dần. Bản cũ tắt/bật 0<->1 tức thì:
    đo được 202 lần nhảy ≥15dB/0.1s trong 95s, có quãng im bặt -> nghe khựng như video bị lag."""
    w = _gate_w(speech)
    return (f"{src}aformat=sample_rates=44100:channel_layouts=stereo,asplit=2[gf][gh];"
            f"[gf]volume='1-{w}':eval=frame[gf2];"
            f"[gh]highpass=f=3000,highpass=f=3000,volume='{GATE_FLOOR}*{w}':eval=frame[gh2];"
            "[gf2][gh2]amix=inputs=2:normalize=0,")


OST_LEVEL = {"orig": 1.0, "both": 0.35}     # mức tiếng gốc: giữ nguyên / lót nhỏ dưới giọng đọc mới


OST_RAMP = 0.2


def ost_windows(segs: list, out_dur: float) -> list:
    """Đoạn đánh dấu giữ tiếng gốc -> [(đầu, cuối, mức)]: 'orig' = chỉ tiếng gốc (cả lời người gốc), không đọc
    lời mới; 'both' = đọc lời mới, tiếng gốc lót nhỏ phía dưới. Cuối = đầu đoạn kế (hoặc hết video)."""
    out = []
    for k, sg in enumerate(segs):
        lvl = OST_LEVEL.get(sg.get("ost", "new"))
        if lvl is None:
            continue
        a = float(sg["start"])
        b = float(segs[k + 1]["start"]) if k + 1 < len(segs) else out_dur
        if min(b, out_dur) > a:
            out.append((round(a, 2), round(min(b, out_dur), 2), lvl))
    return out


def _trap(a: float, b: float, r: float = OST_RAMP) -> str:
    """1 trong [a, b], vuốt lên/xuống r giây NẰM TRONG cửa sổ (không lấn sang đoạn giọng mới hai bên)."""
    r = max(0.01, min(r, (b - a) / 2))
    return f"clip((t-{a:.2f})/{r:.3f},0,1)*clip(({b:.2f}-t)/{r:.3f},0,1)"


def _ost_level_expr(wins: list) -> str:
    """Biểu thức âm lượng tiếng gốc theo thời gian (vuốt 0.2s ở hai đầu mỗi cửa sổ)."""
    return "min(1," + ("+".join(f"{lvl}*{_trap(a, b)}" for a, b, lvl in wins) or "0") + ")"


def _ost_any_expr(wins: list) -> str:
    return "min(1," + ("+".join(_trap(a, b) for a, b, _ in wins) or "0") + ")"


def out_fps(src_fps: float) -> float:
    """Tốc độ khung đầu ra: nguồn ≤30 giữ nguyên (không chèn khung lặp); >30 chia ĐỀU
    (50->25, 60->30, 48->24). Ép 50->30 phải lấy khung nhịp 2-1-2-2-1 -> chuyển động giật."""
    if not src_fps or src_fps <= 0 or src_fps > 240:
        return 30.0
    if src_fps <= 30.5:
        return round(src_fps, 3)
    n = math.ceil(src_fps / 30.0 - 0.02)
    if n > 1 and src_fps / n < 24:             # 31-45 fps: chia đôi còn 15-22 fps giật hơn là giữ nguyên
        n -= 1
    return round(src_fps / n, 3)


def kelvin_gains(kelvin: float, mix: float) -> tuple:
    """Hệ số nhân từng kênh R, G, B của filter colortemperature (công thức kelvin2rgb của FFmpeg, pl=0):
    out = c * (1 - mix + mix * k_c). Với pl=0 nó chỉ là nhân hằng số từng kênh."""
    k = kelvin / 100.0
    sat = lambda v: min(1.0, max(0.0, v))   # noqa: E731
    if k <= 66:
        rgb = [1.0, sat(0.39008157876901960784 * math.log(k) - 0.63184144378862745098)]
    else:
        t = max(k - 60.0, 0.0)
        rgb = [sat(1.29293618606274509804 * t ** -0.1332047592), sat(1.12989086089529411765 * t ** -0.0755148492)]
    rgb.append(1.0 if k >= 66 else 0.0 if k <= 19 else sat(0.54320678911019607843 * math.log(k - 10) - 1.19625408914))
    return tuple(1 - mix + mix * c for c in rgb)


def warm_filter(kelvin: float = 6000, mix: float = 0.35) -> str:
    """Làm ấm màu: KẾT QUẢ NHƯ colortemperature=temperature=6000:mix=0.35 (đo SSIM 0.99987, PSNR 55dB) nhưng bằng
    bảng tra 1 chiều lutrgb thay vì tính số thực từng điểm ảnh: phần lọc hình 1080p nhanh hơn ~20% trên 1 lõi."""
    gains = kelvin_gains(kelvin, mix)
    parts = [f"{ch}='clip(val*{g:.8f}+0.5\\,0\\,255)'" for ch, g in zip("rgb", gains) if abs(g - 1) > 1e-6]
    return ("lutrgb=" + ":".join(parts) + ",") if parts else ""


def look_chain(mode: str, W: int, H: int, src_w: int = 0, src_h: int = 0) -> str:
    """Hình đẹp hơn bản gốc (dùng chung cho video và ảnh bìa): cắt 4% viền -> khử nhiễu nén (hqdn3d) + tương
    phản/sáng nhẹ + vibrance (tăng màu nhạt, không làm cháy màu da) + ấm nhẹ cho món ăn, và scale lanczos (thứ tự
    tuỳ nguồn nhỏ hay lớn) -> làm nét (cas khi QUALITY=high, không thì unsharp)."""
    cook = mode == "cook"
    # Công thức màu GIỮ NGUYÊN bản người dùng đã duyệt: vibrance chỉ tăng màu vùng còn nhạt, chừa vùng đã đậm
    # (món chiên vàng cam). Bản thử eq saturation=1.2 tăng đều mọi thứ -> gà rang muối cam loang, bị chê -> bỏ.
    look = ("hqdn3d=1.2:1.2:3:3,"
            f"eq=contrast=1.05:brightness=0.012:saturation={'1.06' if cook else '1.03'},"
            f"vibrance=intensity={'0.2' if cook else '0.1'},"
            + (warm_filter(6000, 0.35) if cook else ""))
    size = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
    # Nguồn NHỎ hơn đầu ra (vd 360x640 -> 720x1280): khử nhiễu + chỉnh màu ở kích thước gốc rồi mới phóng to
    # (ít điểm ảnh hơn 4 lần; khử nhiễu trên điểm ảnh thật tốt hơn trên điểm ảnh nội suy).
    upscale = bool(src_w and src_h) and max(W / (src_w * 0.96), H / (src_h * 0.96)) > 1.05
    sharpen = "cas=strength=0.5" if config.QUALITY == "high" else "unsharp=3:3:0.6:3:3:0"
    # cas: làm nét thích ứng, đẹp nhất, tốn ~2.7x unsharp; unsharp kênh sáng: SSIM 0.984-0.988 so với bản cas
    return "crop=in_w*0.96:in_h*0.96," + (look + size if upscale else size + look) + sharpen


TEASER_DUR = 3.0          # đoạn mở đầu: cảnh món đã xong chiếu trước 3 giây, kèm câu hook (bản 29)


TEASER_ZOOM = 0.06        # đẩy cận 6% trong đoạn mở đầu (ảnh tĩnh: x1.7)


TEASER_STILL_ZOOM = 1.7


def teaser_window(t: float, cuts: list, end: float, dur: float = TEASER_DUR) -> tuple:
    """(giây bắt đầu, ảnh tĩnh?) của đoạn mở đầu dài dur giây trong CÙNG cảnh với khung t (thời gian video gốc).
    Đủ chỗ thì bắt đầu đúng khung t (khung đầu video = ảnh bìa AI chọn, Facebook/TikTok hay lấy khung đầu làm ảnh
    bìa); cảnh đủ dài nhưng t sát cuối cảnh thì lùi về trước; cảnh ngắn hơn dur thì đứng hình tại t (đẩy cận)."""
    prev = max([c for c in cuts if c <= t + 0.05], default=0.0)
    nxt = min([c for c in cuts if c > t + 0.05] + [end])
    if nxt - t >= dur + 0.15:
        return round(t, 3), False
    if nxt - prev >= dur + 0.3:
        return round(max(prev + 0.15, nxt - dur - 0.15), 3), False
    return round(t, 3), True


def subtitle_texts(segs: list, teaser: dict | None = None) -> list:
    """Chữ phụ đề của từng đoạn: đoạn giữ tiếng gốc không có chữ; có đoạn mở đầu và chữ hook to trên màn hình
    (BRAND_INTRO) thì câu hook không hiện phụ đề nữa (hai lớp chữ chồng nhau che mất món)."""
    hide0 = bool(teaser) and config.BRAND_INTRO != "0"
    return ["" if s.get("ost") == "orig" or (hide0 and k == 0 and float(s.get("start", 1.0)) < 0.05) else s["text"]
            for k, s in enumerate(segs)]


def build_filter(mode: str, out_dur: float, ambient: bool, ass_path: str | None,
                 cover_bands=None, W: int = 720, H: int = 1280,
                 ambient_src: str = "[0:a]", speech_gate=None,
                 fps: float = 30.0, src_w: int = 0, src_h: int = 0, stretch: float = 1.0,
                 ost=None, orig_audio: bool = True, bed: bool = False, teaser: dict | None = None) -> str:
    """Hình: look_chain (fps=30 ĐẦU TIÊN: nguồn TikTok HD thường 50-60fps, lọc trước khi bớt khung là phí 67-100%)
    + che dải chữ gốc + phụ đề mới. Tiếng: giọng mới + tiếng nền (tách giọng / cổng) + tiếng gốc giữ lại.
    teaser: đoạn mở đầu {"iv": đầu vào hình (đã tua tới cảnh món xong), "ia": đầu vào tiếng nền của cảnh đó hoặc
    None, "dur": giây, "still": ảnh tĩnh} nối TRƯỚC video chính; mọi mốc (phụ đề, cổng tiếng, cửa sổ tiếng gốc) do
    caller đưa vào đã tính cả đoạn mở đầu."""
    tz = teaser if teaser and teaser.get("dur") else None
    T = float(tz["dur"]) if tz else 0.0
    # stretch > 1: làm chậm hình ngay trong lần render này (trước đây stretch() mã hoá cả video một lần riêng)
    slow = f"setpts=PTS*{stretch:.4f}," if abs(stretch - 1) > 1e-3 else ""
    orig_src = "[0:a]"
    if slow:
        ambient_src += f"atempo={1 / stretch:.4f},"          # tiếng nền chậm theo hình (tiếng gốc hoặc đã tách giọng)
        orig_src += f"atempo={1 / stretch:.4f},"
    if tz:
        orig_src += f"adelay={int(round(T * 1000))}:all=1,"   # tiếng gốc của video chính lùi sau đoạn mở đầu
    ost = [w for w in (ost or []) if w[1] > w[0]] if orig_audio else []
    # tiếng gốc NGUYÊN BẢN (cả lời người gốc) chỉ trong các cửa sổ được đánh dấu giữ tiếng gốc
    ost_chain = (f"{orig_src}aformat=sample_rates=44100:channel_layouts=stereo,"
                 f"volume='{_ost_level_expr(ost)}':eval=frame[ost];") if ost else ""
    look = look_chain(mode, W, H, src_w, src_h)
    if tz:
        # cảnh món đã xong chiếu trước: cùng chuỗi màu với video, đẩy cận nhẹ cho có nhịp (ảnh tĩnh đẩy mạnh hơn),
        # rồi nối thẳng vào video chính. Đầu vào riêng đã tua sẵn: không bắt FFmpeg giữ hàng nghìn khung chờ nối.
        z = TEASER_ZOOM * (TEASER_STILL_ZOOM if tz.get("still") else 1.0)
        v = (f"[{tz['iv']}:v]"
             + (f"trim=end_frame=1,tpad=stop_mode=clone:stop_duration={T + 0.2:.2f}," if tz.get("still") else "")
             + f"setpts=PTS-STARTPTS,fps={fps:g},{look},"
             f"scale=w='trunc({W}*(1+{z:.3f}*t/{T:.2f})/2)*2':h=-2:eval=frame,"
             f"crop={W}:{H}:x='(trunc({W}*(1+{z:.3f}*t/{T:.2f})/2)*2-{W})/2'"
             f":y='(trunc({H}*(1+{z:.3f}*t/{T:.2f})/2)*2-{H})/2',setsar=1,"
             f"trim=duration={T:.3f}[tzv];"
             f"[0:v]{slow}fps={fps:g},{look},setsar=1[mnv];[tzv][mnv]concat=n=2:v=1:a=0")
    else:
        v = f"[0:v]{slow}fps={fps:g}," + look
    v += _cover_filter(cover_bands or [], W, H)
    if ass_path:
        v += f",subtitles=filename={ass_path}:fontsdir={ff.ASSETS_DIR}"
    v += "[v];"
    if bed:
        # video KHÔNG LỜI: lớp nền đã trộn sẵn (nhạc nền + tiếng gốc, music_bed) là tiếng chính, không có giọng đọc;
        # không qua lọc giọng (EQ/nén/vang làm méo nhạc), không làm chậm (nhạc giữ đúng nhịp, music_bed đã tính)
        return v + (f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,{LOUDNORM},{AOUT},"
                    f"apad=whole_dur={out_dur:.2f}[a]")
    if mode == "cook" and ambient:
        # highpass 3kHz giữ tiếng xèo xèo; sidechaincompress tự hạ tiếng nền khi giọng đọc
        # vang lên (ducking như video chuyên nghiệp); loudnorm chuẩn hóa âm lượng -16 LUFS
        pre = ""
        if tz:
            # tiếng nền của đoạn mở đầu: tiếng xèo/giòn của chính cảnh đó (bản đã tách giọng); không có bản tách
            # giọng thì im (tiếng gốc cảnh đó có thể còn lời người gốc), rồi tới tiếng nền của video chính
            pre = ((f"[{tz['ia']}:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
                    f"apad=whole_dur={T:.3f},atrim=0:{T:.3f}[tza];") if tz.get("ia") is not None else
                   f"anullsrc=r=44100:cl=stereo,atrim=0:{T:.3f}[tza];")
            pre += (f"{ambient_src}aformat=sample_rates=44100:channel_layouts=stereo[mna];"
                    "[tza][mna]concat=n=2:v=0:a=1[ambx];")
            ambient_src = "[ambx]"
        a = (
            pre + "[1:a]aformat=sample_rates=44100:channel_layouts=stereo," + _voice_fx() + "asplit=2[vo][sc];"
            + (_gate_chain(speech_gate, ambient_src) if speech_gate else
               f"{ambient_src}aformat=sample_rates=44100:channel_layouts=stereo,")
            + "volume=0.8"
            # trong cửa sổ giữ tiếng gốc thì tắt lớp tiếng nền (tiếng gốc đã có tiếng nền, tránh nghe gấp đôi)
            + (f",volume='1-{_ost_any_expr(ost)}':eval=frame" if ost else "") + "[amb];"
            "[amb][sc]sidechaincompress=threshold=0.02:ratio=6:attack=100:release=500[ambd];"
            + ost_chain
            + ("[vo][ambd][ost]amix=inputs=3:duration=longest:weights='1 0.5 1'," if ost else
               "[vo][ambd]amix=inputs=2:duration=longest:weights='1 0.5',")
            + f"{LOUDNORM},{AOUT}[a]"
        )
    elif ost:
        # giọng mới + tiếng gốc trong các cửa sổ giữ tiếng gốc
        fx = _voice_fx().rstrip(",") or "anull"
        a = (
            f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,{fx}[vo];" + ost_chain
            + "[vo][ost]amix=inputs=2:duration=longest:normalize=0,"
            f"{LOUDNORM},{AOUT},apad=whole_dur={out_dur:.2f}[a]"
        )
    else:
        # chỉ giọng AI; apad hữu hạn đệm im lặng tới hết video
        # (apad vô hạn + -shortest từng gây treo/OOM, nên luôn pad có giới hạn và cắt bằng -t)
        a = (
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo," + _voice_fx() +
            f"{LOUDNORM},{AOUT},apad=whole_dur={out_dur:.2f}[a]"
        )
    return v + a
