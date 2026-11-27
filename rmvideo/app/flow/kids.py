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
from kaggle_api import Parked
from queue_manager import Job
from rmvideo.kids import job as kids_job, llm, pitch, prompts, story as story_mod, writer


def _log(s: str) -> None:
    """Log một dòng của phần truyện bé (không lặp tiền tố khi hàm gọi đã ghi «Truyện bé:»)."""
    logging.info("%s", s if s.startswith("Truyện bé") else "Truyện bé: " + s)


def _parked(e: Parked) -> RuntimeError:
    return RuntimeError(f"{e}: phiên vẫn giữ chỗ trên Kaggle (tối đa {kaggle_worker.KIDS_PARK_MAX / 3600:.0f} giờ), bot "
                        "theo dõi và tự thu kết quả khi xong")


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
    if not brief.get("pitch"):                  # ý truyện: nghĩ nhiều ý, chọn ý hay nhất, rồi mới viết (1.4.1)
        job.step = "Truyện bé: AI nghĩ ý truyện — " + story_mod.describe(brief, data)
        import kids_auto
        got = await pitch.choose(brief, data, kids_auto.load_state()["stories"],
                                 [config.KIDS_GEMINI_MODEL, config.KIDS_GEMINI_REVISE_MODEL, ""],
                                 log=_log)
        if got:
            brief["pitch"] = {k: got[k] for k in ("kind", "title", "premise", "mood")}
            if got.get("things"):           # 1.5.3: 3 đồ vật ý đã chọn tới người viết (1.4.2 định vậy nhưng flow bỏ rơi)
                brief["pitch"]["things"] = list(got["things"])
            job.note(f"ℹ️ Ý truyện ({got['kind']}, chọn trong {got['n']} ý): «{got['title']}». ")
    job.step = "Truyện bé: Gemini viết truyện — " + story_mod.describe(brief, data)
    be = writer.GeminiBackend(config.KIDS_GEMINI_MODEL, log=_log,
                              revise_model=config.KIDS_GEMINI_REVISE_MODEL, thinking=config.KIDS_GEMINI_THINKING)
    spec = {"brief": brief, "channel": __import__("kids_auto").channel()}
    try:
        st, attempts = await asyncio.to_thread(llm.generate_story, spec, data, "", _log, srv=be)
    except Exception as e:
        if provider == "gemini":
            raise
        job.note(f"⚠️ Gemini không viết được ({str(e)[:120]}): Qwen trên Kaggle viết thay. ")
        logging.warning("Truyện bé job %s: Gemini lỗi (%s), để Kaggle viết", job.id, str(e)[:200])
        return None, []
    job.note(f"ℹ️ Gemini viết truyện: {be.summary()} ({be.calls} lượt gọi). ")
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
    if not (brief.get("collect") or kids_auto.parked_info(job.id)):
        why = await asyncio.to_thread(kaggle_worker.kids_ready)      # thiếu model trên Kaggle: báo ngay, khỏi viết truyện
        if why:
            raise RuntimeError(f"Truyện bé: {why}")
    # job thu kết quả (kids_auto.watch_parked tạo) hoặc job chạy lại sau khi bot tắt lúc phiên đang giữ chỗ (1.4.6)
    col = brief.get("collect") or kids_auto.parked_info(job.id)
    st0: dict | None = None
    try:
        if col:
            job.step = "Truyện bé: thu kết quả phiên Kaggle đang giữ chỗ"
            job.note(f"ℹ️ Thu kết quả phiên Kaggle giữ chỗ (truyện #{col.get('job') or job.id}). ")
            res = await kaggle_worker.collect_parked(col, tmp, on_step=lambda s: setattr(job, "step", s))
        else:
            st0, attempts0 = await write_on_botkeep(job, brief, data)
            job.step = "Truyện bé: chọn nhạc nền"

            def bgm_step(msg: str) -> None:
                job.step = msg
                logging.info("Truyện bé job %s: %s", job.id, msg)
            music = await kids_auto.pick_music(brief=brief, story_data=data, log=bgm_step)
            spec = kaggle_worker.kids_spec(job, brief, music, story=st0, attempts=attempts0)
            spec["park"] = {"job": job.id, "source": job.source, "chat_id": job.chat_id, "title_hint": job.title_hint}
            job.step = ("Truyện bé: gửi Kaggle (" + ("vẽ 8 cảnh" if st0 else "viết truyện, vẽ 8 cảnh") + ", đọc giọng, dựng) — "
                        + story_mod.describe(brief, data))
            res = await kaggle_worker.produce(spec, tmp, on_step=lambda s: setattr(job, "step", s))
    except Parked as e:
        raise _parked(e) from None
    except asyncio.CancelledError:                    # bot tắt / người dùng huỷ: kaggle_worker.abort quyết giữ hay bỏ mục
        raise
    except Exception:
        if not col:                                   # lỗi thật sau khi gửi (không phải giữ chỗ): bỏ mục đã ghi lúc gửi
            kids_auto.unpark(job.id)
        raise
    # Mục giữ chỗ (ghi từ lúc gửi) còn tới cuối hàm: bot tắt / quá hạn lúc đang kiểm video thì job chạy lại thu kết quả,
    # không gửi lại cả phiên. Xong (hoặc kết quả không dùng được — thu lại cũng vậy) mới bỏ; job 7 ngày 25/11 không bỏ
    # nên vòng tự động thấy phiên «xong» và tạo job 8 tải lại 78 MB thêm 15 phút.
    parked_job = int(col.get("job") or job.id) if col else job.id
    try:
        video = await _finish_kids(job, brief, res, col, st0, data)
    except asyncio.CancelledError:                    # bot tắt / người dùng huỷ (jobs.py tự bỏ mục khi huỷ)
        raise
    except Exception:
        kids_auto.unpark(parked_job)
        raise
    kids_auto.unpark(parked_job)
    return video


