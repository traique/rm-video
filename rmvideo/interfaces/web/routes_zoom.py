"""Kênh Zoom Team Chat (bản 1.0.0): nhắn link cho chatbot Zoom -> bot làm video rồi nhắn lại link tải (KHÔNG gửi file).

POST /zoom/events là đường công khai (Zoom gọi tới) nhưng chỉ nhận request có chữ ký HMAC đúng bằng ZOOM_SECRET_TOKEN và
còn mới (≤5 phút); người nhắn phải nằm trong ZOOM_ALLOWED_USERS (người lạ được báo JID để chủ bot thêm vào, như Telegram).
Zoom đòi trả lời trong 3 giây (chậm là gửi lại), nên kiểm link / xếp job / nhắn lại chạy nền; sự kiện gửi lặp bị bỏ.

Lệnh (nhắn riêng cho bot, hoặc «/tên-bot …» trong kênh):
  <link> [<link>…]   làm lại (nấu ăn, giữ nguyên thời lượng). Thêm chữ để đổi: review · 30 / 60 / 90 (giây) ·
                     full / split60 / split90 / highlight (video dài) · tải (chỉ tải về) · duyệt (duyệt kịch bản trên web)
  trạng thái         5 job gần nhất của bạn (kèm link tải job đã xong)
  huỷ                huỷ job của bạn đang chờ / đang chạy
  id                 JID và user id của bạn (điền vào ZOOM_ALLOWED_USERS)
  help               hướng dẫn

POST /api/notify/test (cần đăng nhập web): gửi thử một tin qua Telegram và Zoom, trả kết quả / lý do từng kênh.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata

from aiohttp import web

import url_guard
from queue_manager import JobQueue, QueueFull
from rmvideo.app import job_factory, notifier
from rmvideo.domain.job import Mode
from rmvideo.interfaces.web.auth import _check
from rmvideo.services import zoom
from state import spawn

ZOOM_BODY_MAX = 64 * 1024        # tin nhắn chatbot chỉ vài KB
MAX_LINKS = 5                    # một tin tối đa 5 link (như hàng đợi web: không để một tin chiếm cả hàng)
SEEN_TTL = 600.0                 # Zoom gửi lại cùng sự kiện khi trả lời chậm: bỏ bản lặp trong 10 phút
_seen: dict[str, float] = {}
_URL_RE = re.compile(r"https?://[^\s<>\"'|]+")
_TRAIL = ".,;:!?)]}>»”’'\""
STATUS_VI = {"queued": "đang chờ", "processing": "đang làm", "review": "chờ duyệt kịch bản trên web",
             "approved": "đã duyệt", "done": "xong", "error": "lỗi", "cancelled": "đã huỷ"}

HELP = ("🎬 Gửi link video (TikTok / YouTube / Facebook...) để bot làm lại; xong bot nhắn link tải vào đây "
        "(Zoom không nhận file video qua bot).\n"
        "Mặc định: nấu ăn, giữ nguyên thời lượng. Thêm chữ sau link để đổi:\n"
        "• review — chế độ review\n"
        "• 30 / 60 / 90 — thời lượng (giây)\n"
        "• full / split60 / split90 / highlight — video dài\n"
        "• tải — chỉ tải video về\n"
        "• duyệt — sửa / duyệt kịch bản trên web trước khi dựng\n"
        "Lệnh khác: «trạng thái» (job của bạn), «huỷ» (huỷ job đang chạy), «id» (mã Zoom của bạn).")


def _plain(s: str) -> str:
    """Bỏ dấu + chữ thường: «Huỷ» / «hủy» / «HUY» như nhau (hai cách bỏ dấu của chữ «huỷ» là hai chuỗi khác nhau)."""
    s = unicodedata.normalize("NFD", (s or "").replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def parse(text: str) -> dict:
    """Tin nhắn -> {urls, mode, duration, long_mode, review, download}."""
    urls: list[str] = []
    for m in _URL_RE.findall(text or ""):
        u = m.rstrip(_TRAIL)
        if u and u not in urls:
            urls.append(u)
    words = set(re.findall(r"\w+", _plain(_URL_RE.sub(" ", text or ""))))
    long_mode = next((m for m in job_factory.LONG_MODES if m in words), "")
    return {"urls": urls[:MAX_LINKS], "more": max(0, len(urls) - MAX_LINKS),
            "mode": Mode.REVIEW.value if "review" in words else Mode.COOK.value,
            "duration": 0 if long_mode else next((int(w) for w in ("30", "60", "90") if w in words), 0),
            "long_mode": long_mode, "review": bool(words & {"duyet", "approve"}),
            "download": bool(words & {"tai", "download"})}


def _duplicate(raw: bytes, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    for k in [k for k, t in _seen.items() if now - t > SEEN_TTL]:
        _seen.pop(k, None)
    key = hashlib.sha256(raw).hexdigest()
    if key in _seen:
        return True
    if len(_seen) >= 1000:
        _seen.pop(next(iter(_seen)))
    _seen[key] = now
    return False


def _mine(queue: JobQueue, user_jid: str) -> list:
    me = (user_jid or "").lower()
    return sorted((j for j in queue.jobs.values()
                   if j.source == "zoom" and (j.zoom or {}).get("user", "").lower() == me),
                  key=lambda j: j.id, reverse=True)


def status_text(queue: JobQueue, user_jid: str) -> str:
    jobs = _mine(queue, user_jid)[:5]
    if not jobs:
        return "Bạn chưa có job nào. Gửi link video để bắt đầu."
    lines = []
    for j in jobs:
        line = f"#{j.id}: {STATUS_VI.get(j.status, j.status)}"
        if j.status == "processing" and j.step:
            line += f" ({j.step})"
        elif j.status == "error" and j.error:
            line += f" — {j.error[:120]}"
        links = [notifier.part_link(j, p) for p in (j.parts or []) if not p.get("path", "").endswith(".jpg")]
        if j.status == "done" and links and links[0]:
            line += "\n   " + "\n   ".join(x for x in links if x)
        lines.append(line)
    return "\n".join(lines)


def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    async def make_jobs(cmd: dict, target: dict) -> str:
        urls = []
        for u in cmd["urls"]:
            try:
                urls.append(await url_guard.check_url_async(u))
            except url_guard.BlockedURL as e:
                return f"⛔ {e}"
        made, pos = 0, 0
        for u in urls:
            if cmd["download"]:
                job = job_factory.download(source="zoom", url=u, zoom=target)
            else:
                job = job_factory.remake(url=u, mode=cmd["mode"], duration=cmd["duration"],
                                         long_mode=cmd["long_mode"], review=cmd["review"],
                                         voice=job_factory.telegram_voice(cmd["mode"]), source="zoom", zoom=target)
            try:
                pos = queue.put(job)
                made += 1
            except QueueFull as e:
                return f"⚠️ {e}" + (f" (đã nhận {made} video)" if made else "")
        if cmd["download"]:
            what = "tải về"
        else:
            what = ("review" if cmd["mode"] == Mode.REVIEW.value else "nấu ăn") + (
                f", {cmd['long_mode']}" if cmd["long_mode"] else f", {cmd['duration']} giây" if cmd["duration"]
                else ", giữ nguyên thời lượng") + (", duyệt kịch bản trên web" if cmd["review"] else "")
        extra = f"\n(Chỉ nhận {MAX_LINKS} link đầu, bỏ {cmd['more']} link còn lại.)" if cmd["more"] else ""
        return f"✅ Đã nhận {made} video ({what}), vị trí cuối trong hàng đợi: #{pos}.{extra}\nXong bot nhắn link tải vào đây."

    def cancel_mine(user_jid: str) -> str:
        fn = hooks.get("cancel")
        mine = [j for j in _mine(queue, user_jid) if j.status in ("queued", "processing")]
        if not mine:
            return "Không có job nào của bạn đang chờ / đang chạy."
        if not fn:
            return "Bot chưa hỗ trợ huỷ ở đây, huỷ trên web panel."
        return "\n".join(fn(j.id) for j in mine)

    async def handle(p: dict) -> None:
        user_jid, user_id = str(p.get("userJid") or ""), str(p.get("userId") or "")
        to_jid, account = str(p.get("toJid") or user_jid), str(p.get("accountId") or "")
        text = str(p.get("cmd") or "").strip()
        target = {"to": to_jid, "account": account, "user": user_jid}
        if not zoom.is_allowed(user_jid, user_id):
            logging.warning("Từ chối người dùng Zoom không có trong ZOOM_ALLOWED_USERS: %s", user_jid or user_id)
            await zoom.notify(target, f"🔒 Bot này chỉ phục vụ người được phép.\nJID Zoom của bạn: {user_jid}\n"
                                      "Chủ bot thêm JID này vào Cài đặt → 🎥 Zoom → «Người được dùng bot Zoom».")
            return
        zoom.remember(user_jid, to_jid, account)        # để báo job xong / phiên bản mới về đúng nơi này
        cmd = parse(text)
        # lệnh khớp theo CẢ chữ đầu tiên, không theo tiền tố: tin «Huỳnh ơi...» không được thành lệnh «huỷ»
        first = (_plain(text).split() or [""])[0]
        if cmd["urls"]:
            reply = await make_jobs(cmd, target)
        elif first == "id":
            reply = f"JID Zoom của bạn: {user_jid}\nUser ID: {user_id}"
        elif first in ("trang", "status", "tt", "job", "jobs"):
            reply = status_text(queue, user_jid)
        elif first in ("huy", "cancel", "stop"):
            reply = cancel_mine(user_jid)
        else:
            reply = HELP
        await zoom.notify(target, reply)

    async def events(request: web.Request) -> web.Response:
        if not zoom.inbound_ready():
            raise web.HTTPServiceUnavailable(text="Zoom chưa cài: điền Secret Token ở Cài đặt → 🔑 Tài khoản & API")
        if (request.content_length or 0) > ZOOM_BODY_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=ZOOM_BODY_MAX, actual_size=request.content_length or 0)
        raw = await request.read()
        if len(raw) > ZOOM_BODY_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=ZOOM_BODY_MAX, actual_size=len(raw))
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise web.HTTPBadRequest(text="JSON lỗi") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="JSON lỗi")
        payload: dict = data["payload"] if isinstance(data.get("payload"), dict) else {}
        ts, sig = request.headers.get("x-zm-request-timestamp", ""), request.headers.get("x-zm-signature", "")
        unsigned_check = data.get("event") == "endpoint.url_validation" and not (ts or sig)
        if not unsigned_check and not zoom.signature_ok(raw, ts, sig):
            logging.warning("Zoom: chữ ký webhook sai hoặc quá cũ, bỏ qua")
            raise web.HTTPUnauthorized(text="Chữ ký sai")
        if data.get("event") == "endpoint.url_validation":
            # plainToken chỉ là chuỗi chữ-số (zoom.url_validation): trả HMAC của nó không giúp giả được chữ ký "v0:..."
            try:
                return web.json_response(zoom.url_validation(str(payload.get("plainToken", ""))))
            except ValueError:
                raise web.HTTPBadRequest(text="plainToken không hợp lệ") from None
        if _duplicate(raw):
            return web.json_response({"ok": True, "duplicate": True})
        if data.get("event") == "bot_notification":
            spawn(handle(payload))
        else:
            logging.info("Zoom: sự kiện %s (bỏ qua)", str(data.get("event"))[:60])
        return web.json_response({"ok": True})

    async def notify_test(request: web.Request) -> web.Response:
        _check(request)
        res = await notifier.test()
        res["zoom_endpoint"] = notifier.web_link("/zoom/events")
        res["zoom_send_ready"] = zoom.configured()
        res["zoom_inbound_ready"] = zoom.inbound_ready()
        return web.json_response(res)

    app.router.add_post("/zoom/events", events)
    app.router.add_post("/api/notify/test", notify_test)
