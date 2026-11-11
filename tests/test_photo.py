"""📸 Ảnh + nhạc (slideshow.py) và nhạc nền / nhạc TikTok cho chế độ 🎵."""
import asyncio
import os
import re
import subprocess

import pytest

import config
import core_video
import slideshow

KICK = 60 / 120          # 120 BPM


def _song(path, dur=30, drop=12.0):
    """Nhạc thử 120 BPM: trống + hi-hat, từ giây `drop` trống to + bass (đoạn drop)."""
    expr = (f"0.12*sin(2*PI*220*t)+(0.2+0.7*gte(t,{drop}))*sin(2*PI*55*t)*exp(-18*mod(t,{KICK}))"
            f"+0.2*(random(0)*2-1)*exp(-60*mod(t+{KICK / 2},{KICK}))+gte(t,{drop})*0.3*sin(2*PI*110*t)*exp(-6*mod(t,{KICK * 2}))")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={dur}", "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)], check=True)
    return str(path)


def _img(path, size, src="testsrc2"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s={size}", "-frames:v", "1", str(path)],
                   check=True)
    return str(path)


@pytest.fixture(scope="module")
def song(tmp_path_factory):
    return _song(tmp_path_factory.mktemp("m") / "song.mp3")


def test_analyze_tempo_and_drop(song):
    info = slideshow.analyze_music(song)
    assert abs(info["bpm"] - 120) < 1.0
    plan = slideshow.plan_video(info, 3, 0, seed=1)
    assert plan["drop"] is not None
    assert abs(plan["music_start"] + plan["drop"] - 12.0) < 0.15          # drop thật ở giây 12 của bài
    assert any(t["drop"] and t["type"] in slideshow.HIT for t in plan["transitions"])


def _info(bpm, density, dur=120.0, e_range=8.0):
    import numpy as np
    period = 60 / bpm
    beats = np.arange(0, dur, period)
    return {"duration": dur, "bpm": bpm, "period": period, "beats": beats, "energy": np.zeros(len(beats)),
            "e_range_db": e_range, "downbeat_phase": 0, "density": density, "env": np.ones(int(dur * slideshow.FPS_A))}


@pytest.mark.parametrize("bpm,density,style", [(80, 1.0, "calm"), (110, 1.6, "groove"), (140, 3.0, "hype")])
def test_plan_follows_beats(bpm, density, style):
    for n_img in (1, 4):
        plan = slideshow.plan_video(_info(bpm, density), n_img, 15, seed=7)
        assert plan["style"] == style
        slots, period = plan["slots"], plan["period"]
        assert slots[0]["start"] == 0 and abs(slots[-1]["end"] - plan["length"]) < 1e-6
        assert all(abs(a["end"] - b["start"]) < 1e-9 for a, b in zip(slots, slots[1:]))
        for t in plan["transitions"]:
            k = t["at"] / period
            assert abs(k - round(k)) < 1e-3                                # đổi cảnh đúng beat
            if style == "calm":
                assert abs(t["start"] + t["dur"] / 2 - t["at"]) < 1e-3    # êm: căn giữa beat
            else:
                assert abs(t["start"] + t["dur"] - t["at"]) < 1e-3        # dứt khoát: ảnh mới hiện trọn đúng beat
            if n_img == 1:
                assert t["type"] in slideshow.TRANSITIONS_ONE[style] + slideshow.HIT
        assert [s["img"] for s in slots][:4] == list(range(min(4, n_img))) + [0] * (min(4, len(slots)) - min(4, n_img)) \
            or n_img == 4
    # cùng đầu vào -> cùng kế hoạch (chạy lại ra đúng video cũ)
    assert slideshow.plan_video(_info(110, 1.6), 3, 15, seed=3) == slideshow.plan_video(_info(110, 1.6), 3, 15, seed=3)


def test_render_photo_video(tmp_path, song):
    imgs = [_img(tmp_path / "a.jpg", "720x1280"), _img(tmp_path / "b.png", "1280x720", "mandelbrot")]
    out = str(tmp_path / "o.mp4")
    plan = asyncio.run(slideshow.render(imgs, song, out, duration=6))
    d = asyncio.run(core_video.probe_duration(out))
    assert abs(d - plan["length"]) < 0.15 and abs(plan["length"] - 6) < 0.6
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?[0-9.]+) LUFS", err)[-1])
    assert abs(lufs + 14) < 1.0
    assert "Nhạc 120 BPM" in slideshow.describe(plan)


def test_bad_image_is_reported(tmp_path, song):
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(RuntimeError, match="Không đọc được ảnh"):
        asyncio.run(slideshow.render([str(bad)], song, str(tmp_path / "o.mp4")))


