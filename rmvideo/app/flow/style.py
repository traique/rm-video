"""Lập kế hoạch dựng (REMAKE_STYLE, bản 42) — chạy trên Botkeep, TRƯỚC khi AI viết lời; kế hoạch (rmvideo/media/edl.py)
gửi kèm sang Kaggle để dựng y hệt.

Lớp A ("light", giữ thứ tự): video gốc máy quay đứng yên, đầu bếp nói liên tục -> đổi cỡ khung luân phiên ở chỗ chuyển
cảnh / ngắt câu (như quay nhiều máy), bỏ khoảng chết (không lời + gần như không chuyển động), tua nhanh 2-4 lần đoạn chờ
dài (không lời), chớp sáng khi hết đoạn tua, đẩy cận chậm cảnh cuối, thẻ "»» 30 PHÚT SAU" khi lời gốc có nói thời gian.
Mọi mép khúc nằm trong khoảng lặng (chừa PAD giây quanh lời): không bao giờ cắt giữa câu, không mất lời gốc nào.

Lớp B ("strong") nằm ở plan_strong (rmvideo/app/flow/style_b.py)."""
from __future__ import annotations

import math
import logging
import os
from collections.abc import Callable

import numpy as np

import config
from rmvideo.domain import recipe
from rmvideo.media import edl, proc

GAP_MIN = 1.2         # khoảng lặng ngắn hơn: không đụng
PAD = 0.3             # chừa sau câu trước / trước câu sau
SKIP_MIN = 4.0        # quãng chờ (đã chừa) dài từ chừng này -> tua nhanh
SKIP_MIN_BLIND = 6.0  # không đo được chuyển động: chỉ tua quãng thật dài
SKIP_TARGET = 1.8     # tua còn khoảng chừng này giây
DEAD_KEEP = 0.6       # khoảng chết chỉ giữ chừng này giây
SHOT_MIN = 2.5        # một cỡ khung giữ ít nhất
SHOT_MAX = 6.0        # ... và nhiều nhất: quãng dài hơn thì đổi cỡ (cận / xa) chia đều, hình không đứng yên (bản 50:
                      # job 7 có quãng 13 giây không đổi khung vì người gốc nói một mạch, không ngắt câu)
SENT_GAP = 0.35       # ngắt câu: khoảng lặng giữa hai câu từ chừng này
ZOOMS = (1.0, 1.12, 1.0, 1.18)
PUSH = 0.06
FPS = 2.0
ACT_HOLD = 4.0        # sau câu có nguyên liệu / định lượng: giữ tốc độ thường chừng này giây rồi mới được tua / bỏ —
                      # người gốc nói "nêm nửa muỗng đường, nửa muỗng hạt nêm" xong mới múc (bản 46: phần múc gia vị
                      # bị tua 2 lần, muỗng ở mép khung nên đo chuyển động không bắt được)


_WAIT_UNITS = {"phút", "giây", "tiếng", "giờ"}


def _action_line(text: str) -> bool:
    """Câu dặn một thao tác sắp làm (có nguyên liệu / định lượng) mà KHÔNG nói thời gian chờ: "nướng 30 phút" là báo
    chờ -> được tua ngay (thẻ »» 30 PHÚT SAU), còn "nêm nửa muỗng đường" thì thao tác theo sau phải được xem trọn."""
    import ingredients
    if any(x.split(" ", 1)[1] in _WAIT_UNITS for x in ingredients.numbers(text or "")):
        return False
    return bool(ingredients.find(text or "") or recipe.parse(text or ""))


# ---------------- chuyển động ----------------

def _motion_from_raw(raw: bytes, w: int = 32, h: int = 56) -> list[float]:
    n = len(raw) // (w * h)
    if n < 2:
        return []
    a = np.frombuffer(raw[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2)) / 255.0
    return [0.0] + [round(float(x), 4) for x in d]


