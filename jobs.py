"""Worker duy nhất chạy hàng đợi: hạn giờ co giãn, huỷ, dọn file, học tốc độ máy để ước tính thời gian."""
import asyncio
import json
import logging
import os
import tempfile
import time

import config
import core_video
import kaggle_worker
from queue_manager import Job, phase_times
from pipeline import run_job
from review import OST_TAG, REVIEW_HELP
from state import current, notify, queue
from storage import _size_mb, disk_report, pick_workdir

JOB_TIMEOUT = {"": 25 * 60, "full": 3 * 3600, "split60": 3 * 3600, "split90": 3 * 3600, "highlight": 2 * 3600}


async def worker():
    """Worker duy nhất (concurrency = 1). Mỗi job có timeout toàn cục và hủy được;
    hủy hoặc quá hạn thì giết mọi FFmpeg/tách giọng đang chạy, không để kẹt hàng đợi."""
    while True:
        job = await queue.get()
        try:
            await _run_one(job)
        except Exception:                        # lưới cuối: lỗi gì cũng không được giết worker
            logging.exception("Worker lỗi ngoài dự kiến ở job %s", job.id)
            if job.status == "processing":
                job.status, job.error = "error", "Lỗi nội bộ, xem log."
                queue.save(job)
            queue.done()


def _drop_inputs(job: Job):
    """Xoá file đầu vào job đang giữ (file tải lên, nguồn giữ lại để duyệt, nhạc tải lên)."""
    for leftover in (job.local, job.music_local, *job.images):
        if leftover and os.path.exists(leftover):
            try:
                os.remove(leftover)
            except OSError:
                logging.warning("Không xoá được %s", leftover)


async def _run_one(job: Job):
    tg = job.source == "tg"
    if job.cancel:
        job.status, job.error = "cancelled", "Đã hủy trước khi chạy."
        _drop_inputs(job)
        queue.save(job)
        queue.done()
        return
    # ước lượng chỗ cần: file nguồn x3 (nguồn + bản đang xử lý + kết quả) + 300MB cho tiếng, ảnh, phụ đề
    src_mb = _size_mb(job.local) if job.local and os.path.exists(job.local) else 60
    need = 300 + 3 * src_mb
    workdir = pick_workdir(need)
    if not workdir:
        job.status, job.error = "error", f"Không đủ chỗ trống (cần ~{need}MB). {disk_report()}"
        logging.error("Job %s: %s", job.id, job.error)
        _drop_inputs(job)
        queue.save(job)
        queue.done()
        return
    job.status, job.started_at, job.timeline, job.error = "processing", time.time(), [], ""
    queue.save(job)                                # trạng thái processing vào DB: chết giữa chừng thì chạy lại
    current["job"] = job
    shutting_down = False
    keep_source = False                            # quyết NGAY khi job dừng ở bước duyệt, không đợi tới finally:
    # người dùng có thể bấm duyệt trên web trong lúc còn đang gửi tin Telegram, lúc đó trạng thái đã thành
    # "approved" mà job mới vẫn cần file nguồn
    try:
        if tg:
            await notify(job.chat_id, f"⏳ Đang xử lý video #{job.id}... (gửi /cancel để hủy)")
        logging.info("Job %s: file tạm ở %s (cần ~%dMB) | %s", job.id, workdir, need, disk_report())
        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            task = asyncio.create_task(run_job(job, tmp))
            current["task"] = task

            async def _timeline():                 # ghi lại từng bước + thời điểm để đo
                last = None
                while True:
                    if job.step != last:
                        last = job.step
                        t = round(time.time() - job.started_at, 1)
                        job.timeline.append((job.step, t))
                        logging.info("Job %s [%4.0fs] %s", job.id, t, job.step)
                    await asyncio.sleep(1)
            tl = asyncio.create_task(_timeline())
            try:
                while True:                       # hạn co giãn: biết độ dài video rồi thì nới ra cho đủ
                    done, _ = await asyncio.wait({task}, timeout=5)
                    if done:
                        # dừng ở bước duyệt -> giữ file nguồn. Lấy từ kết quả task chứ không từ job.status: người
                        # dùng có thể đã bấm duyệt trên web (status thành "approved") trước khi worker kịp xét
                        keep_source = task.result() == "REVIEW"
                        break
                    if time.time() - job.started_at > job_budget(job):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise asyncio.TimeoutError
            finally:
                tl.cancel()
                if not task.done():               # bot tắt giữa chừng: dừng hẳn job trước khi xoá thư mục tạm
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        if keep_source:
            if tg and job.status == "review":
                lines = "\n".join(f"{s['i']}: {OST_TAG.get(s.get('ost', 'new'), '')}{s['text']}" for s in job.approved)
                await notify(job.chat_id, (f"📝 Kịch bản job #{job.id}:\n\n{lines[:3500]}\n\n"
                                           + REVIEW_HELP))
        else:
            job.status, job.step = "done", ""
    except asyncio.TimeoutError:
        core_video.kill_running()
        job.status, job.error = "error", (f"Quá thời gian cho phép ({job_budget(job) // 60:.0f} phút) ở bước "
                                          f"«{job.step}», đã hủy. Thử chất lượng 'fast' hoặc video ngắn hơn.")
        logging.error("Job %s quá hạn ở bước: %s | các bước: %s", job.id, job.step,
                      " > ".join(f"{s}@{t:.0f}s" for s, t in job.timeline))
        if tg:
            await notify(job.chat_id, f"❌ Job #{job.id} quá thời gian, đã hủy.")
    except asyncio.CancelledError:
        core_video.kill_running()
        me = asyncio.current_task()
        if me is not None and me.cancelling():
            # Chính worker bị huỷ = bot đang tắt (SIGTERM khi Botkeep dừng/khởi động lại), KHÔNG phải người dùng bấm
            # Hủy (nút Hủy chỉ huỷ task của job). Trước đây job bị ghi "Đã hủy theo yêu cầu" (không chạy lại được),
            # file đầu vào bị xoá, còn worker nuốt tín hiệu huỷ rồi bốc tiếp job sau nên tiến trình không thoát.
            # Giờ: giữ trạng thái "processing" + file đầu vào -> lần khởi động sau tự chạy lại (tối đa MAX_ATTEMPTS).
            shutting_down = True
            job.step = "Bot tắt giữa chừng, sẽ chạy lại khi khởi động"
            logging.warning("Job %s dừng vì bot tắt, sẽ chạy lại khi khởi động", job.id)
            raise
        job.status, job.error = "cancelled", "Đã hủy theo yêu cầu."
        if tg:
            await notify(job.chat_id, f"🛑 Đã hủy job #{job.id}.")
    except Exception as e:
        core_video.kill_running()
        logging.exception("Job %s thất bại", job.id)
        job.status, job.error, job.step = "error", str(e), ""
        if tg:
            await notify(job.chat_id, f"❌ Lỗi: {e}")
    finally:
        job.finished_at = time.time()
        current["job"], current["task"] = None, None
        if not keep_source and not shutting_down:   # chờ duyệt / bot tắt giữa chừng: giữ file nguồn để chạy tiếp
            _drop_inputs(job)
        _learn_speed(job)
        queue.log_steps(job)
        if job.timeline and job.started_at and not shutting_down:
            total = job.finished_at - job.started_at
            logging.info("Job %s %s sau %.1f phút: %s", job.id, job.status, total / 60,
                         " | ".join(f"{p['name']} {p['secs']:.0f}s" for p in phase_times(job.timeline, total)))
        queue.save(job)
        queue.done()


