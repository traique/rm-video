import asyncio
import json
import logging
import os
import re
import time
import shutil
import sys
import tempfile

from aiohttp import web

import config
import core_video
import healthcheck
import metrics
import ytdlp_update
import beat
import cache
import scenes
import ingredients
import vocal_sep
import prompt_engine
import url_guard
from queue_manager import Job, JobQueue, QueueFull
from transcribe import transcribe_segments
import subs as subs_mod
from web_panel import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S")
for _noisy in ("google_genai", "google_genai.models", "httpx", "aiogram.event"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)      # bỏ dòng "AFC is enabled..." và log mỗi request

queue = JobQueue()
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

bot = None
if config.BOT_TOKEN:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    from aiogram import BaseMiddleware

    bot = Bot(config.BOT_TOKEN)
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

    from aiogram.filters import Command

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
            for line in m.text.splitlines():            # "3: lời mới" -> sửa đoạn 3
                mm = re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
                if mm:
                    edits[int(mm.group(1))] = mm.group(2).strip()
            if not edits:
                await m.answer("Trả lời \"ok\" để render, hoặc gửi các dòng dạng \"số: lời mới\" để sửa đoạn đó.")
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


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(BASE_DIR, "work")      # thư mục tạm trong ổ của bot (khi /tmp chật)


def _free_mb(path: str) -> int:
    try:
        return shutil.disk_usage(path).free >> 20
    except Exception:
        return 0


def _size_mb(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path) >> 20
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total >> 20


def disk_report() -> str:
    """Ổ bot + /tmp còn trống bao nhiêu, thư mục nào chiếm chỗ (in lúc khởi động và lệnh status)."""
    tmp = tempfile.gettempdir()
    parts = [f"ổ bot trống {_free_mb(BASE_DIR)}MB", f"{tmp} trống {_free_mb(tmp)}MB"]
    for name in ("outputs", "uploads_in", "work", "cache", "models", "potoken", "tools"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            parts.append(f"{name} {_size_mb(p)}MB")
    return " | ".join(parts)


def make_room(need_mb: int, keep: set = frozenset()) -> int:
    """Xóa video kết quả cũ nhất (và file tải lên mồ côi) tới khi ổ bot trống ≥ need_mb. Trả về MB trống."""
    busy = {j.local for j in queue.jobs.values() if j.status in ("queued", "processing", "review") and j.local}
    for f in sorted((os.path.join(config.UPLOAD_DIR, x) for x in os.listdir(config.UPLOAD_DIR)), key=os.path.getmtime):
        if f not in busy and time.time() - os.path.getmtime(f) > 3600:
            os.remove(f)
    if _free_mb(BASE_DIR) < need_mb:
        cache.prune(max_mb=0)              # cache tạo lại được, video kết quả thì không: bỏ cache trước
    for f in sorted((os.path.join(OUT_DIR, x) for x in os.listdir(OUT_DIR)), key=os.path.getmtime):
        if _free_mb(BASE_DIR) >= need_mb:
            break
        if f not in keep:
            logging.info("Dọn chỗ: xóa %s (%dMB)", os.path.basename(f), _size_mb(f))
            os.remove(f)
    return _free_mb(BASE_DIR)


def pick_workdir(need_mb: int) -> str | None:
    """Chọn nơi đặt file tạm của job: /tmp hay work/ trong ổ bot, chỗ nào đủ rộng. Thiếu cả hai thì dọn ổ bot."""
    tmp = tempfile.gettempdir()
    if _free_mb(tmp) >= need_mb:
        return tmp
    os.makedirs(WORK_DIR, exist_ok=True)
    if _free_mb(BASE_DIR) >= need_mb or make_room(need_mb + 150) >= need_mb:   # +150: chừa chỗ cho file kết quả
        return WORK_DIR
    return None


def _cleanup_outputs(keep: set = frozenset()):
    """Giữ video mới nhất cho web tải về, xóa cũ khi tổng vượt OUT_MAX_MB (ổ Botkeep chỉ 2GB)."""
    files = sorted((os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR)), key=os.path.getmtime, reverse=True)
    total = 0
    for f in files:
        total += os.path.getsize(f)
        if total > config.OUT_MAX_MB * 1024 * 1024 and f not in keep:
            os.remove(f)


async def deliver(job: Job, path: str, label: str, caption: str):
    """Giao 1 file kết quả: giữ cho web tải về; Telegram gửi thẳng nếu ≤48MB, lớn hơn thì gửi link."""
    import secrets as _secrets
    ext = os.path.splitext(path)[1] or ".mp4"
    if ext == ".mp3" and label == "Video":
        label = "MP3"
    keep = os.path.join(OUT_DIR, f"out_{job.id}_{len(job.parts)}{ext}")
    await asyncio.to_thread(shutil.move, path, keep)       # /tmp -> ổ bot có thể là chép thật, không chặn vòng lặp
    queue.log_source(job, keep, label)
    part = {"label": label, "path": keep, "token": _secrets.token_urlsafe(16)}
    job.parts.append(part)
    job.output = keep                       # tương thích: nút tải chính = file mới nhất
    _cleanup_outputs({p["path"] for p in job.parts})
    if job.source != "tg" or not bot:
        return
    size = os.path.getsize(keep)
    link = (f"{config.PUBLIC_URL.rstrip('/')}/download/{job.id}/{len(job.parts) - 1}?t={part['token']}"
            if config.PUBLIC_URL else "")
    where = f"Tải tại: {link}" if link else "Mở web panel để tải (đặt PUBLIC_URL trong Cài đặt để nhận link ở đây)."
    if size <= config.TG_LIMIT:
        from aiogram.types import FSInputFile
        try:
            if ext == ".mp3":
                await bot.send_audio(job.chat_id, FSInputFile(keep), caption=caption[:1024], request_timeout=600)
            else:
                await bot.send_video(job.chat_id, FSInputFile(keep), caption=caption[:1024], request_timeout=600)
            return
        except Exception as e:                  # video đã làm xong: lỗi gửi không được biến job thành lỗi
            logging.warning("Gửi file qua Telegram lỗi: %s", e)
            await notify(job.chat_id, f"🎬 {label} xong nhưng gửi qua Telegram lỗi ({str(e)[:80]}).\n{where}"
                                      f"\n\n{caption[:800]}")
            return
    await notify(job.chat_id, f"🎬 {label}: file {size / 1048576:.0f}MB vượt giới hạn 50MB của Telegram.\n{where}"
                              f"\n\n{caption[:800]}")


