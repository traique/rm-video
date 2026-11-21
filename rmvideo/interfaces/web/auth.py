"""Phiên đăng nhập web (cookie ký HMAC, thu hồi khi đăng xuất), mật khẩu, khoá dò mật khẩu theo IP và toàn cục, chống CSRF theo Origin, header bảo mật."""
import hashlib
import hmac
import logging
import os
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web

import config
from rmvideo.app import events

_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # gốc bot


SESSION_COOKIE = "vb_session"


SESSION_TTL = 30 * 86400


_KEY_FILE = os.path.join(_DIR, ".session_key")


FAIL_WINDOW, FAIL_PER_IP, FAIL_GLOBAL = 60.0, 5, 30   # 5 lần sai/phút mỗi IP, 30 lần/phút toàn bộ


_fails: dict[str, list[float]] = {}


def _server_salt() -> bytes:
    """Muối ngẫu nhiên lưu cạnh bot (quyền 600): cookie không giả mạo được kể cả khi biết mã nguồn."""
    try:
        with open(_KEY_FILE, "rb") as f:
            salt = f.read()
        if len(salt) >= 32:
            return salt
    except OSError:
        pass
    salt = secrets.token_bytes(32)
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(salt)
    return salt


def _epoch() -> bytes:
    """Mốc thu hồi phiên: đăng xuất đổi mốc -> mọi cookie đã cấp hết hiệu lực. Trước đây đăng xuất chỉ xoá cookie phía
    trình duyệt, cookie bị sao chép (máy dùng chung, log proxy) vẫn dùng được tới 30 ngày."""
    try:
        with open(_KEY_FILE + ".epoch", "rb") as f:
            return f.read(64)
    except OSError:
        return b"0"


def revoke_sessions():
    fd = os.open(_KEY_FILE + ".epoch", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secrets.token_hex(16).encode())
    events.changed()                       # đánh thức các luồng SSE để chúng kiểm lại phiên và tự đóng


def _session_key() -> bytes:
    return hashlib.sha256(b"vb-session|" + _server_salt() + b"|" + _epoch() + b"|"
                          + config.WEB_PASSWORD.encode()).digest()


def make_session(now: float | None = None) -> str:
    exp = str(int((now or time.time()) + SESSION_TTL))
    sig = hmac.new(_session_key(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_session(token: str, now: float | None = None) -> bool:
    try:
        exp, sig = (token or "").split(".", 1)
        if int(exp) < (now or time.time()):
            return False
    except ValueError:
        return False
    good = hmac.new(_session_key(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.encode(), good.encode())     # so bytes: chuỗi có dấu không làm lỗi 500


def _password_ok(pw: str) -> bool:
    return bool(config.WEB_PASSWORD) and hmac.compare_digest(pw.encode(), config.WEB_PASSWORD.encode())


def _client_key(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return fwd or request.remote or "?"


def _recent(key: str, now: float) -> list:
    lst = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    _fails[key] = lst
    return lst


def login_locked(key: str, now: float | None = None) -> int:
    """Số giây còn bị khoá (0 = được thử). Khoá theo IP và cả toàn cục (IP giả qua X-Forwarded-For)."""
    now = now or time.time()
    for k, cap in ((key, FAIL_PER_IP), ("*", FAIL_GLOBAL)):
        lst = _recent(k, now)
        if len(lst) >= cap:
            return int(FAIL_WINDOW - (now - lst[0])) + 1
    return 0


def record_fail(key: str, now: float | None = None):
    now = now or time.time()
    for k in (key, "*"):
        _recent(k, now).append(now)
    if len(_fails) > 2000:                        # X-Forwarded-For giả ngẫu nhiên: không để bảng đếm phình mãi
        for k in [k for k, v in _fails.items() if not v or now - v[-1] >= FAIL_WINDOW]:
            _fails.pop(k, None)


def _allowed_hosts(request: web.Request) -> set:
    hosts = {request.host}
    if request.headers.get("X-Forwarded-Host"):
        hosts.add(request.headers["X-Forwarded-Host"].split(",")[0].strip())
    if config.PUBLIC_URL:
        hosts.add(urlsplit(config.PUBLIC_URL).netloc)
    return {h.lower() for h in hosts if h}


def _check_origin(request: web.Request):
    """Chống CSRF cho request đổi trạng thái: Origin (nếu trình duyệt gửi) phải là chính trang này."""
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return                      # cookie SameSite=Strict đã không đi kèm request từ trang khác
    host = urlsplit(origin).netloc.lower()
    if host not in _allowed_hosts(request):
        logging.warning("Chặn request khác nguồn: Origin=%s Host=%s", origin, request.host)
        raise web.HTTPForbidden(text="Request từ trang khác bị chặn (CSRF). Nếu proxy đổi tên miền, đặt biến "
                                     "môi trường PUBLIC_URL bằng link web của bạn rồi khởi động lại bot.")


def _authed(request: web.Request) -> bool:
    """Chỉ xét cookie phiên (dùng cho /api/me công khai: không được thành chỗ dò mật khẩu)."""
    return bool(config.WEB_PASSWORD) and valid_session(request.cookies.get(SESSION_COOKIE, ""))


def _check(request: web.Request):
    if not config.WEB_PASSWORD:
        raise web.HTTPForbidden(text="Web panel đang khoá vì chưa đặt WEB_PASSWORD. "
                                     "Đặt biến môi trường WEB_PASSWORD rồi khởi động lại bot.")
    hdr = request.headers.get("X-Auth")
    if hdr is not None:
        # cho script/curl: header tuỳ biến không gửi được từ trang khác (khỏi kiểm Origin), nhưng vẫn chịu
        # cùng giới hạn thử mật khẩu như trang đăng nhập
        key = _client_key(request)
        wait = login_locked(key)
        if wait:
            raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
        if not _password_ok(hdr):
            record_fail(key)
            raise web.HTTPUnauthorized(text="Sai mật khẩu")
        return
    if request.method not in ("GET", "HEAD"):
        _check_origin(request)
    if not _authed(request):
        raise web.HTTPUnauthorized(text="Cần đăng nhập")


def _is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


@web.middleware
async def _secure_headers(request: web.Request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        _harden(e.headers)
        raise
    _harden(resp.headers)
    return resp


def _harden(h):
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("X-Robots-Tag", "noindex, nofollow")       # bảng điều khiển riêng: không bao giờ lên máy tìm kiếm
