"""1.4.6 (log 24/11 job 3 truyện bé): Kaggle không cấp GPU 75 phút -> giữ chỗ trong hàng chờ, kids_auto theo dõi, job thu
kết quả; Lyria đứng im theo gói; ý truyện dài không bị loại hết; tiêu đề «bí mật» không báo lạc đề."""
import asyncio
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

import config
import kaggle_api
import kaggle_worker
import kids_auto
from rmvideo.app import job_factory
from rmvideo.kids import story
from test_kids import DATA, _fake_produce, _qa_small
from test_v144 import MOOD, _CutLyria


@pytest.fixture
def clean_parked():
    st = kids_auto.load_state()
    st["parked"], st["pending"], st["paused"], st["streak"] = [], None, "", 0
    kids_auto.save_state(st)
    yield
    st = kids_auto.load_state()
    st["parked"], st["pending"] = [], None
    kids_auto.save_state(st)


def _spec(job_id=7):
    return {"kind": "kids", "slug": "rmvideo-kids", "token": "tok" + str(job_id), "fetch_files": ["cover.jpg", "story.json"],
            "brief": {"main": "rabbit", "words": []}, "voice": {"engine": "vieneu"},
            "park": {"job": job_id, "source": "web", "chat_id": 0, "title_hint": "Thỏ Trắng"}}


# ---------------- giữ chỗ ----------------

def test_wait_parks_when_asked(monkeypatch):
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    with pytest.raises(kaggle_api.Parked, match="chưa cấp máy"):
        kaggle_api.wait("x", 100, park=lambda secs: True)
    seen = []
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("RUNNING", "") if seen.append(1) or len(seen) < 3 else ("COMPLETE", ""))
    assert kaggle_api.wait("x", 100, park=lambda secs: True) == "COMPLETE"     # đang chạy: không giữ chỗ, chờ tiếp
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("RUNNING", ""))
    with pytest.raises(kaggle_api.Parked, match="chưa xong"):                   # chạy quá giờ chờ: vẫn giữ chỗ
        kaggle_api.wait("x", 0, park=lambda secs: False)
    with pytest.raises(kaggle_api.KaggleError, match="chạy quá"):
        kaggle_api.wait("x", 0)


def test_run_sync_parks_kids_session(monkeypatch, clean_parked):
    """Job 3 ngày 24/11: chờ GPU 75 phút rồi bỏ -> mất truyện lẫn chỗ xếp hàng. Giờ: phiên gửi với hạn 4 giờ, có job khác
    chờ thì nhường worker sau 5 phút, phiên ghi vào kids_auto (parked) để thu kết quả sau."""
    sent = {}
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 99999})
    monkeypatch.setattr(kaggle_api, "push", lambda *a, **k: {})
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    monkeypatch.setattr(kaggle_worker, "kernel_text", lambda spec: sent.update(spec) or "x")
    monkeypatch.setattr(kaggle_worker, "KIDS_PARK_MIN", 0)
    monkeypatch.setattr(kaggle_worker, "_others_waiting", lambda: True)
    steps = []
    with pytest.raises(kaggle_api.Parked) as ei:
        kaggle_worker._run_sync(_spec(), "/tmp", steps.append)
    assert "park" not in sent and sent["expires_at"] > time.time() + 3.5 * 3600      # hạn bắt đầu dài, chat_id không lên Kaggle
    info = ei.value.info
    assert info["job"] == 7 and info["slug"] == "rmvideo-kids" and info["token"] == "tok7" and info["brief"]["main"] == "rabbit"
    p = kids_auto.parked_info(7)
    assert p and p["source"] == "web" and p["fetch_files"] == ["cover.jpg", "story.json"] and p["expires_at"] == info["expires_at"]
    assert kaggle_worker._ABORT["run"] is None


