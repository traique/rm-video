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
HOOK_KEEP = 2.0          # câu đầu tiên dài ít nhất chừng này giây luôn giữ làm câu mở đầu (thường là món thành phẩm)
HOOK_MAX = 6.0
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


def _hook_end(segs: list, intro_end: float) -> float:
    """Hết câu mở đầu: cuối câu đầu tiên kết thúc sau HOOK_KEEP giây (không quá HOOK_MAX / hết phần chào). Bản 43
    (video mực): bỏ cả phần chào từ giây 0 làm mất cảnh đầu bếp nhấc mực thành phẩm, video mở bằng thẻ "1/2 muỗng bột
    ngọt"; giờ giữ câu đầu làm câu hook rồi mới tới điểm danh nguyên liệu."""
    import prompt_engine
    for a, b, t in segs:
        if prompt_engine._GREET_RE.search(t or ""):         # câu chào ("xin chào cả nhà"): không đáng giữ làm hook
            return 0.0
        if b >= HOOK_KEEP:
            return round(b, 3) if b <= min(HOOK_MAX, intro_end + 0.05) else 0.0
    return 0.0


def _split_at(pieces: list, t: float) -> int:
    """Chỉ số chèn khúc phụ ngay tại giây gốc t (tách khúc main chứa t làm đôi nếu cần)."""
    for k, p in enumerate(pieces):
        if p.role != "main" or not (p.a - 0.05 <= t <= p.b + 0.05):
            continue
        if t - p.a < edl.MIN_PIECE:                     # sát đầu khúc: chèn trước khúc
            return k
        if p.b - t < edl.MIN_PIECE:                     # sát cuối khúc (câu mở đầu hết 2,58s, chuyển cảnh 2,7s)
            return k + 1
        pieces[k:k + 1] = [edl.Piece(p.a, round(t, 3), p.speed, p.zoom, 0.0, p.fx, p.role, p.tag),
                           edl.Piece(round(t, 3), p.b, p.speed, p.zoom, p.push, "", p.role, p.tag)]
        return k + 1
    return 0


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


def _kept_at(pieces: list, t0: float, t1: float, length: float) -> float | None:
    """Giây bắt đầu một khúc dài `length` trong [t0, t1] mà phần gốc VẪN CÒN trong video chính, chạy tốc độ thường (khúc
    lặp chỉ được lặp lại cảnh người xem đã thấy — luật R5). Lấy giữa phần còn lại dài nhất; không có thì None.
    Bản 43 (video mực): tóm tắt lấy giữa bước "bắc nước sôi" (giây 29,7) đúng quãng lớp A đã tua -> vi phạm R5, cả lớp B
    bị bỏ (mất điểm danh nguyên liệu + tóm tắt)."""
    best: tuple | None = None
    for q in pieces:
        if not (q.role == "main" or q.tag == "moved") or q.speed > 1.01:     # khúc đổi chỗ vẫn là cảnh người xem thấy
            continue
        a, b = max(t0, q.a), min(t1, q.b)
        if b - a >= min(length, q.b - q.a) - 1e-6 and (best is None or b - a > best[1] - best[0]):
            best = (a, b)
    if best is None:
        return None
    a, b = best
    return round(max(a, min((a + b) / 2 - length / 2, b - length)), 3)


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


OPS = {"B3": "bỏ chào hỏi", "B6": "đổi chỗ bước sơ chế", "B2": "điểm danh nguyên liệu", "B4": "dời cảm thán",
       "B5": "tóm tắt", "B9": "bỏ lời tán gẫu"}
# phép nào bị nghi khi gặp lỗi của luật nào (thử bỏ lần lượt, phép rủi ro cao trước)
_SUSPECTS = (("R5 khúc recap", ("B5",)), ("R5 khúc montage", ("B2",)), ("R2 bước nấu", ("B6", "B4")),
             ("R3", ("B6",)), ("R7", ("B9",)), ("R1", ("B9", "B3", "B6", "B4")), ("R4", ("B3", "B6", "B4", "B9")),
             ("R2", ("B6", "B4", "B2", "B5")), ("", ("B6", "B4", "B3", "B5", "B2", "B9")))


