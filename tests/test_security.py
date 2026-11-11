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


def test_any_domain_still_blocks_private():
    assert url_guard.check_url("https://cdn.example.net/v.mp4", resolve=_resolver("8.8.8.8"), any_domain=True)
    with pytest.raises(url_guard.BlockedURL):
        url_guard.check_url("https://cdn.example.net/v.mp4", resolve=_resolver("10.1.2.3"), any_domain=True)


def test_header_auth_respects_lockout(monkeypatch, tmp_path):
    """Review: X-Auth từng là đường dò mật khẩu không bị khoá; /api/me từng trả authed theo header."""
    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            for _ in range(web_panel.FAIL_PER_IP):
                assert (await c.get("/api/jobs", headers={"X-Auth": "sai"})).status == 401
            assert (await c.get("/api/jobs", headers={"X-Auth": "pw-123"})).status == 429
            me = await (await c.get("/api/me", headers={"X-Auth": "pw-123"})).json()
            assert me["authed"] is False
    asyncio.run(go())


def test_non_ascii_tokens_do_not_crash(monkeypatch, tmp_path):
    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            c.session.cookie_jar.update_cookies({web_panel.SESSION_COOKIE: "9999999999.é"})
            assert (await c.get("/api/jobs")).status == 401
            assert (await c.get("/download/1?t=é")).status == 404
    asyncio.run(go())


# ---- Link chia sẻ Facebook (facebook.com/share/r/..., fb.watch): yt-dlp không nhận, phải mở ra link reel thật ----

def test_fb_short_detect():
    import core_video
    assert core_video.is_fb_short("https://www.facebook.com/share/r/1DZndFhe5U/")
    assert core_video.is_fb_short("https://m.facebook.com/share/v/abc/")
    assert core_video.is_fb_short("https://fb.watch/v5Kx3V8bQm/")
    assert not core_video.is_fb_short("https://www.facebook.com/reel/2140518443517872/")
    assert not core_video.is_fb_short("https://evil.com/share/r/x/")
    assert not core_video.is_fb_short("https://notfacebook.com/share/r/x/")


def test_fb_clean_url():
    import core_video
    assert (core_video._clean_fb_url("https://www.facebook.com/reel/2140518443517872/?rdid=x&share_url=y")
            == "https://www.facebook.com/reel/2140518443517872/")
    assert (core_video._clean_fb_url("https://www.facebook.com/watch/?v=123&mibextid=abc&rdid=q")
            == "https://www.facebook.com/watch/?v=123")
    with pytest.raises(RuntimeError, match="đăng nhập"):
        core_video._clean_fb_url("https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Freel%2F1")


def _redirect_server(routes):
    """Máy chủ HTTP cục bộ: path -> Location (302)."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", routes[self.path])
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_fb_redirect_handler_stops_at_real_link_and_blocks_internal(monkeypatch):
    import urllib.request
    import core_video
    real = url_guard.check_url
    monkeypatch.setattr(url_guard, "check_url",
                        lambda u, resolve=None, any_domain=False: real(u, resolve=_resolver(
                            "10.0.0.5" if "internal" in u else "157.240.1.35"), any_domain=any_domain))
    srv = _redirect_server({"/a": "https://www.facebook.com/reel/42/?rdid=z",
                            "/b": "https://internal.facebook.com/reel/1/",
                            "/c": "https://evil.example/x"})
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with pytest.raises(core_video._ShortResolved) as ok:
            core_video._SHORT_OPENER.open(urllib.request.Request(base + "/a"), timeout=5)
        assert ok.value.url == "https://www.facebook.com/reel/42/?rdid=z"
        with pytest.raises(url_guard.BlockedURL, match="nội bộ"):   # tên miền đúng nhưng trỏ vào IP nội bộ
            core_video._SHORT_OPENER.open(urllib.request.Request(base + "/b"), timeout=5)
        with pytest.raises(url_guard.BlockedURL):          # chuyển hướng ra ngoài nền tảng được phép
            core_video._SHORT_OPENER.open(urllib.request.Request(base + "/c"), timeout=5)
    finally:
        srv.shutdown()


def test_fb_expand_short_url(monkeypatch):
    import io
    import urllib.error
    import core_video
    monkeypatch.setattr(url_guard, "check_url", lambda u, **k: u)

    class Op:
        def __init__(self, do):
            self.do, self.seen = do, []

        def open(self, req, timeout=None):
            self.seen.append(req.get_header("User-agent"))
            return self.do(req)

    def redirect(req):
        raise core_video._ShortResolved("https://www.facebook.com/reel/2140518443517872/?rdid=a&share_url=b")
    op = Op(redirect)
    assert (core_video.expand_short_url("https://www.facebook.com/share/r/1DZndFhe5U/", opener=op)
            == "https://www.facebook.com/reel/2140518443517872/")
    assert op.seen and "Mozilla" in op.seen[0]                # header chuẩn của yt-dlp, không giả danh bot khác

    page = b'<html><link rel="canonical" href="https://www.facebook.com/u/videos/abc/77/?s=1&amp;mibextid=x" /></html>'

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    assert (core_video.expand_short_url("https://fb.watch/xyz/", opener=Op(lambda r: Resp(page)))
            == "https://www.facebook.com/u/videos/abc/77/")

    def gone(req):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    with pytest.raises(RuntimeError, match="404"):
        core_video.expand_short_url("https://www.facebook.com/share/v/zzz/", opener=Op(gone))
    # link thường không bị đụng tới, không gọi mạng
    assert core_video.expand_short_url("https://www.tiktok.com/@a/video/1", opener=Op(gone)) == \
        "https://www.tiktok.com/@a/video/1"


def test_download_entry_points_resolve_share_links(monkeypatch, tmp_path):
    import core_video
    seen = []
    monkeypatch.setattr(core_video, "resolve_url", lambda u: seen.append(u) or "https://www.facebook.com/reel/9/")

    def fake_video(url, tmpdir, max_duration):
        seen.append(("yt", url))
        raise RuntimeError("stop")
    monkeypatch.setattr(core_video, "_ytdlp_video_sync", fake_video)
    with pytest.raises(RuntimeError, match="stop"):
        core_video._download_sync("https://www.facebook.com/share/r/x/", str(tmp_path))
    assert seen == ["https://www.facebook.com/share/r/x/", ("yt", "https://www.facebook.com/reel/9/")]


def test_fb_title_stats_removed():
    import core_video
    assert core_video.clean_title("2.6M views · 29K reactions | #duynauan #amthuc") == "#duynauan #amthuc"
    assert core_video.clean_title("136K views · 1.6K reactions | Cách làm đùi gà") == "Cách làm đùi gà"
    assert core_video.clean_title("Bò lá lốt 10 views") == "Bò lá lốt 10 views"


def test_unsupported_url_message_is_friendly():
    import core_video
    e = core_video._friendly(Exception("ERROR: No suitable extractor found for URL https://www.facebook.com/x/"))
    assert "Sao chép liên kết" in str(e)
