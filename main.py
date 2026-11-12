"""Điểm chạy: python main.py. Nối các phần: hàng đợi + worker (jobs), web panel, bot Telegram, console quản trị."""
import asyncio
import logging
import shutil
import signal

from aiohttp import web

import config
import state
from admin import CONSOLE_HELP, console, maintenance
from jobs import cancel_job, speed_ratio, worker
from review import approve_job
from state import notify, queue, spawn
from storage import disk_report, warn_low_disk
from web_panel import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S")
for _noisy in ("google_genai", "google_genai.models", "httpx", "aiogram.event"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)      # bỏ dòng "AFC is enabled..." và log mỗi request


async def main():
    dp = None
    if config.BOT_TOKEN:
        from aiogram import Bot
        import tg_bot
        state.bot = Bot(config.BOT_TOKEN)
        dp = tg_bot.setup(state.bot)
    bot = state.bot
    shutil.rmtree(state.WORK_DIR, ignore_errors=True)        # file tạm sót lại từ lần chạy bị ngắt (TRƯỚC khi worker chạy)
    spawn(worker())
    spawn(console())
    spawn(maintenance())
    for job in queue.restored:
        logging.info("Chạy lại job #%s sau khi khởi động lại", job.id)
        if job.source == "tg":
            spawn(notify(job.chat_id, f"🔄 Bot vừa khởi động lại, job #{job.id} được chạy tiếp."))

    app = build_app(queue, {"cancel": cancel_job, "approve": approve_job, "speed": speed_ratio})
    runner = web.AppRunner(app, access_log=None)      # không ghi log mỗi lần web polling (giảm 95% log)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    logging.info("Web panel chạy tại cổng %s", config.PORT)
    logging.info("BOT_TOKEN: %s | WEB_PASSWORD: %s",
                 "OK" if config.BOT_TOKEN else "THIẾU", "OK" if config.WEB_PASSWORD else "THIẾU")
    if not config.WEB_PASSWORD:
        logging.warning("Chưa đặt WEB_PASSWORD: web panel đang KHOÁ. Đặt biến môi trường WEB_PASSWORD để dùng.")
    logging.info(CONSOLE_HELP)
    logging.info("Dung lượng: %s", disk_report())
    warn_low_disk()
    spawn(_ffmpeg_selftest())
    import autopost
    spawn(autopost.run_forever())                  # 🤖 tự động đăng Facebook (AUTO_POST=1 mới chạy)
    import potoken_setup
    if not potoken_setup.js_runtime():
        logging.info("YouTube: chưa cài bộ giải JavaScript (deno). Cần tải YouTube thì gõ  setup_youtube  (~100MB).")
    if bot and not config.allowed_ids():
        logging.warning("Chưa đặt ALLOWED_USERS: bot Telegram từ chối mọi người. Nhắn bot để biết chat id "
                        "của bạn rồi điền vào Cài đặt trên web.")

    stop = asyncio.Event()                         # SIGTERM (Botkeep dừng server) / Ctrl-C -> thoát gọn
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    if bot:
        await poll_forever(dp, bot, stop)
    else:
        logging.warning("Không có BOT_TOKEN — chạy chế độ chỉ-web.")
        await stop.wait()
    logging.info("Nhận tín hiệu dừng, thoát.")


async def _ffmpeg_selftest():
    """Lúc khởi động: thử bản ffmpeg trên máy với đủ bộ lọc bot dùng; hỏng thì báo NGAY trong log (trước đây lỗi
    tương thích FFmpeg 8 trên Botkeep chỉ lộ ra khi đang làm video)."""
    import core_video
    ok, detail = await asyncio.to_thread(core_video.selftest_sync)
    if ok:
        logging.info("ffmpeg tự kiểm tra: OK (%s)", detail[:60])
    else:
        logging.error("ffmpeg tự kiểm tra LỖI — video có thể hỏng ở bước dùng bộ lọc này: %s", detail)
        return
    sp = core_video.render_speed()
    if sp is None:                      # lần đầu trên máy này: đo tốc độ dựng để chọn 1080p/720p (xem output_size)
        sp = await asyncio.to_thread(core_video.render_benchmark_sync)
        if sp:
            logging.info("Đo tốc độ dựng: %.1f giây máy cho 1 giây video 1080p -> video 1080p dài hơn %.0fs sẽ tự "
                         "ra 720p (QUALITY=balanced, RENDER_BUDGET=%.0fs)", sp, core_video.RENDER_BUDGET / sp,
                         core_video.RENDER_BUDGET)
    else:
        logging.info("Tốc độ dựng đã đo: %.1f giây máy / giây video 1080p", sp)


async def poll_forever(dp, bot, stop: asyncio.Event | None = None, first_wait: float = 15, max_wait: float = 300):
    """Long polling Telegram (không cần mở thêm port). Mất mạng/Telegram lỗi lúc khởi động trước đây làm chết cả
    tiến trình, kéo theo web panel; giờ chỉ ghi log rồi thử lại, chờ tăng dần tới 5 phút. Tín hiệu dừng do main
    tự xử lý (aiogram bỏ qua tín hiệu đến trong lúc chờ thử lại), `stop` được đặt là thoát ngay."""
    stop = stop or asyncio.Event()
    wait = first_wait
    while not stop.is_set():
        poll = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({poll, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            try:
                await dp.stop_polling()
            except Exception:
                poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)
            return
        stopper.cancel()
        exc = poll.exception()
        if exc is None:
            return
        logging.error("Telegram polling lỗi (%s), thử lại sau %.0fs; web vẫn chạy", str(exc)[:150], wait)
        try:
            await asyncio.wait_for(stop.wait(), wait)
            return
        except asyncio.TimeoutError:
            wait = min(max_wait, wait * 2)


if __name__ == "__main__":
    asyncio.run(main())
