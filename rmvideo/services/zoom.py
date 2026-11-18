"""Zoom Team Chat chatbot (bản 1.0.0): gửi tin CHỮ (kèm link tải, KHÔNG gửi file video) và kiểm chữ ký webhook.

Cài đặt một lần (Zoom App Marketplace → Develop → Build App → General App):
- Features → Surface → bật Team Chat → Chatbot: «Bot endpoint URL» = <PUBLIC_URL>/zoom/events; ghi lại Bot JID.
- App Credentials: Client ID, Client Secret. Features → Access: Secret Token (khoá ký webhook).
- Cài app vào tài khoản (Local Test → Add App Now), mở Zoom → Team Chat → Apps → nhắn bot «id» để lấy JID của mình.

API (developers.zoom.us): token chatbot = client credentials (POST zoom.us/oauth/token, hạn 1 giờ); gửi tin = POST
api.zoom.us/v2/im/chat/messages {robot_jid, to_jid, account_id, content}; webhook ký bằng HMAC-SHA256(Secret Token,
"v0:{x-zm-request-timestamp}:{body}"), xác minh địa chỉ bằng endpoint.url_validation (encryptedToken = HMAC hex).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time

import aiohttp

import config

TOKEN_URL = "https://zoom.us/oauth/token?grant_type=client_credentials"
SEND_URL = "https://api.zoom.us/v2/im/chat/messages"
MAX_SKEW = 300                    # giây: webhook cũ hơn chừng này bị bỏ (chống phát lại)
_token: dict = {"value": "", "exp": 0.0, "key": ""}
_lock = threading.Lock()


class ZoomError(RuntimeError):
    pass


def configured() -> bool:
    """Đủ khoá để GỬI tin."""
    return bool(config.ZOOM_CLIENT_ID and config.ZOOM_CLIENT_SECRET and config.ZOOM_BOT_JID)


def inbound_ready() -> bool:
    """Đủ khoá để NHẬN tin (kiểm chữ ký)."""
    return bool(config.ZOOM_SECRET_TOKEN)


def allowed_ids() -> set:
    """ZOOM_ALLOWED_USERS: JID (…@xmpp.zoom.us) hoặc user id, cách nhau dấu phẩy. Trống = từ chối tất cả (như Telegram)."""
    return {x.strip().lower() for x in (config.ZOOM_ALLOWED_USERS or "").split(",") if x.strip()}


def is_allowed(user_jid: str, user_id: str = "") -> bool:
    ids = allowed_ids()
    return bool(ids) and bool({(user_jid or "").lower(), (user_id or "").lower()} & ids)


# ---------------- chữ ký webhook ----------------

def _mac(msg: bytes) -> str:
    return hmac.new(config.ZOOM_SECRET_TOKEN.encode(), msg, hashlib.sha256).hexdigest()


def signature_ok(raw: bytes, ts: str, sig: str, now: float | None = None) -> bool:
    """x-zm-signature == "v0=" + HMAC-SHA256(Secret Token, "v0:{timestamp}:{body}") và timestamp còn mới."""
    if not (config.ZOOM_SECRET_TOKEN and ts and sig):
        return False
    try:
        t = float(ts)
    except ValueError:
        return False
    t = t / 1000 if t > 1e11 else t                      # có nơi gửi mili giây
    if abs((time.time() if now is None else now) - t) > MAX_SKEW:
        return False
    return hmac.compare_digest("v0=" + _mac(b"v0:" + ts.encode() + b":" + raw), sig)


_PLAIN = re.compile(r"[A-Za-z0-9_\-]{8,128}")


def url_validation(plain: str) -> dict:
    """Trả lời endpoint.url_validation. plainToken phải là chuỗi ngẫu nhiên kiểu Zoom (không có dấu ':'): không để kẻ
    lạ lấy HMAC của "v0:{ts}:{body}" tự chọn rồi giả chữ ký."""
    if not _PLAIN.fullmatch(plain or ""):
        raise ValueError("plainToken không hợp lệ")
    return {"plainToken": plain, "encryptedToken": _mac(plain.encode())}


# ---------------- nơi gửi (học từ tin nhắn đến) ----------------

def _targets_path() -> str:
    return os.environ.get("ZOOM_TARGETS") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "zoom_targets.json")


def remember(user_jid: str, to_jid: str, account_id: str) -> None:
    """Lưu nơi trả lời của mỗi người (account_id chỉ biết được từ tin nhắn đến) để gửi thông báo chủ động sau này."""
    if not (user_jid and account_id):
        return
    with _lock:
        d = targets()
        d[user_jid.lower()] = {"to": to_jid or user_jid, "account": account_id, "user": user_jid}
        tmp = _targets_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _targets_path())


def targets() -> dict:
    try:
        with open(_targets_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def admin_target() -> dict | None:
    """Nơi nhận thông báo chung (job làm trên web / tự đăng, phiên bản mới): ZOOM_ADMIN, không đặt thì người đầu tiên
    trong ZOOM_ALLOWED_USERS — phải nhắn bot ít nhất một lần để bot biết account_id."""
    want = (config.ZOOM_ADMIN or "").strip().lower() or next(iter(sorted(allowed_ids())), "")
    if not want:
        return None
    d = targets()
    return d.get(want) or next((v for v in d.values() if v.get("to", "").lower() == want), None)


# ---------------- gửi tin ----------------

async def _token_value(session: aiohttp.ClientSession) -> str:
    key = f"{config.ZOOM_CLIENT_ID}:{config.ZOOM_CLIENT_SECRET}"
    if _token["value"] and _token["key"] == key and time.time() < _token["exp"] - 60:
        return _token["value"]
    basic = base64.b64encode(key.encode()).decode()
    async with session.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"}) as r:
        data = await r.json(content_type=None)
        if r.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
            raise ZoomError(f"lấy token chatbot lỗi (HTTP {r.status}): {str(data)[:160]} — kiểm tra Client ID / Secret")
    _token.update(value=data["access_token"], exp=time.time() + float(data.get("expires_in") or 3599), key=key)
    return _token["value"]


def message_body(to_jid: str, account_id: str, text: str, head: str, user_jid: str = "") -> dict:
    body = {"robot_jid": config.ZOOM_BOT_JID, "to_jid": to_jid, "account_id": account_id,
            "content": {"head": {"text": head[:120]}, "body": [{"type": "message", "text": text[:3800]}]}}
    if user_jid and config.ZOOM_APP_TYPE != "admin":          # app «User-managed» bắt buộc user_jid
        body["user_jid"] = user_jid
    return body


async def send(target: dict, text: str, head: str = "rmvideo") -> None:
    """Gửi một tin chữ tới target {to, account, user}. Ném ZoomError khi chưa cài / Zoom báo lỗi (caller tự xử lý)."""
    if not configured():
        raise ZoomError("chưa điền Client ID / Client Secret / Bot JID của Zoom (Cài đặt → 🔑 Tài khoản & API)")
    if not target or not target.get("to") or not target.get("account"):
        raise ZoomError("chưa biết nơi gửi: nhắn bot trên Zoom một tin (vd «id») để bot ghi nhận")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        tok = await _token_value(s)
        payload = message_body(target["to"], target["account"], text, head, target.get("user", ""))
        async with s.post(SEND_URL, json=payload, headers={"Authorization": f"Bearer {tok}"}) as r:
            if r.status >= 300:
                raise ZoomError(f"gửi tin Zoom lỗi (HTTP {r.status}): {(await r.text())[:200]}")


async def notify(target: dict | None, text: str, head: str = "rmvideo") -> str:
    """Như send nhưng KHÔNG BAO GIỜ ném lỗi (thông báo hỏng không được làm hỏng job). Trả "ok" hoặc lý do lỗi."""
    try:
        await send(target or {}, text, head)
        return "ok"
    except Exception as e:
        logging.warning("Không gửi được tin Zoom: %s", str(e)[:200])
        return str(e)[:200]
