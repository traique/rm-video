"""Phase 1: tiến trình con có giới hạn, huỷ theo từng job (rmvideo/media/proc.py), dừng yt-dlp khi huỷ (C8),
SQLite WAL cho jobs.db (C10)."""
import asyncio
import os
import sqlite3
import subprocess
import threading
import time

import pytest
import yt_dlp

import core_video
import metrics
from queue_manager import JobQueue
from rmvideo.app import db
from rmvideo.media import proc


def _alive(tag: str) -> list:
    ps = subprocess.run(["ps", "-eo", "stat,args"], capture_output=True, text=True).stdout.splitlines()
    return [ln for ln in ps if tag in ln and not ln.strip().startswith("Z") and "ps -eo" not in ln]


def test_run_reports_errors_caps_stderr_and_kills_whole_group():
    async def go():
        r = await proc.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=0.2", "-f", "null", "-"], timeout=20)
        assert r.code == 0 and r.stderr == ""
        with pytest.raises(proc.ProcError) as e:
            await proc.run(["ffmpeg", "-i", "/khong/co.mp4", "-f", "null", "-"], timeout=20)
        assert e.value.code != 0 and "co.mp4" in e.value.stderr and str(e.value).startswith("ffmpeg lỗi: ")
        big = await proc.run(["sh", "-c", "head -c 200000 /dev/zero | tr '\\0' x >&2"], timeout=20, stderr_keep=1000)
        assert len(big.stderr) == 1000                               # log dài không phình RAM
        t = time.time()
        with pytest.raises(TimeoutError):                            # sh sinh sleep (cháu): quá giờ giết cả cháu
            await proc.run(["sh", "-c", "sleep 41 & wait"], timeout=0.5)
        assert time.time() - t < 5 and proc.running() == 0
    asyncio.run(go())
    time.sleep(0.2)
    assert not _alive("sleep 41")


def test_cancel_kills_only_the_cancelled_job():
    """Huỷ job A không giết tiến trình của job B hay của request web (không thuộc job nào) — C9."""
    async def go():
        async def job(owner, tag):
            with proc.owned_by(owner):
                return await proc.run(["sh", "-c", f"sleep {tag}"], timeout=30, check=False)
        a = asyncio.create_task(job("A", 42))
        b = asyncio.create_task(job("B", 0.6))
        web = asyncio.create_task(proc.run(["sh", "-c", "sleep 0.6"], timeout=30, check=False))
        await asyncio.sleep(0.3)
        assert proc.running("A") == 1 and proc.running("B") == 1
        assert proc.kill_owner("A") == 1
        ra = await a
        assert ra.code != 0                                          # A bị giết
        assert (await b).code == 0 and (await web).code == 0         # B và web chạy trọn
    asyncio.run(go())


def test_run_sync_in_thread_is_killable_and_mirrors_subprocess_run():
    r = proc.run_sync(["sh", "-c", "printf ok; printf err >&2; exit 3"], text=True)
    assert (r.returncode, r.stdout, r.stderr) == (3, "ok", "err")
    with pytest.raises(subprocess.CalledProcessError):
        proc.run_sync(["sh", "-c", "exit 2"], check=True)
    assert proc.run_sync(["cat"], input=b"abc").stdout == b"abc"
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run_sync(["sh", "-c", "sleep 43"], timeout=0.3)

    async def go():
        with proc.owned_by("job-7"):
            t0 = time.time()
            fut = asyncio.ensure_future(asyncio.to_thread(proc.run_sync, ["sh", "-c", "sleep 44 & wait"]))
            await asyncio.sleep(0.4)
            assert proc.running("job-7") == 1                       # luồng phụ mang mã job qua contextvar
            proc.kill_owner("job-7")
            r = await fut
            assert r.returncode != 0 and time.time() - t0 < 5
    asyncio.run(go())
    time.sleep(0.2)
    assert not _alive("sleep 43") and not _alive("sleep 44")


def test_popen_and_spawn_unregister_and_kill_on_exit():
    with proc.popen(["sh", "-c", "sleep 45"], stdout=subprocess.DEVNULL) as p:
        assert proc.running() == 1
    assert p.poll() is not None and proc.running() == 0

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            async with proc.spawn(["sh", "-c", "sleep 46"], stdout=asyncio.subprocess.DEVNULL) as sp:
                await asyncio.wait_for(sp.wait(), 0.3)
        assert sp.returncode is not None and proc.running() == 0
    asyncio.run(go())


def test_ytdlp_hook_stops_download_of_cancelled_job():
    hook = core_video._stop_if_cancelled
    opts = {}
    core_video._add_cookies(opts, "/tmp")
    assert hook in opts["progress_hooks"]
    hook({"status": "downloading"})                                  # không thuộc job nào: không dừng
    with proc.owned_by("j"):
        hook({"status": "downloading"})
        done = threading.Event()
        err = []

        def worker():                                                # yt-dlp chạy trong luồng phụ (asyncio.to_thread)
            try:
                hook({"status": "downloading"})
            except yt_dlp.utils.DownloadCancelled as e:
                err.append(e)
            done.set()
        proc.kill_owner("j")
        import contextvars
        threading.Thread(target=contextvars.copy_context().run, args=(worker,)).start()
        assert done.wait(5) and err


def test_jobs_db_uses_wal_and_metrics_share_it(tmp_path, monkeypatch):
    path = str(tmp_path / "jobs.db")
    q = JobQueue(db_path=path)
    assert q._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert q._db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    monkeypatch.setenv("JOBS_DB", path)
    monkeypatch.setattr(metrics, "_conn", None)
    metrics.record("asr", "groq")
    assert sqlite3.connect(path).execute("SELECT provider FROM events").fetchall() == [("groq",)]
    assert db.jobs_db_path() == path and os.path.exists(path + "-wal")


def test_ytdlp_options_one_place_and_cookies_private(tmp_path, monkeypatch):
    import config
    import url_guard
    from rmvideo.media import fetch
    monkeypatch.setattr(config, "YTDLP_PROXY", "socks5://1.2.3.4:1080")
    monkeypatch.setattr(config, "YTDLP_COOKIES", "# Netscape HTTP Cookie File\n")
    base = fetch.ytdlp_base_options({"quiet": True})
    assert base["allowed_extractors"] == url_guard.YTDLP_EXTRACTORS and base["proxy"] == "socks5://1.2.3.4:1080"
    full = {"quiet": True}
    fetch._add_cookies(full, str(tmp_path))
    assert full["allowed_extractors"] == url_guard.YTDLP_EXTRACTORS and fetch._stop_if_cancelled in full["progress_hooks"]
    assert oct(os.stat(full["cookiefile"]).st_mode)[-3:] == "600"           # cookie phiên đăng nhập: 0600
