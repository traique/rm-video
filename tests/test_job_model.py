"""Phase 2: từ vựng job, ghi chú không bị đè, bus sự kiện, dữ liệu theo phạm vi job, một chỗ dựng Job."""
import asyncio
import json
import threading
from dataclasses import asdict

import config
import scenes
from queue_manager import Job
from rmvideo.app import context, events, job_factory
from rmvideo.domain.job import LongMode, Mode, Status


def test_enums_are_the_old_strings():
    job = Job(url="u", mode=Mode.COOK, duration=0, voice="v", long_mode=LongMode.SPLIT90)
    job.status = Status.DONE
    d = json.loads(json.dumps(asdict(job)))                       # jobs.db cũ / mới cùng định dạng
    assert d["mode"] == "cook" and d["long_mode"] == "split90" and d["status"] == "done"
    assert LongMode("split90").is_split and LongMode("split90").part_len == 90 and Mode("review").narrated
    assert "a,b".split(",") == ["a", "b"] and LongMode.SPLIT60.split("t") == ["spli", "60"]   # không che str.split


def test_note_appends_never_overwrites():
    job = Job(url="", mode="cook", duration=0, voice="")
    job.note("ℹ️ một. ")
    job.note("")
    job.note("⚠️ hai.")
    assert job.error == "ℹ️ một. ⚠️ hai."


def test_change_bus_wakes_waiters_and_watches_steps_from_threads():
    async def go():
        job = Job(url="", mode="cook", duration=0, voice="")
        seen = []
        v0 = events.version()
        with events.watch_steps(job, seen.append):
            waiter = asyncio.ensure_future(events.wait_change(v0, 5))
            await asyncio.sleep(0.05)
            t = threading.Thread(target=setattr, args=(job, "step", "Render (FFmpeg)"))   # vd tiến độ Kaggle
            t.start()
            t.join()
            assert await asyncio.wait_for(waiter, 2) > v0
            job.step = "Render (FFmpeg)"                          # không đổi: không báo
            job.timeline = [("x", 1.0)]                            # trường không hiển thị: không báo bước
        job.step = "sau khi bỏ theo dõi"
        assert seen == ["Render (FFmpeg)"]
        v1 = events.version()
        assert await events.wait_change(v1, 0.05) == v1            # hết giờ mà không đổi: trả lại phiên bản cũ
    asyncio.run(go())


def test_analysis_cache_lives_per_job():
    scenes._ANALYZED.clear()
    scenes.remember_analysis("/v.mp4", {"dir": "/chung", "n": 1})
    with context.job_scope():
        assert scenes.analyzed("/v.mp4") is None                  # job mới không thấy dữ liệu ngoài job
        for k in range(8):                                         # video dài 8 phần: không phần nào bị đẩy ra
            scenes.remember_analysis(f"/p{k}.mp4", {"dir": f"/t{k}", "n": 3})
        assert len(scenes._ANALYZED) == 8 and scenes.analyzed("/p0.mp4")["dir"] == "/t0"
    assert scenes.analyzed("/p0.mp4") is None and scenes.analyzed("/v.mp4")["dir"] == "/chung"
    for k in range(6):                                             # ngoài job: vẫn giữ 4 mục như cũ
        scenes.remember_analysis(f"/o{k}.mp4", {"n": k})
    assert len(scenes._ANALYZED) == 4
    scenes._ANALYZED.clear()


def test_job_factory_rules(monkeypatch):
    monkeypatch.setattr(config, "TTS_PROVIDER", "capcut")
    assert job_factory.parse_duration("split60") == (0, "split60") and job_factory.parse_duration("45") == (0, "")
    assert job_factory.parse_duration("60") == (60, "") and job_factory.video_mode("photo") == "cook"
    assert job_factory.telegram_voice("cook") == config.CAPCUT_VOICE_FEMALE
    assert job_factory.telegram_voice("review") == config.CAPCUT_VOICE_MALE
    j = job_factory.remake(mode="cook", voice="v", source="web", url="u", duration=60, long_mode="full", music_mix="bg")
    assert j.duration == 0 and j.long_mode == "full" and j.music_mix == ""      # nhạc nền chỉ cho chế độ 🎵
    m = job_factory.remake(mode="music", voice="", source="tg", url="u", music_mix="bg", quality="1080p-super-long")
    assert m.music_mix == "bg" and m.quality == "1080p-su"
    assert job_factory.download(source="tg", url="u", quality="best").quality == ""
    a = job_factory.auto(url="u", title="t" * 300, duration=120, mode="cook", voice="v", chat_id=5)
    assert a.long_mode == "full" and a.source == "auto" and len(a.title_hint) == 200
    parent = Job(url="", mode="review", duration=0, voice="v", subs=False, source="web", chat_id=9)
    parent.id = 77
    sub = job_factory.piece(parent, "/p.mp4", "Phần 1", {})
    assert (sub.id, sub.long_mode, sub.local, sub.subs, sub.chat_id) == (77, "full", "/p.mp4", False, 9)
    segs = job_factory.approved_segments([{"i": 0, "start": 1.0, "slot": 2.0, "text": "a", "extra": 1}])
    assert segs == [{"i": 0, "start": 1.0, "slot": 2.0, "span": 2.0, "text": "a", "orig": "", "ost": "new"}]
