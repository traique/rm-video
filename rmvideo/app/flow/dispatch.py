"""Điểm vào xử lý một job: lấy nguồn, chọn luồng theo chế độ (📸 ảnh / ⬇️ tải về / 🎵 nhạc / làm lại có lời), huỷ
các việc nền khi job dừng, giao kết quả.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
from __future__ import annotations

import asyncio
import os

import config
from rmvideo.media import fetch, ff
import state
from queue_manager import Job
from rmvideo.app.flow import common, delivery, long_video, photo
from rmvideo.app.flow import music as music_flow
from rmvideo.app.flow.remake import Remake
from rmvideo.domain.job import LongMode, Mode


async def process(job: Job, tmp: str, allow_review: bool = True, local_only: bool = False, teaser_ok: bool = True):
    """Xử lý một job; task nền nào (tách giọng, ảnh bìa) còn chạy khi job dừng giữa chừng thì bị huỷ cùng.
    teaser_ok=False: phần cắt từ video dài — không chiếu trước cảnh món (món xong nằm ở phần cuối của cả video)."""
    tasks: list = []
    try:
        return await _process(job, tmp, allow_review, tasks, local_only, teaser_ok)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process(job: Job, tmp: str, allow_review: bool, tasks: list, local_only: bool = False,
                   teaser_ok: bool = True):
    if job.mode == Mode.PHOTO:
        return await photo.process_photo(job, tmp)
    inp, title, desc = await acquire(job, tmp)
    if job.mode == Mode.DOWNLOAD:
        return await finish_download(job, tmp, inp, title)
    if job.mode == Mode.MUSIC:
        return await music_flow.process_music(job, tmp, inp, title, desc)
    return await Remake(job, tmp, inp, title, desc, allow_review=allow_review, tasks=tasks, local_only=local_only,
                        teaser_ok=teaser_ok).run()


async def acquire(job: Job, tmp: str) -> tuple:
    """Nguồn của job -> (đường dẫn, tiêu đề, mô tả): file tại chỗ, file Telegram, hoặc tải từ link."""
    if job.local:
        # dùng file tại chỗ (file tải lên, hoặc nguồn đã giữ lại lúc chờ duyệt): không chép/chuyển; bot chết
        # giữa chừng thì file còn nguyên để chạy lại; worker xoá file khi job xong (trừ job chờ duyệt)
        job.step = "Dùng file nguồn đã có" if job.approved else "Nhận file tải lên"
        return job.local, job.title_hint, ""
    if job.tg_file_id:
        job.step = "Tải file từ Telegram"
        inp = os.path.join(tmp, "input.mp4")
        await state.bot.download(job.tg_file_id, destination=inp)
        return inp, job.title_hint, ""
    if job.mode == Mode.DOWNLOAD and job.quality:
        lbl = "MP3" if job.quality == "mp3" else (job.quality + "p" if job.quality.isdigit() else "tốt nhất")
        job.step = f"Tải {lbl}..."

        def _pct(p, lbl=lbl):
            job.step = f"Tải {lbl}... {p}%"
        inp, title = await fetch.download_format(job.url, tmp, job.quality, _pct)
        return inp, title, ""
    if job.mode == Mode.DOWNLOAD:
        job.step = "Tải video gốc (không logo)"
        inp, title, desc, job.source_meta = await fetch.download(job.url, tmp, max_duration=600)
        return inp, title, desc
    job.step = "Tải video (yt-dlp)"
    inp, title, desc, job.source_meta = await fetch.download(
        job.url, tmp, max_duration=config.LONG_MAX if job.long_mode == LongMode.FULL or common.auto_full(job)
        else config.MAX_DURATION)
    common.check_source_allowed(job)
    return inp, title, desc


async def finish_download(job: Job, tmp: str, inp: str, title: str) -> str:
    """⬇️ Chỉ tải về: không mã hoá lại, đóng gói mp4 + faststart (mp3 giữ nguyên)."""
    if inp.endswith(".mp3"):
        out = inp
    else:
        out = os.path.join(tmp, "output.mp4")
        await ff.remux(inp, out)
    dur = await ff.probe_duration(out)
    job.script = f"Tải xong: {dur:.0f}s, {os.path.getsize(out) // 1024 // 1024}MB"
    job.post = title or ""
    return out


async def run_job(job: Job, tmp: str):
    if job.long_mode.startswith("split"):
        await long_video.process_split(job, tmp)
    elif job.long_mode == LongMode.HIGHLIGHT:
        await long_video.process_highlights(job, tmp)
    else:
        out = await process(job, tmp)
        if out == "REVIEW":
            return "REVIEW"
        await delivery.deliver(job, out, "Video", job.post or job.script or "")
