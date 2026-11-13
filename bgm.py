"""Nhạc nền cho video KHÔNG LỜI (NO_SPEECH=music): không cho AI bịa lời theo hình (dễ nói sai sự thật của video gốc),
chỉ chèn nhạc nền. Thư viện nhạc: link trong BGM_LINKS (TikTok / YouTube / file mp3 trên mạng — Kaggle tự tải được) và
file tải lên trên web (assets/bgm/; Kaggle cần PUBLIC_URL để lấy). Mỗi lần chọn ngẫu nhiên, tránh 3 bài vừa dùng."""
import json
import logging
import os
import random
import re
import time
import urllib.parse

import config

BASE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(BASE, "assets", "bgm")
RECENT_FILE = os.environ.get("BGM_RECENT") or os.path.join(BASE, "assets", "bgm_recent.json")
EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac")
AVOID = 3                       # không lặp lại 3 bài vừa dùng (thư viện ít bài hơn thì lặp ít nhất có thể)
MAX_FILES = 40
MAX_MB = 15


def links() -> list:
    out = []
    for s in re.split(r"[\s,]+", getattr(config, "BGM_LINKS", "") or ""):
        if s.startswith(("http://", "https://")) and s not in out:
            out.append(s)
    return out


def files() -> list:
    try:
        return sorted(os.path.join(DIR, f) for f in os.listdir(DIR) if f.lower().endswith(EXTS))
    except OSError:
        return []


def link_name(u: str) -> str:
    """Tên dễ đọc của một link nhạc: 'tiktok.com @kenh 7300...', 'youtube.com abc123', 'x.example b.mp3'."""
    p = urllib.parse.urlsplit(u)
    host = re.sub(r"^(www|m|vm|vt)\.", "", (p.hostname or "").lower())
    segs = [x for x in p.path.split("/") if x]
    who = next((x for x in segs if x.startswith("@")), "")
    tail = (urllib.parse.parse_qs(p.query).get("v") or [segs[-1] if segs else ""])[0]
    return " ".join(x for x in (host, who if who != tail else "", tail) if x)[:60] or u[:60]


def library() -> list:
    """[{"key", "name", "url" | "path"}]."""
    items = [{"key": u, "name": link_name(u), "url": u} for u in links()]
    items += [{"key": "file:" + os.path.basename(p), "name": os.path.basename(p), "path": p} for p in files()]
    return items


def _recent() -> list:
    try:
        d = json.load(open(RECENT_FILE, encoding="utf-8"))
        return [str(x) for x in d] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def remember(item: dict):
    rec = [k for k in _recent() if k != item["key"]] + [item["key"]]
    try:
        os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
        tmp = RECENT_FILE + ".tmp"
        json.dump(rec[-20:], open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, RECENT_FILE)
    except OSError as e:
        logging.info("Không ghi được lịch sử nhạc nền: %s", e)


def pick(rnd: random.Random | None = None, prefer_links: bool = False, exclude=()) -> dict | None:
    """Một bài trong thư viện, tránh các bài vừa dùng. prefer_links: chỉ lấy link khi có (Kaggle không lấy được file
    trên Botkeep khi thiếu PUBLIC_URL). exclude: các key không lấy (bài vừa tải lỗi)."""
    items = [x for x in library() if x["key"] not in exclude]
    if prefer_links and any("url" in x for x in items):
        items = [x for x in items if "url" in x]
    if not items:
        return None
    rnd = rnd or random.Random()
    recent = _recent()[-min(AVOID, max(0, len(items) - 1)):] if len(items) > 1 else []
    fresh = [x for x in items if x["key"] not in recent] or items
    return rnd.choice(fresh)


async def fetch(item: dict, tmp: str) -> str:
    """Đường dẫn file nhạc trên Botkeep (link thì tải về: cần để chọn đoạn nhạc hay + dựng khi không có Kaggle)."""
    if item.get("path"):
        return item["path"]
    import core_video
    return await core_video.download_audio(item["url"], tmp)


def best_start(path: str, length: float) -> float:
    """Giây bắt đầu đoạn nhạc hay nhất (điệp khúc, năng lượng cao) dài `length`; lỗi thì từ đầu bài."""
    try:
        import slideshow
        info = slideshow.analyze_music(path)
        return float(slideshow.choose_window(info, min(length, max(4.0, info["duration"] - 0.5)))[0])
    except Exception as e:
        logging.info("Không phân tích được nhạc nền (%s), lấy từ đầu bài", str(e)[:100])
        return 0.0


def save_upload(name: str, data: bytes) -> str:
    """Lưu một file nhạc tải lên (tên an toàn, giới hạn số bài + dung lượng). Trả tên file."""
    if len(data) > MAX_MB * 1024 * 1024:
        raise ValueError(f"File nhạc tối đa {MAX_MB}MB")
    # trình duyệt gửi tên UTF-8 thô, một số client (aiohttp, curl) mã hoá %XX: giải mã trước khi làm sạch
    base, ext = os.path.splitext(os.path.basename(urllib.parse.unquote(name or "") or "nhac"))
    ext = ext.lower()
    if ext not in EXTS:
        raise ValueError("Chỉ nhận file nhạc: " + ", ".join(EXTS))
    if len(files()) >= MAX_FILES:
        raise ValueError(f"Thư viện nhạc nền tối đa {MAX_FILES} bài, xoá bớt trước")
    safe = re.sub(r"[^\w.-]+", "_", base, flags=re.UNICODE).strip("._")[:60] or "nhac"
    os.makedirs(DIR, exist_ok=True)
    fn = f"{safe}{ext}"
    if os.path.exists(os.path.join(DIR, fn)):
        fn = f"{safe}_{int(time.time())}{ext}"
    with open(os.path.join(DIR, fn), "wb") as f:
        f.write(data)
    return fn


def delete(fn: str) -> bool:
    fn = os.path.basename(fn or "")
    p = os.path.join(DIR, fn)
    if fn and fn.lower().endswith(EXTS) and os.path.isfile(p):
        os.remove(p)
        return True
    return False
