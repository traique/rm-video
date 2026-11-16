"""Kế hoạch dựng (EDL) — bản 42, REMAKE_STYLE: video ra là chuỗi KHÚC lấy từ video gốc, mỗi khúc có tốc độ, cỡ khung,
đẩy cận, chuyển cảnh riêng. Dùng chung Botkeep và Kaggle (gửi kèm spec dạng dict), nên chỉ có kiểu dữ liệu, phép đổi
mốc thời gian và phần sinh filter FFmpeg; phần LẬP kế hoạch (cần lời gốc, AI) ở rmvideo/app/flow/style.py.

Hai loại khúc:
- "main": lấy THEO THỨ TỰ video gốc (a tăng dần, không chồng nhau). Dựng bằng MỘT đầu vào + split/trim/concat: thứ tự
  gốc nên khung hình không phải xếp hàng chờ -> RAM không tăng dù nhiều khúc. Lời gốc nằm trong khúc nào đi theo khúc đó.
- "side": khúc lặp lại / đảo chỗ (điểm danh nguyên liệu, tóm tắt, cảm thán dời về cuối). Mỗi dãy khúc side liền nhau
  dựng trước thành MỘT clip phụ ngắn (renderer.side_clip), bản dựng chính nhận clip đó như một đầu vào (như đoạn mở
  đầu). Khúc side không mang lời gốc (lời của nó do AI viết từ bảng nguyên liệu đã đối chiếu).

Mốc "ra" ở đây chưa cộng đoạn mở đầu (teaser); renderer / Remake tự cộng.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field

MIN_PIECE = 0.5
MAX_PIECES = 90            # trần số khúc (filter graph, số chuỗi màu chạy song song)
MAX_SIDE_RUNS = 3          # tối đa 3 clip phụ (luật R6: Botkeep 2GB)
FLASH = 0.18               # giây chớp trắng ở đầu khúc có fx="flash"
SKIP_VOLUME = 0.5          # tiếng nền ở khúc tua nhanh nhỏ lại


@dataclass(frozen=True)
class Piece:
    a: float
    b: float
    speed: float = 1.0
    zoom: float = 1.0
    push: float = 0.0
    fx: str = ""                  # "" | "flash"
    role: str = "main"            # main | side
    tag: str = ""                 # skip (tua nhanh) | montage | recap | moved | ...

    @property
    def length(self) -> float:    # giây ra (chưa nhân stretch)
        return max(0.0, (self.b - self.a) / max(self.speed, 1e-6))


@dataclass(frozen=True)
class Overlay:
    t: float                      # giây trên trục RA (chưa cộng đoạn mở đầu)
    dur: float
    kind: str                     # skip | ingredient | tip | price
    text: str


@dataclass
class EditPlan:
    pieces: list = field(default_factory=list)
    overlays: list = field(default_factory=list)
    stretch: float = 1.0
    level: str = "light"
    summary: str = ""
    texts: list = field(default_factory=list)   # lời "gốc" của khúc side (do mã viết): [{"t", "dur", "text"}] trục ra

    # ---------- (de)serialize ----------
    def to_dict(self) -> dict:
        return {"v": 1, "pieces": [asdict(p) for p in self.pieces], "overlays": [asdict(o) for o in self.overlays],
                "stretch": self.stretch, "level": self.level, "summary": self.summary, "texts": list(self.texts)}

    @staticmethod
    def from_dict(d: dict | None) -> EditPlan | None:
        if not d or d.get("v") != 1 or not d.get("pieces"):
            return None
        return EditPlan([Piece(**p) for p in d["pieces"]], [Overlay(**o) for o in d.get("overlays") or []],
                        float(d.get("stretch") or 1.0), d.get("level") or "light", d.get("summary") or "",
                        list(d.get("texts") or []))

    # ---------- trục thời gian ----------
    def starts(self) -> list[float]:
        out, t = [], 0.0
        for p in self.pieces:
            out.append(t)
            t += p.length * self.stretch
        return out

    def out_dur(self) -> float:
        return sum(p.length for p in self.pieces) * self.stretch

    def to_out(self, t: float) -> float:
        """Giây trên video gốc -> giây ra, theo các khúc MAIN. Rơi vào quãng đã bỏ: lấy đầu khúc kế tiếp."""
        best = None
        for p, s in zip(self.pieces, self.starts()):
            if p.role != "main":
                continue
            if p.a - 1e-6 <= t <= p.b + 1e-6:
                return s + (min(max(t, p.a), p.b) - p.a) / p.speed * self.stretch
            if p.a > t and best is None:
                best = s
        return best if best is not None else self.out_dur()

    def src_time(self, x: float) -> float:
        """Giây ra -> giây trên video gốc (ảnh cho AI, ảnh bìa)."""
        for p, s in zip(self.pieces, self.starts()):
            e = s + p.length * self.stretch
            if s - 1e-6 <= x < e or p is self.pieces[-1]:
                return p.a + min(max(x - s, 0.0), p.length * self.stretch) / self.stretch * p.speed
        return 0.0

    def map_segments(self, segs: list) -> list:
        """[(a, b, lời)] trục gốc -> trục ra. Đoạn nằm hẳn trong quãng bỏ (lời tán gẫu đã duyệt bỏ) thì mất."""
        out = []
        for a, b, t in segs:
            oa, ob = self.to_out(a), self.to_out(b)
            if ob - oa >= 0.05:
                out.append((round(oa, 2), round(ob, 2), t))
        return out

    def map_windows(self, wins: list) -> list:
        out: list = []
        for a, b in wins:
            oa, ob = self.to_out(a), self.to_out(b)
            if ob - oa < 0.05:
                continue
            if out and oa <= out[-1][1] + 0.01:
                out[-1] = (out[-1][0], max(out[-1][1], ob))
            else:
                out.append((oa, ob))
        return [(round(a, 2), round(b, 2)) for a, b in out]

    def cut_points(self) -> list[float]:
        """Mốc ra có đổi cảnh (đổi cỡ khung, nối hai quãng không liền, đổi tốc độ, khúc side): để câu mới bắt đầu
        đúng lúc đổi cảnh (scenes.snap_to_cuts)."""
        out = []
        prev = None
        for p, s in zip(self.pieces, self.starts()):
            if prev is not None and (p.role != "main" or prev.role != "main" or abs(p.a - prev.b) > 0.05
                                     or p.zoom != prev.zoom or p.speed != prev.speed):
                out.append(round(s, 2))
            prev = p
        return out

    def side_runs(self) -> list[list[int]]:
        """Các dãy chỉ số khúc side liền nhau (mỗi dãy = một clip phụ)."""
        runs: list = []
        for k, p in enumerate(self.pieces):
            if p.role == "side":
                if runs and runs[-1][-1] == k - 1:
                    runs[-1].append(k)
                else:
                    runs.append([k])
        return runs

    def validate(self) -> list[str]:
        """Lỗi kỹ thuật của kế hoạch (rỗng = dựng được)."""
        errs = []
        mains = [p for p in self.pieces if p.role == "main"]
        if not self.pieces:
            errs.append("không có khúc nào")
        if len(self.pieces) > MAX_PIECES:
            errs.append(f"{len(self.pieces)} khúc > {MAX_PIECES}")
        for x, y in zip(mains, mains[1:]):
            if y.a < x.b - 1e-6:
                errs.append(f"khúc chính không theo thứ tự gốc ({x.a:.2f}-{x.b:.2f} rồi {y.a:.2f})")
        for p in self.pieces:
            if p.b - p.a < MIN_PIECE - 1e-6 or p.speed <= 0 or p.zoom < 1.0:
                errs.append(f"khúc {p.a:.2f}-{p.b:.2f} sai (dài/tốc độ/cỡ khung)")
        if len(self.side_runs()) > MAX_SIDE_RUNS:
            errs.append(f"{len(self.side_runs())} clip phụ > {MAX_SIDE_RUNS}")
        return errs


# ---------------- FFmpeg ----------------

def zoomed_bands(bands: list, zooms: Iterable[float]) -> list:
    """Dải chữ in cứng (tỉ lệ trên khung gốc) phủ đủ mọi cỡ khung đã dùng: cận 12% thì dải dịch xa tâm và dày thêm,
    che chữ sau khi nối khúc nên lấy hợp của mọi cỡ."""
    out = []
    zs = {1.0, *zooms}
    for top, bottom in bands:
        tops = [0.5 + (top - 0.5) * z for z in zs]
        bots = [0.5 + (bottom - 0.5) * z for z in zs]
        out.append((max(0.0, min(tops)), min(1.0, max(bots))))
    return out


def _atempo(factor: float) -> str:
    """atempo nối tầng (mỗi tầng 0.5-2.0, chạy được cả FFmpeg cũ)."""
    if abs(factor - 1) < 1e-3:
        return ""
    parts = []
    while factor > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.4f}")
    return ",".join(parts) + ","


def push_filter(W: int, H: int, push: float, length: float) -> str:
    """Đẩy cận chậm trong khúc (giống đoạn mở đầu): phóng dần theo thời gian rồi cắt về đúng khung."""
    L = max(length, 0.1)
    sc = f"trunc({W}*(1+{push:.3f}*t/{L:.2f})/2)*2"
    sh = f"trunc({H}*(1+{push:.3f}*t/{L:.2f})/2)*2"
    return (f"scale=w='{sc}':h=-2:eval=frame,crop={W}:{H}:x='({sc}-{W})/2':y='({sh}-{H})/2',")


def video_graph(plan: EditPlan, look: Callable[[float], str], fps: float, W: int, H: int,
                side_inputs: dict | None = None, lead: list | None = None) -> str:
    """Chuỗi filter dựng hình theo kế hoạch: trả "<các nhánh>;[e0][e1]...concat=n=..:v=1:a=0" (chưa gắn nhãn ra, caller
    nối tiếp ",<che chữ>,subtitles..."). look(zoom): chuỗi màu + cỡ khung của khúc. side_inputs: {chỉ số dãy side: số
    đầu vào clip phụ}. lead: nhãn đứng trước (đoạn mở đầu "[tzv]")."""
    side_inputs = side_inputs or {}
    runs = plan.side_runs()
    run_of = {k: r for r, ks in enumerate(runs) for k in ks}
    mains = [k for k, p in enumerate(plan.pieces) if p.role == "main"]
    parts = []
    if mains:
        parts.append(f"[0:v]split={len(mains)}" + "".join(f"[s{k}]" for k in mains) if len(mains) > 1 else
                     f"[0:v]null[s{mains[0]}]")
    labels = list(lead or [])
    done_runs = set()
    for k, p in enumerate(plan.pieces):
        if p.role == "main":
            chain = (f"[s{k}]trim=start={p.a:.3f}:end={p.b:.3f},"
                     f"setpts=(PTS-STARTPTS)*{plan.stretch / p.speed:.5f},fps={fps:g},{look(p.zoom)}")
            if p.push > 0:
                chain += "," + push_filter(W, H, p.push, p.length * plan.stretch).rstrip(",")
            if p.fx == "flash":
                chain += f",fade=t=in:st=0:d={FLASH}:color=white"
            parts.append(chain + f",setsar=1[e{k}]")
            labels.append(f"[e{k}]")
        else:
            r = run_of[k]
            if r in done_runs:
                continue
            done_runs.add(r)
            idx = side_inputs.get(r)
            if idx is None:
                raise ValueError("thiếu clip phụ cho khúc side")
            parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,fps={fps:g},scale={W}:{H},setsar=1[r{r}]")
            labels.append(f"[r{r}]")
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0"


def audio_graph(plan: EditPlan, src: str, out: str, side_audio: dict | None = None, skip_volume: float = SKIP_VOLUME,
                tag: str = "x") -> str:
    """Tiếng đi theo kế hoạch: src (vd "[2:a]") -> "[out]". Khúc tua nhanh: atempo + nhỏ lại; khúc side: tiếng của clip
    phụ (side_audio {dãy: "[k:a]"}) hoặc im lặng. Trả chuỗi kết thúc bằng ";"."""
    side_audio = side_audio or {}
    runs = plan.side_runs()
    run_of = {k: r for r, ks in enumerate(runs) for k in ks}
    mains = [k for k, p in enumerate(plan.pieces) if p.role == "main"]
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    parts = []
    if mains:
        parts.append(f"{src}{fmt},asplit={len(mains)}" + "".join(f"[{tag}s{k}]" for k in mains) if len(mains) > 1
                     else f"{src}{fmt},anull[{tag}s{mains[0]}]")
    labels = []
    done = set()
    for k, p in enumerate(plan.pieces):
        if p.role == "main":
            vol = f"volume={skip_volume:g}," if p.speed > 1.01 else ""
            parts.append(f"[{tag}s{k}]atrim=start={p.a:.3f}:end={p.b:.3f},asetpts=PTS-STARTPTS,"
                         f"{_atempo(p.speed / plan.stretch)}{vol}{fmt}[{tag}e{k}]")
            labels.append(f"[{tag}e{k}]")
        else:
            r = run_of[k]
            if r in done:
                continue
            done.add(r)
            d = sum(plan.pieces[j].length for j in runs[r]) * plan.stretch
            sa = side_audio.get(r)
            parts.append((f"{sa}{fmt},asetpts=PTS-STARTPTS,apad=whole_dur={d:.3f},atrim=0:{d:.3f}[{tag}r{r}]") if sa
                         else f"anullsrc=r=44100:cl=stereo,atrim=0:{d:.3f}[{tag}r{r}]")
            labels.append(f"[{tag}r{r}]")
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1{out};"
