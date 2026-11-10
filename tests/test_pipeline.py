"""Chạy TRỌN process() với provider giả (không mạng): kéo dài video trong một lần render (#7), duyệt kịch bản
rồi render lại không gọi AI/không tải lại (#8), cache giọng, cắt quảng cáo áp lại sau duyệt (N1)."""
import asyncio
import os
import subprocess

import pytest

import cache
import config
import core_video
import main
import prompt_engine
import vocal_sep
from queue_manager import Job

ORIG = [(0.5, 1.8, "cho thịt vào chảo"), (2.0, 3.4, "đảo đều tay"), (3.6, 4.8, "nêm muối vừa ăn"),
        (5.0, 7.8, "bà con bấm vào giỏ hàng góc trái màn hình mua ngay")]


def _video(path, dur=8):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=360x640:rate=25",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", path], check=True)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    calls = {"tts": 0, "llm": 0, "sep_windows": None}
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    monkeypatch.setattr(config, "TTS_PROVIDER", "capcut")
    monkeypatch.setattr(config, "CUT_PROMO", "1")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    monkeypatch.setattr(config, "QUALITY", "fast")

    async def fake_transcribe(audio):
        return " ".join(t for *_, t in ORIG), list(ORIG)
    monkeypatch.setattr(main, "transcribe_segments", fake_transcribe)

    async def fake_timed(title, desc, mode, segs, out_dur, images_for=None):
        calls["llm"] += 1
        if images_for:
            imgs = await images_for(segs)
            assert len(imgs) == len(segs) and all(imgs)
        out = [{"i": k, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": f"Lời mới đoạn {k} nè",
                "orig": t} for k, (a, b, t) in enumerate(segs)]
        return {"segments": out, "script": " ".join(x["text"] for x in out), "fresh": 1.0,
                "title": "Món ngon", "caption": "Ngon lắm", "hashtags": "#a"}
    monkeypatch.setattr(prompt_engine, "generate_timed_any", fake_timed)

    async def fake_synth(text, voice, path):
        calls["tts"] += 1
        d = 0.25 * len(text.split())
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=500:duration={d}",
                        "-ar", "24000", "-ac", "1", path], check=True)
        return None                                  # như CapCut: không có mốc từ
    monkeypatch.setattr(core_video, "synth_clip", fake_synth)

    async def fake_sep(src, out, timeout=0, windows=None):
        calls["sep_windows"] = windows
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "2", "-ar", "44100", out], check=True)
        return True
    monkeypatch.setattr(vocal_sep, "remove_voice", fake_sep)
    return calls


def test_stretch_review_approve_flow(fakes, tmp_path):
    src = str(tmp_path / "up" / "in.mp4")
    _video(src)
    job = Job(url="", mode="cook", duration=10, voice=config.CAPCUT_VOICE_FEMALE, local=src, review=True)
    tmp1 = tmp_path / "w1"
    tmp1.mkdir()
    assert asyncio.run(main.process(job, str(tmp1))) == "REVIEW"
    assert fakes["llm"] == 1 and fakes["tts"] == 0
    # mốc lời nhân theo hệ số kéo dài 10/8 = 1.25; đoạn quảng cáo cuối (5.0s gốc = 6.25s) bị cắt
    assert job.cut_at == pytest.approx(5.0 * 1.25 - 0.05, abs=0.02)
    assert [s["orig"] for s in job.approved] == [t for *_, t in ORIG[:3]]
    assert job.approved[1]["start"] == pytest.approx(2.0 * 1.25, abs=0.2)

    job.status = "review"
    main.queue.jobs[job.id] = job
    new = main.approve_job(job, {1: "Đảo đều tay cho thịt săn lại nha"})
    tmp2 = tmp_path / "w2"
    tmp2.mkdir()
    out = asyncio.run(main.process(new, str(tmp2)))
    assert fakes["llm"] == 1                              # bản duyệt không gọi AI lần nữa
    assert fakes["tts"] == 3                              # 3 đoạn, chưa có cache
    dur = asyncio.run(core_video.probe_duration(out))
    assert dur == pytest.approx(job.cut_at, abs=0.15)      # vẫn cắt quảng cáo cuối sau khi duyệt (N1)
    assert fakes["sep_windows"] and fakes["sep_windows"][0][0] == pytest.approx(0.2, abs=0.01)   # cửa sổ theo giờ GỐC

    # render lại lần nữa: đoạn không đổi lấy từ cache giọng
    again = main.approve_job(job, {1: "Đảo đều tay cho thịt săn lại nha"})
    tmp3 = tmp_path / "w3"
    tmp3.mkdir()
    asyncio.run(main.process(again, str(tmp3)))
    assert fakes["tts"] == 3


def test_stretch_happens_inside_render(fakes, tmp_path, monkeypatch):
    seen = {}
    real = core_video.render

    async def spy(*a, **kw):
        seen.update(kw)
        return await real(*a, **kw)
    monkeypatch.setattr(core_video, "render", spy)
    monkeypatch.setattr(config, "CUT_PROMO", "0")
    src = str(tmp_path / "up" / "in2.mp4")
    _video(src)
    job = Job(url="", mode="review", duration=10, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    tmp = tmp_path / "w"
    tmp.mkdir()
    out = asyncio.run(main.process(job, str(tmp)))
    assert seen["stretch"] == pytest.approx(1.25)
    assert not any(f.startswith("input_slow") for f in os.listdir(tmp))   # không còn file mã hoá trung gian
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(10.0, abs=0.15)


def test_review_keeps_downloaded_source(fakes, tmp_path, monkeypatch):
    """#8: duyệt xong render lại KHÔNG tải lại video từ link."""
    import shutil
    master = str(tmp_path / "master.mp4")
    _video(master)
    downloads = {"n": 0}

    async def fake_download(url, tmpdir, max_duration=0):
        downloads["n"] += 1
        p = os.path.join(tmpdir, "input.mp4")
        shutil.copyfile(master, p)
        return p, "Tiêu đề", "", {"uploader": "Bếp Nhà", "webpage_url": url}
    monkeypatch.setattr(core_video, "download", fake_download)
    job = Job(url="https://www.tiktok.com/@a/video/1", mode="cook", duration=0,
              voice=config.CAPCUT_VOICE_FEMALE, review=True)
    tmp1 = tmp_path / "w1"
    tmp1.mkdir()
    assert asyncio.run(main.process(job, str(tmp1))) == "REVIEW"
    assert job.local.startswith(config.UPLOAD_DIR) and os.path.exists(job.local)
    assert job.source_meta["uploader"] == "Bếp Nhà"
    job.status = "review"
    new = main.approve_job(job)
    tmp2 = tmp_path / "w2"
    tmp2.mkdir()
    asyncio.run(main.process(new, str(tmp2)))
    assert downloads["n"] == 1 and new.source_meta["uploader"] == "Bếp Nhà"
