"""(Botkeep) Đăng YouTube Shorts (1.2.0): kênh bếp («cook») và kênh truyện bé («kids») là HAI kênh YouTube riêng, giống
hai Fanpage.

Kết nối (một lần mỗi kênh): OAuth 2.0 kiểu web. Một dự án Google Cloud (bật YouTube Data API v3, OAuth client «Web
application», địa chỉ chuyển hướng PUBLIC_URL + /youtube/callback) dùng chung cho cả hai kênh: YT_CLIENT_ID /
YT_CLIENT_SECRET. Nút «▶️ Kết nối YouTube» (mục 🤖 Tự động đăng / 🧸 Truyện bé) mở trang Google chọn tài khoản + kênh ->
/youtube/callback đổi mã lấy refresh token, lưu YT_REFRESH_TOKEN (bếp) / KIDS_YT_REFRESH_TOKEN (bé). Một tài khoản Google
quản lý được tới 100 kênh (kênh thương hiệu): Google hỏi chọn kênh lúc đăng nhập.

Đăng: autopost / kids_auto hẹn xong bài Fanpage thì gọi after_facebook(); run_forever (mỗi 2 phút) tải video lên bằng
giao thức resumable của YouTube Data API v3 (videos.insert: từ 1/6/2026 có hạn mức riêng 100 lần / ngày / dự án; từ
2/11/2026 dự án chưa qua kiểm duyệt không còn bị ép video riêng tư). Video dọc ≤ 3 phút YouTube tự xếp vào Shorts. Giờ
hẹn Facebook còn ở tương lai: tải lên riêng tư + publishAt (YouTube tự công khai đúng giờ); sắp tới / đã qua thì công khai
ngay. Kênh bé khai «dành cho trẻ em» (selfDeclaredMadeForKids — bắt buộc với nội dung cho trẻ theo chính sách YouTube).
Tiêu đề / mô tả: phần «▶ YouTube Shorts» của gói nội dung (prompt_engine.platform_post).

Lỗi tách khỏi Facebook: mạng / lỗi tạm thử lại sau 5, 10, 20, 40 phút (tối đa MAX_TRIES lần); hết hạn mức / giới hạn tải
lên của kênh: LATER giây sau; token bị thu hồi / thiếu quyền: dừng kênh đó, báo admin kết nối lại (kết nối lại là tự chạy
tiếp). Địa chỉ phiên tải lưu TRƯỚC khi gửi file: mạng rớt / bot dừng giữa chừng thì lượt sau hỏi phiên — YouTube đã
nhận đủ thì lấy id (không tải lại thành video trùng), còn dở thì tải tiếp. Trạng thái: youtube.json (YT_STATE)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

import aiohttp

import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
CALLBACK = "/youtube/callback"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = os.environ.get("YT_STATE") or os.path.join(_ROOT, "youtube.json")
VN = timezone(timedelta(hours=7))
# 26 = Howto & Style (nấu ăn), 1 = Film & Animation (truyện hoạt hình)
CHANNELS: dict[str, dict[str, Any]] = {
    "cook": {"key": "YT_REFRESH_TOKEN", "vi": "kênh bếp", "category": "26", "kids": False},
    "kids": {"key": "KIDS_YT_REFRESH_TOKEN", "vi": "kênh truyện bé", "category": "1", "kids": True},
}
TITLE_MAX = 100                # YouTube: tiêu đề ≤ 100 ký tự, không có < >
DESC_MAX_BYTES = 4900          # YouTube: mô tả ≤ 5000 byte (tiếng Việt 2-3 byte / chữ có dấu)
MIN_LEAD = 15 * 60             # giờ hẹn gần hơn chừng này (tải lên mất vài phút): công khai ngay thay vì publishAt
MAX_TRIES = 5
LATER = 6 * 3600               # hết hạn mức / giới hạn tải lên của kênh: thử lại sau 6 giờ
CONNECT_TTL = 15 * 60          # phiên «Kết nối YouTube» (state OAuth) hết hạn sau 15 phút, dùng một lần
UPLOAD_TIMEOUT = 1800
SESSION_TTL = 6 * 86400        # địa chỉ phiên tải resumable của YouTube sống ~1 tuần: quá 6 ngày thì mở phiên mới
VIEWS_AFTER, VIEWS_RETRY, VIEWS_GIVEUP = 24 * 3600, 6 * 3600, 7 * 86400
KEEP_ITEMS = 300
PENDING_MAX_AGE = 3 * 86400    # chờ quá 3 ngày (kênh dừng mà chưa kết nối lại): bỏ, để storage được dọn file
# 401 / authError KHÔNG nằm đây: access token hết hạn giữa lúc tải (PUT tới 30 phút) cũng trả 401 -> bỏ token trong bộ
# nhớ, thử lại như lỗi thường; refresh token bị thu hồi thì máy chủ OAuth trả invalid_grant -> dừng kênh.
FATAL = {"invalid_grant", "invalid_client", "unauthorized_client", "forbidden", "insufficientPermissions",
         "youtubeSignupRequired", "accessNotConfigured", "SERVICE_DISABLED"}
LATER_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}      # hạn mức ngày: 6 giờ sau
SOON_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "RESOURCE_EXHAUSTED"}  # theo phút: 10, 20, 40… phút
_LOCK = asyncio.Lock()
_ACCESS: dict[str, tuple[str, float, str]] = {}      # kênh -> (access token, hết hạn, dấu refresh token)
_PENDING: dict[str, tuple[str, float]] = {}          # state OAuth -> (kênh, hết hạn)


class YTError(RuntimeError):
    """fatal: phải kết nối lại / sửa cài đặt (thử lại vô ích); later: hết hạn mức ngày, thử lại sau LATER giây; soon:
    giới hạn theo phút, thử lại sớm (không tính là lần lỗi)."""

    def __init__(self, msg: str, reason: str = "", status: int = 0, fatal: bool = False, later: bool = False,
                 soon: bool = False):
        super().__init__(msg)
        self.reason, self.status, self.fatal, self.later, self.soon = reason, status, fatal, later, soon


# ---------------- cài đặt / trạng thái ----------------

def _fp(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12] if s else ""


def client() -> tuple[str, str]:
    return (config.YT_CLIENT_ID or "").strip(), (config.YT_CLIENT_SECRET or "").strip()


def refresh_token(ch: str) -> str:
    return str(getattr(config, CHANNELS[ch]["key"], "") or "").strip()


def key_fp(ch: str) -> str:
    """Dấu của bộ khoá đang dùng cho kênh (refresh token + client): đổi bất kỳ khoá nào (kết nối lại, sửa secret) thì
    kênh đang dừng tự chạy lại."""
    cid, sec = client()
    rt = refresh_token(ch)
    return _fp(f"{rt}|{cid}|{sec}") if rt else ""


def missing(ch: str) -> str:
    """Lý do chưa đăng được lên YouTube cho kênh `ch` ("" = sẵn sàng)."""
    cid, sec = client()
    if not refresh_token(ch):
        return f"chưa kết nối YouTube cho {CHANNELS[ch]['vi']}"
    if not (cid and sec):
        return "thiếu YT_CLIENT_ID / YT_CLIENT_SECRET (Cài đặt → 🔑 Tài khoản & API)"
    return ""


def load_state(path: str | None = None) -> dict:
    """{"items": [video chờ / đã đăng], "paused": {kênh: {why, fp, at}}, "channels": {kênh: {id, title, at}}}."""
    path = path or STATE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("không phải object JSON")
    except FileNotFoundError:
        st = {}
    except (OSError, ValueError) as e:
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        logging.error("youtube.json hỏng (%s), đã đổi tên thành .bad", e)
        st = {}
    for k in ("items",):
        st.setdefault(k, [])
    for k in ("paused", "channels"):
        st.setdefault(k, {})
    return st


def save_state(st: dict, path: str | None = None) -> None:
    path = path or STATE_FILE
    st["items"] = st["items"][-KEEP_ITEMS:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _patch(key: str, **fields: Any) -> dict:
    """Đọc lại file, sửa một mục theo khoá, ghi ngay (không await ở giữa: autopost / kids_auto thêm mục trong lúc đang tải
    lên thì không bị ghi đè mất)."""
    st = load_state()
    for it in st["items"]:
        if it.get("key") == key:
            it.update(fields)
    save_state(st)
    return st


# ---------------- chữ đăng ----------------

def _safe(s: str) -> str:
    return (s or "").replace("<", "‹").replace(">", "›")


def clean_title(t: str) -> str:
    """≤ TITLE_MAX ký tự, không < >; quá dài thì cắt ở ranh giới từ, giữ «#Shorts» ở cuối."""
    t = " ".join(_safe(t).split())
    if len(t) <= TITLE_MAX:
        return t or "Video mới"
    tag = " #Shorts" if re.search(r"#shorts\b", t, re.I) else ""
    base = re.sub(r"\s*#shorts\b", "", t, flags=re.I).strip()
    room = TITLE_MAX - len(tag)
    if len(base) > room:
        base = base[:room - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"
    return base + tag


def clean_desc(d: str) -> str:
    b = _safe(d).strip().encode()
    return b[:DESC_MAX_BYTES].decode("utf-8", "ignore").rstrip() if len(b) > DESC_MAX_BYTES else b.decode()


def texts(post: str, title: str = "", desc: str = "") -> tuple[str, str]:
    """(tiêu đề, mô tả) cho YouTube: phần «▶ YouTube Shorts» của gói nội dung (dòng đầu «<tiêu đề> #Shorts», sau đó
    caption + công thức + hashtag + dòng ghi nguồn). Không có phần đó: dùng chữ đăng Fanpage (title, desc)."""
    post = (post or "").strip()
    sec = ""
    if "▶ YouTube Shorts:\n" in post:
        sec = post.split("▶ YouTube Shorts:\n", 1)[1].split("\n\n▶ ", 1)[0].strip()
    if sec:
        head, _, body = sec.partition("\n")
        return clean_title(head), clean_desc(body)
    t = title or (post.split("\n", 1)[0].replace("📌", "").strip() if post else "")
    if "#shorts" not in (t + " " + desc).lower():
        t = f"{t} #Shorts" if t else t
    return clean_title(t), clean_desc(desc or post)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scheduled(when: float | None, now: float) -> bool:
    return bool(when) and float(when or 0) >= now + MIN_LEAD


def video_body(ch: str, title: str, desc: str, when: float | None, now: float) -> dict:
    """Thân videos.insert (snippet + status). Hẹn giờ: riêng tư + publishAt (YouTube chỉ nhận publishAt với video riêng
    tư); không thì công khai ngay."""
    c = CHANNELS[ch]
    status: dict[str, Any] = {"privacyStatus": "public", "selfDeclaredMadeForKids": bool(c["kids"]), "embeddable": True}
    if scheduled(when, now):
        status.update(privacyStatus="private", publishAt=_iso(float(when or 0)))
    return {"snippet": {"title": clean_title(title), "description": clean_desc(desc), "categoryId": c["category"],
                        "defaultLanguage": "vi", "defaultAudioLanguage": "vi"},
            "status": status}


# ---------------- HTTP ----------------

def _error(status: int, d: Any) -> YTError:
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, str):                               # máy chủ OAuth: {"error": "invalid_grant", "error_description": …}
        reason, msg = e, str(d.get("error_description") or e)
    elif isinstance(e, dict):                            # YouTube API: {"error": {"code", "message", "errors": [{reason}]}}
        errs = e.get("errors") or [{}]
        reason = str((errs[0] or {}).get("reason") or e.get("status") or "")
        msg = str(e.get("message") or reason)
    else:
        reason, msg = "", f"HTTP {status}"
    later = reason in LATER_REASONS
    soon = not later and (reason in SOON_REASONS or status == 429)
    fatal = not (later or soon) and reason in FATAL
    return YTError(f"YouTube: {msg[:200]} ({reason or status})", reason, status, fatal, later, soon)


