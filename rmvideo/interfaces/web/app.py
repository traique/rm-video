"""Ghép web panel: trần body + header bảo mật (middleware), rồi từng nhóm đường dẫn.

Trước đây toàn bộ là MỘT hàm build_app 592 dòng chứa 30 handler lồng nhau (web_panel.py); giờ mỗi nhóm một module
routes_*.py, phiên/mật khẩu/CSRF ở auth.py, tiện ích request ở http.py. web_panel.py còn là mặt tiền tương thích.
"""
from __future__ import annotations

from aiohttp import web

from queue_manager import JobQueue
from rmvideo.interfaces.web import (
    routes_auth,
    routes_autopost,
    routes_jobs,
    routes_media,
    routes_settings,
    routes_zoom,
)
from rmvideo.interfaces.web.auth import _secure_headers
from rmvideo.interfaces.web.http import JSON_MAX

GROUPS = (routes_auth, routes_jobs, routes_settings, routes_autopost, routes_media, routes_zoom)


def build_app(queue: JobQueue, hooks: dict | None = None) -> web.Application:
    """hooks: {"cancel": huỷ job, "approve": duyệt kịch bản, "speed": giây máy / giây video cho ETA}."""
    hooks = hooks or {}
    app = web.Application(client_max_size=JSON_MAX, middlewares=[_secure_headers])
    for group in GROUPS:
        group.add_routes(app, queue, hooks)
    return app
