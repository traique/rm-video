"""🤖 Tự động đăng: đọc kênh nguồn, chọn giờ vàng, không dùng lại video, đăng/hẹn giờ lên Page qua Graph API
(máy chủ Graph API giả chạy cục bộ)."""
import asyncio
import os
import random
from datetime import datetime

import pytest
from aiohttp import web

import autopost
import config
from queue_manager import Job


@pytest.fixture(autouse=True)
def _cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(autopost, "STATE_FILE", str(tmp_path / "ap.json"))
    for k, v in (("AUTO_POST", "1"), ("AUTO_SOURCES", "duynauan"), ("AUTO_PER_DAY", "2"), ("AUTO_TIMES", ""),
                 ("AUTO_MODE", "cook"), ("FB_PAGE_ID", "111"), ("FB_PAGE_TOKEN", "tok"), ("TTS_PROVIDER", "edge")):
        monkeypatch.setattr(config, k, v)


def _ts(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=autopost.VN).timestamp()


def test_normalize_source():
    n = autopost.normalize_source
    assert n("duynauan") == n("@duynauan") == n("https://m.facebook.com/duynauan/reels/") == \
        "https://www.facebook.com/duynauan"
    assert n("https://www.facebook.com/profile.php?id=6159&sk=reels_tab") == \
        "https://www.facebook.com/profile.php?id=6159"
    for bad in ("https://www.tiktok.com/@a", "https://www.facebook.com/reel/123/", "https://www.facebook.com/share/v/x/"):
        with pytest.raises(ValueError):
            n(bad)


def test_parse_listing_keeps_order_and_owner():
    reels = '..."userID":"61593373703078"...{"video_id":"4575789695967670"}..{"video_id":"1664186575284347"}' \
            '{"video_id":"4575789695967670"}'
    videos = 'href="https:\\/\\/www.facebook.com\\/duynauan\\/videos\\/mon-ngon\\/2307537166712991\\/"'
    ids, owner = autopost.parse_listing(reels, videos)
    assert ids == ["4575789695967670", "1664186575284347", "2307537166712991"] and owner == "61593373703078"


def test_choose_slots_default_learned_fixed(monkeypatch):
    st = autopost.load_state()
    rnd = random.Random(1)
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    assert autopost.choose_slots(st, 2, rnd) == ["11:30", "20:30"]         # chưa có số liệu: trưa + tối
    st["posts"] = [{"slot": "19:00", "views": 900}, {"slot": "19:00", "views": 1100},
                   {"slot": "20:30", "views": 300}, {"slot": "20:30", "views": 200},
                   {"slot": "11:30", "views": 800}, {"slot": "11:30", "views": 600}]
    assert autopost.choose_slots(st, 2, rnd) == ["11:30", "19:00"]          # học theo lượt xem thật
    monkeypatch.setattr(autopost, "EXPLORE", 1.0)
    got = autopost.choose_slots(st, 2, rnd)
    assert "19:00" in got and len(got) == 2                                  # thử khung khác, giữ khung tốt nhất
    monkeypatch.setattr(config, "AUTO_TIMES", "20:00, 12:00")
    assert autopost.choose_slots(st, 2, rnd) == ["12:00", "20:00"]
    for k in range(4):                                                       # các khung luôn cách nhau >= 3 giờ
        s = autopost.choose_slots(autopost.load_state(), 4, random.Random(k))
        assert all(abs(autopost._hm(a) - autopost._hm(b)) >= 180 for a in s for b in s if a != b)


def test_free_slots_skip_taken_and_too_soon(monkeypatch):
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    st = autopost.load_state()
    now = _ts(12, "11:20")                                  # 11:30 hôm nay còn 10 phút: không kịp hẹn
    got = autopost.free_slots(st, now, random.Random(0))
    assert got[0] == (_ts(12, "20:30"), "20:30") and (_ts(13, "11:30"), "11:30") in got
    st["posts"].append({"time": _ts(12, "20:30")})
    assert autopost.free_slots(st, now, random.Random(0))[0] == (_ts(13, "11:30"), "11:30")


# ---------------- máy chủ Graph API giả ----------------

