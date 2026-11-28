"""Dựng Dispatcher của bot Telegram: kiểm quyền (ALLOWED_USERS) rồi đăng ký handler."""
from typing import Any

from aiogram import Dispatcher

from rmvideo.interfaces.telegram import handlers
from rmvideo.interfaces.telegram.access import AccessMiddleware, StaleButtonMiddleware


def setup(bot: Any) -> Dispatcher:
    """Đăng ký mọi handler lên một Dispatcher mới và trả về nó."""
    dp = Dispatcher()
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.callback_query.middleware(StaleButtonMiddleware())      # sau kiểm quyền: nút trên tin nhắn đã cũ
    handlers.register(dp, bot)
    return dp
