"""Điểm chạy: python main.py. Nối các phần: hàng đợi + worker (jobs), web panel, bot Telegram, console quản trị."""
import asyncio
import logging
import shutil

from aiohttp import web

import config
import state
from admin import CONSOLE_HELP, console, maintenance
from jobs import cancel_job, speed_ratio, worker
from review import approve_job
from state import notify, queue, spawn
from storage import disk_report
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
    if bot and not config.allowed_ids():
        logging.warning("Chưa đặt ALLOWED_USERS: bot Telegram từ chối mọi người. Nhắn bot để biết chat id "
                        "của bạn rồi điền vào Cài đặt trên web.")

    if bot:
        await poll_forever(dp, bot)
    else:
        logging.warning("Không có BOT_TOKEN — chạy chế độ chỉ-web.")
        await asyncio.Event().wait()


async def poll_forever(dp, bot, first_wait: float = 15, max_wait: float = 300):
    """Long polling Telegram (không cần mở thêm port). Mất mạng/Telegram lỗi lúc khởi động trước đây làm chết cả
    tiến trình, kéo theo web panel; giờ chỉ ghi log rồi thử lại, chờ tăng dần tới 5 phút."""
    wait = first_wait
    while True:
        try:
            await dp.start_polling(bot)          # tự xử lý SIGTERM: dừng gọn rồi return
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error("Telegram polling lỗi (%s), thử lại sau %.0fs; web vẫn chạy", str(e)[:150], wait)
            await asyncio.sleep(wait)
            wait = min(max_wait, wait * 2)


if __name__ == "__main__":
    asyncio.run(main())
