"""Bản ngắn ≤ 90 giây cho Reels (bản 42, SHORT_VERSION=on): từ CÙNG video nguồn, sau bản đầy đủ.

Video nguồn 2-5 phút, đầu bếp nói liên tục: không thể giữ mọi câu. Bản ngắn giữ:
  - mỗi bước sơ chế / nấu / bày (nhãn của AI đọc công thức) ÍT NHẤT một câu, câu có nguyên liệu / con số được ưu tiên,
    đúng thứ tự gốc (khúc main theo thứ tự -> luật R2, R3 tự đạt);
  - điểm danh nguyên liệu đầu video (thẻ số đã đối chiếu) nếu đủ chỗ;
  - cảnh cuối (món thành phẩm) đẩy cận;
  - còn chỗ thì thêm câu điểm cao (có số liệu, thuộc bước chính); câu tán gẫu không bao giờ lấy.
Định lượng đầy đủ nằm ở thẻ + bài đăng (công thức đã đối chiếu), lời đọc là lời AI viết mới cho các câu được giữ.
Không đủ dữ liệu (chưa có nhãn bước) hoặc không nhét vừa: không làm bản ngắn, ghi lý do."""
from __future__ import annotations

import ingredients
from rmvideo.app.flow import style, style_b
from rmvideo.domain import recipe
from rmvideo.media import edl

REEL_MAX = 90.0
MARGIN = 1.5            # chừa làm tròn (màn kết nối thêm chừa riêng: brand.tail_len)
PAD = 0.15              # chừa quanh câu
MERGE_GAP = 0.6         # hai câu giữ cách nhau ít hơn: lấy luôn khoảng giữa (khỏi giật hình)
HERO = 2.5              # cảnh cuối
MONTAGE_LEN = 1.0
MIN_SOURCE = 100.0      # nguồn ngắn hơn: bản đầy đủ đã vừa Reels


def _score(text: str, chat: bool, core: bool) -> int:
    if chat:
        return 0
    return 1 + (2 if (ingredients.find(text) or recipe.parse(text)) else 0) + (1 if core else 0)


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + MERGE_GAP:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _length(spans: list) -> float:
    return sum(b - a for a, b in _merge(spans))


