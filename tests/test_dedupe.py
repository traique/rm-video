"""Bản 42 (S5-S6): dấu vân hình chống trùng (cùng video ở cỡ khác / có logo / lật ngang), thẻ mẹo trên video."""
import asyncio
import subprocess

import config
from rmvideo.app import brand
from rmvideo.app.flow import dedupe
from rmvideo.media import edl, renderer


def _ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, capture_output=True, timeout=120)


def test_same_video_reposted_is_detected(tmp_path):
    a, b, c, d = (str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4", "d.mp4"))
    _ff("-f", "lavfi", "-i", "mandelbrot=s=360x640:r=25", "-t", "10", a)
    # bản đăng lại: cỡ khác, nén nặng, có logo chữ ở góc, lật ngang
    _ff("-i", a, "-vf", "scale=540:960,drawbox=x=20:y=20:w=120:h=40:color=white:t=fill", "-crf", "35", b)
    _ff("-i", a, "-vf", "hflip", c)
    _ff("-f", "lavfi", "-i", "testsrc2=s=360x640:r=25", "-t", "10", d)
    fa, fb, fc, fd = (asyncio.run(dedupe.fingerprint(p)) for p in (a, b, c, d))
    assert len(fa) == dedupe.N
    assert dedupe.same(fb, fa) and dedupe.same(fc, fa)
    assert not dedupe.same(fd, fa)
    assert dedupe.duplicate(fb, {"tt:1": fd, "tt:2": fa}) == "tt:2" and dedupe.duplicate([], {"x": fa}) == ""


def test_autopost_skips_duplicate_without_streak(monkeypatch, tmp_path):
    import autopost
    import state
    st = autopost.load_state(str(tmp_path / "a.json"))
    st["pending"] = {"vid": "fb:9", "source": "x", "job": 8}

    class J:
        id, status, error, no_autopost, source_meta = 8, "error", f"{dedupe.DUP_MARK}: giống video tt:1", "", {}
    monkeypatch.setattr(state.queue, "jobs", {8: J()})
    msg = asyncio.run(autopost._finish_pending(st, 1000.0, None))
    assert dedupe.DUP_MARK in msg and st["pending"] is None and st.get("streak", 0) == 0


def test_tip_card_renders(tmp_path, monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    for k, d in brand.OLD_DEFAULTS.items():      # như khi CHƯA đặt nhận diện kênh (mặc định bản 43: test_ban43.py)
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    src, voice, out = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav"), str(tmp_path / "o.mp4")
    _ff("-f", "lavfi", "-i", "testsrc2=s=360x640:d=9:r=25", "-f", "lavfi", "-i", "sine=d=9", "-shortest", src)
    _ff("-f", "lavfi", "-i", "sine=f=500:d=6", voice)
    card = {"t": 2.0, "dur": 3.0, "kind": "tip", "text": "MẸO: Chiên hai lần cho cánh gà giòn lâu, không bị ỉu khi để nguội."}
    fc = renderer.overlay_filter([edl.Overlay(**card)], 720, 1280, 0.0, str(tmp_path))
    assert "between(t,2.00,5.00)" in fc and "boxcolor=0xFFE100@0.9" in fc
    assert "\n" in open(tmp_path / "ov_0.txt", encoding="utf-8").read()            # mẹo dài: xuống dòng
    asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, [(0.0, 9.0)], allow_hd=False,
                                tmp=str(tmp_path), overlays=[card]))
    assert asyncio.run(dedupe.ff.probe_duration(out)) > 8.5
