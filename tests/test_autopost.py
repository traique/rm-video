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
    monkeypatch.setattr(autopost, "_POSTED", {})     # test khác tạo JobQueue mới -> số job đánh lại từ đầu
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

    async def thumbnails(req):
        form = await req.post()
        calls.append(("thumb", req.match_info["vid"], form.get("is_preferred"), len(form["source"].file.read())))
        if req.match_info["vid"] == "R1" and os.environ.get("FAKE_THUMB_FAIL"):
            return web.json_response({"error": {"code": 100, "message": "Unsupported post request"}}, status=400)
        return web.json_response({"success": True})
    app.router.add_post("/v27.0/111/video_reels", reels)
    app.router.add_post("/v27.0/{vid}/thumbnails", thumbnails)
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


# ---------------- các lỗi tìm được khi rà soát trước deploy ----------------

def _done_job(tmp_path, **kw):
    out = tmp_path / "o.mp4"
    out.write_bytes(b"x" * 100)
    job = Job(url="u", mode="cook", duration=0, voice="", source="auto", status="done", **kw)
    job.parts, job.out_dur, job.post = [{"label": "Video", "path": str(out)}], 40.0, "📌 Tiêu đề\nnội dung"
    return job


def _pending_for(job, slot="20:30"):
    st = autopost.load_state()
    st["pending"] = {"job": job.id, "vid": "555", "source": "s", "slot_ts": _ts(12, slot), "slot": slot, "dur": 40}
    st["used"]["555"] = {"job": job.id}
    autopost.save_state(st)


def test_concurrent_ticks_publish_once(monkeypatch, tmp_path):
    import state
    job = _done_job(tmp_path)
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    monkeypatch.setattr(state.queue, "pending", 1)
    _pending_for(job)
    calls = []

    async def slow_publish(*a, **k):
        calls.append(1)
        await asyncio.sleep(0.2)
        return "R1", "reel"
    monkeypatch.setattr(autopost, "publish", slow_publish)

    async def both():
        return await asyncio.gather(autopost.tick(now=_ts(12, "10:00")), autopost.tick(now=_ts(12, "10:00")))
    a, b = asyncio.run(both())
    assert len(calls) == 1 and "một lượt khác" in (a + b)
    assert len(autopost.load_state()["posts"]) == 1


def test_never_republish_after_crash_or_failed_save(monkeypatch, tmp_path):
    import state
    job = _done_job(tmp_path)
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    monkeypatch.setattr(state.queue, "pending", 1)
    calls = []

    async def pub(*a, **k):
        calls.append(1)
        return "R1", "reel"
    monkeypatch.setattr(autopost, "publish", pub)
    # (a) bot chết đúng lúc đang tải lên: lần sau KHÔNG đăng lại, chỉ báo kiểm tra
    _pending_for(job)
    st = autopost.load_state()
    st["pending"]["publishing"] = _ts(12, "09:59")
    autopost.save_state(st)
    asyncio.run(autopost.tick(now=_ts(12, "10:00")))
    st = autopost.load_state()
    assert calls == [] and st["pending"] is None and st["used"]["555"]["skip"] == "dừng giữa lúc đăng"
    # (b) đăng xong nhưng ghi file lỗi: lượt sau đọc lại pending cũ mà vẫn không đăng lần hai
    _pending_for(job)
    real_save = autopost.save_state
    n = {"saves": 0}

    def flaky_save(st, path=None):
        n["saves"] += 1
        if n["saves"] >= 2:                     # lần ghi dấu "đang đăng" OK, các lần sau (sau khi đăng) lỗi
            raise OSError("No space left on device")
        real_save(st, path)
    monkeypatch.setattr(autopost, "save_state", flaky_save)
    with pytest.raises(OSError):
        asyncio.run(autopost.tick(now=_ts(12, "10:00")))
    monkeypatch.setattr(autopost, "save_state", real_save)
    st = autopost.load_state()                  # trên đĩa vẫn là pending + dấu "đang đăng"
    st["pending"].pop("publishing", None)       # (kể cả khi dấu này cũng mất)
    autopost.save_state(st)
    asyncio.run(autopost.tick(now=_ts(12, "10:02")))
    assert len(calls) == 1 and len(autopost.load_state()["posts"]) == 1