def plan_short(end: float, segs: list, facts: recipe.Facts | None, budget: float, stretch: float = 1.0
               ) -> tuple[edl.EditPlan | None, str]:
    """(kế hoạch, lý do không làm). segs: lời gốc trục GỐC; budget: giây ra tối đa (đã trừ đoạn mở đầu)."""
    if facts is None or not facts.is_recipe or not facts.steps:
        return None, "chưa có nhãn bước của AI đọc công thức"
    segs = [(a, min(b, end), t) for a, b, t in segs if a < end]
    steps = [s for s in facts.steps if s.t0 >= 0]
    core = [s for s in steps if s.kind in style_b.CORE_KINDS]
    if not core:
        return None, "AI không thấy bước sơ chế / nấu nào"
    chat = style_b.chatter(segs, steps)
    in_core = [bool(st and st.kind in style_b.CORE_KINDS) for st in (style_b._step_at(steps, (a + b) / 2)
                                                                      for a, b, _ in segs)]
    score = [_score(t, c, k) for (_, _, t), c, k in zip(segs, chat, in_core)]
    span = [(max(0.0, a - PAD), min(end, b + PAD)) for a, b, _ in segs]
    cap = budget / stretch
    hero_a = max(0.0, end - HERO)
    # bắt buộc: một câu (điểm cao nhất, sớm nhất) cho mỗi bước chính; bước không lời: 1,5 giây giữa bước
    must: list = []
    for st in core:
        ks = [k for k, (a, b, _) in enumerate(segs) if st.t0 - 0.05 <= a and b <= st.t1 + 0.05 and score[k] > 0]
        if ks:
            k = max(ks, key=lambda k: (score[k], -segs[k][0]))
            must.append((st, [span[k]], score[k]))
        else:
            m = (st.t0 + st.t1) / 2
            must.append((st, [(max(0.0, m - 0.75), min(end, m + 0.75))], 1))
    chosen = [sp for _, sps, _ in must for sp in sps]
    while _length(chosen + [(hero_a, end)]) > cap and len(must) > 1:
        # bỏ bớt câu của bước sơ chế điểm thấp nhất (giữ các bước nấu / bày)
        cand = [x for x in must if x[0].kind == "prep"] or must[:-1]
        drop = min(cand, key=lambda x: x[2])
        must.remove(drop)
        chosen = [sp for _, sps, _ in must for sp in sps]
    if _length(chosen + [(hero_a, end)]) > cap:
        return None, f"các bước chính đã dài hơn {budget:.0f}s"
    kept_steps = len(must)
    # điểm danh nguyên liệu (khúc lặp, thẻ số đã đối chiếu) nếu còn chỗ
    items: list = []
    seen: set = set()
    when = lambda it: it.seen_at if it.seen_at >= 0 else it.said_at          # noqa: E731
    # nguyên liệu có định lượng đã đối chiếu được ưu tiên lên thẻ, rồi xếp lại theo lúc xuất hiện
    for it in sorted(facts.items, key=lambda it: (not (it.verified and it.qty is not None), when(it))):
        key = recipe._key(it.name)
        if when(it) >= 0 and key not in seen and len(items) < 5:
            seen.add(key)
            items.append((it, when(it)))
    items = [(it, t) for it, t in style_b.montage_times([it for it, _ in sorted(items, key=lambda x: x[1])], end,
                                                         MONTAGE_LEN)]
    montage = items if len(items) >= 3 and _length(chosen + [(hero_a, end)]) + len(items) * MONTAGE_LEN <= cap else []
    room = cap - _length(chosen + [(hero_a, end)]) - len(montage) * MONTAGE_LEN
    # thêm câu điểm cao còn vừa
    for k in sorted(range(len(segs)), key=lambda k: (-score[k], segs[k][0])):
        if score[k] < 2 or span[k] in chosen:
            continue
        extra = _length(chosen + [span[k], (hero_a, end)]) - _length(chosen + [(hero_a, end)])
        if extra <= room:
            chosen.append(span[k])
            room -= extra
    spans = _merge(chosen + [(hero_a, end)])
    pieces = []
    for i, (a, b) in enumerate(spans):
        if b - a < edl.MIN_PIECE:
            continue
        last = i == len(spans) - 1
        pieces.append(edl.Piece(round(a, 3), round(b, 3), zoom=1.0 if last else style.ZOOMS[i % len(style.ZOOMS)],
                                push=style.PUSH if last else 0.0))
    side = [edl.Piece(t, round(t + MONTAGE_LEN, 3), zoom=1.12, role="side", tag="montage") for _, t in montage]
    plan = edl.EditPlan(pieces=side + pieces, stretch=stretch, level="short")
    if montage:
        labels = [style_b._label(it) for it, _ in montage]
        plan.texts.append({"t": 0.0, "dur": round(len(montage) * MONTAGE_LEN * stretch, 2),
                           "text": "Nguyên liệu gồm: " + ", ".join(n[:1].lower() + n[1:] for n in labels) + "."})
        plan.overlays = [edl.Overlay(round(k * MONTAGE_LEN * stretch + 0.1, 2), round(MONTAGE_LEN * stretch - 0.2, 2),
                                     "ingredient", lab) for k, lab in enumerate(labels)]
    errs = plan.validate()
    if errs:
        return None, errs[0]
    plan.summary = (f"Bản ngắn: giữ {kept_steps}/{len(core)} bước chính"
                    + (f", điểm danh {len(montage)} nguyên liệu" if montage else "")
                    + f"; {end * stretch:.0f}s -> {plan.out_dur():.0f}s.")
    return plan, ""


def budget(teaser_dur: float) -> float:
    return REEL_MAX - MARGIN - teaser_dur
