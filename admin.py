"""Quản trị: lệnh gõ vào console Botkeep (stdin) và vòng bảo trì định kỳ (hết hạn duyệt, dọn cache, kiểm tra dịch vụ)."""
import asyncio
import difflib
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
from rmvideo.app import notifier
from state import current, notify_admin, queue, spawn
import storage
from storage import disk_report

CONSOLE_HELP = ("Lệnh console: help | status | health (kiểm tra dịch vụ ngoài ngay) | update_ytdlp (cập nhật yt-dlp, "
                "tự quay lại bản cũ nếu hỏng) | setup_offline / remove_offline (gói offline ~185MB: tách giọng nhanh, "
                "giọng Piper, dò chữ OCR) | setup_youtube / remove_youtube (deno cho YouTube, ~100MB) | "
                "setup_potoken (PO token cho YouTube, ~300MB) | remove_potoken | clean (dọn bộ nhớ đệm pip + cache) | "
                "yt_test <link> | cancel <số job> | golden [run|baseline|compare] (bộ video mẫu, xem golden.py) | "
                "auto (trạng thái tự động đăng Facebook) | auto_now (lấy video ngay) | auto_resume | fb_test (thử token Page) | "
                "kaggle_test (thử token Kaggle + giờ GPU còn lại) | kaggle_check (kiểm model / LoRA / kho nhạc trên Kaggle, tự đẩy lại kho nhạc "
                "bị mất) | kids (trạng thái truyện bé) | kids_now (làm 1 truyện ngay) | "
                "kids_setup (chuẩn bị model trên Kaggle, một lần) | kids_setup_status | kids_bgm (làm đủ kho nhạc Lyria) | "
                "kids_fb_test | kids_resume | kids_tick (chạy một lượt tự động ngay) | cook_bgm (tạo đủ kho nhạc Lyria cho bếp, chạy "
                "nền) | bgm_kaggle (tạo kho nhạc bé + bếp bằng Lyria trên Kaggle CPU, chạy nền — mạng Botkeep quá chậm cho Lyria; "
                "kids_bgm / cook_bgm tự chuyển sang đây khi Kaggle đã tạo được nhạc)")


COMMANDS = ("help", "status", "health", "update_ytdlp", "setup_offline", "remove_offline", "setup_youtube",
            "remove_youtube", "setup_potoken", "remove_potoken", "clean", "yt_test", "cancel", "golden", "auto",
            "auto_now", "auto_resume", "fb_test", "kaggle_test", "kaggle_check", "kids", "kids_now", "kids_setup",
            "kids_setup_status", "kids_bgm", "kids_fb_test", "kids_resume", "kids_tick", "cook_bgm", "bgm_kaggle")


def normalize(cmd: str) -> str:
    """Chữ thường; tên lệnh gõ «-» thay «_» vẫn nhận (25/11: «kids_setup-status»). Phần sau tên lệnh (link…) giữ nguyên."""
    head, sep, rest = cmd.strip().partition(" ")
    return head.lower().replace("-", "_") + sep + rest