def test_temporary_errors_back_off_instead_of_burning_videos(monkeypatch, tmp_path):
    import core_video
    import state
    monkeypatch.setattr(state.queue, "pending", 0)
    listed = []

    def listing(src):
        listed.append(src)
        return ["900000001", "900000002"], "777"
    monkeypatch.setattr(autopost, "list_channel_sync", listing)

    async def probe_down(url, td):
        raise RuntimeError("Tải video lỗi: HTTP Error 503")
    monkeypatch.setattr(core_video, "probe_formats", probe_down)
    t0 = _ts(12, "09:00")
    asyncio.run(autopost.tick(now=t0))
    st = autopost.load_state()
    assert st["used"] == {} and st["fails"]["900000001"]["n"] == 1          # chưa bỏ: chỉ nghỉ 6 giờ
    asyncio.run(autopost.tick(now=t0 + 120))
    assert autopost.load_state()["fails"]["900000001"]["n"] == 1 and len(listed) == 1   # chưa tới giờ thử lại
    asyncio.run(autopost.tick(now=t0 + autopost.RETRY_AFTER + 60))
    st = autopost.load_state()
    assert st["fails"]["900000001"]["n"] == 2 and "900000001" in st["used"]  # lỗi 2 lần mới bỏ hẳn

    def listing_down(src):
        listed.append(src)
        raise RuntimeError("HTTP 429")
    monkeypatch.setattr(autopost, "list_channel_sync", listing_down)
    st["listing"] = {}
    autopost.save_state(st)
    for k in range(3):
        asyncio.run(autopost.tick(now=t0 + 7 * 3600 + 120 * k))
    assert len(listed) == 3                    # t0, sau 6 giờ, lần lỗi lúc 7 giờ; không đọc lại mỗi 2 phút sau lỗi


def test_failed_jobs_retry_then_pause_after_streak(monkeypatch, tmp_path):
    import state
    monkeypatch.setattr(state.queue, "pending", 1)
    sent = []

    async def admin(text):
        sent.append(text)
    monkeypatch.setattr(state, "notify_admin", admin)
    t = _ts(12, "09:00")
    for k in range(3):
        job = Job(url="u", mode="cook", duration=0, voice="", source="auto", status="error", error="Ổ đĩa đầy")
        monkeypatch.setattr(state.queue, "jobs", {job.id: job})
        _pending_for(job)
        asyncio.run(autopost.tick(now=t + k))
    st = autopost.load_state()
    assert "3 video liên tiếp" in st["paused"] and len(sent) == 1
    assert st["fails"]["555"]["n"] == 3 and "555" in st["used"]


def test_weak_script_never_published(monkeypatch, tmp_path):
    import state
    job = _done_job(tmp_path, no_autopost="AI viết kịch bản lỗi, video dùng kịch bản mẫu")
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    monkeypatch.setattr(state.queue, "pending", 1)
    _pending_for(job)
    calls = []

    async def pub(*a, **k):
        calls.append(1)
        return "R", "reel"
    monkeypatch.setattr(autopost, "publish", pub)
    asyncio.run(autopost.tick(now=_ts(12, "10:00")))
    st = autopost.load_state()
    assert calls == [] and st["pending"] is None and "kịch bản mẫu" in st["fails"]["555"]["why"]


def test_publish_error_backoff_and_pause_codes(monkeypatch, tmp_path):
    import state
    job = _done_job(tmp_path)
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    monkeypatch.setattr(state.queue, "pending", 1)
    _pending_for(job)
    calls = []

    async def rate_limited(*a, **k):
        calls.append(1)
        raise autopost.FBError("Facebook lỗi 4: Application request limit reached", 4)
    monkeypatch.setattr(autopost, "publish", rate_limited)
    t = _ts(12, "10:00")
    with pytest.raises(autopost.FBError):
        asyncio.run(autopost.tick(now=t))
    assert asyncio.run(autopost.tick(now=t + 120)).startswith("chờ thử đăng lại") and len(calls) == 1
    st = autopost.load_state()
    assert st["pending"]["tries"] == 1 and not st["paused"]

    async def wrong_page(*a, **k):
        raise autopost.FBError("Facebook lỗi 100: Unsupported post request", 100)
    monkeypatch.setattr(autopost, "publish", wrong_page)
    with pytest.raises(autopost.FBError):
        asyncio.run(autopost.tick(now=t + 600))
    st = autopost.load_state()
    assert st["paused"] and st["pending"]["job"] == job.id                  # dừng nhưng vẫn giữ video để đăng lại