async def _json(resp: aiohttp.ClientResponse) -> dict:
    try:
        d = await resp.json(content_type=None)
    except Exception:
        d = {}
    if resp.status >= 400:
        raise _error(resp.status, d)
    return d if isinstance(d, dict) else {}


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300, sock_connect=30))


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def access_token(ch: str, session: aiohttp.ClientSession, margin: float = 60) -> str:
    """Access token (1 giờ) từ refresh token của kênh, giữ trong bộ nhớ tới khi còn dưới `margin` giây."""
    rt = refresh_token(ch)
    hit = _ACCESS.get(ch)
    if hit and hit[2] == key_fp(ch) and hit[1] > time.time() + margin:
        return hit[0]
    why = missing(ch)
    if why:
        raise YTError(why, fatal=True)
    cid, sec = client()
    async with session.post(TOKEN_URL, data={"client_id": cid, "client_secret": sec, "refresh_token": rt,
                                             "grant_type": "refresh_token"}) as r:
        d = await _json(r)
    tok = str(d.get("access_token") or "")
    if not tok:
        raise YTError("YouTube: máy chủ Google không trả access token")
    _ACCESS[ch] = (tok, time.time() + float(d.get("expires_in") or 3600), key_fp(ch))
    return tok


async def channel_info(token: str, session: aiohttp.ClientSession) -> tuple[str, str]:
    """(id, tên) kênh YouTube của token."""
    async with session.get(f"{API}/channels", params={"part": "snippet", "mine": "true"}, headers=_auth(token)) as r:
        d = await _json(r)
    items = d.get("items") or []
    if not items:
        raise YTError("Tài khoản Google này chưa có kênh YouTube: vào youtube.com tạo kênh trước rồi kết nối lại", fatal=True)
    return str(items[0].get("id") or ""), str((items[0].get("snippet") or {}).get("title") or "")