def test_abort_parks_only_when_asked(monkeypatch, clean_parked):
    run = kaggle_worker._watch("rmvideo-kids")
    run["park_info"] = {"job": 8, "slug": "rmvideo-kids", "token": "t", "since": time.time(), "expires_at": time.time() + 100}
    kaggle_worker.abort()                                   # người dùng huỷ: không giữ chỗ
    assert run["flag"] and kids_auto.parked_info(8) is None
    kaggle_worker._ABORT["run"] = run
    kaggle_worker.abort(park=True)                          # bot tắt / quá hạn: giữ chỗ
    assert kids_auto.parked_info(8)["token"] == "t"
    kaggle_worker._unwatch(run)


def test_watch_parked_collects_when_done(monkeypatch, clean_parked):
    from state import queue
    import state
    sent = []

    async def notify(text):
        sent.append(text)
    monkeypatch.setattr(state, "notify_admin", notify)
    now = time.time()
    base = {"slug": "rmvideo-kids", "token": "t9", "fetch_files": ["cover.jpg"], "gpu": True, "since": now - 600,
            "expires_at": now + 3000, "brief": {"main": "cat", "pitch": {"title": "x"}}, "source": "web", "chat_id": 5,
            "title_hint": "Mèo Cam"}
    kids_auto.park_record({**base, "job": 9})
    st = kids_auto.load_state()
    st["pending"] = {"job": 9, "slot_ts": now + 7200, "slot": "19:00"}
    kids_auto.save_state(st)
    old = job_factory.kids(brief={}, source="web")
    old.id, old.status, old.error = 9, "error", "Kaggle chưa cấp GPU"
    monkeypatch.setitem(queue.jobs, 9, old)
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))
    assert asyncio.run(kids_auto.watch_parked(now)) == [] and kids_auto.parked_info(9)
    assert "Giữ chỗ Kaggle: truyện #9" in kids_auto.status_text(now)
    put = []
    monkeypatch.setattr(queue, "put", lambda job, force=False: put.append(job) or 1)
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("COMPLETE", ""))
    msgs = asyncio.run(kids_auto.watch_parked(now))
    assert len(put) == 1 and "thu kết quả truyện #9" in msgs[0]
    job = put[0]
    assert job.source == "web" and job.chat_id == 5 and job.title_hint == "Mèo Cam" and job.brief["main"] == "cat"
    assert job.brief["collect"]["token"] == "t9" and job.brief["collect"]["job"] == 9
    st = kids_auto.load_state()
    assert st["parked"] == [] and st["pending"]["job"] == job.id and not sent


def test_watch_parked_drops_failed_or_expired(monkeypatch, clean_parked):
    import state
    sent = []

    async def notify(text):
        sent.append(text)
    monkeypatch.setattr(state, "notify_admin", notify)
    now = time.time()
    kids_auto.park_record({"job": 11, "slug": "rmvideo-kids", "token": "a", "since": now - 5 * 3600,
                           "expires_at": now - 3600, "brief": {}})
    kids_auto.park_record({"job": 12, "slug": "rmvideo-kids", "token": "b", "since": now - 60, "expires_at": now + 3600,
                           "brief": {}})
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))
    msgs = asyncio.run(kids_auto.watch_parked(now))
    assert len(msgs) == 1 and "không làm xong truyện #11" in msgs[0] and kids_auto.parked_info(11) is None
    assert kids_auto.parked_info(12) and len(sent) == 1
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("ERROR", "CUDA out of memory"))
    msgs = asyncio.run(kids_auto.watch_parked(now))
    assert "truyện #12 lỗi" in msgs[0] and kids_auto.parked_info(12) is None and len(sent) == 2


def test_finish_pending_keeps_parked_job(monkeypatch, clean_parked):
    """Job tự động hỏng vì chờ GPU nhưng phiên còn giữ chỗ: kids_auto không tính là thất bại (không tăng streak)."""
    from state import queue
    monkeypatch.setattr(config, "KIDS_AUTO", "1")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_ID", "222")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "tok-kids")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    now = time.time()
    job = job_factory.kids(brief={}, source="auto")
    job.status, job.error = "error", "Kaggle chưa cấp GPU sau 75 phút"
    monkeypatch.setitem(queue.jobs, job.id, job)
    kids_auto.park_record({"job": job.id, "slug": "rmvideo-kids", "token": "a", "since": now, "expires_at": now + 3600,
                           "brief": {}})
    st = kids_auto.load_state()
    st["pending"] = {"job": job.id, "slot_ts": now + 7200, "slot": "19:00"}
    kids_auto.save_state(st)
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))
    msg = asyncio.run(kids_auto.tick(now))
    assert msg.startswith("đang giữ chỗ Kaggle") and kids_auto.load_state()["streak"] == 0