def test_source_without_scheme_and_settings_change_apply_now(monkeypatch):
    n = autopost.normalize_source
    for s in ("facebook.com/duynauan", "www.facebook.com/duynauan/", "m.facebook.com/duynauan/reels"):
        assert n(s) == "https://www.facebook.com/duynauan"
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    st = autopost.load_state()
    now = _ts(12, "06:00")
    assert [t for _, t in autopost.upcoming_slots(st, now, random.Random(0))][:2] == ["11:30", "20:30"]
    monkeypatch.setattr(config, "AUTO_TIMES", "08:00,13:00")
    assert [t for _, t in autopost.upcoming_slots(st, now, random.Random(0))][:2] == ["08:00", "13:00"]
    st["posts"].append({"time": _ts(13, "08:00")})                         # ngày đã có bài hẹn: giữ khung cũ
    monkeypatch.setattr(config, "AUTO_TIMES", "09:00")
    days = {t for ts, t in autopost.upcoming_slots(st, now, random.Random(0))
            if datetime.fromtimestamp(ts, autopost.VN).day == 13}
    assert days == {"08:00", "13:00"}


def test_corrupt_state_kept_and_paused(monkeypatch, tmp_path):
    open(autopost.STATE_FILE, "w").write('{"used": {"1": ')
    st = autopost.load_state()
    assert st["paused"] and os.path.exists(autopost.STATE_FILE + ".bad")
    assert asyncio.run(autopost.tick()).startswith("đang dừng")
    assert autopost.load_state()["paused"]                                  # lần sau vẫn dừng (đã ghi lại)


# ---------------- bản 28: nguồn TikTok, ô tick nguồn, chống trùng giữa hai nền tảng ----------------

def test_normalize_tiktok():
    n = autopost.normalize_tiktok
    want = "https://www.tiktok.com/@quetrai.nauan"
    assert n("quetrai.nauan") == n("@QueTrai.NauAn") == n("tiktok.com/@quetrai.nauan") == \
        n("https://www.tiktok.com/@quetrai.nauan?lang=vi") == n("https://m.tiktok.com/@quetrai.nauan/") == want
    for bad in ("https://www.tiktok.com/@a/video/7300000000000000000", "https://www.facebook.com/quetrai",
                "https://evil.example/@quetrai", "@a", "https://vt.tiktok.com/ZSabc/"):
        with pytest.raises(ValueError):
            n(bad)


def test_list_tiktok_uses_ytdlp_flat_listing(monkeypatch):
    import sys
    import types
    seen = {}

    class FakeYDL:
        entries = [
            {"id": "7300000000000000001", "url": "https://www.tiktok.com/@quetrai/video/7300000000000000001",
             "title": "Cá kho tộ #nauan", "duration": 58},
            {"id": "7300000000000000002", "url": "https://evil.example/@quetrai/video/7300000000000000002",
             "title": "x", "duration": 10},                                    # link lạ: bỏ
            {"id": "abc", "url": "https://www.tiktok.com/@quetrai/video/abc"},  # id không phải số: bỏ
            {"id": "7300000000000000003", "url": "https://www.tiktok.com/@quetrai/photo/7300000000000000003"},
        ]

        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            seen["url"] = url
            return {"entries": list(self.entries)}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(autopost.url_guard, "check_url", lambda u, **k: u)
    items, owner = autopost.list_tiktok_sync("https://www.tiktok.com/@quetrai")
    assert owner == "quetrai" and [x["id"] for x in items] == ["7300000000000000001"]
    assert items[0]["dur"] == 58 and items[0]["title"] == "Cá kho tộ #nauan"
    assert seen["opts"]["extract_flat"] == "in_playlist" and seen["opts"]["playlistend"] == 30
    FakeYDL.entries = []                                                       # kênh riêng tư / bị chặn
    with pytest.raises(RuntimeError, match="TikTok"):
        autopost.list_tiktok_sync("https://www.tiktok.com/@quetrai")


