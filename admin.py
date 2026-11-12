"""Quản trị: lệnh gõ vào console Botkeep (stdin) và vòng bảo trì định kỳ (hết hạn duyệt, dọn cache, kiểm tra dịch vụ)."""
import asyncio
import logging
import os
import sys
import tempfile
import threading

import autopost
import cache
import config
import core_video
import healthcheck
import metrics
import offline
import ytdlp_update
from jobs import cancel_job
from state import current, notify, notify_admin, queue, spawn
import storage
from storage import disk_report

CONSOLE_HELP = ("Lệnh console: help | status | health (kiểm tra dịch vụ ngoài ngay) | update_ytdlp (cập nhật yt-dlp, "
                "tự quay lại bản cũ nếu hỏng) | setup_offline / remove_offline (gói offline ~185MB: tách giọng nhanh, "
                "giọng Piper, dò chữ OCR) | setup_youtube / remove_youtube (deno cho YouTube, ~100MB) | "
                "setup_potoken (PO token cho YouTube, ~300MB) | remove_potoken | clean (dọn bộ nhớ đệm pip + cache) | "
                "yt_test <link> | cancel <số job> | golden [run|baseline|compare] (bộ video mẫu, xem golden.py) | "
                "auto (trạng thái tự động đăng Facebook) | auto_now (lấy video ngay) | auto_resume | fb_test (thử token Page)")


def _stdin_queue(loop) -> asyncio.Queue:
    """Đọc stdin trong luồng DAEMON riêng, đẩy từng dòng vào hàng đợi asyncio ("" = hết stdin).
    Trước đây đọc bằng run_in_executor: lúc tắt bot, asyncio.run chờ luồng executor đang kẹt ở readline (console
    Botkeep giữ stdin mở) tới 5 phút nên tiến trình không thoát sau SIGTERM."""
    q: asyncio.Queue = asyncio.Queue()

    def put(line):
        try:
            loop.call_soon_threadsafe(q.put_nowait, line)
        except RuntimeError:                  # vòng lặp đã đóng (bot đang thoát)
            pass

    def reader():
        try:
            for line in iter(sys.stdin.readline, ""):
                put(line)
        except Exception:
            pass
        put("")
    threading.Thread(target=reader, name="console-stdin", daemon=True).start()
    return q