def check_source_allowed(job: Job):
    """ALLOWED_SOURCES (Cài đặt): chỉ remake video của các kênh đã được phép (kênh của bạn, kênh đã cấp phép).
    Trống = không giới hạn. So khớp không phân biệt hoa thường với uploader / uploader_id."""
    allowed = {x.strip().lstrip("@").lower() for x in config.ALLOWED_SOURCES.split(",") if x.strip()}
    if not allowed or job.mode == "download":
        return
    meta = job.source_meta or {}
    names = {str(meta.get(k) or "").lstrip("@").lower() for k in ("uploader", "uploader_id")} - {""}
    if not names & allowed:
        raise RuntimeError(f"Kênh nguồn «{meta.get('uploader') or '?'}» không nằm trong danh sách ALLOWED_SOURCES "
                           "(Cài đặt). Chỉ remake video của kênh đã được phép.")


async def process_split(job: Job, tmp: str):
    """Chế độ ✂️: cắt video dài (≤10 phút) thành các phần ~60/90s tại chỗ ngưng nói/chuyển cảnh,
    remake từng phần, xong phần nào giao phần đó."""
    part_len = 90 if job.long_mode == "split90" else 60
    if job.local:
        src = job.local                       # dùng file tải lên tại chỗ (không chép), worker xoá khi job xong
        title = job.title_hint
    elif job.tg_file_id:
        job.step = "Tải file từ Telegram"
        src = os.path.join(tmp, "source.mp4")
        await bot.download(job.tg_file_id, destination=src)
        title = job.title_hint
    else:
        job.step = "Tải video dài"
        src, title, _, job.source_meta = await core_video.download(job.url, tmp, max_duration=config.SPLIT_MAX)
        check_source_allowed(job)
    dur = min(await core_video.probe_duration(src), config.SPLIT_MAX)
    job.step = "Tìm điểm cắt (chỗ ngưng nói + chuyển cảnh)"
    bounds = scenes.split_points(dur, part_len, await scenes.detect_cuts(src), await core_video.silences(src))
    n = len(bounds) - 1
    ok, fails = 0, []
    for i in range(n):
        a, b = bounds[i], bounds[i + 1]
        label = f"Phần {i + 1}/{n}"
        part_dir = os.path.join(tmp, f"p{i}")
        os.makedirs(part_dir, exist_ok=True)
        piece = os.path.join(part_dir, "piece.mp4")
        job.step = f"{label}: cắt {a:.0f}s-{b:.0f}s"
        await core_video.cut(src, piece, a, b)
        sub = Job(url="", mode=job.mode, duration=0, voice=job.voice, subs=job.subs, cover=job.cover,
                  local=piece, title_hint=f"{title} ({label})", source=job.source, chat_id=job.chat_id)
        sub.id = job.id

        async def _mirror(s=sub, lb=label):           # hiện tiến độ của phần đang làm lên job cha
            while True:
                if s.step:
                    job.step = f"{lb}: {s.step}"
                await asyncio.sleep(2)
        mirror = asyncio.create_task(_mirror())
        try:
            if job.mode == "download":
                out = piece                            # chỉ cắt, không remake
                caption = f"{title} ({label})"
            else:
                out = await process(sub, part_dir)
                caption = (sub.post or sub.script or "").replace("📌 ", f"📌 [{label}] ", 1)
            await deliver(job, out, label, caption)
            ok += 1
        except Exception as e:
            logging.exception("%s thất bại", label)
            fails.append(f"{label}: {str(e)[:120]}")
            if job.source == "tg" and bot:
                await bot.send_message(job.chat_id, f"❌ {label} lỗi: {e}")
        finally:
            mirror.cancel()
            for d in os.listdir(part_dir):            # giải phóng ổ đĩa ngay sau mỗi phần
                p = os.path.join(part_dir, d)
                if os.path.isfile(p):
                    os.remove(p)
    job.script = f"Đã xong {ok}/{n} phần (mỗi phần ~{part_len}s, cắt tại chỗ ngưng nói/chuyển cảnh)."
    if fails:
        job.error = "; ".join(fails)
    if ok == 0:
        raise RuntimeError("Không phần nào thành công. " + "; ".join(fails))


def credit_line(job: Job) -> str:
    """'🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI' cho nội dung đăng (tắt bằng SOURCE_CREDIT=0)."""
    if config.SOURCE_CREDIT == "0":
        return ""
    parts = []
    uploader = (job.source_meta or {}).get("uploader")
    if uploader:
        parts.append(f"🎬 Nguồn: {uploader}")
    if job.mode != "music":
        parts.append("🔊 Giọng đọc AI")
    return " · ".join(parts)


SEP_MAX_SPEECH = 45      # giây có lời tối đa để tách giọng AI (máy 1 lõi ~1 giây xử lý / giây audio)
SEP_MAX_VIDEO = 120      # và video không dài quá 2 phút


