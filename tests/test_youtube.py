"""▶️ YouTube Shorts (1.2.0): chữ đăng, thân videos.insert, kết nối OAuth, tải lên resumable, hàng chờ + lỗi, web."""
import asyncio
import json
import os

import pytest
from aiohttp import web

import config
import prompt_engine
from rmvideo.app import youtube

NOW = 1_800_000_000.0


@pytest.fixture
def yt(monkeypatch, tmp_path):
    """Cài đặt riêng (settings.json tạm), trạng thái riêng, thông báo admin ghi lại; xong trả cài đặt như cũ."""
    import state
    orig = config.SETTINGS_FILE
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(youtube, "STATE_FILE", str(tmp_path / "youtube.json"))
    monkeypatch.setattr(youtube, "_ACCESS", {})
    monkeypatch.setattr(youtube, "_PENDING", {})
    config.save_settings({"YT_CLIENT_ID": "123-abc.apps.googleusercontent.com", "YT_CLIENT_SECRET": "sec",
                          "PUBLIC_URL": "https://bot.example"})
    sent: list = []

    async def notify(msg, *a, **k):
        sent.append(msg)
    monkeypatch.setattr(state, "notify_admin", notify)
    yield sent
    config.SETTINGS_FILE = orig
    config._apply()


def _fake_google(calls: list, flags: dict) -> web.Application:
    app = web.Application(client_max_size=50 << 20)

    async def token(req):
        f = await req.post()
        calls.append(("token", f["grant_type"], f.get("refresh_token") or f.get("code")))
        if f["grant_type"] == "refresh_token":
            if f["refresh_token"] == "bad":
                return web.json_response({"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
                                         status=400)
            return web.json_response({"access_token": "AT-" + f["refresh_token"], "expires_in": 3599})
        if f["code"] != "c1" or f["redirect_uri"] != "https://bot.example/youtube/callback":
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "AT1", "refresh_token": "RT-new", "expires_in": 3599,
                                  "scope": " ".join(youtube.SCOPES)})

    async def channels(req):
        calls.append(("channels", req.headers["Authorization"], req.query["mine"]))
        return web.json_response({"items": [{"id": flags.get("chan", "UC1"), "snippet": {"title": "Bếp Đong Đủ"}}]})

    async def init(req):
        body = await req.json()
        calls.append(("init", req.query["uploadType"], req.query["part"], req.headers["X-Upload-Content-Length"], body))
        if flags.get("quota"):
            return web.json_response({"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}},
                                     status=403)
        if flags.get("down"):
            return web.json_response({"error": {"code": 503, "message": "backend", "errors": [{"reason": "backendError"}]}},
                                     status=503)
        return web.json_response({}, headers={"Location": str(req.url.with_path("/up/session/1").with_query({}))})

    async def put(req):
        data = await req.read()
        calls.append(("put", req.headers["Authorization"], req.headers["Content-Type"], len(data)))
        return web.json_response({"id": "VID1", "status": {"uploadStatus": "uploaded"}})

    async def videos(req):
        calls.append(("views", req.query["id"], req.query["part"]))
        return web.json_response({"items": [{"statistics": {"viewCount": "321"}}]})

    async def revoke(req):
        calls.append(("revoke", (await req.post())["token"]))
        return web.json_response({})
    app.router.add_post("/token", token)
    app.router.add_get("/yt/channels", channels)
    app.router.add_get("/yt/videos", videos)
    app.router.add_post("/up/videos", init)
    app.router.add_put("/up/session/1", put)
    app.router.add_post("/revoke", revoke)
    return app


async def _with_google(monkeypatch, coro_fn, flags=None):
    calls: list = []
    runner = web.AppRunner(_fake_google(calls, flags if flags is not None else {}))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(youtube, "TOKEN_URL", base + "/token")
    monkeypatch.setattr(youtube, "REVOKE_URL", base + "/revoke")
    monkeypatch.setattr(youtube, "API", base + "/yt")
    monkeypatch.setattr(youtube, "UPLOAD", base + "/up/videos")
    try:
        return await coro_fn(), calls
    finally:
        await runner.cleanup()


def test_texts_from_platform_post_and_limits():
    post = prompt_engine.platform_post("Canh chua cá lóc miền Tây", "Chua chua ngọt ngọt, nấu 20 phút là xong.",
                                       "canhchua, monngon, bepdongdu", "Nguồn: @bepnha · giọng đọc AI")
    title, desc = youtube.texts(post, "FB", "fb desc")
    assert title == "Canh chua cá lóc miền Tây #Shorts"
    assert desc.startswith("Chua chua ngọt ngọt") and "#canhchua" in desc and "Nguồn: @bepnha" in desc
    assert "▶" not in desc
    # không có phần YouTube (job cũ / job ảnh): dùng chữ Fanpage, thêm #Shorts
    assert youtube.texts("Món ngon", "Món ngon", "Mô tả") == ("Món ngon #Shorts", "Mô tả")
    long = "Cách nấu " + "rất ngon " * 20 + "#Shorts"
    t = youtube.clean_title(long)
    assert len(t) <= youtube.TITLE_MAX and t.endswith("… #Shorts")
    assert youtube.clean_title("Bánh <ngon> 1 > 0") == "Bánh ‹ngon› 1 › 0"            # YouTube từ chối < >
    d = youtube.clean_desc("ạ" * 3000)                                                 # 3 byte / chữ
    assert len(d.encode()) <= youtube.DESC_MAX_BYTES and d == "ạ" * (youtube.DESC_MAX_BYTES // 3)


def test_video_body_schedule_and_kids():
    far = youtube.video_body("cook", "T", "D", NOW + 3600, NOW)
    assert far["status"] == {"privacyStatus": "private", "publishAt": "2027-01-15T09:00:00Z",
                             "selfDeclaredMadeForKids": False, "embeddable": True}
    assert far["snippet"]["categoryId"] == "26" and far["snippet"]["defaultLanguage"] == "vi"
    near = youtube.video_body("kids", "T", "D", NOW + 300, NOW)                         # sắp tới giờ: công khai ngay
    assert near["status"]["privacyStatus"] == "public" and "publishAt" not in near["status"]
    assert near["status"]["selfDeclaredMadeForKids"] is True and near["snippet"]["categoryId"] == "1"
    assert youtube.video_body("cook", "T", "D", None, NOW)["status"]["privacyStatus"] == "public"


def test_connect_flow_saves_token_per_channel(monkeypatch, yt):
    import urllib.parse
    url = youtube.start("kids", now=NOW)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith(youtube.AUTH_URL) and q["redirect_uri"] == "https://bot.example/youtube/callback"
    assert q["access_type"] == "offline" and "consent" in q["prompt"] and "select_account" in q["prompt"]
    assert set(q["scope"].split()) == set(youtube.SCOPES) and q["client_id"].endswith(".apps.googleusercontent.com")
    with pytest.raises(youtube.YTError):                        # state lạ / dùng lại: từ chối
        asyncio.run(youtube.finish("khong-phai", "c1", now=NOW))
    (ch, title, warn), calls = asyncio.run(_with_google(monkeypatch, lambda: youtube.finish(q["state"], "c1", now=NOW)))
    assert (ch, title, warn) == ("kids", "Bếp Đong Đủ", "")
    assert config.KIDS_YT_REFRESH_TOKEN == "RT-new" and config.YT_REFRESH_TOKEN == ""   # chỉ kênh bé
    assert ("channels", "Bearer AT1", "true") in calls
    assert youtube.load_state()["channels"]["kids"]["id"] == "UC1"
    with pytest.raises(youtube.YTError):
        asyncio.run(youtube.finish(q["state"], "c1", now=NOW))                           # state dùng một lần
    # kênh bếp chọn nhầm cùng kênh YouTube: kết nối được nhưng có cảnh báo
    q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(youtube.start("cook", now=NOW)).query))
    (ch, _t, warn), _c = asyncio.run(_with_google(monkeypatch, lambda: youtube.finish(q2["state"], "c1", now=NOW)))
    assert ch == "cook" and "kênh truyện bé" in warn
    with pytest.raises(youtube.YTError):                                                  # state quá hạn
        q3 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(youtube.start("cook", now=NOW)).query))
        asyncio.run(youtube.finish(q3["state"], "c1", now=NOW + youtube.CONNECT_TTL + 1))
    config.save_settings({"PUBLIC_URL": "-"})
    with pytest.raises(youtube.YTError, match="PUBLIC_URL"):
        youtube.start("cook")