def test_pipeline_photo_job(tmp_path, song, monkeypatch):
    import pipeline
    from queue_manager import Job
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    job = Job(url="", mode="photo", duration=10, voice="", images=[_img(tmp_path / "a.jpg", "1080x1350")],
              music_local=song, title_hint="Đà Lạt", source="web")
    out = asyncio.run(pipeline.process(job, str(tmp_path)))
    assert os.path.exists(out) and "BPM" in job.script and job.post and 9 <= job.out_dur <= 11


def test_music_bg_keeps_original(tmp_path, song):
    src = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=8", "-f", "lavfi",
                    "-i", "sine=f=900:d=8", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
    out = str(tmp_path / "o.mp4")
    asyncio.run(core_video.render_music(src, song, out, 0, speed=1.0, trim=5.0, keep_orig=True))
    assert abs(asyncio.run(core_video.probe_duration(out)) - 8.0) < 0.2       # không đổi tốc độ video
    # tiếng gốc (900Hz) vẫn còn trong bản trộn
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "bandpass=f=900:w=60,volumedetect", "-f", "null",
                          "-"], capture_output=True, text=True).stderr
    assert float(re.findall(r"mean_volume: (-?[0-9.]+) dB", err)[-1]) > -40


def test_tiktok_music_prefers_original_sound(tmp_path, monkeypatch):
    calls = []

    def fake_sound(url, tmpdir):
        calls.append(url)
        p = os.path.join(tmpdir, "music.mp3")
        open(p, "wb").write(b"x" * 2000)
        return p, "Bài hát - Ca sĩ"
    monkeypatch.setattr(core_video, "_tiktok_sound_sync", fake_sound)
    monkeypatch.setattr(core_video.url_guard, "check_url", lambda u, **k: u)
    p = core_video._download_audio_sync("https://www.tiktok.com/@a/video/1", str(tmp_path))
    assert p.endswith("music.mp3") and calls
    assert core_video.is_tiktok("https://vt.tiktok.com/x") and not core_video.is_tiktok("https://youtube.com/x")


def test_web_photo_upload(tmp_path, monkeypatch, song):
    from aiohttp import FormData
    from aiohttp.test_utils import TestClient, TestServer

    import web_panel
    from queue_manager import JobQueue
    monkeypatch.setattr(config, "WEB_PASSWORD", "pw")
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "k"))
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    img = _img(tmp_path / "a.jpg", "360x640")

    def form(n, music=True, mode="photo"):
        fd = FormData()
        fd.add_field("mode", mode)
        fd.add_field("duration", "15")
        fd.add_field("title", "Đà Lạt")
        for k in range(n):
            fd.add_field("images", open(img, "rb"), filename=f"a{k}.jpg", content_type="image/jpeg")
        if music:
            fd.add_field("music", open(song, "rb"), filename="s.mp3", content_type="audio/mpeg")
        fd.add_field("file", b"video thua", filename="v.mp4", content_type="video/mp4")   # không dùng: phải bị xoá
        return fd

    async def go():
        async with TestClient(TestServer(web_panel.build_app(q, {}))) as c:
            h = {"X-Auth": "pw"}
            r = await c.post("/api/upload", data=form(2), headers=h)
            assert r.status == 200, await r.text()
            job = q.jobs[(await r.json())["id"]]
            assert job.mode == "photo" and len(job.images) == 2 and job.duration == 15 and job.music_local
            assert all(os.path.exists(p) for p in job.images)
            assert (await c.post("/api/upload", data=form(5), headers=h)).status == 400       # quá 4 ảnh
            assert (await c.post("/api/upload", data=form(1, music=False), headers=h)).status == 400   # thiếu nhạc
            r = await c.post("/api/jobs", json={"url": "https://x", "mode": "photo"}, headers=h)
            assert r.status == 400
            left = os.listdir(tmp_path / "up")
            assert len(left) == 3            # 2 ảnh + 1 nhạc của job hợp lệ; job lỗi không để lại file
    asyncio.run(go())


def test_image_size_follows_exif_rotation(tmp_path):
    """Ảnh điện thoại lưu nằm ngang + cờ EXIF xoay 90°: kích thước phải là sau khi xoay (dọc)."""
    import struct
    src = tmp_path / "l.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=800x600", "-frames:v", "1", str(src)],
                   check=True)
    data = src.read_bytes()
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, 6, 0) \
        + struct.pack(">I", 0)
    app1 = b"Exif\x00\x00" + tiff
    rot = tmp_path / "r.jpg"
    rot.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + data[2:])
    assert slideshow.image_size(str(rot)) == (600, 800)