def _video(dst):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=2", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", dst], check=True)


def test_collect_parked_fetches_result(monkeypatch, tmp_path):
    vid = str(tmp_path / "src.mp4")
    _video(vid)
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("COMPLETE", ""))
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": [{"fileName": "result.json", "url": "r"},
                                                                       {"fileName": "output.mp4", "url": "v"},
                                                                       {"fileName": "cover.jpg", "url": "c"}]})

    def dl(url, dst, *a, **k):
        if url == "r":
            json.dump({"ok": True, "token": "tk", "duration": 2.0}, open(dst, "w"))
        elif url == "v":
            shutil.copy(vid, dst)
        else:
            open(dst, "wb").write(b"jpg")
        return 1
    monkeypatch.setattr(kaggle_api, "download", dl)
    monkeypatch.setattr(kaggle_worker, "_log_quota_sync", lambda: None)
    park = {"slug": "rmvideo-kids", "token": "tk", "fetch_files": ["cover.jpg", "story.json"], "gpu": True}
    res = asyncio.run(kaggle_worker.collect_parked(park, str(tmp_path)))
    assert os.path.exists(res["video"]) and res["files"]["cover.jpg"].endswith("kaggle_cover.jpg") and res["gpu"]
    park["token"] = "khac"
    with pytest.raises(kaggle_api.KaggleError, match="không phải của việc này"):
        asyncio.run(kaggle_worker.collect_parked(park, str(tmp_path)))


def test_flow_kids_collect_job(tmp_path, monkeypatch, clean_parked):
    """Job thu kết quả (brief.collect): không nghĩ ý / viết / gửi Kaggle, chỉ tải về rồi làm bài đăng; truyện ghi dưới mã
    job cũ. Job chạy lại sau khi bot tắt lúc đang giữ chỗ (parked_info theo mã job) cũng vậy."""
    import pipeline
    from rmvideo.kids import job as kids_job
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    calls, produced = [], []
    fake_collect = _fake_produce(tmp_path, [])

    async def collect(park, tmp, on_step=None):
        produced.append(park)
        return await fake_collect({"brief": brief}, tmp, on_step)
    monkeypatch.setattr(kaggle_worker, "collect_parked", collect)
    monkeypatch.setattr(kaggle_worker, "produce", _fake_produce(tmp_path, calls))
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)
    old = kids_auto.make_now("web")                              # job đã nghĩ đề, giữ chỗ rồi hỏng
    brief = old.brief
    col = {"slug": "rmvideo-kids", "token": "t1", "fetch_files": ["cover.jpg"], "gpu": True, "job": old.id}
    job = job_factory.kids(brief={**brief, "collect": col}, source="web")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and produced == [col] and calls == [] and "giữ chỗ" in job.error
    st = kids_auto.load_state()
    assert st["stories"][-1]["job"] == old.id and st["stories"][-1]["title"] == job.hook     # không thêm mục truyện mới
    # chạy lại chính job cũ (bot tắt lúc đang giữ chỗ): thấy phiên giữ chỗ của mình -> thu kết quả, bỏ mục giữ chỗ
    kids_auto.park_record({**col, "since": time.time(), "expires_at": time.time() + 100, "brief": brief})
    produced.clear()
    out = asyncio.run(pipeline.process(old, str(tmp_path / "w")))
    assert os.path.exists(out) and len(produced) == 1 and kids_auto.parked_info(old.id) is None


# ---------------- Lyria ----------------