def test_upload_resumable_and_views(monkeypatch, yt, tmp_path):
    config.save_settings({"YT_REFRESH_TOKEN": "RT1"})
    vid = tmp_path / "v.mp4"
    vid.write_bytes(os.urandom(200_000))

    async def go():
        a = await youtube.upload("cook", str(vid), "Canh chua #Shorts", "Mô tả", NOW + 7200, now=NOW)
        async with youtube._session() as s:
            v = await youtube.views("cook", "VID1", s)
        return a, v
    (a, v), calls = asyncio.run(_with_google(monkeypatch, go))
    assert a == "VID1" and v == 321
    init = next(c for c in calls if c[0] == "init")
    assert init[1:4] == ("resumable", "snippet,status", "200000")
    assert init[4]["status"]["privacyStatus"] == "private" and init[4]["snippet"]["title"] == "Canh chua #Shorts"
    assert ("put", "Bearer AT-RT1", "video/mp4", 200_000) in calls
    assert [c for c in calls if c[0] == "token"] == [("token", "refresh_token", "RT1")]      # access token dùng lại


def test_queue_tick_upload_errors_and_views(monkeypatch, yt, tmp_path):
    sent = yt
    W = NOW + 2 * 86400                                   # giờ hẹn Facebook: 2 ngày nữa
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 1000)

    class Job:
        id = 7
        post = prompt_engine.platform_post("Gà chiên nước mắm", "Giòn rụm", "gachien")
    assert youtube.after_facebook("cook", Job, str(vid), "t", "d", W) is False    # chưa kết nối: bỏ qua
    config.save_settings({"YT_REFRESH_TOKEN": "RT1"})
    assert youtube.after_facebook("cook", Job, str(vid), "t", "d", W) is True
    assert youtube.after_facebook("cook", Job, str(vid), "t", "d", W) is False    # một lần mỗi job
    it = youtube.load_state()["items"][0]
    assert it["title"] == "Gà chiên nước mắm #Shorts" and it["status"] == "pending"
    youtube._patch("cook:7", created=NOW)                  # after_facebook ghi giờ thật; test chạy trên mốc NOW

    # hết hạn mức: thử lại sau 6 giờ, không tính là lỗi
    flags = {"quota": True}
    msg, _ = asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=NOW), flags))
    it = youtube.load_state()["items"][0]
    assert "hạn mức" in msg and it["next_try"] == NOW + youtube.LATER and it["tries"] == 0
    assert asyncio.run(youtube.tick(now=NOW + 60)) == ""                                   # chưa tới giờ thử lại
    # lỗi máy chủ: thử lại 5 phút sau, tính 1 lần
    flags = {"down": True}
    asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=NOW + youtube.LATER), flags))
    it = youtube.load_state()["items"][0]
    assert it["tries"] == 1 and it["next_try"] == NOW + youtube.LATER + 300 and it["status"] == "pending"
    # tải được: hẹn giờ (giờ Facebook còn xa) -> báo admin kèm link
    t1 = NOW + youtube.LATER + 300
    msg, calls = asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=t1)))
    it = youtube.load_state()["items"][0]
    assert it["status"] == "done" and it["yt_id"] == "VID1" and it["publish"] == W and it["desc"] == ""
    assert any("YouTube (kênh bếp)" in m and "youtube.com/shorts/VID1" in m for m in sent)
    lines = youtube.status_lines("cook", now=t1)
    assert "đã đăng 1, chờ 0" in lines[0] and lines[1].startswith("YouTube đã hẹn")
    # lượt xem sau 24 giờ
    asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=W + youtube.VIEWS_AFTER)))
    assert youtube.load_state()["items"][0]["views"] == 321
    assert "#7 321" in youtube.status_lines("cook", now=W + youtube.VIEWS_AFTER)[-1]