def test_length_never_exceeds_music():
    plan = slideshow.plan_video(_info(120, 2.0, dur=20.0), 2, 60, seed=1)
    assert plan["music_start"] + plan["length"] <= 20.0
    assert "Nhạc chỉ còn đủ" in slideshow.describe(plan)
    auto = slideshow.plan_video(_info(100, 1.5, dur=4.5), 1, 0, seed=1)
    assert auto["music_start"] + auto["length"] <= 4.5


def test_groups_cover_video_frame_aligned():
    plan = slideshow.plan_video(_info(150, 3.0), 4, 60, seed=2)
    gs = slideshow.groups(plan, size=6)
    assert gs[0][2] == 0.0 and gs[-1][3] == plan["length"]
    assert all(b[2] == a[3] for a, b in zip(gs, gs[1:]))
    assert all(i1 - i0 + 1 <= 6 for i0, i1, _, _ in gs)
    assert all(abs(t0 * slideshow.FPS - round(t0 * slideshow.FPS)) < 1e-6 for _, _, t0, _ in gs)
    assert sum(i1 - i0 + 1 for i0, i1, _, _ in gs) == len(plan["slots"])


def test_render_in_segments_keeps_sync(tmp_path, song, monkeypatch):
    """Dựng theo khúc (ép khúc 2 cảnh): đủ khung, hình và tiếng dài đúng bằng nhau."""
    monkeypatch.setattr(slideshow, "GROUP", 2)
    imgs = [_img(tmp_path / "a.jpg", "360x640"), _img(tmp_path / "b.jpg", "640x360", "mandelbrot")]
    out = str(tmp_path / "o.mp4")
    plan = asyncio.run(slideshow.render(imgs, song, out, duration=10))
    assert plan["segments"] >= 2
    import json
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                                    "stream=codec_type,nb_read_frames,duration", "-of", "json", out],
                                   capture_output=True, text=True).stdout)
    st = {s["codec_type"]: s for s in pr["streams"]}
    assert abs(int(st["video"]["nb_read_frames"]) - round(plan["length"] * slideshow.FPS)) <= 1
    assert abs(float(st["audio"]["duration"]) - plan["length"]) < 0.05
    assert not [f for f in os.listdir(tmp_path) if f.startswith("slides_")]      # thư mục khúc tạm đã dọn


def test_music_bg_survives_short_original_audio(tmp_path, song):
    """Video 8s nhưng tiếng gốc chỉ 3s: nhạc nền vẫn phải chạy tới hết video (lỗi cũ: im lặng 5s cuối)."""
    src = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=8", "-f", "lavfi",
                    "-i", "sine=f=900:d=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src], check=True)
    out = str(tmp_path / "o.mp4")
    asyncio.run(core_video.render_music(src, song, out, 0, speed=1.0, trim=13.0, keep_orig=True))
    err = subprocess.run(["ffmpeg", "-hide_banner", "-ss", "5", "-i", out, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    assert float(re.findall(r"mean_volume: (-?[0-9.]+) dB", err)[-1]) > -40


def test_media_redirect_to_internal_is_blocked():
    import urllib.request

    import url_guard
    h = core_video._GuardedRedirect()
    req = urllib.request.Request("https://cdn.example.com/a.mp3")
    with pytest.raises(url_guard.BlockedURL):
        h.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data")


def test_fetch_media_leaves_no_partial_file(tmp_path, monkeypatch):
    class Resp:
        def __init__(self):
            self.n = 0

        def read(self, k):
            self.n += 1
            return b"x" * 1024 if self.n < 50 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(core_video.url_guard, "check_url", lambda u, **k: u)
    monkeypatch.setattr(core_video._GUARDED_OPENER, "open", lambda req, timeout=0: Resp())
    dst = str(tmp_path / "music.mp3")
    with pytest.raises(RuntimeError, match="quá"):
        core_video.fetch_media("https://x/a.mp3", dst, 10 * 1024, "File nhạc quá 20MB.")
    assert os.listdir(tmp_path) == []                      # không còn file dở để đường dự phòng nhặt nhầm
    assert core_video.fetch_media("https://x/a.mp3", dst, 1 << 20, "quá") == 49 * 1024 and os.path.exists(dst)


def test_make_room_keeps_waiting_photo_inputs(tmp_path, monkeypatch):
    import time as _t

    import storage
    from queue_manager import Job
    up = tmp_path / "up"
    up.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(up))
    keep_img, keep_mus, orphan = up / "img", up / "mus", up / "old"
    for f in (keep_img, keep_mus, orphan):
        f.write_bytes(b"x")
        os.utime(f, (_t.time() - 7200, _t.time() - 7200))
    j = Job(url="", mode="photo", duration=0, voice="", images=[str(keep_img)], music_local=str(keep_mus))
    monkeypatch.setitem(storage.queue.jobs, j.id, j)
    storage.make_room(0)
    assert keep_img.exists() and keep_mus.exists() and not orphan.exists()
