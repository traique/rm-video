"""Giai đoạn 1 — hàng đợi bền qua restart, job chờ duyệt không bị rơi, đo đạc dịch vụ ngoài, kiểm tra định kỳ."""
import asyncio
import json

import config
import metrics
import queue_manager
from queue_manager import Job, JobQueue


def _mk(**kw):
    base = dict(url="https://youtu.be/x", mode="cook", duration=0, voice="")
    base.update(kw)
    return Job(**base)


def test_restart_requeues_and_retries_once(tmp_path):
    db = str(tmp_path / "q.db")
    q = JobQueue(db_path=db)
    waiting = _mk()
    running = _mk()
    upload_gone = _mk(url="", local=str(tmp_path / "khong-con.mp4"))
    review = _mk(status="review", chat_id=5, source="tg")
    q.put(waiting)
    q.put(running)
    running.status = "processing"
    q.save(running)
    q.save(upload_gone)
    q.jobs[review.id] = review
    q.save(review)

    q2 = JobQueue(db_path=db)                                    # "khởi động lại"
    assert {j.id for j in q2.restored} == {waiting.id, running.id}
    assert q2.pending == 2
    assert q2.jobs[running.id].attempts == 1
    assert q2.jobs[upload_gone.id].status == "error"
    assert q2.review_job_for(5).id == review.id                  # vẫn duyệt được sau restart

    r = q2.jobs[running.id]                                      # chết lần nữa giữa chừng -> dừng hẳn
    r.status = "processing"
    q2.save(r)
    q3 = JobQueue(db_path=db)
    assert q3.jobs[running.id].status == "error"


def test_review_jobs_never_evicted(tmp_path):
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    review = _mk(status="review")
    q.jobs[review.id] = review
    for _ in range(queue_manager.KEEP_IN_MEMORY + 10):
        j = _mk()
        q.put(j, force=True)
        j.status = "done"
    q.put(_mk(), force=True)
    assert review.id in q.jobs
    assert sum(1 for j in q.jobs.values() if j.status == "done") <= queue_manager.KEEP_IN_MEMORY


def test_review_expires_after_24h(tmp_path):
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    old = _mk(status="review", local=str(src))
    old.finished_at = 1000.0
    fresh = _mk(status="review")
    fresh.finished_at = 1000.0 + queue_manager.REVIEW_TTL
    q.jobs.update({old.id: old, fresh.id: fresh})
    out = q.expire_reviews(now=1000.0 + queue_manager.REVIEW_TTL + 5)
    assert [j.id for j in out] == [old.id]
    assert old.status == "error" and not src.exists() and fresh.status == "review"


def test_steps_logged(tmp_path):
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    j = _mk()
    j.started_at, j.finished_at = 100.0, 160.0
    j.timeline = [("Tải video", 0.0), ("Bóc lời", 12.5), ("Render", 30.0)]
    q.log_steps(j)
    rows = q._db.execute("SELECT step, start, end FROM steps WHERE job_id=?", (j.id,)).fetchall()
    assert rows == [("Tải video", 0.0, 12.5), ("Bóc lời", 12.5, 30.0), ("Render", 30.0, 60.0)]