def test_revoked_token_pauses_channel_until_reconnect(monkeypatch, yt, tmp_path):
    sent = yt
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 1000)
    config.save_settings({"YT_REFRESH_TOKEN": "bad", "KIDS_YT_REFRESH_TOKEN": "RTK"})
    assert youtube.enqueue("cook", 1, str(vid), "A", "d", None, now=NOW)
    asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=NOW)))
    st = youtube.load_state()
    assert "cook" in st["paused"] and "invalid_grant" in st["paused"]["cook"]["why"]
    assert st["items"][0]["status"] == "pending" and any("⛔ YouTube (kênh bếp)" in m for m in sent)
    assert "DỪNG" in youtube.status_lines("cook")[0]
    # kênh bé vẫn chạy trong lúc kênh bếp dừng
    assert youtube.enqueue("kids", 2, str(vid), "B", "d", None, now=NOW)
    asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=NOW + 1)))
    st = youtube.load_state()
    assert [i["status"] for i in st["items"]] == ["pending", "done"]
    init = []
    # kết nối lại (token đổi): tự bỏ dừng, video chờ được đăng ngay (giờ hẹn None -> công khai)
    config.save_settings({"YT_REFRESH_TOKEN": "RT2"})
    _m, calls = asyncio.run(_with_google(monkeypatch, lambda: youtube.tick(now=NOW + 2)))
    init = [c for c in calls if c[0] == "init"]
    st = youtube.load_state()
    assert st["paused"] == {} and st["items"][0]["status"] == "done"
    assert init[0][4]["status"]["privacyStatus"] == "public"


def test_stale_upload_not_retried_and_missing_file(monkeypatch, yt, tmp_path):
    sent = yt
    config.save_settings({"YT_REFRESH_TOKEN": "RT1"})
    assert youtube.enqueue("cook", 3, str(tmp_path / "khong-co.mp4"), "A", "d", None, now=NOW)
    youtube._patch("cook:3", status="uploading")                                     # bot bị dừng giữa lúc tải
    assert youtube.enqueue("cook", 4, str(tmp_path / "khong-co.mp4"), "B", "d", None, now=NOW)
    asyncio.run(youtube.tick(now=NOW))
    st = {i["key"]: i for i in youtube.load_state()["items"]}
    assert st["cook:3"]["status"] == "unknown" and st["cook:4"]["status"] == "failed"
    assert any("KHÔNG tự tải lại" in m for m in sent) and any("không còn file" in m for m in sent)


