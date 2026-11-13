"""Bản 36 — mục Tự động đăng tự quản kênh nguồn (nhiều kênh Facebook/TikTok) và hàng chờ link dán tay; cài đặt gom
nhóm với nhãn dễ hiểu."""
import asyncio
import random
from datetime import datetime

import pytest

import autopost
import config
import web_panel


@pytest.fixture(autouse=True)
def _cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(autopost, "STATE_FILE", str(tmp_path / "ap.json"))
    monkeypatch.setattr(autopost, "_POSTED", {})
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))     # không đụng settings.json thật
    for k, v in (("AUTO_POST", "1"), ("AUTO_SOURCES", ""), ("AUTO_TIKTOK_SOURCES", ""), ("AUTO_PLATFORMS", ""),
                 ("AUTO_PER_DAY", "2"), ("AUTO_TIMES", ""), ("AUTO_MODE", "cook"), ("FB_PAGE_ID", "111"),
                 ("FB_PAGE_TOKEN", "tok"), ("TTS_PROVIDER", "edge")):
        monkeypatch.setattr(config, k, v)


def _ts(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=autopost.VN).timestamp()


def test_add_and_remove_sources_many_channels():
    assert autopost.add_source("https://www.facebook.com/duynauan/reels") == ("facebook", "https://www.facebook.com/duynauan")
    assert autopost.add_source("m.facebook.com/bepnha") == ("facebook", "https://www.facebook.com/bepnha")
    assert autopost.add_source("https://www.tiktok.com/@quetrai?lang=vi") == ("tiktok", "https://www.tiktok.com/@quetrai")
    assert autopost.add_source("tiktok.com/@bep.que") == ("tiktok", "https://www.tiktok.com/@bep.que")
    autopost.add_source("https://www.facebook.com/duynauan")                 # thêm lại: không nhân đôi
    assert autopost.sources() == ["https://www.facebook.com/duynauan", "https://www.facebook.com/bepnha"]
    assert autopost.tiktok_sources() == ["https://www.tiktok.com/@quetrai", "https://www.tiktok.com/@bep.que"]
    for bad in ("https://www.youtube.com/@abc", "chữ bất kỳ", "https://www.tiktok.com/@quetrai/video/123"):
        with pytest.raises(ValueError):
            autopost.add_source(bad)
    assert autopost.remove_source("https://www.facebook.com/bepnha/videos") and autopost.sources() == ["https://www.facebook.com/duynauan"]
    assert not autopost.remove_source("https://www.facebook.com/khongco") and not autopost.remove_source("rác")
    assert autopost.remove_source("@quetrai") is False                      # không có tên miền: không đoán nền tảng
    assert autopost.remove_source("https://www.tiktok.com/@quetrai") and autopost.tiktok_sources() == ["https://www.tiktok.com/@bep.que"]
    assert autopost.detect_platform("https://fb.watch/abc") == "facebook" and autopost.detect_platform("") == ""


def _fake_probe(monkeypatch, infos):
    import core_video

    async def probe(url, td):
        if url not in infos:
            raise RuntimeError("không xem được")
        return infos[url]
    monkeypatch.setattr(core_video, "probe_formats", probe)
    monkeypatch.setattr(autopost.url_guard, "check_url_async", _identity)


async def _identity(u):
    return u


def test_queue_link_validates_and_keeps_order(monkeypatch):
    v1, v2 = "https://www.tiktok.com/@quetrai/video/7301", "https://www.facebook.com/reel/900000001/"
    _fake_probe(monkeypatch, {v1: {"duration": 58, "title": "Cá kho tộ"}, v2: {"duration": 40, "title": "Gà chiên"},
                              "https://youtu.be/long": {"duration": 900, "title": "dài"},
                              "https://youtu.be/short": {"duration": 3, "title": "ngắn"}})
    m = asyncio.run(autopost.queue_link(v1))
    assert m["title"] == "Cá kho tộ" and m["dur"] == 58 and m["key"].startswith("m:")
    asyncio.run(autopost.queue_link(v2))
    assert [x["url"] for x in autopost.load_state()["manual"]] == [v1, v2]
    for bad, why in ((v1, "đã có"), ("https://youtu.be/long", "dài"), ("https://youtu.be/short", "ngắn")):
        with pytest.raises(ValueError, match=why):
            asyncio.run(autopost.queue_link(bad))
    with pytest.raises(RuntimeError):                                       # link hỏng: báo ngay, không xếp
        asyncio.run(autopost.queue_link("https://youtu.be/khong"))
    assert "Link bạn dán đang chờ (2" in autopost.status_text() and "Cá kho tộ" in autopost.status_text()
    assert asyncio.run(autopost.remove_link(v2)) and not asyncio.run(autopost.remove_link(v2))
    assert [x["url"] for x in autopost.load_state()["manual"]] == [v1]
    # video đã làm trước đó (trùng tiêu đề + độ dài) thì từ chối
    st = autopost.load_state()
    st["used"]["tt:1"] = {"at": 1, "job": 5, "plat": "tiktok", "dur": 40, "title": autopost._norm_title("Gà chiên")}
    autopost.save_state(st)
    with pytest.raises(ValueError, match="đã làm trước đó"):
        asyncio.run(autopost.queue_link(v2))


