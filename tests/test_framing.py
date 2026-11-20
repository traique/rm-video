"""1.1.2: khung dọc theo nội dung (rmvideo/media/framing.py): bỏ dải đen, nguồn ngang cắt cửa sổ 9:16 theo vùng có chuyển
động hoặc đặt nguyên khung lên nền mờ; dải chữ gốc đổi sang khung ra; Kaggle dựng cùng khung."""
import asyncio
import os
import subprocess

import numpy as np
import pytest

import config
import kaggle_job
import kaggle_worker
from rmvideo.media import filtergraph, framing


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(config, "FRAMING_AUTO", "1")
    tok = framing.FRAMING.set(None)
    yield
    framing.FRAMING.reset(tok)


def _pairs(h, w, n=6, x_motion=None, bars_tb=0, rng=None, spread=False):
    """n cặp khung xám (2, h, w): nền ngẫu nhiên tĩnh, một khối «tay» dịch chuyển giữa hai khung tại cột x_motion (hoặc
    rải ngẫu nhiên mỗi cặp), dải đen bars_tb điểm trên/dưới."""
    rng = rng or np.random.default_rng(1)
    out = []
    for k in range(n):
        base = rng.integers(60, 200, (h, w)).astype(np.uint8)
        a, b = base.copy(), base.copy()
        x = x_motion if x_motion is not None else (10 + k * (w - 40) // max(1, n - 1) if spread
                                                   else int(rng.integers(10, w - 30)))
        a[h // 3:h // 3 + 30, x:x + 20] = 250
        b[h // 3 + 8:h // 3 + 38, x + 6:x + 26] = 250
        if bars_tb:
            for f in (a, b):
                f[:bars_tb] = 0
                f[h - bars_tb:] = 0
        out.append(np.stack([a, b]))
    return out


def test_bars_and_dark_scene():
    fr = np.full((4, 100, 60), 120, np.uint8)
    fr[:, :20] = 0
    fr[:, 80:] = 0
    assert framing.bars(fr) == (0, 21, 60, 79)                   # nới vào trong 1 điểm mỗi bên có dải
    dark = fr.copy()
    dark[1] = 0                                                  # một khung tối hẳn (chuyển cảnh): không đổi kết quả
    assert framing.bars(dark) == (0, 21, 60, 79)
    assert framing.bars(np.full((3, 100, 60), 120, np.uint8)) == (0, 0, 60, 100)
    assert framing.bars(np.zeros((3, 100, 60), np.uint8)) == (0, 0, 60, 100)  # đen hẳn: không tin


def test_window_and_profile():
    prof = np.zeros(100)
    prof[70:80] = 1
    assert framing.best_window(prof / prof.sum(), 30) == (50, 1.0)
    start, frac = framing.best_window(np.full(100, 0.01), 30)
    assert frac == pytest.approx(0.3)
    assert framing.best_window(np.zeros(50), 20) == (15, 0.0)


def test_decide_landscape_vertical_letterbox():
    # nguồn ngang 1280x720, chuyển động dồn bên phải -> cửa sổ 9:16 bên phải (cắt, không nền mờ)
    fr = framing.decide(1280, 720, _pairs(136, 240, x_motion=190))
    assert not fr["pad"] and fr["box"][3] == 720 and fr["box"][2] == pytest.approx(720 * 9 / 16, abs=8)
    assert fr["box"][0] > 640 and "tay / chảo" in fr["why"]
    # chuyển động rải khắp khung -> nền mờ, giữ nguyên khung
    fr = framing.decide(1280, 720, _pairs(136, 240, n=10, spread=True))
    assert fr["pad"] and fr["box"] == [0, 0, 1280, 720] and "nền mờ" in fr["why"]
    # nguồn dọc bình thường -> như cũ
    assert framing.decide(720, 1280, _pairs(426, 240)) == {}
    # nguồn dọc có video ngang ở giữa (dải đen trên dưới) -> bỏ dải đen
    fr = framing.decide(720, 1280, _pairs(426, 240, n=10, bars_tb=140))
    assert fr["box"][1] == pytest.approx(140 * 1280 / 426, abs=8) and fr["box"][3] < 720 and "dải đen" in fr["why"]
    assert framing.decide(720, 1280, []) == {}


def test_map_bands():
    assert framing.map_bands([(0.8, 0.9)], {}) == [(0.8, 0.9)]
    # dải đen: chữ ở 0.6-0.65 của khung 1280 -> theo vùng nội dung y=420..860
    box = {"box": [0, 420, 720, 440], "pad": False, "src": [720, 1280]}
    a, b = framing.map_bands([(0.6, 0.65)], box)[0]
    assert a == pytest.approx((0.6 * 1280 - 420) / 440, abs=1e-3) and b == pytest.approx((0.65 * 1280 - 420) / 440, abs=1e-3)
    assert framing.map_bands([(0.05, 0.1)], box) == []          # chữ nằm trong dải đen đã bỏ: không còn trên khung ra
    # nền mờ: khung 16:9 vừa bề ngang nằm giữa -> chữ ở đáy khung gốc lên khoảng giữa dưới của khung ra
    pad = {"box": [0, 0, 1280, 720], "pad": True, "src": [1280, 720]}
    (a, b), = framing.map_bands([(0.85, 0.95)], pad)
    y, h = filtergraph.band_to_output(a, b)
    assert 640 + 100 < y < 640 + 360 // 2 + 200 and h >= 96


def test_look_chain_and_render(tmp_path):
    """Nguồn dọc có dải đen: khung ra không còn hàng đen ở trên; nguồn ngang nền mờ: đúng cỡ, có hình ở mép trên."""
    src = str(tmp_path / "lb.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x200:d=1:r=10",
                    "-vf", "pad=360:640:0:220:black", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    src], check=True)
    fr = framing.analyze(src, 1.0, 360, 640, samples=2)
    assert fr and fr["box"][1] >= 220 and fr["box"][3] <= 200 and fr["pad"]     # 360x200 ngang: nền mờ
    tok = framing.FRAMING.set(fr)
    try:
        chain = filtergraph.look_chain("cook", 180, 320, 360, 640)
        assert chain.count("split=2") == 1 and "boxblur" in chain and chain.startswith(framing.pre_crop(fr))
        assert filtergraph.look_chain("cook", 180, 320, 640, 360) == filtergraph.look_chain("cook", 180, 320, 640, 360) \
            and "boxblur" not in filtergraph.look_chain("cook", 180, 320, 640, 360)          # nguồn khác cỡ: như cũ
        import re
        one, two = (set(re.findall(r"\[fra(\d+)\]", filtergraph.look_chain("cook", 180, 320, 360, 640))) for _ in range(2))
        assert len(one) == 1 and one != two                          # nhãn riêng mỗi lần gọi (một đồ thị nhiều khúc)
        out = str(tmp_path / "f.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-frames:v", "1", "-vf", chain, out], check=True)
    finally:
        framing.FRAMING.reset(tok)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(320, 180)
    assert img[:20].mean() > 30 and img[-20:].mean() > 30              # nền mờ thay cho dải đen
    assert 200 > img[150:170].std() > 10                               # khung chính có chi tiết


