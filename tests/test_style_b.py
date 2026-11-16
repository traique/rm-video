"""Bản 42 (S4): lớp B — đảo thứ tự có kiểm soát; luật cứng R1-R7 do mã kiểm, vi phạm thì lùi về lớp A."""
import asyncio
import subprocess

import pytest

import config
from rmvideo.app.flow import style_b
from rmvideo.domain import recipe as r
from rmvideo.media import edl, ff, renderer

SEGS = [(0.3, 2.5, "xin chào cả nhà hôm nay mình làm món"), (3.0, 6.0, "đây là 1 ký cánh gà rửa sạch"),
        (6.4, 9.5, "băm 5 tép tỏi"), (10.0, 13.0, "thái 2 củ hành tím"), (13.5, 16.0, "ướp gà với 2 muỗng nước mắm"),
        (16.5, 19.0, "giờ bắc chảo chiên gà"), (19.5, 22.0, "ôi thơm quá trời luôn"), (30.0, 33.0, "lật mặt gà"),
        (33.5, 37.0, "rưới nước mắm tỏi lên"), (38.0, 41.0, "bày ra dĩa là xong")]
MOT = [0.002 if 45 <= i < 59 else 0.03 for i in range(100)]


def facts(**kw):
    steps = kw.pop("steps", None) or [
        r.Step("intro", 0, 0, t0=0.3, t1=2.5), r.Step("ingredients", 1, 1, "Rửa sạch cánh gà", t0=3.0, t1=6.0),
        r.Step("prep", 2, 2, "Băm tỏi", ["tỏi"], t0=6.4, t1=9.5),
        r.Step("prep", 3, 3, "Thái hành tím", ["hành tím"], True, t0=10.0, t1=13.0),
        r.Step("prep", 4, 4, "Ướp gà với nước mắm", ["cánh gà", "nước mắm"], t0=13.5, t1=16.0),
        r.Step("cook", 5, 5, "Chiên gà", ["cánh gà"], t0=16.5, t1=19.0), r.Step("reaction", 6, 6, "", t0=19.5, t1=22.0),
        r.Step("cook", 7, 8, "Lật mặt, rưới nước mắm tỏi", t0=30.0, t1=37.0),
        r.Step("plate", 9, 9, "Bày ra dĩa", t0=38.0, t1=41.0)]
    return r.Facts(is_recipe=True, dish="Cánh gà", steps=steps, items=kw.pop("items", None) or [
        r.Item("cánh gà", 1.0, "kg", "ok", seen_at=3.5), r.Item("tỏi", 5.0, "tép", "ok", seen_at=7.0),
        r.Item("hành tím", 2.0, "củ", "ok", said_at=10.0), r.Item("nước mắm", 2.0, "muỗng", "unverified", said_at=13.5)])


def test_strong_plan_applies_all_ops_and_obeys_rules():
    f = facts()
    plan, why = style_b.plan_strong(45, SEGS, [6.2, 16.2, 30.0], MOT, 1.0, "auto", f)
    assert why == "" and plan.level == "strong"
    assert not style_b.check_rules(plan, SEGS, f.steps, style_b.chatter(SEGS, f.steps))
    tags = [p.tag for p in plan.pieces]
    assert tags[:4] == ["montage"] * 4 and "recap" in tags and tags.count("moved") == 2
    labels = [o.text for o in plan.overlays if o.kind == "ingredient"]
    assert labels == ["1 kg cánh gà", "5 tép tỏi", "2 củ hành tím", "Nước mắm"]      # số chưa đối chiếu: không in
    assert plan.texts[0]["text"].startswith("Nguyên liệu gồm: 1 kg cánh gà")
    # hai bước sơ chế độc lập (tỏi, hành) đổi chỗ; các bước nấu giữ thứ tự; cảnh cuối (đẩy cận) vẫn cuối
    mains = [p for p in plan.pieces if p.role == "main"]
    assert all(x.b <= y.a + 1e-6 for x, y in zip(mains, mains[1:])) and plan.pieces[-1].push > 0
    assert not any(p.role == "main" and p.a < 2.7 for p in plan.pieces)                  # bỏ chào hỏi
    assert len(plan.side_runs()) <= edl.MAX_SIDE_RUNS


def test_no_swap_when_steps_share_ingredient_or_not_independent():
    f = facts()
    f.steps[3] = r.Step("prep", 3, 3, "Thái hành tím với tỏi", ["hành tím", "tỏi"], True, t0=10.0, t1=13.0)
    plan, why = style_b.plan_strong(45, SEGS, [], MOT, 1.0, "auto", f)
    assert why == "" and not any(p.tag == "moved" and p.a < 10 for p in plan.pieces)


def test_rules_catch_violations():
    f = facts()
    chat = style_b.chatter(SEGS, f.steps)
    # đảo hai bước nấu: R2
    bad = edl.EditPlan([edl.Piece(0, 16.2), edl.Piece(29.7, 45), edl.Piece(16.2, 29.7, role="side", tag="moved")])
    assert any(e.startswith("R2") for e in style_b.check_rules(bad, SEGS, f.steps, chat))
    # mất câu có số liệu: R1 / R4
    gone = edl.EditPlan([edl.Piece(0, 6.2), edl.Piece(9.75, 45)])
    errs = style_b.check_rules(gone, SEGS, f.steps, chat)
    assert any(e.startswith("R4") for e in errs) and any(e.startswith("R1") for e in errs)
    # tua nhanh đè lên câu: R7
    fast = edl.EditPlan([edl.Piece(0, 30), edl.Piece(30, 37, speed=2.0, tag="skip"), edl.Piece(37, 45)])
    assert any(e.startswith("R7") for e in style_b.check_rules(fast, SEGS, f.steps, chat))
    # khúc tóm tắt không còn trong video chính: R5
    orphan = edl.EditPlan([edl.Piece(5, 6, role="side", tag="recap"), edl.Piece(10, 45)])
    assert any(e.startswith("R5") for e in style_b.check_rules(orphan, SEGS, f.steps, chat))


def test_falls_back_to_light_without_facts_or_on_violation(monkeypatch):
    plan, why = style_b.plan_strong(45, SEGS, [], MOT, 1.0, "auto", None)
    assert plan.level == "light" and "chỉ áp lớp A" in why
    monkeypatch.setattr(style_b, "check_rules", lambda *a: ["R2 thử"])
    plan, why = style_b.plan_strong(45, SEGS, [], MOT, 1.0, "auto", facts())
    assert plan.level == "light" and "R2 thử" in why


def test_render_strong_plan_for_real(tmp_path, monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    src, voice, amb, out = (str(tmp_path / n) for n in ("s.mp4", "v.wav", "a.wav", "o.mp4"))
    for args in (["-f", "lavfi", "-i", "testsrc2=s=360x640:d=45:r=25", "-f", "lavfi", "-i", "sine=d=45", "-shortest", src],
                 ["-f", "lavfi", "-i", "sine=f=500:d=30", voice], ["-f", "lavfi", "-i", "anoisesrc=d=45:a=0.05", amb]):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=120)
    plan, why = style_b.plan_strong(45, SEGS, [6.2, 16.2, 30.0], MOT, 1.0, "auto", facts())
    assert why == ""
    got = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, amb, None, allow_hd=False,
                                      tmp=str(tmp_path), edit=plan.to_dict()))
    assert got == pytest.approx(plan.out_dur(), abs=0.05)
    assert asyncio.run(ff.probe_duration(out)) == pytest.approx(plan.out_dur(), abs=0.2)