def test_tick_takes_pasted_link_before_random_video(monkeypatch):
    import state
    monkeypatch.setattr(config, "AUTO_SOURCES", "duynauan")
    monkeypatch.setattr(config, "AUTO_PLATFORMS", "facebook")
    monkeypatch.setattr(autopost, "EXPLORE", 0.0)
    monkeypatch.setattr(autopost, "list_channel_sync", lambda src: (["100000001"], "777"))
    link = "https://www.tiktok.com/@khac/video/7777"
    _fake_probe(monkeypatch, {link: {"duration": 58, "title": "Cá kho tộ", "uploader": "khac"},
                              "https://www.facebook.com/reel/100000001/": {"duration": 60, "uploader_id": "777", "title": "Món"}})
    queued = []
    monkeypatch.setattr(state.queue, "put", lambda job, force=False: queued.append(job) or 1)
    monkeypatch.setattr(state.queue, "pending", 0)
    asyncio.run(autopost.queue_link(link))
    msg = asyncio.run(autopost.tick(now=_ts(12, "09:00"), rnd=random.Random(3)))
    assert msg.startswith("job #") and [j.url for j in queued] == [link]      # link dán đi trước kênh nguồn
    assert queued[0].source == "auto" and queued[0].title_hint == "Cá kho tộ"
    st = autopost.load_state()
    assert st["manual"] == [] and st["pending"]["vid"].startswith("m:") and st["pending"]["plat"] == "tiktok"
    assert st["used"][st["pending"]["vid"]]["source"] == "link dán tay"
    # job xong -> hẹn đăng như video thường; lượt sau mới lấy video ngẫu nhiên từ kênh
    job = queued[0]
    monkeypatch.setattr(state.queue, "jobs", {job.id: job})
    import tempfile
    out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    out.write(b"x" * 100)
    out.close()
    job.status, job.parts, job.out_dur, job.post = "done", [{"label": "Video", "path": out.name}], 58.0, "📌 Cá kho\nnd"
    sent = []

    async def fake_publish(path, title, desc, dur, when, thumb="", session=None):
        sent.append((title, when))
        return "R1", "reel"
    monkeypatch.setattr(autopost, "publish", fake_publish)
    msg = asyncio.run(autopost.tick(now=_ts(12, "09:05"), rnd=random.Random(3)))
    assert sent and sent[0][0] == "Cá kho" and msg.startswith("job #") and queued[1].url.startswith("https://www.facebook.com/reel/")


def test_pasted_link_works_without_channels_and_without_free_slots(monkeypatch):
    import state
    link = "https://www.facebook.com/reel/900000002/"
    _fake_probe(monkeypatch, {link: {"duration": 30, "title": "Bún riêu"}})
    queued = []
    monkeypatch.setattr(state.queue, "put", lambda job, force=False: queued.append(job) or 1)
    monkeypatch.setattr(state.queue, "pending", 0)
    assert "chưa có kênh nguồn" in asyncio.run(autopost.tick(now=_ts(12, "09:00")))
    asyncio.run(autopost.queue_link(link))
    monkeypatch.setattr(autopost, "free_slots", lambda st, now, rnd: [])        # hết khung trống vẫn làm ngay
    msg = asyncio.run(autopost.tick(now=_ts(12, "09:00"), rnd=random.Random(1)))
    assert msg.startswith("job #") and [j.url for j in queued] == [link]
    assert autopost.load_state()["pending"]["slot"] == "09:00"
    # link hỏng lúc tới lượt: báo admin, bỏ qua, không kẹt
    monkeypatch.setattr(autopost, "free_slots", lambda st, now, rnd: [(_ts(12, "20:30"), "20:30")])
    st = autopost.load_state()
    st["pending"] = None
    st["manual"] = [{"url": "https://www.facebook.com/reel/1/", "title": "x", "dur": 10, "key": "m:1"}]
    autopost.save_state(st)
    notes = []

    async def note(text):
        notes.append(text)
    monkeypatch.setattr(state, "notify_admin", note)
    msg = asyncio.run(autopost.tick(now=_ts(12, "10:00"), rnd=random.Random(1)))
    assert msg.startswith("chưa có kênh nguồn") and autopost.load_state()["manual"] == [] and "không xem được" in notes[0]


