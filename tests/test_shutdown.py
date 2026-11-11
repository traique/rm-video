"""Tắt bot (SIGTERM) giữa lúc đang làm job: job phải được chạy lại lần sau, không bị ghi là người dùng huỷ."""
import asyncio
import io
import os

import admin
import jobs
import state
from queue_manager import Job


def _job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.queue, "done", lambda: None)      # job không đi qua hàng đợi thật trong test
    monkeypatch.setattr(jobs.queue, "save", lambda job: None)
    src = tmp_path / "up.mp4"
    src.write_bytes(b"x")
    job = Job(url="", mode="cook", duration=0, voice="v", local=str(src), source="web")
    state.queue.jobs[job.id] = job
    return job, src


def test_shutdown_keeps_job_for_restart(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def slow(job, tmp):
        job.step = "Render (FFmpeg)"
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(jobs, "run_job", slow)
    job, src = _job(tmp_path, monkeypatch)

    async def go():
        w = asyncio.create_task(jobs._run_one(job))
        await started.wait()
        w.cancel()                                    # asyncio.run huỷ worker khi bot nhận SIGTERM
        try:
            await w
        except asyncio.CancelledError:
            return True                               # tín hiệu huỷ phải được truyền tiếp: worker thoát
        return False
    assert asyncio.run(go())
    assert job.status == "processing" and "chạy lại" in job.step
    assert os.path.exists(src)                        # file đầu vào còn để chạy lại


def test_user_cancel_still_cancels(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def slow(job, tmp):
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(jobs, "run_job", slow)
    job, src = _job(tmp_path, monkeypatch)

    async def go():
        w = asyncio.create_task(jobs._run_one(job))
        await started.wait()
        assert "Đang hủy" in jobs.cancel_job(job.id)   # nút Hủy: chỉ huỷ task của job
        await w                                       # worker KHÔNG bị huỷ, chạy tiếp được
    asyncio.run(go())
    assert job.status == "cancelled" and not os.path.exists(src)


def test_console_reads_stdin_in_daemon_thread(monkeypatch):
    monkeypatch.setattr(admin.sys, "stdin", io.StringIO("help\nstatus\n"))

    async def go():
        q = admin._stdin_queue(asyncio.get_running_loop())
        return [await asyncio.wait_for(q.get(), 5) for _ in range(3)]
    assert asyncio.run(go()) == ["help\n", "status\n", ""]
    import threading
    assert all(t.daemon for t in threading.enumerate() if t.name == "console-stdin")