def _fake_graph(calls):
    app = web.Application(client_max_size=50 << 20)

    async def reels(req):
        q = dict(req.query)
        body = await req.json() if req.content_type == "application/json" else {}
        if body.get("upload_phase") == "start":
            calls.append(("start", body.get("access_token")))
            return web.json_response({"video_id": "R1", "upload_url": str(req.url.with_path("/video-upload/R1"))})
        calls.append(("finish", q.get("video_state"), q.get("scheduled_publish_time"), q.get("description")))
        return web.json_response({"success": True})

    async def upload(req):
        data = await req.read()
        calls.append(("upload", req.headers.get("Authorization"), int(req.headers["file_size"]), len(data)))
        return web.json_response({"success": True})

    async def status(req):
        if req.match_info["vid"] == "BAD":
            return web.json_response({"error": {"code": 190, "message": "Error validating access token"}}, status=400)
        return web.json_response({"status": {"video_status": "upload_complete",
                                             "uploading_phase": {"status": "complete"}}})

    async def videos(req):
        form = await req.post()
        calls.append(("videos", form.get("published"), form.get("scheduled_publish_time"),
                      len(form["source"].file.read())))
        return web.json_response({"id": "V9"})

    async def insights(req):
        return web.json_response({"data": [{"name": "total_video_views", "period": "lifetime",
                                            "values": [{"value": 4321}]}]})
    app.router.add_post("/v27.0/111/video_reels", reels)
    app.router.add_post("/video-upload/R1", upload)
    app.router.add_post("/v27.0/111/videos", videos)
    app.router.add_get("/v27.0/{vid}/video_insights", insights)
    app.router.add_get("/v27.0/{vid}", status)
    return app


async def _with_graph(monkeypatch, coro_fn):
    calls = []
    runner = web.AppRunner(_fake_graph(calls))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    for k in ("GRAPH", "GRAPH_VIDEO", "RUPLOAD"):
        monkeypatch.setattr(autopost, k, base)
    try:
        return await coro_fn(), calls
    finally:
        await runner.cleanup()


