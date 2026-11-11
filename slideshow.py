"""📸 Ảnh + nhạc: 1-4 ảnh + một bản nhạc -> video dọc theo nhịp, kiểu mẫu "beat sync" của CapCut/TikTok.

Cách làm (tham khảo: mẫu beat-sync của CapCut — cắt cảnh rơi đúng beat, chuyển cảnh "hit" ở đoạn drop; kỹ thuật
xfade nối ảnh tĩnh của advancedweb.hu; Ken Burns bằng zoompan trên ảnh phóng to trước như still-motion /
kburns-slideshow để chuyển động mượt dưới mức điểm ảnh; editly/gl-transitions đẹp nhưng cần headless-GL, quá nặng
cho máy 1 lõi nên chỉ dùng bộ chuyển cảnh xfade có sẵn trong FFmpeg):

1. Phân tích nhạc (numpy, không cần librosa): tempo (tự tương quan + tinh chỉnh chu kỳ bằng lược dò mịn), lưới beat,
   phách mạnh đầu ô nhịp (năng lượng dải trầm), năng lượng từng beat, mật độ onset, đoạn "drop" (năng lượng bật lên).
2. Chọn đoạn nhạc: cửa sổ dài bằng video, bắt đầu ở phách mạnh, năng lượng trung bình cao nhất (+ điểm thưởng nếu
   mở đầu đúng lúc nhạc bật lên) -> thường là điệp khúc/đoạn hook như TikTok vẫn dùng. Khớp lại pha beat trong đoạn.
3. Kiểu dựng theo nhạc:
   - êm (chậm < 92 BPM hoặc thưa nhịp): mỗi ảnh 2 ô nhịp, Ken Burns chậm có làm mềm, chuyển mờ/dissolve 1 phách
     căn giữa beat, hơi ấm màu.
   - nhún (vừa): mỗi ảnh 1 ô nhịp, lia/zoom, trượt/quét/mở tròn nửa phách kết thúc đúng beat, nảy nhẹ ở phách mạnh.
   - sôi (nhanh, dày nhịp): đổi ảnh mỗi 2 phách (trước drop 4 phách), mỗi beat nảy zoom, rung + loé trắng ở phách
     mạnh sau drop, chuyển zoom/loé trắng/lia nhoè 1/4-1/2 phách; đúng điểm drop là chuyển "hit" + loé.
4. Dựng bằng MỘT lệnh FFmpeg: mỗi ảnh -> nền (ảnh khác tỉ lệ 9:16 thì lót nền mờ) phóng to 2 lần -> zoompan theo
   biểu thức thời gian (có nảy theo beat) -> nối bằng xfade -> chỉnh màu + loé theo beat -> nhạc đã cắt, chuẩn hoá
   âm lượng hai lượt. Cùng ảnh + nhạc + thời lượng thì ra đúng video cũ (ngẫu nhiên có hạt giống cố định).
"""
import hashlib
import logging
import os
import random
import subprocess

import numpy as np

import beat
import config
import core_video

SR = beat.SR
HOP = beat.HOP
FPS_A = SR / HOP
FPS = 30
MAX_IMAGES = 4
MAX_SCENES = 20                  # trần số cảnh cả video: ảnh lặp quá nhiều vòng thì nhàm (video dài -> cảnh dài hơn)
GROUP = 8                        # số cảnh tối đa mỗi lệnh FFmpeg: RAM không tăng theo độ dài video (xem render)
UPSCALE = 2                      # phóng to nền trước zoompan: chuyển động mượt dưới mức điểm ảnh

STYLES = ("calm", "groove", "hype")
STYLE_VI = {"calm": "êm", "groove": "nhún", "hype": "sôi"}
TRANSITIONS = {
    "calm": ["fade", "dissolve", "smoothleft", "smoothright", "smoothup", "fadeblack"],
    "groove": ["slideleft", "slideright", "slideup", "slidedown", "wipeleft", "wiperight", "circleopen", "rectcrop",
               "smoothleft", "horzopen"],
    "hype": ["zoomin", "fadewhite", "slideleft", "slideup", "hblur", "pixelize", "circlecrop", "squeezeh", "slideright"],
}
TRANSITIONS_ONE = {"calm": ["fadeblack", "fadewhite", "zoomin"], "groove": ["zoomin", "circleopen", "fadewhite", "radial"],
                   "hype": ["zoomin", "fadewhite", "hblur"]}