def promo_cut(job: Job, segs: list, out_dur: float) -> float | None:
    """Mốc cắt đoạn quảng cáo cuối của người gốc. Bản đã duyệt dùng lại ĐÚNG mốc đã quyết lúc viết kịch bản
    (kịch bản duyệt không có đoạn đó; trước đây bản duyệt render lại nguyên đoạn quảng cáo — lỗi N1)."""
    if job.approved:
        return job.cut_at or None
    if job.mode in ("cook", "review") and config.CUT_PROMO != "0":
        return prompt_engine.find_promo_tail(segs, out_dur)
    return None


async def process(job: Job, tmp: str):
    if job.local:
        # dùng file tại chỗ (file tải lên, hoặc nguồn đã giữ lại lúc chờ duyệt): không chép/chuyển; bot chết
        # giữa chừng thì file còn nguyên để chạy lại; worker xoá file khi job xong (trừ job chờ duyệt)
        job.step = "Dùng file nguồn đã có" if job.approved else "Nhận file tải lên"
        inp = job.local
        title, desc = job.title_hint, ""
    elif job.tg_file_id:
        job.step = "Tải file từ Telegram"
        inp = os.path.join(tmp, "input.mp4")
        await bot.download(job.tg_file_id, destination=inp)
        title, desc = job.title_hint, ""
    elif job.mode == "download" and job.quality:
        lbl = "MP3" if job.quality == "mp3" else (job.quality + "p" if job.quality.isdigit() else "tốt nhất")
        job.step = f"Tải {lbl}..."

        def _pct(p, lbl=lbl):
            job.step = f"Tải {lbl}... {p}%"
        inp, title = await core_video.download_format(job.url, tmp, job.quality, _pct)
        desc = ""
    elif job.mode == "download":
        job.step = "Tải video gốc (không logo)"
        inp, title, desc, job.source_meta = await core_video.download(job.url, tmp, max_duration=600)
    else:
        job.step = "Tải video (yt-dlp)"
        inp, title, desc, job.source_meta = await core_video.download(
            job.url, tmp, max_duration=config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION)
        check_source_allowed(job)

    if job.mode == "download":
        if inp.endswith(".mp3"):
            out = inp
        else:
            out = os.path.join(tmp, "output.mp4")
            await core_video.remux(inp, out)
        dur = await core_video.probe_duration(out)
        job.script = f"Tải xong: {dur:.0f}s, {os.path.getsize(out) // 1024 // 1024}MB"
        job.post = title or ""
        return out

    if job.mode == "music":
        job.step = "Tải nhạc mới"
        if job.music_tg_file_id:
            music = os.path.join(tmp, "music.m4a")
            await bot.download(job.music_tg_file_id, destination=music)
        elif job.music_local:
            music = job.music_local
        elif job.music_url:
            music = await core_video.download_audio(job.music_url, tmp)
        else:
            raise RuntimeError("Chế độ 🎵 cần link hoặc file nhạc mới.")

        job.step = "Dò beat & khớp nhịp"
        try:
            bpm_v, beat_v = await beat.analyze(inp)
            bpm_m, beat_m = await beat.analyze(music)
            sync = beat.compute_sync(bpm_v, beat_v, bpm_m, beat_m)
            job.script = (f"Khớp beat: nhảy {bpm_v:.0f} BPM ↔ nhạc {bpm_m:.0f} BPM "
                          f"→ tốc độ video x{sync['speed']:.2f}")
        except Exception as e:
            sync = {"speed": 1.0, "trim": 0.0, "delay": 0.0}
            job.script = f"Không dò được beat ({e}) — thay nhạc với tốc độ gốc."

        job.step = "Render khớp nhạc (FFmpeg)"
        out = os.path.join(tmp, "output.mp4")
        await core_video.render_music(inp, music, out, job.duration, **sync)

        pack = await prompt_engine.generate(title, desc, "music", "", job.duration)
        job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))
        return out

    src_dur = await core_video.probe_duration(inp)
    stretch = 1.0
    if job.duration and job.duration > src_dur + 1:
        # làm chậm NGAY TRONG lần render cuối thay vì mã hoá riêng cả video một lần (lỗi #7: thêm một thế hệ nén
        # và cả một lượt mã hoá); bóc lời, dò cảnh, tách giọng chạy trên bản gốc rồi nhân mốc thời gian lên
        stretch = min(job.duration / src_dur, 1.4)
        job.step = f"Kéo dài video gốc {src_dur:.0f}s -> {src_dur * stretch:.0f}s (chậm {1 / stretch:.2f}x)"
        if stretch < job.duration / src_dur:
            job.error = (f"ℹ️ Video gốc chỉ {src_dur:.0f}s; kéo chậm tối đa 1.4x được {src_dur * stretch:.0f}s "
                         f"(chậm hơn sẽ lộ). ")

    vid_dur = src_dur * stretch
    limit = config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION
    out_dur = min(vid_dur, job.duration or vid_dur, limit)
    job.out_dur = out_dur
    render_dur = job.duration or (int(limit) if vid_dur > limit + 0.5 else 0)
    is_long = out_dur > 100                             # video dài: 720p
    if vid_dur > limit + 0.5 and not job.duration:
        job.error = (job.error or "") + f"ℹ️ Video {vid_dur:.0f}s, chỉ làm {limit}s đầu (chọn chế độ video dài để làm hết). "
    target = job.duration or int(vid_dur)

    job.step = "Bóc lời thoại + dò chữ in cứng + tách cảnh (song song)"
    src_hash = await asyncio.to_thread(cache.file_hash, inp)
    audio_mp3 = os.path.join(tmp, "audio.mp3")
    await core_video.extract_audio(inp, audio_mp3)
    (text, raw_segs), (cuts, bands) = await asyncio.gather(
        transcribe_segments(audio_mp3),                                  # ("", []) nếu không có nguồn STT
        scenes.analyze_cached(inp, tmp, src_dur, bool(job.subs and job.cover), src_hash,
                              need_thumbs=not job.approved))             # 1 lượt giải mã: cảnh + ảnh + dải chữ
    segs = [(round(a * stretch, 2), round(b * stretch, 2), t) for a, b, t in raw_segs]   # trục thời gian đã làm chậm
    cuts = [round(c * stretch, 2) for c in cuts]
    speech = [(a, b) for a, b, _ in segs]               # lúc người gốc đang nói
    sep_windows = core_video.speech_windows([(a, b) for a, b, _ in raw_segs], src_dur) if raw_segs else None
    speech_secs = sum(b - a for a, b in sep_windows) if sep_windows else src_dur
    # tách giọng AI chỉ chạy trên những đoạn người gốc nói: giới hạn theo SỐ GIÂY CÓ LỜI, không theo độ dài video
    sep_ok = (config.VOICE_SEPARATION != "0" and job.mode == "cook" and out_dur <= SEP_MAX_VIDEO
              and speech_secs <= SEP_MAX_SPEECH)

    def keyframes_for(sg):                              # ảnh cho AI: đổi mốc về trục thời gian của video gốc
        return scenes.keyframes(inp, [(a / stretch, b / stretch, t) for a, b, t in sg])

    async def _ambient():
        """Chế độ nấu ăn: tiếng nền KHÔNG còn giọng người gốc. Ưu tiên tách giọng AI (chỉ các đoạn có lời);
        không được -> tắt tiếng nền đúng lúc người gốc nói; không có mốc -> bỏ tiếng nền."""
        if job.mode != "cook":
            return None, None
        if sep_ok:
            k = cache.key("ambient", src_hash, sep_windows)
            hit = cache.get("ambient", k, ".wav")
            if hit:
                return hit, None
            p = os.path.join(tmp, "ambient.wav")
            if await vocal_sep.remove_voice(inp, p, windows=sep_windows):
                metrics.record("sep", "onnx")
                try:
                    cache.put("ambient", k, p, ".wav")
                except OSError:
                    pass
                return p, None
            metrics.record("sep", "onnx", ok=False)
            metrics.record("sep", "gate", fallback=True)
        return None, (speech or [(0.0, 99999.0)])

    pos_y = None
    if bands:
        y, h = core_video.band_to_output(*bands[0])
        pos_y = y + h // 2
    ass = os.path.join(tmp, "subs.ass")

    # ----- Bỏ đoạn quảng cáo sản phẩm của người gốc ở cuối video (cả hình lẫn tiếng) -----
    cut = promo_cut(job, segs, out_dur)
    if cut and cut < out_dur:
        logging.info("Cắt bỏ đoạn quảng cáo của người gốc: %.1fs -> %.1fs", cut, out_dur)
        job.cut_at = cut
        job.error = (job.error or "") + f"ℹ️ Đã cắt bỏ {out_dur - cut:.0f}s quảng cáo sản phẩm của người gốc ở cuối. "
        out_dur = render_dur = cut
        job.out_dur = out_dur

    # ----- Đường chính: giọng KHỚP HÌNH -----
    segs = [s for s in segs if s[0] < out_dur - 0.5]
    timed = None
    if job.approved:                                    # kịch bản đã duyệt: bỏ qua bước AI
        timed = {"segments": job.approved, "script": " ".join(s["text"] for s in job.approved),
                 "title": "", "caption": "", "hashtags": "", "fresh": 1.0}
    elif len(segs) >= 2:
        segs = scenes.snap_to_cuts(segs, cuts)          # câu mới vang lên đúng lúc chuyển cảnh
        job.step = f"Viết lại {len(segs)} đoạn theo mốc lời + {len(cuts)} điểm chuyển cảnh (AI)"
        timed = await prompt_engine.generate_timed_any(
            title, desc, job.mode, segs, out_dur,
            images_for=keyframes_for)                   # AI nhìn ảnh từng đoạn để lời khớp hình
    elif len(cuts) >= 2:                                # video không lời: AI nhìn ảnh từng cảnh
        sc = scenes.scene_segments(cuts, out_dur)
        job.step = f"AI xem {len(sc)} cảnh và viết lời cho từng cảnh"
        timed = await prompt_engine.generate_scenes(title, desc, job.mode, sc, await keyframes_for(sc), out_dur)
    if timed:
        job.script = timed["script"]
        if not job.approved:
            job.post = prompt_engine.platform_post(timed["title"], timed["caption"], timed["hashtags"], credit_line(job))
        if (job.review or config.REVIEW_SCRIPT == "1") and not job.approved:
            job.approved = [{"i": s["i"], "start": s["start"], "slot": s["slot"], "span": s.get("span", s["slot"]),
                             "text": s["text"], "orig": s.get("orig", "")} for s in timed["segments"]]
            if not job.local:        # nguồn từ link/Telegram: giữ lại để render sau khi duyệt KHÔNG phải tải lại (#8)
                keep = os.path.join(config.UPLOAD_DIR, f"review_{job.id}{os.path.splitext(inp)[1] or '.mp4'}")
                await asyncio.to_thread(shutil.move, inp, keep)
                job.local = keep
            job.status, job.step = "review", "Chờ bạn duyệt/sửa kịch bản rồi bấm Render"
            return "REVIEW"
        sem = asyncio.Semaphore(3)

        async def _one(seg):
            p = os.path.join(tmp, f"clip_{seg['i']}_{cache.key('clip', seg['text'])[:10]}.mp3")
            async with sem:
                w = await core_video.synth_clip_cached(seg["text"], job.voice, p)   # đoạn không đổi: lấy từ cache
            return {"path": p, "words": w}

        async def _durs(cl):
            return list(await asyncio.gather(*[core_video.probe_duration(c["path"]) for c in cl]))

        segs_t = timed["segments"]

        async def _shorten(idx: list, durs: list) -> list:
            """AI rút gọn các đoạn idx (nguyên câu, không mất nguyên liệu/con số). Trả chỉ số đoạn đã đổi."""
            items = []
            for i in idx:
                room = (segs_t[i + 1]["start"] if i + 1 < len(segs_t) else out_dur) - segs_t[i]["start"]
                n = len(segs_t[i]["text"].split())
                items.append((i, segs_t[i]["text"], max(3, int(n * room / max(durs[i], 0.1) * 0.95))))
            changed = []
            for i, text in (await prompt_engine.shorten(items)).items():
                if 0 <= i < len(segs_t) and len(text.split()) < len(segs_t[i]["text"].split()) \
                        and ingredients.find(text) >= ingredients.find(segs_t[i]["text"]) \
                        and not ingredients.check_numbers(segs_t[i]["text"], text)[0]:   # không được mất nguyên liệu/số
                    segs_t[i]["text"] = text
                    changed.append(i)
            if changed:
                job.script = " ".join(s["text"] for s in segs_t)
            return changed

        # ước lượng độ dài TRƯỚC khi đọc (kiểu VideoLingo): đoạn chắc chắn tràn thì rút gọn luôn, đỡ một lượt
        # gọi TTS không chính thức rồi lại phải đọc lại. Ước lượng thận trọng (x0.85) để không rút gọn nhầm.
        if not job.approved:
            est = [len(sg["text"].split()) / prompt_engine.words_per_sec() * 0.85 for sg in segs_t]
            _, sure_long = core_video.plan_voice(est, segs_t, out_dur)
            if sure_long:
                job.step = f"Rút gọn trước {len(sure_long)} đoạn chắc chắn quá dài"
                await _shorten(sure_long, est)

        amb_task = asyncio.create_task(_ambient())      # tách giọng (CPU) chạy song song với tạo giọng (mạng)
        job.step = f"Đọc giọng {len(segs_t)} đoạn song song" + (" + tách giọng gốc" if sep_ok else "")
        clips = list(await asyncio.gather(*[_one(s) for s in segs_t]))
        durs = await _durs(clips)
        plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        if too_long:                                     # đoạn nào vẫn quá dài -> AI viết ngắn lại 1 lần
            job.step = f"Rút gọn {len(too_long)} đoạn quá dài"
            changed = await _shorten(too_long, durs)
            if changed:
                redo = await asyncio.gather(*[_one(segs_t[i]) for i in changed])
                for i, c in zip(changed, redo):
                    clips[i] = c
                for i, d in zip(changed, await _durs(redo)):
                    durs[i] = d
            plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        voice_track = os.path.join(tmp, "voice.wav")
        await core_video.compose_voice(clips, plan, voice_track, out_dur)
        got_subs = False
        if job.subs:
            words = await core_video.words_for_track(clips, plan, voice_track, [s["text"] for s in segs_t])
            if words:
                subs_mod.write_ass(words, ass, pos_y, config.SUB_STYLE)
                got_subs = True
        job.step = "Chờ tách giọng gốc xong" if sep_ok else "Render (FFmpeg)"
        amb_wav, gate = await amb_task
        job.step = "Render (FFmpeg)"
        out = os.path.join(tmp, "output.mp4")
        await core_video.render(inp, voice_track, out, job.mode, render_dur,
                                ass if got_subs else None, bands, amb_wav, gate, allow_hd=not is_long,
                                title=(job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title), tmp=tmp,
                                stretch=stretch)
        if job.mode == "cook":
            job.error = (job.error or "") + (
                "ℹ️ Đã tách bỏ giọng gốc bằng AI. " if amb_wav else
                "ℹ️ Tách giọng AI đang tắt (VOICE_SEPARATION=0): tắt tiếng nền lúc người gốc nói. " if config.VOICE_SEPARATION == "0" else
                f"ℹ️ Người gốc nói {speech_secs:.0f}s (>{SEP_MAX_SPEECH}s): bỏ tách giọng AI cho nhanh, tắt tiếng nền lúc người gốc nói. " if not sep_ok else
                "ℹ️ Tách giọng AI không chạy được (máy chậm/thiếu RAM): tắt tiếng nền lúc người gốc nói. ")
        fast = sum(1 for p in plan if p["factor"] > 1.12)
        drift = max(abs(p["start"] - s["start"]) for p, s in zip(plan, segs_t))
        job.error = (job.error or "") + (
            (f"ℹ️ Lời mới {timed['fresh'] * 100:.0f}% khác lời gốc; " if "fresh" in timed else "ℹ️ ")
            + f"{len(clips)} đoạn giọng, {len(cuts)} điểm chuyển cảnh; lệch tối đa {drift:.1f}s"
            + (f"; {fast} đoạn nói nhanh" if fast else "")
            + (f"; {len(too_long)} đoạn vẫn hơi dài" if too_long else "")
            + (f"; đã che {len(bands)} dải chữ gốc" if bands else "") + ".")
        return out

    # ----- Đường cũ: không có mốc thời gian -> đọc một mạch -----
    job.step = "Viết kịch bản + content (AI)"
    pack = await prompt_engine.generate(title, desc, job.mode, text, target)
    if pack.get("fallback"):
        job.error = "⚠️ AI viết kịch bản lỗi (xem log), đang dùng kịch bản mẫu. Kiểm tra key/model trong Cài đặt."
    job.script = pack["script"]
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))

    job.step = "Đọc giọng AI + phụ đề"
    voice_mp3 = os.path.join(tmp, "voice.mp3")
    got_subs = await core_video.tts(job.script, voice_mp3, job.voice,
                                    ass if job.subs else None, pos_y)
    # Lưới an toàn: giọng đọc xong quá sớm (provider đọc nhanh hơn dự tính) -> viết lại dài hơn 1 lần
    voice_dur = await core_video.probe_duration(voice_mp3)
    if target and voice_dur < 0.72 * target:
        job.step = f"Giọng {voice_dur:.0f}s ngắn hơn video {target}s, viết lại dài hơn"
        scaled = int(target * target / max(voice_dur, 1.0) * 0.95)
        pack2 = await prompt_engine.generate(title, desc, job.mode, text, scaled)
        if not pack2.get("fallback") and len(pack2["script"].split()) > len(job.script.split()):
            job.script = pack2["script"]
            job.post = prompt_engine.platform_post(pack2["title"], pack2["caption"], pack2["hashtags"], credit_line(job))
            got_subs = await core_video.tts(job.script, voice_mp3, job.voice,
                                            ass if job.subs else None, pos_y)

    job.step = "Render (FFmpeg)"
    out = os.path.join(tmp, "output.mp4")
    amb_wav, gate = await _ambient()
    await core_video.render(inp, voice_mp3, out, job.mode, render_dur,
                            ass if (job.subs and got_subs) else None, bands, amb_wav, gate, allow_hd=not is_long,
                            title=(job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title), tmp=tmp,
                            stretch=stretch)
    if bands:
        job.error = f"ℹ️ Đã che {len(bands)} dải chữ gốc và đặt phụ đề mới lên đó."
    return out


