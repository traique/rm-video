"""Điểm vào xử lý một job: chọn luồng theo chế độ (ảnh / tải về / nhạc / làm lại), huỷ các việc nền khi job dừng, giao kết quả.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio

from queue_manager import Job
from rmvideo.app.flow import delivery, long_video, remake


async def process(job: Job, tmp: str, allow_review: bool = True, local_only: bool = False, teaser_ok: bool = True):
    """Xử lý một job; task nền nào (tách giọng) còn chạy khi job dừng giữa chừng thì bị huỷ cùng.
    teaser_ok=False: phần cắt từ video dài — không chiếu trước cảnh món (món xong nằm ở phần cuối của cả video)."""
    tasks: list = []
    try:
        return await remake._process(job, tmp, allow_review, tasks, local_only, teaser_ok)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_job(job: Job, tmp: str):
    if job.long_mode.startswith("split"):
        await long_video.process_split(job, tmp)
    elif job.long_mode == "highlight":
        await long_video.process_highlights(job, tmp)
    else:
        out = await process(job, tmp)
        if out == "REVIEW":
            return "REVIEW"
        await delivery.deliver(job, out, "Video", job.post or job.script or "")
