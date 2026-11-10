"""Giai đoạn 0 — bảo mật: chặn SSRF, phiên đăng nhập web, giới hạn thử mật khẩu, CSRF, khoá API."""
import asyncio
import socket

import pytest

import config
import url_guard
import web_panel


def _resolver(ip):
    return lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@a/video/1", "https://vt.tiktok.com/ZSabc/", "https://youtu.be/xyz",
    "https://m.youtube.com/watch?v=1", "https://www.facebook.com/reel/1", "https://fb.watch/abc",
    "https://www.instagram.com/reel/abc", "https://soundcloud.com/a/b",
])
def test_url_allowed(url):
    assert url_guard.check_url(url, resolve=_resolver("142.250.1.1")) == url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://[::1]/", "http://169.254.169.254/latest/meta-data",
    "https://evil.com/?u=tiktok.com", "https://tiktok.com.evil.com/", "https://eviltiktok.com/",
    "file:///etc/passwd", "ftp://youtube.com/", "https://user:pw@youtube.com/", "https://youtube.com:8080/",
    "not a url", "",
])
def test_url_blocked(url):
    with pytest.raises(url_guard.BlockedURL):
        url_guard.check_url(url, resolve=_resolver("142.250.1.1"))


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.2", "127.0.0.1", "169.254.169.254", "::1",
                                "::ffff:10.0.0.1", "100.64.0.1", "0.0.0.0"])
def test_dns_to_private_blocked(ip):
    with pytest.raises(url_guard.BlockedURL):
        url_guard.check_url("https://www.youtube.com/watch?v=1", resolve=_resolver(ip))


def test_ytdlp_never_uses_generic():
    import core_video
    opts = {}
    core_video._add_cookies(opts, "/tmp")
    assert opts["allowed_extractors"] == ["default", "-generic"]


def test_session_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setattr(config, "WEB_PASSWORD", "mat-khau-1")
    tok = web_panel.make_session(now=1000)
    assert web_panel.valid_session(tok, now=1001)
    assert not web_panel.valid_session(tok, now=1000 + web_panel.SESSION_TTL + 1)      # hết hạn
    exp, sig = tok.split(".")
    assert not web_panel.valid_session(f"{int(exp) + 999}.{sig}", now=1001)             # sửa hạn
    assert not web_panel.valid_session("rác", now=1001)
    monkeypatch.setattr(config, "WEB_PASSWORD", "mat-khau-2")                           # đổi mật khẩu
    assert not web_panel.valid_session(tok, now=1001)


def test_login_rate_limit(monkeypatch):
    monkeypatch.setattr(web_panel, "_fails", {})
    for _ in range(web_panel.FAIL_PER_IP):
        assert web_panel.login_locked("1.2.3.4", now=100) == 0
        web_panel.record_fail("1.2.3.4", now=100)
    assert web_panel.login_locked("1.2.3.4", now=101) > 0
    assert web_panel.login_locked("5.6.7.8", now=101) == 0          # IP khác vẫn thử được
    assert web_panel.login_locked("1.2.3.4", now=100 + web_panel.FAIL_WINDOW + 1) == 0
    for i in range(web_panel.FAIL_GLOBAL):                           # IP giả xoay vòng vẫn chạm trần toàn cục
        web_panel.record_fail(f"9.9.9.{i}", now=500)
    assert web_panel.login_locked("8.8.8.8", now=501) > 0


def test_allowed_ids(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USERS", "123, -100456")
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    assert config.allowed_ids() == {123, -100456}
    assert config.admin_chat_id() == 123
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "777")
    assert config.admin_chat_id() == 777


def _client(monkeypatch, tmp_path, password):
    from aiohttp.test_utils import TestClient, TestServer
    from queue_manager import JobQueue
    monkeypatch.setattr(config, "WEB_PASSWORD", password)
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setattr(web_panel, "_fails", {})
    app = web_panel.build_app(JobQueue(db_path=str(tmp_path / "q.db")), {"cancel": lambda i: "ok", "speed": lambda m: 6.0})
    return TestClient(TestServer(app))


def test_web_locked_without_password(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path, "") as c:
            assert (await c.get("/api/jobs")).status == 403
            assert (await c.post("/api/jobs", json={"url": "https://youtu.be/x"})).status == 403
            assert (await c.get("/api/settings")).status == 403
            h = await (await c.get("/health")).json()
            assert set(h) == {"ok", "pending"}                  # không lộ POT_SERVER, ổ đĩa...
    asyncio.run(go())


def test_web_login_flow(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/jobs")).status == 401
            assert (await c.get("/api/jobs?auth=pw-123")).status == 401       # không còn nhận mật khẩu qua URL
            assert (await c.post("/api/login", json={"password": "sai"})).status == 401
            r = await c.post("/api/login", json={"password": "pw-123"})
            assert r.status == 200
            cookie = r.cookies[web_panel.SESSION_COOKIE]
            assert cookie["httponly"] and cookie["samesite"] == "Strict"
            assert (await c.get("/api/jobs")).status == 200
            assert (await c.get("/api/me")).status == 200
            # POST từ trang khác bị chặn dù trình duyệt có cookie (CSRF)
            r = await c.post("/api/jobs/1/cancel", headers={"Origin": "https://evil.example"})
            assert r.status == 403
            r = await c.post("/api/jobs", json={"url": "http://169.254.169.254/"})
            assert r.status == 400
    asyncio.run(go())


def test_web_header_auth_for_scripts(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/jobs", headers={"X-Auth": "pw-123"})).status == 200
            assert (await c.get("/api/jobs", headers={"X-Auth": "pw-12"})).status == 401
    asyncio.run(go())


def test_login_lockout(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            for _ in range(web_panel.FAIL_PER_IP):
                await c.post("/api/login", json={"password": "sai"})
            r = await c.post("/api/login", json={"password": "pw-123"})    # đúng mật khẩu cũng phải chờ
            assert r.status == 429
    asyncio.run(go())