async def _session_state(s: aiohttp.ClientSession, loc: str, tok: str, size: int) -> tuple[str, Any]:
    """Hỏi phiên tải resumable đã nhận tới đâu (PUT rỗng, Content-Range: bytes */size): ("done", video) khi YouTube đã
    nhận đủ và tạo video; ("offset", số byte đã nhận) khi còn dở; ("gone", None) khi phiên hết hạn / không còn."""
    async with s.put(loc, data=b"", headers={**_auth(tok), "Content-Range": f"bytes */{size}"},
                     allow_redirects=False) as r:
        if r.status == 308:                          # còn dở: Range «bytes=0-N» là phần đã nhận (không có = chưa nhận gì)
            m = re.match(r"bytes=0-(\d+)$", r.headers.get("Range", ""))
            return "offset", int(m.group(1)) + 1 if m else 0
        if r.status in (404, 410):
            return "gone", None
        return "done", await _json(r)


async def upload(ch: str, path: str, title: str, desc: str, when: float | None,
                 session: aiohttp.ClientSession | None = None, now: float | None = None,
                 info: dict | None = None, resume_url: str = "",
                 on_session: Callable[[str], Any] | None = None) -> str:
    """Tải video lên kênh `ch` (resumable: POST metadata -> địa chỉ phiên tải -> PUT file). Trả id video YouTube; info:
    nhận phần status YouTube trả về (privacyStatus, uploadStatus…) để soát. resume_url: phiên của lượt trước — hỏi YouTube
    đã nhận tới đâu: đã đủ (mạng rớt đúng lúc YouTube trả kết quả, bot dừng ngay sau khi tải xong) thì lấy luôn id, KHÔNG tải
    lại thành video trùng; còn dở thì tải tiếp từ chỗ dở. on_session(url): lưu phiên mới TRƯỚC khi gửi file."""
    now = time.time() if now is None else now
    size = os.path.getsize(path)
    own = session is None
    s = session or _session()
    try:
        tok = await access_token(ch, s, margin=UPLOAD_TIMEOUT)      # không để token hết hạn giữa lúc tải
        loc, offset, d = resume_url, 0, None
        if loc:
            state, val = await _session_state(s, loc, tok, size)
            if state == "done":
                d = val
            elif state == "offset":
                offset = int(val)
            else:
                loc = ""
        if d is None and not loc:
            async with s.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                              json=video_body(ch, title, desc, when, now),
                              headers={**_auth(tok), "X-Upload-Content-Type": "video/mp4",
                                       "X-Upload-Content-Length": str(size)}) as r:
                await _json(r)
                loc = r.headers.get("Location", "")
            if not loc:
                raise YTError("YouTube không trả địa chỉ tải lên")
            if on_session is not None:
                on_session(loc)
        if d is None:
            hdr = {**_auth(tok), "Content-Type": "video/mp4"}
            if offset:
                hdr["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
            with open(path, "rb") as f:
                f.seek(min(offset, size))
                async with s.put(loc, data=f, headers=hdr, allow_redirects=False,
                                 timeout=aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT)) as r:
                    if r.status == 308:
                        raise YTError("YouTube chưa nhận đủ file (308), thử lại tải tiếp")
                    d = await _json(r)
        vid = str(d.get("id") or "")
        if not vid:
            raise YTError("YouTube không trả id video")
        if info is not None:
            info.update(d.get("status") or {})
        return vid
    finally:
        if own:
            await s.close()