def plan_strong(end: float, segs: list, cuts: list, mot: list, stretch: float, label_mode: str,
                facts: recipe.Facts | None) -> tuple[edl.EditPlan, str]:
    """(kế hoạch, ghi chú). Không đủ dữ liệu: kế hoạch lớp A + lý do. Phạm luật: bỏ ĐÚNG phép gây lỗi rồi dựng lại
    (bản 45: trước đây một phép hỏng là bỏ cả lớp B — video mực hai lần liền mất điểm danh nguyên liệu + tóm tắt);
    bỏ hết vẫn phạm thì lớp A. Ghi chú nêu phép đã bỏ và luật."""
    segs = [(a, min(b, end), t) for a, b, t in segs if a < end]          # câu cuối có thể dài quá hết video
    light = style.plan_light(end, segs, cuts, mot, stretch, label_mode, "light")
    if facts is None or not facts.is_recipe or not facts.steps:
        return light, "không có nhãn bước của AI: chỉ áp lớp A"
    steps = [s for s in facts.steps if s.t0 >= 0]
    skip: set = set()
    notes: list[str] = []
    for _ in range(len(OPS) + 1):
        out, errs = _strong(end, segs, cuts, mot, stretch, label_mode, facts, steps, frozenset(skip))
        if not errs:
            return out, "; ".join(notes)
        sus = next(ops for key, ops in _SUSPECTS if errs[0].startswith(key) and any(o not in skip for o in ops))
        op = next(o for o in sus if o not in skip)
        skip.add(op)
        notes.append(f"bỏ phép {OPS[op]} ({errs[0]})")
        if len(skip) == len(OPS):
            break
    return light, f"lớp B vi phạm luật ({errs[0]}): chỉ áp lớp A"


