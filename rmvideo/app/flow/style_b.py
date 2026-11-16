"""Lớp B của kế hoạch dựng (REMAKE_STYLE=strong, bản 42): đảo thứ tự CÓ KIỂM SOÁT cho video nấu ăn, dựa trên nhãn
bước + nguyên liệu đã đối chiếu (rmvideo/domain/recipe.py). AI chỉ dán nhãn; MÃ kiểm luật cứng R1-R7, vi phạm là bỏ
toàn bộ phép B, chỉ áp lớp A, ghi lý do.

Phép được làm:
  B2 điểm danh nguyên liệu  4-5 khúc ~1.2 giây LẶP LẠI lúc từng nguyên liệu xuất hiện, thẻ "1 kg cánh gà" (chỉ số đã
                            đối chiếu); các bước sau vẫn chiếu đủ, đúng thứ tự
  B3 bỏ phần chào hỏi      bước intro ở đầu không có nguyên liệu / con số
  B4 dời cảm thán về cuối   tối đa 1 đoạn reaction giữa video, không có bước / nguyên liệu / số
  B5 tóm tắt trước khi kết  4-6 bước chính, ~0.7 giây mỗi bước (lặp)
  B6 đổi chỗ hai bước sơ chế  tối đa 1 lần: hai bước prep liền nhau trước lần nấu đầu, AI nói độc lập VÀ mã thấy
                            không chung nguyên liệu
  B9 bỏ lời tán gẫu lúc chờ  đoạn lời gốc thuộc bước wait/other/reaction/intro/promo, không có nguyên liệu / số
Luật:
  R1 đủ bước   mỗi bước prep/cook/plate còn ≥ 60% thời lượng (tính cả khúc tua nhanh) và đủ mọi câu không phải tán gẫu
  R2 đúng thứ tự  lần xuất hiện chính của các bước cook/plate tăng dần theo video gốc
  R3 sơ chế trước nấu  mọi bước prep đứng trước bước cook đầu tiên trong video gốc vẫn đứng trước nó trong video ra
  R4 không mất số liệu  mọi câu lời gốc có nguyên liệu / con số đều còn, cùng thứ tự tương đối
  R5 lặp không thay thế  khúc montage / recap là bản lặp: phần gốc của nó vẫn còn trong khúc chính
  R6 giới hạn kỹ thuật  EditPlan.validate()
  R7 tua nhanh  khúc tua nhanh không chứa câu không phải tán gẫu
"""
from __future__ import annotations

import ingredients
from rmvideo.app.flow import style
from rmvideo.domain import recipe
from rmvideo.media import edl

MONTAGE_MAX = 5
MONTAGE_LEN = 1.2
RECAP_MAX = 5
RECAP_LEN = 0.7
CHAT_KINDS = ("wait", "other", "reaction", "intro", "promo")
CORE_KINDS = ("prep", "cook", "plate")


def _step_at(steps: list, t: float) -> recipe.Step | None:
    return next((s for s in steps if s.t0 - 0.05 <= t <= s.t1 + 0.05), None)


def _informative(text: str) -> bool:
    return bool(ingredients.find(text) or recipe.parse(text))


def chatter(segs: list, steps: list) -> list[bool]:
    """B9: câu nào là tán gẫu (bỏ được): thuộc bước tán gẫu và không có nguyên liệu / con số."""
    out = []
    for a, b, t in segs:
        st = _step_at(steps, (a + b) / 2)
        out.append(bool(st and st.kind in CHAT_KINDS and not _informative(t)))
    return out


def _cut_out(pieces: list, a: float, b: float) -> list:
    """Bỏ quãng [a, b] (trục gốc) khỏi các khúc main."""
    out = []
    for p in pieces:
        if p.role != "main" or p.b <= a or p.a >= b:
            out.append(p)
            continue
        if p.a < a and a - p.a >= edl.MIN_PIECE:
            out.append(edl.Piece(p.a, round(a, 3), p.speed, p.zoom, 0.0, p.fx, p.role, p.tag))
        if p.b > b and p.b - b >= edl.MIN_PIECE:
            out.append(edl.Piece(round(b, 3), p.b, p.speed, p.zoom, p.push, "", p.role, p.tag))
    return out