def job_budget(job: Job) -> float:
    """Hạn thời gian theo độ dài video và mức chất lượng (máy Botkeep 1.5 vCPU):
    tối thiểu 25 phút; 95s balanced ≈ 41 phút; video dài tối đa 3 giờ."""
    base = JOB_TIMEOUT.get(job.long_mode, 25 * 60)
    if not job.out_dur:
        return base
    per_sec = {"fast": 14, "high": 40}.get(config.QUALITY, 22)
    budget = min(3 * 3600, max(base, 600 + job.out_dur * per_sec * (1 if job.mode != "download" else 0.2)))
    if not kaggle_worker.enabled():               # chờ Kaggle (hàng chờ máy + chạy) rồi vẫn còn đủ giờ lùi về Botkeep
        parts = max(1, int(job.out_dur // (90 if job.long_mode == "split90" else 60)) + 1) if job.long_mode else 1
        budget += kaggle_worker.TIMEOUT + kaggle_worker.TIMEOUT_PER_EXTRA * (parts - 1)
        if config.KAGGLE_ANALYZE != "0" and job.mode in ("cook", "review"):
            budget += kaggle_worker.TIMEOUT_ANALYZE           # thêm phiên bóc lời + phân tích hình trên Kaggle
    return budget


def cancel_job(jid: int) -> str:
    """Hủy job đang chờ hoặc đang chạy. Trả về thông báo."""
    job = queue.jobs.get(jid)
    if not job:
        return "Không thấy job."
    if job.status == "queued":
        job.cancel = True
        queue.save(job)                      # lưu ngay: khởi động lại không được chạy lại job đã huỷ
        return f"Job #{jid} sẽ bị bỏ qua khi tới lượt."
    if job.status == "processing" and current["job"] is job and current["task"]:
        current["task"].cancel()
        kaggle_worker.abort()                  # phiên Kaggle đang chạy cho job này (nếu có) cũng dừng, khỏi tốn giờ GPU
        return f"Đang hủy job #{jid}..."
    return f"Job #{jid} đã kết thúc ({job.status})."


SPEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed.json")


def _learn_speed(job: Job):
    """Học tốc độ máy: giây xử lý / giây video, để ước tính thời gian các job sau."""
    if job.status != "done" or not job.out_dur or not job.started_at:
        return
    try:
        d = json.load(open(SPEED_FILE)) if os.path.exists(SPEED_FILE) else {}
        key = "long" if job.long_mode else "normal"
        ratio = (job.finished_at - job.started_at) / job.out_dur
        d[key] = round(0.6 * d.get(key, ratio) + 0.4 * ratio, 1)
        json.dump(d, open(SPEED_FILE, "w"))
    except Exception:
        pass


def speed_ratio(long_mode: str) -> float:
    try:
        d = json.load(open(SPEED_FILE))
        return d.get("long" if long_mode else "normal", 6.0)
    except Exception:
        return 6.0
