"""Nhạc: video KHÔNG LỜI chèn nhạc nền (thư viện nhạc, Kaggle hoặc Botkeep) và lấy nhạc của job (Telegram, tải lên, link).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os

import bgm
import config
import core_video
import kaggle_worker
import metrics
import prompt_engine
import state
from queue_manager import Job
from rmvideo.app.flow import common


BED_ORIG_VI = {"off": "bỏ tiếng gốc", "low": "giữ tiếng gốc nhỏ dưới nhạc", "keep": "giữ tiếng gốc, nhạc nằm dưới"}


async def _pick_bgm(tmp: str, prefer_links: bool, need: float) -> tuple:
    """(bài, file nhạc, giây bắt đầu, lý do lỗi). Bài tải lỗi thì thử một bài khác trong thư viện."""
    tried, why = set(), ""
    for _ in range(2):
        item = bgm.pick(prefer_links=prefer_links, exclude=tried)
        if not item:
            return None, None, 0.0, why or "thư viện nhạc nền trống"
        try:
            music = await bgm.fetch(item, tmp)
            return item, music, await asyncio.to_thread(bgm.best_start, music, need), ""
        except Exception as e:
            logging.warning("Không tải được nhạc nền %s: %s", item["key"], e)
            tried.add(item["key"])
            why = f"tải nhạc nền «{item['name']}» lỗi ({str(e)[:100]})"
    return None, None, 0.0, why


async def _music_only(job: Job, inp: str, tmp: str, title: str, desc: str, out_dur: float, render_dur: float,
                      stretch: float, is_long: bool, bands, local_only: bool, start_cover, finish_cover):
    """Video KHÔNG LỜI (NO_SPEECH=music): chèn nhạc nền, KHÔNG để AI xem hình rồi bịa lời (dễ nói sai sự thật của
    video gốc: đoán sai món, sai nguyên liệu, bịa bước). Vẫn dựng như remake: tiêu đề + hook, logo, che chữ gốc, ảnh
    bìa. Có Kaggle thì dựng trên Kaggle (CPU, không đọc giọng, không tách giọng)."""
    job.step = "Video không lời: chọn nhạc nền"
    on_kaggle = not local_only and kaggle_worker.is_enabled()
    item, music, trim, why = await _pick_bgm(tmp, on_kaggle and not (config.PUBLIC_URL or "").strip(), out_dur)
    orig = config.BGM_ORIG if music else "keep"
    if music:
        job.script = (f"🎵 Video không lời: chèn nhạc nền «{item['name']}» (từ giây {trim:.0f} của bài), "
                      f"{BED_ORIG_VI.get(orig, orig)}; không thêm lời đọc để không nói sai nội dung video gốc.")
    else:
        job.script = (f"🎵 Video không lời nhưng chưa chèn được nhạc nền ({why}): giữ nguyên tiếng gốc, không thêm "
                      "lời đọc. Thêm nhạc ở ô «🎵 Nhạc nền cho video không lời» trên web (link TikTok/YouTube "
                      "hoặc tải file lên).")
        job.no_autopost = f"video không lời, {why}"
    job.step = "Viết tiêu đề + nội dung đăng (AI)"
    pack = await prompt_engine.post_only(title, desc, job.mode)
    if pack.get("fallback"):
        job.no_autopost = job.no_autopost or "AI viết nội dung đăng lỗi, dùng mẫu"
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"],
                                           common.credit_line(job, narrated=False))
    job.hook = pack.get("hook") or prompt_engine.clean_hook(pack["title"] if not pack.get("fallback") else "")
    post_title = job.hook or (job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title)
    max_dur = config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION
    info = ((f"ℹ️ Video không lời: chèn nhạc nền thay cho lời đọc ({BED_ORIG_VI.get(orig, orig)})" if music else
             "ℹ️ Video không lời: giữ nguyên tiếng gốc, không thêm lời đọc (chưa có nhạc nền)")
            + (f"; đã che {len(bands)} dải chữ gốc" if bands else "") + ". ")

    def _done():
        if music:
            bgm.remember(item)
        job.note(info)

    async def _finish_remote(res, cover="start"):
        metrics.record("kaggle", "kaggle")
        job.note("".join(res.get("notes") or [])
                 + f"ℹ️ Dựng trên Kaggle (CPU) {(res.get('timings') or {}).get('render', 0):.0f}s. ")
        await finish_cover(start_cover(post_title) if cover == "start" else cover)
        _done()
        return res["video"]

    pre_cover = None
    if on_kaggle:
        spec = None
        try:
            spec = kaggle_worker.bed_spec(job, out_dur, render_dur, stretch, not is_long, post_title, bands, max_dur,
                                          music, (item or {}).get("url", "") if music else "", trim, orig,
                                          local_path=inp, src=job.remote_src or None)
        except Exception as e:
            job.note(f"ℹ️ Chưa dựng được trên Kaggle ({str(e)[:140]}): dựng trên Botkeep. ")
        if spec is not None and kaggle_worker.collecting():
            return kaggle_worker.Deferred(spec, _finish_remote, [])     # phần của video dài: chạy chung một phiên
        if spec is not None:
            pre_cover = start_cover(post_title)
            try:
                job.step = "Kaggle: dựng video + nhạc nền"
                res = await kaggle_worker.produce(spec, tmp, on_step=lambda st: setattr(job, "step", st))
            except Exception as e:
                logging.warning("Kaggle lỗi (video không lời), dựng trên Botkeep: %s", e)
                metrics.record("kaggle", "kaggle", ok=False, detail=str(e))
                job.note(f"ℹ️ Kaggle lỗi ({str(e)[:140]}): dựng trên Botkeep. ")
            else:
                return await _finish_remote(res, pre_cover)
    job.step = "Trộn nhạc nền"
    bed = os.path.join(tmp, "bed.wav")
    await core_video.music_bed(inp, music, bed, out_dur, trim, orig, stretch)
    job.step = "Render (FFmpeg)"
    out = os.path.join(tmp, "output.mp4")
    cover_task = pre_cover or start_cover(post_title)
    notes: list = []
    await core_video.render(inp, bed, out, job.mode, render_dur, None, bands, bed, None, allow_hd=not is_long,
                            title=post_title, tmp=tmp, stretch=stretch, notes=notes, music_bed=True)
    job.note("".join(notes))
    await finish_cover(cover_task)
    _done()
    return out


async def _get_music(job: Job, tmp: str) -> str:
    """Nhạc của job: file gửi qua Telegram, file tải lên web, hoặc link (TikTok: bản nhạc gốc của video)."""
    if job.music_tg_file_id:
        music = os.path.join(tmp, "music.m4a")
        await state.bot.download(job.music_tg_file_id, destination=music)
        return music
    if job.music_local:
        return job.music_local
    if job.music_url:
        return await core_video.download_audio(job.music_url, tmp)
    raise RuntimeError("Chế độ này cần nhạc: gửi link (TikTok/YouTube...) hoặc file nhạc.")