async def motion(inp: str, end: float, thumbs: dict | None = None) -> list[float]:
    """Mức chuyển động theo từng nửa giây (chỉ số k = giây k/2) trên video gốc. Có ảnh 2 khung/giây từ phân tích
    Kaggle thì đọc ảnh (nhanh); không thì giải mã video ở cỡ tí hon. Lỗi: [] (kế hoạch chỉ dựa vào lời)."""
    try:
        if thumbs and thumbs.get("dir") and not thumbs.get("times") and thumbs.get("n", 0) >= 4:
            cmd = ["ffmpeg", "-v", "error", "-framerate", f"{FPS:g}", "-i", os.path.join(thumbs["dir"], "%05d.jpg"),
                   "-frames:v", str(int(end * FPS) + 1), "-vf", "scale=32:56,format=gray", "-f", "rawvideo", "-"]
        else:
            # bỏ giải mã khung không làm mốc + bộ lọc khối: nhanh ~2 lần (video 197s 1080p HEVC: 35s -> 16s một lõi;
            # Botkeep log 16/11 mất 155s), tín hiệu chuyển động gần như y hệt (tương quan 0,99)
            cmd = ["ffmpeg", "-v", "error", "-skip_frame", "noref", "-skip_loop_filter", "all", "-t", f"{end:.2f}",
                   "-i", inp, "-an", "-vf", f"fps={FPS:g},scale=32:56,format=gray", "-f", "rawvideo", "-"]
        res = await proc.run(cmd, stdout=True, timeout=600)
        return _motion_from_raw(res.stdout)
    except Exception as e:
        logging.info("Không đo được chuyển động (%s): kế hoạch dựng chỉ dựa vào lời", str(e)[:100])
        return []


def _mean(mot: list[float], a: float, b: float) -> float | None:
    xs = mot[int(a * FPS): max(int(a * FPS) + 1, int(b * FPS))]
    return float(np.mean(xs)) if xs else None


# ---------------- lớp A ----------------

def _speech_windows(speech: list, end: float) -> list:
    out: list = []
    for a, b in sorted((max(0.0, a), min(b, end)) for a, b in speech if a < end):
        if out and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        elif b > a:
            out.append((a, b))
    return out


def skip_label(segs: list, at: float, mode: str) -> str:
    """Chữ trên đoạn tua nhanh: "»» 30 PHÚT SAU" khi câu ngay trước quãng chờ (≤ 8 giây) có nói thời gian, không thì
    "»»" (không bịa thời gian). mode: auto | icon | off."""
    if mode == "off":
        return ""
    if mode == "auto":
        before = [t for a, b, t in segs if b <= at + 0.05 and at - b <= 8.0]
        for t in reversed(before[-2:]):
            for q in recipe.parse(t):
                if q.unit in ("phút", "giờ"):
                    return f"»» {recipe.fmt_number(q.value)} {'PHÚT' if q.unit == 'phút' else 'TIẾNG'} SAU"
    return "»»"


