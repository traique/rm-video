"""🎵 Gói đăng TikTok bán tự động (1.2.0): caption, lịch gửi, Telegram (file + tin chữ riêng), Zoom (chữ + link), lỗi."""
import asyncio

import pytest

import config
import prompt_engine
from rmvideo.app import tiktok_kit

NOW = 1_800_000_000.0
W = NOW + 3 * 3600                         # giờ vàng (giờ hẹn Fanpage)


class FakeBot:
    def __init__(self, fail: bool = False):
        self.calls: list = []
        self.fail = fail

    async def send_video(self, cid, f, caption="", **kw):
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.calls.append(("video", cid, caption, kw.get("supports_streaming")))

    async def send_message(self, cid, text, **kw):
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.calls.append(("text", cid, text))


@pytest.fixture
def kit(monkeypatch, tmp_path):
    import state
    from rmvideo.app import notifier
    monkeypatch.setattr(tiktok_kit, "STATE_FILE", str(tmp_path / "tk.json"))
    monkeypatch.setattr(config, "BOT_TOKEN", "123:abc", raising=False)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "555")
    monkeypatch.setattr(config, "TIKTOK_KIT", "slot")
    monkeypatch.setattr(config, "KIDS_TIKTOK_KIT", "slot")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example")
    monkeypatch.setattr(notifier, "zoom_in_use", lambda: False)
    bot = FakeBot()
    monkeypatch.setattr(state, "bot", bot)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 1000)

    class Job:
        id = 9
        post = prompt_engine.platform_post("Canh chua cá lóc", "Chua ngọt đậm đà miền Tây", "canhchua, monngon",
                                           "Nguồn: @bepnha")
        parts = [{"label": "Video", "path": str(vid), "token": "tok1"}]
    return bot, Job, str(vid)


def test_caption_is_tiktok_section():
    post = prompt_engine.platform_post("Bò kho", "Thơm lừng, mềm rục", "bokho, monngon", "Nguồn: @a")
    c = tiktok_kit.caption(post, "fb")
    assert c.startswith("Thơm lừng, mềm rục #bokho") and "Nguồn: @a" in c and "▶" not in c and "#Shorts" not in c
    assert tiktok_kit.caption("", "chữ Fanpage") == "chữ Fanpage"


def test_slot_mode_sends_video_then_caption_before_golden_hour(kit):
    bot, Job, vid = kit
    assert tiktok_kit.after_facebook("cook", Job, vid, "fb desc", W, now=NOW)
    assert not tiktok_kit.after_facebook("cook", Job, vid, "fb desc", W, now=NOW)         # một lần mỗi job
    it = tiktok_kit.load_state()["items"][0]
    assert it["send_at"] == W - tiktok_kit.LEAD and it["link"] == "https://bot.example/download/9/0?t=tok1"
    assert asyncio.run(tiktok_kit.tick(now=W - tiktok_kit.LEAD - 60)) == "" and bot.calls == []   # chưa tới giờ
    msg = asyncio.run(tiktok_kit.tick(now=W - tiktok_kit.LEAD))
    assert "đã gửi gói video #9" in msg
    (k1, cid, head, stream), (k2, cid2, text) = bot.calls
    assert (k1, cid, stream, k2, cid2) == ("video", 555, True, "text", 555)
    assert "kênh bếp" in head and "đăng lúc" in head
    assert text == tiktok_kit.caption(Job.post) and text.startswith("Chua ngọt đậm đà")          # tin riêng: chỉ chữ đăng
    assert tiktok_kit.load_state()["items"][0]["status"] == "sent"
    assert asyncio.run(tiktok_kit.tick(now=W)) == "" and len(bot.calls) == 2                   # không gửi lại
    assert "đã gửi 1" in tiktok_kit.status_lines("cook", now=W)[0]


def test_ready_mode_off_mode_and_big_file(kit, monkeypatch):
    bot, Job, vid = kit
    monkeypatch.setattr(config, "KIDS_TIKTOK_KIT", "ready")
    monkeypatch.setattr(config, "TG_LIMIT", 10)                          # file lớn hơn giới hạn Telegram: gửi link
    assert tiktok_kit.after_facebook("kids", Job, vid, "d", W, now=NOW)
    assert tiktok_kit.load_state()["items"][0]["send_at"] == NOW
    asyncio.run(tiktok_kit.tick(now=NOW))
    assert [c[0] for c in bot.calls] == ["text", "text"]
    assert "TikTok Studio" in bot.calls[0][2] and "download/9/0" in bot.calls[0][2]
    monkeypatch.setattr(config, "TIKTOK_KIT", "off")
    assert not tiktok_kit.after_facebook("cook", Job, vid, "d", W, now=NOW)
    assert "tắt" in tiktok_kit.status_lines("cook")[0]


def test_retry_when_nobody_receives_then_give_up(kit, monkeypatch):
    import state
    bot, Job, vid = kit
    monkeypatch.setattr(state, "bot", FakeBot(fail=True))
    assert tiktok_kit.after_facebook("cook", Job, vid, "d", W, now=NOW)
    t = W - tiktok_kit.LEAD
    for k in range(tiktok_kit.MAX_TRIES):
        msg = asyncio.run(tiktok_kit.tick(now=t))
        t += tiktok_kit.RETRY
    it = tiktok_kit.load_state()["items"][0]
    assert it["status"] == "failed" and "bỏ chặn" in it["why"] and "không gửi được" in msg


