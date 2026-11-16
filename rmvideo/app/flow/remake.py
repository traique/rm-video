"""Làm lại video có lời (nấu ăn / review): đo, phân tích, cắt quảng cáo, đoạn mở đầu, AI viết lời theo mốc, duyệt, đọc giọng + dựng (Kaggle hoặc Botkeep).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os
import shutil

import beat
import cache
import config
import core_video
import cover as cover_mod
import ingredients
import kaggle_worker
import metrics
import ocr_text
import offline
import prompt_engine
import scenes
import slideshow
import state
import subs as subs_mod
import transcribe
import vocal_sep
from queue_manager import Job
from review import spoken_script
from rmvideo.app import job_factory
from rmvideo.app.flow import analysis, backends, common, photo, teaser
from rmvideo.app.flow import music as music_flow


AUTO_MIN_FRESH = 0.7     # 🤖 tự đăng: lời mới phải khác lời gốc ít nhất 70% (không đăng bản gần như chép lời)


SEP_MAX_SPEECH = 45      # giây có lời tối đa để tách giọng AI (máy 1 lõi ~1 giây xử lý / giây audio)


SEP_MAX_VIDEO = 120      # và video không dài quá 2 phút


REEL_MAX = 90            # Facebook Reels <= 90 giây: đoạn mở đầu không được đẩy video ra khỏi Reels


NO_SPEECH_WORDS = 6      # bóc lời ra dưới 6 chữ (không ai nói, hoặc chỉ "ừm", "ngon quá") = video KHÔNG LỜI


def no_speech(segs: list, text: str, timed: bool = True) -> bool:
    """Video không lời: quá ít lời để viết lại mà không bịa, chỉ chèn nhạc nền (NO_SPEECH=music). segs: các đoạn lời
    trong phần sẽ render. Dưới 6 chữ, hoặc chỉ MỘT câu (trước đây AI viết cả bài đọc một mạch từ đúng một câu đó, phần
    còn lại là bịa). timed=False (nguồn bóc lời không có mốc, đọc một mạch theo lời gốc) thì chỉ đếm chữ trên text."""
    if segs or timed:
        return len(segs) < 2 or sum(len(t.split()) for *_, t in segs) < NO_SPEECH_WORDS
    return len((text or "").split()) < NO_SPEECH_WORDS


def promo_cut(job: Job, segs: list, out_dur: float) -> float | None:
    """Mốc cắt đoạn quảng cáo cuối của người gốc. Bản đã duyệt dùng lại ĐÚNG mốc đã quyết lúc viết kịch bản
    (kịch bản duyệt không có đoạn đó; trước đây bản duyệt render lại nguyên đoạn quảng cáo — lỗi N1)."""
    if job.approved:
        return job.cut_at or None
    if job.mode in ("cook", "review") and config.CUT_PROMO != "0":
        return prompt_engine.find_promo_tail(segs, out_dur)
    return None


async def _process(job: Job, tmp: str, allow_review: bool, tasks: list, local_only: bool = False,
                   teaser_ok: bool = True):
    if job.mode == "photo":
        return await photo.process_photo(job, tmp)
    if job.local:
        # dùng file tại chỗ (file tải lên, hoặc nguồn đã giữ lại lúc chờ duyệt): không chép/chuyển; bot chết
        # giữa chừng thì file còn nguyên để chạy lại; worker xoá file khi job xong (trừ job chờ duyệt)
        job.step = "Dùng file nguồn đã có" if job.approved else "Nhận file tải lên"
        inp = job.local
        title, desc = job.title_hint, ""
    elif job.tg_file_id:
        job.step = "Tải file từ Telegram"
        inp = os.path.join(tmp, "input.mp4")
        await state.bot.download(job.tg_file_id, destination=inp)
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
            job.url, tmp, max_duration=config.LONG_MAX if job.long_mode == "full" or common.auto_full(job)
            else config.MAX_DURATION)
        common.check_source_allowed(job)

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
        music = await music_flow._get_music(job, tmp)
        if job.music_mix == "bg":
            # nhạc nền: giữ tiếng gốc, không đổi tốc độ; lấy đoạn hay nhất (điệp khúc) của bài làm nền
            job.step = "Chọn đoạn nhạc hay làm nhạc nền"
            vid = await core_video.probe_duration(inp)
            try:
                info = await asyncio.to_thread(slideshow.analyze_music, music)
                start = slideshow.choose_window(info, min(vid, max(4.0, info["duration"] - 0.5)))[0]
            except Exception as e:
                logging.warning("Không phân tích được nhạc (%s), dùng từ đầu bài", str(e)[:100])
                start = 0.0
            job.script = f"Nhạc nền lấy từ giây {start:.0f} của bài, giữ tiếng gốc (nhạc tự nhỏ lại khi tiếng gốc to)."
            job.step = "Render nhạc nền (FFmpeg)"
            args = {"speed": 1.0, "trim": start, "keep_orig": True}
            out, _ = await backends.kaggle_render(job, tmp, lambda: kaggle_worker.music_spec(job, inp, music, args), "Nhạc nền")
            if not out:
                out = os.path.join(tmp, "output.mp4")
                await core_video.render_music(inp, music, out, job.duration, **args)
            pack = await prompt_engine.generate(title, desc, "music", "", job.duration)
            job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], common.credit_line(job))
            return out

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
        out, _ = await backends.kaggle_render(job, tmp, lambda: kaggle_worker.music_spec(job, inp, music, sync), "Thay nhạc")
        if not out:
            out = os.path.join(tmp, "output.mp4")
            await core_video.render_music(inp, music, out, job.duration, **sync)

        pack = await prompt_engine.generate(title, desc, "music", "", job.duration)
        job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], common.credit_line(job))
        return out

    src_dur = await core_video.probe_duration(inp)
    if common.auto_full(job) and config.MAX_DURATION + 0.5 < src_dur:
        job.long_mode = "full"
        job.note((f"ℹ️ Video {src_dur:.0f}s, chọn «Giữ nguyên» nên làm nguyên cả video "
                                         f"(≤{config.LONG_MAX // 60} phút, dựng trên Kaggle). "))
    stretch = 1.0
    if job.duration and job.duration > src_dur + 1:
        # làm chậm NGAY TRONG lần render cuối thay vì mã hoá riêng cả video một lần (lỗi #7: thêm một thế hệ nén
        # và cả một lượt mã hoá); bóc lời, dò cảnh, tách giọng chạy trên bản gốc rồi nhân mốc thời gian lên
        stretch = min(job.duration / src_dur, 1.4)
        job.step = f"Kéo dài video gốc {src_dur:.0f}s -> {src_dur * stretch:.0f}s (chậm {1 / stretch:.2f}x)"
        if stretch < job.duration / src_dur:
            job.note(f"ℹ️ Video gốc chỉ {src_dur:.0f}s; kéo chậm tối đa 1.4x được {src_dur * stretch:.0f}s "
                     f"(chậm hơn sẽ lộ). ")

    vid_dur = src_dur * stretch
    limit = config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION
    out_dur = min(vid_dur, job.duration or vid_dur, limit)
    job.out_dur = out_dur
    render_dur = job.duration or (int(limit) if vid_dur > limit + 0.5 else 0)
    is_long = out_dur > 100                             # video dài: 720p
    if vid_dur > limit + 0.5 and not job.duration:
        job.note(f"ℹ️ Video {vid_dur:.0f}s, chỉ làm {limit}s đầu (chọn chế độ video dài để làm hết). ")
    target = job.duration or int(vid_dur)

    src_hash = await asyncio.to_thread(cache.file_hash, inp)
    want_gray = bool(job.subs and job.cover)
    has_aud = await core_video.has_audio(inp)
    why_an = analysis.analyze_remote(job, inp, local_only) if has_aud else "nguồn không có tiếng"
    an = None
    if not why_an or job.approved:                      # bản đã duyệt: lấy kết quả lần đầu trong cache (nếu có)
        job.step = "Bóc lời + phân tích hình trên Kaggle"
        an = await analysis.kaggle_analysis(job, inp, tmp, title, desc, src_hash, want_gray, run=not why_an)
    if an:
        text, raw_segs, cuts, bands = an["text"], an["segs"], an["cuts"], an["bands"]
        tm = an["timings"]
        if tm:
            job.note((
                f"ℹ️ Bóc lời + phân tích hình trên Kaggle (tách giọng {tm.get('sep', 0):.0f}s, Whisper "
                f"{tm.get('asr', 0):.0f}s, dò cảnh {tm.get('scenes', 0):.0f}s). "))
    else:
        job.step = ("Bóc lời thoại + dò chữ in cứng (song song)" if scenes.mode() == "light"
                    else "Bóc lời thoại + dò chữ in cứng + tách cảnh (song song)")
        audio_mp3 = os.path.join(tmp, "audio.mp3")

        async def _words():
            if not has_aud:                             # clip câm tiếng (quay màn hình, xuất -an): không có lời để bóc
                return "", []
            await core_video.extract_audio(inp, audio_mp3)
            return await transcribe.transcribe_segments(audio_mp3, title, desc)   # ("", []) nếu không có nguồn STT
        (text, raw_segs), (cuts, bands) = await asyncio.gather(
            _words(), scenes.analyze_cached(inp, tmp, src_dur, want_gray, src_hash,
                                            need_thumbs=not job.approved))   # 1 lượt giải mã: cảnh + ảnh + dải chữ
    if config.TEXT_DETECT == "ocr" and job.subs and job.cover and ocr_text.available():
        bands = await analysis.ocr_bands(inp, src_dur, src_hash, bands)
    segs = [(round(a * stretch, 2), round(b * stretch, 2), t) for a, b, t in raw_segs]   # trục thời gian đã làm chậm
    cuts = [round(c * stretch, 2) for c in cuts]
    speech = [(a, b) for a, b, _ in segs]               # lúc người gốc đang nói
    sep = {"ok": False, "windows": None, "secs": 0.0, "why": "", "engines": []}

    def _plan_separation():
        """Gọi SAU khi đã biết đoạn thực render (đã cắt quảng cáo): tách giọng AI chỉ các đoạn người gốc nói
        trong phần sẽ render; giới hạn theo SỐ GIÂY CÓ LỜI, không theo độ dài video."""
        end = out_dur / stretch                         # trục thời gian của video gốc
        win = (core_video.speech_windows([(a, min(b, end)) for a, b, _ in raw_segs if a < end], end)
               if raw_segs else None)
        secs = sum(b - a for a, b in win) if win is not None else end
        kim = out_dur <= SEP_MAX_VIDEO and secs <= SEP_MAX_SPEECH
        fast = offline.spleeter_available()          # gói offline: Spleeter nhanh ~30 lần, dùng cho video nhiều lời
        why = ("VOICE_SEPARATION=0" if config.VOICE_SEPARATION == "0" else "" if kim or fast else
               f"video {out_dur:.0f}s > {SEP_MAX_VIDEO}s" if out_dur > SEP_MAX_VIDEO else
               f"người gốc nói {secs:.0f}s > {SEP_MAX_SPEECH}s; cài gói offline để tách nhanh")
        sep.update(ok=job.mode == "cook" and not why, windows=win, secs=secs, why=why,
                   engines=(["kim"] if kim else []) + (["fast"] if fast else []))

    async def _late_cuts():
        """Phân tích nhẹ không dò chuyển cảnh; video không lời mới cần (mỗi cảnh một câu): dò ở mức vừa lúc này."""
        job.step = "Video không lời: dò chuyển cảnh"
        cuts[:] = [round(c * stretch, 2) for c in await scenes.detect_cuts(inp)]
        return len(cuts) >= 2

    tz = None                                           # đoạn mở đầu (cảnh món đã xong chiếu trước), quyết ở dưới

    def src_time(x: float) -> float:                    # mốc trên video ra -> giây trên video gốc
        if tz and x < tz["dur"]:
            return tz["a"] + (0.0 if tz.get("still") else min(x, tz["dur"] - 0.1))
        return (x - (tz["dur"] if tz else 0.0)) / stretch

    def keyframes_for(sg):                              # ảnh cho AI: ảnh GIỮA mỗi đoạn, lấy trên video gốc
        return scenes.keyframes(inp, [(m, m, t) for m, t in ((src_time((a + b) / 2), t) for a, b, t in sg)])

    async def _ambient():
        """Chế độ nấu ăn: tiếng nền KHÔNG còn giọng người gốc. Ưu tiên tách giọng AI (chỉ các đoạn có lời);
        không được -> tắt tiếng nền đúng lúc người gốc nói; không có mốc -> bỏ tiếng nền."""
        if job.mode != "cook":
            return None, None
        if an and an.get("ambient") and os.path.exists(an["ambient"]):
            return an["ambient"], None                  # đã tách CẢ BÀI trên Kaggle lúc phân tích
        if sep["ok"]:
            for k_eng, engine in enumerate(sep["engines"]):     # Kim_Vocal_2 (sạch hơn) -> Spleeter (nhanh hơn)
                k = cache.key("ambient", src_hash, sep["windows"], engine)
                hit = cache.get("ambient", k, ".wav")
                if hit:
                    return hit, None
                p = os.path.join(tmp, f"ambient_{engine}.wav")
                run = vocal_sep.remove_voice if engine == "kim" else offline.remove_voice_fast
                if await run(inp, p, windows=sep["windows"]):
                    metrics.record("sep", "onnx" if engine == "kim" else "spleeter", fallback=k_eng > 0)
                    try:                        # chuyển (không chép): tiếng nền 10MB/phút không nằm hai chỗ tới hết job
                        p = cache.put("ambient", k, p, ".wav", move=True)
                    except OSError:
                        pass
                    return p, None
                metrics.record("sep", "onnx" if engine == "kim" else "spleeter", ok=False, fallback=k_eng > 0)
            metrics.record("sep", "gate", fallback=True)
        return None, (speech or [(0.0, 99999.0)])

    def _start_cover(post_title: str):
        """Ảnh bìa chạy song song với render (chấm điểm khung + AI chọn ảnh: nhẹ CPU, chủ yếu chờ mạng)."""
        if config.COVER == "0":
            return None
        a = scenes.analyzed(inp)
        t = asyncio.create_task(cover_mod.make_cover(
            inp, tmp, job.mode, post_title, src_dur, cuts=cover_src["cuts"], end=cover_src["end"], bands=bands,
            thumbs_dir=a["dir"] if a else None, thumb_times=a.get("times") if a else None, at=cover_src["at"]))
        tasks.append(t)
        return t

    async def _finish_cover(t):
        if t is not None and not t.done():
            job.step = "Tạo ảnh bìa"
        if t is not None:
            await t

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
        job.note(f"ℹ️ Đã cắt bỏ {out_dur - cut:.0f}s quảng cáo sản phẩm của người gốc ở cuối. ")
        out_dur = render_dur = cut
        job.out_dur = out_dur
    _plan_separation()
    # ảnh bìa: thời gian theo video GỐC (trước khi lùi mốc cho đoạn mở đầu); "at" = khung AI đã chọn sẵn
    cover_src = {"cuts": [c / stretch for c in cuts], "end": out_dur / stretch, "at": None}
    silent = no_speech([s for s in segs if s[0] < out_dur - 0.5], text, bool(raw_segs))
    if not job.approved and config.NO_SPEECH != "narrate" and silent:
        return await music_flow._music_only(job, inp, tmp, title, desc, out_dur, render_dur, stretch, is_long, bands,
                                 local_only, _start_cover, _finish_cover)
    # ----- Đoạn mở đầu: cảnh MÓN ĐÃ XONG chiếu trước 3 giây kèm câu hook (chỉ video nấu ăn có lời) -----
    if job.approved:
        tz = dict(job.teaser) if job.teaser else None   # bản đã duyệt: đúng đoạn mở đầu đã quyết lúc viết lời
    elif teaser_ok and job.mode == "cook" and config.TEASER != "0" and not silent:
        job.step = "AI chọn cảnh món đã xong (ảnh bìa + đoạn mở đầu)"
        tz, moment, why_tz = await teaser.plan_teaser(inp, job.mode, title, src_dur, cover_src["cuts"], cover_src["end"])
        if moment:
            cover_src["at"] = moment["t"]               # ảnh bìa = đúng khung đó, khỏi hỏi AI lần nữa
        job.teaser = tz or {}
        if why_tz:
            job.note(f"ℹ️ Không chiếu trước cảnh món ({why_tz}). ")
    if tz:
        cover_src["at"] = tz["t"]
        T = float(tz["dur"])
        full = out_dur + T
        # chọn 30/60/90 giây thì giữ đúng độ dài đó; "giữ nguyên" mà video ≤ 90s thì không để vượt 90s (hết là Reels)
        total = round(min(full, limit, job.duration or (REEL_MAX if out_dur <= REEL_MAX + 0.01 else full)), 2)
        segs = [(round(a + T, 2), round(b + T, 2), t) for a, b, t in segs]
        cuts = [T] + [round(c + T, 2) for c in cuts]    # đoạn mở đầu -> video chính cũng là một lần chuyển cảnh
        speech = [(a + T, b + T) for a, b in speech]
        render_dur = total if (render_dur or total < full - 0.01) else 0
        out_dur = job.out_dur = total
        job.note((
            f"ℹ️ Mở đầu bằng {T:.0f} giây cảnh món đã xong (giây {tz['t']:.0f} video gốc"
            + (", cảnh ngắn nên đứng hình" if tz.get("still") else "") + ") kèm câu hook"
            + (f"; bớt {full - total:.0f}s cuối cho vừa {total:.0f}s" if total < full - 0.5 else "") + ". "))
    why_local = "chạy trên Botkeep theo yêu cầu" if local_only else kaggle_worker.usable(job, inp)
    remote = not why_local
    prompt_engine.set_voice_rate(kaggle_worker.words_per_sec(job.voice) if remote else None)
    if kaggle_worker.voice_name(job.voice) and why_local:
        job.note(f"ℹ️ Giọng VieNeu chưa dùng được ({why_local}): đọc bằng giọng edge. ")
    # Tách giọng gốc (nặng CPU) bắt đầu NGAY, chạy trong lúc chờ AI viết kịch bản và đọc giọng (việc mạng, CPU
    # rảnh). Trước đây chỉ bắt đầu cùng lúc đọc giọng nên thời gian chờ AI bị phí. Chạy trên Kaggle thì Kaggle tách.
    amb_task = None if remote else asyncio.create_task(_ambient())
    if amb_task:
        tasks.append(amb_task)

    def _local_ambient():
        nonlocal amb_task
        if amb_task is None:                            # Kaggle lỗi / không dùng: tách giọng trên Botkeep
            amb_task = asyncio.create_task(_ambient())
            tasks.append(amb_task)
        return amb_task

    target = int(out_dur)                               # đường dự phòng (đọc một mạch): theo độ dài SẼ dựng
    if raw_segs:                                        # (sau khi cắt quảng cáo / thêm đoạn mở đầu)
        text = " ".join(t for a, _, t in segs if a < out_dur - 0.5 and t)
    # ----- Đường chính: giọng KHỚP HÌNH -----
    segs = [s for s in segs if s[0] < out_dur - 0.5]
    timed = None
    if job.approved:                                    # kịch bản đã duyệt: bỏ qua bước AI
        timed = {"segments": job.approved, "script": spoken_script(job.approved),
                 "title": "", "caption": "", "hashtags": "", "fresh": 1.0}
    elif len(segs) >= 2:
        segs = scenes.snap_to_cuts(segs, cuts)          # câu mới vang lên đúng lúc chuyển cảnh
        job.step = f"Viết lại {len(segs)} đoạn theo mốc lời" + (f" + {len(cuts)} điểm chuyển cảnh" if cuts else "") + " (AI)"
        timed = await prompt_engine.generate_timed_any(
            title, desc, job.mode, segs, out_dur,
            images_for=keyframes_for,                   # AI nhìn ảnh từng đoạn để lời khớp hình
            **({"teaser": float(tz["dur"])} if tz else {}))
    elif len(cuts) >= 2 or (not cuts and scenes.mode() == "light" and await _late_cuts()):
        sc = scenes.scene_segments(cuts, out_dur)       # video không lời: AI nhìn ảnh từng cảnh
        job.step = f"AI xem {len(sc)} cảnh và viết lời cho từng cảnh"
        timed = await prompt_engine.generate_scenes(title, desc, job.mode, sc, await keyframes_for(sc), out_dur)
    if timed:
        job.script = spoken_script(timed["segments"]) or timed["script"]
        if timed.get("hook") and not job.approved:
            job.hook = timed["hook"]                    # chữ hook in màn hình 3 giây đầu + ảnh bìa
        if timed.get("fresh", 1.0) < AUTO_MIN_FRESH:            # AI lỗi một phần -> nhiều đoạn giữ nguyên lời gốc
            job.no_autopost = f"lời mới chỉ {timed['fresh'] * 100:.0f}% khác lời gốc"
        if not job.approved:
            job.post = prompt_engine.platform_post(timed["title"], timed["caption"], timed["hashtags"], common.credit_line(job))
        if allow_review and job.source != "auto" and (job.review or config.REVIEW_SCRIPT == "1") and not job.approved:
            job.approved = job_factory.approved_segments(timed["segments"])
            if amb_task:
                amb_task.cancel()    # dừng chờ duyệt: tách giọng làm sau, lúc render bản đã duyệt
                await asyncio.gather(amb_task, return_exceptions=True)
            if not job.local:        # nguồn từ link/Telegram: giữ lại để render sau khi duyệt KHÔNG phải tải lại (#8)
                keep = os.path.join(config.UPLOAD_DIR, f"review_{job.id}{os.path.splitext(inp)[1] or '.mp4'}")
                await asyncio.to_thread(shutil.move, inp, keep)
                job.local = keep
            job.status, job.step = "review", "Chờ bạn duyệt/sửa kịch bản rồi bấm Render"
            return "REVIEW"
        sem = asyncio.Semaphore(3)

        async def _one(seg):
            if seg.get("ost") == "orig" or not (seg.get("text") or "").strip():   # giữ tiếng gốc / không lời: không đọc
                return {"path": None, "words": []}
            p = os.path.join(tmp, f"clip_{seg['i']}_{cache.key('clip', seg['text'])[:10]}.mp3")
            async with sem:
                w = await core_video.synth_clip_cached(seg["text"], job.voice, p)   # đoạn không đổi: lấy từ cache
            return {"path": p, "words": w}

        async def _durs(cl):
            async def one(c):
                return await core_video.speech_duration(c["path"], c.get("words")) if c.get("path") else 0.0
            return list(await asyncio.gather(*[one(c) for c in cl]))

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
                job.script = spoken_script(segs_t)
            return changed

        # ước lượng độ dài TRƯỚC khi đọc (kiểu VideoLingo): đoạn chắc chắn tràn thì rút gọn luôn, đỡ một lượt
        # gọi TTS không chính thức rồi lại phải đọc lại. Ước lượng thận trọng (x0.85) để không rút gọn nhầm.
        if not job.approved:
            # chạy trên Kaggle: biết đúng tốc độ của giọng VieNeu và KHÔNG có vòng AI rút gọn sau khi đọc (Kaggle không
            # giữ key) -> ước tính đủ (x1.0); trên Botkeep còn vòng rút gọn sau nên ước tính thận trọng (x0.85)
            est_k = 1.0 if remote else 0.85
            est = [0.0 if sg.get("ost") == "orig" else
                   prompt_engine.spoken_words(sg["text"]) / prompt_engine.words_per_sec() * est_k for sg in segs_t]
            _, sure_long = core_video.plan_voice(est, segs_t, out_dur)
            if sure_long:
                job.step = f"Rút gọn trước {len(sure_long)} đoạn chắc chắn quá dài"
                await _shorten(sure_long, est)

        pre_cover = None
        if remote:
            post_title = job.hook or (job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title)
            # Kaggle 4 lõi: không cần giới hạn của Botkeep; tách CẢ BÀI nên không cần mốc lời (bóc lời không nghe được
            # chữ nào thì vẫn tách, thay vì tắt hết tiếng nền)
            sep_r = dict(sep, ok_remote=job.mode == "cook" and config.VOICE_SEPARATION != "0")
            spec = None
            try:
                spec = kaggle_worker.build_spec(
                    job, segs_t, out_dur, render_dur, stretch, not is_long, post_title, bands, pos_y, sep_r, speech,
                    config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION, local_path=inp,
                    src=job.remote_src or None, amb_token=an["token"] if an else "", teaser=tz)
            except Exception as e:
                job.note(f"ℹ️ Không gửi được sang Kaggle ({str(e)[:160]}): làm trên Botkeep. ")

            async def _finish_remote(res, cover="start"):
                metrics.record("kaggle", "kaggle")
                job.srt = res.get("srt") or ""
                job.note("".join(res.get("notes") or []))
                await _finish_cover(_start_cover(post_title) if cover == "start" else cover)
                job.final_segments = common.final_timeline(segs_t, res["plan"], out_dur)
                tm = res.get("timings") or {}
                eng = kaggle_worker.voice_engine(job.voice)
                job.note((
                    f"ℹ️ Kaggle ({'GPU' if res.get('gpu') else 'CPU'}) đọc giọng "
                    + (f"VieNeu {kaggle_worker.voice_name(job.voice)}" if eng == "vieneu" else eng)
                    + f" {tm.get('tts', 0):.0f}s, dựng {tm.get('render', 0):.0f}s"
                    + ("; đã tách bỏ giọng gốc bằng AI" if res.get("amb") else "")
                    + (f"; {len(res['too_long'])} đoạn vẫn hơi dài (đọc nhanh hơn)" if res.get("too_long") else "")
                    + ". "))
                return res["video"]
            if spec is not None and kaggle_worker.collecting():
                # video dài: chạy chung một phiên với các phần khác; kèm lời đã chốt để nếu Kaggle lỗi thì làm lại
                # phần này trên Botkeep mà KHÔNG bóc lời + gọi AI lần nữa (đi đường "kịch bản đã duyệt")
                return kaggle_worker.Deferred(spec, _finish_remote, job_factory.approved_segments(segs_t))
            if spec is not None:
                pre_cover = _start_cover(post_title)    # ảnh bìa làm trên Botkeep trong lúc Kaggle chạy
                try:
                    job.step = "Kaggle: chuẩn bị"
                    res = await kaggle_worker.produce(spec, tmp, on_step=lambda s: setattr(job, "step", s))
                except Exception as e:
                    logging.warning("Kaggle lỗi, làm tiếp trên Botkeep: %s", e)
                    metrics.record("kaggle", "kaggle", ok=False, detail=str(e))
                    job.note(f"ℹ️ Kaggle lỗi ({str(e)[:160]}): đọc giọng + dựng trên Botkeep. ")
                else:
                    return await _finish_remote(res, pre_cover)
        _local_ambient()
        job.step = f"Đọc giọng {len(segs_t)} đoạn song song" + (" + tách giọng gốc" if sep["ok"] else "")
        clips = list(await common.gather_all([_one(s) for s in segs_t]))
        durs = await _durs(clips)
        plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        if too_long:                                     # đoạn nào vẫn quá dài -> AI viết ngắn lại 1 lần
            job.step = f"Rút gọn {len(too_long)} đoạn quá dài"
            changed = await _shorten(too_long, durs)
            if changed:
                redo = await common.gather_all([_one(segs_t[i]) for i in changed])
                for i, c in zip(changed, redo):
                    clips[i] = c
                for i, d in zip(changed, await _durs(redo)):
                    durs[i] = d
            plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        voice_track = os.path.join(tmp, "voice.wav")
        await core_video.compose_voice(clips, plan, voice_track, out_dur)
        got_subs = False
        if job.subs:
            words = await core_video.words_for_track(clips, plan, voice_track, core_video.subtitle_texts(segs_t, tz))
            if words:
                job.srt = subs_mod.to_srt(words)
                subs_mod.write_ass(words, ass, pos_y, config.SUB_STYLE)
                got_subs = True
        job.step = "Chờ tách giọng gốc xong" if sep["ok"] else "Render (FFmpeg)"
        amb_wav, gate = await _local_ambient()
        job.step = "Render (FFmpeg)"
        out = os.path.join(tmp, "output.mp4")
        post_title = job.hook or (job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title)
        cover_task = pre_cover or _start_cover(post_title)
        notes = []
        await core_video.render(inp, voice_track, out, job.mode, render_dur,
                                ass if got_subs else None, bands, amb_wav, gate, allow_hd=not is_long,
                                title=post_title, tmp=tmp,
                                stretch=stretch, ost=core_video.ost_windows(segs_t, out_dur), notes=notes, teaser=tz)
        if notes:
            job.note("".join(notes))
        await _finish_cover(cover_task)
        job.final_segments = common.final_timeline(segs_t, plan, out_dur)
        if job.mode == "cook":
            job.note((
                "ℹ️ Đã tách bỏ giọng gốc bằng AI. " if amb_wav else
                f"ℹ️ Không tách giọng AI ({sep['why']}): tắt tiếng nền lúc người gốc nói. " if sep["why"] else
                "ℹ️ Tách giọng AI không chạy được (máy chậm/thiếu RAM): tắt tiếng nền lúc người gốc nói. "))
        fast = sum(1 for p in plan if p["factor"] > 1.12)
        drift = max(abs(p["start"] - s["start"]) for p, s in zip(plan, segs_t))
        job.note((
            (f"ℹ️ Lời mới {timed['fresh'] * 100:.0f}% khác lời gốc; " if "fresh" in timed else "ℹ️ ")
            + f"{len(clips)} đoạn giọng" + (f", {len(cuts)} điểm chuyển cảnh" if cuts else "") + f"; lệch tối đa {drift:.1f}s"
            + (f"; {fast} đoạn nói nhanh" if fast else "")
            + (f"; {len(too_long)} đoạn vẫn hơi dài" if too_long else "")
            + (f"; đã che {len(bands)} dải chữ gốc" if bands else "") + "."))
        return out

    # ----- Đường cũ: không có mốc thời gian -> đọc một mạch -----
    job.step = "Viết kịch bản + content (AI)"
    pack = await prompt_engine.generate(title, desc, job.mode, text, target)
    if pack.get("fallback"):
        job.note(("⚠️ AI viết kịch bản lỗi (xem log), đang dùng kịch bản mẫu. "
                                         "Kiểm tra key/model trong Cài đặt. "))
        job.no_autopost = "AI viết kịch bản lỗi, video dùng kịch bản mẫu"
    job.script = pack["script"]
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], common.credit_line(job))

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
            job.post = prompt_engine.platform_post(pack2["title"], pack2["caption"], pack2["hashtags"], common.credit_line(job))
            got_subs = await core_video.tts(job.script, voice_mp3, job.voice,
                                            ass if job.subs else None, pos_y)

    job.step = "Render (FFmpeg)"
    out = os.path.join(tmp, "output.mp4")
    amb_wav, gate = await _local_ambient()
    post_title = job.hook or (job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title)
    cover_task = _start_cover(post_title)
    notes = []
    await core_video.render(inp, voice_mp3, out, job.mode, render_dur,
                            ass if (job.subs and got_subs) else None, bands, amb_wav, gate, allow_hd=not is_long,
                            title=post_title, tmp=tmp, stretch=stretch, notes=notes, teaser=tz)
    if notes:
        job.note("".join(notes))
    await _finish_cover(cover_task)
    if bands:
        job.note(f"ℹ️ Đã che {len(bands)} dải chữ gốc và đặt phụ đề mới lên đó.")
    return out