class _HangLyria:
    """Mỗi phiên gửi một gói `chunk` giây rồi đứng im (không gửi, không đóng)."""

    def __init__(self, chunk=3.0):
        self.sessions = 0
        outer = self

        class _S:
            async def set_weighted_prompts(self, prompts):
                pass

            async def set_music_generation_config(self, config):
                pass

            async def play(self):
                pass

            async def stop(self):
                pass

            async def receive(self):
                yield SimpleNamespace(server_content=SimpleNamespace(audio_chunks=[SimpleNamespace(
                    data=(500).to_bytes(2, "little", signed=True) * 2 * int(48000 * chunk))]), filtered_prompt=None)
                await asyncio.sleep(3600)
                yield None

        class _C:
            async def __aenter__(self):
                outer.sessions += 1
                return _S()

            async def __aexit__(self, *a):
                return False
        self.aio = SimpleNamespace(live=SimpleNamespace(music=SimpleNamespace(connect=lambda model: _C())))


def test_lyria_chunk_stall_reconnects(monkeypatch):
    """Notebook nhạc 24/11: một bài «kết nối đứng im» 250 giây, mất cả phần đã nhận. Giờ: không có gói nào trong CHUNK_STALL
    giây thì ngắt phiên, giữ phần đã nhận, nối phiên mới."""
    from rmvideo.media import lyria
    monkeypatch.setattr(lyria, "CHUNK_STALL", 0.2)
    monkeypatch.setattr(lyria, "_SLOW", {"until": 0.0, "why": ""})
    logs = []
    fake = _HangLyria(chunk=3.0)
    t0 = time.time()
    pcm = asyncio.run(lyria.stream(MOOD, 5.0, fake, logs.append, timeout=60, stall=5))
    assert len(pcm) == int(5.0 * lyria.RATE * lyria.CH * 2) and fake.sessions == 3 and time.time() - t0 < 5
    assert any("không gửi gói nào" in m for m in logs) and lyria.slow_left() == 0


class _SlowLyria:
    """Mỗi phiên gửi gói `chunk` giây cách nhau `every` giây (chậm hơn thời gian thật), gửi `n` gói rồi đóng."""

    def __init__(self, chunk=0.01, every=0.06, n=50):
        self.sessions = 0
        outer = self

        class _S:
            async def set_weighted_prompts(self, prompts):
                pass

            async def set_music_generation_config(self, config):
                pass

            async def play(self):
                pass

            async def stop(self):
                pass

            async def receive(self):
                for _ in range(n):
                    await asyncio.sleep(every)
                    yield SimpleNamespace(server_content=SimpleNamespace(audio_chunks=[SimpleNamespace(
                        data=(500).to_bytes(2, "little", signed=True) * 2 * int(48000 * chunk))]), filtered_prompt=None)
                raise ConnectionError("1006 None. abnormal closure [internal]")

        class _C:
            async def __aenter__(self):
                outer.sessions += 1
                return _S()

            async def __aexit__(self, *a):
                return False
        self.aio = SimpleNamespace(live=SimpleNamespace(music=SimpleNamespace(connect=lambda model: _C())))


def test_lyria_slow_detected_inside_session(monkeypatch):
    """Botkeep: nhận chậm rõ rệt thì ngắt ngay trong phiên (không đợi máy chủ cắt sau 51 giây); SLOW_RATE=0 (notebook Kaggle)
    thì không bao giờ kết luận chậm / nghỉ."""
    from rmvideo.media import lyria
    monkeypatch.setattr(lyria, "_SLOW", {"until": 0.0, "why": ""})
    monkeypatch.setattr(lyria, "SLOW_MIN", 0.1)
    monkeypatch.setattr(lyria, "CHUNK_STALL", 0.5)
    fake = _SlowLyria()
    t0 = time.time()
    with pytest.raises(lyria.Slow, match="mạng không theo kịp"):
        asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=60, stall=5))
    assert fake.sessions == 1 and lyria.slow_left() > 0 and time.time() - t0 < 1.5
    monkeypatch.setattr(lyria, "_SLOW", {"until": 0.0, "why": ""})
    monkeypatch.setattr(lyria, "SLOW_RATE", 0.0)
    fake = _SlowLyria(n=3)
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=1, stall=0.5))
    assert not isinstance(ei.value, lyria.Slow) and lyria.slow_left() == 0 and fake.sessions == lyria.SESSIONS