JOB_TIMEOUT = {"": 25 * 60, "full": 3 * 3600, "split60": 3 * 3600, "split90": 3 * 3600}
current: dict = {"job": None, "task": None}


async def run_job(job: Job, tmp: str):
    if job.long_mode.startswith("split"):
        await process_split(job, tmp)
    else:
        out = await process(job, tmp)
        if out == "REVIEW":
            return
        await deliver(job, out, "Video", job.post or job.script or "")


async def notify(chat_id: int, text: str):
    """Gửi tin Telegram KHÔNG BAO GIỜ ném lỗi: trước đây mạng lỗi lúc báo "❌ Lỗi" làm chết luôn worker,
    hàng đợi kẹt cho tới khi khởi động lại."""
    if not (bot and chat_id):
        return
    try:
        await bot.send_message(chat_id, text[:4000])
    except Exception as e:
        logging.warning("Không gửi được tin Telegram tới %s: %s", chat_id, str(e)[:120])


async def worker():
    """Worker duy nhất (concurrency = 1). Mỗi job có timeout toàn cục và hủy được;
    hủy hoặc quá hạn thì giết mọi FFmpeg/tách giọng đang chạy, không để kẹt hàng đợi."""
    while True:
        job = await queue.get()
        try:
            await _run_one(job)
        except Exception:                        # lưới cuối: lỗi gì cũng không được giết worker
            logging.exception("Worker lỗi ngoài dự kiến ở job %s", job.id)
            if job.status == "processing":
                job.status, job.error = "error", "Lỗi nội bộ, xem log."
                queue.save(job)
            queue.done()


