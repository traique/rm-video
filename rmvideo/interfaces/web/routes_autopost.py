"""🤖 Tự động đăng: trạng thái, lấy video ngay, thử token, kênh nguồn, link dán tay."""

from aiohttp import web

from queue_manager import JobQueue
from rmvideo.interfaces.web.auth import _check
from rmvideo.interfaces.web.http import _json_dict



def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    def _auto_payload() -> dict:
        import autopost
        st = autopost.load_state()
        return {"status": autopost.status_text(), "platforms": autopost.platforms(),
                "sources": {"facebook": autopost.sources(), "tiktok": autopost.tiktok_sources()},
                "manual": [{"url": m.get("url", ""), "title": m.get("title", ""), "dur": m.get("dur", 0)}
                           for m in st["manual"]]}

    async def auto_status(request):
        _check(request)
        p = _auto_payload()
        return web.json_response({"text": p["status"], **p})

    async def auto_action(request):
        """now: lấy video ngay | test: thử token | resume: chạy lại sau lỗi | source: thêm/bỏ kênh nguồn ({url, delete?})
        | queue: xếp/bỏ link video dán tay ({url, delete?})."""
        _check(request)
        import autopost
        act = request.match_info["act"]
        try:
            if act == "now":
                msg = await autopost.tick(force=True)
            elif act == "test":
                msg = "✅ Token dùng được cho Fanpage: " + await autopost.check_token()
            elif act == "resume":
                msg = await autopost.resume()
            elif act == "source":
                d = await _json_dict(request)
                url = str(d.get("url") or "").strip()
                if d.get("delete"):
                    msg = "✅ Đã bỏ kênh" if autopost.remove_source(url) else "Không thấy kênh này trong danh sách"
                else:
                    plat, norm = autopost.add_source(url)
                    msg = f"✅ Đã thêm kênh {'TikTok' if plat == 'tiktok' else 'Facebook'}: {norm}"
            elif act == "queue":
                d = await _json_dict(request)
                url = str(d.get("url") or "").strip()
                if d.get("delete"):
                    msg = "✅ Đã bỏ link khỏi hàng chờ" if await autopost.remove_link(url) else "Không thấy link này"
                else:
                    m = await autopost.queue_link(url)
                    msg = (f"✅ Đã xếp «{m['title'] or m['url']}» ({m['dur']}s): lượt lấy video kế tiếp sẽ làm video này "
                           "trước, xong sẽ đăng vào khung giờ vàng còn trống. Bấm «Lấy video ngay» để làm luôn.")
            else:
                raise web.HTTPNotFound()
        except web.HTTPException:
            raise
        except Exception as e:
            msg = f"❌ {str(e)[:300]}"
        return web.json_response({"text": msg, **_auto_payload()})

    app.router.add_get("/api/autopost", auto_status)
    app.router.add_post("/api/autopost/{act}", auto_action)
