"""Trạng thái dùng chung của tiến trình bot: hàng đợi, bot Telegram, job đang chạy, thư mục; gửi tin an toàn."""
import asyncio
import logging
import os
from typing import Any

import config
from queue_manager import JobQueue

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "outputs")
WORK_DIR = os.path.join(BASE_DIR, "work")      # thư mục tạm trong ổ của bot (khi /tmp chật)
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

queue = JobQueue()
bot: Any = None                                # aiogram.Bot khi có BOT_TOKEN (main gán lúc khởi động)
current: dict = {"job": None, "task": None}    # job worker đang chạy + task của nó (để huỷ)

async def notify(chat_id: int, text: str):
    """Gửi tin Telegram KHÔNG BAO GIỜ ném lỗi: trước đây mạng lỗi lúc báo "❌ Lỗi" làm chết luôn worker,
    hàng đợi kẹt cho tới khi khởi động lại."""
    if not (bot and chat_id):
        return
    try:
        await bot.send_message(chat_id, text[:4000])
    except Exception as e:
        logging.warning("Không gửi được tin Telegram tới %s: %s", chat_id, str(e)[:120])


_TASKS: set = set()


def spawn(coro):
    """create_task có giữ tham chiếu: task không giữ tham chiếu có thể bị gom rác giữa chừng (tài liệu asyncio)."""
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


async def notify_admin(text: str):
    """Cảnh báo quản trị (tự đăng dừng, Kaggle lỗi...): Telegram và, từ bản 1.0.0, cả Zoom (ngang hàng Telegram).
    Không bao giờ ném lỗi; kênh nào chưa gửi được thì notifier ghi lý do vào log."""
    from rmvideo.app import notifier
    try:
        await notifier.admins(text)
    except Exception as e:                                  # cảnh báo hỏng không được làm hỏng việc đang chạy
        logging.warning("Không gửi được cảnh báo: %s | %s", str(e)[:120], text[:200])
