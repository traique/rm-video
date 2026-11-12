"""🤖 Tự động đăng: lấy NGẪU NHIÊN một video (không bao giờ lặp lại) từ kênh Facebook nguồn -> làm lại như job thường
-> đăng lên Fanpage của bạn qua Graph API chính thức, HẸN ĐÚNG GIỜ VÀNG; mặc định 2 video/ngày.

- Danh sách video của kênh: trang Reels + Video của kênh (không cần đăng nhập Facebook lấy được ~10 video mới nhất mỗi
  tab; yt-dlp không liệt kê được kênh Facebook). Video đã dùng ghi vào autopost.json nên không bao giờ dùng lại; kênh
  hết video mới thì chờ kênh đăng thêm (kiểm lại mỗi 6 giờ).
- Làm video: job bình thường (source="auto", không dừng chờ duyệt). Chỉ thêm job khi hàng đợi TRỐNG: video bạn tự gửi
  luôn được làm trước. Máy chậm (~40 phút/video) nên làm TRƯỚC và hẹn giờ đăng phía Facebook (giữ sẵn ~1 ngày).
- Đăng: video <= 90s -> Reels API (start -> rupload -> finish, video_state=SCHEDULED); dài hơn -> /{page}/videos
  (published=false + scheduled_publish_time). Facebook yêu cầu giờ hẹn cách hiện tại > 10 phút.
- Giờ vàng: chưa có số liệu thì dùng 20:30 và 11:30 (giờ Việt Nam: cao điểm tối + trưa của người dùng Facebook VN).
  Mỗi bài sau 24 giờ được đọc lượt xem (video_insights) và cộng vào khung giờ đã đăng; khung nào trung bình cao hơn
  thì được chọn nhiều hơn, thỉnh thoảng thử khung khác (25%) để không bỏ sót giờ tốt hơn. Đặt AUTO_TIMES để cố định.
"""
import asyncio
import json
import logging
import os
import random
import re
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import aiohttp

import config
import url_guard

VN = timezone(timedelta(hours=7))           # Việt Nam không đổi giờ mùa hè
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("AUTOPOST_STATE") or os.path.join(BASE, "autopost.json")
API_VER = os.environ.get("FB_GRAPH_VERSION", "v27.0")
GRAPH = "https://graph.facebook.com"
GRAPH_VIDEO = "https://graph-video.facebook.com"
RUPLOAD = "https://rupload.facebook.com"
REEL_MAX = 90                  # Reels API nhận 3-90 giây
LEAD = 15 * 60                 # hẹn giờ phải > 10 phút sau hiện tại (Graph API); chừa 15 phút
HORIZON = 36 * 3600            # làm sẵn video cho các khung giờ trong 36 giờ tới
LIST_TTL = 6 * 3600            # danh sách video của kênh: đọc lại sau 6 giờ
MIN_GAP_H = 3                  # hai khung giờ trong ngày cách nhau ít nhất 3 giờ
EXPLORE = 0.25                 # xác suất thử một khung giờ khác thay khung yếu nhất
CANDIDATES = ["07:00", "09:30", "11:30", "12:30", "17:30", "19:00", "20:30", "21:30"]
DEFAULT_ORDER = ["20:30", "11:30", "17:30", "21:30", "07:00", "12:30", "19:00", "09:30"]
VIEW_METRICS = ("fb_reels_total_plays", "blue_reels_play_count", "total_video_views", "post_video_views")


class FBError(RuntimeError):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


# ---------------- trạng thái ----------------

def load_state(path: str | None = None) -> dict:
    """File hỏng KHÔNG được lặng lẽ thay bằng trạng thái rỗng (mất danh sách video đã dùng -> đăng lại video cũ,
    mất lịch đã hẹn -> hẹn trùng giờ): đổi tên thành .bad, dừng tự động đăng và báo lý do."""
    path = path or STATE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("nội dung không phải object JSON")
    except FileNotFoundError:
        st = {}
    except (OSError, ValueError) as e:
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        logging.error("autopost.json hỏng (%s), đã đổi tên thành autopost.json.bad", e)
        st = {"paused": f"autopost.json hỏng ({str(e)[:60]}), đã lưu bản cũ thành autopost.json.bad — kiểm tra "
                        "rồi gõ auto_resume (danh sách video đã dùng bắt đầu lại từ đầu)"}
        try:                                    # ghi ngay trạng thái dừng: ai đọc tiếp (vòng chạy, web) cũng thấy
            save_state(dict(st), path)
        except OSError:
            pass
    for k, v in (("used", {}), ("posts", []), ("listing", {}), ("days", {}), ("pending", None), ("paused", ""),
                 ("fails", {}), ("streak", 0)):
        st.setdefault(k, v)
    return st


