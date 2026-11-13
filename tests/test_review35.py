"""Bản 35 — rà soát production: giới hạn body web, thu hồi phiên, lỗi vào 400 thay vì 500, logo quá cỡ cho Kaggle,
SSRF NAT64/ký tự điều khiển, tệp trạng thái JSON tự hồi phục, huỷ phiên Kaggle khi quá hạn hoặc bot tắt."""
import asyncio
import os
import socket
import stat
import tempfile

import pytest

import cache
import config
import jobs
import kaggle_worker
import state
import storage
import url_guard
import web_panel
from queue_manager import Job


def _resolver(ip):
    return lambda host, port: [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _client(monkeypatch, tmp_path, password="pw-123456789"):
    from aiohttp.test_utils import TestClient, TestServer
    from queue_manager import JobQueue
    monkeypatch.setattr(config, "WEB_PASSWORD", password)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setattr(web_panel, "_fails", {})
    app = web_panel.build_app(JobQueue(db_path=str(tmp_path / "q.db")), {"cancel": lambda i: "ok", "speed": lambda m: 6.0})
    return TestClient(TestServer(app))


async def _login(c, password="pw-123456789"):
    r = await c.post("/api/login", json={"password": password})
    assert r.status == 200
    return r.cookies[web_panel.SESSION_COOKIE].value


def test_body_limits_json_only_uploads_stream(monkeypatch, tmp_path):
    """Trần body 1MB chỉ áp cho JSON; /api/login không đọc body lớn; upload multipart 3MB vẫn đi qua (đọc từng khúc)."""
    import aiohttp

    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            r = await c.post("/api/login", data=b"x" * 5000, headers={"Content-Type": "application/json"})
            assert r.status == 413
            await _login(c)
            r = await c.post("/api/jobs", data=b'{"url": "' + b"a" * (2 << 20) + b'"}',
                             headers={"Content-Type": "application/json"})
            assert r.status == 413
            form = aiohttp.FormData()
            form.add_field("file", b"\0" * (3 << 20), filename="v.mp4", content_type="video/mp4")
            form.add_field("mode", "cook")
            r = await c.post("/api/upload", data=form)
            assert r.status == 200, await r.text()
            files = os.listdir(tmp_path / "up")
            assert len(files) == 1 and os.path.getsize(tmp_path / "up" / files[0]) == 3 << 20
    asyncio.run(go())


def test_logout_revokes_sessions_only_when_authenticated(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            tok = await _login(c)
            assert web_panel.valid_session(tok)
            c.session.cookie_jar.clear()
            assert (await c.post("/api/logout")).status == 200          # chưa đăng nhập: không được thu hồi phiên của người khác
            assert web_panel.valid_session(tok)
            assert (await c.get("/api/jobs", headers={"Cookie": f"{web_panel.SESSION_COOKIE}={tok}"})).status == 200
            await _login(c)
            assert (await c.post("/api/logout")).status == 200
            assert not web_panel.valid_session(tok)                      # cookie bị sao chép cũng hết hiệu lực
            assert (await c.get("/api/jobs", headers={"Cookie": f"{web_panel.SESSION_COOKIE}={tok}"})).status == 401
            tok2 = await _login(c)                                       # đăng nhập lại vẫn bình thường
            assert web_panel.valid_session(tok2) and (await c.get("/api/jobs")).status == 200
    asyncio.run(go())


def test_bad_input_is_4xx_not_500(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            await _login(c)
            assert (await c.post("/api/jobs/abc/cancel")).status == 404
            assert (await c.post("/api/jobs/abc/approve", json={})).status == 404
            assert (await c.post("/api/jobs", data=b"not json", headers={"Content-Type": "application/json"})).status == 400
            assert (await c.post("/api/jobs", json=[1, 2])).status == 400
            assert (await c.post("/api/settings", json="x")).status == 400
            assert (await c.post("/api/bgm/link", json=[])).status == 400
            assert (await c.post("/api/upload", data={"url": "x"})).status == 400          # urlencoded, không phải multipart
            assert (await c.post("/api/logo", data=b"x", headers={"Content-Type": "image/png"})).status == 400
    asyncio.run(go())


def test_logo_capped_for_kaggle_and_single_video_part(monkeypatch, tmp_path):
    import aiohttp
    import core_video
    monkeypatch.setattr(core_video, "LOGO_PATH", str(tmp_path / "logo.png"))

    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            await _login(c)
            form = aiohttp.FormData()
            form.add_field("logo", b"\x89PNG\r\n\x1a\n" + b"\0" * config.LOGO_MAX_BYTES, filename="l.png")
            r = await c.post("/api/logo", data=form)
            assert r.status == 400 and "Kaggle" in await r.text() and not os.path.exists(tmp_path / "logo.png")
            form = aiohttp.FormData()
            form.add_field("logo", b"\x89PNG\r\n\x1a\n" + b"\0" * 100, filename="l.png")
            assert (await c.post("/api/logo", data=form)).status == 200 and os.path.exists(tmp_path / "logo.png")
            form = aiohttp.FormData()
            form.add_field("file", b"\0" * 1000, filename="a.mp4")
            form.add_field("file", b"\0" * 1000, filename="b.mp4")
            r = await c.post("/api/upload", data=form)
            assert r.status == 400 and "một file video" in await r.text()
            assert os.listdir(tmp_path / "up") == []                     # file thứ nhất không bị bỏ rơi trên đĩa
    asyncio.run(go())


def test_security_headers_on_pages_and_errors(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            for r in (await c.get("/login"), await c.get("/api/jobs")):
                assert r.headers["X-Frame-Options"] == "SAMEORIGIN" and r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.status == 401
    asyncio.run(go())


def test_url_guard_nat64_and_control_characters():
    with pytest.raises(url_guard.BlockedURL):                        # 64:ff9b::7f00:1 = 127.0.0.1 qua NAT64
        url_guard.check_url("https://www.youtube.com/watch?v=1", resolve=_resolver("64:ff9b::7f00:1"))
    with pytest.raises(url_guard.BlockedURL):
        url_guard.check_url("https://www.youtube.com/watch?v=1", resolve=_resolver("64:ff9b::a00:1"))
    assert url_guard.check_url("https://www.youtube.com/watch?v=1", resolve=_resolver("64:ff9b::8efa:101"))
    for bad in ("https://youtube.com\t/", "https://youtube.com/x\nhttps://evil.com", "https://youtube.com/\x00"):
        with pytest.raises(url_guard.BlockedURL, match="ký tự lạ"):
            url_guard.check_url(bad, resolve=_resolver("142.250.1.1"))


def test_settings_file_created_0600_before_secrets_written(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    modes = []
    real = os.replace

    def spy(a, b):
        modes.append(stat.S_IMODE(os.stat(a).st_mode))
        real(a, b)
    monkeypatch.setattr(config.os, "replace", spy)
    old = os.umask(0o022)
    try:
        config.save_settings({"BRAND_NAME": "Bếp"})
    finally:
        os.umask(old)
    assert modes == [0o600] and stat.S_IMODE(os.stat(tmp_path / "settings.json").st_mode) == 0o600
    assert not os.path.exists(tmp_path / "settings.json.tmp")


def test_json_state_files_self_heal(monkeypatch, tmp_path):
    p = str(tmp_path / "s.json")
    open(p, "w").write("{garbage")
    assert cache.read_json_file(p, {}) == {}
    cache.write_json_file(p, {"a": 1})
    assert cache.read_json_file(p, None) == {"a": 1} and os.listdir(tmp_path) == ["s.json"]
    # speed.json hỏng: học tiếp được (trước đây nuốt lỗi, ETA kẹt ở 6.0 mãi)
    monkeypatch.setattr(jobs, "SPEED_FILE", str(tmp_path / "speed.json"))
    open(jobs.SPEED_FILE, "w").write("{garbage")
    assert jobs.speed_ratio("") == 6.0
    job = Job(url="", mode="cook", duration=0, voice="v", source="web")
    job.status, job.out_dur, job.started_at, job.finished_at = "done", 10.0, 100.0, 150.0
    jobs._learn_speed(job)
    assert jobs.speed_ratio("") == 5.0


def test_stt_cache_survives_corruption(monkeypatch, tmp_path):
    import transcribe
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"audio-bytes")
    monkeypatch.setattr(transcribe, "_CACHE", str(tmp_path / "stt_cache.json"))
    open(transcribe._CACHE, "w").write("{garbage")
    calls = []

    async def fake(path, prompt=""):
        calls.append(path)
        return "xin chào cả nhà", [(0.0, 1.5, "xin chào cả nhà")]
    monkeypatch.setattr(transcribe, "_transcribe_uncached", fake)
    text, segs = asyncio.run(transcribe.transcribe_segments(str(audio)))
    assert "xin chào" in text and len(calls) == 1
    asyncio.run(transcribe.transcribe_segments(str(audio)))
    assert len(calls) == 1                                           # lần hai lấy từ cache: tệp đã được ghi lại hợp lệ
    assert cache.read_json_file(transcribe._CACHE, None)[transcribe._cache_key(str(audio))]["v"] == transcribe.CACHE_V


def test_sweep_tmp_and_clean_keeps_running_job_files(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "rmv_abc").mkdir()
    (tmp_path / "other").mkdir()
    assert storage.sweep_tmp() == 1 and (tmp_path / "other").exists() and not (tmp_path / "rmv_abc").exists()
    work, pip = tmp_path / "work", tmp_path / "pip"
    pruned = []
    monkeypatch.setattr(storage, "WORK_DIR", str(work))
    monkeypatch.setattr(storage, "PIP_CACHE", str(pip))
    monkeypatch.setattr(storage, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(cache, "prune", lambda max_mb=None, keep=frozenset(): pruned.append(max_mb) or 0)
    work.mkdir()
    pip.mkdir()
    storage.clean(lambda s: None, keep_work=True)
    assert work.exists() and not pip.exists() and pruned == []
    storage.clean(lambda s: None)
    assert not work.exists() and pruned == [0]


def test_kaggle_spec_skips_oversized_logo(monkeypatch, tmp_path):
    monkeypatch.setattr(kaggle_worker, "BASE", str(tmp_path))
    monkeypatch.setattr(config, "BRAND_LOGO", "1")
    (tmp_path / "assets").mkdir()
    logo = tmp_path / "assets" / "logo.png"
    logo.write_bytes(b"\0" * (config.LOGO_MAX_BYTES + 1))
    assert "logo_b64" not in kaggle_worker._common({})
    logo.write_bytes(b"\0" * 100)
    assert len(kaggle_worker._common({})["logo_b64"]) > 100


def _job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.queue, "done", lambda: None)
    monkeypatch.setattr(jobs.queue, "save", lambda job: None)
    src = tmp_path / "up.mp4"
    src.write_bytes(b"x")
    job = Job(url="", mode="cook", duration=0, voice="v", local=str(src), source="web")
    monkeypatch.setitem(state.queue.jobs, job.id, job)
    return job


def test_over_budget_and_shutdown_abort_kaggle_session(tmp_path, monkeypatch):
    aborts = []
    monkeypatch.setattr(kaggle_worker, "abort", lambda: aborts.append(1))
    monkeypatch.setattr(jobs, "job_budget", lambda job: 0)
    started = asyncio.Event()

    async def slow(job, tmp):
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(jobs, "run_job", slow)
    job = _job(tmp_path, monkeypatch)
    asyncio.run(jobs._run_one(job))
    assert job.status == "error" and "Quá thời gian" in job.error and aborts == [1]

    monkeypatch.setattr(jobs, "job_budget", lambda job: 3600)
    started.clear()
    job2 = _job(tmp_path, monkeypatch)

    async def go():
        w = asyncio.create_task(jobs._run_one(job2))
        await started.wait()
        w.cancel()                                      # SIGTERM: worker bị huỷ -> phiên Kaggle cũng phải dừng
        with pytest.raises(asyncio.CancelledError):
            await w
    asyncio.run(go())
    assert job2.status == "processing" and aborts == [1, 1]


def test_tiktok_listing_never_uses_generic_extractor(monkeypatch):
    import yt_dlp
    import autopost
    seen = {}

    class Fake:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"entries": [{"id": "7300000000000000001", "url": "https://www.tiktok.com/@quetrai/video/7300000000000000001",
                                 "title": "Cá kho", "duration": 58}]}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Fake)
    monkeypatch.setattr(url_guard, "check_url", lambda u, **k: u)
    monkeypatch.setattr(config, "YTDLP_PROXY", "socks5://127.0.0.1:1080")
    items, _ = autopost.list_tiktok_sync("https://www.tiktok.com/@quetrai")
    assert len(items) == 1
    assert seen["allowed_extractors"] == ["default", "-generic"] and seen["proxy"] == "socks5://127.0.0.1:1080"