def test_same_video_across_platforms():
    a = {"dur": 58.0, "title": autopost._norm_title("Cá kho tộ đậm đà chuẩn vị miền Tây 🐟 #nauan #monngon")}
    b = {"dur": 57.2, "title": autopost._norm_title("Cá kho tộ đậm đà chuẩn vị miền Tây #fyp")}
    assert autopost.same_video(a, b)
    assert not autopost.same_video(a, {**b, "dur": 61})                         # lệch hơn 1.5 giây
    assert not autopost.same_video(a, {"dur": 58, "title": autopost._norm_title("Gà chiên nước mắm giòn rụm")})
    assert not autopost.same_video({"dur": 58, "title": ""}, {"dur": 58, "title": ""})   # thiếu chú thích: không đoán
    st = autopost.load_state()
    st["used"] = {"123456789": {"job": 1, "plat": "facebook", **a}, "tt:9": {"skip": "khác kênh", **a}}
    assert autopost.duplicate_of(st, "tt:7", {"duration": 58, "title": "Cá kho tộ đậm đà chuẩn vị miền Tây 😋"}) \
        == "123456789"
    assert autopost.duplicate_of(st, "123456789", {"duration": 58, "title": "Cá kho tộ đậm đà chuẩn vị miền Tây"}) == ""


def test_platform_checkboxes_and_alternation(monkeypatch):
    monkeypatch.setattr(config, "AUTO_TIKTOK_SOURCES", "@quetrai")
    for raw, want in (("", ["facebook", "tiktok"]), ("tiktok", ["tiktok"]), ("facebook", ["facebook"]),
                      ("tiktok,facebook", ["facebook", "tiktok"]), ("xyz", ["facebook", "tiktok"])):
        monkeypatch.setattr(config, "AUTO_PLATFORMS", raw)
        assert autopost.platforms() == want                                    # bỏ tick hết = lấy cả hai
    monkeypatch.setattr(config, "AUTO_PLATFORMS", "tiktok")
    assert autopost.all_sources() == [("tiktok", "https://www.tiktok.com/@quetrai")]
    monkeypatch.setattr(config, "AUTO_PLATFORMS", "facebook,tiktok")
    st = autopost.load_state()
    rnd = random.Random(0)
    st["used"] = {"1": {"at": 1, "job": 1, "plat": "facebook"}, "2": {"at": 2, "job": 2, "plat": "facebook"},
                  "tt:3": {"at": 3, "job": 3, "plat": "tiktok"}, "tt:x": {"at": 4, "skip": "khác kênh", "plat": "tiktok"}}
    assert autopost._platform_order(st, rnd) == ["tiktok", "facebook"]          # TikTok ít hơn -> lượt này TikTok
    st["used"]["tt:4"] = {"at": 5, "job": 4, "plat": "tiktok"}
    st["used"]["tt:5"] = {"at": 6, "job": 5, "plat": "tiktok"}
    assert autopost._platform_order(st, rnd) == ["facebook", "tiktok"]
    monkeypatch.setattr(config, "AUTO_TIKTOK_SOURCES", "")                      # tick TikTok mà chưa có kênh: bỏ qua
    assert autopost._platform_order(st, rnd) == ["facebook"]
    monkeypatch.setattr(config, "AUTO_SOURCES", "")
    monkeypatch.setattr(config, "AUTO_PLATFORMS", "tiktok")
    assert "AUTO_TIKTOK_SOURCES" in autopost.enabled()


def _tiktok_only(monkeypatch, items, probe_info):
    import core_video
    import state
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    monkeypatch.setattr(config, "AUTO_TIKTOK_SOURCES", "@quetrai")
    monkeypatch.setattr(config, "AUTO_PLATFORMS", "tiktok")
    monkeypatch.setattr(autopost, "list_tiktok_sync", lambda src: (items, "quetrai"))

    def no_fb(src):
        raise AssertionError("không tick Facebook mà vẫn đọc kênh Facebook")
    monkeypatch.setattr(autopost, "list_channel_sync", no_fb)

    async def probe(url, td):
        return probe_info[url.rsplit("/", 1)[1]]
    monkeypatch.setattr(core_video, "probe_formats", probe)
    queued = []
    monkeypatch.setattr(state.queue, "put", lambda job, force=False: queued.append(job) or 1)
    monkeypatch.setattr(state.queue, "pending", 0)
    return queued


def test_tick_picks_tiktok_video(monkeypatch):
    items = [{"id": "7302", "url": "https://www.tiktok.com/@quetrai/video/7302", "title": "Gà chiên", "dur": 40}]
    queued = _tiktok_only(monkeypatch, items, {"7302": {"duration": 40, "uploader": "quetrai",
                                                        "title": "Gà chiên nước mắm giòn rụm"}})
    msg = asyncio.run(autopost.tick(now=_ts(12, "09:00"), rnd=random.Random(1)))
    assert msg.startswith("job #") and [j.url for j in queued] == ["https://www.tiktok.com/@quetrai/video/7302"]
    assert queued[0].source == "auto" and queued[0].title_hint == "Gà chiên nước mắm giòn rụm"
    st = autopost.load_state()
    assert st["used"]["tt:7302"]["plat"] == "tiktok" and st["pending"]["plat"] == "tiktok"
    assert st["used"]["tt:7302"]["title"] == "gà chiên nước mắm giòn rụm" and st["used"]["tt:7302"]["dur"] == 40
    assert "TikTok 1" in autopost.status_text() and "Nguồn đang tick: Tiktok" in autopost.status_text()