HIT = ["zoomin", "fadewhite"]     # chuyển cảnh "hit" đúng điểm drop
MOTIONS = {
    "calm": ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up", "zoom_in"],
    "groove": ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"],
    "hype": ["zoom_in", "zoom_out", "pan_right", "pan_left", "zoom_in"],
}


# ---------- 1. phân tích nhạc ----------

def _decode(path: str, max_s: float) -> np.ndarray:
    return beat._decode(path, max_s)


def _frames_energy(x: np.ndarray) -> tuple:
    """(năng lượng RMS mỗi khung, năng lượng dải trầm < 150Hz mỗi khung) theo khung HOP mẫu."""
    n = len(x) // HOP
    fr = x[: n * HOP].reshape(n, HOP)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    spec = np.abs(np.fft.rfft(fr * np.hanning(HOP), axis=1))
    k = max(1, int(150 / (SR / HOP)))
    low = np.sqrt((spec[:, 1:k + 1] ** 2).mean(axis=1) + 1e-12)
    return rms, low


def _comb(env: np.ndarray, period: float, phase: float) -> float:
    idx = np.arange(phase, len(env) - 1, period)
    if len(idx) == 0:
        return 0.0
    i0 = idx.astype(int)
    w = idx - i0
    return float(((1 - w) * env[i0] + w * env[i0 + 1]).mean())


def refine_grid(env: np.ndarray, bpm: float) -> tuple:
    """Tinh chỉnh chu kỳ (±2%) và pha bằng lược dò mịn trên đường onset -> (bpm, beat đầu tiên giây)."""
    p0 = 60.0 * FPS_A / bpm
    best = (-1.0, p0, 0.0)
    for p in np.linspace(p0 * 0.98, p0 * 1.02, 81):
        for ph in np.arange(0, p, 0.5):
            sc = _comb(env, p, ph)
            if sc > best[0]:
                best = (sc, p, ph)
    _, p, ph = best
    return 60.0 * FPS_A / p, ph / FPS_A


def local_phase(env: np.ndarray, period_s: float, a: float, b: float) -> float:
    """Pha beat khớp nhất TRONG đoạn [a, b] (giây): lưới toàn bài có thể trôi vài chục ms ở giữa bài."""
    seg = env[int(a * FPS_A): int(b * FPS_A) + 1]
    if len(seg) < 4:
        return a
    p = period_s * FPS_A
    best = max(np.arange(0, p, 0.5), key=lambda ph: _comb(seg, p, ph))
    return a + best / FPS_A