async def views(ch: str, vid: str, session: aiohttp.ClientSession) -> int | None:
    tok = await access_token(ch, session)
    async with session.get(f"{API}/videos", params={"part": "statistics", "id": vid}, headers=_auth(tok)) as r:
        d = await _json(r)
    items = d.get("items") or []
    v = str(((items[0].get("statistics") or {}) if items else {}).get("viewCount") or "")
    return int(v) if v.isdigit() else None


# ---------------- kết nối ----------------

def redirect_uri() -> str:
    base = (config.PUBLIC_URL or "").strip().rstrip("/")
    if not (base.startswith("https://") or re.match(r"http://(localhost|127\.0\.0\.1)(:\d+)?$", base)):
        raise YTError("Cần đặt PUBLIC_URL (link web https của bot, Cài đặt → 🔑 Tài khoản & API) trước khi kết nối YouTube")
    return base + CALLBACK


def start(ch: str, now: float | None = None) -> str:
    """Link trang đồng ý của Google cho kênh `ch` (state ngẫu nhiên, dùng một lần trong CONNECT_TTL)."""
    if ch not in CHANNELS:
        raise YTError("kênh không hợp lệ")
    cid, sec = client()
    if not (cid and sec):
        raise YTError("Điền YT_CLIENT_ID và YT_CLIENT_SECRET (Cài đặt → 🔑 Tài khoản & API) trước khi kết nối YouTube")
    uri = redirect_uri()
    now = time.time() if now is None else now
    for k, (_c, exp) in list(_PENDING.items()):
        if exp < now:
            del _PENDING[k]
    while len(_PENDING) >= 20:
        _PENDING.pop(next(iter(_PENDING)))
    state = secrets.token_urlsafe(24)
    _PENDING[state] = (ch, now + CONNECT_TTL)
    q = {"client_id": cid, "redirect_uri": uri, "response_type": "code", "scope": " ".join(SCOPES),
         "access_type": "offline", "prompt": "select_account consent", "include_granted_scopes": "true", "state": state}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def _other(ch: str) -> str:
    return next(c for c in CHANNELS if c != ch)