def test_publish_reel_scheduled_and_long_video(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(os.urandom(300_000))
    when = 1_800_000_000

    async def both():
        a = await autopost.publish(str(vid), "Tiêu đề", "Mô tả #a", 45, when)
        b = await autopost.publish(str(vid), "Tiêu đề", "Mô tả #a", 170, when)
        c = await autopost.publish(str(vid), "Tiêu đề", "Mô tả #a", 30, None)
        return a, b, c
    (a, b, c), calls = asyncio.run(_with_graph(monkeypatch, both))
    assert a == ("R1", "reel") and b == ("V9", "video") and c == ("R1", "reel")
    assert ("upload", "OAuth tok", 300_000, 300_000) in calls
    assert ("finish", "SCHEDULED", str(when), "Mô tả #a") in calls                 # <= 90s: Reels, hẹn giờ
    assert ("videos", "false", str(when), 300_000) in calls                       # > 90s: video thường, hẹn giờ
    assert ("finish", "PUBLISHED", None, "Mô tả #a") in calls


def test_tick_picks_new_video_then_schedules_post(monkeypatch, tmp_path):
    import core_video
    import state
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    monkeypatch.setattr(autopost, "list_channel_sync",
                        lambda src: (["100000001", "100000002", "100000003"], "777"))

    async def probe(url, td):
        vid = url.rstrip("/").rsplit("/", 1)[1]
        return {"duration": 400 if vid == "100000003" else 60, "uploader_id": "999" if vid == "100000002" else "777",
                "title": "Món ngon"}
    monkeypatch.setattr(core_video, "probe_formats", probe)
    queued = []
    monkeypatch.setattr(state.queue, "put", lambda job, force=False: queued.append(job) or 1)
    monkeypatch.setattr(state.queue, "pending", 0)
    now = _ts(12, "09:00")
    msg = asyncio.run(autopost.tick(now=now, rnd=random.Random(3)))
    assert msg.startswith("job #") and len(queued) == 1
    job = queued[0]
    assert job.url == "https://www.facebook.com/reel/100000001/" and job.source == "auto" and job.mode == "cook"
    st = autopost.load_state()
    reasons = {"100000002": "khác kênh", "100000003": "dài"}                  # video khác kênh / quá dài bị bỏ
    assert all(st["used"][k]["skip"].startswith(r) for k, r in reasons.items() if k in st["used"])
    assert st["pending"]["slot"] == "11:30"
    # chưa xong thì chờ, không lấy thêm video
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    job.status = "processing"
    assert asyncio.run(autopost.tick(now=now + 60)).startswith("đang làm")
    # xong (trễ khung 11:30 vì máy chậm) -> hẹn sang khung trống kế tiếp 20:30
    out = tmp_path / "out.mp4"
    out.write_bytes(b"x" * 1000)
    job.status, job.parts, job.out_dur = "done", [{"label": "Video", "path": str(out)}], 58.0
    job.post = "📌 Mì xào bò ngon\nCaption #monngon"
    sent = []

    async def fake_publish(path, title, desc, dur, when, thumb="", session=None):
        sent.append((path, title, dur, when))
        return "R7", "reel"
    monkeypatch.setattr(autopost, "publish", fake_publish)
    later = _ts(12, "11:25")
    msg = asyncio.run(autopost.tick(now=later, rnd=random.Random(3)))
    assert sent == [(str(out), "Mì xào bò ngon", 58.0, _ts(12, "20:30"))]
    st = autopost.load_state()
    assert st["posts"][0]["slot"] == "20:30" and st["posts"][0]["fb_id"] == "R7" and st["pending"] is None
    # cùng lượt đó đi tìm video cho khung kế tiếp: hai video còn lại đều bị loại -> báo hết video, không lặp lại
    assert msg.startswith("hết video mới") and len(queued) == 1
    assert set(st["used"]) == {"100000001", "100000002", "100000003"}
    assert st["used"]["100000002"]["skip"] == "khác kênh" and st["used"]["100000003"]["skip"].startswith("dài")


def test_token_error_pauses(monkeypatch, tmp_path):
    import state
    out = tmp_path / "o.mp4"
    out.write_bytes(b"x")
    job = Job(url="u", mode="cook", duration=0, voice="", source="auto", status="done")
    job.parts = [{"label": "Video", "path": str(out)}]
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    st = autopost.load_state()
    st["pending"] = {"job": job.id, "vid": "1", "source": "s", "slot_ts": _ts(12, "20:30"), "slot": "20:30", "dur": 30}
    autopost.save_state(st)

    async def bad(*a, **k):
        raise autopost.FBError("Facebook lỗi 190: token hết hạn", 190)
    monkeypatch.setattr(autopost, "publish", bad)
    with pytest.raises(autopost.FBError):
        asyncio.run(autopost.tick(now=_ts(12, "10:00")))
    assert autopost.load_state()["paused"]
    assert asyncio.run(autopost.tick(now=_ts(12, "10:05"))).startswith("đang dừng")


def test_views_learning(monkeypatch):
    st = autopost.load_state()
    st["posts"] = [{"fb_id": "V1", "slot": "20:30", "time": _ts(10, "20:30"), "views": None}]
    autopost.save_state(st)

    async def run():
        import aiohttp
        async with aiohttp.ClientSession() as s:
            return await autopost.views_of("V1", s)
    v, _ = asyncio.run(_with_graph(monkeypatch, run))
    assert v == 4321


def test_disabled_without_token(monkeypatch):
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "")
    assert "FB_PAGE_TOKEN" in asyncio.run(autopost.tick())


def test_settings_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    for k, v in (("AUTO_TIMES", "25:00"), ("AUTO_PER_DAY", "9"), ("AUTO_SOURCES", "https://www.tiktok.com/@a"),
                 ("FB_PAGE_ID", "abc")):
        with pytest.raises(ValueError):
            config.save_settings({k: v})
    config.save_settings({"AUTO_TIMES": "11:30,20:30", "AUTO_SOURCES": "duynauan, vaobepmoingaynhe"})


def test_web_status_and_actions(monkeypatch, tmp_path):
    from test_security import _client
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "")

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/autopost")).status == 401                  # phải đăng nhập
            r = await c.get("/api/autopost", headers={"X-Auth": "pw-123"})
            assert r.status == 200 and "Tự động đăng" in (await r.json())["text"]
            r = await c.post("/api/autopost/now", headers={"X-Auth": "pw-123"})
            d = await r.json()
            assert "FB_PAGE_TOKEN" in d["text"] and "Tự động đăng" in d["status"]
            assert (await c.post("/api/autopost/xoa", headers={"X-Auth": "pw-123"})).status == 404
    asyncio.run(go())