def _gap_bounds(segs: list, keep: list[bool], a: float, b: float) -> tuple[float, float]:
    """Mở [a, b] ra tới giữa khoảng lặng với câu được giữ hai bên (không cắt vào câu khác)."""
    before = [s[1] for s, k in zip(segs, keep) if k and s[1] <= a + 0.01]
    after = [s[0] for s, k in zip(segs, keep) if k and s[0] >= b - 0.01]
    lo = (max(before) + a) / 2 if before else max(0.0, a - 0.3)
    hi = (min(after) + b) / 2 if after else b + 0.3
    return round(max(lo, a - 0.4), 3), round(min(hi, b + 0.4), 3)


def montage_times(items: list, end: float, length: float) -> list[tuple]:
    """[(nguyên liệu, giây bắt đầu khúc)]: lúc thấy nguyên liệu (ảnh) hoặc lúc nói định lượng; trùng / sát nhau (AI hay
    chỉ cùng một ảnh cho cả loạt gia vị) thì lấy lúc nói, rồi lùi dần cho khỏi chiếu một cảnh nhiều lần."""
    used: list = []
    out = []
    for it in items:
        cands = [t for t in (it.seen_at, it.said_at) if t >= 0]
        t = next((c for c in cands if all(abs(c - u) >= length for u in used)), cands[0] if cands else -1.0)
        while any(abs(t - u) < length for u in used) and t + length < end:
            t += length
        t = round(max(0.0, min(t, end - length)), 3)
        used.append(t)
        out.append((it, t))
    return out


_TAIL_STOP = {"thành", "và", "với", "trong", "khoảng", "tầm", "chừng", "cho", "để", "rồi", "là", "của", "vào", "ra",
              "lên", "thêm", "nửa", "một", "các", "những", "đã", "sẽ", "đến", "tới", "từ"}


def _short_step(text: str, kind: str) -> str:
    """Một bước cho lời tóm tắt: vế đầu, tối đa 6 chữ ("Chiên lửa nhỏ khoảng 1 phút rưỡi")."""
    head = (text or kind).split(",")[0].split(";")[0].strip().rstrip(".")
    words = head.split()[:6]
    while len(words) > 2 and (words[-1].lower() in _TAIL_STOP or any(ch.isdigit() for ch in words[-1])):
        words.pop()                               # không để cụt ở "thành", "và", "khoảng 15"
    return " ".join(words).lower()


def _label(it: recipe.Item) -> str:
    q = it.quantity()
    if q is not None and it.verified:
        return f"{recipe.fmt(q)} {it.name}"
    return it.name[:1].upper() + it.name[1:]