def save_state(st: dict, path: str | None = None):
    path = path or STATE_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------- cài đặt ----------------

def sources() -> list:
    out = []
    for s in re.split(r"[,\s]+", config.AUTO_SOURCES or ""):
        try:
            n = normalize_source(s)
        except ValueError:
            continue
        if n and n not in out:
            out.append(n)
    return out


def normalize_source(s: str) -> str:
    """'duynauan', '@duynauan', 'https://m.facebook.com/duynauan/reels' -> https://www.facebook.com/duynauan;
    profile.php?id=123 giữ id. Chỉ nhận kênh Facebook."""
    s = (s or "").strip()
    if not s:
        return ""
    if re.match(r"(?i)^(?:[\w-]+\.)*facebook\.com/", s):          # "facebook.com/x", "m.facebook.com/x"
        s = "https://" + s
    elif not s.startswith(("http://", "https://")):
        s = "https://www.facebook.com/" + s.lstrip("@/")
    parts = urllib.parse.urlsplit(s)
    host = (parts.hostname or "").lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com")):
        raise ValueError(f"«{s}» không phải kênh Facebook")
    seg = [p for p in parts.path.split("/") if p]
    if seg and seg[0] == "profile.php":
        pid = urllib.parse.parse_qs(parts.query).get("id", [""])[0]
        if not pid.isdigit():
            raise ValueError(f"«{s}» thiếu id kênh")
        return f"https://www.facebook.com/profile.php?id={pid}"
    if not seg or seg[0] in ("reel", "watch", "share", "videos", "groups", "events", "login"):
        raise ValueError(f"«{s}» là link video/nhóm, không phải link kênh")
    return "https://www.facebook.com/" + seg[0]


def per_day() -> int:
    try:
        return max(1, min(4, int(config.AUTO_PER_DAY)))          # 4 khung cách nhau >= 3 giờ là tối đa hợp lý
    except (TypeError, ValueError):
        return 2


def fixed_times() -> list:
    return sorted({t for t in re.split(r"[,\s]+", config.AUTO_TIMES or "") if re.fullmatch(r"\d{2}:\d{2}", t)})


def enabled() -> str:
    """"" nếu chạy được; không thì lý do."""
    if config.AUTO_POST != "1":
        return "AUTO_POST đang tắt"
    if not (config.FB_PAGE_ID.strip() and config.FB_PAGE_TOKEN.strip()):
        return "thiếu FB_PAGE_ID / FB_PAGE_TOKEN"
    if not sources():
        return "chưa có kênh nguồn (AUTO_SOURCES)"
    return ""


# ---------------- danh sách video của kênh nguồn ----------------

class _AllowedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url_guard.check_url(newurl)            # mỗi bước chuyển hướng vẫn phải là nền tảng được phép, không IP nội bộ
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowedRedirect)
_VIDEO_ID = re.compile(r'"video_id":"(\d{8,})"')
_VIDEOS_PATH = re.compile(r'facebook\.com/[^/"\s?]+/videos/(?:[^/"\s?]+/)?(\d{8,})')
_OWNER = re.compile(r'"userID":"(\d{5,})"')


def _tab(src: str, tab: str) -> str:
    if "profile.php" in src:
        return src + ("&sk=reels_tab" if tab == "reels" else "&sk=videos")
    return f"{src}/{tab}/"


def _fetch(url: str, opener=None) -> str:
    from yt_dlp.utils.networking import std_headers
    url = url_guard.check_url(url)
    req = urllib.request.Request(url, headers=dict(std_headers))     # header chuẩn của yt-dlp, như khi tải video
    with (opener or _OPENER).open(req, timeout=30) as r:
        final = r.geturl()
        body = r.read(5 << 20).decode("utf-8", "replace")
    if "/login" in urllib.parse.urlsplit(final).path:
        raise RuntimeError("Facebook bắt đăng nhập mới xem được kênh này")
    return body