def plan_light(end: float, segs: list, cuts: list, mot: list[float], stretch: float = 1.0,
               label_mode: str = "auto", level: str = "light", drop: Callable[[float, float], bool] | None = None
               ) -> edl.EditPlan:
    """segs: lời gốc [(a, b, lời)] trục GỐC trong [0, end]; cuts: điểm chuyển cảnh trục gốc; mot: motion().
    drop(a, b): quãng lời được phép bỏ (lớp B: lời tán gẫu lúc chờ) -> coi như khoảng lặng."""
    speech = _speech_windows([(a, b) for a, b, t in segs if not (drop and drop(a, b))], end)
    sents = sorted((a, b) for a, b, t in segs)               # mọi câu (kể cả câu được phép bỏ): chỗ đổi khung tránh
    acts = [b for a, b, t in segs if not (drop and drop(a, b)) and _action_line(t)]
    vals = sorted(mot) if mot else []
    lo = vals[int(len(vals) * 0.35)] if vals else None
    dead = vals[int(len(vals) * 0.15)] if vals else None
    # 1. khoảng lặng bên trong (trước câu đầu cũng tính; sau câu cuối là cảnh món thành phẩm: giữ nguyên)
    gaps = []
    prev = 0.0
    for a, b in speech:
        if a - prev >= GAP_MIN:
            gaps.append((prev, a, prev == 0.0))
        prev = b
    # 2. quãng: (a, b, loại) phủ [0, end]; loại: norm | skip; phần bị bỏ thì không có
    spans: list = []
    cur = 0.0
    removed = skipped_src = 0.0
    for g0, g1, lead in gaps:
        i0, i1 = (g0 if lead else g0 + PAD), g1 - PAD
        if any(g0 - 0.35 <= e <= g0 + 0.05 for e in acts):
            i0 = max(i0, g0 + ACT_HOLD)                # thao tác vừa nói tới: cho xem trọn
        L = i1 - i0
        if L < 1.0:
            continue
        m = _mean(mot, i0, i1) if mot else None
        if L >= (SKIP_MIN if m is not None else SKIP_MIN_BLIND) and (m is None or lo is None or m <= lo * 1.2):
            if i0 - cur > 1e-3:
                spans.append((cur, i0, "norm"))
            spans.append((i0, i1, "skip"))
            skipped_src += L
            cur = i1
        elif m is not None and dead is not None and m <= dead and L >= 1.0 and not lead:
            if i0 + DEAD_KEEP - cur > 1e-3:
                spans.append((cur, i0 + DEAD_KEEP, "norm"))
            removed += L - DEAD_KEEP
            cur = i1
    if end - cur > 1e-3:
        spans.append((cur, end, "norm"))
    # 3. chỗ đổi cỡ khung: điểm chuyển cảnh + chỗ ngắt câu (giữa khoảng lặng giữa hai câu)
    breaks = sorted({round(c, 3) for c in cuts if 0 < c < end})
    sent = sorted(round((x[1] + y[0]) / 2, 3) for x, y in zip(speech, speech[1:]) if y[0] - x[1] >= SENT_GAP)
    pieces: list = []
    k_zoom = 0
    after_skip = False
    for a, b, kind in spans:
        if kind == "skip":
            speed = min(4.0, max(2.0, round((b - a) / SKIP_TARGET * 2) / 2))
            pieces.append(edl.Piece(round(a, 3), round(b, 3), speed=speed, tag="skip"))
            after_skip = True
            continue
        pts = [a]
        cand = sorted({x for x in breaks + sent if a + SHOT_MIN <= x <= b - SHOT_MIN})
        for x in cand:                    # chuyển cảnh thật: đổi ngay; ngắt câu: cách lần đổi trước ≥ 3 giây
            if x - pts[-1] >= (1.5 if x in breaks else SHOT_MIN * 1.2):
                pts.append(x)
        pts.append(b)
        if len(pieces) + len(pts) < edl.MAX_PIECES - 25:     # chừa chỗ cho khúc tua / khúc phụ của lớp B
            pts = _split_long(pts, sents)
        for x, y in zip(pts, pts[1:]):
            if y - x < edl.MIN_PIECE and pieces and pieces[-1].tag != "skip":
                p = pieces[-1]
                pieces[-1] = edl.Piece(p.a, y, p.speed, p.zoom, p.push, p.fx, p.role, p.tag)
                continue
            pieces.append(edl.Piece(round(x, 3), round(y, 3), zoom=ZOOMS[k_zoom % len(ZOOMS)],
                                    fx="flash" if after_skip else ""))
            k_zoom += 1
            after_skip = False
    # 4. đẩy cận chậm cảnh cuối (thường là món thành phẩm)
    for i in range(len(pieces) - 1, -1, -1):
        p = pieces[i]
        if p.tag != "skip" and p.b - p.a >= 2.0 and p.a >= end * 0.7:
            pieces[i] = edl.Piece(p.a, p.b, p.speed, 1.0, PUSH, p.fx, p.role, p.tag)
            break
    plan = edl.EditPlan(pieces=pieces, stretch=stretch, level=level)
    finalize(plan, segs, label_mode, end, removed, skipped_src)
    return plan


