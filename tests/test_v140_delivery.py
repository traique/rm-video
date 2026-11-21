"""1.4.0: nội dung đăng luôn gửi kèm qua Telegram / Zoom; notebook Kaggle giữ đúng slug (tiêu đề theo slug)."""
import asyncio
import os

import kaggle_api
import state
from queue_manager import Job
from rmvideo.app import notifier
from rmvideo.app.flow import delivery

POST = "📌 Đồ chua giòn\n\n▶ TikTok:\n" + "Đồ chua giòn sần sật " * 40 + "\n\n▶ Facebook:\n" + "Cà rốt củ cải " * 60


def test_chunks_cut_at_line_breaks():
    parts = delivery.chunks(POST, 1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) >= 2
    assert "".join(parts).replace(" ", "").replace("\n", "") == POST.replace(" ", "").replace("\n", "")
    assert delivery.chunks("ngắn") == ["ngắn"] and delivery.chunks("") == []


def _files(tmp_path):
    work = tmp_path / "w"
    os.makedirs(work, exist_ok=True)
    (work / "output.mp4").write_bytes(b"x" * 10)
    os.makedirs(tmp_path / "out", exist_ok=True)
    return str(work / "output.mp4")


def test_telegram_long_post_goes_in_its_own_messages(tmp_path, monkeypatch):
    sent, texts = [], []

    class Bot:
        async def send_video(self, chat_id, video, caption="", thumbnail=None, **k):
            sent.append(caption)

    async def notify(chat_id, text):
        texts.append(text)
    monkeypatch.setattr(state, "bot", Bot())
    monkeypatch.setattr(delivery, "notify", notify)
    monkeypatch.setattr(delivery, "OUT_DIR", str(tmp_path / "out"))
    job = Job(url="", mode="cook", duration=0, voice="v", source="tg", chat_id=1)
    asyncio.run(delivery.deliver(job, _files(tmp_path), "Video", POST))
    assert len(sent[0]) <= 1024 and "nội dung đăng ở tin bên dưới" in sent[0]          # trước đây: POST cắt ở 1024
    assert texts and all(t.startswith(f"📝 Nội dung đăng (job #{job.id})") for t in texts)
    assert "Cà rốt củ cải" in texts[-1] and "▶ Facebook:" in "".join(texts)
    sent.clear(), texts.clear()
    job2 = Job(url="", mode="cook", duration=0, voice="v", source="tg", chat_id=1)
    asyncio.run(delivery.deliver(job2, _files(tmp_path), "Video", "📌 Ngắn gọn #a"))   # vừa chú thích: như cũ
    assert sent == ["📌 Ngắn gọn #a"] and texts == []


def test_web_auto_zoom_jobs_get_the_post_too(tmp_path, monkeypatch):
    events = []

    async def job_event(job, text, admin=True):
        events.append((job.source, text))
    monkeypatch.setattr(notifier, "job_event", job_event)
    monkeypatch.setattr(delivery, "OUT_DIR", str(tmp_path / "out"))
    for src in ("auto", "zoom", "web"):
        events.clear()
        job = Job(url="", mode="kids", duration=0, voice="v", source=src)
        asyncio.run(delivery.deliver(job, _files(tmp_path), "Video", POST + "\n\n" + POST + "\n\n" + POST))
        assert events[0][1].startswith(f"✅ Video job #{job.id} xong") and "📝" not in events[0][1]
        assert len(events) >= 3 and all("📝 Nội dung đăng" in t and len(t) <= 3800 for _, t in events[1:])
        assert "(1/" in events[1][1]
    events.clear()
    job = Job(url="", mode="download", duration=0, voice="", source="web")
    asyncio.run(delivery.deliver(job, _files(tmp_path), "Video", "Tên video"))     # chỉ tải về: không có bài đăng
    assert len(events) == 1


def test_push_keeps_slug_and_follows_kaggle_ref(monkeypatch):
    calls = []
    reply = {"ref": "/code/quetrai/chua-gi-n-s-n-s-t", "url": "https://www.kaggle.com/code/quetrai/chua-gi-n-s-n-s-t",
             "versionNumber": 41}

    def call(method, body, service=None, timeout=120):
        calls.append((method, body))
        return reply if method == "SaveKernel" else {"status": "RUNNING"}
    monkeypatch.setattr(kaggle_api, "call", call)
    monkeypatch.setattr(kaggle_api, "user", lambda: "quetrai")
    kaggle_api.push("rmvideo-worker", "ĐỒ CHUA GIÒN SẦN SẬT", "print(1)")
    assert calls[0][1]["newTitle"] == "rmvideo worker" and calls[0][1]["slug"] == "quetrai/rmvideo-worker"
    # Kaggle vẫn trả slug khác (notebook cũ đã bị đổi tên): hỏi trạng thái theo slug đó, không 404
    assert kaggle_api.status("rmvideo-worker") == ("RUNNING", "")
    assert calls[-1][1]["kernelSlug"] == "chua-gi-n-s-n-s-t"
    reply.update(ref="quetrai/rmvideo-worker", url="")
    kaggle_api.push("rmvideo-worker", "x", "print(1)")
    kaggle_api.status("rmvideo-worker")
    assert calls[-1][1]["kernelSlug"] == "rmvideo-worker"
    assert kaggle_api._ref_slug({}) == "" and kaggle_api.title_for("rmvideo-kids") == "rmvideo kids"