def test_web_autopost_sources_and_queue(monkeypatch, tmp_path):
    from test_security import _client
    _fake_probe(monkeypatch, {"https://www.tiktok.com/@quetrai/video/7301": {"duration": 58, "title": "Cá kho tộ"}})

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            h = {"X-Auth": "pw-123"}
            r = await c.post("/api/autopost/source", headers=h, json={"url": "https://www.tiktok.com/@quetrai"})
            d = await r.json()
            assert d["text"].startswith("✅") and d["sources"] == {"facebook": [], "tiktok": ["https://www.tiktok.com/@quetrai"]}
            d = await (await c.post("/api/autopost/source", headers=h, json={"url": "facebook.com/duynauan"})).json()
            assert d["sources"]["facebook"] == ["https://www.facebook.com/duynauan"] and "Kênh Facebook: https://www.facebook.com/duynauan" in d["status"]
            d = await (await c.post("/api/autopost/source", headers=h, json={"url": "https://youtube.com/@x"})).json()
            assert d["text"].startswith("❌") and len(d["sources"]["facebook"]) == 1
            d = await (await c.post("/api/autopost/source", headers=h, json={"url": "https://www.facebook.com/duynauan", "delete": True})).json()
            assert d["sources"]["facebook"] == []
            d = await (await c.post("/api/autopost/queue", headers=h, json={"url": "https://www.tiktok.com/@quetrai/video/7301"})).json()
            assert d["text"].startswith("✅ Đã xếp «Cá kho tộ»") and d["manual"] == [{"url": "https://www.tiktok.com/@quetrai/video/7301", "title": "Cá kho tộ", "dur": 58}]
            d = await (await c.get("/api/autopost", headers=h)).json()
            assert len(d["manual"]) == 1 and d["sources"]["tiktok"] == ["https://www.tiktok.com/@quetrai"]
            d = await (await c.post("/api/autopost/queue", headers=h, json={"url": "https://www.tiktok.com/@quetrai/video/7301", "delete": True})).json()
            assert d["manual"] == [] and d["text"].startswith("✅")
            assert (await c.post("/api/autopost/queue", headers={**h, "Content-Type": "application/json"}, data=b"x")).status == 400
    asyncio.run(go())


def test_settings_meta_covers_every_key_and_api_groups(monkeypatch, tmp_path):
    ids = [g["id"] for g in config.setting_groups()]
    assert ids[0] == "api" and len(ids) == len(set(ids))
    missing = [k for k in config.EDITABLE if k not in config.META]
    assert missing == [], missing
    for k, (group, label, help_, link, need) in config.META.items():
        assert k in config.EDITABLE and group in ids and label and need in ("", "must", "should", "optional")
        assert not link or link.startswith("https://")
    api = [k for k, m in config.META.items() if m[0] == "api"]
    assert set(api) >= {"GEMINI_API_KEY", "KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "FB_PAGE_ID", "FB_PAGE_TOKEN", "PUBLIC_URL"}
    assert all(k in config.META and config.META[k][0] == "api" for k in config.SECRET_KEYS)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIzaSy-abcdefghijklmnop")
    d = config.describe_settings()
    assert d["GEMINI_API_KEY"]["need"] == "must" and d["GEMINI_API_KEY"]["set"] and "•••" in d["GEMINI_API_KEY"]["value"]
    assert d["GEMINI_API_KEY"]["link"].startswith("https://aistudio") and d["QUALITY"]["group"] == "video"
    from test_security import _client

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            r = await c.get("/api/settings", headers={"X-Auth": "pw-123"})
            d = await r.json()
            assert r.status == 200 and [g["id"] for g in d["groups"]] == ids and d["fields"]["TTS_PROVIDER"]["options"]
            assert d["fields"]["TTS_PROVIDER"]["label"] == "Nguồn giọng đọc"
    asyncio.run(go())
    html = open(web_panel.INDEX_HTML, encoding="utf-8").read()
    assert "auto_src_add" in html and "auto_link_add" in html and "d.fields" in html
