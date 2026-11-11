"""Quản trị: lệnh gõ vào console Botkeep (stdin) và vòng bảo trì định kỳ (hết hạn duyệt, dọn cache, kiểm tra dịch vụ)."""
import asyncio
import logging
import os
import sys
import tempfile

import cache
import config
import core_video
import healthcheck
import metrics
import offline
import ytdlp_update
from jobs import cancel_job
from state import current, notify, notify_admin, queue, spawn
from storage import disk_report

CONSOLE_HELP = ("Lệnh console: help | status | health (kiểm tra dịch vụ ngoài ngay) | update_ytdlp (cập nhật yt-dlp, "
                "tự quay lại bản cũ nếu hỏng) | setup_offline / remove_offline (gói offline ~185MB: tách giọng nhanh, "
                "giọng Piper, dò chữ OCR) | setup_potoken (PO token cho YouTube, ~300MB) | remove_potoken | "
                "yt_test <link> | cancel <số job>")


async def console():
    """Ô console Botkeep (Pterodactyl) gửi chữ vào stdin của bot -> chạy lệnh quản trị ngay tại đó."""
    import potoken_setup
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            return
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
            elif low.startswith("cancel "):
                logging.info(cancel_job(int(low.split()[1])))
            else:
                logging.info("Không hiểu lệnh «%s». %s", cmd[:40], CONSOLE_HELP)
        except Exception as e:
            logging.warning("Lệnh console lỗi: %s", e)


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