def test_metrics_fallback_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(queue_manager, "DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(metrics, "_conn", None)
    for _ in range(8):
        metrics.record("tts", "capcut")
    metrics.record("tts", "capcut", ok=False)
    metrics.record("tts", "edge", fallback=True)
    metrics.record("tts", "edge", fallback=True)
    st = metrics.stats(24)["tts"]
    assert st["total"] == 11 and st["fail"] == 1 and st["fallback"] == 2
    assert abs(metrics.fallback_rate(st) - 0.2) < 1e-9
    assert "Đọc giọng" in metrics.summary(24)


def test_health_monitor_alerts_only_on_change(monkeypatch):
    import healthcheck
    sent = []

    async def notify(t):
        sent.append(t)
    state = {"ok": True}

    async def fake_check():
        if not state["ok"]:
            raise RuntimeError("CapCut đổi khoá")
        return "ok"
    monkeypatch.setattr(healthcheck, "CHECKS", [("CapCut TTS", fake_check)])
    monkeypatch.setattr(healthcheck.metrics, "stats", lambda h: {})
    m = healthcheck.Monitor(notify)

    async def go():
        await m.tick(force=True)                     # lần đầu, mọi thứ ổn: không báo
        state["ok"] = False
        await m.tick(force=True)                     # hỏng: báo
        await m.tick(force=True)                     # vẫn hỏng: không báo lại
        state["ok"] = True
        await m.tick(force=True)                     # hồi phục: báo
    asyncio.run(go())
    assert len(sent) == 2 and "hỏng" in sent[0] and "hồi phục" in sent[1]


def test_source_allowlist(monkeypatch):
    import pipeline
    import pytest
    monkeypatch.setattr(config, "ALLOWED_SOURCES", "@kenhcuatoi, Bếp Nhà Mình")
    ok = _mk(source_meta={"uploader": "Bếp nhà mình", "uploader_id": "@bepnha"})
    pipeline.check_source_allowed(ok)
    bad = _mk(source_meta={"uploader": "Kênh Lạ", "uploader_id": "kenhla"})
    with pytest.raises(RuntimeError, match="ALLOWED_SOURCES"):
        pipeline.check_source_allowed(bad)
    monkeypatch.setattr(config, "ALLOWED_SOURCES", "")
    pipeline.check_source_allowed(bad)                    # trống = không giới hạn


def test_job_roundtrip_json():
    j = _mk(source_meta={"uploader": "a"}, cut_at=3.5, attempts=1)
    d = json.loads(json.dumps(queue_manager.asdict(j)))
    assert Job(**d).source_meta == {"uploader": "a"}


def test_ffmpeg_threads(monkeypatch):
    import core_video
    monkeypatch.setattr(config, "FFMPEG_THREADS", 3)
    assert core_video.ffmpeg_threads() == 3
    monkeypatch.setattr(config, "FFMPEG_THREADS", 0)
    monkeypatch.setattr(core_video, "cgroup_cpus", lambda: 1.5)
    assert core_video.ffmpeg_threads() == 2
    monkeypatch.setattr(core_video, "cgroup_cpus", lambda: 1.0)
    assert core_video.ffmpeg_threads() == 1
    args = core_video.encode_args(1280)
    assert args[:2] == ["-threads", "1"]



def test_credit_line_in_post(monkeypatch):
    import pipeline
    import prompt_engine
    monkeypatch.setattr(config, "SOURCE_CREDIT", "1")
    job = _mk(source_meta={"uploader": "Bếp Nhà Mình"})
    credit = pipeline.credit_line(job)
    assert credit == "🎬 Nguồn: Bếp Nhà Mình · 🔊 Giọng đọc AI"
    post = prompt_engine.platform_post("Gà rang", "Ngon lắm", "#a #b", credit)
    assert post.count(credit) == 3                         # có ở cả TikTok, Facebook, YouTube
    assert pipeline.credit_line(_mk(mode="music", source_meta={})) == ""
    monkeypatch.setattr(config, "SOURCE_CREDIT", "0")
    assert pipeline.credit_line(job) == ""


def test_cancelled_and_failed_states_persist_across_restart(tmp_path):
    db = str(tmp_path / "q.db")
    q = JobQueue(db_path=db)
    c = _mk()
    q.put(c)
    c.cancel = True
    q.save(c)
    gone = _mk(url="", local=str(tmp_path / "mat.mp4"))
    gone.status = "queued"
    q.save(gone)
    q2 = JobQueue(db_path=db)
    assert c.id not in {j.id for j in q2.restored} and q2.jobs[c.id].status == "cancelled"
    q3 = JobQueue(db_path=db)                                  # trạng thái mới đã được lưu, không xét lại
    assert q3.jobs[gone.id].status == "error" and q3.jobs[c.id].status == "cancelled"


def test_old_review_job_survives_restart(tmp_path):
    db = str(tmp_path / "q.db")
    q = JobQueue(db_path=db)
    old = _mk(status="review", source="tg", chat_id=9)
    q.jobs[old.id] = old
    q.save(old)
    for _ in range(60):                                        # hơn 50 job mới hơn
        j = _mk(status="done")
        q.save(j)
    q2 = JobQueue(db_path=db)
    assert q2.review_job_for(9) is not None