def unknown(cmd: str) -> str:
    """Gõ sai lệnh: gợi ý lệnh gần nhất thay vì in cả trang trợ giúp (25/11: «kid_setup», «kids_check»)."""
    head = normalize(cmd).split(" ")[0]
    near = difflib.get_close_matches(head, COMMANDS, n=3, cutoff=0.6)
    if near:
        return f"Không hiểu lệnh «{cmd[:40]}». Có phải ý bạn là: {' / '.join(near)} ? (gõ help để xem mọi lệnh)"
    return f"Không hiểu lệnh «{cmd[:40]}». {CONSOLE_HELP}"


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
        low = normalize(cmd)
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
                await asyncio.to_thread(storage.clean, lambda s: logging.info("[clean] %s", s), bool(current["job"]))
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
                logging.info("Tự động đăng: %s", await autopost.resume())
            elif low == "kaggle_check":
                import kaggle_worker
                for line in await asyncio.to_thread(kaggle_worker.resources_report):
                    logging.info("%s", line)
            elif low == "kaggle_test":
                import kaggle_api
                if not kaggle_api.configured():
                    logging.info("❌ Chưa điền KAGGLE_API_TOKEN trong Cài đặt")
                else:
                    try:
                        who = await asyncio.to_thread(kaggle_api.owner)
                        q = await asyncio.to_thread(kaggle_api.quota)
                        logging.info("✅ Kaggle dùng được: tài khoản %s, %s", who or "?", kaggle_api.quota_text(q))
                        typed = kaggle_api.clean_user(config.KAGGLE_USERNAME)
                        if who and typed and typed != who:
                            logging.info("ℹ️ KAGGLE_USERNAME đang điền '%s' nhưng token là của '%s' — bot tự dùng '%s'",
                                         typed, who, who)
                    except Exception as e:
                        logging.info("❌ Kaggle lỗi: %s", e)
            elif low == "fb_test":
                try:
                    logging.info("✅ Token dùng được cho Fanpage: %s", await autopost.check_token())
                except Exception as e:
                    logging.info("❌ Token Fanpage lỗi: %s", e)
            elif low in ("kids", "kids_status"):
                import kids_auto
                logging.info(kids_auto.status_text())
            elif low == "kids_now":
                import kids_auto
                job = kids_auto.make_now("web")
                logging.info("🧸 Đã xếp job #%s: %s", job.id, job.title_hint)
            elif low == "kids_tick":
                import kids_auto
                logging.info("Truyện bé: %s", await kids_auto.tick(force=True))
            elif low == "kids_resume":
                import kids_auto
                logging.info("Truyện bé: %s", await kids_auto.resume())
            elif low == "kids_setup":
                import kaggle_worker
                try:
                    logging.info("🧸 %s", await asyncio.to_thread(kaggle_worker.kids_setup))
                except Exception as e:
                    logging.info("❌ kids_setup lỗi: %s", e)
            elif low in ("kids_bgm", "cook_bgm"):
                from rmvideo.media import lyria_kaggle
                if lyria_kaggle.proven():                 # mạng Botkeep không theo kịp Lyria: kho làm trên Kaggle (1.4.5)
                    logging.info("🎵 %s", lyria_kaggle.start_later(force=True) or "không có gì để làm")
                elif low == "kids_bgm":
                    from rmvideo.kids import bgm
                    try:
                        logging.info("🎵 %s", await bgm.build(log=lambda m: logging.info("🎵 %s", m)))
                    except Exception as e:
                        logging.info("❌ kids_bgm lỗi: %s", e)
                else:
                    import bgm
                    logging.info("🎵 %s", bgm.build_lyria_later())
            elif low == "bgm_kaggle":
                from rmvideo.media import lyria_kaggle
                logging.info("🎵 %s", lyria_kaggle.start_later(force=True) or "không có gì để làm")
            elif low == "kids_setup_status":
                import kaggle_worker
                try:
                    logging.info("🧸 %s", await asyncio.to_thread(kaggle_worker.kids_setup_status))
                except Exception as e:
                    logging.info("❌ %s", e)
            elif low == "kids_fb_test":
                try:
                    logging.info("✅ Token dùng được cho Fanpage truyện bé: %s",
                                 await autopost.check_token(page=config.KIDS_FB_PAGE_ID, token=config.KIDS_FB_PAGE_TOKEN))
                except Exception as e:
                    logging.info("❌ Token Fanpage truyện bé lỗi: %s", e)
            elif low.startswith("cancel "):
                logging.info(cancel_job(int(low.split()[1])))
            else:
                logging.info("%s", unknown(cmd))
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
                await notifier.job_event(job, f"⌛ Kịch bản job #{job.id} đã quá 24 giờ chưa duyệt nên hết hạn.")
            metrics.prune()
            await asyncio.to_thread(cache.prune)
            await monitor.tick()
        except Exception:
            logging.exception("Bảo trì định kỳ lỗi")
        await asyncio.sleep(MAINTENANCE_EVERY)
