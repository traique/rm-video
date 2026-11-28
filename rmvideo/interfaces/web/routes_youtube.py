"""▶️ YouTube (1.2.0): kết nối kênh (OAuth), thử, ngắt; trang Google chuyển về sau khi chọn kênh."""
import asyncio
import html

from aiohttp import web

from queue_manager import JobQueue
from rmvideo.app import youtube
from rmvideo.interfaces.web.auth import _check, _client_key, _password_ok, login_locked, record_fail
from rmvideo.interfaces.web.http import REAUTH_FIELD, _json_dict

_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kết nối YouTube</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;background:#fff;color:#111}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}a{{color:#8ab4f8}}}}</style></head>
<body><h1>▶️ Kết nối YouTube</h1><p>{msg}</p><p><a href="/">← Về bảng điều khiển</a></p></body></html>"""


def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    async def yt_status(request: web.Request) -> web.Response:
        _check(request)
        return web.json_response({ch: youtube.status_lines(ch) for ch in youtube.CHANNELS})

    async def yt_action(request: web.Request) -> web.Response:
        """connect: link trang Google (phải nhập lại mật khẩu web như token Fanpage: đổi nơi video được đăng tới)
        | test: thử kết nối | disconnect: ngắt kênh."""
        _check(request)
        ch, act = request.match_info["ch"], request.match_info["act"]
        if ch not in youtube.CHANNELS or act not in ("connect", "test", "disconnect"):
            raise web.HTTPNotFound()
        body = await _json_dict(request) if request.can_read_body else {}
        vi = youtube.CHANNELS[ch]["vi"]
        if act in ("connect", "disconnect") and request.headers.get("X-Auth") is None:
            key = _client_key(request)
            wait = login_locked(key)
            if wait:
                raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
            pw = str(body.get(REAUTH_FIELD) or "")
            if not _password_ok(pw):
                if pw:
                    record_fail(key)
                    await asyncio.sleep(0.5)
                return web.json_response({"need_password": True, "keys": [youtube.CHANNELS[ch]["key"]],
                                          "error": "Sai mật khẩu" if pw else f"Nhập lại mật khẩu web để đổi YouTube {vi}"},
                                         status=403)
        try:
            if act == "connect":
                return web.json_response({"url": youtube.start(ch)})
            if act == "test":
                text = f"✅ YouTube {vi}: kênh «{await youtube.check(ch)}»"
            else:
                await youtube.disconnect(ch)
                text = f"✅ Đã ngắt YouTube {vi}"
        except youtube.YTError as e:
            text = f"❌ {e}"
        return web.json_response({"text": text, "status": youtube.status_lines(ch)})

    async def callback(request: web.Request) -> web.Response:
        q = request.query
        if q.get("error"):
            youtube._PENDING.pop(q.get("state", ""), None)
            msg = f"❌ Chưa kết nối: Google báo «{q['error']}» (bạn bấm huỷ hoặc chưa cấp quyền). Bấm lại «▶️ Kết nối YouTube»."
        else:
            try:
                ch, title, warn = await youtube.finish(q.get("state", ""), q.get("code", ""))
                msg = f"✅ Đã kết nối {youtube.CHANNELS[ch]['vi']} với kênh YouTube «{title}». {warn}".strip()
            except youtube.YTError as e:
                msg = f"❌ {e}"
            except Exception as e:
                msg = f"❌ Lỗi khi kết nối: {str(e)[:200]}"
        return web.Response(text=_PAGE.format(msg=html.escape(msg)), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/youtube", yt_status)
    app.router.add_post("/api/youtube/{ch}/{act}", yt_action)
    app.router.add_get(youtube.CALLBACK, callback)
