"""Bot Telegram: chỉ người trong ALLOWED_USERS dùng được; nhận link/file, chọn chế độ, thời lượng, duyệt kịch bản."""
import asyncio
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
        [InlineKeyboardButton(text="✨ Đoạn hay nhất (AI chọn ≤3 đoạn 30-90s)", callback_data="dur:highlight")],
        [InlineKeyboardButton(text="📝 Bật/tắt duyệt kịch bản trước khi render", callback_data="dur:review")],
    ])
    MIX_KB = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Thay hẳn nhạc + khớp beat điệu nhảy", callback_data="mix:beat")],
        [InlineKeyboardButton(text="🎶 Làm nhạc nền (giữ tiếng gốc video)", callback_data="mix:bg")],
    ])
    PHOTO_KB = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Tự động theo nhạc (~12-16s)", callback_data="pdur:0")],
        [InlineKeyboardButton(text="10s", callback_data="pdur:10"), InlineKeyboardButton(text="15s", callback_data="pdur:15"),
         InlineKeyboardButton(text="30s", callback_data="pdur:30"), InlineKeyboardButton(text="60s", callback_data="pdur:60")],
    ])
    PHOTO_ASK_MUSIC = ("🎵 Gửi nhạc cho video: link TikTok (bot lấy bản nhạc gốc của video) / YouTube, "
                       "hoặc gửi thẳng file nhạc (mp3/m4a ≤ 20MB).")
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
        if not p or p.get("mode") == "photo":
            if p:
                pending[cb.message.chat.id] = p             # nút cũ của luồng khác: giữ nguyên ảnh đã gửi
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
            f"hoặc cắt thành nhiều phần (nguồn ≤ {config.SPLIT_MAX // 60} phút). File gửi qua Telegram ≤ 20MB.\n"
            "📸 Ảnh + nhạc: gửi 1-4 ảnh (một album hoặc từng ảnh), rồi gửi nhạc: bot dựng video có hiệu ứng và "
            "chuyển cảnh theo nhịp nhạc."
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

    async def _after_music(m: Message, p: dict):
        """Đã có nhạc: ảnh + nhạc -> chọn thời lượng; nhảy/nhạc -> chọn cách dùng nhạc (thay hẳn / nhạc nền)."""
        p["need_music"] = False
        if p.get("mode") == "photo":
            await m.answer(f"📸 {len(p['images'])} ảnh + nhạc. Chọn độ dài video:", reply_markup=PHOTO_KB)
        else:
            await m.answer("Dùng nhạc này thế nào?", reply_markup=MIX_KB)

    async def _ack_photos(chat_id: int, p: dict, delay: float = 1.2):
        """Album ảnh tới thành nhiều tin liền nhau: chờ hết rồi mới trả lời MỘT lần."""
        await asyncio.sleep(delay)
        if pending.get(chat_id) is p and p.get("need_music"):
            n = len(p["images"])
            await bot.send_message(chat_id, f"📸 Đã nhận {n} ảnh" + (" (tối đa 4, bỏ bớt ảnh thừa)" if p.get("over") else "")
                                   + ". " + PHOTO_ASK_MUSIC)

    @dp.message(F.photo | (F.document & F.document.mime_type.startswith("image/")))
    async def got_photo(m: Message):
        if m.document and m.document.file_size and m.document.file_size > 20 * 1024 * 1024:
            await m.answer("Ảnh ≤ 20MB nhé (giới hạn Bot API).")
            return
        fid = m.photo[-1].file_id if m.photo else m.document.file_id
        p = pending.get(m.chat.id)
        if not (p and p.get("mode") == "photo" and p.get("need_music")):
            p = {"mode": "photo", "images": [], "need_music": True, "title": ""}
            pending[m.chat.id] = p
        if len(p["images"]) < 4:
            p["images"].append(fid)
        else:
            p["over"] = True
        if m.caption and not p["title"]:
            p["title"] = m.caption[:200]
        old = p.get("ack")
        if old and not old.done():
            old.cancel()
        p["ack"] = asyncio.create_task(_ack_photos(m.chat.id, p))

    @dp.callback_query(F.data.startswith("mix:"))
    async def pick_mix(cb: CallbackQuery):
        p = pending.get(cb.message.chat.id)
        if not p or p.get("mode") != "music":
            await cb.answer("Hãy gửi video, chọn chế độ 🎵 và gửi nhạc trước nhé.", show_alert=True)
            return
        p["music_mix"] = "bg" if cb.data == "mix:bg" else ""
        await cb.message.edit_text("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)
        await cb.answer()

    @dp.callback_query(F.data.startswith("pdur:"))
    async def pick_photo_duration(cb: CallbackQuery):
        p = pending.get(cb.message.chat.id)
        if not p or p.get("mode") != "photo" or p.get("need_music") or not p.get("images"):
            await cb.answer("Hãy gửi 1-4 ảnh rồi gửi nhạc trước nhé.", show_alert=True)
            return
        pending.pop(cb.message.chat.id, None)
        job = Job(url="", mode="photo", duration=int(cb.data.split(":")[1]), voice="", source="tg",
                  chat_id=cb.message.chat.id, tg_images=list(p["images"]), title_hint=p.get("title", ""),
                  music_url=p.get("music_url", ""), music_tg_file_id=p.get("music_file_id", ""))
        try:
            pos = queue.put(job)
            await cb.message.edit_text(f"✅ Đã nhận {len(p['images'])} ảnh + nhạc, vị trí hàng đợi: #{pos}")
        except QueueFull as e:
            await cb.message.edit_text(f"⚠️ {e}")
        await cb.answer()

    @dp.message(F.text.regexp(r"https?://\S+"))
    async def got_link(m: Message):
        p = pending.get(m.chat.id)
        urls = await _guard_urls(m, re.findall(r"https?://\S+", m.text))
        if not urls:
            return
        if p and p.get("need_music"):
            p["music_url"] = urls[0]
            await _after_music(m, p)
            return
        pending[m.chat.id] = {"url": urls[0], "urls": urls}
        await m.answer(("Nhận %d link. " % len(urls) if len(urls) > 1 else "") + "Chọn chế độ xử lý:", reply_markup=MODE_KB)

    @dp.message(F.audio | F.voice)
    async def got_music(m: Message):
        p = pending.get(m.chat.id)
        if not p or not p.get("need_music"):
            await m.answer("Gửi link/file VIDEO (chọn chế độ 🎵) hoặc 1-4 ẢNH trước, rồi mới gửi nhạc nhé.")
            return
        obj = m.audio or m.voice
        if obj.file_size and obj.file_size > 20 * 1024 * 1024:
            await m.answer("File nhạc ≤ 20MB nhé (giới hạn Bot API).")
            return
        p["music_file_id"] = obj.file_id
        await _after_music(m, p)

    @dp.message(F.video | F.animation | F.document)
    async def got_file(m: Message):
        obj = m.video or m.animation or m.document
        p = pending.get(m.chat.id)
        if p and p.get("need_music") and m.document and (m.document.mime_type or "").startswith("audio/"):
            if obj.file_size and obj.file_size > 20 * 1024 * 1024:
                await m.answer("File nhạc ≤ 20MB nhé (giới hạn Bot API).")
                return
            p["music_file_id"] = obj.file_id
            await _after_music(m, p)
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
        if not p or p.get("mode") == "photo":               # nút cũ không được biến luồng ảnh thành job video
            await cb.answer("Hãy gửi link video trước nhé." if not p else
                            "Bạn đang gửi ảnh: gửi nhạc rồi chọn độ dài video nhé.", show_alert=True)
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
            await cb.message.edit_text("🎵 Gửi link nhạc mới (link video TikTok: bot lấy bản nhạc gốc của video; YouTube...) "
                                       "hoặc gửi thẳng file nhạc (mp3/m4a ≤ 20MB):")
        else:
            await cb.message.edit_text("Chọn thời lượng video đầu ra:", reply_markup=DUR_KB)
        await cb.answer()

    @dp.callback_query(F.data.startswith("dur:"))
    async def pick_duration(cb: CallbackQuery):
        p = pending.pop(cb.message.chat.id, None)
        if not p or "mode" not in p or p.get("need_music") or p["mode"] == "photo":
            if p and p.get("mode") == "photo":
                pending[cb.message.chat.id] = p             # nút cũ của luồng khác: giữ nguyên ảnh đã gửi
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
        long_mode = choice if choice in ("full", "split60", "split90", "highlight") else ""
        made, pos = 0, 0
        for u in (p.get("urls") or [p.get("url", "")]):
            job = Job(url=u, mode=p["mode"], duration=0 if long_mode else int(choice),
                      long_mode=long_mode, review=p.get("review", False),
                      voice=voice, source="tg", chat_id=cb.message.chat.id,
                      tg_file_id=p.get("file_id", ""), title_hint=p.get("title", ""),
                      music_url=p.get("music_url", ""), music_tg_file_id=p.get("music_file_id", ""),
                      music_mix=p.get("music_mix", "") if p["mode"] == "music" else "")
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
