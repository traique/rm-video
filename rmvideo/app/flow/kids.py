"""🧸 Truyện bé trên Botkeep: đề (đã chọn bằng mã) -> Gemini viết truyện ngay trên Botkeep (KIDS_LLM_PROVIDER auto/gemini;
1 lượt gọi + 1 mỗi lần viết lại) -> một phiên Kaggle làm trọn (vẽ 8 cảnh, giọng VieNeu, dựng; Qwen viết nếu chưa có truyện)
-> tải về -> kiểm bằng mã -> bài đăng -> giao như job thường. Không có đường lùi cho phần dựng (model không chạy được trên
máy 1,5 vCPU): Kaggle lỗi là job lỗi, kids_auto chọn khung giờ khác và báo admin."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

import config
import kaggle_worker
import prompt_engine
from queue_manager import Job
from rmvideo.kids import job as kids_job, llm, prompts, story as story_mod, writer


async def write_on_botkeep(job: Job, brief: dict, data: dict) -> tuple[dict | None, list]:
    """Viết truyện bằng Gemini theo KIDS_LLM_PROVIDER: auto = thử Gemini, lỗi thì để Kaggle viết (Qwen); gemini = bắt buộc;
    qwen = bỏ qua. Trả (truyện đã chèn câu dạy từ, các lượt) hoặc (None, [])."""
    provider = (config.KIDS_LLM_PROVIDER or "auto").strip().lower()
    if provider == "qwen":
        return None, []
    if not config.GEMINI_API_KEY:
        if provider == "gemini":
            raise RuntimeError("KIDS_LLM_PROVIDER=gemini nhưng chưa có GEMINI_API_KEY")
        job.note("ℹ️ Chưa có GEMINI_API_KEY: Qwen trên Kaggle viết truyện. ")
        return None, []
    job.step = "Truyện bé: Gemini viết truyện — " + story_mod.describe(brief, data)
    be = writer.GeminiBackend(config.KIDS_GEMINI_MODEL, log=lambda s: logging.info("Truyện bé: %s", s))
    spec = {"brief": brief, "channel": __import__("kids_auto").channel()}
    try:
        st, attempts = await asyncio.to_thread(llm.generate_story, spec, data, "", lambda s: logging.info("Truyện bé: %s", s),
                                               srv=be)
    except Exception as e:
        if provider == "gemini":
            raise
        job.note(f"⚠️ Gemini không viết được ({str(e)[:120]}): Qwen trên Kaggle viết thay. ")
        logging.warning("Truyện bé job %s: Gemini lỗi (%s), để Kaggle viết", job.id, str(e)[:200])
        return None, []
    job.note(f"ℹ️ Gemini {be.model} viết truyện: {be.calls} lượt gọi. ")
    return st, attempts


async def process_kids(job: Job, tmp: str) -> str:
    import kids_auto
    data = kids_auto.data()
    if not job.brief:
        job.brief = kids_auto.new_brief(job)
    brief = job.brief
    why = kaggle_worker.enabled()
    if why:
        raise RuntimeError(f"Truyện bé cần Kaggle (GPU): {why}")
    st0, attempts0 = await write_on_botkeep(job, brief, data)
    job.step = "Truyện bé: chọn nhạc nền"
    music = await kids_auto.pick_music()
    spec = kaggle_worker.kids_spec(job, brief, music, story=st0, attempts=attempts0)
    job.step = ("Truyện bé: gửi Kaggle (" + ("vẽ 8 cảnh" if st0 else "viết truyện, vẽ 8 cảnh") + ", đọc giọng, dựng) — "
                + story_mod.describe(brief, data))
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
    job.script = (f"🧸 {title}\n{story_mod.describe(brief, data)}\n\n"
                  + (f"🎣 {st.get('hook')} [{st.get('hook_text')}]\n\n" if st.get("hook") else "") + story_mod.script_text(st)
                  + (f"\n\n(model viết {len(attempts)} lượt)" if len(attempts) > 1 else ""))
    t = res.get("timings") or {}
    job.note("ℹ️ Kaggle (GPU): " + (f"viết {t.get('llm', 0):.0f}s, " if not st0 else "")
             + f"vẽ {t.get('images', 0):.0f}s, đọc {t.get('tts', 0):.0f}s, dựng {t.get('render', 0):.0f}s"
             + ("; nhạc nền có" if res.get("music") else "; không nhạc nền") + ". ")
    for n in res.get("notes") or []:
        job.note(f"ℹ️ {n} ")
    kids_auto.remember_done(job, brief, st)
    logging.info("Truyện bé job %s: «%s» %.1fs, %s", job.id, title, job.out_dur, t)
    return video
