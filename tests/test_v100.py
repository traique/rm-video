"""Bản 1.0.0: thông báo Telegram / Zoom, kênh Zoom, số phiên bản + báo lên phiên bản, tông màu tự động, nhạc lót."""
import asyncio
import hashlib
import hmac
import json
import os
import time

import numpy as np
import pytest

import config
import state
from rmvideo.app import brand, job_factory, notifier, release
from rmvideo.interfaces.web import routes_zoom
from rmvideo.media import autolook, filtergraph, renderer, voicebed
from rmvideo.services import zoom
from rmvideo.version import VERSION

SECRET = "zoom-secret-token-123"


def _sign(body: bytes, ts: str | None = None) -> dict:
    ts = ts or str(int(time.time()))
    mac = hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"x-zm-request-timestamp": ts, "x-zm-signature": "v0=" + mac, "Content-Type": "application/json"}


@pytest.fixture
def zoomcfg(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ZOOM_SECRET_TOKEN", SECRET)
    monkeypatch.setattr(config, "ZOOM_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "ZOOM_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(config, "ZOOM_BOT_JID", "bot@xmpp.zoom.us")
    monkeypatch.setattr(config, "ZOOM_ALLOWED_USERS", "me@xmpp.zoom.us")
    monkeypatch.setattr(config, "ZOOM_ADMIN", "")
    monkeypatch.setenv("ZOOM_TARGETS", str(tmp_path / "targets.json"))
    sent: list = []

    async def fake_notify(target, text, head="rmvideo"):
        sent.append((dict(target or {}), text))
        return "ok"
    monkeypatch.setattr(zoom, "notify", fake_notify)
    return sent


# ---------------- Zoom: chữ ký, xác minh địa chỉ ----------------

def test_signature_checks(zoomcfg):
    body = b'{"event":"bot_notification"}'
    h = _sign(body)
    assert zoom.signature_ok(body, h["x-zm-request-timestamp"], h["x-zm-signature"])
    assert not zoom.signature_ok(body + b" ", h["x-zm-request-timestamp"], h["x-zm-signature"])     # sửa body
    old = str(int(time.time()) - 3600)
    assert not zoom.signature_ok(body, old, _sign(body, old)["x-zm-signature"])                    # quá cũ
    ms = str(int(time.time() * 1000))                                                              # mili giây
    assert zoom.signature_ok(body, ms, _sign(body, ms)["x-zm-signature"])
    assert not zoom.signature_ok(body, "", "")


def test_url_validation_refuses_signature_oracle(zoomcfg):
    r = zoom.url_validation("qgg8vlvZRS6UYooatFL8Aw")
    assert r["encryptedToken"] == hmac.new(SECRET.encode(), b"qgg8vlvZRS6UYooatFL8Aw", hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):                  # "v0:ts:body" có dấu ':' -> không cho ký hộ
        zoom.url_validation("v0:1700000000:{}")


def test_message_body_user_managed_needs_user_jid(zoomcfg, monkeypatch):
    b = zoom.message_body("to@x", "acc", "hi", "head", "me@x")
    assert b["robot_jid"] == "bot@xmpp.zoom.us" and b["user_jid"] == "me@x" and b["content"]["body"][0]["text"] == "hi"
    monkeypatch.setattr(config, "ZOOM_APP_TYPE", "admin")
    assert "user_jid" not in zoom.message_body("to@x", "acc", "hi", "head", "me@x")


def test_parse_flags():
    p = routes_zoom.parse("https://vt.tiktok.com/ZSabc/ review 60 duyệt")
    assert p["urls"] == ["https://vt.tiktok.com/ZSabc/"] and p["mode"] == "review" and p["duration"] == 60
    assert p["review"] and not p["download"]
    p = routes_zoom.parse("Tải giúp (https://youtu.be/xyz). split90")
    assert p["urls"] == ["https://youtu.be/xyz"] and p["download"] and p["long_mode"] == "split90" and p["duration"] == 0
    assert routes_zoom._plain("Huỷ") == routes_zoom._plain("hủy") == "huy"


# ---------------- Zoom: đường nhận tin ----------------

def _client(monkeypatch, tmp_path):
    from test_security import _client as web_client
    return web_client(monkeypatch, tmp_path, "pw-123")


def _event(cmd: str, user: str = "me@xmpp.zoom.us") -> bytes:
    return json.dumps({"event": "bot_notification", "event_ts": time.time(),
                       "payload": {"accountId": "acc1", "cmd": cmd, "robotJid": "bot@xmpp.zoom.us", "toJid": user,
                                   "userJid": user, "userId": "uid1", "userName": "Me"}}).encode()


def test_zoom_events_flow(zoomcfg, monkeypatch, tmp_path):
    pending: list = []
    monkeypatch.setattr(routes_zoom, "spawn", lambda coro: pending.append(coro))
    monkeypatch.setattr(routes_zoom, "_seen", {})

    async def ok_url(u):
        return u
    monkeypatch.setattr(routes_zoom.url_guard, "check_url_async", ok_url)

    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            body = _event("https://vt.tiktok.com/ZSabc/ 60")
            assert (await c.post("/zoom/events", data=body, headers={"Content-Type": "application/json"})).status == 401
            r = await c.post("/zoom/events", data=body, headers=_sign(body))
            assert r.status == 200
            for co in pending:
                await co
    asyncio.run(go())
    assert zoomcfg and "Đã nhận 1 video" in zoomcfg[-1][1] and "60 giây" in zoomcfg[-1][1]
    assert zoomcfg[-1][0] == {"to": "me@xmpp.zoom.us", "account": "acc1", "user": "me@xmpp.zoom.us"}
    assert zoom.targets()["me@xmpp.zoom.us"]["account"] == "acc1"         # nhớ nơi gửi cho thông báo sau này


def test_zoom_events_job_fields_and_commands(zoomcfg, monkeypatch, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    import web_panel
    from queue_manager import JobQueue
    pending: list = []
    monkeypatch.setattr(routes_zoom, "spawn", lambda coro: pending.append(coro))
    monkeypatch.setattr(routes_zoom, "_seen", {})

    async def ok_url(u):
        return u
    monkeypatch.setattr(routes_zoom.url_guard, "check_url_async", ok_url)
    monkeypatch.setattr(config, "WEB_PASSWORD", "pw-123")
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    cancelled: list = []
    app = web_panel.build_app(q, {"cancel": lambda i: cancelled.append(i) or f"huỷ {i}"})

    async def post(c, body):
        r = await c.post("/zoom/events", data=body, headers=_sign(body))
        for co in pending:
            await co
        pending.clear()
        return r

    async def go():
        async with TestClient(TestServer(app)) as c:
            body = _event("https://youtu.be/abc review duyệt")
            r = await post(c, body)
            assert r.status == 200
            # Zoom gửi lại đúng sự kiện đó (trả lời chậm): bỏ, không tạo job thứ hai
            assert (await (await c.post("/zoom/events", data=body, headers=_sign(body))).json()).get("duplicate")
            await post(c, _event("trạng thái"))
            await post(c, _event("id"))
            await post(c, _event("Huỳnh đây"))                 # tên người, không phải lệnh huỷ
            assert not cancelled
            await post(c, _event("Huỷ"))
            await post(c, _event("https://youtu.be/abc", user="stranger@xmpp.zoom.us"))
            v = b'{"event":"endpoint.url_validation","payload":{"plainToken":"abcDEF123456"}}'
            r = await c.post("/zoom/events", data=v, headers=_sign(v))
            assert (await r.json())["plainToken"] == "abcDEF123456"
    asyncio.run(go())
    jobs = list(q.jobs.values())
    assert len(jobs) == 1                                          # người lạ không tạo được job
    j = jobs[0]
    assert j.source == "zoom" and j.mode == "review" and j.review and j.zoom["account"] == "acc1"
    texts = [t for _, t in zoomcfg]
    assert any("Đã nhận 1 video (review" in t for t in texts)
    assert any(t.startswith(f"#{j.id}: đang chờ") for t in texts)
    assert any("JID Zoom của bạn: me@xmpp.zoom.us" in t for t in texts)
    assert cancelled == [j.id]
    assert any("🔒" in t and "stranger@xmpp.zoom.us" in t for t in texts)
    assert "stranger@xmpp.zoom.us" not in zoom.targets()               # người lạ không được ghi nhớ


def test_zoom_endpoint_off_without_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ZOOM_SECRET_TOKEN", "")

    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            assert (await c.post("/zoom/events", data=b"{}")).status == 503
    asyncio.run(go())


# ---------------- thông báo ----------------

def test_job_event_routes_by_source(monkeypatch):
    tg, zm, adm = [], [], []

    async def fake_tg(cid, text):
        tg.append((cid, text))

    async def fake_zoom(target, text, head="rmvideo"):
        zm.append((target, text))
        return "ok"

    async def fake_admins(text, head="rmvideo"):
        adm.append(text)
        return {}
    monkeypatch.setattr(state, "notify", fake_tg)
    monkeypatch.setattr(zoom, "notify", fake_zoom)
    monkeypatch.setattr(notifier, "admins", fake_admins)
    monkeypatch.setattr(config, "NOTIFY_JOBS", "1")

    class J:
        def __init__(self, source):
            self.source, self.chat_id, self.id, self.zoom = source, 42, 7, {"to": "t", "account": "a"}

    async def go():
        await notifier.job_event(J("tg"), "a")
        await notifier.job_event(J("zoom"), "b")
        await notifier.job_event(J("web"), "c")
        await notifier.job_event(J("auto"), "d", admin=False)       # tin chỉ có ích cho người đang nhắn: không báo
        monkeypatch.setattr(config, "NOTIFY_JOBS", "0")
        await notifier.job_event(J("web"), "e")
    asyncio.run(go())
    assert tg == [(42, "a")] and zm == [({"to": "t", "account": "a"}, "b")] and adm == ["c"]


def test_telegram_admin_explains_why(monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", "")
    assert "BOT_TOKEN" in asyncio.run(notifier.telegram_admin("x"))
    monkeypatch.setattr(config, "BOT_TOKEN", "123:abc")
    monkeypatch.setattr(state, "bot", None)
    assert "chưa chạy" in asyncio.run(notifier.telegram_admin("x"))

    class Bot:
        async def send_message(self, cid, text):
            raise RuntimeError("Telegram server says - Forbidden: bot can't initiate conversation with a user")
    monkeypatch.setattr(state, "bot", Bot())
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    monkeypatch.setattr(config, "ALLOWED_USERS", "")
    assert "ADMIN_CHAT_ID" in asyncio.run(notifier.telegram_admin("x"))
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "777")
    assert "/start" in asyncio.run(notifier.telegram_admin("x"))


def test_notify_test_endpoint_needs_login(monkeypatch, tmp_path):
    async def fake_test():
        return {"telegram": "ok"}
    monkeypatch.setattr(notifier, "test", fake_test)

    async def go():
        async with _client(monkeypatch, tmp_path) as c:
            assert (await c.post("/api/notify/test")).status == 401
            r = await c.post("/api/notify/test", headers={"X-Auth": "pw-123"})
            d = await r.json()
            assert r.status == 200 and d["telegram"] == "ok" and "zoom_inbound_ready" in d
            me = await (await c.get("/api/me")).json()
            assert "version" not in me                              # trang công khai không lộ phiên bản
            await c.post("/api/login", json={"password": "pw-123"})
            assert (await (await c.get("/api/me")).json())["version"] == VERSION
    asyncio.run(go())


def test_zoom_target_survives_review_and_pieces():
    src = job_factory.remake(url="https://youtu.be/x", mode="cook", voice="v", source="zoom",
                             zoom={"to": "t", "account": "a", "user": "u"})
    assert job_factory.approved(src, []).zoom == {"to": "t", "account": "a", "user": "u"}
    assert job_factory.piece(src, "/tmp/p.mp4", "x", {}).zoom["account"] == "a"
    assert job_factory.download(source="zoom", url="u", zoom={"to": "t"}).zoom == {"to": "t"}


def test_part_link(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example/")

    class J:
        id = 5
        parts = [{"path": "a.mp4", "token": "tok"}]
    assert notifier.part_link(J, J.parts[0]) == "https://bot.example/download/5/0?t=tok"
    assert notifier.part_link(J, {"token": "x"}) == ""
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    assert notifier.part_link(J, J.parts[0]) == ""


# ---------------- phiên bản ----------------

def test_version_is_semver_and_changelog_has_section():
    assert VERSION == "1.0.0"
    title, lines = release.notes(VERSION)
    assert title and 2 <= len(lines) <= release.MAX_LINES and all(not x.startswith("**") for x in lines)
    assert release.message().startswith(f"🆕 Bot đã lên phiên bản {VERSION}")


def test_notes_parser(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text("# T\n\n## 1.0.1 — sửa nhỏ (19/11/2026)\n\n- **Một** việc\n- `hai`\n\nĐoạn văn.\n- không lấy\n\n"
                 "## 1.0.0 — bản mới\n- cũ\n", encoding="utf-8")
    assert release.notes("1.0.1", str(p)) == ("sửa nhỏ (19/11/2026)", ["Một việc", "hai"])
    assert release.notes("1.0.0", str(p)) == ("bản mới", ["cũ"])
    assert release.notes("1.0.10", str(p)) == ("", [])


def test_announce_once_per_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("VERSION_STATE", str(tmp_path / "v.json"))
    calls = {"tg": 0, "zoom": 0}
    tg_result = {"v": "chưa đặt ADMIN_CHAT_ID"}

    async def fake_tg(text):
        calls["tg"] += 1
        return tg_result["v"]

    async def fake_zoom(target, text, head="rmvideo"):
        calls["zoom"] += 1
        return "ok"
    monkeypatch.setattr(notifier, "telegram_admin", fake_tg)
    monkeypatch.setattr(zoom, "notify", fake_zoom)
    monkeypatch.setattr(notifier, "zoom_in_use", lambda: True)
    assert asyncio.run(release.announce("9.9.9")) == {"telegram": "chưa đặt ADMIN_CHAT_ID", "zoom": "ok"}
    tg_result["v"] = "ok"
    assert asyncio.run(release.announce("9.9.9")) == {"telegram": "ok"}       # Zoom đã nhận: không báo lặp
    assert asyncio.run(release.announce("9.9.9")) == {}
    assert calls == {"tg": 2, "zoom": 1}


# ---------------- tông màu tự động ----------------

def test_pick_look_rules():
    assert autolook.pick({"luma": 130, "sat": 0.3, "warmth": -5})[0] == "am_moc"        # ám xanh / lạnh
    assert autolook.pick({"luma": 80, "sat": 0.3, "warmth": 20})[0] == "tuoi_sang"      # tối
    assert autolook.pick({"luma": 140, "sat": 0.1, "warmth": 9})[0] == "tuoi_sang"      # nhạt (video mực)
    assert autolook.pick({"luma": 110, "sat": 0.5, "warmth": 80})[0] == "tuoi_sang"     # đã ám vàng
    assert autolook.pick({"luma": 121, "sat": 0.23, "warmth": 30})[0] == "goc"          # ổn (video gà)


def test_auto_look_feeds_look_chain(monkeypatch):
    monkeypatch.setattr(config, "BRAND_LOOK", "auto")
    token = filtergraph.AUTO_LOOK.set("")
    try:
        assert filtergraph.look_name() == "tuoi_sang"                       # chưa chọn: mặc định trước 1.0.0
        filtergraph.AUTO_LOOK.set("am_moc")
        assert "colorbalance=rs=0.03" in filtergraph.look_chain("cook", 720, 1280)
        monkeypatch.setattr(config, "BRAND_LOOK", "dien_anh")
        assert filtergraph.look_name() == "dien_anh"                        # chọn tay: không tự đổi
    finally:
        filtergraph.AUTO_LOOK.reset(token)


def test_ensure_picks_once_and_notes(monkeypatch):
    monkeypatch.setattr(config, "BRAND_LOOK", "auto")
    calls = []
    monkeypatch.setattr(autolook, "frame_stats", lambda p, d, samples=8: calls.append(p) or
                        {"luma": 80, "sat": 0.3, "warmth": 20, "frames": 8})
    notes: list = []

    async def go():
        a = await autolook.ensure("x.mp4", 30, notes.append)
        b = await autolook.ensure("y.mp4", 30, notes.append)                # cùng job: không đo lại
        return a, b
    assert asyncio.run(go()) == ("tuoi_sang", "tuoi_sang") and calls == ["x.mp4"]
    assert notes == ["🎨 Tông màu tự chọn: Tươi sáng (video gốc hơi tối). "]
    monkeypatch.setattr(config, "BRAND_LOOK", "goc")
    assert asyncio.run(autolook.ensure("x.mp4", 30)) == ""


def test_frame_stats_on_real_frames(tmp_path):
    out = str(tmp_path / "blue.mp4")
    os.system(f"ffmpeg -v error -y -f lavfi -i color=c=0x3050a0:s=320x568:d=4 -pix_fmt yuv420p {out}")
    st = autolook.frame_stats(out, 4.0)
    assert st and st["warmth"] < autolook.COLD and autolook.pick(st)[0] == "am_moc"


def test_kit_migration_moves_old_default_look_to_auto(tmp_path, monkeypatch):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"BRAND_LOOK": "tuoi_sang", "QUALITY": "fast"}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(sf))
    for k in ("BRAND_LOOK", "QUALITY", *brand.OLD_DEFAULTS):
        monkeypatch.setattr(config, k, getattr(config, k))
    state_file = tmp_path / "brand_state.json"
    state_file.write_text(json.dumps({"kit": brand.KIT_ID}))               # máy đã chạy bản 43
    assert brand.apply_default_kit(str(state_file)) == ["BRAND_LOOK"]
    assert json.loads(sf.read_text()) == {"QUALITY": "fast"}
    assert brand.apply_default_kit(str(state_file)) == []                   # một lần
    sf.write_text(json.dumps({"BRAND_LOOK": "am_moc"}))
    state_file.write_text(json.dumps({"kit": brand.KIT_ID}))
    assert brand.apply_default_kit(str(state_file)) == [] and json.loads(sf.read_text()) == {"BRAND_LOOK": "am_moc"}


# ---------------- nhạc lót dưới lời ----------------

def test_tonal_score_music_vs_noise():
    rng = np.random.default_rng(0)
    t = np.arange(voicebed.SR * 20) / voicebed.SR
    noise = (rng.standard_normal(len(t)) * 0.1).astype(np.float32)
    notes = sum(np.sin(2 * np.pi * f * t) for f in (262, 330, 392)) * 0.1
    assert voicebed.tonal_score(noise) < 0.05
    assert voicebed.tonal_score((notes + 0.02 * noise).astype(np.float32)) > voicebed.MUSIC_SCORE


def test_build_filter_voice_bed_branches():
    base = filtergraph.build_filter("cook", 30, True, None, ambient_src="[2:a]")
    assert base == filtergraph.build_filter("cook", 30, True, None, ambient_src="[2:a]", voice_bed=None)
    fc = filtergraph.build_filter("cook", 30, True, None, ambient_src="[2:a]", voice_bed={"idx": 3, "gain_db": -12})
    assert "asplit=3[vo][sc][bk1]" in fc and "[3:a]aformat" in fc and "volume=-12.0dB" in fc
    assert "[vo][ambd][bedd]amix=inputs=3" in fc and "weights='1 0.5 1'" in fc
    ost = [(2.0, 4.0, 1.0)]
    fc = filtergraph.build_filter("review", 30, False, None, ost=ost, voice_bed={"idx": 2, "gain_db": -10})
    assert "[vo][ost][bedd]amix=inputs=3" in fc and "volume='1-" in fc.split("[bed0]")[0]
    fc = filtergraph.build_filter("review", 30, False, None, voice_bed={"idx": 2, "gain_db": -10})
    assert "[vo][bedd]amix=inputs=2" in fc
    # video không lời (lớp nhạc nền là tiếng chính): không lót thêm
    fc = filtergraph.build_filter("cook", 30, True, None, ambient_src="[2:a]", bed=True, voice_bed={"idx": 3})
    assert "[bed0]" not in fc


def test_bed_level_skips_when_source_has_music(monkeypatch, tmp_path):
    music = tmp_path / "m.wav"
    os.system(f"ffmpeg -v error -y -f lavfi -i sine=f=440:d=6 {music}")
    voice = tmp_path / "v.wav"
    os.system(f"ffmpeg -v error -y -f lavfi -i sine=f=200:d=6 -af volume=0.5 {voice}")
    notes: list = []
    monkeypatch.setattr(voicebed, "source_has_music", lambda p, s=None: True)
    bed = {"path": str(music), "trim": 0.0, "name": "Bài thử"}
    assert asyncio.run(renderer._bed_level(bed, str(voice), "orig.mp4", None, 6.0, notes)) is None
    assert "đã có nhạc" in notes[-1]
    monkeypatch.setattr(voicebed, "source_has_music", lambda p, s=None: False)
    lv = asyncio.run(renderer._bed_level(bed, str(voice), "orig.mp4", None, 6.0, notes))
    assert lv and -45 <= lv["gain_db"] <= 6 and "Bài thử" in notes[-1]
    assert asyncio.run(renderer._bed_level({"path": str(tmp_path / "none.mp3")}, str(voice), None, None, 6, None)) is None


def test_kaggle_spec_carries_bed_and_look(monkeypatch):
    import kaggle_worker
    from queue_manager import Job
    job = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="female")
    token = filtergraph.AUTO_LOOK.set("am_moc")
    try:
        spec = kaggle_worker.build_spec(job, [], 30.0, 0, 1.0, True, "t", [], None, {}, [], 90,
                                        voice_bed={"path": None, "url": "https://youtu.be/song", "trim": 4.0,
                                                   "name": "song"})
    finally:
        filtergraph.AUTO_LOOK.reset(token)
    assert spec["look"] == "am_moc"
    assert spec["voice_bed"] == {"music": {"url": "https://youtu.be/song", "kind": "audio", "max_duration": 600},
                                 "trim": 4.0, "name": "song"}
    assert not [k for k in spec["settings"] if k.startswith(("ZOOM_", "NOTIFY_"))]
