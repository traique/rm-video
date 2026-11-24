"""🧸 Truyện bé «Kể Bé Nghe»: truyện đã làm (chống trùng 30 ngày), lịch 2 video/ngày, đăng Fanpage RIÊNG (1.1.0).

Giống 🤖 Tự động đăng (autopost.py) nhưng không có kênh nguồn: «nguồn» là đề do mã chọn (rmvideo/kids/story.pick). Vòng
chạy: còn khung giờ trống trong 36 giờ tới và hàng đợi rảnh -> tạo job truyện bé cho khung đó -> job xong -> hẹn đăng
Reels đúng giờ qua autopost.publish(page=Fanpage truyện bé). Làm tay: make_now() (nút «Làm 1 truyện ngay» / lệnh
kids_now) tạo job không gắn khung giờ, không tự đăng — để bạn soát vài video đầu rồi mới bật KIDS_AUTO.
Trạng thái: kids_auto.json (KIDS_STATE) {stories, pending, posts, paused, streak, bgm_recent}."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

from typing import Any

import autopost
import config
from rmvideo.kids import story as story_mod

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("KIDS_STATE") or os.path.join(BASE, "kids_auto.json")
BGM_DIR = os.path.join(BASE, "assets", "kids_bgm")
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac")
VN = autopost.VN
HORIZON = 36 * 3600            # làm sẵn truyện cho các khung giờ trong 36 giờ tới
LEAD = autopost.LEAD           # Facebook: giờ hẹn phải cách hiện tại > 10 phút
KEEP_STORIES = 400
STREAK_PAUSE = 3               # chừng này truyện liên tiếp lỗi thì dừng, báo admin
_LOCK = asyncio.Lock()
_POSTED: dict = {}


# ---------------- dữ liệu / nhận diện ----------------

def data() -> dict:
    d = json.loads(json.dumps(story_mod.load()))
    if (config.KIDS_WORLD_NAME or "").strip():
        d["world"]["name"] = config.KIDS_WORLD_NAME.strip()
    return d


def channel() -> dict:
    return {"name": config.KIDS_CHANNEL_NAME, "world": data()["world"]["name"], "tagline": config.KIDS_TAGLINE,
            "hashtags": config.KIDS_HASHTAGS}


# ---------------- trạng thái ----------------

def load_state(path: str | None = None) -> dict:
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
        logging.error("kids_auto.json hỏng (%s), đã đổi tên thành .bad", e)
        st = {"paused": f"kids_auto.json hỏng ({str(e)[:60]}), đã lưu bản cũ thành .bad — kiểm tra rồi gõ kids_resume"}
    for k, v in (("stories", []), ("pending", None), ("posts", []), ("paused", ""), ("streak", 0), ("bgm_recent", []),
                 ("parked", [])):
        st.setdefault(k, v)
    return st


def save_state(st: dict, path: str | None = None):
    path = path or STATE_FILE
    st["stories"] = st["stories"][-KEEP_STORIES:]
    # tên file tạm riêng từng luồng: worker (park_record, remember_done, luồng Kaggle) và vòng tick ghi cùng lúc thì không
    # ghi đè nhau nửa chừng (file hỏng -> tự động dừng); os.replace nguyên tử, bản ghi sau thắng
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


TICK_KEYS = ("pending", "posts", "streak", "paused")


def save_tick(st: dict) -> None:
    """Ghi phần vòng chạy sở hữu (pending/posts/streak/paused) lên bản MỚI NHẤT của file. Lượt đăng chờ Facebook tải video
    hàng phút; trong lúc đó worker (remember_done), nút «Làm 1 truyện ngay» (new_brief) hay chọn nhạc (bgm_recent) đã ghi
    file — ghi đè bằng bản chụp cũ thì mất truyện vừa ghi, lần sau bốc trùng đề."""
    fresh = load_state()
    for k in TICK_KEYS:
        fresh[k] = st.get(k, fresh[k])
    save_state(fresh)
    st.clear()
    st.update(fresh)


def times() -> list:
    return sorted({t.strip() for t in (config.KIDS_TIMES or "08:00,19:00").split(",") if t.strip()})


def enabled(st: dict | None = None) -> str:
    """Lý do tự động chưa chạy ("" = chạy)."""
    if config.KIDS_AUTO != "1":
        return "KIDS_AUTO đang tắt (bật ở Cài đặt → 🧸 Truyện bé khi đã soát vài video)"
    if not config.KIDS_FB_PAGE_ID.strip() or not config.KIDS_FB_PAGE_TOKEN.strip():
        return "thiếu KIDS_FB_PAGE_ID / KIDS_FB_PAGE_TOKEN (Fanpage riêng của kênh truyện bé)"
    import kaggle_worker
    why = kaggle_worker.enabled()
    return f"truyện bé cần Kaggle: {why}" if why else ""


# ---------------- đề + bộ nhớ truyện ----------------

def new_brief(job=None, rnd: random.Random | None = None) -> dict:
    """Chọn đề cho một job và ghi vào bộ nhớ NGAY (hai job tạo liền nhau không trùng đề)."""
    st = load_state()
    brief = story_mod.pick(data(), st["stories"], rnd)
    st["stories"].append({"job": getattr(job, "id", 0), "created_at": time.time(), "lesson": brief["lesson"],
                          "setting": brief["setting"], "conflict": brief["conflict"], "plot": brief.get("plot") or "",
                          "words": [w["en"] for w in brief["words"]], "main": brief["main"], "title": ""})  # plot: xoay khung
    save_state(st)
    return brief


def remember_done(job, brief: dict, story: dict):
    st = load_state()
    jid = int((brief.get("collect") or {}).get("job") or job.id)     # job thu kết quả: truyện ghi dưới mã job đã nghĩ đề
    hit = next((s for s in reversed(st["stories"]) if s.get("job") == jid), None)
    if hit is None:
        hit = {"job": jid, "created_at": time.time(), "lesson": brief.get("lesson"), "setting": brief.get("setting"),
               "conflict": brief.get("conflict"), "plot": brief.get("plot") or "",
               "words": [w["en"] for w in brief.get("words", [])], "main": brief.get("main")}
        st["stories"].append(hit)
    hit["title"] = story.get("title", "")
    hit["done_at"] = time.time()
    hit["motifs"] = story_mod.motifs(story)         # chi tiết đã dùng: vài truyện sau tránh lặp (phản hồi 21/11)
    if brief.get("words"):                  # từ thật sự dạy (choose_words đổi theo truyện), để vòng 30 ngày xoay đúng từ
        hit["words"] = [w["en"] if isinstance(w, dict) else str(w) for w in brief["words"]]
    if brief.get("plot"):
        hit["plot"] = brief["plot"]
    p = brief.get("pitch") or {}
    if p.get("premise"):                    # ý truyện đã dùng: lượt nghĩ ý sau tránh lặp (pitch.recent_lines)
        hit["premise"], hit["kind"] = str(p["premise"])[:300], str(p.get("kind") or "")
    save_state(st)


# ---------------- phiên Kaggle giữ chỗ (1.4.6) ----------------
PARK_GRACE = 15 * 60           # quá hạn bắt đầu chừng này giây mà vẫn chờ máy: phiên đã tự thoát, bỏ
PARK_HARD_MAX = 8 * 3600       # giữ chỗ lâu nhất (kể cả đang chạy — notebook treo): quá là bỏ
PARK_LOG_EVERY = 10 * 60       # ghi log trạng thái phiên giữ chỗ tối đa mỗi chừng này giây
PARK_COLLECTS = 3              # job thu kết quả lỗi (mạng đứt) chừng này lần thì bỏ, báo admin
_STATE_LOCK = threading.Lock()  # load -> sửa -> save kids_auto.json: luồng Kaggle (park_record) và vòng tick không chen nhau


def park_record(info: dict) -> None:
    """Worker ghi phiên Kaggle truyện bé đang giữ chỗ (kaggle_worker._park): {job, slug, token, fetch_files, gpu, since,
    expires_at, brief, source, chat_id, title_hint}. Mỗi job một mục (ghi lại thì giữ số lần đã thu)."""
    with _STATE_LOCK:
        st = load_state()
        old = next((p for p in st["parked"] if p.get("job") == info.get("job")), {})
        st["parked"] = [p for p in st["parked"] if p.get("job") != info.get("job")] + [{**old, **info}]
        save_state(st)


def parked_info(job_id: int) -> dict | None:
    return next((p for p in load_state()["parked"] if p.get("job") == job_id), None)


def unpark(job_id: int) -> None:
    """Bỏ mục giữ chỗ của job (hoặc mục có job thu kết quả là job này)."""
    with _STATE_LOCK:
        st = load_state()
        if any(job_id in (p.get("job"), p.get("collect_job")) for p in st["parked"]):
            st["parked"] = [p for p in st["parked"] if job_id not in (p.get("job"), p.get("collect_job"))]
            save_state(st)


def _mark_parked(job_id: int, **kw: Any) -> None:
    with _STATE_LOCK:
        st = load_state()
        for p in st["parked"]:
            if p.get("job") == job_id:
                p.update(kw)
        save_state(st)


async def watch_parked(now: float | None = None) -> list[str]:
    """Mỗi lượt tick (kể cả khi KIDS_AUTO tắt: job kids_now cũng giữ chỗ): hỏi trạng thái các phiên Kaggle đang giữ chỗ.
    Xong -> job thu kết quả (brief.collect; cùng nguồn / chat với job cũ nên giao như job cũ); notebook lỗi hay quá hạn
    bắt đầu -> bỏ, báo admin. Job đang chạy lại (bot vừa khởi động lại) tự thu kết quả, không tạo job mới."""
    import kaggle_api
    from rmvideo.app import job_factory
    from state import notify_admin, queue
    now = now or time.time()
    out: list[str] = []
    for p in list(load_state()["parked"]):
        jid = p.get("job")
        busy = [queue.jobs.get(j) for j in (jid, p.get("collect_job")) if j]
        if any(j is not None and j.status in ("queued", "processing", "approved") for j in busy):
            continue                                    # job (chạy lại sau khởi động) hay job thu đang làm: tự thu kết quả
        try:
            s, why = await asyncio.to_thread(kaggle_api.status, p["slug"])
        except Exception as e:
            s, why = "", str(e)
        up = s.upper()
        mins = (now - float(p.get("since") or now)) / 60
        if any(x in up for x in kaggle_api.DONE):
            n = int(p.get("collects") or 0)
            if n >= PARK_COLLECTS:
                unpark(jid)
                msg = f"job thu kết quả truyện #{jid} lỗi {n} lần, bỏ (kết quả vẫn nằm trên Kaggle, notebook {p['slug']})"
                logging.warning("Truyện bé: %s", msg)
                await notify_admin(f"⚠️ Truyện bé: {msg}")
                out.append(msg)
                continue
            brief = dict(p.get("brief") or {})
            brief["collect"] = {k: p.get(k) for k in ("slug", "token", "fetch_files", "gpu", "since", "expires_at", "job")}
            job = job_factory.kids(brief=brief, source=p.get("source") or "auto", chat_id=int(p.get("chat_id") or 0),
                                   title_hint=str(p.get("title_hint") or ""))
            queue.put(job, force=True)
            # mục giữ chỗ còn tới khi job thu xong (flow/kids.py unpark): job thu lỗi (mạng đứt) thì lượt sau thu lại
            prev_collect = p.get("collect_job")
            _mark_parked(jid, collect_job=job.id, collects=n + 1)
            with _STATE_LOCK:
                st = load_state()
                if st["pending"] and st["pending"].get("job") in (jid, prev_collect):
                    st["pending"]["job"] = job.id
                    save_state(st)
            logging.info("Truyện bé: Kaggle đã dựng xong truyện #%s (giữ chỗ %.0f phút): job #%s thu kết quả về", jid, mins,
                         job.id)
            out.append(f"job #{job.id} thu kết quả truyện #{jid}")
            continue
        failed = any(x in up for x in kaggle_api.FAILED)
        expired = (now > float(p.get("expires_at") or 0) + PARK_GRACE and "QUEUED" in up) or mins * 60 > PARK_HARD_MAX
        if failed or expired:
            unpark(jid)
            msg = (f"phiên Kaggle của truyện #{jid} lỗi ({s}: {why[:120]})" if failed
                   else f"Kaggle không làm xong truyện #{jid} trong {mins / 60:.1f} giờ ({s or 'không hỏi được'}), bỏ")
            logging.warning("Truyện bé: %s", msg)
            await notify_admin(f"⚠️ Truyện bé: {msg}")
            out.append(msg)
            continue
        if now - float(p.get("logged") or 0) >= PARK_LOG_EVERY:
            logging.info("Truyện bé #%s: Kaggle %s (%.0f phút, giữ chỗ)", jid,
                         "đang chờ máy" if "QUEUED" in up else ("đang làm" if up else f"không hỏi được ({why[:80]})"), mins)
            _mark_parked(jid, logged=now)
    return out


def make_now(source: str = "web", chat_id: int = 0):
    """Tạo MỘT job truyện bé ngay (không gắn khung giờ, không tự đăng). Trả Job; hàng đợi đầy thì ném QueueFull."""
    from rmvideo.app import job_factory
    from state import queue
    parked = load_state()["parked"]
    if parked:                                          # cùng notebook Kaggle: gửi thêm là mất phiên đang giữ chỗ
        raise RuntimeError(f"truyện #{parked[0].get('job')} đang giữ chỗ trên Kaggle (chờ GPU): chờ nó xong rồi làm truyện mới")
    job = job_factory.kids(brief={}, source=source, chat_id=chat_id or config.admin_chat_id() or 0)
    job.brief = new_brief(job)
    job.title_hint = story_mod.describe(job.brief, data())
    queue.put(job)
    return job


# ---------------- nhạc nền ----------------

def music_items() -> list:
    """[{"key", "name", "url"|"path"}]: link KIDS_BGM_LINKS + file trong assets/kids_bgm/."""
    items = [{"key": u, "name": u[:60], "url": u} for u in autopost._norm_list(config.KIDS_BGM_LINKS, str)
             if u.startswith(("http://", "https://"))]
    try:
        files = sorted(f for f in os.listdir(BGM_DIR) if f.lower().endswith(AUDIO_EXTS))
    except OSError:
        files = []
    items += [{"key": "file:" + f, "name": f, "path": os.path.join(BGM_DIR, f)} for f in files]
    return items


async def pick_music(rnd: random.Random | None = None, brief: dict | None = None, story_data: dict | None = None,
                     log=None) -> dict | None:
    """Nhạc nền theo KIDS_BGM: auto = file / link của mình nếu có, không thì kho Lyria theo khung truyện (rmvideo/kids/bgm);
    lyria = chỉ kho Lyria; files = chỉ file / link; off = không nhạc. Trả đầu vào Kaggle hoặc None."""
    mode = (config.KIDS_BGM or "auto").strip().lower()
    if mode == "off":
        return None
    if mode in ("auto", "files"):
        r = await _pick_file_music(rnd)
        if r or mode == "files":
            return r
    if brief is None:
        return None
    from rmvideo.kids import bgm
    try:
        return await bgm.pick(brief, story_data or data(), rnd=rnd, log=log or (lambda s: logging.info("%s", s)))
    except Exception as e:
        logging.warning("Truyện bé: không có nhạc Lyria (%s)", str(e)[:200])
        return None


async def _pick_file_music(rnd: random.Random | None = None) -> dict | None:
    """Một bài của mình (tránh 3 bài vừa dùng) dưới dạng đầu vào Kaggle (kaggle_worker.ref); không có / không gửi được: None."""
    import kaggle_worker
    rnd = rnd or random.Random()
    items = music_items()
    if not items:
        return None
    st = load_state()
    recent = st["bgm_recent"][-min(3, len(items) - 1):] if len(items) > 1 else []
    fresh = [x for x in items if x["key"] not in recent] or items
    rnd.shuffle(fresh)
    for item in fresh:
        try:
            r = kaggle_worker.ref(item.get("path"), item.get("url", ""), "audio")
        except Exception as e:                                   # file mà chưa có PUBLIC_URL: thử bài khác (link)
            logging.info("Truyện bé: nhạc %s không gửi Kaggle được (%s)", item["name"], str(e)[:80])
            continue
        st["bgm_recent"] = [k for k in st["bgm_recent"] if k != item["key"]][-20:] + [item["key"]]
        save_state(st)
        return r
    return None


# ---------------- khung giờ ----------------

def free_slots(st: dict, now: float, horizon: float = HORIZON) -> list:
    """[(mốc giây, "HH:MM")] các khung KIDS_TIMES trong `horizon` tới chưa có bài hẹn / chưa có job đang làm."""
    taken = {int(p.get("time", 0)) // 60 for p in st["posts"]}
    if st.get("pending"):
        taken.add(int(st["pending"].get("slot_ts", 0)) // 60)
    out = []
    day0 = datetime.fromtimestamp(now, VN).date()
    for k in range(3):
        day = day0 + timedelta(days=k)
        for hm in times():
            h, m = map(int, hm.split(":"))
            ts = datetime(day.year, day.month, day.day, h, m, tzinfo=VN).timestamp()
            if now + LEAD < ts <= now + horizon and int(ts) // 60 not in taken:
                out.append((ts, hm))
    return sorted(out)


# ---------------- vòng chạy ----------------

async def _fail(st: dict, p: dict, now: float, why: str) -> str:
    from state import notify_admin
    logging.warning("Truyện bé: job #%s không đăng được (%s)", p.get("job"), why)
    st["pending"] = None
    st["streak"] = st.get("streak", 0) + 1
    if st["streak"] >= STREAK_PAUSE:
        st["paused"] = f"{st['streak']} truyện liên tiếp lỗi, lần cuối: {why[:150]}"
        await notify_admin(f"⛔ Truyện bé đã dừng tự động: {st['paused']}. Sửa xong gõ  kids_resume  (hoặc nút trên web).")
    return why


async def _finish_pending(st: dict, now: float) -> str:
    from state import notify_admin, queue
    p = st["pending"]
    if p["job"] in _POSTED:
        if not any(x.get("job") == p["job"] for x in st["posts"]):
            st["posts"].append(_POSTED[p["job"]])
        st["pending"] = None
        return f"job #{p['job']} đã đăng trước đó"
    if p.get("publishing"):
        st["pending"] = None
        await notify_admin(f"⚠️ Bot dừng đúng lúc đang đăng truyện #{p['job']} lên Fanpage truyện bé. Mở Fanpage kiểm tra "
                           "(kể cả bài đã lên lịch); bot KHÔNG tự đăng lại để tránh trùng.")
        return f"job #{p['job']} dừng giữa lúc đăng, không đăng lại"
    if p.get("next_try", 0) > now:
        return "chờ thử đăng lại lúc " + datetime.fromtimestamp(p["next_try"], VN).strftime("%H:%M")
    job = queue.jobs.get(p["job"])
    if job is None:
        return await _fail(st, p, now, "job truyện bé không còn trong danh sách")
    if job.status in ("queued", "processing", "approved"):
        return f"đang làm job #{job.id}"
    if job.status == "error" and any(job.id in (x.get("job"), x.get("collect_job")) for x in st.get("parked") or []):
        return f"đang giữ chỗ Kaggle cho job #{job.id}"
    if job.status != "done":
        return await _fail(st, p, now, f"job #{job.id} {job.status}: {(job.error or '')[:120]}")
    video, thumb = autopost._output_files(job)
    if not video:
        return await _fail(st, p, now, f"job #{job.id} không còn file video")
    when = p["slot_ts"]
    if when < now + LEAD:                               # trễ khung (Kaggle chờ máy lâu): sang khung trống kế tiếp
        nxt = free_slots(st, now)
        when = nxt[0][0] if nxt else None
        p["slot"] = nxt[0][1] if nxt else datetime.fromtimestamp(now, VN).strftime("%H:%M")
    title, desc = autopost._post_text(job)
    p["publishing"] = now
    save_tick(st)
    try:
        fb_id, kind = await autopost.publish(video, title, desc, job.out_dur or 75, when, thumb,
                                             page=config.KIDS_FB_PAGE_ID, token=config.KIDS_FB_PAGE_TOKEN)
    except Exception as e:
        p.pop("publishing", None)
        p["tries"] = p.get("tries", 0) + 1
        p["next_try"] = now + min(3600, 300 * 2 ** (p["tries"] - 1))
        code = e.code if isinstance(e, autopost.FBError) else 0
        if code in autopost.PAUSE_CODES or p["tries"] >= 5:
            st["paused"] = str(e)[:200]
            await notify_admin(f"⛔ Truyện bé: đăng Fanpage lỗi, đã dừng: {str(e)[:200]}. Kiểm KIDS_FB_PAGE_ID / "
                               f"KIDS_FB_PAGE_TOKEN rồi gõ  kids_resume  (truyện #{job.id} vẫn giữ để đăng lại).")
        raise
    rec = {"fb_id": fb_id, "kind": kind, "job": job.id, "slot": p["slot"], "time": when or now, "views": None,
           "title": title[:120]}
    _POSTED[job.id] = rec
    st["posts"].append(rec)
    st["posts"] = st["posts"][-200:]
    st["pending"] = None
    st["streak"] = 0
    save_tick(st)
    from rmvideo.app import youtube
    youtube.after_facebook("kids", job, video, title, desc, when)     # ▶️ YouTube Shorts kênh bé (đã kết nối)
    from rmvideo.app import tiktok_kit
    tiktok_kit.after_facebook("kids", job, video, desc, when)        # 🎵 gói đăng TikTok gửi admin đúng giờ
    at = datetime.fromtimestamp(when or now, VN).strftime("%H:%M %d/%m")
    logging.info("Truyện bé: %s %s lên Fanpage, %s %s", kind, fb_id, "hẹn" if when else "đăng ngay", at)
    await notify_admin(f"🧸 Đã {'hẹn đăng' if when else 'đăng'} truyện #{job.id} lên Fanpage truyện bé lúc {at}: {title[:80]}")
    return f"đã {'hẹn' if when else 'đăng'} {kind} {fb_id} lúc {at}"


async def tick(now: float | None = None, rnd: random.Random | None = None, force: bool = False) -> str:
    if _LOCK.locked():
        return "một lượt khác đang chạy"
    async with _LOCK:
        now = now or time.time()
        try:
            notes = await watch_parked(now)
        except Exception as e:
            logging.warning("Truyện bé: theo dõi phiên Kaggle giữ chỗ lỗi: %s", str(e)[:160])
            notes = []
        msg = await _tick(now, rnd, force)
        return "; ".join(notes + [msg]) if notes else msg


async def _tick(now: float, rnd: random.Random | None, force: bool) -> str:
    from queue_manager import QueueFull
    from rmvideo.app import job_factory
    from state import queue
    st = load_state()
    why = enabled(st)
    if why:
        return why
    if st["paused"]:
        return "đang dừng vì lỗi: " + st["paused"]
    try:
        if st["pending"]:
            return await _finish_pending(st, now)       # vừa đăng / đang làm / lỗi: lượt sau mới lấy khung kế
        if st["parked"]:                                # mọi phiên truyện bé cùng notebook: gửi thêm là mất phiên đang giữ chỗ
            return f"đang giữ chỗ Kaggle cho truyện #{st['parked'][0].get('job')}, chưa làm truyện mới"
        slots = free_slots(st, now)
        if not slots:
            return "đã đủ truyện cho các khung giờ trong 36 giờ tới"
        if queue.pending and not force:
            return "chờ hàng đợi trống (video bạn gửi được làm trước)"
        slot = slots[0]
        job = job_factory.kids(brief={}, source="auto", chat_id=config.admin_chat_id() or 0)
        job.brief = new_brief(job, rnd)
        job.title_hint = story_mod.describe(job.brief, data())
        try:
            queue.put(job)
        except QueueFull:
            return "hàng đợi đầy, thử lại sau"
        st["pending"] = {"job": job.id, "slot_ts": slot[0], "slot": slot[1]}
        logging.info("Truyện bé: job #%s (%s) cho khung %s %s", job.id, job.title_hint, slot[1],
                     datetime.fromtimestamp(slot[0], VN).strftime("%d/%m"))
        return f"job #{job.id}: {job.title_hint} → khung {slot[1]} {datetime.fromtimestamp(slot[0], VN).strftime('%d/%m')}"
    finally:
        save_tick(st)                                          # new_brief / worker đã ghi file giữa chừng: không ghi đè


async def resume() -> str:
    async with _LOCK:
        st = load_state()
        st["paused"], st["streak"] = "", 0
        save_state(st)
    return "Đã chạy lại. " + await tick()


def status_text(now: float | None = None) -> str:
    st = load_state()
    now = time.time() if now is None else now
    why = enabled(st)
    lines = [f"🧸 Truyện bé «{config.KIDS_CHANNEL_NAME}»: tự động {'BẬT' if not why else 'tắt — ' + why}"
             + (f" | DỪNG: {st['paused']}" if st["paused"] else "")]
    lines.append(f"Giờ đăng: {', '.join(times())} | model chữ: {config.KIDS_LLM_PATTERN} | ảnh: {config.KIDS_IMAGE_MODEL} "
                 f"| giọng: {config.KIDS_VOICE} | nhạc nền: {len(music_items())} bài")
    if st["pending"]:
        p = st["pending"]
        lines.append(f"Đang làm: job #{p['job']} cho khung {p['slot']} {datetime.fromtimestamp(p['slot_ts'], VN).strftime('%d/%m')}")
    for p in st["parked"]:
        lines.append(f"Giữ chỗ Kaggle: truyện #{p.get('job')} từ "
                     f"{datetime.fromtimestamp(float(p.get('since') or now), VN).strftime('%H:%M %d/%m')} (chờ GPU)")
    fut = [p for p in st["posts"] if p.get("time", 0) > now]
    if fut:
        lines.append("Đã hẹn: " + ", ".join(datetime.fromtimestamp(p["time"], VN).strftime("%H:%M %d/%m") for p in fut))
    d = data()
    rec = story_mod.recent(st["stories"], now)
    lines.append(f"Đã làm {len(st['stories'])} truyện ({len(rec)} trong 30 ngày), đã đăng {len(st['posts'])} bài.")
    from rmvideo.app import youtube
    lines += youtube.status_lines("kids", now)
    from rmvideo.app import tiktok_kit
    lines += tiktok_kit.status_lines("kids", now)
    for s in st["stories"][-5:][::-1]:
        when = datetime.fromtimestamp(float(s.get("created_at") or 0), VN).strftime("%d/%m %H:%M")
        lines.append(f"  #{s.get('job', '?')} {when}: {s.get('title') or '(đang làm)'} — "
                     + story_mod.describe({**s, "words": [{"en": w} for w in s.get("words", [])]}, d))
    return "\n".join(lines)


async def run_forever(every: float = 120):
    await asyncio.sleep(90)
    while True:
        try:
            msg = await tick()
            if msg and not msg.startswith(("KIDS_AUTO", "đang làm", "đã đủ", "chờ hàng đợi", "một lượt", "chờ thử",
                                           "đang dừng", "thiếu KIDS", "truyện bé cần", "đang giữ chỗ")):
                logging.info("Truyện bé: %s", msg)
        except Exception as e:
            logging.warning("Truyện bé lỗi: %s", str(e)[:200])
        await asyncio.sleep(every)