async def _run_one(job: Job):
    tg = job.source == "tg"
    if job.cancel:
        job.status, job.error = "cancelled", "Đã hủy trước khi chạy."
        queue.save(job)
        queue.done()
        return
    # ước lượng chỗ cần: file nguồn x3 (nguồn + bản đang xử lý + kết quả) + 300MB cho tiếng, ảnh, phụ đề
    src_mb = _size_mb(job.local) if job.local and os.path.exists(job.local) else 60
    need = 300 + 3 * src_mb
    workdir = pick_workdir(need)
    if not workdir:
        job.status, job.error = "error", f"Không đủ chỗ trống (cần ~{need}MB). {disk_report()}"
        logging.error("Job %s: %s", job.id, job.error)
        queue.save(job)
        queue.done()
        return
    job.status, job.started_at, job.timeline, job.error = "processing", time.time(), [], ""
    queue.save(job)                                # trạng thái processing vào DB: chết giữa chừng thì chạy lại
    current["job"] = job
    try:
        if tg:
            await notify(job.chat_id, f"⏳ Đang xử lý video #{job.id}... (gửi /cancel để hủy)")
        logging.info("Job %s: file tạm ở %s (cần ~%dMB) | %s", job.id, workdir, need, disk_report())
        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            task = asyncio.create_task(run_job(job, tmp))
            current["task"] = task

            async def _timeline():                 # ghi lại từng bước + thời điểm để đo
                last = None
                while True:
                    if job.step != last:
                        last = job.step
                        t = round(time.time() - job.started_at, 1)
                        job.timeline.append((job.step, t))
                        logging.info("Job %s [%4.0fs] %s", job.id, t, job.step)
                    await asyncio.sleep(1)
            tl = asyncio.create_task(_timeline())
            try:
                while True:                       # hạn co giãn: biết độ dài video rồi thì nới ra cho đủ
                    done, _ = await asyncio.wait({task}, timeout=5)
                    if done:
                        task.result()
                        break
                    if time.time() - job.started_at > job_budget(job):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise asyncio.TimeoutError
            finally:
                tl.cancel()
        if job.status == "review":
            if tg:
                lines = "\n".join(f"{s['i']}: {s['text']}" for s in job.approved)
                await notify(job.chat_id, (f"📝 Kịch bản job #{job.id}:\n\n{lines[:3500]}\n\n"
                                           "Trả lời \"ok\" để render, hoặc gửi \"số: lời mới\" (mỗi dòng một đoạn) để sửa."))
        else:
            job.status, job.step = "done", ""
    except asyncio.TimeoutError:
        core_video.kill_running()
        job.status, job.error = "error", (f"Quá thời gian cho phép ({job_budget(job) // 60:.0f} phút) ở bước "
                                          f"«{job.step}», đã hủy. Thử chất lượng 'fast' hoặc video ngắn hơn.")
        logging.error("Job %s quá hạn ở bước: %s | các bước: %s", job.id, job.step,
                      " > ".join(f"{s}@{t:.0f}s" for s, t in job.timeline))
        if tg:
            await notify(job.chat_id, f"❌ Job #{job.id} quá thời gian, đã hủy.")
    except asyncio.CancelledError:
        core_video.kill_running()
        job.status, job.error = "cancelled", "Đã hủy theo yêu cầu."
        if tg:
            await notify(job.chat_id, f"🛑 Đã hủy job #{job.id}.")
    except Exception as e:
        core_video.kill_running()
        logging.exception("Job %s thất bại", job.id)
        job.status, job.error, job.step = "error", str(e), ""
        if tg:
            await notify(job.chat_id, f"❌ Lỗi: {e}")
    finally:
        job.finished_at = time.time()
        current["job"], current["task"] = None, None
        if job.status != "review":            # job đang chờ duyệt giữ lại file nguồn để render sau
            for leftover in (job.local, job.music_local):
                if leftover and os.path.exists(leftover):
                    os.remove(leftover)
        _learn_speed(job)
        queue.log_steps(job)
        queue.save(job)
        queue.done()


