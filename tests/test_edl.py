"""Bản 42 (S3): kế hoạch dựng (rmvideo/media/edl.py) + lớp A (rmvideo/app/flow/style.py): đổi mốc thời gian đúng,
không cắt giữa câu, filter FFmpeg chạy thật (khúc chính, tua nhanh, đẩy cận, chớp sáng, clip phụ, thẻ chữ, đoạn mở đầu,
tiếng nền / tiếng gốc theo khúc)."""
import asyncio
import os
import random
import subprocess

import pytest

import config
from rmvideo.app.flow import style
from rmvideo.media import edl, ff, renderer

P = edl.Piece


def _plan():
    return edl.EditPlan([P(0.0, 3.0, zoom=1.0), P(3.0, 6.0, zoom=1.12), P(8.0, 14.0, speed=3.0, tag="skip"),
                         P(14.0, 18.0, zoom=1.18, fx="flash", push=0.06)], stretch=1.0)


def test_time_mapping_roundtrip():
    p = _plan()
    assert p.out_dur() == pytest.approx(3 + 3 + 2 + 4)
    assert p.to_out(1.5) == pytest.approx(1.5) and p.to_out(7.0) == pytest.approx(6.0)     # quãng bỏ -> khúc sau
    assert p.to_out(11.0) == pytest.approx(7.0) and p.to_out(16.0) == pytest.approx(10.0)
    for x in (0.5, 4.0, 6.5, 9.0, 11.5):
        assert p.to_out(p.src_time(x)) == pytest.approx(x, abs=1e-6)
    assert p.map_segments([(0.5, 2.5, "a"), (6.2, 7.5, "bỏ"), (15.0, 17.0, "c")]) == [(0.5, 2.5, "a"), (9.0, 11.0, "c")]
    assert p.map_windows([(0.5, 2.0), (2.0, 4.0), (15, 16)]) == [(0.5, 4.0), (9.0, 10.0)]
    assert p.cut_points() == [3.0, 6.0, 8.0]
    assert edl.EditPlan.from_dict(p.to_dict()).to_dict() == p.to_dict() and not p.validate()
    bad = edl.EditPlan([P(5, 8), P(2, 4)])
    assert bad.validate() and edl.EditPlan([P(0, 0.2)]).validate()


def test_stretch_and_atempo_and_bands():
    p = edl.EditPlan([P(0, 4), P(4, 10, speed=3)], stretch=1.25)
    assert p.out_dur() == pytest.approx((4 + 2) * 1.25)
    assert edl._atempo(3.0) == "atempo=2.0,atempo=1.5000," and edl._atempo(1.0) == ""
    assert edl._atempo(0.3) == "atempo=0.5,atempo=0.6000,"
    (t, b), = edl.zoomed_bands([(0.70, 0.80)], {1.12, 1.18})
    assert t == pytest.approx(0.70) and b == pytest.approx(0.5 + 0.3 * 1.18)


def test_plan_light_never_cuts_speech_property():
    """Lời gốc sinh ngẫu nhiên: mọi câu đều còn đủ và đúng thứ tự, mép khúc không rơi vào giữa câu."""
    rnd = random.Random(7)
    for _ in range(60):
        t, segs = 0.0, []
        while t < 120:
            t += rnd.choice([0.2, 0.5, 1.5, 3.0, 7.0, 12.0])
            d = rnd.uniform(1.0, 6.0)
            segs.append((round(t, 2), round(t + d, 2), "x"))
            t += d
        end = segs[-1][1] + rnd.uniform(0, 8)
        mot = [rnd.choice([0.001, 0.01, 0.03]) for _ in range(int(end * 2) + 2)]
        cuts = sorted(rnd.uniform(0, end) for _ in range(rnd.randint(0, 15)))
        plan = style.plan_light(end, segs, cuts, mot if rnd.random() < 0.8 else [])
        assert not plan.validate()
        mapped = plan.map_segments(segs)
        assert len(mapped) == len(segs)
        assert all(abs((b - a) - (sb - sa)) < 0.02 for (a, b, _), (sa, sb, _) in zip(mapped, segs))   # không tua lời
        for x, y in zip(plan.pieces, plan.pieces[1:]):     # chỗ bỏ / đổi tốc độ không bao giờ rơi giữa câu
            if y.a - x.b > 0.01 or x.speed != 1 or y.speed != 1:
                for a, b, _ in segs:
                    assert not (a + 0.01 < x.b < b - 0.01) and not (a + 0.01 < y.a < b - 0.01)
        assert plan.out_dur() <= end + 0.002


