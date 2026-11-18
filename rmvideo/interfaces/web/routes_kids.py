"""🧸 Truyện bé: trạng thái, làm 1 truyện ngay, thử token Fanpage riêng, chuẩn bị model trên Kaggle, chạy lại sau lỗi."""

from aiohttp import web

from queue_manager import JobQueue
from rmvideo.interfaces.web.auth import _check


def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    def _payload() -> dict:
        import kids_auto
        st = kids_auto.load_state()
        return {"status": kids_auto.status_text(), "times": kids_auto.times(),
                "stories": [{"job": s.get("job"), "title": s.get("title", ""), "words": s.get("words", []),
                             "created_at": s.get("created_at")} for s in st["stories"][-10:][::-1]]}

    async def kids_status(request):
        _check(request)
        p = _payload()
        return web.json_response({"text": p["status"], **p})

    async def kids_action(request):
        """now: làm 1 truyện ngay (không tự đăng) | test: thử token Fanpage truyện bé | setup: đẩy notebook chuẩn bị
        model | setup_status | resume: chạy lại sau lỗi | tick: một lượt tự động (khi KIDS_AUTO bật)."""
        _check(request)
        import kaggle_worker
        import kids_auto
        act = request.match_info["act"]
        try:
            if act == "now":
                job = kids_auto.make_now("web")
                msg = f"✅ Đã xếp job #{job.id}: {job.title_hint}. Theo dõi ở Hàng đợi; xong thì tải về soát."
            elif act == "test":
                import autopost
                import config
                msg = "✅ Token dùng được cho Fanpage truyện bé: " + await autopost.check_token(
                    page=config.KIDS_FB_PAGE_ID, token=config.KIDS_FB_PAGE_TOKEN)
            elif act == "setup":
                msg = "✅ " + kaggle_worker.kids_setup()
            elif act == "setup_status":
                msg = "ℹ️ " + kaggle_worker.kids_setup_status()
            elif act == "resume":
                msg = await kids_auto.resume()
            elif act == "tick":
                msg = await kids_auto.tick(force=True)
            else:
                raise web.HTTPNotFound()
        except web.HTTPException:
            raise
        except Exception as e:
            msg = f"❌ {str(e)[:300]}"
        return web.json_response({"text": msg, **_payload()})

    app.router.add_get("/api/kids", kids_status)
    app.router.add_post("/api/kids/{act}", kids_action)