async def console():
    """Ô console Botkeep (Pterodactyl) gửi chữ vào stdin của bot -> chạy lệnh quản trị ngay tại đó."""
    import potoken_setup
    lines = _stdin_queue(asyncio.get_running_loop())
    while True:
        line = await lines.get()
        if not line:                       # không có stdin (chạy nền) -> thôi
            return
        cmd = line.strip()
        low = cmd.lower()
        try:
            if not cmd:
                continue
            if low in ("help", "?", "/help"):
                logging.info(CONSOLE_HELP)
            elif low in ("status", "/status"):
                cur = current.get("job")
                logging.info("Hàng đợi: %d | đang chạy: %s | %s | PO token: %s | proxy: %s | cookies: %s",
                             queue.pending, f"#{cur.id} {cur.step}" if cur else "không",
                             disk_report(), core_video.pot_status() or "chưa cài",
                             "có" if config.YTDLP_PROXY.strip() else "không",
                             "có" if config.YTDLP_COOKIES.strip() else "không")
                logging.info("yt-dlp %s | luồng FFmpeg %d | gói offline: %s\nDịch vụ ngoài 24 giờ qua:\n%s",
                             ytdlp_update.installed_version(), core_video.ffmpeg_threads(), offline.status(),
                             metrics.summary(24))
            elif low in ("health", "/health"):
                spawn(monitor.tick(force=True))
            elif low in ("update_ytdlp", "update-ytdlp"):
                spawn(ytdlp_update.update(lambda s: logging.info("[yt-dlp] %s", s)))
            elif "setup_potoken" in low or "setup-potoken" in low:
                logging.info("Bắt đầu cài PO token (vài phút, bot vẫn chạy bình thường)...")
                spawn(potoken_setup.install(lambda s: logging.info("[potoken] %s", s)))
            elif low in ("setup_offline", "setup-offline"):
                logging.info("Cài gói offline (~185MB: tách giọng nhanh, giọng Piper, model dò chữ)...")
                spawn(offline.install(lambda s: logging.info("[offline] %s", s)))
            elif low in ("remove_offline", "remove-offline"):
                offline.remove(lambda s: logging.info("[offline] %s", s))
            elif low in ("setup_youtube", "setup-youtube"):
                spawn(potoken_setup.install_deno(lambda s: logging.info("[youtube] %s", s)))
            elif low in ("remove_youtube", "remove-youtube"):
                potoken_setup.remove_deno(lambda s: logging.info("[youtube] %s", s))
            elif low == "clean":
                await asyncio.to_thread(storage.clean, lambda s: logging.info("[clean] %s", s))
            elif "remove_potoken" in low:
                potoken_setup.remove(lambda s: logging.info("[potoken] %s", s))
            elif low.startswith("yt_test"):
                url = cmd.split(maxsplit=1)[1] if " " in cmd else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
                with tempfile.TemporaryDirectory() as td:
                    try:
                        d = await core_video.probe_formats(url, td)
                        logging.info("1/2 Xem định dạng OK: %s", d["title"][:50])
                        p, _ = await core_video.download_format(url, td, "360")   # tải thật mới chắc
                        logging.info("✅ 2/2 Tải thật OK (%dKB) — YouTube dùng được.", os.path.getsize(p) >> 10)
                    except Exception as e:
                        logging.info("❌ YouTube vẫn lỗi: %s", e)
            elif low.split()[0] == "golden":
                arg = (low.split() + ["run"])[1]
                if arg not in ("run", "baseline", "compare"):
                    logging.info("golden run | golden baseline | golden compare")
                elif arg == "run" and (queue.pending or current.get("job")):
                    logging.info("Hàng đợi đang có video, chạy bộ video mẫu sau khi xong để không tranh CPU.")
                else:
                    spawn(run_golden(arg))
            elif low in ("auto", "auto_status"):
                logging.info(autopost.status_text())
            elif low == "auto_now":
                logging.info("Tự động đăng: %s", await autopost.tick(force=True))
            elif low == "auto_resume":
                st = autopost.load_state()
                st["paused"] = ""
                autopost.save_state(st)
                logging.info("Tự động đăng: chạy lại. %s", await autopost.tick())
            elif low == "fb_test":
                try:
                    logging.info("✅ Token dùng được cho Fanpage: %s", await autopost.check_token())
                except Exception as e:
                    logging.info("❌ Token Fanpage lỗi: %s", e)
            elif low.startswith("cancel "):
                logging.info(cancel_job(int(low.split()[1])))
            else:
                logging.info("Không hiểu lệnh «%s». %s", cmd[:40], CONSOLE_HELP)
        except Exception as e:
            logging.warning("Lệnh console lỗi: %s", e)


async def run_golden(arg: str):
    """Chạy golden.py trong tiến trình riêng, ưu tiên CPU thấp (nice 10), in từng dòng kết quả ra log."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.py")
    base = os.path.dirname(script)
    env = dict(os.environ, PYTHONUNBUFFERED="1",               # in tiến độ ngay, không đợi tới cuối
               JOBS_DB=os.path.join(os.environ.get("GOLDEN_DIR") or os.path.join(base, "golden"), ".golden_jobs.db"))
    proc = await asyncio.create_subprocess_exec(sys.executable, script, arg, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, env=env,
                                                preexec_fn=lambda: os.nice(10))
    async for line in proc.stdout:
        text = line.decode(errors="ignore").rstrip()
        if text:
            logging.info("[golden] %s", text)
    code = await proc.wait()
    logging.info("[golden] xong (mã %d%s). Báo cáo: golden/report.md", code, ", CÓ chỉ số tụt" if code == 1 else "")


monitor = healthcheck.Monitor(notify_admin)
MAINTENANCE_EVERY = 600          # giây


async def maintenance():
    """Mỗi 10 phút: hết hạn job chờ duyệt quá 24 giờ, dọn số liệu cũ, kiểm tra dịch vụ ngoài theo lịch."""
    await asyncio.sleep(60)                          # chờ bot khởi động xong
    while True:
        try:
            for job in queue.expire_reviews():
                if job.source == "tg":
                    await notify(job.chat_id, f"⌛ Kịch bản job #{job.id} đã quá 24 giờ chưa duyệt nên hết hạn.")
            metrics.prune()
            await asyncio.to_thread(cache.prune)
            await monitor.tick()
        except Exception:
            logging.exception("Bảo trì định kỳ lỗi")
        await asyncio.sleep(MAINTENANCE_EVERY)
