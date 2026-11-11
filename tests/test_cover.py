"""Ảnh bìa tự động (cover.py) và việc giao ảnh bìa kèm video."""
import asyncio
import os
import subprocess

import numpy as np

import config
import cover


def _frames():
    """4 khung: mờ, tối, nét + rực màu, nét nhưng xám."""
    rng = np.random.default_rng(1)
    sharp = (rng.integers(0, 255, (cover.SH, cover.SW, 3))).astype(np.uint8)
    blur = np.full_like(sharp, 120)
    blur[..., 0] = 160
    dark = (sharp * 0.04).astype(np.uint8)
    gray = np.repeat(sharp.mean(axis=2, keepdims=True), 3, axis=2).astype(np.uint8)
    return np.stack([blur, dark, sharp, gray])


def test_score_prefers_sharp_colorful_and_excludes_edges():
    fr = _frames()
    times = np.array([3.0, 5.0, 7.0, 9.0])
    sc = cover.score_frames(fr, times, 12.0, "cook")
    assert int(np.argmax(sc)) == 2
    assert sc[2] > sc[3] > sc[0]                       # rực màu > xám > mờ
    assert not np.isfinite(sc[1])                      # khung gần đen: loại
    sc2 = cover.score_frames(fr, times, 12.0, "cook", cuts=[7.1], end=12.0)
    assert not np.isfinite(sc2[2])                     # sát điểm chuyển cảnh (nhoè): loại
    sc3 = cover.score_frames(fr, times, 12.0, "cook", end=8.0)
    assert not np.isfinite(sc3[2]) and not np.isfinite(sc3[3])   # trong đoạn quảng cáo đã cắt / màn kết


def test_pick_candidates_spread_out():
    times = np.arange(0, 10, 0.5)
    scores = -np.abs(times - 5.0)
    c = cover.pick_candidates(times, scores, k=3, min_gap=1.5)
    t = sorted(float(times[i]) for i in c)
    assert t == [3.5, 5.0, 6.5]


def test_title_layout():
    assert cover.cover_title("Gà chiên nước mắm 🔥🔥 #nauan https://x.y") == "Gà chiên nước mắm"
    txt, size = cover.layout_title("Gà chiên nước mắm")
    assert txt.count("\n") <= 2 and size == 0.068
    long_txt, small = cover.layout_title("Cách làm thịt kho tàu mềm rục nước màu cánh gián chuẩn vị miền Tây "
                                         "ăn hoài không ngán luôn bà con ơi")
    assert long_txt.count("\n") == 2 and small < size


def test_make_cover_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    src = str(tmp_path / "s.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:r=24:d=4", "-f", "lavfi",
                    "-i", "color=c=0x101010:s=360x640:r=24:d=2", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", src], check=True)
    p = asyncio.run(cover.make_cover(src, str(tmp_path), "cook", "Bò lá lốt 🔥", 6.0))
    assert p and os.path.exists(p)
    dims = [subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", f],
                           capture_output=True, text=True).stdout.strip() for f in (p, str(tmp_path / cover.THUMB_NAME))]
    assert dims == ["720,1280", "180,320"]
    assert os.path.getsize(tmp_path / cover.THUMB_NAME) < 200 * 1024      # giới hạn thumbnail của Telegram
    monkeypatch.setattr(config, "COVER", "0")
    assert asyncio.run(cover.make_cover(src, str(tmp_path / "x"), "cook", "t", 6.0)) is None


def test_ai_pick_validates_index(monkeypatch):
    import prompt_engine
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images, schema=None, label="", timeout=60):
        assert len(images) == 3 and "ẢNH BÌA" in prompt
        return '{"best": 2, "reason": "món rõ"}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    assert asyncio.run(cover.ai_pick([b"a", b"b", b"c"], "Gà", "cook")) == 2

    async def bad(*a, **k):
        return '{"best": 9, "reason": ""}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", bad)
    assert asyncio.run(cover.ai_pick([b"a", b"b", b"c"], "Gà", "cook")) is None


def test_deliver_sends_cover_and_thumbnail(tmp_path, monkeypatch):
    import pipeline
    import state
    from queue_manager import Job
    sent = []

    class Bot:
        async def send_video(self, chat_id, video, caption="", thumbnail=None, **k):
            sent.append(("video", thumbnail is not None))

        async def send_photo(self, chat_id, photo, caption=""):
            sent.append(("photo", caption))
    monkeypatch.setattr(state, "bot", Bot())
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out")
    work = tmp_path / "w"
    os.makedirs(work)
    for n in ("output.mp4", cover.COVER_NAME, cover.THUMB_NAME):
        (work / n).write_bytes(b"x" * 10)
    job = Job(url="", mode="cook", duration=0, voice="v", source="tg", chat_id=1)
    asyncio.run(pipeline.deliver(job, str(work / "output.mp4"), "Video", "cap"))
    assert [p["label"] for p in job.parts] == ["Video", "Ảnh bìa"]
    assert job.output.endswith(".mp4") and job.parts[1]["path"].endswith(".jpg")
    assert sent[0] == ("video", True) and sent[1][0] == "photo"
