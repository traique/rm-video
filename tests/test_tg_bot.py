"""Bot Telegram chạy thật trên aiogram, chỉ thay lớp mạng bằng phiên giả ghi lại các lời gọi API."""
import asyncio
import datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import config
import state
import tg_bot
import url_guard
from queue_manager import Job


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True

    async def stream_content(self, *a, **kw):
        yield b""

    async def close(self):
        pass


def _msg(uid, text, mid=1):
    return Message(message_id=mid, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                   from_user=User(id=uid, is_bot=False, first_name="A"), text=text)


def _cb(uid, data):
    return CallbackQuery(id="c1", from_user=User(id=uid, is_bot=False, first_name="A"), chat_instance="x", data=data,
                         message=_msg(uid, "menu", 2))


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USERS", "111")
    monkeypatch.setattr(config, "MAX_QUEUE", 1000)          # hàng đợi dùng chung với test khác có thể đã đầy

    async def ok(u):
        return u
    monkeypatch.setattr(url_guard, "check_url_async", ok)
    session = FakeSession()
    bot = Bot("42:TEST-TOKEN", session=session)
    dp = tg_bot.setup(bot)
    upd = {"n": 0}

    async def feed(obj):
        upd["n"] += 1
        kw = {"message": obj} if isinstance(obj, Message) else {"callback_query": obj}
        await dp.feed_update(bot, Update(update_id=upd["n"], **kw))
        return [type(c).__name__ + ":" + str(getattr(c, "text", "") or "") for c in session.calls]
    return feed, session


def test_stranger_is_refused_with_chat_id(tg):
    feed, session = tg
    calls = asyncio.run(feed(_msg(999, "https://www.tiktok.com/@a/video/1")))
    assert any("999" in c and "ALLOWED_USERS" in c for c in calls)
    assert not any(j.chat_id == 999 for j in state.queue.jobs.values())


def test_link_mode_duration_creates_job(tg):
    feed, session = tg
    before = {id(j) for j in state.queue.jobs.values()}      # so theo đối tượng: id job có thể trùng giữa các test

    async def flow():
        await feed(_msg(111, "https://www.tiktok.com/@a/video/123"))
        await feed(_cb(111, "mode:cook"))
        await feed(_cb(111, "dur:30"))
    asyncio.run(flow())
    new = [j for j in state.queue.jobs.values() if id(j) not in before]
    assert len(new) == 1 and new[0].mode == "cook" and new[0].duration == 30 and new[0].chat_id == 111


def test_review_reply_approves_with_ost(tg):
    feed, session = tg
    src = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="", status="review", source="tg", chat_id=111,
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "text": "Một", "ost": "new"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "text": "Hai", "ost": "new"}])
    state.queue.jobs[src.id] = src
    before = set(map(id, state.queue.jobs.values()))
    asyncio.run(feed(_msg(111, "1: gốc")))
    assert src.status == "approved"
    # job mới = job vừa thêm (không lấy theo số lớn nhất: test khác dựng JobQueue mới làm bộ đếm số job quay về 1)
    new = [j for j in state.queue.jobs.values() if id(j) not in before]
    assert len(new) == 1 and [s["ost"] for s in new[0].approved] == ["new", "orig"]


def test_polling_retries_instead_of_crashing(monkeypatch):
    import main
    calls = {"n": 0}

    class FakeDp:
        async def start_polling(self, bot, handle_signals=True):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("Telegram không truy cập được")

    asyncio.run(main.poll_forever(FakeDp(), object(), first_wait=0.01))
    assert calls["n"] == 3


def test_polling_stops_on_signal_during_retry_wait():
    import main

    class DownDp:
        async def start_polling(self, bot, handle_signals=True):
            raise ConnectionError("mất mạng")

        async def stop_polling(self):
            raise RuntimeError("chưa chạy")

    async def go():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.2, stop.set)
        await asyncio.wait_for(main.poll_forever(DownDp(), object(), stop, first_wait=30), 5)   # không chờ đủ 30s
    asyncio.run(go())


def _photo(uid, fid, mid, group="g1", caption=None):
    from aiogram.types import PhotoSize
    return Message(message_id=mid, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                   from_user=User(id=uid, is_bot=False, first_name="A"), media_group_id=group, caption=caption,
                   photo=[PhotoSize(file_id=fid + "_s", file_unique_id=fid + "s", width=90, height=160),
                          PhotoSize(file_id=fid, file_unique_id=fid, width=1080, height=1920)])


def test_photo_album_music_duration_creates_job(tg):
    feed, session = tg
    before = {id(j) for j in state.queue.jobs.values()}      # so theo đối tượng: id job có thể trùng giữa các test

    async def flow():
        for k in range(5):                                   # album 5 ảnh: chỉ lấy 4
            await feed(_photo(111, f"f{k}", 10 + k, caption="Đà Lạt" if k == 0 else None))
        await feed(_msg(111, "https://www.tiktok.com/@a/video/9", 20))
        await feed(_cb(111, "pdur:15"))
    asyncio.run(flow())
    new = [j for j in state.queue.jobs.values() if id(j) not in before]
    assert len(new) == 1
    j = new[0]
    assert j.mode == "photo" and j.tg_images == ["f0", "f1", "f2", "f3"] and j.duration == 15
    assert j.music_url.endswith("/video/9") and j.title_hint == "Đà Lạt"


def test_music_mode_background_choice(tg):
    feed, session = tg
    before = {id(j) for j in state.queue.jobs.values()}      # so theo đối tượng: id job có thể trùng giữa các test

    async def flow():
        await feed(_msg(111, "https://www.tiktok.com/@a/video/1"))
        await feed(_cb(111, "mode:music"))
        await feed(_msg(111, "https://www.tiktok.com/@b/video/2", 3))
        await feed(_cb(111, "mix:bg"))
        await feed(_cb(111, "dur:30"))
    asyncio.run(flow())
    new = [j for j in state.queue.jobs.values() if id(j) not in before]
    assert len(new) == 1 and new[0].mode == "music" and new[0].music_mix == "bg" and new[0].duration == 30


def test_stale_buttons_do_not_hijack_photo_flow(tg):
    feed, session = tg
    before = {id(j) for j in state.queue.jobs.values()}

    async def flow():
        await feed(_photo(111, "fA", 30, group="g9"))
        await feed(_cb(111, "mode:download"))               # nút cũ của luồng video
        await feed(_cb(111, "dlq:720"))
        await feed(_msg(111, "https://www.tiktok.com/@a/video/5", 31))
        await feed(_cb(111, "mode:cook"))
        await feed(_cb(111, "dur:30"))
        await feed(_cb(111, "pdur:0"))
    asyncio.run(flow())
    new = [j for j in state.queue.jobs.values() if id(j) not in before]
    assert len(new) == 1 and new[0].mode == "photo" and new[0].tg_images == ["fA"]