def _strong(end: float, segs: list, cuts: list, mot: list, stretch: float, label_mode: str, facts: recipe.Facts,
            steps: list, skip: frozenset) -> tuple[edl.EditPlan, list]:
    """Một lượt lớp B, trừ các phép trong `skip`. Trả (kế hoạch, lỗi luật)."""
    chat = chatter(segs, steps) if "B9" not in skip else [False] * len(segs)
    keep = [not c for c in chat]
    dropped = {(a, b) for (a, b, _), c in zip(segs, chat) if c}
    plan = style.plan_light(end, segs, cuts, mot, stretch, label_mode, "strong", drop=lambda a, b: (a, b) in dropped)
    pieces = list(plan.pieces)
    did = []
    roll_t = [it.seen_at if it.seen_at >= 0 else it.said_at for it in facts.items
              if it.seen_at >= 0 or it.said_at >= 0]
    # B3: phần chào hỏi ở đầu (không bỏ nếu nguyên liệu được bày ra đúng lúc đó: điểm danh chỉ được LẶP, không thay)
    intro = [s for s in steps if s.kind == "intro" and s.t0 < 2.0]
    hook_end = 0.0
    if intro and "B3" not in skip and not any(t <= intro[0].t1 + 0.5 for t in roll_t):
        s = intro[0]
        in_segs = [k for k, (a, b, _) in enumerate(segs) if s.t0 - 0.05 <= a and b <= s.t1 + 0.05]
        hook_end = _hook_end(segs, s.t1)                   # câu đầu giữ làm câu mở đầu, chỉ bỏ phần chào sau nó
        if in_segs and all(chat[k] for k in in_segs) and s.t1 - max(hook_end, s.t0) >= 1.5:
            lo, hi = _gap_bounds(segs, keep, hook_end, s.t1)
            pieces = _cut_out(pieces, max(hook_end, lo) if hook_end else 0.0, hi)
            did.append(f"bỏ {hi - hook_end:.0f}s chào hỏi đầu video")
    elif intro or (segs and segs[0][0] < 1.0):
        hook_end = _hook_end(segs, intro[0].t1 if intro else HOOK_MAX)
    # B6: đổi chỗ hai bước sơ chế độc lập (tối đa 1)
    first_cook = min((s.t0 for s in steps if s.kind == "cook"), default=end)
    moved: tuple | None = None
    preps = [s for s in steps if s.kind == "prep" and s.t1 <= first_cook]
    for x, y in (zip(preps, preps[1:]) if "B6" not in skip else []):
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
    montage: list[tuple] = []
    for it, a in montage_times(roll, end, MONTAGE_LEN):
        ka = _kept_at(pieces, a - 1.5, a + MONTAGE_LEN + 1.5, MONTAGE_LEN)     # cảnh đã bị tua / cắt: lấy sát bên
        if ka is not None and all(abs(ka - p.a) >= MONTAGE_LEN for p, _ in montage):
            montage.append((edl.Piece(ka, round(ka + MONTAGE_LEN, 3), zoom=1.12, role="side", tag="montage"),
                            _label(it)))
    if len(montage) >= 3 and "B2" not in skip:
        at = _split_at(pieces, hook_end) if hook_end else 0       # sau câu mở đầu, không thay nó
        pieces[at:at] = [p for p, _ in montage]
        did.append(f"điểm danh {len(montage)} nguyên liệu")
    else:
        montage = []
    # B4 + B5: cảm thán giữa video dời về cuối, tóm tắt các bước chính, trước cảnh cuối
    tail: list = []
    tail_text = []
    react = [s for s in steps if s.kind == "reaction" and s.t1 < end * 0.8 and 1.5 <= s.t1 - s.t0 <= 5.0
             and all(chat[k] for k, (a, b, _) in enumerate(segs) if s.t0 - 0.05 <= a and b <= s.t1 + 0.05)]
    if react and "B4" not in skip:
        r = max(react, key=lambda s: s.t1 - s.t0)
        lo, hi = _gap_bounds(segs, keep, r.t0, r.t1)
        pieces = _cut_out(pieces, lo, hi)
        tail.append(edl.Piece(lo, hi, role="side", tag="moved"))
        said = " ".join(t for a, b, t in segs if lo <= a and b <= hi).strip()
        tail_text.append(said if said[-1:] in ".!?…" else said + ".")
        did.append("dời đoạn cảm thán về cuối")
    core = [s for s in steps if s.kind in ("cook", "plate", "prep") and s.t1 - s.t0 >= 1.0
            and _kept_at(pieces, s.t0, s.t1, RECAP_LEN) is not None]
    if len(core) >= 3 and "B5" not in skip:
        pick = core[:: max(1, len(core) // RECAP_MAX)][:RECAP_MAX]
        for s in pick:
            a = _kept_at(pieces, s.t0, s.t1, RECAP_LEN) or 0.0
            tail.append(edl.Piece(a, round(min(end, a + RECAP_LEN), 3), zoom=1.18, role="side", tag="recap"))
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
        k_m = next(k for k, q in enumerate(pieces) if q is montage[0][0])
        t0 = starts[k_m]
        out.texts.append({"t": round(t0, 2), "dur": round(sum(p.length for p, _ in montage) * stretch, 2),
                          "text": "Nguyên liệu gồm: " + ", ".join(n[:1].lower() + n[1:] for n in names) + "."})
        out.overlays = [edl.Overlay(round(s + 0.1, 2), round(p.length * stretch - 0.2, 2), "ingredient", lab)
                        for (p, lab), s in zip(montage, starts[k_m: k_m + len(montage)])]
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
    return out, check_rules(out, segs, steps, chat)


def check_rules(plan: edl.EditPlan, segs: list, steps: list, chat: list[bool]) -> list[str]:
    """Luật cứng R1-R7 (rỗng = đạt)."""
    errs = list(plan.validate())                                              # R6
    starts = plan.starts()

    # dãy khúc main tốc độ 1 nối liền nhau (liền cả trên video gốc lẫn video ra): câu vắt qua điểm chuyển cảnh nằm ở hai
    # khúc liền nhau vẫn là câu còn nguyên (bản 45: lời Kaggle gộp câu dài 8,6-14,3s qua chuyển cảnh 8,67s -> báo
    # nhầm "R1 mất câu", bỏ cả lớp B hai lần liền)
    runs: list = []
    for p in plan.pieces:
        if p.role == "main" and p.speed == 1.0 and runs and runs[-1] is not None and abs(runs[-1][1] - p.a) < 0.02:
            runs[-1][1] = p.b
        else:
            runs.append([p.a, p.b] if p.role == "main" and p.speed == 1.0 else None)

    def kept(a: float, b: float) -> bool:                                     # câu nằm trọn trong một dãy như vậy
        return any(r is not None and r[0] - 0.01 <= a and b <= r[1] + 0.01 for r in runs)

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