async def finish(state: str, code: str, session: aiohttp.ClientSession | None = None,
                 now: float | None = None) -> tuple[str, str, str]:
    """Google chuyển về /youtube/callback: đổi mã lấy refresh token, lưu vào cài đặt. Trả (kênh, tên kênh YouTube,
    cảnh báo). Không cần cookie phiên (cookie SameSite=Strict không đi kèm lượt chuyển hướng từ Google): state ngẫu nhiên
    do người đã đăng nhập tạo, dùng một lần."""
    now = time.time() if now is None else now
    hit = _PENDING.pop(state or "", None)
    if not hit or hit[1] < now:
        raise YTError("Phiên kết nối đã hết hạn hoặc không hợp lệ: bấm lại «▶️ Kết nối YouTube» trên web")
    ch = hit[0]
    if not code:
        raise YTError("Google không trả mã xác nhận")
    cid, sec = client()
    own = session is None
    s = session or _session()
    try:
        async with s.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": sec,
                                           "redirect_uri": redirect_uri(), "grant_type": "authorization_code"}) as r:
            d = await _json(r)
        rt, at = str(d.get("refresh_token") or ""), str(d.get("access_token") or "")
        granted = set(str(d.get("scope") or "").split())
        if granted and SCOPES[0] not in granted:
            raise YTError("Chưa cấp quyền tải video lên YouTube: kết nối lại và tick ô quyền «quản lý video YouTube»")
        if not rt:
            raise YTError("Google không trả refresh token: vào myaccount.google.com/permissions gỡ quyền của ứng dụng rồi "
                          "kết nối lại")
        yt_id, title = await channel_info(at, s)
    finally:
        if own:
            await s.close()
    config.save_settings({CHANNELS[ch]["key"]: rt})
    _ACCESS[ch] = (at, now + float(d.get("expires_in") or 3600), key_fp(ch))
    st = load_state()
    st["channels"][ch] = {"id": yt_id, "title": title, "at": now}
    st["paused"].pop(ch, None)
    save_state(st)
    o = _other(ch)
    warn = ""
    if (st["channels"].get(o) or {}).get("id") == yt_id and refresh_token(o):
        warn = f"⚠️ Kênh này đang dùng cho cả {CHANNELS[o]['vi']}: hai kênh nên là hai kênh YouTube khác nhau."
    logging.info("YouTube: đã kết nối %s với kênh «%s» (%s)", CHANNELS[ch]["vi"], title, yt_id)
    return ch, title, warn


