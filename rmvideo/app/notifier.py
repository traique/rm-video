"""Thông báo qua Telegram và Zoom (bản 1.0.0).

Trước 1.0.0 chỉ job gửi từ Telegram mới được báo (vào chính chat đó); job làm trên web hay tự đăng thì xong hay lỗi
đều im lặng, nên người dùng tưởng Telegram hỏng. Giờ:
- job từ Telegram: báo vào đúng chat đó như cũ;
- job từ Zoom: báo vào đúng cuộc trò chuyện Zoom (chỉ chữ + link tải, không gửi file video);
- job làm trên web / tự đăng: báo về kênh quản trị (Telegram: ADMIN_CHAT_ID hoặc người đầu tiên trong ALLOWED_USERS;
  Zoom: ZOOM_ADMIN hoặc người đầu tiên trong ZOOM_ALLOWED_USERS) khi NOTIFY_JOBS bật.
Gửi hỏng không bao giờ làm hỏng job; test() trả lý do cụ thể cho từng kênh để sửa cài đặt.
"""
from __future__ import annotations

import logging
from typing import Any

import config
import state
from rmvideo.services import zoom

_TG_HINTS = (
    ("bot was blocked", "người nhận đã chặn bot: mở bot trên Telegram, bỏ chặn rồi bấm /start"),
    ("can't initiate conversation", "người nhận chưa nhắn bot lần nào: mở bot trên Telegram và bấm /start"),
    ("chat not found", "chat id sai, hoặc người nhận chưa bấm /start với bot"),
    ("unauthorized", "BOT_TOKEN sai hoặc đã bị thu hồi (lấy lại ở @BotFather)"),
    ("forbidden", "Telegram từ chối gửi (bot bị chặn / chưa được thêm vào nhóm)"),
)


def explain_telegram(err: Exception | str) -> str:
    """Lỗi Telegram -> câu sửa được bằng tay."""
    low = str(err).lower()
    return next((hint for key, hint in _TG_HINTS if key in low), f"gửi lỗi: {str(err)[:150]}")


async def telegram_admin(text: str) -> str:
    """Tin Telegram tới chat quản trị. Trả "ok" hoặc lý do không gửi được."""
    if not config.BOT_TOKEN:
        return "chưa đặt BOT_TOKEN (biến môi trường trên Botkeep, đặt xong khởi động lại bot)"
    if not state.bot:
        return "bot Telegram chưa chạy (khởi động lại bot sau khi đặt BOT_TOKEN)"
    cid = config.admin_chat_id()
    if not cid:
        return "chưa đặt ADMIN_CHAT_ID hay ALLOWED_USERS (Cài đặt → 💬 Telegram & thông báo)"
    try:
        await state.bot.send_message(cid, text[:4000])
        return "ok"
    except Exception as e:
        logging.warning("Không gửi được tin Telegram tới %s: %s", cid, str(e)[:150])
        return explain_telegram(e)


def zoom_in_use() -> bool:
    return zoom.configured() or bool(zoom.allowed_ids()) or bool(config.ZOOM_ADMIN)


async def admins(text: str, head: str = "rmvideo") -> dict:
    """Gửi kênh quản trị (Telegram, và Zoom nếu đã cài). Trả {kênh: "ok" | lý do}."""
    out = {"telegram": await telegram_admin(text)}
    if zoom_in_use():
        out["zoom"] = await zoom.notify(zoom.admin_target(), text, head)
    bad = {k: v for k, v in out.items() if v != "ok"}
    if bad:
        logging.info("Thông báo quản trị chưa tới: %s", bad)
    return out


async def job_event(job: Any, text: str, admin: bool = True) -> None:
    """Báo người tạo job: Telegram / Zoom vào đúng cuộc trò chuyện đó; job web / tự đăng thì về kênh quản trị
    (admin=False: tin chỉ có ích cho người đang nhắn, vd «đang xử lý», «bot khởi động lại»)."""
    if job.source == "tg":
        await state.notify(job.chat_id, text)
    elif job.source == "zoom":
        await zoom.notify(getattr(job, "zoom", None) or {}, text, f"rmvideo · job #{job.id}")
    elif admin and config.NOTIFY_JOBS != "0":
        await admins(text, f"rmvideo · job #{job.id}")


def web_link(path: str = "") -> str:
    base = (config.PUBLIC_URL or "").rstrip("/")
    return f"{base}{path}" if base else ""


def part_link(job: Any, part: dict) -> str:
    """Link tải một file kết quả (mã riêng của file, không cần đăng nhập web); trống khi chưa đặt PUBLIC_URL."""
    try:
        k = job.parts.index(part)
    except ValueError:
        return ""
    return web_link(f"/download/{job.id}/{k}?t={part.get('token', '')}")


async def test() -> dict:
    """Nút «Gửi thử thông báo» trên web: gửi thật một tin, trả kết quả từng kênh."""
    return await admins("🔔 Thử thông báo từ rmvideo: nhận được tin này là thông báo đã chạy.")