def test_ensure_spec_and_kaggle(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(framing, "analyze", lambda p, d, w, h: {"box": [0, 100, 720, 400], "pad": False, "src": [720, 1280],
                                                                "why": "bỏ dải đen (30% khung)"})

    async def run():
        fr = await framing.ensure("x.mp4", 10.0, 720, 1280, notes.append)
        return fr, framing.spec_value()
    fr, sv = asyncio.run(run())
    assert fr["box"] == [0, 100, 720, 400] and "dải đen" in notes[0]
    assert sv == {"box": [0, 100, 720, 400], "pad": False, "src": [720, 1280]}
    monkeypatch.setattr(config, "FRAMING_AUTO", "0")
    assert asyncio.run(framing.ensure("x.mp4", 10.0, 720, 1280)) == {}

    # Kaggle: spec mang khung, phiên Kaggle dùng đúng khung đó
    tok = framing.FRAMING.set({"box": [0, 100, 720, 400], "pad": False, "src": [720, 1280], "why": "x"})
    try:
        spec = kaggle_worker._common({"kind": "bed"})
    finally:
        framing.FRAMING.reset(tok)
    assert spec["framing"] == {"box": [0, 100, 720, 400], "pad": False, "src": [720, 1280]}

    async def check():
        framing.apply_spec(spec)
        return framing.current(720, 1280)
    assert asyncio.run(check())["box"] == [0, 100, 720, 400]
    assert "framing" not in kaggle_worker._common({"kind": "bed"})
    assert hasattr(kaggle_job, "run_one")


def test_build_filter_maps_cover_bands():
    base = filtergraph.build_filter("cook", 10.0, False, None, cover_bands=[(0.6, 0.65)], W=720, H=1280,
                                    src_w=720, src_h=1280)
    tok = framing.FRAMING.set({"box": [0, 420, 720, 440], "pad": False, "src": [720, 1280]})
    try:
        moved = filtergraph.build_filter("cook", 10.0, False, None, cover_bands=[(0.6, 0.65)], W=720, H=1280,
                                         src_w=720, src_h=1280)
    finally:
        framing.FRAMING.reset(tok)
    assert base != moved and "crop=720:440:0:420," in moved
    y_old = filtergraph.band_to_output(0.6, 0.65)[0]
    y_new = filtergraph.band_to_output(*framing.map_bands([(0.6, 0.65)], {"box": [0, 420, 720, 440], "pad": False,
                                                                          "src": [720, 1280]})[0])[0]
    assert f"overlay=0:{y_new}" in moved and f"overlay=0:{y_old}" not in moved and os.sep


# ---------------- chạy thật: pipeline Botkeep, kế hoạch dựng nhiều khúc + đoạn mở đầu + clip phụ ----------------

from tests import test_edl as _te, test_no_speech as _tn  # noqa: E402

lib, silent_job, media = _tn.lib, _tn.silent_job, _te.media     # fixture: video không lời, dựng theo kế hoạch
P, _dur, _plan, _song = _te.P, _te._dur, _te._plan, _tn._song


def test_pipeline_landscape_source_gets_blur_pad(silent_job, monkeypatch, tmp_path):
    import bgm
    import core_video
    import pipeline
    job, work, beds = silent_job
    monkeypatch.setattr(config, "FRAMING_AUTO", "1")
    src = str(tmp_path / "wide.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=25",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3:duration=6", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", src], check=True)
    job.local = src
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "a.mp3"))
    out = asyncio.run(pipeline.process(job, work))
    assert asyncio.run(core_video.source_info(out))[:2] == (720, 1280)
    assert "Khung dọc" in job.error and "nền mờ" in job.error


def test_render_plan_with_pad_framing(media):
    """Nền mờ trong kế hoạch dựng: mỗi khúc (cả clip phụ, đoạn mở đầu) một bộ nhãn riêng, đồ thị ffmpeg hợp lệ."""
    from rmvideo.media import edl, renderer
    tmp, src, voice, amb = media
    plan = _plan()
    plan.pieces.insert(1, P(15.0, 16.0, zoom=1.12, role="side", tag="montage"))
    out = str(tmp / "o.mp4")
    tok = framing.FRAMING.set({"box": [0, 200, 360, 240], "pad": True, "src": [360, 640]})
    try:
        got = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, [(0.70, 0.80)], amb, None, allow_hd=False,
                                          title="THỬ", tmp=str(tmp), edit=plan.to_dict(),
                                          teaser={"a": 16.0, "dur": 2.0, "still": False}))
    finally:
        framing.FRAMING.reset(tok)
    assert got == pytest.approx(plan.out_dur() + 2.0, abs=0.05) and _dur(out) == pytest.approx(got, abs=0.15)
    assert isinstance(edl.EditPlan, type)