async def check(ch: str, session: aiohttp.ClientSession | None = None) -> str:
    """Tên kênh YouTube nếu kết nối dùng được; không thì ném YTError."""
    why = missing(ch)
    if why:
        raise YTError(why)
    own = session is None
    s = session or _session()
    try:
        tok = await access_token(ch, s)
        yt_id, title = await channel_info(tok, s)
    finally:
        if own:
            await s.close()
    st = load_state()
    st["channels"][ch] = {**(st["channels"].get(ch) or {}), "id": yt_id, "title": title}
    st["paused"].pop(ch, None)                      # «Thử YouTube» được: chạy tiếp (vd vừa bật API trên Google Cloud)
    save_state(st)
    return title


async def disconnect(ch: str, session: aiohttp.ClientSession | None = None) -> None:
    """Xoá refresh token của kênh (và thu hồi phía Google, lỗi thì thôi). Video đang chờ giữ lại tới khi kết nối lại."""
    key = CHANNELS[ch]["key"]
    if str(getattr(config, "_ENV", {}).get(key) or "").strip():
        raise YTError(f"{key} đang đặt bằng biến môi trường trên Botkeep: xoá biến đó trên Botkeep rồi khởi động lại bot "
                      "(nút Ngắt chỉ gỡ được token lưu từ web)")
    rt = refresh_token(ch)
    config.save_settings({key: "-"})
    _ACCESS.pop(ch, None)
    st = load_state()
    st["channels"].pop(ch, None)
    st["paused"].pop(ch, None)
    save_state(st)
    if not rt:
        return
    own = session is None
    s = session or _session()
    try:
        async with s.post(REVOKE_URL, data={"token": rt}, timeout=aiohttp.ClientTimeout(total=20)) as r:
            await r.read()
    except Exception as e:
        logging.info("YouTube: thu hồi token lỗi (%s), bỏ qua", str(e)[:80])
    finally:
        if own:
            await s.close()


# ---------------- hàng chờ ----------------

def enqueue(ch: str, job: int, path: str, title: str, desc: str, when: float | None, now: float | None = None) -> bool:
    """Thêm video vào hàng chờ tải lên kênh `ch` (một lần mỗi job). False: kênh chưa kết nối hoặc đã có."""
    if ch not in CHANNELS or missing(ch):
        return False
    st = load_state()
    key = f"{ch}:{job}"
    if any(it.get("key") == key for it in st["items"]):
        return False
    st["items"].append({"key": key, "ch": ch, "job": job, "path": path, "title": clean_title(title),
                        "desc": clean_desc(desc), "when": when, "status": "pending", "tries": 0, "next_try": 0,
                        "created": time.time() if now is None else now})
    save_state(st)
    return True


def after_facebook(ch: str, job: Any, path: str, title: str, desc: str, when: float | None) -> bool:
    """Gọi sau khi Fanpage đã nhận bài: xếp video lên YouTube cùng giờ hẹn. Lỗi gì cũng không làm hỏng bài Facebook."""
    try:
        t, d = texts(str(getattr(job, "post", "") or ""), title, desc)
        ok = enqueue(ch, int(job.id), path, t, d, when)
        if ok:
            logging.info("YouTube: xếp video #%s lên %s", job.id, CHANNELS[ch]["vi"])
        return ok
    except Exception as e:
        logging.warning("YouTube: không xếp được video #%s (%s)", getattr(job, "id", "?"), str(e)[:150])
        return False


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts, VN).strftime("%H:%M %d/%m")


async def tick(now: float | None = None, session: aiohttp.ClientSession | None = None) -> str:
    if _LOCK.locked():
        return ""
    async with _LOCK:
        return await _tick(time.time() if now is None else now, session)