def test_settings_keys_secret_and_not_sent_to_kaggle():
    from rmvideo.app import settings
    for k in ("YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "KIDS_YT_REFRESH_TOKEN"):
        assert k in settings.SECRET_KEYS and k in settings.REAUTH_KEYS
    assert all(any(k.startswith(p) for p in settings.SNAPSHOT_EXCLUDE_PREFIXES)
               for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "KIDS_YT_REFRESH_TOKEN"))
    with pytest.raises(ValueError):
        settings.BY_KEY["YT_CLIENT_ID"].validate("abc")


def test_web_connect_needs_password_and_callback(monkeypatch, yt, tmp_path):
    from test_security import _client

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/youtube")).status == 401
            r = await c.post("/api/login", json={"password": "pw-123"})
            assert r.status == 200
            j = await (await c.get("/api/youtube")).json()
            assert set(j) == {"cook", "kids"} and "chưa kết nối" in j["cook"][0]
            r = await c.post("/api/youtube/cook/connect", json={})                 # phiên đăng nhập chưa đủ
            assert r.status == 403 and (await r.json())["need_password"]
            r = await c.post("/api/youtube/cook/connect", json={"_password": "sai"})
            assert r.status == 403 and (await r.json())["error"] == "Sai mật khẩu"
            r = await c.post("/api/youtube/cook/connect", json={"_password": "pw-123"})
            url = (await r.json())["url"]
            assert url.startswith(youtube.AUTH_URL) and len(youtube._PENDING) == 1
            assert (await c.post("/api/youtube/xyz/connect", json={})).status == 404
            r = await c.post("/api/youtube/kids/test", json={})
            assert "❌" in (await r.json())["text"]
            # Google chuyển về: không cần cookie (SameSite=Strict), state lạ báo lỗi, chữ được escape
            c.session.cookie_jar.clear()
            r = await c.get("/youtube/callback", params={"state": "<script>", "code": "x"})
            t = await r.text()
            assert r.status == 200 and "❌" in t and "<script>" not in t and r.headers["Cache-Control"] == "no-store"
            r = await c.get("/youtube/callback", params={"error": "access_denied", "state": "s"})
            assert "access_denied" in await r.text()
    asyncio.run(go())


def test_autopost_finish_queues_youtube(monkeypatch, yt, tmp_path):
    """Fanpage nhận bài xong thì video vào hàng chờ YouTube cùng giờ hẹn (kênh bếp đã kết nối)."""
    import autopost
    import state
    config.save_settings({"YT_REFRESH_TOKEN": "RT1"})
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 100)

    class Job:
        id, status, error, no_autopost, out_dur, parts = 42, "done", "", "", 60, []
        post = prompt_engine.platform_post("Bò kho", "Thơm lừng", "bokho")
        output = str(vid)
        source_meta: dict = {}

    async def publish(*a, **k):
        return "FB1", "reel"
    monkeypatch.setattr(autopost, "publish", publish)
    monkeypatch.setattr(state.queue, "jobs", {42: Job})
    st = autopost.load_state()
    st["pending"] = {"job": 42, "vid": "v1", "source": "s", "slot": "20:30", "slot_ts": NOW + 7200}
    msg = asyncio.run(autopost._finish_pending(st, NOW, __import__("random").Random(1)))
    assert "FB1" in msg
    it = youtube.load_state()["items"][0]
    assert (it["key"], it["when"], it["title"]) == ("cook:42", NOW + 7200, "Bò kho #Shorts")
    assert json.dumps(it, ensure_ascii=False)


def test_pending_too_long_is_dropped(monkeypatch, yt, tmp_path):
    """Kênh dừng (token bị thu hồi) mà không kết nối lại: video chờ quá PENDING_MAX_AGE thì bỏ (để ổ đĩa được dọn)."""
    config.save_settings({"YT_REFRESH_TOKEN": "RT1"})
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")
    assert youtube.enqueue("cook", 1, str(vid), "A", "d", None, now=NOW - youtube.PENDING_MAX_AGE - 1)
    st = youtube.load_state()
    st["paused"]["cook"] = {"why": "x", "fp": youtube._fp("RT1"), "at": NOW}
    youtube.save_state(st)
    asyncio.run(youtube.tick(now=NOW))
    it = youtube.load_state()["items"][0]
    assert it["status"] == "failed" and "3 ngày" in it["why"] and youtube.pending_paths() == set()
