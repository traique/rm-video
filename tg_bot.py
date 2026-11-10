"""Bot Telegram: chỉ người trong ALLOWED_USERS dùng được; nhận link/file, chọn chế độ, thời lượng, duyệt kịch bản."""
import logging
import re

import config
import url_guard
from aiogram import BaseMiddleware, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from queue_manager import Job, QueueFull
from jobs import cancel_job
from review import REVIEW_HELP, approve_job, parse_review_reply
from state import queue


def setup(bot):
    """Đăng ký mọi handler lên một Dispatcher mới và trả về nó."""
    dp = Dispatcher()

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

    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())

    async def _guard_urls(m: Message, urls: list) -> list | None:
        """Kiểm từng link (nền tảng được hỗ trợ, không trỏ vào mạng nội bộ). Sai thì báo và trả None."""
        out = []
        for u in urls[:10]:
            try:
                out.append(await url_guard.check_url_async(u))
            except url_guard.BlockedURL as e:
                await m.answer(f"⛔ {e}")
                return None
        return out
    pending: dict[int, dict] = {}  # chat_id -> {"url":..., "urls": [...], "mode":...}

    MODE_KB = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍲 Nấu ăn (giữ ASMR + tăng màu)", callback_data="mode:cook")],
        [InlineKeyboardButton(text="📦 Review sản phẩm (đổi giọng)", callback_data="mode:review")],
        [InlineKeyboardButton(text="🎵 Nhảy/Nhạc (thay nhạc mới + khớp beat)", callback_data="mode:music")],
        [InlineKeyboardButton(text="⬇️ Chỉ tải về (TikTok không logo / FB / YouTube)", callback_data="mode:download")],
    ])
    DUR_KB = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 30 giây", callback_data="dur:30"),
         InlineKeyboardButton(text="⏱ 60 giây", callback_data="dur:60")],
        [InlineKeyboardButton(text="⏱ 90 giây", callback_data="dur:90"),
         InlineKeyboardButton(text="Giữ nguyên", callback_data="dur:0")],
        [InlineKeyboardButton(text="🎞 Video dài: remake nguyên (≤5 phút)", callback_data="dur:full")],
        [InlineKeyboardButton(text="✂️ Cắt phần 60s", callback_data="dur:split60"),
         InlineKeyboardButton(text="✂️ Cắt phần 90s", callback_data="dur:split90")],
        [InlineKeyboardButton(text="📝 Bật/tắt duyệt kịch bản trước khi render", callback_data="dur:review")],
    ])
    DLQ_KB = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Tốt nhất (TikTok không logo)", callback_data="dlq:best")],
        [InlineKeyboardButton(text="2160p", callback_data="dlq:2160"),
         InlineKeyboardButton(text="1080p", callback_data="dlq:1080"),
         InlineKeyboardButton(text="720p", callback_data="dlq:720")],
        [InlineKeyboardButton(text="480p", callback_data="dlq:480"),
         InlineKeyboardButton(text="360p", callback_data="dlq:360"),
         InlineKeyboardButton(text="🎵 MP3", callback_data="dlq:mp3")],
    ])

    @dp.callback_query(F.data.startswith("dlq:"))
    async def pick_dlq(cb: CallbackQuery):
        p = pending.pop(cb.message.chat.id, None)
        if not p:
            await cb.answer("Hãy gửi link trước nhé.", show_alert=True)
            return
        q = cb.data.split(":")[1]
        made, pos = 0, 0
        for u in (p.get("urls") or [p.get("url", "")]):
            job = Job(url=u, mode="download", duration=0, voice="", source="tg",
                      chat_id=cb.message.chat.id, quality="" if q == "best" else q)
            try:
                pos = queue.put(job)
                made += 1
            except QueueFull as e:
                await cb.message.edit_text(f"⚠️ {e}")
                await cb.answer()
                return
        await cb.message.edit_text(f"✅ Đã nhận {made} link, vị trí hàng đợi: #{pos}")
        await cb.answer()

    @dp.message(CommandStart())
    async def cmd_start(m: Message):
        await m.answer(
            "Gửi mình link video (TikTok/YouTube/...) để bắt đầu.\n"
            f"Video thường ≤ {config.MAX_DURATION}s; video dài: remake nguyên ≤ {config.LONG_MAX // 60} phút "
            f"hoặc cắt thành nhiều phần (nguồn ≤ {config.SPLIT_MAX // 60} phút). File gửi qua Telegram ≤ 20MB."
        )

    @dp.message(Command("cancel"))
    async def cmd_cancel(m: Message):
        mine = [j for j in queue.jobs.values() if j.chat_id == m.chat.id and j.status in ("queued", "processing")]
        if not mine:
            await m.answer("Không có job nào của bạn đang chạy.")
            return
        await m.answer("\n".join(cancel_job(j.id) for j in mine))

    # job chờ duyệt lấy từ hàng đợi (lưu SQLite) chứ không từ bộ nhớ riêng: bot khởi động lại vẫn duyệt được
    @dp.message(F.text & ~F.text.startswith("/"),
                lambda msg: queue.review_job_for(msg.chat.id) is not None and not re.search(r"https?://", msg.text or ""))
    async def review_reply(m: Message):
        job = queue.review_job_for(m.chat.id)
        if not job:
            await m.answer("Không còn job nào chờ duyệt.")
            return
        edits = {}
        if m.text.strip().lower() not in ("ok", "oke", "duyệt", "render", "được"):
            edits = parse_review_reply(m.text)
            if not edits:
                await m.answer(REVIEW_HELP)
                return
        try:
            new = approve_job(job, edits)
        except Exception as e:                       # job vẫn ở trạng thái chờ duyệt, gửi lại được
            await m.answer(f"⚠️ Chưa render được: {e}")
            return
        await m.answer(f"✅ Đã duyệt ({len(edits)} đoạn sửa), đang render ở job #{new.id}.")

    @dp.message(F.text.regexp(r"https?://\S+"))
    async def got_link(m: Message):
        p = pending.get(m.chat.id)
        urls = await _guard_urls(m, re.findall(r"https?://\S+", m.text))
        if not urls:
            return
        if p and p.get("need_music"):
            p["music_url"] = urls[0]
            p["need_music"] = False
            await m.answer("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)
            return
        pending[m.chat.id] = {"url": urls[0], "urls": urls}
        await m.answer(("Nhận %d link. " % len(urls) if len(urls) > 1 else "") + "Chọn chế độ xử lý:", reply_markup=MODE_KB)

    @dp.message(F.audio | F.voice)
    async def got_music(m: Message):
        p = pending.get(m.chat.id)
        if not p or not p.get("need_music"):
            await m.answer("Gửi link/file VIDEO trước, chọn chế độ 🎵 rồi mới gửi nhạc nhé.")
            return
        obj = m.audio or m.voice
        if obj.file_size and obj.file_size > 20 * 1024 * 1024:
            await m.answer("File nhạc ≤ 20MB nhé (giới hạn Bot API).")
            return
        p["music_file_id"] = obj.file_id
        p["need_music"] = False
        await m.answer("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)

    @dp.message(F.video | F.animation | F.document)
    async def got_file(m: Message):
        obj = m.video or m.animation or m.document
        p = pending.get(m.chat.id)
        if p and p.get("need_music") and m.document and (m.document.mime_type or "").startswith("audio/"):
            if obj.file_size and obj.file_size > 20 * 1024 * 1024:
                await m.answer("File nhạc ≤ 20MB nhé (giới hạn Bot API).")
                return
            p["music_file_id"] = obj.file_id
            p["need_music"] = False
            await m.answer("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)
            return
        if m.document and not (m.document.mime_type or "").startswith("video/"):
            await m.answer("File này không phải video.")
            return
        if obj.file_size and obj.file_size > 20 * 1024 * 1024:
            await m.answer("Bot Telegram chỉ tải được file ≤ 20MB.\nFile lớn hơn hãy dùng web panel (tới 80MB) nhé.")
            return
        pending[m.chat.id] = {"file_id": obj.file_id,
                              "title": m.caption or getattr(obj, "file_name", "") or "video tải lên"}
        await m.answer("📎 Đã nhận video đính kèm. Chọn chế độ xử lý:", reply_markup=MODE_KB)

    @dp.callback_query(F.data.startswith("mode:"))
    async def pick_mode(cb: CallbackQuery):
        p = pending.get(cb.message.chat.id)
        if not p:
            await cb.answer("Hãy gửi link video trước nhé.", show_alert=True)
            return
        p["mode"] = cb.data.split(":", 1)[1]
        if p["mode"] == "download":
            if p.get("url"):
                await cb.message.edit_text("Chọn chất lượng tải về:", reply_markup=DLQ_KB)
                await cb.answer()
                return
            pending.pop(cb.message.chat.id, None)
            job = Job(url="", mode="download", duration=0, voice="", source="tg",
                      chat_id=cb.message.chat.id, tg_file_id=p.get("file_id", ""), title_hint=p.get("title", ""))
            try:
                pos = queue.put(job)
                await cb.message.edit_text(f"✅ Đã nhận, vị trí hàng đợi: #{pos}")
            except QueueFull as e:
                await cb.message.edit_text(f"⚠️ {e}")
            await cb.answer()
            return
        if p["mode"] == "music":
            p["need_music"] = True
            await cb.message.edit_text("🎵 Gửi link nhạc mới (YouTube/TikTok...) hoặc gửi thẳng file nhạc (mp3/m4a ≤ 20MB):")
        else:
            await cb.message.edit_text("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)
        await cb.answer()

    @dp.callback_query(F.data.startswith("dur:"))
    async def pick_duration(cb: CallbackQuery):
        p = pending.pop(cb.message.chat.id, None)
        if not p or "mode" not in p or p.get("need_music"):
            await cb.answer("Hãy gửi link, chọn chế độ (và nhạc mới nếu chọn 🎵) trước nhé.", show_alert=True)
            return
        if config.TTS_PROVIDER == "capcut":
            voice = config.CAPCUT_VOICE_FEMALE if p["mode"] == "cook" else config.CAPCUT_VOICE_MALE
        else:
            voice = config.VOICE_FEMALE if p["mode"] == "cook" else config.VOICE_MALE
        choice = cb.data.split(":")[1]
        if choice == "review":
            pending[cb.message.chat.id] = p
            p["review"] = not p.get("review", False)
            await cb.answer(("Bật" if p["review"] else "Tắt") + " duyệt kịch bản. Giờ chọn thời lượng.", show_alert=True)
            return
        long_mode = choice if choice in ("full", "split60", "split90") else ""
        made, pos = 0, 0
        for u in (p.get("urls") or [p.get("url", "")]):
            job = Job(url=u, mode=p["mode"], duration=0 if long_mode else int(choice),
                      long_mode=long_mode, review=p.get("review", False),
                      voice=voice, source="tg", chat_id=cb.message.chat.id,
                      tg_file_id=p.get("file_id", ""), title_hint=p.get("title", ""),
                      music_url=p.get("music_url", ""), music_tg_file_id=p.get("music_file_id", ""))
            try:
                pos = queue.put(job)
                made += 1
            except QueueFull as e:
                await cb.message.edit_text(f"⚠️ {e}" + (f" (đã nhận {made})" if made else ""))
                await cb.answer()
                return
        await cb.message.edit_text(f"✅ Đã nhận {made} video, vị trí cuối trong hàng đợi: #{pos}")
        await cb.answer()
    return dp