def parse_listing(reels_html: str, videos_html: str) -> tuple:
    """(danh sách id video theo thứ tự xuất hiện, id chủ kênh hoặc "")."""
    ids = []
    for body in (reels_html, videos_html.replace("\\/", "/")):
        for rx in (_VIDEO_ID, _VIDEOS_PATH):
            for m in rx.finditer(body):
                if m.group(1) not in ids:
                    ids.append(m.group(1))
    owner = _OWNER.search(reels_html) or _OWNER.search(videos_html)
    return ids, owner.group(1) if owner else ""


def list_channel_sync(src: str, fetch=_fetch) -> tuple:
    reels = videos = ""
    errs = []
    for tab in ("reels", "videos"):
        try:
            body = fetch(_tab(src, tab))
        except Exception as e:
            errs.append(str(e)[:120])
            continue
        if tab == "reels":
            reels = body
        else:
            videos = body
    if not (reels or videos):
        raise RuntimeError("; ".join(errs) or "không đọc được kênh")
    return parse_listing(reels, videos)


# ---------------- giờ vàng ----------------

def _hm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _spaced(chosen: list, t: str) -> bool:
    return all(abs(_hm(t) - _hm(c)) >= MIN_GAP_H * 60 for c in chosen)


def slot_stats(st: dict) -> dict:
    """khung giờ -> [lượt xem sau 24 giờ của các bài đã đăng ở khung đó]."""
    out: dict = {}
    for p in st["posts"]:
        if p.get("views") is not None and p.get("slot"):
            out.setdefault(p["slot"], []).append(p["views"])
    return out


def choose_slots(st: dict, n: int, rnd: random.Random) -> list:
    """n khung giờ cho một ngày. Khung đã có >= 2 bài đo được xếp theo lượt xem trung bình; khung chưa đủ số liệu
    được tính bằng mức trung bình chung (để vẫn có lượt thử) và xếp theo thứ tự mặc định; thỉnh thoảng (EXPLORE) đổi
    khung yếu nhất lấy một khung ít được thử nhất."""
    fixed = fixed_times()
    if fixed:
        return fixed[:n] if len(fixed) >= n else fixed
    stats = slot_stats(st)
    known = {k: sum(v) / len(v) for k, v in stats.items() if len(v) >= 2}
    overall = (sum(known.values()) / len(known)) if known else 0.0

    def score(t):
        return (known.get(t, overall), -DEFAULT_ORDER.index(t))
    ranked = sorted(CANDIDATES, key=score, reverse=True)
    chosen = []
    for t in ranked:
        if len(chosen) < n and _spaced(chosen, t):
            chosen.append(t)
    if len(chosen) >= 2 and rnd.random() < EXPLORE:
        tries = sorted((t for t in CANDIDATES if t not in chosen), key=lambda t: (len(stats.get(t, [])), rnd.random()))
        keep = chosen[:-1]
        for t in tries:
            if _spaced(keep, t):
                chosen = keep + [t]
                break
    return sorted(chosen, key=_hm)


def _settings_fp() -> str:
    return f"{','.join(fixed_times())}|{per_day()}"


def _day_busy(st: dict, day: str) -> bool:
    """Ngày đó đã có bài hẹn / video đang làm: giữ nguyên khung giờ của ngày (không hẹn thêm lệch giờ)."""
    times = [p.get("time", 0) for p in st["posts"]] + ([st["pending"].get("slot_ts", 0)] if st.get("pending") else [])
    return any(datetime.fromtimestamp(t, VN).date().isoformat() == day for t in times if t)


def day_slots(st: dict, day: str, rnd: random.Random) -> list:
    """Khung giờ của một ngày, chọn một lần rồi giữ. Đổi AUTO_TIMES / AUTO_PER_DAY thì chọn lại ngay cho các ngày
    chưa có bài nào (trước đây phải chờ ~2 ngày)."""
    d = st["days"].get(day)
    fp = _settings_fp()
    if isinstance(d, dict) and d.get("fp") == fp:
        return d["slots"]
    if d is not None and _day_busy(st, day):
        return d["slots"] if isinstance(d, dict) else d
    st["days"][day] = {"fp": fp, "slots": choose_slots(st, per_day(), rnd)}
    for old in sorted(st["days"])[:-10]:
        st["days"].pop(old, None)
    return st["days"][day]["slots"]