def test_music_kernel_disables_slow_and_expires():
    from rmvideo.media import lyria_kaggle
    text = lyria_kaggle.kernel_text({"key": "k", "token": "t", "budget": 10, "tracks": [], "expires_at": 1})
    assert "ly.SLOW_RATE = 0.0" in text and "expires_at" in text


def test_pick_skips_botkeep_lyria_once_kaggle_proven(tmp_path, monkeypatch):
    from rmvideo.kids import bgm
    from rmvideo.media import lyria_kaggle
    monkeypatch.setattr(lyria_kaggle, "STATE_FILE", str(tmp_path / "state.json"))
    asked = []
    monkeypatch.setattr(lyria_kaggle, "start_later", lambda **k: asked.append(k) or "gửi notebook nhạc lên Kaggle")
    fake = _CutLyria(per=10.0)
    assert not lyria_kaggle.proven()
    lyria_kaggle.save_state({"status": "done", "at": time.time(), "proven_at": time.time()})
    assert lyria_kaggle.proven()
    logs = []
    out = asyncio.run(bgm.pick({"plot": ""}, {"plots": []}, lib_dir=str(tmp_path / "lib"), client=fake, log=logs.append))
    assert out is None and fake.sessions == 0 and len(asked) == 1 and "thiếu bài" in asked[0]["why"]
    assert logs == ["Nhạc bé: gửi notebook nhạc lên Kaggle"]


# ---------------- ý truyện, tiêu đề ----------------

def test_pitch_long_premises_still_used(monkeypatch):
    """Job 3 ngày 24/11: cả 4 ý 98-110 chữ bị LOẠI -> viết không có ý (mất màu nhạc, đồ vật đã chọn)."""
    from rmvideo.kids import pitch
    long = " ".join(["chữ"] * 105)
    assert pitch.problems({"premise": long, "title": "Mèo Cam và cái giỏ", "kind": "hài"}, []) == []
    longer = " ".join(["chữ"] * 130)
    assert pitch.problems({"premise": longer, "title": "Mèo Cam và cái giỏ", "kind": "hài"}, []) == ["ý 130 chữ"]
    raw = json.dumps({"pitches": [{"kind": "hài", "title": "Mèo Cam và cái giỏ nhẹ tênh", "premise": longer, "mood": "playful",
                                   "things": ["cái giỏ"]},
                                  {"kind": "bí ẩn", "title": "Bí mật dưới cái mền của Mèo Cam", "premise": longer + " nữa",
                                   "mood": "curious", "things": ["sữa"]}]}, ensure_ascii=False)
    judge = json.dumps({"scores": [{"i": 0, "score": 7}, {"i": 1, "score": 9}], "best": 1})

    async def ask(prompt, schema, models):
        return judge if prompt.startswith("Bạn là biên tập viên") else raw
    monkeypatch.setattr(pitch, "_ask", ask)
    logs = []
    brief = story.pick(DATA, [], __import__("random").Random(3))
    got = asyncio.run(pitch.choose(brief, DATA, [], [""], log=logs.append))
    assert got and got["title"].startswith("Bí mật") and any("chỉ dài quá" in m for m in logs)
    assert "tối đa 70 chữ" in pitch.PITCH_PROMPT


def test_title_abstract_words_not_mismatch():
    text = "Mèo Cam trải cái mền ra cỏ. Sữa đổ, Mèo Cam kéo mền che lại. Gấu Nâu nhìn thấy, cả hai cùng cười."
    assert story.title_mismatch("Bí mật dưới cái mền của Mèo Cam", text, DATA) == []
    assert story.title_mismatch("Chiếc bánh ngọt ngào của Mèo Cam", text, DATA)      # vẫn bắt tiêu đề lạc đề thật
