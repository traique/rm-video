"""Trang + đăng nhập / đăng xuất / tình trạng (công khai: /health, /login)."""
import asyncio
import os

from aiohttp import web

import config
from queue_manager import JobQueue
from rmvideo.interfaces.web.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    _authed,
    _check_origin,
    _client_key,
    _is_https,
    _password_ok,
    login_locked,
    make_session,
    record_fail,
    revoke_sessions,
)
from rmvideo.interfaces.web.http import LOGIN_BODY_MAX
from rmvideo.version import VERSION

_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # gốc bot
INDEX_HTML = os.path.join(_DIR, "static", "index.html")
LOGIN_HTML = os.path.join(_DIR, "static", "login.html")


def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    async def index(request):
        """Bảng điều khiển chỉ hiện khi đã đăng nhập; chưa thì sang trang đăng nhập riêng (/login)."""
        if not _authed(request):
            raise web.HTTPFound("/login")
        return web.FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})

    async def login_page(request):
        if _authed(request):
            raise web.HTTPFound("/")
        return web.FileResponse(LOGIN_HTML, headers={"Cache-Control": "no-cache"})

    async def health(_):
        # công khai cho uptime-check nên không lộ gì ngoài "còn sống" và số job chờ
        return web.json_response({"ok": True, "pending": queue.pending})

    async def me(request):
        authed = _authed(request)
        # phiên bản chỉ cho người đã đăng nhập (trang công khai không nói bot đang chạy bản nào)
        return web.json_response({"password_set": bool(config.WEB_PASSWORD), "authed": authed,
                                  **({"version": VERSION} if authed else {})})

    async def login(request):
        if not config.WEB_PASSWORD:
            raise web.HTTPForbidden(text="Chưa đặt WEB_PASSWORD: đặt biến môi trường rồi khởi động lại bot.")
        _check_origin(request)
        key = _client_key(request)
        wait = login_locked(key)
        if wait:
            raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
        if (request.content_length or 0) > LOGIN_BODY_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=LOGIN_BODY_MAX, actual_size=request.content_length)
        try:
            pw = str((await request.json()).get("password", ""))
        except Exception:
            pw = ""
        if not _password_ok(pw):
            record_fail(key)
            await asyncio.sleep(0.5)                    # làm chậm dò mật khẩu
            raise web.HTTPUnauthorized(text="Sai mật khẩu")
        resp = web.json_response({"ok": True})
        resp.set_cookie(SESSION_COOKIE, make_session(), max_age=SESSION_TTL, httponly=True,
                        samesite="Strict", secure=_is_https(request), path="/")
        return resp

    async def logout(request):
        _check_origin(request)
        if _authed(request):                      # thu hồi mọi phiên đã cấp (chỉ khi chính phiên hợp lệ yêu cầu)
            revoke_sessions()
        resp = web.json_response({"ok": True})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    app.router.add_get("/", index)
    app.router.add_get("/login", login_page)
    app.router.add_get("/health", health)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
