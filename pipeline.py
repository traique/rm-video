"""Xử lý MỘT job: tải/nhận nguồn -> bóc lời -> phân tích hình -> AI viết kịch bản -> (duyệt) -> đọc giọng ->
xếp giọng + phụ đề -> tiếng nền -> render -> giao. Cắt phần video dài. Không biết gì về Telegram/web ngoài
state.bot (gửi file) và hàng đợi."""
import asyncio
import logging
import os
import shutil

import beat
import cache
import config
import core_video
import cover as cover_mod
import highlights
import ingredients
import metrics
import ocr_text
import offline
import prompt_engine
import scenes
import slideshow
import state
import subs as subs_mod
import vocal_sep
from queue_manager import Job
from review import spoken_script
from state import OUT_DIR, notify, queue
from storage import _cleanup_outputs
from transcribe import transcribe_segments


async def _gather_all(coros: list) -> list:
    """Như asyncio.gather nhưng một việc lỗi thì huỷ các việc còn lại. Trước đây các đoạn đọc giọng khác vẫn chạy tiếp
    sau khi job đã hỏng, gọi TTS vô ích và ghi vào thư mục tạm đã bị xoá ("No such file or directory: .../clip_13_...")."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


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


def check_source_allowed(job: Job):
    """ALLOWED_SOURCES (Cài đặt): chỉ remake video của các kênh đã được phép (kênh của bạn, kênh đã cấp phép).
    Trống = không giới hạn. So khớp không phân biệt hoa thường với uploader / uploader_id."""
    allowed = {x.strip().lstrip("@").lower() for x in config.ALLOWED_SOURCES.split(",") if x.strip()}
    if not allowed or job.mode == "download":
        return
    meta = job.source_meta or {}
    names = {str(meta.get(k) or "").lstrip("@").lower() for k in ("uploader", "uploader_id")} - {""}
    if not names & allowed:
        raise RuntimeError(f"Kênh nguồn «{meta.get('uploader') or '?'}» không nằm trong danh sách ALLOWED_SOURCES "
                           "(Cài đặt). Chỉ remake video của kênh đã được phép.")


async def _long_source(job: Job, tmp: str, what: str) -> tuple:
    """Nguồn cho chế độ cắt phần / đoạn hay: file tải lên, file Telegram hoặc link (≤ SPLIT_MAX). Trả (src, title, desc)."""
    if job.local:
        return job.local, job.title_hint, ""            # dùng file tải lên tại chỗ (không chép), worker xoá khi job xong
    if job.tg_file_id:
        job.step = "Tải file từ Telegram"
        src = os.path.join(tmp, "source.mp4")
        await state.bot.download(job.tg_file_id, destination=src)
        return src, job.title_hint, ""
    job.step = what
    src, title, desc, job.source_meta = await core_video.download(job.url, tmp, max_duration=config.SPLIT_MAX)
    check_source_allowed(job)
    return src, title, desc


async def _remake_pieces(job: Job, tmp: str, src: str, title: str, pieces: list, kfs: list) -> tuple:
    """Cắt và remake từng phần, xong phần nào giao phần đó. pieces: [{"a", "b", "label", "title"?, "note"?}].
    Phần đã giao (bot khởi động lại giữa chừng) thì bỏ qua. Trả (số phần xong, [lỗi])."""
    ok, fails = 0, []
    delivered = {p["label"] for p in job.parts}
    for i, pc in enumerate(pieces):
        a, b, label = pc["a"], pc["b"], pc["label"]
        if label in delivered:
            ok += 1
            continue
        part_dir = os.path.join(tmp, f"p{i}")
        os.makedirs(part_dir, exist_ok=True)
        piece = os.path.join(part_dir, "piece.mp4")
        job.step = f"{label}: cắt {a:.0f}s-{b:.0f}s"
        how = await core_video.cut(src, piece, a, b, copy=core_video.is_keyframe(a, kfs))
        logging.info("%s: cắt %.1f-%.1fs (%s)", label, a, b, "chép luồng" if how == "copy" else "mã hoá lại")
        sub = Job(url="", mode=job.mode, duration=0, voice=job.voice, subs=job.subs, cover=job.cover,
                  local=piece, title_hint=f"{pc.get('title') or title} ({label})", source=job.source,
                  chat_id=job.chat_id, source_meta=job.source_meta)
        sub.id = job.id

        async def _mirror(s=sub, lb=label):           # hiện tiến độ của phần đang làm lên job cha
            while True:
                if s.step:
                    job.step = f"{lb}: {s.step}"
                await asyncio.sleep(2)
        mirror = asyncio.create_task(_mirror())
        try:
            if job.mode == "download":
                out = piece                            # chỉ cắt, không remake
                caption = f"{pc.get('title') or title} ({label})"
            else:
                out = await process(sub, part_dir, allow_review=False)   # cắt phần không dừng chờ duyệt
                caption = (sub.post or sub.script or "").replace("📌 ", f"📌 [{label}] ", 1)
            if pc.get("note"):
                caption = pc["note"] + "\n" + caption
            await deliver(job, out, label, caption)
            queue.save(job)                            # ghi lại phần đã giao (khởi động lại thì không giao lại)
            ok += 1
        except Exception as e:
            logging.exception("%s thất bại", label)
            fails.append(f"{label}: {str(e)[:120]}")
            if job.source == "tg":
                await notify(job.chat_id, f"❌ {label} lỗi: {e}")
        finally:
            mirror.cancel()
            for d in os.listdir(part_dir):            # giải phóng ổ đĩa ngay sau mỗi phần
                p = os.path.join(part_dir, d)
                if os.path.isfile(p):
                    os.remove(p)
    return ok, fails


def _even_pieces(dur: float, part_len: int, cuts: list, sil: list, kfs: list) -> list:
    bounds = scenes.split_points(dur, part_len, cuts, sil)
    bounds = core_video.snap_to_keyframes(bounds, kfs)        # cắt đúng khung khoá -> chép luồng, không mã hoá lại
    n = len(bounds) - 1
    return [{"a": bounds[i], "b": bounds[i + 1], "label": f"Phần {i + 1}/{n}"} for i in range(n)]


async def process_split(job: Job, tmp: str):
    """Chế độ ✂️: cắt video dài (≤10 phút) thành các phần ~60/90s tại chỗ ngưng nói/chuyển cảnh,
    remake từng phần, xong phần nào giao phần đó."""
    part_len = 90 if job.long_mode == "split90" else 60
    src, title, _ = await _long_source(job, tmp, "Tải video dài")
    dur = min(await core_video.probe_duration(src), config.SPLIT_MAX)
    job.step = "Tìm điểm cắt (chỗ ngưng nói + chuyển cảnh)"
    kfs = await core_video.keyframe_times(src)
    pieces = _even_pieces(dur, part_len, await scenes.detect_cuts(src), await core_video.silences(src), kfs)
    ok, fails = await _remake_pieces(job, tmp, src, title, pieces, kfs)
    n = len(pieces)
    job.script = f"Đã xong {ok}/{n} phần (mỗi phần ~{part_len}s, cắt tại chỗ ngưng nói/chuyển cảnh)."
    if fails:
        job.error = "; ".join(fails)
    if ok == 0:
        raise RuntimeError("Không phần nào thành công. " + "; ".join(fails))


async def _highlight_images(src: str, tmp: str, dur: float) -> tuple:
    """Một lượt giải mã: điểm chuyển cảnh + ảnh thu nhỏ; lấy ~1 ảnh mỗi max(8s, dur/60) cho AI xem. (cuts, ảnh, giây)."""
    cuts, _ = await scenes.analyze(src, tmp, dur, False)
    a = scenes._ANALYZED.get(src)
    if not a or not a["n"] or not config.GEMINI_API_KEY:
        return cuts, [], []
    step = max(8.0, dur / 60)
    images, times, t = [], [], step / 2
    while t < dur:
        k = min(a["n"], int(round(t * scenes.THUMB_FPS)) + 1)
        p = os.path.join(a["dir"], f"{k:05d}.jpg")
        if os.path.exists(p):
            images.append(open(p, "rb").read())
            times.append(round(t, 1))
        t += step
    return cuts, images, times


async def process_highlights(job: Job, tmp: str):
    """Chế độ ✨: AI chấm điểm và chọn tối đa 3 đoạn hay nhất (30-90s, trọn ý) của video dài, remake từng đoạn,
    đoạn điểm cao giao trước. Video ít lời / AI lỗi: quay về cắt phần 60s như chế độ ✂️ (có báo)."""
    src, title, desc = await _long_source(job, tmp, "Tải video dài")
    dur = min(await core_video.probe_duration(src), config.SPLIT_MAX)
    kfs = await core_video.keyframe_times(src)
    pieces = job.pieces                                 # đã chọn ở lần chạy trước (bot khởi động lại): giữ nguyên
    note = ""
    if not pieces:
        job.step = "Bóc lời toàn bộ video + dò cảnh"
        audio = os.path.join(tmp, "audio_full.mp3")
        await core_video.extract_audio(src, audio, max_dur=dur)
        (_, segs), (cuts, images, img_t), sil = await asyncio.gather(
            transcribe_segments(audio, title, desc), _highlight_images(src, tmp, dur), core_video.silences(src))
        job.step = f"Chấm điểm đoạn hay (AI){' + xem ' + str(len(images)) + ' ảnh' if images else ''}"
        clips = await highlights.choose(title, desc, dur, segs, cuts, sil, images, img_t)
        if clips:
            n = len(clips)
            for k, c in enumerate(clips):
                _, a, b, _ = core_video.snap_to_keyframes([0.0, c["start"], c["end"], dur + 1.0], kfs)
                pieces.append({"a": a, "b": b, "label": f"Đoạn hay {k + 1}/{n}", "title": c["title"] or title,
                               "note": f"⭐ {c['score']}/10 · {c['title']}" + (f" — {c['reason']}" if c["reason"] else "")})
        else:
            note = "ℹ️ Không chọn được đoạn hay (video ít lời hoặc AI lỗi), đã cắt thành phần 60s. "
            pieces.extend(_even_pieces(dur, 60, cuts, sil, kfs))
        queue.save(job)
    ok, fails = await _remake_pieces(job, tmp, src, title, pieces, kfs)
    n = len(pieces)
    hl = pieces and pieces[0]["label"].startswith("Đoạn hay")
    job.script = (f"Đã xong {ok}/{n} đoạn hay: " + "; ".join(f"{p['a']:.0f}-{p['b']:.0f}s {p.get('title', '')}"
                                                            for p in pieces) if hl else f"Đã xong {ok}/{n} phần.")[:600]
    job.error = note + ("; ".join(fails) if fails else "")
    if ok == 0:
        raise RuntimeError("Không đoạn nào thành công. " + "; ".join(fails))


def final_timeline(segs: list, plan: list, out_dur: float) -> list:
    """Timeline cuối của video (xuất timeline.json để dựng tay / lưu trữ): mỗi đoạn có khe thời gian, cờ tiếng
    gốc, lời gốc, lời mới và chỗ giọng mới thật sự nằm (đầu, cuối, hệ số tốc độ)."""
    out = []
    for k, (sg, p) in enumerate(zip(segs, plan)):
        nxt = segs[k + 1]["start"] if k + 1 < len(segs) else out_dur
        spoken = sg.get("ost") != "orig"
        out.append({"i": k, "start": round(sg["start"], 2), "end": round(min(nxt, out_dur), 2),
                    "ost": sg.get("ost", "new"), "orig": sg.get("orig", ""), "text": sg.get("text", ""),
                    "voice": ({"start": p["start"], "end": p["end"], "speed": p["factor"]} if spoken else None)})
    return out


def credit_line(job: Job) -> str:
    """'🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI' cho nội dung đăng (tắt bằng SOURCE_CREDIT=0)."""
    if config.SOURCE_CREDIT == "0":
        return ""
    parts = []
    uploader = (job.source_meta or {}).get("uploader")
    if uploader:
        parts.append(f"🎬 Nguồn: {uploader}")
    if job.mode != "music":
        parts.append("🔊 Giọng đọc AI")
    return " · ".join(parts)


SEP_MAX_SPEECH = 45      # giây có lời tối đa để tách giọng AI (máy 1 lõi ~1 giây xử lý / giây audio)
SEP_MAX_VIDEO = 120      # và video không dài quá 2 phút


async def _ocr_bands(inp: str, src_dur: float, src_hash: str, fallback: list) -> list:
    """Dò dải chữ in cứng bằng model OCR (TEXT_DETECT=ocr); lỗi thì giữ kết quả heuristic."""
    k = cache.key("ocr", src_hash)
    hit = cache.get_json("ocr", k)
    if hit is not None:
        return [tuple(b) for b in hit]
    try:
        bands = await asyncio.to_thread(ocr_text.detect_sync, inp, 0.0, min(src_dur, config.HARD_MAX))
        cache.put_json("ocr", k, [list(b) for b in bands])
        return bands
    except Exception as e:
        logging.warning("Dò chữ OCR lỗi (%s), dùng cách dò cũ", str(e)[:100])
        return fallback


def promo_cut(job: Job, segs: list, out_dur: float) -> float | None:
    """Mốc cắt đoạn quảng cáo cuối của người gốc. Bản đã duyệt dùng lại ĐÚNG mốc đã quyết lúc viết kịch bản
    (kịch bản duyệt không có đoạn đó; trước đây bản duyệt render lại nguyên đoạn quảng cáo — lỗi N1)."""
    if job.approved:
        return job.cut_at or None
    if job.mode in ("cook", "review") and config.CUT_PROMO != "0":
        return prompt_engine.find_promo_tail(segs, out_dur)
    return None


async def process(job: Job, tmp: str, allow_review: bool = True):
    """Xử lý một job; task nền nào (tách giọng) còn chạy khi job dừng giữa chừng thì bị huỷ cùng."""
    tasks: list = []
    try:
        return await _process(job, tmp, allow_review, tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
    music = await _get_music(job, tmp)
    job.step = f"Dựng ảnh theo nhạc ({len(images)} ảnh)"
    out = os.path.join(tmp, "output.mp4")
    plan = await slideshow.render(images, music, out, job.duration)
    job.out_dur = plan["length"]
    job.script = slideshow.describe(plan)
    pack = await prompt_engine.generate(job.title_hint or "", "", "photo", "", int(plan["length"]))
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))
    return out


async def _process(job: Job, tmp: str, allow_review: bool, tasks: list):
    if job.mode == "photo":
        return await process_photo(job, tmp)
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
            job.url, tmp, max_duration=config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION)
        check_source_allowed(job)

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
        music = await _get_music(job, tmp)
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
            out = os.path.join(tmp, "output.mp4")
            await core_video.render_music(inp, music, out, job.duration, speed=1.0, trim=start, keep_orig=True)
            pack = await prompt_engine.generate(title, desc, "music", "", job.duration)
            job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))
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
        out = os.path.join(tmp, "output.mp4")
        await core_video.render_music(inp, music, out, job.duration, **sync)

        pack = await prompt_engine.generate(title, desc, "music", "", job.duration)
        job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))
        return out

    src_dur = await core_video.probe_duration(inp)
    stretch = 1.0
    if job.duration and job.duration > src_dur + 1:
        # làm chậm NGAY TRONG lần render cuối thay vì mã hoá riêng cả video một lần (lỗi #7: thêm một thế hệ nén
        # và cả một lượt mã hoá); bóc lời, dò cảnh, tách giọng chạy trên bản gốc rồi nhân mốc thời gian lên
        stretch = min(job.duration / src_dur, 1.4)
        job.step = f"Kéo dài video gốc {src_dur:.0f}s -> {src_dur * stretch:.0f}s (chậm {1 / stretch:.2f}x)"
        if stretch < job.duration / src_dur:
            job.error = (f"ℹ️ Video gốc chỉ {src_dur:.0f}s; kéo chậm tối đa 1.4x được {src_dur * stretch:.0f}s "
                         f"(chậm hơn sẽ lộ). ")

    vid_dur = src_dur * stretch
    limit = config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION
    out_dur = min(vid_dur, job.duration or vid_dur, limit)
    job.out_dur = out_dur
    render_dur = job.duration or (int(limit) if vid_dur > limit + 0.5 else 0)
    is_long = out_dur > 100                             # video dài: 720p
    if vid_dur > limit + 0.5 and not job.duration:
        job.error = (job.error or "") + f"ℹ️ Video {vid_dur:.0f}s, chỉ làm {limit}s đầu (chọn chế độ video dài để làm hết). "
    target = job.duration or int(vid_dur)

    job.step = "Bóc lời thoại + dò chữ in cứng + tách cảnh (song song)"
    src_hash = await asyncio.to_thread(cache.file_hash, inp)
    audio_mp3 = os.path.join(tmp, "audio.mp3")
    await core_video.extract_audio(inp, audio_mp3)
    (text, raw_segs), (cuts, bands) = await asyncio.gather(
        transcribe_segments(audio_mp3, title, desc),                     # ("", []) nếu không có nguồn STT
        scenes.analyze_cached(inp, tmp, src_dur, bool(job.subs and job.cover), src_hash,
                              need_thumbs=not job.approved))             # 1 lượt giải mã: cảnh + ảnh + dải chữ
    if config.TEXT_DETECT == "ocr" and job.subs and job.cover and ocr_text.available():
        bands = await _ocr_bands(inp, src_dur, src_hash, bands)
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

    def keyframes_for(sg):                              # ảnh cho AI: đổi mốc về trục thời gian của video gốc
        return scenes.keyframes(inp, [(a / stretch, b / stretch, t) for a, b, t in sg])

    async def _ambient():
        """Chế độ nấu ăn: tiếng nền KHÔNG còn giọng người gốc. Ưu tiên tách giọng AI (chỉ các đoạn có lời);
        không được -> tắt tiếng nền đúng lúc người gốc nói; không có mốc -> bỏ tiếng nền."""
        if job.mode != "cook":
            return None, None
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
                    try:
                        cache.put("ambient", k, p, ".wav")
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
        a = scenes._ANALYZED.get(inp)
        t = asyncio.create_task(cover_mod.make_cover(
            inp, tmp, job.mode, post_title, src_dur, cuts=[c / stretch for c in cuts],
            end=out_dur / stretch, bands=bands, thumbs_dir=a["dir"] if a else None))
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
        job.error = (job.error or "") + f"ℹ️ Đã cắt bỏ {out_dur - cut:.0f}s quảng cáo sản phẩm của người gốc ở cuối. "
        out_dur = render_dur = cut
        job.out_dur = out_dur
    _plan_separation()
    # Tách giọng gốc (nặng CPU) bắt đầu NGAY, chạy trong lúc chờ AI viết kịch bản và đọc giọng (việc mạng, CPU
    # rảnh). Trước đây chỉ bắt đầu cùng lúc đọc giọng nên thời gian chờ AI bị phí.
    amb_task = asyncio.create_task(_ambient())
    tasks.append(amb_task)

    # ----- Đường chính: giọng KHỚP HÌNH -----
    segs = [s for s in segs if s[0] < out_dur - 0.5]
    timed = None
    if job.approved:                                    # kịch bản đã duyệt: bỏ qua bước AI
        timed = {"segments": job.approved, "script": spoken_script(job.approved),
                 "title": "", "caption": "", "hashtags": "", "fresh": 1.0}
    elif len(segs) >= 2:
        segs = scenes.snap_to_cuts(segs, cuts)          # câu mới vang lên đúng lúc chuyển cảnh
        job.step = f"Viết lại {len(segs)} đoạn theo mốc lời + {len(cuts)} điểm chuyển cảnh (AI)"
        timed = await prompt_engine.generate_timed_any(
            title, desc, job.mode, segs, out_dur,
            images_for=keyframes_for)                   # AI nhìn ảnh từng đoạn để lời khớp hình
    elif len(cuts) >= 2:                                # video không lời: AI nhìn ảnh từng cảnh
        sc = scenes.scene_segments(cuts, out_dur)
        job.step = f"AI xem {len(sc)} cảnh và viết lời cho từng cảnh"
        timed = await prompt_engine.generate_scenes(title, desc, job.mode, sc, await keyframes_for(sc), out_dur)
    if timed:
        job.script = spoken_script(timed["segments"]) or timed["script"]
        if not job.approved:
            job.post = prompt_engine.platform_post(timed["title"], timed["caption"], timed["hashtags"], credit_line(job))
        if allow_review and job.source != "auto" and (job.review or config.REVIEW_SCRIPT == "1") and not job.approved:
            job.approved = [{"i": s["i"], "start": s["start"], "slot": s["slot"], "span": s.get("span", s["slot"]),
                             "text": s["text"], "orig": s.get("orig", ""), "ost": s.get("ost", "new")}
                            for s in timed["segments"]]
            amb_task.cancel()        # dừng chờ duyệt: tách giọng làm sau, lúc render bản đã duyệt
            await asyncio.gather(amb_task, return_exceptions=True)
            if not job.local:        # nguồn từ link/Telegram: giữ lại để render sau khi duyệt KHÔNG phải tải lại (#8)
                keep = os.path.join(config.UPLOAD_DIR, f"review_{job.id}{os.path.splitext(inp)[1] or '.mp4'}")
                await asyncio.to_thread(shutil.move, inp, keep)
                job.local = keep
            job.status, job.step = "review", "Chờ bạn duyệt/sửa kịch bản rồi bấm Render"
            return "REVIEW"
        sem = asyncio.Semaphore(3)

        async def _one(seg):
            if seg.get("ost") == "orig":                 # đoạn giữ nguyên tiếng gốc: không đọc lời mới
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
            est = [0.0 if sg.get("ost") == "orig" else len(sg["text"].split()) / prompt_engine.words_per_sec() * 0.85
                   for sg in segs_t]
            _, sure_long = core_video.plan_voice(est, segs_t, out_dur)
            if sure_long:
                job.step = f"Rút gọn trước {len(sure_long)} đoạn chắc chắn quá dài"
                await _shorten(sure_long, est)

        job.step = f"Đọc giọng {len(segs_t)} đoạn song song" + (" + tách giọng gốc" if sep["ok"] else "")
        clips = list(await _gather_all([_one(s) for s in segs_t]))
        durs = await _durs(clips)
        plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        if too_long:                                     # đoạn nào vẫn quá dài -> AI viết ngắn lại 1 lần
            job.step = f"Rút gọn {len(too_long)} đoạn quá dài"
            changed = await _shorten(too_long, durs)
            if changed:
                redo = await _gather_all([_one(segs_t[i]) for i in changed])
                for i, c in zip(changed, redo):
                    clips[i] = c
                for i, d in zip(changed, await _durs(redo)):
                    durs[i] = d
            plan, too_long = core_video.plan_voice(durs, segs_t, out_dur)
        voice_track = os.path.join(tmp, "voice.wav")
        await core_video.compose_voice(clips, plan, voice_track, out_dur)
        got_subs = False
        if job.subs:
            words = await core_video.words_for_track(
                clips, plan, voice_track, ["" if s.get("ost") == "orig" else s["text"] for s in segs_t])
            if words:
                job.srt = subs_mod.to_srt(words)
                subs_mod.write_ass(words, ass, pos_y, config.SUB_STYLE)
                got_subs = True
        job.step = "Chờ tách giọng gốc xong" if sep["ok"] else "Render (FFmpeg)"
        amb_wav, gate = await amb_task
        job.step = "Render (FFmpeg)"
        out = os.path.join(tmp, "output.mp4")
        post_title = job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title
        cover_task = _start_cover(post_title)
        notes = []
        await core_video.render(inp, voice_track, out, job.mode, render_dur,
                                ass if got_subs else None, bands, amb_wav, gate, allow_hd=not is_long,
                                title=post_title, tmp=tmp,
                                stretch=stretch, ost=core_video.ost_windows(segs_t, out_dur), notes=notes)
        if notes:
            job.error = (job.error or "") + "".join(notes)
        await _finish_cover(cover_task)
        job.final_segments = final_timeline(segs_t, plan, out_dur)
        if job.mode == "cook":
            job.error = (job.error or "") + (
                "ℹ️ Đã tách bỏ giọng gốc bằng AI. " if amb_wav else
                f"ℹ️ Không tách giọng AI ({sep['why']}): tắt tiếng nền lúc người gốc nói. " if sep["why"] else
                "ℹ️ Tách giọng AI không chạy được (máy chậm/thiếu RAM): tắt tiếng nền lúc người gốc nói. ")
        fast = sum(1 for p in plan if p["factor"] > 1.12)
        drift = max(abs(p["start"] - s["start"]) for p, s in zip(plan, segs_t))
        job.error = (job.error or "") + (
            (f"ℹ️ Lời mới {timed['fresh'] * 100:.0f}% khác lời gốc; " if "fresh" in timed else "ℹ️ ")
            + f"{len(clips)} đoạn giọng, {len(cuts)} điểm chuyển cảnh; lệch tối đa {drift:.1f}s"
            + (f"; {fast} đoạn nói nhanh" if fast else "")
            + (f"; {len(too_long)} đoạn vẫn hơi dài" if too_long else "")
            + (f"; đã che {len(bands)} dải chữ gốc" if bands else "") + ".")
        return out

    # ----- Đường cũ: không có mốc thời gian -> đọc một mạch -----
    job.step = "Viết kịch bản + content (AI)"
    pack = await prompt_engine.generate(title, desc, job.mode, text, target)
    if pack.get("fallback"):
        job.error = "⚠️ AI viết kịch bản lỗi (xem log), đang dùng kịch bản mẫu. Kiểm tra key/model trong Cài đặt."
    job.script = pack["script"]
    job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], credit_line(job))

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
            job.post = prompt_engine.platform_post(pack2["title"], pack2["caption"], pack2["hashtags"], credit_line(job))
            got_subs = await core_video.tts(job.script, voice_mp3, job.voice,
                                            ass if job.subs else None, pos_y)

    job.step = "Render (FFmpeg)"
    out = os.path.join(tmp, "output.mp4")
    amb_wav, gate = await amb_task
    post_title = job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title
    cover_task = _start_cover(post_title)
    notes = []
    await core_video.render(inp, voice_mp3, out, job.mode, render_dur,
                            ass if (job.subs and got_subs) else None, bands, amb_wav, gate, allow_hd=not is_long,
                            title=post_title, tmp=tmp, stretch=stretch, notes=notes)
    if notes:
        job.error = (job.error or "") + "".join(notes)
    await _finish_cover(cover_task)
    if bands:
        job.error = f"ℹ️ Đã che {len(bands)} dải chữ gốc và đặt phụ đề mới lên đó."
    return out




async def run_job(job: Job, tmp: str):
    if job.long_mode.startswith("split"):
        await process_split(job, tmp)
    elif job.long_mode == "highlight":
        await process_highlights(job, tmp)
    else:
        out = await process(job, tmp)
        if out == "REVIEW":
            return "REVIEW"
        await deliver(job, out, "Video", job.post or job.script or "")