def test_skip_label_from_speech():
    segs = [(0, 3, "đậy nắp hầm 30 phút cho mềm"), (20, 22, "mở nắp ra")]
    assert style.skip_label(segs, 3.5, "auto") == "»» 30 PHÚT SAU"
    assert style.skip_label(segs, 3.5, "icon") == "»»" and style.skip_label(segs, 3.5, "off") == ""
    assert style.skip_label(segs, 15.0, "auto") == "»»"                       # câu nói thời gian đã quá xa


def _ffmpeg(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, capture_output=True, timeout=180)


@pytest.fixture
def media(tmp_path, monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    src, voice, amb = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav"), str(tmp_path / "amb.wav")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:d=20:r=25", "-f", "lavfi", "-i", "sine=f=300:d=20", "-shortest", src)
    _ffmpeg("-f", "lavfi", "-i", "sine=f=500:d=10", voice)
    _ffmpeg("-f", "lavfi", "-i", "anoisesrc=d=20:a=0.05", "-ar", "44100", amb)
    return tmp_path, src, voice, amb


def _dur(p):
    return asyncio.run(ff.probe_duration(p))


def test_render_plan_cook_with_ambient_ost_teaser_side_and_overlays(media):
    tmp, src, voice, amb = media
    plan = _plan()
    plan.pieces.insert(1, P(15.0, 16.0, zoom=1.12, role="side", tag="montage"))
    plan.pieces.insert(2, P(9.0, 10.0, role="side", tag="montage"))
    plan.overlays = [edl.Overlay(0.5, 1.5, "ingredient", "300 g thịt ba chỉ"), edl.Overlay(8.1, 1.4, "skip", "»» 30 PHÚT SAU")]
    assert not plan.validate() and plan.side_runs() == [[1, 2]]
    out = str(tmp / "o.mp4")
    T = 2.0
    got = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, [(0.70, 0.80)], amb, None, allow_hd=False,
                                      title="THỬ", tmp=str(tmp), ost=[(1.0, 2.0, 1.0)], edit=plan.to_dict(),
                                      teaser={"a": 16.0, "dur": T, "still": False}))
    assert got == pytest.approx(plan.out_dur() + T, abs=0.05)
    assert _dur(out) == pytest.approx(plan.out_dur() + T, abs=0.15)


def test_render_plan_gate_and_review_mode(media):
    tmp, src, voice, _ = media
    plan = _plan()
    out = str(tmp / "g.mp4")
    asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, [(0.5, 2.0), (9.0, 10.0)],
                                allow_hd=False, tmp=str(tmp), edit=plan.to_dict()))
    assert _dur(out) == pytest.approx(plan.out_dur(), abs=0.15)
    out2 = str(tmp / "r.mp4")
    asyncio.run(renderer.render(src, voice, out2, "review", 0, None, None, None, None, allow_hd=False, tmp=str(tmp),
                                ost=[(0.5, 2.0, 0.35)], edit=plan.to_dict()))
    assert _dur(out2) == pytest.approx(plan.out_dur(), abs=0.15)


def test_side_clip_without_ambient(media):
    tmp, src, _, _ = media
    out = str(tmp / "side.mkv")
    has_a = asyncio.run(renderer.side_clip(src, [P(2, 3, zoom=1.12), P(10, 11.5, push=0.06)], out, "cook", 720, 1280,
                                           30, 360, 640, None))
    assert not has_a and os.path.exists(out) and _dur(out) == pytest.approx(2.5, abs=0.1)
