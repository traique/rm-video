"""Giao kết quả: giữ file cho web tải (outputs/), gửi Telegram (≤48MB gửi thẳng, lớn hơn gửi link), ảnh bìa kèm theo.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os
import shutil

import config
import cover as cover_mod
import state
from queue_manager import Job
from state import OUT_DIR, notify, queue
from storage import _cleanup_outputs


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
    cover_keep = ""
    if cover_src and os.path.exists(cover_src):
        cover_keep = os.path.join(OUT_DIR, f"out_{job.id}_{len(job.parts)}.jpg")
        await asyncio.to_thread(shutil.move, cover_src, cover_keep)
        job.parts.append({"label": "Ảnh bìa" if label == "Video" else f"Ảnh bìa {label}", "path": cover_keep,
                          "token": _secrets.token_urlsafe(16)})
    _cleanup_outputs({p["path"] for p in job.parts})
    if job.source != "tg" or not state.bot:
        return
    job.step = f"Gửi {label} qua Telegram"
    size = os.path.getsize(keep)
    link = (f"{config.PUBLIC_URL.rstrip('/')}/download/{job.id}/{job.parts.index(part)}?t={part['token']}"
            if config.PUBLIC_URL else "")
    where = f"Tải tại: {link}" if link else "Mở web panel để tải (đặt PUBLIC_URL trong Cài đặt để nhận link ở đây)."
    from aiogram.types import FSInputFile
    if size <= config.TG_LIMIT:
        try:
            if ext == ".mp3":
                await state.bot.send_audio(job.chat_id, FSInputFile(keep), caption=caption[:1024], request_timeout=600)
            else:
                thumb = FSInputFile(thumb_src) if thumb_src and os.path.exists(thumb_src) else None
                await state.bot.send_video(job.chat_id, FSInputFile(keep), caption=caption[:1024], thumbnail=thumb,
                                           supports_streaming=True, request_timeout=600)
        except Exception as e:                  # video đã làm xong: lỗi gửi không được biến job thành lỗi
            logging.warning("Gửi file qua Telegram lỗi: %s", e)
            await notify(job.chat_id, f"🎬 {label} xong nhưng gửi qua Telegram lỗi ({str(e)[:80]}).\n{where}"
                                      f"\n\n{caption[:800]}")
    else:
        await notify(job.chat_id, f"🎬 {label}: file {size / 1048576:.0f}MB vượt giới hạn 50MB của Telegram.\n{where}"
                                  f"\n\n{caption[:800]}")
    if cover_keep:
        try:
            await state.bot.send_photo(job.chat_id, FSInputFile(cover_keep),
                                       caption=f"🖼 Ảnh bìa {'' if label == 'Video' else label} — chọn làm ảnh bìa khi đăng")
        except Exception as e:
            logging.warning("Gửi ảnh bìa qua Telegram lỗi: %s", e)
