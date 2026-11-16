"""Video dài: cắt phần (✂️ 60/90 giây tại chỗ ngưng nói/chuyển cảnh) và đoạn hay (✨ AI chọn), remake từng phần (gom một phiên Kaggle), giao từng phần.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import contextlib
import logging
import os
import shutil

import config
import core_video
import highlights
import kaggle_worker
import metrics
import scenes
import state
import transcribe
from queue_manager import Job
from state import notify, queue
from rmvideo.app import events, job_factory
from rmvideo.app.flow import common, delivery, dispatch


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
    common.check_source_allowed(job)
    return src, title, desc


async def _remake_pieces(job: Job, tmp: str, src: str, title: str, pieces: list, kfs: list) -> tuple:
    """Cắt và remake từng phần. pieces: [{"a", "b", "label", "title"?, "note"?}]. Phần đã giao (bot khởi động lại giữa
    chừng) thì bỏ qua. Có Kaggle: mỗi phần chuẩn bị xong (bóc lời + AI viết lời trên Botkeep) thì để chờ, rồi MỌI phần
    chạy chung MỘT phiên Kaggle (tải nguồn + nạp model một lần, Kaggle tự cắt phần từ nguồn); phần nào Kaggle lỗi thì
    làm lại phần đó trên Botkeep. Không có Kaggle: xong phần nào giao phần đó như cũ. Trả (số phần xong, [lỗi])."""
    ok, fails = 0, []
    delivered = {p["label"] for p in job.parts}
    batch = job.mode != "download" and kaggle_worker.is_enabled()
    # Kaggle lấy từng phần: có PUBLIC_URL thì tải đúng file phần Botkeep đã cắt (nhỏ, khớp từng khung); không có thì
    # tự tải video gốc theo link (một lần cho cả lô) rồi cắt y như Botkeep
    parent_ref = None
    if batch and job.url and not (config.PUBLIC_URL or "").strip():
        parent_ref = {"url": job.url, "kind": "video", "max_duration": config.SPLIT_MAX}
    waiting = []                                       # [(nhãn, phần, job con, Deferred, thư mục)]

    def _cleanup(part_dir):
        for d in os.listdir(part_dir):                 # giải phóng ổ đĩa ngay sau mỗi phần
            p = os.path.join(part_dir, d)
            if os.path.isfile(p):
                os.remove(p)

    async def _deliver(pc, label, sub, out):
        if job.mode == "download":
            caption = f"{pc.get('title') or title} ({label})"
        else:
            caption = (sub.post or sub.script or "").replace("📌 ", f"📌 [{label}] ", 1)
        if pc.get("note"):
            caption = pc["note"] + "\n" + caption
        await delivery.deliver(job, out, label, caption)
        queue.save(job)                                # ghi lại phần đã giao (khởi động lại thì không giao lại)

    for i, pc in enumerate(pieces):
        a, b, label = pc["a"], pc["b"], pc["label"]
        if label in delivered:
            ok += 1
            continue
        part_dir = os.path.join(tmp, f"p{i}")
        os.makedirs(part_dir, exist_ok=True)
        piece = os.path.join(part_dir, "piece.mp4")
        job.step = f"{label}: cắt {a:.0f}s-{b:.0f}s"
        copy = core_video.is_keyframe(a, kfs)
        how = await core_video.cut(src, piece, a, b, copy=copy)
        logging.info("%s: cắt %.1f-%.1fs (%s)", label, a, b, "chép luồng" if how == "copy" else "mã hoá lại")
        # long_mode="full": làm TRỌN phần đã cắt (split90 có phần tới 112s; trước đây không có Kaggle thì bị cắt còn 90s)
        sub = job_factory.piece(job, piece, f"{pc.get('title') or title} ({label})",
                                {"cut": [a, b, copy], "of": parent_ref} if parent_ref else {})

        def _mirror(step, lb=label):                  # hiện tiến độ của phần đang làm lên job cha (ngay lúc đổi)
            if step:
                job.step = f"{lb}: {step}"
        mirror = events.watch_steps(sub, _mirror)
        mirror.__enter__()
        deferred = False
        try:
            if job.mode == "download":
                out = piece                            # chỉ cắt, không remake
            else:
                with kaggle_worker.collect() if batch else contextlib.nullcontext():
                    out = await dispatch.process(sub, part_dir, allow_review=False, teaser_ok=False)   # không dừng chờ duyệt
            if isinstance(out, kaggle_worker.Deferred):
                waiting.append((label, pc, sub, out, part_dir))
                deferred = True
                continue
            await _deliver(pc, label, sub, out)
            ok += 1
        except Exception as e:
            logging.exception("%s thất bại", label)
            fails.append(f"{label}: {str(e)[:120]}")
            if job.source == "tg":
                await notify(job.chat_id, f"❌ {label} lỗi: {e}")
        finally:
            mirror.__exit__(None, None, None)
            if not deferred:
                _cleanup(part_dir)

    if waiting:
        job.step = f"Kaggle: {len(waiting)} phần trong một lần chạy"
        results = await kaggle_worker.produce_batch([w[3].spec for w in waiting], tmp,
                                                    on_step=lambda s: setattr(job, "step", s))
        for (label, pc, sub, d, part_dir), res in zip(waiting, results):
            try:
                try:
                    if isinstance(res, Exception):
                        raise res
                    out = await d.finish(res)
                    if os.path.dirname(out) != part_dir:       # lô Kaggle ghi video ở thư mục gốc: đưa về thư mục phần
                        dst = os.path.join(part_dir, os.path.basename(out))     # để ảnh bìa nằm cạnh (deliver tìm ở đó)
                        await asyncio.to_thread(shutil.move, out, dst)
                        out = dst
                except Exception as e:                 # Kaggle lỗi phần này: làm lại phần này trên Botkeep
                    logging.warning("%s: Kaggle lỗi (%s), làm trên Botkeep", label, e)
                    metrics.record("kaggle", "kaggle", ok=False, detail=str(e))
                    sub.error = f"ℹ️ Kaggle lỗi ({str(e)[:120]}): làm trên Botkeep. "
                    sub.approved, sub.post_keep = d.approved, sub.post      # lời đã chốt: không gọi AI lần nữa
                    job.step = f"{label}: làm trên Botkeep"
                    out = await dispatch.process(sub, part_dir, allow_review=False, local_only=True, teaser_ok=False)
                    sub.post = sub.post or sub.post_keep
                await _deliver(pc, label, sub, out)
                ok += 1
            except Exception as e:
                logging.exception("%s thất bại", label)
                fails.append(f"{label}: {str(e)[:120]}")
                if job.source == "tg":
                    await notify(job.chat_id, f"❌ {label} lỗi: {e}")
            finally:
                _cleanup(part_dir)
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
    cuts, _ = await scenes.analyze(src, tmp, dur, False, scenes.cut_mode())   # cần điểm chuyển cảnh + ảnh 2/giây
    a = scenes.analyzed(src)
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
        has_aud = await core_video.has_audio(src)

        async def _words():
            if not has_aud:
                return "", []
            await core_video.extract_audio(src, audio, max_dur=dur)
            return await transcribe.transcribe_segments(audio, title, desc)

        async def _sil():
            return await core_video.silences(src) if has_aud else []
        (_, segs), (cuts, images, img_t), sil = await asyncio.gather(_words(), _highlight_images(src, tmp, dur), _sil())
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
