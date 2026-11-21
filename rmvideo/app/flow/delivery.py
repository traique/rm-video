"""Giao kết quả: giữ file cho web tải (outputs/), gửi Telegram (≤48MB gửi thẳng, lớn hơn gửi link), ảnh bìa kèm theo.
1.4.0: nội dung đăng (tiêu đề, caption, hashtag từng nền tảng) luôn gửi kèm qua Telegram và Zoom — trước đây job web /
tự đăng chỉ báo link, còn video gửi thẳng Telegram thì caption bị cắt ở 1024 ký tự (giới hạn chú thích video).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os
import shutil

import config
import cover as cover_mod
import state
from queue_manager import Job
from rmvideo.app import notifier
from state import OUT_DIR, notify, queue
from storage import _cleanup_outputs

CAPTION_MAX = 1024          # chú thích video / ảnh của Telegram
CHUNK = 3500                # một tin nhắn (Telegram tối đa 4096, Zoom bot cắt ở 3800)


def chunks(text: str, n: int = CHUNK) -> list[str]:
    """Cắt nội dung dài thành các tin ≤ n ký tự, ưu tiên cắt ở chỗ xuống dòng (không cắt đôi hashtag / câu)."""
    text = (text or "").strip()
    out: list[str] = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        if cut < n // 2:
            cut = text.rfind(" ", 0, n)
        if cut <= 0:
            cut = n
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return out + ([text] if text else [])


def content_messages(job: Job, label: str, caption: str) -> list[str]:
    """Các tin «📝 Nội dung đăng» (đánh số khi phải chia nhiều tin). Chế độ ⬇️ chỉ tải về: không có bài đăng."""
    if job.mode == "download":
        return []
    parts = chunks(caption)
    head = f"📝 Nội dung đăng {'' if label == 'Video' else label + ' '}(job #{job.id})"
    return [f"{head}{f' ({k + 1}/{len(parts)})' if len(parts) > 1 else ''}:\n{p}" for k, p in enumerate(parts)]


async def deliver(job: Job, path: str, label: str, caption: str):
    """Giao 1 file kết quả: giữ cho web tải về; Telegram gửi thẳng nếu ≤48MB, lớn hơn thì gửi link."""
    import secrets as _secrets
    ext = os.path.splitext(path)[1] or ".mp4"
    if ext == ".mp3" and label == "Video":
        label = "MP3"
    # ảnh bìa (cover.py) nằm cạnh file video trong thư mục tạm của job / của phần
    cover_src = os.path.join(os.path.dirname(path), cover_mod.COVER_NAME) if ext == ".mp4" else ""
    thumb_src = os.path.join(os.path.dirname(path), cover_mod.THUMB_NAME) if ext == ".mp4" else ""
    keep = os.path.join(OUT_DIR, f"out_{job.id}_{len(job.parts)}{ext}")
    await asyncio.to_thread(shutil.move, path, keep)       # /tmp -> ổ bot có thể là chép thật, không chặn vòng lặp
    queue.log_source(job, keep, label)
    part = {"label": label, "path": keep, "token": _secrets.token_urlsafe(16)}
    job.parts.append(part)
    job.output = keep                       # tương thích: nút tải chính = file mới nhất
    cover_part: dict = {}
    if cover_src and os.path.exists(cover_src):
        cover_keep = os.path.join(OUT_DIR, f"out_{job.id}_{len(job.parts)}.jpg")
        await asyncio.to_thread(shutil.move, cover_src, cover_keep)
        cover_part = {"label": "Ảnh bìa" if label == "Video" else f"Ảnh bìa {label}", "path": cover_keep,
                      "token": _secrets.token_urlsafe(16)}
        job.parts.append(cover_part)
    _cleanup_outputs({p["path"] for p in job.parts})
    size = os.path.getsize(keep)
    link = notifier.part_link(job, part)
    where = f"Tải tại: {link}" if link else "Mở web panel để tải (đặt PUBLIC_URL trong Cài đặt để nhận link ở đây)."
    if job.source != "tg":
        # job Zoom / web / tự đăng (bản 1.0.0): báo xong + link tải (không gửi file). Trước đây im lặng hoàn toàn.
        cover_link = notifier.part_link(job, cover_part) if cover_part else ""
        await notifier.job_event(job, f"✅ {label} job #{job.id} xong ({size / 1048576:.0f}MB).\n{where}"
                                      + (f"\nẢnh bìa: {cover_link}" if cover_link else ""))
        for msg in content_messages(job, label, caption):      # nội dung đăng: tin riêng, đủ chữ, dễ chép
            await notifier.job_event(job, msg)
        return
    if not state.bot:
        return
    job.step = f"Gửi {label} qua Telegram"
    from aiogram.types import FSInputFile
    caption = (caption or "").strip()
    # vừa chú thích video thì để nguyên; dài hơn (bài có công thức thường 1.500-2.500 ký tự) thì gửi đủ ở tin riêng
    fits = len(caption) <= CAPTION_MAX
    rest = [] if fits else content_messages(job, label, caption)
    if size <= config.TG_LIMIT:
        try:
            short = caption if fits else f"🎬 {label} job #{job.id} — nội dung đăng ở tin bên dưới"
            if ext == ".mp3":
                await state.bot.send_audio(job.chat_id, FSInputFile(keep), caption=short, request_timeout=600)
            else:
                thumb = FSInputFile(thumb_src) if thumb_src and os.path.exists(thumb_src) else None
                await state.bot.send_video(job.chat_id, FSInputFile(keep), caption=short, thumbnail=thumb,
                                           supports_streaming=True, request_timeout=600)
        except Exception as e:                  # video đã làm xong: lỗi gửi không được biến job thành lỗi
            logging.warning("Gửi file qua Telegram lỗi: %s", e)
            await notify(job.chat_id, f"🎬 {label} xong nhưng gửi qua Telegram lỗi ({str(e)[:80]}).\n{where}")
            rest = content_messages(job, label, caption)
    else:
        await notify(job.chat_id, f"🎬 {label}: file {size / 1048576:.0f}MB vượt giới hạn 50MB của Telegram.\n{where}")
        rest = content_messages(job, label, caption)
    for msg in rest:
        await notify(job.chat_id, msg)
    if cover_part:
        try:
            await state.bot.send_photo(job.chat_id, FSInputFile(cover_part["path"]),
                                       caption=f"🖼 Ảnh bìa {'' if label == 'Video' else label} — chọn làm ảnh bìa khi đăng")
        except Exception as e:
            logging.warning("Gửi ảnh bìa qua Telegram lỗi: %s", e)
