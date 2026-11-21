"""Chỉ người trong ALLOWED_USERS dùng được bot (chat_id hoặc user_id); người lạ được báo chat id của họ."""
import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import config


class AccessMiddleware(BaseMiddleware):
    """Chỉ người trong ALLOWED_USERS (chat_id hoặc user_id) mới dùng được bot: người lạ tìm ra bot
    không tiêu được quota Gemini/Groq và CPU. Người lạ được báo chat id để chủ bot thêm vào nếu muốn."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        allowed = config.allowed_ids()
        if (user and user.id in allowed) or (chat and chat.id in allowed):
            return await handler(event, data)
        uid = user.id if user else (chat.id if chat else 0)
        logging.warning("Từ chối người dùng Telegram không có trong ALLOWED_USERS: %s", uid)
        if isinstance(event, CallbackQuery):
            await event.answer("🔒 Bạn không có quyền dùng bot này.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer(f"🔒 Bot này chỉ phục vụ người được phép.\nChat id của bạn: {uid}\n"
                               "Chủ bot thêm số này vào ALLOWED_USERS (Cài đặt trên web) để mở quyền.")
        return None


class StaleButtonMiddleware(BaseMiddleware):
    """Nút bấm trên tin nhắn đã cũ (Telegram trả InaccessibleMessage sau ~48 giờ) hoặc không kèm tin nhắn: báo người
    dùng gửi lại thay vì để handler lỗi im lặng khi sửa tin. Sau middleware này, handler callback luôn có
    `cb.message` là Message và `cb.data` là chuỗi."""
    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery) and (not isinstance(event.message, Message) or event.data is None):
            await event.answer("Nút này đã cũ, hãy gửi lại link / file nhé.", show_alert=True)
            return None
        return await handler(event, data)
