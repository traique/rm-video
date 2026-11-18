"""🧸 Truyện bé trên Botkeep: đề (đã chọn bằng mã) -> một phiên Kaggle làm trọn (model chữ viết truyện, vẽ 8 cảnh, giọng
VieNeu, dựng) -> tải về -> kiểm bằng mã -> bài đăng -> giao như job thường. Không có đường lùi về Botkeep (model không chạy
được trên máy 1,5 vCPU): Kaggle lỗi là job lỗi, kids_auto chọn khung giờ khác và báo admin."""
from __future__ import annotations

import logging
import os
import shutil

import kaggle_worker
import prompt_engine
from queue_manager import Job
from rmvideo.kids import job as kids_job, prompts, story as story_mod


async def process_kids(job: Job, tmp: str) -> str:
    import kids_auto
    data = kids_auto.data()
    if not job.brief:
        job.brief = kids_auto.new_brief(job)
    brief = job.brief
    why = kaggle_worker.enabled()
    if why:
        raise RuntimeError(f"Truyện bé cần Kaggle (GPU): {why}")
    job.step = "Truyện bé: chọn nhạc nền"
    music = await kids_auto.pick_music()
    spec = kaggle_worker.kids_spec(job, brief, music)
    job.step = "Truyện bé: gửi Kaggle (viết truyện, vẽ 8 cảnh, đọc giọng, dựng) — " + story_mod.describe(brief, data)
    res = await kaggle_worker.produce(spec, tmp, on_step=lambda s: setattr(job, "step", s))
    video = res["video"]
    job.step = "Truyện bé: kiểm video"
    bad = await kids_job.qa_video(video)
    if bad:
        raise RuntimeError("Video Kaggle trả về không đạt: " + "; ".join(bad))
    st = res.get("story") or {}
    files = res.get("files") or {}
    for name in ("cover.jpg", "thumb.jpg"):                 # delivery tìm ảnh bìa cạnh file video
        if files.get(name) and os.path.exists(files[name]):
            shutil.move(files[name], os.path.join(os.path.dirname(video), name))
    title, desc, tags = prompts.post_pack(st, brief, kids_auto.channel(), data)
    job.post = prompt_engine.platform_post(title, desc, tags)
    job.hook = title
    job.srt = res.get("srt") or ""
    job.out_dur = float(res.get("duration") or 0.0)
    attempts = res.get("attempts") or []
    job.script = (f"🧸 {title}\n{story_mod.describe(brief, data)}\n\n" + story_mod.script_text(st)
                  + (f"\n\n(model viết {len(attempts)} lượt)" if len(attempts) > 1 else ""))
    t = res.get("timings") or {}
    job.note("ℹ️ Kaggle (GPU): viết " + f"{t.get('llm', 0):.0f}s, vẽ {t.get('images', 0):.0f}s, đọc {t.get('tts', 0):.0f}s, "
             f"dựng {t.get('render', 0):.0f}s" + ("; nhạc nền có" if res.get("music") else "; không nhạc nền") + ". ")
    for n in res.get("notes") or []:
        job.note(f"ℹ️ {n} ")
    kids_auto.remember_done(job, brief, st)
    logging.info("Truyện bé job %s: «%s» %.1fs, %s", job.id, title, job.out_dur, t)
    return video