def _split_long(pts: list, sents: list | None = None) -> list:
    """Chèn chỗ đổi cỡ khung vào quãng dài hơn SHOT_MAX: chia đều, mỗi chỗ dời về khoảng lặng giữa hai câu gần nhất
    (±1,5 giây). sents: [(a, b)] các câu lời gốc, theo thứ tự."""
    out = [pts[0]]
    for x, y in zip(pts, pts[1:]):
        n = math.ceil((y - x) / SHOT_MAX) if y - x > SHOT_MAX else 1
        for i in range(1, n):
            t = _calm_point(x + (y - x) * i / n, out[-1], y, sents or [])
            if t is not None:
                out.append(t)
        out.append(y)
    return out


def _calm_point(t: float, lo: float, hi: float, sents: list) -> float | None:
    """Chỗ đổi khung gần t: khoảng lặng giữa hai câu nếu có (±1,5 giây); không thì giữ t nhưng KHÔNG đặt trong 0,6 giây
    cuối một câu — khúc phụ chèn ngay sau câu đó bị kéo lùi về ranh giới gần (< 0,5 giây, style_b._split_at) và cắt đôi
    câu (video mực: khúc tóm tắt chen giữa câu 174,0s). Cách ranh giới hai bên ≥ SHOT_MIN; không được thì None."""
    def ok(p: float) -> bool:
        return p - lo >= SHOT_MIN and hi - p >= SHOT_MIN
    gaps: list[float] = [(b1 + a2) / 2 for (_, b1), (a2, _) in zip(sents, sents[1:]) if a2 - b1 >= 0.1]
    near: list[float] = [g for g in gaps if abs(g - t) <= 1.5 and ok(g)]
    if near:
        best = min(near, key=lambda g: abs(g - t))
        return round(best, 3)
    for a, b in sents:
        if max(a, b - 0.6) <= t < b:
            t = max(a, b - 0.6) - 0.05 if b - 0.6 > a else t
            break
    return round(t, 3) if ok(t) else None


def finalize(plan: edl.EditPlan, segs: list, label_mode: str, end: float, removed: float = 0.0,
             skipped_src: float = 0.0, extra: str = "") -> None:
    """Thẻ tua nhanh (mốc theo khúc hiện tại) + câu tóm tắt kế hoạch. Gọi lại sau mỗi lần sửa khúc."""
    starts = plan.starts()
    keep = [o for o in plan.overlays if o.kind != "skip"]
    ovs = []
    used: set = set()
    for p, s in zip(plan.pieces, starts):
        if p.tag == "skip":
            text = skip_label(segs, p.a, label_mode)
            if text != "»»" and text in used:
                # cùng một quãng chờ nhắc lại ("luộc khoảng 4 phút" ... "sau khi luộc khoảng tầm 4 phút"): chỉ ghi thời
                # gian một lần, không thì người xem tưởng chờ gấp đôi (bản 47: hai thẻ »» 4 PHÚT SAU)
                text = "»»"
            used.add(text)
            if text:
                ovs.append(edl.Overlay(round(s + 0.1, 2), round(min(1.6, p.length * plan.stretch - 0.15), 2), "skip",
                                       text))
    plan.overlays = sorted(keep + ovs, key=lambda o: o.t)
    pieces = plan.pieces
    skipped_src = skipped_src or sum(p.b - p.a for p in pieces if p.tag == "skip")
    n_skip = sum(1 for p in pieces if p.tag == "skip")
    zchg = sum(1 for x, y in zip(pieces, pieces[1:]) if x.zoom != y.zoom)
    plan.summary = (f"Nhịp dựng {'nhẹ' if plan.level == 'light' else 'mạnh'}: {len(pieces)} khúc, đổi cỡ khung {zchg} lần"
                    + (f", bỏ {removed:.0f}s khoảng chết" if removed >= 1 else "")
                    + (f", tua nhanh {n_skip} đoạn chờ ({skipped_src:.0f}s còn "
                       f"{sum(p.length for p in pieces if p.tag == 'skip') * plan.stretch:.0f}s)" if n_skip else "")
                    + extra + f"; video {end * plan.stretch:.0f}s -> {plan.out_dur():.0f}s.")


def level() -> str:
    return config.REMAKE_STYLE if config.REMAKE_STYLE in ("light", "strong") else ""