async def _finish_kids(job: Job, brief: dict, res: dict, col: dict | None, st0: dict | None, data: dict) -> str:
    """Kết quả Kaggle đã về máy: kiểm video, ảnh bìa, bài đăng, bộ nhớ truyện. Trả đường dẫn video."""
    import kids_auto
    video = res["video"]
    job.step = "Truyện bé: kiểm video"
    bad = await kids_job.qa_video(video)
    if bad:
        raise RuntimeError("Video Kaggle trả về không đạt: " + "; ".join(bad))
    st = res.get("story") or {}
    final = res.get("brief") or {}
    if final.get("words"):                  # Qwen viết trên Kaggle: từ hôm nay chốt ở đó (choose_words) -> bài đăng + bộ nhớ theo
        brief["words"] = final["words"]
        brief["plot"] = final.get("plot") or brief.get("plot") or ""
    files = res.get("files") or {}
    for name in ("cover.jpg", "thumb.jpg"):                 # delivery tìm ảnh bìa cạnh file video
        if files.get(name) and os.path.exists(files[name]):
            shutil.move(files[name], os.path.join(os.path.dirname(video), name))
    title, desc, tags = prompts.post_pack(st, brief, kids_auto.channel(), data)   # tiêu đề bài đăng có đuôi từ khoá (1.5.0)
    job.post = prompt_engine.platform_post(title, desc, tags)
    title = str(st.get("title") or title).strip() or title               # tên truyện trần cho hook / kịch bản / log
    job.hook = title
    job.srt = res.get("srt") or ""
    job.out_dur = float(res.get("duration") or 0.0)
    attempts = res.get("attempts") or []
    job.script = (f"🧸 {title}\n{story_mod.describe(brief, data)}\n\n"
                  + (f"🎣 {st.get('hook')} [{st.get('hook_text')}]\n\n" if st.get("hook") else "") + story_mod.script_text(st)
                  + (f"\n\n(model viết {len(attempts)} lượt)" if len(attempts) > 1 else ""))
    t = res.get("timings") or {}
    job.note("ℹ️ Kaggle (GPU): " + (f"viết {t.get('llm', 0):.0f}s, " if not st0 and not col else "")
             + f"vẽ {t.get('images', 0):.0f}s, đọc {t.get('tts', 0):.0f}s, dựng {t.get('render', 0):.0f}s"
             + ("; nhạc nền có" if res.get("music") else "; không nhạc nền") + ". ")
    for n in res.get("notes") or []:
        job.note(f"ℹ️ {n} ")
    kids_auto.remember_done(job, brief, st)
    logging.info("Truyện bé job %s: «%s» %.1fs, %s", job.id, title, job.out_dur, t)
    return video