def approve_job(src: Job, edits: dict | None = None) -> Job:
    """Tạo job render từ kịch bản đã duyệt (edits: {i: text mới}). Job cũ chuyển sang 'approved'."""
    segs = []
    for s in src.approved:
        t = (edits or {}).get(int(s["i"]), s["text"]).strip()
        if t:
            segs.append({"i": s["i"], "start": s["start"], "slot": s["slot"], "span": s.get("span", s["slot"]), "text": t})
    if not segs:
        raise ValueError("Kịch bản trống: cần ít nhất một đoạn có lời.")
    job = Job(url=src.url, mode=src.mode, duration=src.duration, voice=src.voice, subs=src.subs, cover=src.cover,
              long_mode=src.long_mode, local=src.local if src.local and os.path.exists(src.local) else "",
              tg_file_id=src.tg_file_id, title_hint=src.title_hint, music_url=src.music_url,
              music_local="", music_tg_file_id=src.music_tg_file_id, source=src.source, chat_id=src.chat_id,
              approved=segs, cut_at=src.cut_at, source_meta=src.source_meta)
    job.post = src.post
    job.script = " ".join(s["text"] for s in segs)
    # put() TRƯỚC, đổi trạng thái job cũ SAU: lỗi gì thì kịch bản vẫn còn ở job cũ để duyệt lại (lỗi #4).
    # Job đã duyệt được vượt giới hạn hàng đợi vì người dùng đã chờ và đã sửa tay.
    queue.put(job, force=True)
    src.status, src.step = "approved", f"Đã duyệt -> render ở job #{job.id}"
    queue.save(src)
    return job