def plan_strong(end: float, segs: list, cuts: list, mot: list, stretch: float, label_mode: str,
                facts: recipe.Facts | None) -> tuple[edl.EditPlan, str]:
    """(kế hoạch, ghi chú). Không đủ dữ liệu / vi phạm luật: kế hoạch lớp A + lý do."""
    segs = [(a, min(b, end), t) for a, b, t in segs if a < end]          # câu cuối có thể dài quá hết video
    light = style.plan_light(end, segs, cuts, mot, stretch, label_mode, "light")
    if facts is None or not facts.is_recipe or not facts.steps:
        return light, "không có nhãn bước của AI: chỉ áp lớp A"
    steps = [s for s in facts.steps if s.t0 >= 0]
    chat = chatter(segs, steps)
    keep = [not c for c in chat]
    dropped = {(a, b) for (a, b, _), c in zip(segs, chat) if c}
    plan = style.plan_light(end, segs, cuts, mot, stretch, label_mode, "strong", drop=lambda a, b: (a, b) in dropped)
    pieces = list(plan.pieces)
    did = []
    roll_t = [it.seen_at if it.seen_at >= 0 else it.said_at for it in facts.items
              if it.seen_at >= 0 or it.said_at >= 0]
    # B3: phần chào hỏi ở đầu (không bỏ nếu nguyên liệu được bày ra đúng lúc đó: điểm danh chỉ được LẶP, không thay)
    intro = [s for s in steps if s.kind == "intro" and s.t0 < 2.0]
    if intro and not any(t <= intro[0].t1 + 0.5 for t in roll_t):
        s = intro[0]
        in_segs = [k for k, (a, b, _) in enumerate(segs) if s.t0 - 0.05 <= a and b <= s.t1 + 0.05]
        if in_segs and all(chat[k] for k in in_segs) and s.t1 - s.t0 >= 1.5:
            lo, hi = _gap_bounds(segs, keep, 0.0, s.t1)
            pieces = _cut_out(pieces, 0.0, hi)
            did.append(f"bỏ {hi:.0f}s chào hỏi đầu video")
    # B6: đổi chỗ hai bước sơ chế độc lập (tối đa 1)
    first_cook = min((s.t0 for s in steps if s.kind == "cook"), default=end)
    moved: tuple | None = None
    preps = [s for s in steps if s.kind == "prep" and s.t1 <= first_cook]
    for x, y in zip(preps, preps[1:]):
        shared = {ingredients.GROUPS[g][0] for n in x.ingredients for g in ingredients.find(n)} & \
                 {ingredients.GROUPS[g][0] for n in y.ingredients for g in ingredients.find(n)}
        if y.independent and not shared and 2.0 <= x.t1 - x.t0 <= 12.0 and 2.0 <= y.t1 - y.t0 <= 12.0 \
                and steps.index(y) == steps.index(x) + 1:
            moved = (x, y)
            break
    if moved:
        x, y = moved
        lo, hi = _gap_bounds(segs, keep, x.t0, x.t1)
        y_end = _gap_bounds(segs, keep, y.t0, y.t1)[1]
        pieces = _cut_out(pieces, lo, hi)
        ins = next((k + 1 for k in range(len(pieces) - 1, -1, -1)
                    if pieces[k].role == "main" and pieces[k].b <= y_end + 0.01), len(pieces))
        pieces.insert(ins, edl.Piece(lo, hi, zoom=1.12, role="side", tag="moved"))
        did.append("đổi chỗ hai bước sơ chế độc lập")
    # B2: điểm danh nguyên liệu (sau câu hook / đoạn mở đầu)
    items = [it for it in facts.items if (it.seen_at >= 0 or it.said_at >= 0)]
    items.sort(key=lambda it: (not (it.verified and it.qty is not None), it.seen_at if it.seen_at >= 0 else it.said_at))
    seen: set = set()
    roll = []
    for it in items:
        k = recipe._key(it.name)
        if k in seen:
            continue
        seen.add(k)
        roll.append(it)
        if len(roll) >= MONTAGE_MAX:
            break
    roll.sort(key=lambda it: it.seen_at if it.seen_at >= 0 else it.said_at)
    montage = [(edl.Piece(a, round(a + MONTAGE_LEN, 3), zoom=1.12, role="side", tag="montage"), _label(it))
               for it, a in montage_times(roll, end, MONTAGE_LEN)]
    if len(montage) >= 3:
        pieces = [p for p, _ in montage] + pieces
        did.append(f"điểm danh {len(montage)} nguyên liệu")
    else:
        montage = []
    # B4 + B5: cảm thán giữa video dời về cuối, tóm tắt các bước chính, trước cảnh cuối
    tail: list = []
    tail_text = []
    react = [s for s in steps if s.kind == "reaction" and s.t1 < end * 0.8 and 1.5 <= s.t1 - s.t0 <= 5.0
             and all(chat[k] for k, (a, b, _) in enumerate(segs) if s.t0 - 0.05 <= a and b <= s.t1 + 0.05)]
    if react:
        r = max(react, key=lambda s: s.t1 - s.t0)
        lo, hi = _gap_bounds(segs, keep, r.t0, r.t1)
        pieces = _cut_out(pieces, lo, hi)
        tail.append(edl.Piece(lo, hi, role="side", tag="moved"))
        said = " ".join(t for a, b, t in segs if lo <= a and b <= hi).strip()
        tail_text.append(said if said[-1:] in ".!?…" else said + ".")
        did.append("dời đoạn cảm thán về cuối")
    core = [s for s in steps if s.kind in ("cook", "plate", "prep") and s.t1 - s.t0 >= 1.0]
    if len(core) >= 3:
        pick = core[:: max(1, len(core) // RECAP_MAX)][:RECAP_MAX]
        for s in pick:
            m = (s.t0 + s.t1) / 2
            tail.append(edl.Piece(round(max(0.0, m - RECAP_LEN / 2), 3), round(min(end, m + RECAP_LEN / 2), 3),
                                  zoom=1.18, role="side", tag="recap"))
        tail_text.append("Tóm lại: " + ", ".join(_short_step(s.text, s.kind) for s in pick) + ".")
        did.append(f"tóm tắt {len(pick)} bước trước khi kết")
    if tail:
        last = max((k for k, p in enumerate(pieces) if p.role == "main"), default=len(pieces) - 1)
        ins = last if pieces[last].push > 0 else last + 1     # trước cảnh món cuối (đang đẩy cận) nếu có
        pieces[ins:ins] = tail
    out = edl.EditPlan(pieces=pieces, stretch=stretch, level="strong")
    # lời "gốc" cho khúc side (để AI viết lời mới cho đúng hình) + thẻ nguyên liệu
    starts = out.starts()
    if montage:
        names = [lab for _, lab in montage]
        t0 = starts[0]
        out.texts.append({"t": round(t0, 2), "dur": round(sum(p.length for p, _ in montage) * stretch, 2),
                          "text": "Nguyên liệu gồm: " + ", ".join(n[:1].lower() + n[1:] for n in names) + "."})
        out.overlays = [edl.Overlay(round(s + 0.1, 2), round(p.length * stretch - 0.2, 2), "ingredient", lab)
                        for (p, lab), s in zip(montage, starts[: len(montage)])]
    if moved:
        mk = next(i for i, q in enumerate(pieces) if q.tag == "moved" and not any(q is x for x in tail))
        mp = pieces[mk]
        out.texts.append({"t": round(starts[mk], 2), "dur": round(mp.length * stretch, 2),
                          "text": " ".join(t for a, b, t in segs if mp.a <= a and b <= mp.b)})
    if tail:
        k0 = pieces.index(tail[0])
        out.texts.append({"t": round(starts[k0], 2), "dur": round(sum(p.length for p in tail) * stretch, 2),
                          "text": " ".join(x for x in tail_text if x)})
        rk = [k for k, p in enumerate(pieces) if p.tag == "recap"]
        if rk:
            out.overlays.append(edl.Overlay(round(starts[rk[0]] + 0.05, 2),
                                            round(sum(pieces[k].length for k in rk) * stretch - 0.1, 2), "tip",
                                            "TÓM TẮT CÁCH LÀM"))
    style.finalize(out, segs, label_mode, end, extra=("; " + ", ".join(did)) if did else "")
    errs = check_rules(out, segs, steps, chat)
    if errs:
        return light, f"lớp B vi phạm luật ({errs[0]}): chỉ áp lớp A"
    return out, ""


def check_rules(plan: edl.EditPlan, segs: list, steps: list, chat: list[bool]) -> list[str]:
    """Luật cứng R1-R7 (rỗng = đạt)."""
    errs = list(plan.validate())                                              # R6
    starts = plan.starts()
    mains = [(p, s) for p, s in zip(plan.pieces, starts) if p.role == "main"]

    def kept(a: float, b: float) -> bool:                                     # câu nằm trọn trong khúc main tốc độ 1
        return any(p.speed == 1.0 and p.a - 0.01 <= a and b <= p.b + 0.01 for p, _ in mains)

    def first_out(t0: float, t1: float, moved_ok: bool = False) -> float | None:
        best = None
        for p, s in zip(plan.pieces, starts):
            if (p.role == "main" or (moved_ok and p.tag == "moved")) and p.b > t0 and p.a < t1:
                o = s + (max(t0, p.a) - p.a) / p.speed * plan.stretch
                best = o if best is None else min(best, o)
        return best
    for st in steps:                                                          # R1
        if st.kind not in CORE_KINDS:
            continue
        cov = sum(max(0.0, min(p.b, st.t1) - max(p.a, st.t0)) for p in plan.pieces
                  if p.role == "main" or p.tag == "moved")
        if st.t1 - st.t0 > 0.5 and cov < 0.6 * (st.t1 - st.t0):
            errs.append(f"R1 bước {st.kind} {st.t0:.0f}-{st.t1:.0f}s chỉ còn {cov:.1f}s")
        for (a, b, _), c in zip(segs, chat):
            if not c and st.t0 - 0.05 <= a and b <= st.t1 + 0.05 and not kept(a, b) and \
                    not any(p.tag == "moved" and p.a - 0.01 <= a and b <= p.b + 0.01 for p in plan.pieces):
                errs.append(f"R1 mất câu ở giây {a:.1f}")
    order = [(st.t0, first_out(st.t0, st.t1, moved_ok=True)) for st in steps if st.kind in ("cook", "plate")]   # R2
    outs = [o for _, o in sorted(order) if o is not None]
    if any(y < x - 1e-6 for x, y in zip(outs, outs[1:])):
        errs.append("R2 các bước nấu không đúng thứ tự")
    for p in plan.pieces:                                                     # chỉ sơ chế / cảm thán được dời chỗ
        if p.tag == "moved" and any(st.kind in ("cook", "plate") and p.a < st.t1 - 0.05 and p.b > st.t0 + 0.05
                                    for st in steps):
            errs.append(f"R2 bước nấu ở giây {p.a:.1f} bị dời chỗ")
    cooks = [st for st in steps if st.kind == "cook"]                          # R3
    if cooks:
        c0 = first_out(cooks[0].t0, cooks[0].t1)
        for st in steps:
            if st.kind == "prep" and st.t1 <= cooks[0].t0 + 0.05:
                o = first_out(st.t0, st.t1, moved_ok=True)
                if c0 is not None and o is not None and o > c0 + 1e-6:
                    errs.append("R3 bước sơ chế đứng sau bước nấu")
    info = [(a, b) for a, b, t in segs if _informative(t)]                    # R4
    pos = []
    for a, b in info:
        o = first_out(a, b, moved_ok=True)
        if o is None:
            errs.append(f"R4 mất câu có nguyên liệu / số ở giây {a:.1f}")
        else:
            pos.append(o)
    moved_ranges = [(p.a, p.b) for p in plan.pieces if p.tag == "moved"]
    fixed = [o for (a, b), o in zip(info, pos) if not any(x <= a and b <= y for x, y in moved_ranges)]
    if any(y < x - 1e-6 for x, y in zip(fixed, fixed[1:])):
        errs.append("R4 câu có số liệu bị đảo thứ tự")
    for p in plan.pieces:                                                     # R5
        if p.tag in ("montage", "recap"):
            m = (p.a + p.b) / 2
            if not any((q.role == "main" or q.tag == "moved") and q.a - 0.6 <= m <= q.b + 0.6 for q in plan.pieces):
                errs.append(f"R5 khúc {p.tag} ở giây {p.a:.1f} không còn trong video chính")
    for p in plan.pieces:                                                     # R7
        if p.tag == "skip":
            for (a, b, _), c in zip(segs, chat):
                if not c and a < p.b - 0.01 and b > p.a + 0.01:
                    errs.append(f"R7 khúc tua nhanh có lời ở giây {a:.1f}")
    return errs