def upcoming_slots(st: dict, now: float, rnd: random.Random, horizon: float = HORIZON) -> list:
    """[(unix time, "HH:MM")] các khung giờ từ now + LEAD tới now + horizon."""
    out = []
    d0 = datetime.fromtimestamp(now, VN).date()
    for k in range(3):
        day = d0 + timedelta(days=k)
        for t in day_slots(st, day.isoformat(), rnd):
            h, m = map(int, t.split(":"))
            ts = datetime(day.year, day.month, day.day, h, m, tzinfo=VN).timestamp()
            if now + LEAD <= ts <= now + horizon:
                out.append((ts, t))
    return sorted(out)


def free_slots(st: dict, now: float, rnd: random.Random) -> list:
    taken = {round(p["time"]) for p in st["posts"] if p.get("time", 0) > now}
    if st.get("pending"):
        taken.add(round(st["pending"].get("slot_ts", 0)))
    return [(ts, t) for ts, t in upcoming_slots(st, now, rnd) if round(ts) not in taken]


# ---------------- Graph API ----------------

def _err(d) -> FBError | None:
    e = (d or {}).get("error") if isinstance(d, dict) else None
    if not e:
        return None
    return FBError(f"Facebook lỗi {e.get('code')}: {e.get('message', '')[:200]}", int(e.get("code") or 0))


async def _json(resp) -> dict:
    try:
        d = await resp.json(content_type=None)
    except Exception:
        d = {"error": {"code": resp.status, "message": (await resp.text())[:200]}}
    err = _err(d)
    if err or resp.status >= 400:
        raise err or FBError(f"Facebook lỗi HTTP {resp.status}", resp.status)
    return d