def job_budget(job: Job) -> float:
    """Hạn thời gian theo độ dài video và mức chất lượng (máy Botkeep 1.5 vCPU):
    tối thiểu 25 phút; 95s balanced ≈ 41 phút; video dài tối đa 3 giờ."""
    base = JOB_TIMEOUT.get(job.long_mode, 25 * 60)
    if not job.out_dur:
        return base
    per_sec = {"fast": 14, "high": 40}.get(config.QUALITY, 22)
    return min(3 * 3600, max(base, 600 + job.out_dur * per_sec * (1 if job.mode != "download" else 0.2)))


def cancel_job(jid: int) -> str:
    """Hủy job đang chờ hoặc đang chạy. Trả về thông báo."""
    job = queue.jobs.get(jid)
    if not job:
        return "Không thấy job."
    if job.status == "queued":
        job.cancel = True
        return f"Job #{jid} sẽ bị bỏ qua khi tới lượt."
    if job.status == "processing" and current["job"] is job and current["task"]:
        current["task"].cancel()
        return f"Đang hủy job #{jid}..."
    return f"Job #{jid} đã kết thúc ({job.status})."


SPEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed.json")


def _learn_speed(job: Job):
    """Học tốc độ máy: giây xử lý / giây video, để ước tính thời gian các job sau."""
    if job.status != "done" or not job.out_dur or not job.started_at:
        return
    try:
        d = json.load(open(SPEED_FILE)) if os.path.exists(SPEED_FILE) else {}
        key = "long" if job.long_mode else "normal"
        ratio = (job.finished_at - job.started_at) / job.out_dur
        d[key] = round(0.6 * d.get(key, ratio) + 0.4 * ratio, 1)
        json.dump(d, open(SPEED_FILE, "w"))
    except Exception:
        pass


def speed_ratio(long_mode: str) -> float:
    try:
        d = json.load(open(SPEED_FILE))
        return d.get("long" if long_mode else "normal", 6.0)
    except Exception:
        return 6.0


CONSOLE_HELP = ("Lệnh console: help | status | health (kiểm tra dịch vụ ngoài ngay) | update_ytdlp (cập nhật yt-dlp, "
                "tự quay lại bản cũ nếu hỏng) | setup_potoken (PO token cho YouTube, ~300MB) | remove_potoken | "
                "yt_test <link> | cancel <số job>")