async def _tick(now: float, session: aiohttp.ClientSession | None) -> str:
    st = load_state()
    for it in [i for i in st["items"] if i.get("status") == "uploading"]:
        # có _LOCK nên không lượt nào đang tải: «uploading» còn lại là bot bị dừng giữa chừng. Video YouTube chỉ được tạo
        # khi phiên tải nhận đủ file, và phiên được lưu TRƯỚC khi gửi file -> lượt sau hỏi phiên: đã đủ thì lấy id, dở
        # thì tải tiếp; chưa có phiên thì chưa gửi byte nào, tải lại từ đầu không trùng.
        _patch(it["key"], status="pending", why="bot dừng giữa lúc tải lên, tải tiếp", next_try=0)
    st = load_state()
    if any(ch not in CHANNELS or p.get("fp") != key_fp(ch) for ch, p in st["paused"].items()):
        st["paused"] = {ch: p for ch, p in st["paused"].items() if ch in CHANNELS and p.get("fp") == key_fp(ch)}
        save_state(st)                              # khoá đã đổi (kết nối lại / dán tay / sửa secret): chạy tiếp
    old = [i for i in st["items"] if i.get("status") == "pending" and float(i.get("created") or now) < now - PENDING_MAX_AGE]
    for i in old:
        _patch(i["key"], status="failed", why=f"chờ quá {PENDING_MAX_AGE // 86400} ngày chưa tải lên được, bỏ")
    if old:
        st = load_state()
    it = next((i for i in st["items"] if i.get("status") == "pending" and float(i.get("next_try") or 0) <= now
               and i.get("ch") in CHANNELS and i["ch"] not in st["paused"] and not missing(i["ch"])), None)
    due = [i for i in st["items"] if i.get("status") == "done" and i.get("yt_id") and i.get("views") is None
           and i.get("ch") in CHANNELS and i["ch"] not in st["paused"] and not missing(i["ch"])
           and float(i.get("publish") or 0) + VIEWS_AFTER <= now < float(i.get("publish") or 0) + VIEWS_GIVEUP
           and float(i.get("views_next") or 0) <= now]
    if it is None and not due:
        return ""
    own = session is None
    s = session or _session()
    try:
        msg = await _upload_one(it, now, s) if it else ""
        if due:
            await _update_views(due[0], now, s)
        return msg
    finally:
        if own:
            await s.close()


async def _upload_one(it: dict, now: float, s: aiohttp.ClientSession) -> str:
    from state import notify_admin
    ch, key, vi = it["ch"], it["key"], CHANNELS[it["ch"]]["vi"]
    if not os.path.isfile(str(it.get("path") or "")):
        _patch(key, status="failed", why="file video không còn trên máy")
        await notify_admin(f"⚠️ YouTube ({vi}): video #{it['job']} không còn file trên máy, bỏ qua.")
        return f"{vi}: video #{it['job']} không còn file"
    _patch(key, status="uploading", started=now)
    fp = key_fp(ch)                                 # trước khi await: kết nối lại giữa lúc tải không bị dừng nhầm
    info: dict = {}
    try:
        fresh = float(it.get("session_at") or 0) > now - SESSION_TTL
        vid = await upload(ch, it["path"], it["title"], it.get("desc") or "", it.get("when"), s, now, info,
                           resume_url=str(it.get("session_url") or "") if fresh else "",
                           on_session=lambda url: _patch(key, session_url=url, session_at=now))
    except Exception as e:
        t = max(now, time.time())                   # lùi giờ thử lại tính từ lúc lỗi (một lượt tải có thể mất 30 phút)
        why = (str(e) or type(e).__name__)[:200]
        err = e if isinstance(e, YTError) else None
        if err is not None and err.status == 401:
            _ACCESS.pop(ch, None)                   # access token hết hạn / bị thu hồi: lượt sau xin token mới
        if err is not None and err.fatal:
            _patch(key, status="pending", why=why)
            st = load_state()
            st["paused"][ch] = {"why": why, "fp": fp, "at": t}
            save_state(st)
            await notify_admin(f"⛔ YouTube ({vi}) đã dừng: {why}. Bấm «▶️ Kết nối YouTube» trên web để kết nối lại, hoặc "
                               f"sửa xong cài đặt thì bấm «Thử YouTube» (video #{it['job']} vẫn giữ để đăng).")
            return f"{vi}: dừng ({why})"
        if err is not None and err.later:
            _patch(key, status="pending", why=why, next_try=t + LATER)
            return f"{vi}: video #{it['job']} hết hạn mức, thử lại lúc {_at(t + LATER)}"
        if err is not None and err.soon:
            n = int(it.get("soon") or 0) + 1
            nxt = t + min(LATER, 600 * 2 ** (n - 1))
            _patch(key, status="pending", why=why, soon=n, next_try=nxt)
            return f"{vi}: video #{it['job']} bị giới hạn tốc độ, thử lại lúc {_at(nxt)}"
        tries = int(it.get("tries") or 0) + 1
        if tries >= MAX_TRIES:
            _patch(key, status="failed", why=why, tries=tries)
            await notify_admin(f"⚠️ YouTube ({vi}): tải video #{it['job']} lỗi {tries} lần, bỏ qua: {why}")
            return f"{vi}: video #{it['job']} bỏ sau {tries} lần lỗi"
        nxt = t + min(3600, 300 * 2 ** (tries - 1))
        _patch(key, status="pending", why=why, tries=tries, next_try=nxt)
        return f"{vi}: video #{it['job']} lỗi ({why}), thử lại lúc {_at(nxt)}"
    sched = scheduled(it.get("when"), now)
    publish = float(it["when"]) if sched else max(now, time.time())
    _patch(key, status="done", yt_id=vid, at=now, publish=publish, why="", desc="", yt_status=info, session_url="")
    want = "private" if sched else "public"
    warn = ""
    if info.get("uploadStatus") in ("rejected", "failed"):
        warn = (f"\n⚠️ YouTube báo video {info.get('uploadStatus')} "
                f"({info.get('rejectionReason') or info.get('failureReason') or '?'}): mở YouTube Studio kiểm tra.")
    elif info.get("privacyStatus") and info["privacyStatus"] != want:
        warn = (f"\n⚠️ YouTube để video ở chế độ «{info['privacyStatus']}» (bot yêu cầu «{want}»): mở YouTube Studio "
                "kiểm tra (dự án Google Cloud có thể đang bị giới hạn).")
    at = _at(publish)
    await notify_admin(f"▶️ Đã {'hẹn đăng' if sched else 'đăng'} video #{it['job']} lên YouTube ({vi}) lúc {at}: "
                       f"{it['title'][:80]}\nhttps://youtube.com/shorts/{vid}{warn}")
    return f"{vi}: {'hẹn' if sched else 'đăng'} {vid} lúc {at}" + (" (cần kiểm tra)" if warn else "")


