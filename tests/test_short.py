"""Bản 42: bản ngắn ≤ 90 giây cho Reels (rmvideo/app/flow/short.py)."""
import pytest

from rmvideo.app.flow import short
from rmvideo.domain import recipe as r


def _src(n_steps=8, per_step=4, sec=6.0):
    """Video ~ n_steps*per_step*sec giây: mỗi bước có câu tán gẫu, câu có số liệu, câu thao tác."""
    segs, steps, t = [], [], 1.0
    kinds = ["prep"] * 3 + ["cook"] * (n_steps - 4) + ["plate"]
    for k, kind in enumerate(kinds):
        t0 = t
        lines = [f"bước {k} cho 200 gam thịt vào", "ôi thơm quá luôn nha", f"đảo đều tay bước {k}", "rồi chờ chút xíu"]
        for j in range(per_step):
            segs.append((round(t, 2), round(t + sec - 1.0, 2), lines[j % 4]))
            t += sec
        steps.append(r.Step(kind, 0, 0, f"Làm bước {k}", t0=round(t0, 2), t1=round(t - 1.0, 2)))
    steps.insert(0, r.Step("intro", 0, 0, t0=0.0, t1=0.9))
    items = [r.Item(n, q, u, "ok", seen_at=3.0 + i * 5) for i, (n, q, u) in
             enumerate([("thịt ba chỉ", 500.0, "g"), ("tỏi", 5.0, "tép"), ("nước mắm", 2.0, "muỗng canh")])]
    return segs, r.Facts(is_recipe=True, dish="Thịt kho", items=items, steps=steps), t + 3.0


def test_short_plan_fits_reels_keeps_every_step_in_order():
    segs, f, end = _src()
    assert end > 150
    plan, why = short.plan_short(end, segs, f, short.budget(3.0))
    assert why == "" and plan.out_dur() <= short.budget(3.0) + 1e-6
    mains = [p for p in plan.pieces if p.role == "main"]
    assert all(x.b <= y.a for x, y in zip(mains, mains[1:]))                       # đúng thứ tự gốc
    core = [s for s in f.steps if s.kind in ("prep", "cook", "plate")]
    assert all(any(p.a < s.t1 and p.b > s.t0 for p in mains) for s in core)        # mọi bước chính đều có mặt
    kept = plan.map_segments(segs)
    assert sum("200 gam" in t for *_, t in kept) == 8                              # câu có số liệu ưu tiên giữ
    assert [o.text for o in plan.overlays] == ["500 g thịt ba chỉ", "5 tép tỏi", "2 muỗng canh nước mắm"]
    assert plan.pieces[-1].push > 0 and plan.pieces[-1].b == pytest.approx(end)    # kết bằng cảnh món, đẩy cận
    assert "Bản ngắn: giữ 8/8 bước chính" in plan.summary


def test_short_never_takes_chatter():
    segs, f, end = _src()
    f.steps[2] = r.Step("wait", 0, 0, "", t0=f.steps[2].t0, t1=f.steps[2].t1)       # bước 1 thành bước chờ
    plan, _ = short.plan_short(end, segs, f, short.budget(0.0))
    wait = f.steps[2]
    kept = [t for a, b, t in segs if wait.t0 <= a <= wait.t1 and plan.to_out(b) - plan.to_out(a) > 0.05]
    assert kept == ["bước 1 cho 200 gam thịt vào"]                                 # bước chờ: chỉ câu có số liệu


def test_short_drops_prep_first_when_too_long_and_refuses_when_impossible():
    segs, f, end = _src(n_steps=8, per_step=2, sec=15.0)                         # câu 14 giây: 8 bước không vừa
    plan, why = short.plan_short(end, segs, f, short.budget(0.0))
    assert plan is not None and plan.out_dur() <= short.budget(0.0)
    assert "bước chính" in plan.summary and "8/8" not in plan.summary
    plan, why = short.plan_short(end, segs, f, 10.0)
    assert plan is None and "dài hơn" in why
    plan, why = short.plan_short(end, segs, None, 85.0)
    assert plan is None and "nhãn bước" in why


def test_autopost_posts_short_version_when_chosen(tmp_path, monkeypatch):
    import autopost
    import config
    full, short_, cov = (tmp_path / n for n in ("full.mp4", "short.mp4", "c.jpg"))
    for f in (full, short_, cov):
        f.write_bytes(b"x")

    class J:
        parts = [{"label": "Video", "path": str(full)}, {"label": "Ảnh bìa", "path": str(cov)},
                 {"label": "Bản ngắn", "path": str(short_)}]
        output = str(full)
    monkeypatch.setattr(config, "AUTO_POST_VERSION", "short")
    assert autopost._output_files(J()) == (str(short_), str(cov))
    monkeypatch.setattr(config, "AUTO_POST_VERSION", "full")
    assert autopost._output_files(J()) == (str(full), str(cov))
