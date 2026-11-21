"""Chế độ 📸: 1-4 ảnh + nhạc -> video dọc cắt đúng beat (Kaggle hoặc Botkeep).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import os
import secrets

import kaggle_worker
import prompt_engine
import slideshow
import state
import trendfx
from queue_manager import Job
from rmvideo.app.flow import backends, common
from rmvideo.app.flow import music as music_flow


async def process_photo(job: Job, tmp: str) -> str:
    """Chế độ 📸: 1-4 ảnh + nhạc -> video dọc, cảnh cắt đúng beat, hiệu ứng + chuyển cảnh chọn theo nhạc."""
    images = list(job.images)
    if job.tg_images:
        job.step = f"Tải {len(job.tg_images)} ảnh từ Telegram"
        images = []
        for k, fid in enumerate(job.tg_images[:slideshow.MAX_IMAGES]):
            p = os.path.join(tmp, f"img_{k}.jpg")
            await state.bot.download(fid, destination=p)
            images.append(p)
    if not images:
        raise RuntimeError("Chế độ 📸 cần 1-4 ảnh.")
    job.step = "Tải nhạc"
    music = await music_flow._get_music(job, tmp)
    job.step = f"Dựng ảnh theo nhạc ({len(images)} ảnh)"
    seed = secrets.randbits(32)
    out, res = await backends.kaggle_render(
        job, tmp, lambda: kaggle_worker.photo_spec(job, images, music, seed, trendfx.load_recent()), "Chế độ 📸")
    if out:
        plan = res["plan"]
        trendfx.remember(plan["template"])             # lịch sử công thức hiệu ứng nằm ở Botkeep
    else:
        out = os.path.join(tmp, "output.mp4")
        plan = await slideshow.render(images, music, out, job.duration)
    job.out_dur = plan["length"]
    job.script = slideshow.describe(plan)
    pack = await prompt_engine.generate(job.title_hint or "", "", "photo", "", int(plan["length"]))
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], common.credit_line(job))
    return out