async def _update_views(it: dict, now: float, s: aiohttp.ClientSession) -> None:
    try:
        v = await views(it["ch"], it["yt_id"], s)
    except Exception as e:
        if isinstance(e, YTError) and e.status == 401:
            _ACCESS.pop(it["ch"], None)
        logging.info("YouTube: đọc lượt xem %s lỗi (%s)", it.get("yt_id"), str(e)[:100])
        v = None
    _patch(it["key"], **({"views": v} if v is not None else {"views_next": now + VIEWS_RETRY}))


# ---------------- hiển thị / vòng chạy ----------------

def status_lines(ch: str, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    why = missing(ch)
    if why:
        return [f"▶️ YouTube: {why} (nút «▶️ Kết nối YouTube»)"]
    st = load_state()
    info = st["channels"].get(ch) or {}
    items = [i for i in st["items"] if i.get("ch") == ch]
    done = [i for i in items if i.get("status") == "done"]
    wait = [i for i in items if i.get("status") in ("pending", "uploading")]
    bad = [i for i in items if i.get("status") in ("failed", "unknown")]
    head = f"▶️ YouTube: kênh «{info.get('title') or '?'}» | đã đăng {len(done)}, chờ {len(wait)}" + \
           (f", lỗi {len(bad)}" if bad else "")
    p = st["paused"].get(ch)
    if p:
        head += f" | DỪNG: {p.get('why', '')} — bấm «▶️ Kết nối YouTube» lại"
    lines = [head]
    fut = [i for i in done if float(i.get("publish") or 0) > now]
    if fut:
        lines.append("YouTube đã hẹn: " + ", ".join(_at(float(i["publish"])) for i in fut[-6:]))
    seen = [i for i in done if i.get("views") is not None][-5:]
    if seen:
        lines.append("Lượt xem YouTube sau 24h: " + ", ".join(f"#{i['job']} {i['views']}" for i in seen))
    if wait and wait[0].get("why"):
        lines.append(f"YouTube lỗi gần nhất (video #{wait[0]['job']}): {wait[0]['why']}")
    return lines


async def run_forever(every: float = 120) -> None:
    await asyncio.sleep(100)
    while True:
        try:
            msg = await tick()
            if msg:
                logging.info("YouTube: %s", msg)
        except Exception as e:
            logging.warning("YouTube lỗi: %s", str(e)[:200])
        await asyncio.sleep(every)


def pending_paths() -> set[str]:
    """File video còn chờ (storage không dọn các file này)."""
    now = time.time()
    return {str(i["path"]) for i in load_state()["items"] if i.get("status") in ("pending", "uploading") and i.get("path")
            and float(i.get("created") or now) >= now - PENDING_MAX_AGE}