def test_zoom_gets_text_and_link(kit, monkeypatch):
    import state
    from rmvideo.app import notifier
    from rmvideo.services import zoom
    bot, Job, vid = kit
    monkeypatch.setattr(state, "bot", None)                              # chỉ có Zoom
    monkeypatch.setattr(notifier, "zoom_in_use", lambda: True)
    monkeypatch.setattr(zoom, "admin_target", lambda: {"to": "a"})
    sent = []

    async def notify(target, text, head="rmvideo"):
        sent.append((target, text, head))
        return "ok"
    monkeypatch.setattr(zoom, "notify", notify)
    assert tiktok_kit.after_facebook("cook", Job, vid, "d", None, now=NOW)        # Fanpage đăng ngay: gửi ngay
    msg = asyncio.run(tiktok_kit.tick(now=NOW))
    assert "(zoom)" in msg and sent[0][0] == {"to": "a"}
    assert "download/9/0?t=tok1" in sent[0][1] and "Chua ngọt đậm đà" in sent[0][1]


def test_stale_and_missing_file_not_sent(kit, tmp_path):
    bot, Job, vid = kit
    assert tiktok_kit.after_facebook("cook", Job, vid, "d", W, now=NOW)
    asyncio.run(tiktok_kit.tick(now=W + tiktok_kit.STALE + 1))                     # bot tắt lâu, quá giờ vàng
    assert tiktok_kit.load_state()["items"][0]["status"] == "failed" and bot.calls == []

    class Job2(Job):
        id = 10
    assert tiktok_kit.after_facebook("cook", Job2, str(tmp_path / "khong.mp4"), "d", None, now=NOW)
    asyncio.run(tiktok_kit.tick(now=NOW))
    assert tiktok_kit.load_state()["items"][1]["status"] == "failed" and bot.calls == []


def test_kids_finish_pending_queues_youtube_and_tiktok(monkeypatch, kit, tmp_path):
    import kids_auto
    import state
    from rmvideo.app import youtube
    bot, Job, vid = kit
    monkeypatch.setattr(youtube, "STATE_FILE", str(tmp_path / "yt.json"))
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "kids.json"))

    class KJob:
        id, status, error, out_dur = 77, "done", "", 70
        post = prompt_engine.platform_post("Thỏ Trắng và bóng bay", "Truyện cho bé", "kebenghe")
        parts = [{"label": "Video", "path": vid, "token": "t"}]
        output = vid

    async def publish(*a, **k):
        return "FBK", "reel"

    async def notify(*a, **k):
        return None
    import autopost
    monkeypatch.setattr(autopost, "publish", publish)
    monkeypatch.setattr(state, "notify_admin", notify)
    monkeypatch.setattr(state.queue, "jobs", {77: KJob})
    st = kids_auto.load_state()
    st["pending"] = {"job": 77, "slot": "19:00", "slot_ts": W}
    assert "FBK" in asyncio.run(kids_auto._finish_pending(st, NOW))
    it = tiktok_kit.load_state()["items"][0]
    assert (it["key"], it["send_at"], it["caption"].startswith("Truyện cho bé")) == ("kids:77", W - tiktok_kit.LEAD, True)
    assert youtube.load_state()["items"] == []                     # YouTube kênh bé chưa kết nối: không xếp


def test_storage_keeps_files_waiting_for_tiktok_or_youtube(monkeypatch, kit, tmp_path):
    """OUT_MAX_MB chỉ giữ ~10 video mà gói TikTok có thể chờ hơn một ngày tới giờ vàng: file chờ không bị dọn."""
    import os
    import time

    import storage
    from rmvideo.app import youtube
    bot, Job, vid = kit
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(storage, "OUT_DIR", str(out))
    monkeypatch.setattr(config, "OUT_MAX_MB", 1)
    monkeypatch.setattr(youtube, "STATE_FILE", str(tmp_path / "yt.json"))
    files = []
    for k in range(4):                                     # 4 file 600KB, cũ nhất trước
        f = out / f"out_{k}_0.mp4"
        f.write_bytes(b"x" * 600_000)
        os.utime(f, (time.time() - 1000 + k, time.time() - 1000 + k))
        files.append(str(f))
    Job.parts = [{"label": "Video", "path": files[0], "token": "t"}]
    assert tiktok_kit.after_facebook("cook", Job, files[0], "d", W, now=NOW)      # file cũ nhất chờ giờ vàng
    st = youtube.load_state()
    st["items"].append({"key": "cook:5", "ch": "cook", "job": 5, "path": files[1], "status": "pending", "created": time.time()})
    st["items"].append({"key": "cook:6", "ch": "cook", "job": 6, "path": files[2], "status": "pending",
                        "created": time.time() - youtube.PENDING_MAX_AGE - 10})          # chờ quá lâu: không giữ
    youtube.save_state(st)
    storage._cleanup_outputs({files[3]})
    assert [os.path.exists(f) for f in files] == [True, True, False, True]
