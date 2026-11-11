"""Công thức hiệu ứng trend (trendfx.py): đúng kiểu nhạc, không lặp giữa các lần tạo, bộ lọc hợp FFmpeg 5.1."""
import asyncio
import random
import re
import subprocess

import pytest

import core_video
import slideshow
import trendfx


def test_pick_matches_style_and_avoids_recent():
    rnd = random.Random(1)
    for style in ("calm", "groove", "hype"):
        ok = [k for k, t in trendfx.TEMPLATES.items() if style in t["styles"]]
        assert len(ok) >= 3
        recent, seen = [], []
        for _ in range(len(ok)):
            k = trendfx.pick(style, rnd, recent)
            assert k in ok and k not in seen          # đủ một vòng không lặp lại công thức nào
            seen.append(k)
            recent.insert(0, k)


def test_recent_history_roundtrip(tmp_path):
    p = str(tmp_path / "r.json")
    for name in ("glitch_rgb", "strobe", "glitch_rgb", "khong_co"):
        if name in trendfx.TEMPLATES:
            trendfx.remember(name, p)
    assert trendfx.load_recent(p) == ["glitch_rgb", "strobe"]
    open(p, "w").write("hỏng")
    assert trendfx.load_recent(p) == []


def test_fx_filters_ffmpeg51_safe():
    """Mọi công thức chỉ dùng bộ lọc có trong FFmpeg 5.1 (Botkeep) và có trong bài tự kiểm tra."""
    allowed = {"eq", "hue", "split", "scale", "gblur", "blend", "format", "hflip", "rgbashift", "drawbox", "noise",
               "vignette", "curves", "colorbalance"}
    beats = [round(k * 0.5, 3) for k in range(25)]
    for name, tpl in trendfx.TEMPLATES.items():
        sets = trendfx.beat_sets(tpl, tpl["styles"][-1], beats, 4.0, 12.0)
        chain = tpl["look"] + trendfx.video_fx(tpl, sets, 0.0, 12.0, 1080, 1920, 0.5, "t")
        used = set(re.findall(r"(?:^|[,;\]])([a-z0-9_]+)=", chain)) | set(re.findall(r"(?:^|,)([a-z]+),", chain))
        assert used <= allowed | {"all_mode"}, (name, used - allowed)
        assert "text_align" not in chain
    selftest = core_video.SELFTEST_VIDEO
    for f in allowed - {"split", "scale", "format", "eq"}:
        assert f in selftest, f


def test_parallel_slots(monkeypatch):
    monkeypatch.setattr(core_video, "cgroup_cpus", lambda: 1.5)
    monkeypatch.setattr(core_video, "mem_available_mb", lambda: 1800)
    assert slideshow.parallel_slots(1920) == 2
    monkeypatch.setattr(core_video, "mem_available_mb", lambda: 900)
    assert slideshow.parallel_slots(1920) == 1 and slideshow.parallel_slots(1280) == 2
    monkeypatch.setattr(core_video, "cgroup_cpus", lambda: 1.0)
    assert slideshow.parallel_slots(1280) == 1


def test_two_runs_give_different_videos(tmp_path, monkeypatch):
    """Làm lại cùng ảnh + nhạc: công thức hiệu ứng khác nhau (không trùng lặp mỗi lần tạo)."""
    monkeypatch.setattr(trendfx, "RECENT_FILE", str(tmp_path / "recent.json"))
    song = str(tmp_path / "s.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.6*sin(2*PI*55*t)*exp(-18*mod(t,0.5))+0.2*(random(0)*2-1)*exp(-60*mod(t+0.25,0.5))':"
                    "s=44100:d=20", "-ac", "2", "-c:a", "libmp3lame", song], check=True)
    img = str(tmp_path / "a.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640", "-frames:v", "1", img],
                   check=True)
    p1 = asyncio.run(slideshow.render([img], song, str(tmp_path / "1.mp4"), 4))
    p2 = asyncio.run(slideshow.render([img], song, str(tmp_path / "2.mp4"), 4))
    assert p1["template"] != p2["template"]
    assert trendfx.load_recent()[:2] == [p2["template"], p1["template"]]


@pytest.mark.parametrize("name", ["glitch_rgb", "mono_pop", "dreamy_glow", "cinematic"])
def test_template_renders(tmp_path, name):
    song = str(tmp_path / "s.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.6*sin(2*PI*55*t)*exp(-18*mod(t,0.5))':s=44100:d=12", "-ac", "2", song], check=True)
    img = str(tmp_path / "a.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640", "-frames:v", "1", img],
                   check=True)
    plan = asyncio.run(slideshow.render([img, img], song, str(tmp_path / "o.mp4"), 5, seed=3, template=name))
    assert plan["template"] == name
    assert trendfx.TEMPLATES[name]["vi"] in slideshow.describe(plan)