async def check_token(session=None) -> str:
    """Tên Page nếu token dùng được cho FB_PAGE_ID; không thì ném FBError."""
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(f"{GRAPH}/{API_VER}/{config.FB_PAGE_ID.strip()}",
                               params={"fields": "id,name", "access_token": config.FB_PAGE_TOKEN.strip()},
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            d = await _json(r)
        return d.get("name") or d.get("id", "")
    finally:
        if own:
            await session.close()


async def _upload_reel(session, path: str, description: str, when: float | None) -> str:
    page, tok = config.FB_PAGE_ID.strip(), config.FB_PAGE_TOKEN.strip()
    async with session.post(f"{GRAPH}/{API_VER}/{page}/video_reels",
                            json={"upload_phase": "start", "access_token": tok}) as r:
        d = await _json(r)
    vid = d["video_id"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        async with session.post(d.get("upload_url") or f"{RUPLOAD}/video-upload/{API_VER}/{vid}", data=f,
                                headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
                                timeout=aiohttp.ClientTimeout(total=1800)) as r:
            await _json(r)
    for _ in range(60):                                  # chờ Facebook nhận xong file (tối đa ~10 phút)
        async with session.get(f"{GRAPH}/{API_VER}/{vid}", params={"fields": "status", "access_token": tok}) as r:
            s = (await _json(r)).get("status") or {}
        if (s.get("uploading_phase") or {}).get("status") == "complete" or \
                s.get("video_status") in ("upload_complete", "processing", "ready"):
            break
        if s.get("video_status") in ("error", "upload_failed", "expired"):
            raise FBError(f"Facebook không nhận file: {s}")
        await asyncio.sleep(10)
    params = {"access_token": tok, "video_id": vid, "upload_phase": "finish", "description": description[:2200],
              "video_state": "SCHEDULED" if when else "PUBLISHED"}
    if when:
        params["scheduled_publish_time"] = str(int(when))
    async with session.post(f"{GRAPH}/{API_VER}/{page}/video_reels", params=params) as r:
        await _json(r)
    return vid


async def _upload_video(session, path: str, title: str, description: str, when: float | None,
                        thumb: str = "") -> str:
    page, tok = config.FB_PAGE_ID.strip(), config.FB_PAGE_TOKEN.strip()
    form = aiohttp.FormData()
    form.add_field("access_token", tok)
    form.add_field("title", title[:250])
    form.add_field("description", description[:5000])
    if when:
        form.add_field("published", "false")
        form.add_field("unpublished_content_type", "SCHEDULED")
        form.add_field("scheduled_publish_time", str(int(when)))
    files = [open(path, "rb")]
    form.add_field("source", files[0], filename=os.path.basename(path), content_type="video/mp4")
    if thumb and os.path.exists(thumb):
        files.append(open(thumb, "rb"))
        form.add_field("thumb", files[1], filename="thumb.jpg", content_type="image/jpeg")
    try:
        async with session.post(f"{GRAPH_VIDEO}/{API_VER}/{page}/videos", data=form,
                                timeout=aiohttp.ClientTimeout(total=3600)) as r:
            d = await _json(r)
    finally:
        for f in files:
            f.close()
    return str(d.get("id") or d.get("video_id") or "")


async def publish(path: str, title: str, description: str, duration: float, when: float | None,
                  thumb: str = "", session=None) -> tuple:
    """Đăng (hoặc hẹn giờ) lên Page. Trả (id video Facebook, "reel" | "video")."""
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
    try:
        if duration <= REEL_MAX:
            return await _upload_reel(session, path, description, when), "reel"
        return await _upload_video(session, path, title, description, when, thumb), "video"
    finally:
        if own:
            await session.close()


async def views_of(video_id: str, session) -> int | None:
    async with session.get(f"{GRAPH}/{API_VER}/{video_id}/video_insights",
                           params={"access_token": config.FB_PAGE_TOKEN.strip()}) as r:
        d = await _json(r)
    vals = {x.get("name"): x for x in d.get("data", [])}
    for m in VIEW_METRICS:
        if m in vals:
            v = (vals[m].get("values") or [{}])[0].get("value")
            if isinstance(v, (int, float)):
                return int(v)
    return None


# ---------------- vòng chạy ----------------

def _post_text(job) -> tuple:
    post = (job.post or "").strip()
    title = post.split("\n", 1)[0].replace("📌", "").strip() if post else ""
    return title or "Video mới", post or title


def _output_files(job) -> tuple:
    video = next((p["path"] for p in job.parts if p.get("label") == "Video" and os.path.exists(p["path"])), "")
    video = video or (job.output if job.output and os.path.exists(job.output) else "")
    thumb = next((p["path"] for p in job.parts if str(p.get("label", "")).startswith("Ảnh bìa")
                  and os.path.exists(p["path"])), "")
    return video, thumb


RETRY_AFTER = 6 * 3600         # video / kênh / lượt xem đọc lỗi: thử lại sau 6 giờ, không gọi Facebook mỗi 2 phút
MAX_VIDEO_FAILS = 2            # một video nguồn lỗi (tải / làm / AI) chừng này lần thì bỏ hẳn
STREAK_PAUSE = 3               # chừng này video liên tiếp không đăng được thì dừng hẳn, báo admin
PAUSE_CODES = {190, 10, 200, 100, 368}   # token hết hạn / thiếu quyền / sai id Page / bị Facebook chặn: cần người sửa
_LOCK = asyncio.Lock()
_POSTED: dict = {}             # job id -> bài đã đăng (trong bộ nhớ): ghi file lỗi cũng không đăng lại


def _fail(st: dict, vid: str, src: str, now: float, why: str, hard: bool = False):
    """Ghi một lần lỗi của video nguồn. Lỗi cố định (khác kênh, quá dài...) hoặc lỗi lặp lại MAX_VIDEO_FAILS lần thì bỏ
    hẳn; lỗi tạm (mạng, hết hạn mức AI, đầy ổ...) thì cho video đó nghỉ RETRY_AFTER rồi thử lại."""
    f = st["fails"].setdefault(vid, {"n": 0})
    f["n"], f["next"], f["why"] = f["n"] + 1, now + RETRY_AFTER, why[:120]
    if hard or f["n"] >= MAX_VIDEO_FAILS:
        st["used"][vid] = {"at": now, "source": src, "skip": why[:120]}
    else:
        st["used"].pop(vid, None)


async def _pick_and_queue(st: dict, slot: tuple, now: float, rnd: random.Random) -> str:
    import core_video
    from queue_manager import Job, QueueFull
    from state import queue
    srcs = sources()
    rnd.shuffle(srcs)
    for src in srcs:
        lst = st["listing"].get(src)
        if not lst or (now - lst.get("at", 0) > LIST_TTL and now - lst.get("fail_at", 0) > RETRY_AFTER / 6):
            try:
                ids, owner = await asyncio.to_thread(list_channel_sync, src)
                lst = st["listing"][src] = {"at": now, "ids": ids, "owner": owner}
            except Exception as e:                      # nhớ lần lỗi: không đọc lại kênh mỗi 2 phút
                logging.warning("Tự động đăng: không đọc được kênh %s (%s)", src, str(e)[:120])
                lst = st["listing"][src] = {**(lst or {"ids": [], "owner": ""}), "fail_at": now,
                                            "err": str(e)[:120]}
        cands = [v for v in lst.get("ids", []) if v not in st["used"]
                 and st["fails"].get(v, {}).get("next", 0) <= now]
        rnd.shuffle(cands)
        for vid in cands[:3]:
            url = f"https://www.facebook.com/reel/{vid}/"
            try:
                with tempfile.TemporaryDirectory() as td:
                    info = await core_video.probe_formats(url, td)
            except Exception as e:
                _fail(st, vid, src, now, f"không xem được: {str(e)[:80]}")
                continue
            dur, up = info.get("duration") or 0, str(info.get("uploader_id") or "")
            why = ("khác kênh" if lst.get("owner") and up and up != lst["owner"] else
                   f"dài {dur}s > {config.LONG_MAX}s" if dur > config.LONG_MAX else
                   "quá ngắn" if 0 < dur < 5 else "")
            if why:
                _fail(st, vid, src, now, why, hard=True)
                continue
            voice = config.default_voice()
            mode = config.AUTO_MODE if config.AUTO_MODE in ("cook", "review") else "cook"
            job = Job(url=url, mode=mode, duration=0, voice=voice, source="auto",
                      long_mode="full" if dur > config.MAX_DURATION else "", chat_id=config.admin_chat_id() or 0,
                      title_hint=info.get("title", "")[:200])
            try:
                queue.put(job)
            except QueueFull:
                return "hàng đợi đầy, thử lại sau"
            st["used"][vid] = {"at": now, "source": src, "job": job.id}
            st["pending"] = {"job": job.id, "vid": vid, "source": src, "slot_ts": slot[0], "slot": slot[1],
                             "dur": dur}
            logging.info("Tự động đăng: job #%s lấy video %s của %s (%ds) cho khung %s %s", job.id, vid, src, dur,
                         slot[1], datetime.fromtimestamp(slot[0], VN).strftime("%d/%m"))
            return f"job #{job.id}"
    return "hết video mới ở các kênh nguồn (chờ kênh đăng thêm)"


async def _job_failed(st: dict, p: dict, now: float, why: str) -> str:
    from state import notify_admin
    logging.warning("Tự động đăng: video %s không đăng được (%s), chọn video khác", p["vid"], why)
    _fail(st, p["vid"], p["source"], now, why)
    st["pending"] = None
    st["streak"] = st.get("streak", 0) + 1
    if st["streak"] >= STREAK_PAUSE:
        st["paused"] = f"{st['streak']} video liên tiếp không đăng được, lần cuối: {why[:150]}"
        await notify_admin(f"⛔ Tự động đăng đã dừng: {st['paused']}. Sửa xong gõ  auto_resume  (hoặc bấm nút trên web).")
    return why


async def _finish_pending(st: dict, now: float, rnd: random.Random) -> str:
    from state import notify_admin, queue
    p = st["pending"]
    if p["job"] in _POSTED:                     # đã đăng ở lượt trước nhưng ghi file lỗi: chỉ ghi lại, KHÔNG đăng lại
        if not any(x.get("job") == p["job"] for x in st["posts"]):
            st["posts"].append(_POSTED[p["job"]])
        st["pending"] = None
        return f"job #{p['job']} đã đăng trước đó"
    if p.get("publishing"):                     # bot dừng / ghi file lỗi đúng lúc đang tải lên: không biết đã lên chưa
        st["pending"] = None
        st["used"][p["vid"]] = {"at": now, "source": p["source"], "skip": "dừng giữa lúc đăng"}
        await notify_admin(f"⚠️ Bot dừng đúng lúc đang đăng video #{p['job']} lên Fanpage. Mở Fanpage kiểm tra (kể cả "
                           "mục bài đã lên lịch); bot KHÔNG tự đăng lại video này để tránh trùng.")
        return f"job #{p['job']} dừng giữa lúc đăng, không đăng lại"
    if p.get("next_try", 0) > now:
        return "chờ thử đăng lại lúc " + datetime.fromtimestamp(p["next_try"], VN).strftime("%H:%M")
    job = queue.jobs.get(p["job"])
    if job is None:
        return await _job_failed(st, p, now, "job tự động không còn trong danh sách")
    if job.status in ("queued", "processing", "approved"):
        return f"đang làm job #{job.id}"
    if job.status != "done":
        return await _job_failed(st, p, now, f"job #{job.id} {job.status}: {(job.error or '')[:100]}")
    if getattr(job, "no_autopost", ""):         # AI lỗi / lời mới gần như chép lời gốc: không đăng công khai
        return await _job_failed(st, p, now, f"job #{job.id}: {job.no_autopost}")
    video, thumb = _output_files(job)
    if not video:
        return await _job_failed(st, p, now, f"job #{job.id} không còn file video")
    when = p["slot_ts"]
    if when < now + LEAD:                       # trễ khung giờ (máy chậm/bot tắt): sang khung trống kế tiếp
        nxt = free_slots(st, now, rnd)
        when = nxt[0][0] if nxt else None       # không còn khung nào trong 36 giờ: đăng ngay
        p["slot"] = nxt[0][1] if nxt else datetime.fromtimestamp(now, VN).strftime("%H:%M")
    title, desc = _post_text(job)
    dur = job.out_dur or p.get("dur") or 0
    p["publishing"] = now
    save_state(st)                              # ghi dấu TRƯỚC khi tải lên; ghi lỗi thì dừng, chưa đăng gì
    try:
        fb_id, kind = await publish(video, title, desc, dur, when, thumb)
    except Exception as e:
        p.pop("publishing", None)
        p["tries"] = p.get("tries", 0) + 1
        p["next_try"] = now + min(3600, 300 * 2 ** (p["tries"] - 1))     # 5, 10, 20, 40, 60 phút
        code = e.code if isinstance(e, FBError) else 0
        if code in PAUSE_CODES or p["tries"] >= 5:
            st["paused"] = str(e)[:200]
            await notify_admin(f"⛔ Tự động đăng Facebook đã dừng: {str(e)[:200]}. Kiểm tra FB_PAGE_ID / FB_PAGE_TOKEN "
                               f"trong Cài đặt rồi gõ  auto_resume  (video #{job.id} vẫn giữ để đăng lại).")
        raise
    rec = {"fb_id": fb_id, "kind": kind, "job": job.id, "vid": p["vid"], "source": p["source"],
           "slot": p["slot"], "time": when or now, "views": None}
    _POSTED[job.id] = rec
    st["posts"].append(rec)
    st["pending"] = None
    st["streak"] = 0
    st["fails"].pop(p["vid"], None)
    try:
        save_state(st)                          # ghi ngay, không đợi cuối lượt
    except OSError as e:
        logging.error("Tự động đăng: đã đăng %s nhưng không ghi được autopost.json (%s)", fb_id, e)
    at = datetime.fromtimestamp(when or now, VN).strftime("%H:%M %d/%m")
    logging.info("Tự động đăng: %s %s lên Page, %s %s", kind, fb_id, "hẹn" if when else "đăng ngay", at)
    await notify_admin(f"📅 Đã {'hẹn đăng' if when else 'đăng'} video #{job.id} lên Fanpage lúc {at}: {title[:80]}")
    return f"đã {'hẹn' if when else 'đăng'} {kind} {fb_id} lúc {at}"


async def _update_views(st: dict, now: float):
    """Lượt xem sau 24 giờ cho việc chọn giờ vàng. Đọc lỗi thì 6 giờ sau mới thử lại; quá 7 ngày thì thôi (không ghi
    0: số 0 giả làm lệch việc chọn giờ)."""
    todo = [p for p in st["posts"] if p.get("views") is None and p.get("fb_id") and not p.get("views_done")
            and now - p.get("time", now) >= 24 * 3600 and now - p.get("views_try", 0) >= RETRY_AFTER]
    if not todo:
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        for p in todo[:3]:
            p["views_try"] = now
            try:
                v = await views_of(p["fb_id"], s)
            except Exception as e:
                logging.info("Tự động đăng: chưa đọc được lượt xem %s (%s)", p["fb_id"], str(e)[:100])
                v = None
            if v is not None:
                p["views"] = v
                logging.info("Tự động đăng: bài %s khung %s được %s lượt xem sau 24 giờ", p["fb_id"], p["slot"], v)
            elif now - p["time"] > 7 * 86400:
                p["views_done"] = True


async def tick(now: float | None = None, rnd: random.Random | None = None, force: bool = False) -> str:
    """Một vòng: xong job thì đăng; còn khung giờ trống trong 36 giờ tới mà hàng đợi rảnh thì lấy video mới.
    force=True: không chờ hàng đợi trống. Chỉ MỘT lượt chạy tại một thời điểm (vòng nền, nút web, lệnh console):
    hai lượt cùng lúc từng có thể đăng trùng một video."""
    if _LOCK.locked():
        return "một lượt khác đang chạy, thử lại sau ít phút"
    async with _LOCK:
        return await _tick(now, rnd, force)


async def _tick(now, rnd, force) -> str:
    from state import queue
    now = now or time.time()
    rnd = rnd or random.Random()
    why = enabled()
    if why:
        return why
    st = load_state()
    if st["paused"]:
        return "đang dừng vì lỗi: " + st["paused"]
    try:
        if st["pending"]:
            msg = await _finish_pending(st, now, rnd)
            if st["pending"] or st["paused"]:
                return msg
        try:
            await _update_views(st, now)
        except Exception as e:
            logging.info("Tự động đăng: lỗi đọc lượt xem (%s)", str(e)[:100])
        slots = free_slots(st, now, rnd)
        if not slots:
            return "đã đủ video cho các khung giờ trong 36 giờ tới"
        if queue.pending and not force:
            return "chờ hàng đợi trống (video bạn gửi được làm trước)"
        return await _pick_and_queue(st, slots[0], now, rnd)
    finally:
        save_state(st)


async def resume() -> str:
    async with _LOCK:
        st = load_state()
        st["paused"] = ""
        st["streak"] = 0
        save_state(st)
    return "Đã chạy lại. " + await tick()


def status_text() -> str:
    st = load_state()
    now = time.time()
    why = enabled()
    lines = [f"🤖 Tự động đăng: {'BẬT' if not why else 'tắt — ' + why}" + (f" | DỪNG: {st['paused']}" if st["paused"] else "")]
    lines.append(f"Kênh nguồn: {', '.join(sources()) or '(chưa có)'} | {per_day()} video/ngày | giờ: "
                 + (", ".join(fixed_times()) or "tự chọn giờ vàng"))
    rnd = random.Random(0)
    today = datetime.fromtimestamp(now, VN).date().isoformat()
    if not why:
        lines.append(f"Khung giờ hôm nay: {', '.join(day_slots(st, today, rnd))}")
    if st["pending"]:
        p = st["pending"]
        lines.append(f"Đang làm: job #{p['job']} cho khung {p['slot']} "
                     f"{datetime.fromtimestamp(p['slot_ts'], VN).strftime('%d/%m')}")
    fut = [p for p in st["posts"] if p.get("time", 0) > now]
    if fut:
        lines.append("Đã hẹn: " + ", ".join(datetime.fromtimestamp(p["time"], VN).strftime("%H:%M %d/%m") for p in fut))
    stats = slot_stats(st)
    if stats:
        lines.append("Lượt xem TB sau 24h theo khung: " + ", ".join(
            f"{k} {sum(v) / len(v):.0f} ({len(v)} bài)" for k, v in sorted(stats.items())))
    lines.append(f"Đã dùng {len(st['used'])} video nguồn, đã đăng {len(st['posts'])} bài.")
    if not why:
        save_state(st)                       # khung giờ hôm nay vừa chọn (nếu chưa có) phải giữ nguyên cho vòng chạy
    return "\n".join(lines)


async def run_forever(every: float = 120):
    await asyncio.sleep(60)                    # để bot khởi động xong, tự kiểm tra ffmpeg trước
    while True:
        try:
            msg = await tick()
            if msg and not msg.startswith(("AUTO_POST đang tắt", "đang làm", "đã đủ", "chờ hàng đợi", "một lượt",
                                           "chờ thử đăng lại", "đang dừng", "thiếu FB", "chưa có kênh")):
                logging.info("Tự động đăng: %s", msg)
        except Exception as e:
            logging.warning("Tự động đăng lỗi: %s", str(e)[:200])
        await asyncio.sleep(every)