def test_tick_skips_cross_platform_duplicate_and_other_owner(monkeypatch):
    items = [{"id": "7301", "url": "https://www.tiktok.com/@quetrai/video/7301", "title": "Cá kho", "dur": 58},
             {"id": "7303", "url": "https://www.tiktok.com/@quetrai/video/7303", "title": "Bò", "dur": 30}]
    queued = _tiktok_only(monkeypatch, items, {
        "7301": {"duration": 58, "uploader": "quetrai", "title": "Cá kho tộ đậm đà chuẩn vị miền Tây #nauan"},
        "7303": {"duration": 30, "uploader": "kenhkhac", "title": "Bò lúc lắc"}})
    st = autopost.load_state()               # cùng video cá kho đã lấy từ Facebook trước đó (đăng ở cả hai nơi)
    st["used"]["100000009"] = {"at": 1, "job": 9, "plat": "facebook", "dur": 58.4,
                               "title": autopost._norm_title("Cá kho tộ đậm đà chuẩn vị miền Tây 🐟")}
    autopost.save_state(st)
    msg = asyncio.run(autopost.tick(now=_ts(12, "09:00"), rnd=random.Random(1)))
    assert msg.startswith("hết video mới") and not queued
    st = autopost.load_state()
    assert st["used"]["tt:7301"]["skip"] == "trùng video đã làm (100000009)"
    assert st["used"]["tt:7303"]["skip"] == "khác kênh"


def test_platform_settings_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    for k, v in (("AUTO_PLATFORMS", "youtube"), ("AUTO_TIKTOK_SOURCES", "https://www.tiktok.com/@a/video/123"),
                 ("AUTO_TIKTOK_SOURCES", "https://www.facebook.com/abc")):
        with pytest.raises(ValueError):
            config.save_settings({k: v})
    config.save_settings({"AUTO_PLATFORMS": "tiktok, facebook", "AUTO_TIKTOK_SOURCES": "@quetrai, tiktok.com/@bepnha"})
    assert config.AUTO_PLATFORMS == "facebook,tiktok"
    assert autopost.tiktok_sources() == ["https://www.tiktok.com/@quetrai", "https://www.tiktok.com/@bepnha"]


def test_web_platform_checkboxes(monkeypatch, tmp_path):
    from test_security import _client
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "")
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))     # không đụng settings.json thật

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            h = {"X-Auth": "pw-123"}
            assert (await (await c.get("/api/autopost", headers=h)).json())["platforms"] == ["facebook", "tiktok"]
            r = await c.post("/api/settings", headers=h, json={"AUTO_PLATFORMS": "tiktok"})
            assert r.status == 200
            assert (await (await c.get("/api/autopost", headers=h)).json())["platforms"] == ["tiktok"]
            assert (await c.post("/api/settings", headers=h, json={"AUTO_PLATFORMS": "abc"})).status == 400
    asyncio.run(go())


def test_reel_gets_ai_cover_and_failure_never_breaks_post(monkeypatch, tmp_path):
    vid, cov = tmp_path / "v.mp4", tmp_path / "cover.jpg"
    vid.write_bytes(os.urandom(200_000))
    cov.write_bytes(b"\xff\xd8" + os.urandom(5000))

    async def go():
        return await autopost.publish(str(vid), "Tiêu đề", "Mô tả", 40, None, str(cov))
    got, calls = asyncio.run(_with_graph(monkeypatch, go))
    assert got == ("R1", "reel") and ("thumb", "R1", "true", 5002) in calls           # Reel: gắn ảnh bìa AI chọn
    monkeypatch.setenv("FAKE_THUMB_FAIL", "1")                                         # Facebook không nhận ảnh bìa Reel
    got, calls = asyncio.run(_with_graph(monkeypatch, go))
    assert got == ("R1", "reel") and any(c[0] == "thumb" for c in calls)               # bài vẫn đăng bình thường
