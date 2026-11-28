"""Công thức hiệu ứng kiểu trend TikTok/CapCut cho chế độ 📸 Ảnh + nhạc — mỗi lần tạo một "công thức" khác.

Mỗi công thức = màu (look) + hiệu ứng bám nhịp (nảy zoom, rung, loé trắng, glitch RGB, nháy đen, đen trắng bật màu,
lật gương theo ô nhịp) + lớp phủ (hạt phim, viền tối, khung điện ảnh, phát sáng) + bộ chuyển cảnh riêng. Chọn theo
kiểu nhạc (êm / nhún / sôi), ngẫu nhiên thật mỗi lần, và TRÁNH các công thức đã dùng ở vài video gần nhất
(lưu trong recent_looks.json) nên làm lại cùng ảnh + nhạc vẫn ra video khác.

Chỉ dùng bộ lọc có trong FFmpeg 5.1 (Botkeep dùng ffmpeg của Debian 12) và bật/tắt theo thời gian được (timeline):
rgbashift, hue, curves, colorbalance, vignette, noise, hflip, drawbox, gblur, blend — bài tự kiểm tra lúc khởi động
(core_video.selftest_sync) có thử đủ các bộ lọc này.
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RECENT_FILE = os.environ.get("RECENT_LOOKS") or os.path.join(BASE, "recent_looks.json")
RECENT_KEEP = 8

# pulse: nảy zoom ("all" mỗi beat | "down" phách mạnh | "none"); shake / flash: "after" (phách mạnh sau drop hoặc
# nửa sau video) | "down" (mọi phách mạnh) | "drop" (chỉ điểm drop) | "none"; fx: hiệu ứng thêm (xem video_fx)
TEMPLATES = {
    "flash_pop": {"vi": "Flash Pop", "styles": ("groove", "hype"),
                  "look": "eq=contrast=1.08:saturation=1.18,", "pulse": "all", "shake": "after", "flash": "after",
                  "fx": [], "trans": ["zoomin", "fadewhite", "slideleft", "slideup", "circlecrop", "squeezeh"]},
    "glitch_rgb": {"vi": "Glitch RGB", "styles": ("groove", "hype"),
                   "look": "eq=contrast=1.1:saturation=1.12,", "pulse": "all", "shake": "down", "flash": "drop",
                   "fx": ["rgb"], "trans": ["hblur", "pixelize", "slideleft", "slideright", "zoomin", "hrslice"]},
    "strobe": {"vi": "Strobe nháy đen", "styles": ("hype",),
               "look": "eq=contrast=1.14:saturation=1.06,", "pulse": "all", "shake": "after", "flash": "drop",
               "fx": ["strobe"], "trans": ["fadeblack", "slideup", "slidedown", "zoomin", "squeezev"]},
    "mono_pop": {"vi": "Đen trắng bật màu", "styles": ("groove", "hype"),
                 "look": "eq=contrast=1.1,", "pulse": "down", "shake": "none", "flash": "drop",
                 "fx": ["mono_pop"], "trans": ["fadegrays", "slideleft", "zoomin", "wipeleft", "circleopen"]},
    "mirror_beat": {"vi": "Lật gương theo nhịp", "styles": ("groove", "hype"),
                    "look": "eq=contrast=1.06:saturation=1.12,", "pulse": "down", "shake": "none", "flash": "down",
                    "fx": ["mirror"], "trans": ["slideright", "slideleft", "zoomin", "horzopen", "rectcrop"]},
    "y2k_neon": {"vi": "Y2K Neon", "styles": ("groove", "hype"),
                 "look": "eq=contrast=1.1:saturation=1.38,", "pulse": "all", "shake": "none", "flash": "drop",
                 "fx": ["hue_drift", "rgb_soft", "glow_light"], "trans": ["radial", "circleopen", "zoomin", "slideup"]},
    "dreamy_glow": {"vi": "Dreamy Glow", "styles": ("calm", "groove"),
                    "look": "eq=contrast=0.97:saturation=1.08:brightness=0.02,", "pulse": "none", "shake": "none",
                    "flash": "none", "fx": ["glow", "vignette"],
                    "trans": ["dissolve", "fade", "smoothleft", "smoothup", "circleopen"]},
    "film_vintage": {"vi": "Film Vintage", "styles": ("calm", "groove"),
                     "look": "curves=preset=vintage,", "pulse": "none", "shake": "none", "flash": "none",
                     "fx": ["grain", "vignette"], "trans": ["fadeblack", "dissolve", "fade", "wipeleft"]},
    "cinematic": {"vi": "Cinematic", "styles": ("calm", "groove"),
                  "look": "colorbalance=rs=-0.04:bs=0.07:rh=0.07:bh=-0.05,eq=contrast=1.07:saturation=1.05,",
                  "pulse": "down", "shake": "none", "flash": "none", "fx": ["vignette", "letterbox"],
                  "trans": ["smoothleft", "smoothright", "fade", "fadeblack", "dissolve"]},
    "pastel_soft": {"vi": "Pastel Soft", "styles": ("calm",),
                    "look": "curves=preset=lighter,eq=contrast=0.95:saturation=0.96,", "pulse": "none",
                    "shake": "none", "flash": "none", "fx": ["glow_light"],
                    "trans": ["dissolve", "fade", "smoothup", "smoothdown"]},
}


def load_recent(path: str | None = None) -> list:
    path = path or RECENT_FILE
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return [x for x in d if x in TEMPLATES] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def remember(name: str, path: str | None = None):
    path = path or RECENT_FILE
    recent = [name] + [x for x in load_recent(path) if x != name]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recent[:RECENT_KEEP], f)
    except OSError:
        pass


def pick(style: str, rnd, recent: list | None = None) -> str:
    """Công thức hợp kiểu nhạc, ngẫu nhiên, tránh các công thức vừa dùng (giữ lại ít nhất 1 lựa chọn)."""
    ok = [k for k, t in TEMPLATES.items() if style in t["styles"]]
    avoid = set((recent or [])[: max(0, len(ok) - 1)])
    cands = [k for k in ok if k not in avoid] or ok
    return cands[rnd.randrange(len(cands))]


def beat_sets(tpl: dict, style: str, beats: list, drop_t, length: float) -> dict:
    """Các mốc (giây, trục video) cho nảy zoom / rung / loé / glitch / nháy đen / bật màu theo công thức."""
    down = [b for k, b in enumerate(beats) if k % 4 == 0]
    after = drop_t if drop_t is not None else length * 0.5
    tail = length - 0.3

    def sel(mode):
        if mode == "all":
            return list(beats)
        if mode == "down":
            return [b for b in down if b < tail]
        if mode == "after":
            out = [b for b in down if after - 1e-6 <= b < tail]
            return sorted(set(out + ([drop_t] if drop_t is not None else [])))
        if mode == "drop":
            return [drop_t] if drop_t is not None else []
        return []
    out = {"pulse": sel(tpl["pulse"]), "shake": sel(tpl["shake"]), "flashes": sel(tpl["flash"])}
    out["rgb"] = [b for b in beats if b < tail] if style == "hype" else [b for b in down if b < tail]
    off = [b + (beats[1] - beats[0]) / 2 if len(beats) > 1 else b for b in beats]      # nửa phách (nháy giữa 2 beat)
    out["strobe"] = [b for b in off if after - 1e-6 <= b < tail][::2]
    out["pop"] = [b for b in down if b < tail] + ([drop_t] if drop_t is not None else [])
    bar = 4 * (beats[1] - beats[0]) if len(beats) > 1 else 2.0
    out["mirror"] = [(round(k * bar, 4), round(min(length, (k + 1) * bar), 4)) for k in range(1, int(length / bar) + 1, 2)]
    return out


def _windows(times: list, t0: float, t1: float, dur: float) -> str:
    """Biểu thức enable: đúng trong [b, b+dur] của các mốc nằm trong khúc (thời gian tương đối đầu khúc)."""
    ws = [f"between(t,{b - t0:.3f},{b - t0 + dur:.3f})" for b in times if t0 - dur <= b < t1]
    return "+".join(ws) if ws else ""


def _decay(times: list, t0: float, t1: float, amp: float, tau: float) -> str:
    ts = [f"{amp}*exp(-(t-{b - t0:.3f})/{tau})*gte(t,{b - t0:.3f})" for b in times if t0 - 1.0 <= b < t1]
    return "+".join(ts) if ts else "0"


def video_fx(tpl: dict, sets: dict, t0: float, t1: float, W: int, H: int, period: float, tag: str) -> str:
    """Chuỗi bộ lọc hiệu ứng cho MỘT khúc [t0, t1] (thời gian trong khúc bắt đầu từ 0). Nhận khung từ chuỗi trước,
    trả về chuỗi kết thúc bằng dấu phẩy (có thể chứa nhánh split/blend, nhãn riêng theo `tag`)."""
    k = W / 1080
    out = ""
    fx = tpl["fx"]
    if "mono_pop" in fx:                                       # đen trắng, bật màu ở phách mạnh rồi nhạt dần
        out += f"hue=s='min(1.25,{_decay(sets['pop'], t0, t1, 1.25, 0.45)})',"
    if "hue_drift" in fx:
        out += f"hue=h='14*sin(2*PI*(t+{t0:.3f})/{4 * period:.4f})',"
    if "glow" in fx or "glow_light" in fx:                     # phát sáng mềm: làm mờ bản thu nhỏ rồi trộn screen
        op = 0.32 if "glow" in fx else 0.18
        out += (f"split=2[{tag}ga][{tag}gb];[{tag}gb]scale={W // 4}:{H // 4},gblur=sigma=6,scale={W}:{H}[{tag}gc];"
                f"[{tag}ga][{tag}gc]blend=all_mode=screen:all_opacity={op},format=yuv420p,")
    if "mirror" in fx:
        w = "+".join(f"between(t,{a - t0:.3f},{b - t0:.3f})" for a, b in sets["mirror"] if b > t0 and a < t1)
        if w:
            out += f"hflip=enable='{w}',"
    if "rgb" in fx:
        w = _windows(sets["rgb"], t0, t1, 0.09)
        if w:
            out += f"rgbashift=rh={-int(14 * k)}:bh={int(14 * k)}:gv={int(4 * k)}:enable='{w}',"
    if "rgb_soft" in fx:
        out += f"rgbashift=rh={-int(4 * k)}:bh={int(4 * k)},"
    if "strobe" in fx:
        w = _windows(sets["strobe"], t0, t1, 0.05)
        if w:
            out += f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{w}',"
    if "grain" in fx:
        out += "noise=alls=9:allf=t,"
    if "vignette" in fx:
        out += "vignette=angle=PI/5,"
    if "letterbox" in fx:
        out += ("drawbox=x=0:y=0:w=iw:h=ih*0.085:color=black:t=fill,"
                "drawbox=x=0:y=ih*0.915:w=iw:h=ih*0.085:color=black:t=fill,")
    return out