async def console():
    """Ô console Botkeep (Pterodactyl) gửi chữ vào stdin của bot -> chạy lệnh quản trị ngay tại đó."""
    import potoken_setup
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            return
        if not line:                       # không có stdin (chạy nền) -> thôi
            return
        cmd = line.strip()
        low = cmd.lower()
        try:
            if not cmd:
                continue
            if low in ("help", "?", "/help"):
                logging.info(CONSOLE_HELP)
            elif low in ("status", "/status"):
                cur = current.get("job")
                logging.info("Hàng đợi: %d | đang chạy: %s | %s | PO token: %s | proxy: %s | cookies: %s",
                             queue.pending, f"#{cur.id} {cur.step}" if cur else "không",
                             disk_report(), core_video.pot_status() or "chưa cài",
                             "có" if config.YTDLP_PROXY.strip() else "không",
                             "có" if config.YTDLP_COOKIES.strip() else "không")
                logging.info("yt-dlp %s | luồng FFmpeg %d\nDịch vụ ngoài 24 giờ qua:\n%s",
                             ytdlp_update.installed_version(), core_video.ffmpeg_threads(), metrics.summary(24))
            elif low in ("health", "/health"):
                spawn(monitor.tick(force=True))
            elif low in ("update_ytdlp", "update-ytdlp"):
                spawn(ytdlp_update.update(lambda s: logging.info("[yt-dlp] %s", s)))
            elif "setup_potoken" in low or "setup-potoken" in low:
                logging.info("Bắt đầu cài PO token (vài phút, bot vẫn chạy bình thường)...")
                spawn(potoken_setup.install(lambda s: logging.info("[potoken] %s", s)))
            elif "remove_potoken" in low:
                potoken_setup.remove(lambda s: logging.info("[potoken] %s", s))
            elif low.startswith("yt_test"):
                url = cmd.split(maxsplit=1)[1] if " " in cmd else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
                with tempfile.TemporaryDirectory() as td:
                    try:
                        d = await core_video.probe_formats(url, td)
                        logging.info("1/2 Xem định dạng OK: %s", d["title"][:50])
                        p, _ = await core_video.download_format(url, td, "360")   # tải thật mới chắc
                        logging.info("✅ 2/2 Tải thật OK (%dKB) — YouTube dùng được.", os.path.getsize(p) >> 10)
                    except Exception as e:
                        logging.info("❌ YouTube vẫn lỗi: %s", e)
            elif low.startswith("cancel "):
                logging.info(cancel_job(int(low.split()[1])))
            else:
                logging.info("Không hiểu lệnh «%s». %s", cmd[:40], CONSOLE_HELP)
        except Exception as e:
            logging.warning("Lệnh console lỗi: %s", e)


_TASKS: set = set()


def spawn(coro):
    """create_task có giữ tham chiếu: task không giữ tham chiếu có thể bị gom rác giữa chừng (tài liệu asyncio)."""
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


async def notify_admin(text: str):
    cid = config.admin_chat_id()
    if cid:
        await notify(cid, text)
    else:
        logging.warning("(chưa đặt ADMIN_CHAT_ID/ALLOWED_USERS nên không gửi cảnh báo) %s", text[:200])


monitor = healthcheck.Monitor(notify_admin)
MAINTENANCE_EVERY = 600          # giây


async def maintenance():
    """Mỗi 10 phút: hết hạn job chờ duyệt quá 24 giờ, dọn số liệu cũ, kiểm tra dịch vụ ngoài theo lịch."""
    await asyncio.sleep(60)                          # chờ bot khởi động xong
    while True:
        try:
            for job in queue.expire_reviews():
                if job.source == "tg":
                    await notify(job.chat_id, f"⌛ Kịch bản job #{job.id} đã quá 24 giờ chưa duyệt nên hết hạn.")
            metrics.prune()
            await asyncio.to_thread(cache.prune)
            await monitor.tick()
        except Exception:
            logging.exception("Bảo trì định kỳ lỗi")
        await asyncio.sleep(MAINTENANCE_EVERY)


async def main():
    shutil.rmtree(WORK_DIR, ignore_errors=True)        # file tạm sót lại từ lần chạy bị ngắt (TRƯỚC khi worker chạy)
    spawn(worker())
    spawn(console())
    spawn(maintenance())
    for job in queue.restored:
        logging.info("Chạy lại job #%s sau khi khởi động lại", job.id)
        if job.source == "tg":
            spawn(notify(job.chat_id, f"🔄 Bot vừa khởi động lại, job #{job.id} được chạy tiếp."))

    app = build_app(queue, {"cancel": cancel_job, "approve": approve_job, "speed": speed_ratio})
    runner = web.AppRunner(app, access_log=None)      # không ghi log mỗi lần web polling (giảm 95% log)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    logging.info("Web panel chạy tại cổng %s", config.PORT)
    logging.info("BOT_TOKEN: %s | WEB_PASSWORD: %s",
                 "OK" if config.BOT_TOKEN else "THIẾU", "OK" if config.WEB_PASSWORD else "THIẾU")
    if not config.WEB_PASSWORD:
        logging.warning("Chưa đặt WEB_PASSWORD: web panel đang KHOÁ. Đặt biến môi trường WEB_PASSWORD để dùng.")
    logging.info(CONSOLE_HELP)
    logging.info("Dung lượng: %s", disk_report())
    if bot and not config.allowed_ids():
        logging.warning("Chưa đặt ALLOWED_USERS: bot Telegram từ chối mọi người. Nhắn bot để biết chat id "
                        "của bạn rồi điền vào Cài đặt trên web.")

    if bot:
        await dp.start_polling(bot)  # long polling: không cần mở thêm port
    else:
        logging.warning("Không có BOT_TOKEN — chạy chế độ chỉ-web.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