def analyze_music(path: str, max_s: float = 300) -> dict:
    x = _decode(path, max_s)
    dur = len(x) / SR
    if dur < 3:
        raise RuntimeError("Nhạc quá ngắn (< 3 giây).")
    env = beat._onset_env(x) if dur >= 5 else np.ones(max(1, len(x) // HOP))
    try:
        bpm, _ = beat.tempo(env)
        while bpm < 70:
            bpm *= 2
        while bpm > 175:
            bpm /= 2
        bpm, first = refine_grid(env, bpm)
        # tự tương quan hay nhầm tỉ lệ 3:4 / 2:3 với nhạc swing (đo: Local Forecast 82 BPM ra 109): thử các tỉ lệ
        # đó, chỉ đổi khi lược beat khớp hơn hẳn (≥ 20%). Bội số 2 không xét (lược luôn thích nửa tempo).
        base = _comb(env, 60 * FPS_A / bpm, first * FPS_A)
        for r in (3 / 4, 4 / 3, 2 / 3, 3 / 2):
            if 70 <= bpm * r <= 175:
                b2, f2 = refine_grid(env, bpm * r)
                sc = _comb(env, 60 * FPS_A / b2, f2 * FPS_A)
                if sc >= 1.2 * base:
                    bpm, first, base = b2, f2, sc
    except Exception:
        bpm, first = 100.0, 0.0
    peaks = (env[1:-1] > 0.3) & (env[1:-1] >= env[:-2]) & (env[1:-1] >= env[2:])
    density = float(peaks.sum()) / max(dur, 1)
    if density < 1.2 and bpm >= 120:
        # nhạc thưa nhịp mà tempo đo ra nhanh: thường là nhịp đôi của bài chậm (Ice Flow 70 BPM đo ra 140) -> lấy
        # nhịp chậm, chọn pha (beat chẵn hay lẻ) khớp onset hơn
        p2 = 2 * 60.0 * FPS_A / bpm
        f0 = first * FPS_A
        first = (f0 if _comb(env, p2, f0) >= _comb(env, p2, f0 + p2 / 2) else f0 + p2 / 2) / FPS_A
        bpm /= 2
    period = 60.0 / bpm
    beats = np.arange(first, dur, period)
    rms, low = _frames_energy(x)

    def per_beat(v):
        out = []
        for b in beats:
            i0, i1 = int(b * FPS_A), int((b + period) * FPS_A)
            out.append(float(v[i0:max(i0 + 1, i1)].mean()) if i0 < len(v) else float(v[-1]))
        return np.array(out)
    e_db = 20 * np.log10(per_beat(rms) + 1e-9)
    sd = float(e_db.std()) or 1.0
    energy = (e_db - float(np.median(e_db))) / sd
    lowb = per_beat(low)
    phase = int(np.argmax([lowb[p::4].mean() if len(lowb[p::4]) else 0 for p in range(4)])) if len(lowb) >= 4 else 0
    return {"duration": dur, "bpm": round(bpm, 2), "period": period, "beats": beats, "energy": energy,
            "e_range_db": float(np.percentile(e_db, 90) - np.percentile(e_db, 10)),
            "downbeat_phase": phase, "density": round(density, 2), "env": env}


def music_style(bpm: float, density: float, drop: bool) -> str:
    if bpm < 92 or density < 1.2:
        return "calm"
    if (bpm >= 125 and density >= 2.2) or (drop and bpm >= 110 and density >= 1.8):
        return "hype"
    return "groove"


DROP_RISE = 0.9          # năng lượng 2 ô nhịp sau so với 2 ô nhịp trước tăng ≥ 0.9 độ lệch chuẩn (và dải động ≥ 3 dB)


def _rise(energy: np.ndarray, k: int, span: int = 8) -> float:
    if k < span // 2 or k + span // 2 > len(energy):
        return 0.0
    before = energy[max(0, k - span):k]
    return float(energy[k:k + span].mean() - before.mean())


def choose_window(info: dict, length: float) -> tuple:
    """(giây bắt đầu, chỉ số beat bắt đầu, có drop không, chỉ số beat của drop hoặc None) cho đoạn dài length giây.
    Đoạn: năng lượng trung bình cao nhất (+ thưởng nếu mở đầu đúng lúc nhạc bật lên). Có drop sát đầu đoạn thì lùi
    đoạn lại 2 ô nhịp để video có đà "build-up" rồi bùng đúng drop (kiểu edit TikTok)."""
    beats, energy, period = info["beats"], info["energy"], info["period"]
    nb = max(4, int(round(length / period)))
    ph = info["downbeat_phase"]
    if len(beats) <= nb + 1:
        return float(beats[0]) if len(beats) else 0.0, 0, False, None
    best, best_k = -1e9, ph
    for k in range(ph, len(beats) - nb, 4):
        sc = float(energy[k:k + nb].mean()) + 0.5 * max(0.0, _rise(energy, k))
        if sc > best:
            best, best_k = sc, k
    dyn = info["e_range_db"] >= 3
    cands = [(k, _rise(energy, k)) for k in range(max(ph, best_k - 8), best_k + nb - 4, 4)]
    drop_k, drop_v = max(cands, key=lambda c: c[1]) if cands else (None, 0.0)
    has_drop = dyn and drop_k is not None and drop_v >= DROP_RISE
    start_k = best_k
    if has_drop and drop_k - best_k < 8:               # drop ở đầu đoạn: lùi lại để có 2 ô nhịp dồn trước drop
        start_k = max(ph % 4, drop_k - 8)
        while start_k + nb > len(beats) - 1 and start_k >= 4:
            start_k -= 4
    if has_drop and not (start_k + 4 <= drop_k <= start_k + nb - 4):
        has_drop = False
    return float(beats[start_k]), int(start_k), has_drop, (int(drop_k) if has_drop else None)


# ---------- 2. kế hoạch dựng ----------

def plan_video(info: dict, n_images: int, duration: float = 0, seed: int = 0) -> dict:
    """Kế hoạch: đoạn nhạc, kiểu dựng, các cảnh (ảnh, chuyển động, mốc), chuyển cảnh, nảy/loé theo beat.
    duration 0 = tự động theo nhạc (12-16 giây). Mọi mốc tính từ đầu video (giây)."""
    rnd = random.Random(seed)
    period = info["period"]
    auto = not duration
    # đoán kiểu sơ bộ để biết độ dài tự động, rồi chọn đoạn nhạc, rồi chốt kiểu (biết có drop hay không)
    pre = music_style(info["bpm"], info["density"], False)
    target = duration or {"calm": 16.0, "groove": 14.0, "hype": 12.0}[pre]
    bar = 4 * period
    target = min(target, info["duration"] - 0.05)
    nbars = max(1, int(round(target / bar))) if auto else max(1, int(target // bar))
    length = nbars * bar
    if not auto:                                       # người dùng chọn độ dài: theo đúng, phần lẻ < 1 ô nhịp cho cảnh cuối
        length = min(float(duration), info["duration"] - 0.05)
    start, k0, has_drop, drop_k = choose_window(info, length)
    style = music_style(info["bpm"], info["density"], has_drop)
    first = local_phase(info["env"], period, start, start + length)
    off = (first - start) % period                     # beat đầu trong đoạn (sau khi khớp lại pha)
    if off > period * 0.5:
        off -= period
    start = max(0.0, start + off)
    # video không được dài hơn phần nhạc còn lại (lỗi cũ: nhạc 20s chọn 60s -> đuôi video im lặng)
    avail = info["duration"] - start - 0.05
    if length > avail:
        length = (int(avail // bar) * bar) if (auto and avail >= bar) else max(1.0, avail)
    beats = [round(float(k * period), 4) for k in range(int(length / period) + 1) if k * period <= length + 1e-6]
    drop_t = round(float((drop_k - k0) * period), 4) if drop_k is not None else None

    slot_beats = {"calm": 8, "groove": 4, "hype": 2}[style]
    while slot_beats * period < 0.7:                   # nhạc rất nhanh: cảnh không ngắn hơn 0.7s
        slot_beats *= 2
    while slot_beats * period > 5.5 and slot_beats > 1:
        slot_beats //= 2
    # video dài: cảnh dài ra (vẫn đổi cảnh đúng beat, vẫn nảy theo từng beat) thay vì lặp ảnh hàng chục vòng
    while length / (slot_beats * period) > MAX_SCENES:
        slot_beats *= 2
    bounds, t, k = [0.0], 0.0, 0
    while True:
        sb = slot_beats * (2 if (style == "hype" and drop_t is not None and t < drop_t - 1e-6) else 1)
        k += sb
        t = round(float(k * period), 4)
        if drop_t is not None and bounds[-1] < drop_t - 1e-6 < t - 1e-6 and drop_t - bounds[-1] >= period:
            t, k = drop_t, int(round(drop_t / period))   # cắt cảnh đúng điểm drop
        if t >= length - max(0.6, period) + 1e-6:
            break
        bounds.append(t)
    bounds.append(round(length, 4))
    n = len(bounds) - 1
    order = list(range(n_images))
    imgs = [order[i % n_images] for i in range(n)]
    motions, prev = [], None
    for i in range(n):
        choices = [m for m in MOTIONS[style] if m != prev] or MOTIONS[style]
        m = choices[rnd.randrange(len(choices))] if i else MOTIONS[style][0]
        motions.append(m)
        prev = m
    trans, prev = [], None
    for i in range(n - 1):
        at = bounds[i + 1]
        is_drop = drop_t is not None and abs(at - drop_t) < 1e-3
        # 1 ảnh: hoà/trượt giữa hai khung của CÙNG một ảnh nhìn như bóng đôi -> chỉ dùng kiểu "nhảy" qua sáng/tối/zoom
        base = TRANSITIONS_ONE[style] if n_images == 1 else TRANSITIONS[style]
        pool = HIT if is_drop else ([x for x in base if x != prev] or base)
        typ = pool[rnd.randrange(len(pool))]
        prev = typ
        if style == "calm":
            d = min(0.9, period, (bounds[i + 1] - bounds[i]) * 0.4)
            t0 = at - d / 2                             # dịu: căn giữa beat
        else:
            d = min(max(period * (0.5 if style == "groove" else 0.4), 0.12), 0.45 if style == "groove" else 0.3)
            d = min(d, (bounds[i + 1] - bounds[i]) * 0.45)
            t0 = at - d                                 # dứt khoát: ảnh mới hiện TRỌN đúng lúc beat
        trans.append({"type": typ, "start": round(max(0.0, t0), 4), "dur": round(d, 4), "at": at, "drop": is_drop})
    # nảy zoom theo beat (trong từng cảnh) và loé sáng
    pulse_beats, flashes = [], []
    if style == "groove":
        pulse_beats = [b for k, b in enumerate(beats) if k % 4 == 0]
        flashes = [drop_t] if drop_t is not None else []
    elif style == "hype":
        pulse_beats = list(beats)
        after = drop_t if drop_t is not None else length * 0.5
        flashes = [b for k, b in enumerate(beats) if k % 4 == 0 and b >= after - 1e-6 and b < length - 0.3]
        if drop_t is not None:
            flashes = sorted(set(flashes + [drop_t]))
    slots = [{"img": imgs[i], "start": bounds[i], "end": bounds[i + 1], "motion": motions[i]} for i in range(n)]
    return {"music_start": round(float(start), 3), "length": round(float(length), 3), "bpm": float(info["bpm"]), "style": style,
            "requested": float(duration or 0),
            "period": period, "drop": drop_t, "slots": slots, "transitions": trans, "beats": beats,
            "pulse": pulse_beats, "flashes": flashes, "shake": flashes if style == "hype" else []}


# ---------- 3. dựng filter FFmpeg ----------

def _ease(u: str, style: str) -> str:
    return f"(3*pow({u},2)-2*pow({u},3))" if style == "calm" else u


def _decay_sum(times: list, t0: float, t1: float, amp: float, tau: float, var: str = "T") -> str:
    """Σ amp·e^(-(T-b)/tau) với các mốc b trong [t0-0.5, t1]: nảy/loé tắt dần sau mỗi beat."""
    terms = [f"{amp}*exp(-({var}-{b:.3f})/{tau})*gte({var},{b:.3f})" for b in times if t0 - 0.5 <= b <= t1]
    return "+".join(terms) if terms else "0"


def zoompan_expr(motion: str, style: str, n_frames: int, t_start: float, pulse: list, shake: list,
                 t_end: float) -> tuple:
    """(z, x, y) cho zoompan của MỘT cảnh. T = giây trên trục video; u = tiến độ 0..1 trong cảnh."""
    u = f"(on/{max(1, n_frames - 1)})"
    e = _ease(u, style)
    amp = {"calm": (1.0, 1.12), "groove": (1.0, 1.16), "hype": (1.04, 1.16)}[style]
    T = f"({t_start:.4f}+on/{FPS})"
    pz = _decay_sum(pulse, t_start, t_end, 0.035 if style == "groove" else 0.06, 0.12, T)
    if motion == "zoom_in":
        z = f"{amp[0]}+{amp[1] - amp[0]:.3f}*{e}"
    elif motion == "zoom_out":
        z = f"{amp[1]}-{amp[1] - amp[0]:.3f}*{e}"
    else:
        z = f"{amp[1] + 0.02:.3f}"
    z = f"({z})+({pz})"
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if motion == "pan_right":
        cx = f"(iw-iw/zoom)*{e}"
    elif motion == "pan_left":
        cx = f"(iw-iw/zoom)*(1-{e})"
    elif motion == "pan_up":
        cy = f"(ih-ih/zoom)*(1-{e})"
    elif motion == "pan_down":
        cy = f"(ih-ih/zoom)*{e}"
    if shake:
        sh = _decay_sum(shake, t_start, t_end, 1.0, 0.09, T)
        cx = f"{cx}+iw*0.012*({sh})*sin(2*PI*23*{T})"
        cy = f"{cy}+ih*0.008*({sh})*cos(2*PI*19*{T})"
    # giữ khung trong ảnh (zoompan không tự chặn khi biểu thức vượt biên)
    x = f"max(0,min(iw-iw/zoom,{cx}))"
    y = f"max(0,min(ih-ih/zoom,{cy}))"
    return z, x, y


def _prep(i: int, w: int, h: int, W: int, H: int) -> str:
    """Ảnh -> nền dọc CWxCH (phóng UPSCALE lần). Ảnh lệch tỉ lệ 9:16 nhiều thì lót nền mờ + ảnh vừa khung."""
    CW, CH = W * UPSCALE, H * UPSCALE
    ar, target = (w / h if h else 0.5625), W / H
    base = f"[{i}:v]format=yuv420p,setsar=1"
    if not w or abs(ar - target) / target <= 0.12:
        return f"{base},scale={CW}:{CH}:force_original_aspect_ratio=increase:flags=lanczos,crop={CW}:{CH}"
    return (f"{base},split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={CW // 4}:{CH // 4}:force_original_aspect_ratio=increase,crop={CW // 4}:{CH // 4},"
            f"gblur=sigma=12,eq=brightness=-0.08:saturation=1.1,scale={CW}:{CH}[bgb{i}];"
            f"[fg{i}]scale={CW}:{CH}:force_original_aspect_ratio=decrease:flags=lanczos[fgs{i}];"
            f"[bgb{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2")


def groups(plan: dict, size: int | None = None) -> list:
    """Chia các cảnh thành khúc ≤ size (mặc định GROUP) cảnh, đều nhau, ranh giới khớp khung hình
    -> [(i0, i1, đầu, cuối)]."""
    size = size or GROUP
    n = len(plan["slots"])
    k = max(1, -(-n // size))
    cuts = [round(i * n / k) for i in range(k + 1)]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        t0 = 0.0 if a == 0 else round(plan["slots"][a]["start"] * FPS) / FPS
        t1 = plan["length"] if b == n else round(plan["slots"][b]["start"] * FPS) / FPS
        out.append((a, b - 1, t0, t1))
    return out


def build_group(plan: dict, grp: tuple, sizes: list, W: int, H: int, available: set | None = None) -> tuple:
    """filter_complex chỉ phần HÌNH của một khúc (cảnh i0..i1, thời gian [t0, t1] trên trục video) và danh sách ảnh
    cần nạp. Chuyển cảnh giữa hai khúc bỏ đi: nhạc êm thì tối dần rồi sáng lại, còn lại là cắt thẳng đúng beat
    kèm loé sáng (kiểu "hit")."""
    style, slots, trans = plan["style"], plan["slots"], plan["transitions"]
    i0, i1, t0, t1 = grp
    first, last = t0 == 0.0, abs(t1 - plan["length"]) < 1e-6
    # mỗi clip: từ lúc chuyển cảnh trước bắt đầu (hoặc đầu khúc) tới lúc chuyển cảnh sau xong (hoặc cuối khúc)
    starts = [t0] + [trans[j]["start"] for j in range(i0, i1)]
    ends = [trans[j]["start"] + trans[j]["dur"] for j in range(i0, i1)] + [t1]
    imgs = sorted({slots[j]["img"] for j in range(i0, i1 + 1)})
    idx = {img: k for k, img in enumerate(imgs)}
    uses: dict = {}
    for j in range(i0, i1 + 1):
        uses[slots[j]["img"]] = uses.get(slots[j]["img"], 0) + 1
    parts, labels = [], {}
    for img, cnt in uses.items():
        w, h = sizes[img]
        prep = _prep(idx[img], w, h, W, H)
        outs = "".join(f"[p{idx[img]}_{k}]" for k in range(cnt))
        parts.append(prep + (f",split={cnt}{outs}" if cnt > 1 else f"[p{idx[img]}_0]"))
        labels[img] = [f"[p{idx[img]}_{k}]" for k in range(cnt)]
    for c, j in enumerate(range(i0, i1 + 1)):
        dur = ends[c] - starts[c]
        nf = max(2, int(round(dur * FPS)) + 1)
        z, x, y = zoompan_expr(slots[j]["motion"], style, nf, starts[c], plan["pulse"], plan["shake"], ends[c])
        parts.append(f"{labels[slots[j]['img']].pop(0)}zoompan=z='{z}':x='{x}':y='{y}':d={nf}:s={W}x{H}:fps={FPS},"
                     f"trim=duration={dur:.4f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[c{c}]")
    cur = "[c0]"
    for c, j in enumerate(range(i0, i1)):
        t = trans[j]
        typ = t["type"] if (available is None or t["type"] in available) else "fade"
        parts.append(f"{cur}[c{c + 1}]xfade=transition={typ}:duration={t['dur']:.4f}:offset={t['start'] - t0:.4f}[x{c}]")
        cur = f"[x{c}]"
    look = {"calm": "eq=contrast=1.03:saturation=1.04," + core_video.warm_filter(5800, 0.25),
            "groove": "eq=contrast=1.05:saturation=1.08,",
            "hype": "eq=contrast=1.08:saturation=1.14,"}[style]
    flashes = [f - t0 for f in plan["flashes"] if t0 - 0.5 <= f < t1]
    if not first and style != "calm":
        flashes = sorted(set(flashes + [0.0]))           # nối khúc = cắt thẳng đúng beat + loé
    fl = _decay_sum(flashes, -0.5, t1 - t0, 0.28, 0.08, "t")
    D = t1 - t0
    fades = ""
    if first:
        fades += f"fade=t=in:st=0:d={0.35 if style == 'calm' else 0.12},"
    elif style == "calm":
        fades += "fade=t=in:st=0:d=0.25,"                 # nối khúc nhạc êm: sáng dần từ đen
    if last:
        fades += f"fade=t=out:st={max(0.0, D - 0.5):.3f}:d=0.5,"
    elif style == "calm":
        fades += f"fade=t=out:st={max(0.0, D - 0.25):.3f}:d=0.25,"
    v = (f"{cur}{look}" + (f"eq=brightness='{fl}':eval=frame," if flashes else "") + fades
         + f"trim=duration={D:.4f},format=yuv420p[v]")
    return ";".join(parts) + ";" + v, imgs


def build_audio(plan: dict, music_idx: int) -> str:
    """Phần TIẾNG: nhạc đã cắt đúng đoạn, vuốt đầu/cuối, chỗ LOUDNORM để chuẩn hoá hai lượt."""
    L, calm = plan["length"], plan["style"] == "calm"
    return (f"[{music_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"afade=t=in:st=0:d={0.3 if calm else 0.03},afade=t=out:st={max(0.0, L - 0.8):.3f}:d=0.8,"
            f"{core_video.LOUDNORM},{core_video.AOUT},apad=whole_dur={L:.3f},atrim=duration={L:.3f}[a]")


_XFADE: set = set()


def xfade_transitions() -> set:
    """Các kiểu xfade bản FFmpeg đang có (bản cũ thiếu vài kiểu: thiếu thì dùng 'fade')."""
    if not _XFADE:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True,
                                 timeout=20).stdout
            import re
            _XFADE.update(re.findall(r"^\s+(\w+)\s+-?\d+\s+\S+", out, re.M))
        except Exception:
            pass
    return _XFADE or {"fade"}


def image_size(path: str) -> tuple:
    """(rộng, cao) của ảnh SAU KHI giải mã (ffmpeg tự xoay theo EXIF Orientation của ảnh chụp điện thoại, còn
    ffprobe vẫn báo kích thước chưa xoay) — đúng cỡ khung mà bộ lọc sẽ nhận. Ảnh hỏng: báo lỗi rõ ràng."""
    import re
    dec = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-frames:v", "1", "-vf", "showinfo", "-f", "null", "-"],
                         capture_output=True, text=True)
    m = re.search(r"\bs:(\d+)x(\d+)", dec.stderr)
    if dec.returncode != 0 or not m or "Invalid data found" in dec.stderr:
        raise RuntimeError(f"Không đọc được ảnh {os.path.basename(path)} (hỏng hoặc không phải ảnh jpg/png/webp).")
    return int(m.group(1)), int(m.group(2))


def seed_for(images: list, music: str, duration: float) -> int:
    h = hashlib.sha1(str(duration).encode())
    for p in list(images) + [music]:
        with open(p, "rb") as f:
            h.update(f.read(1 << 16))
    return int(h.hexdigest()[:8], 16)


async def render(images: list, music: str, out: str, duration: float = 0, music_start: float | None = None) -> dict:
    """Dựng video từ 1-4 ảnh + nhạc. music_start: ép điểm bắt đầu nhạc (None = tự chọn đoạn hay nhất).
    Dựng theo KHÚC ≤ GROUP cảnh (mỗi khúc một lệnh FFmpeg, chỉ hình), ghép khúc bằng chép luồng (không mã hoá lại),
    rồi trộn nhạc: RAM không tăng theo độ dài (trước đây cả video một lệnh: 60s nhạc nhanh 2GB RAM ở 1080p).
    Trả kế hoạch đã dùng (kiểu, BPM, đoạn nhạc, số cảnh) để báo lại người dùng."""
    import asyncio
    import tempfile
    images = images[:MAX_IMAGES]
    if not images:
        raise RuntimeError("Cần ít nhất 1 ảnh.")
    sizes = [await asyncio.to_thread(image_size, p) for p in images]
    info = await asyncio.to_thread(analyze_music, music)
    plan = plan_video(info, len(images), duration, await asyncio.to_thread(seed_for, images, music, duration))
    if music_start is not None:
        plan["music_start"] = music_start
    hd = config.OUTPUT_HD and min(max(w, h) for w, h in sizes) >= 1600
    W, H = (1080, 1920) if hd else (720, 1280)
    avail = xfade_transitions()
    grps = groups(plan)
    logging.info("Ảnh + nhạc: %d ảnh, %.0f BPM (%s), nhạc từ %.1fs, %d cảnh / %d khúc, %.1fs%s", len(images),
                 plan["bpm"], plan["style"], plan["music_start"], len(plan["slots"]), len(grps), plan["length"],
                 f", drop ở {plan['drop']:.1f}s" if plan["drop"] is not None else "")
    work = tempfile.mkdtemp(prefix="slides_", dir=os.path.dirname(os.path.abspath(out)))
    try:
        segs = []
        for k, g in enumerate(grps):
            fc, used = build_group(plan, g, sizes, W, H, avail)
            seg = os.path.join(work, f"seg{k}.mp4")
            await core_video._run_ffmpeg("-y", *core_video.thread_args(),
                                         *[a for img in used for a in ("-i", images[img])],
                                         "-filter_complex", fc, "-map", "[v]", "-an",
                                         *core_video.encode_args(H, FPS), "-t", f"{g[3] - g[2]:.4f}", seg)
            segs.append(seg)
        lst = os.path.join(work, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.writelines(f"file '{p}'\n" for p in segs)
        audio_in = ["-ss", f"{plan['music_start']:.3f}", "-t", f"{plan['length'] + 1:.3f}", "-i", music]
        afc = await core_video.two_pass_loudness(build_audio(plan, 0), audio_in, plan["length"])
        await core_video._run_ffmpeg("-y", "-f", "concat", "-safe", "0", "-i", lst, *audio_in,
                                     "-filter_complex", afc.replace("[0:a]", "[1:a]", 1), "-map", "0:v", "-map", "[a]",
                                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                                     "-movflags", "+faststart", "-t", f"{plan['length']:.3f}", out)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    plan["size"] = f"{W}x{H}"
    plan["segments"] = len(grps)
    return plan


def describe(plan: dict) -> str:
    short = plan.get("requested") and plan["requested"] > plan["length"] + 0.5
    return ((f"Nhạc chỉ còn đủ cho {plan['length']:.0f}s (bạn chọn {plan['requested']:.0f}s). " if short else "")
            + f"Nhạc {plan['bpm']:.0f} BPM, kiểu {STYLE_VI[plan['style']]}; lấy đoạn từ {plan['music_start']:.0f}s; "
            f"{len(plan['slots'])} cảnh, đổi cảnh đúng beat"
            + (f", drop ở giây {plan['drop']:.1f}" if plan["drop"] is not None else "") + f"; {plan['length']:.1f}s.")
