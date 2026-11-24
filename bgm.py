"""Nhạc nền kênh bếp: video KHÔNG LỜI (NO_SPEECH=music, nhạc thay lời — không cho AI bịa lời theo hình) và nhạc lót dưới
lời đọc (VOICE_BED). Hai nguồn:

- Kho Lyria (BGM_SOURCE=lyria, mặc định, 20/11 — làm giống truyện bé): nhạc tự tạo bằng Lyria RealTime theo «màu» món
  (ấm cúng / sôi động / thư giãn, `mood_for`), tạo dần trong nền, đẩy lên Kaggle thành dataset riêng tư `rmvideo-cook-bgm`
  (rmvideo/media/lyria.py). Không bản quyền (Facebook / TikTok không tắt tiếng), Kaggle không cần PUBLIC_URL.
- Thư viện của mình: link trong BGM_LINKS (TikTok / YouTube / file mp3 trên mạng — Kaggle tự tải được) và file tải lên
  trên web (assets/bgm/; Kaggle cần PUBLIC_URL để lấy). Dùng khi BGM_SOURCE=files, hoặc kho Lyria chưa có bài.
Mỗi lần chọn ngẫu nhiên, tránh 3 bài vừa dùng."""
import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse

import config
from rmvideo.media import lyria

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


# ---------------- kho nhạc Lyria của kênh bếp (giống truyện bé, 20/11) ----------------
LYRIA_DIR = os.environ.get("BGM_LYRIA_DIR") or os.path.join(BASE, "assets", "bgm_lyria")
LYRIA_DATASET = "rmvideo-cook-bgm"
LYRIA_SECS = 150                # video thường ≤ 90 giây; video dài (≤ 5 phút) thì nhạc lặp lại
LYRIA_PER_MOOD = 3
LYRIA_RETRY_AFTER = 1800        # Lyria lỗi: 30 phút sau mới thử tạo lại trong nền
# Lyria lọc prompt nhắc tên nghệ sĩ / bài hát: chỉ tả nhạc cụ, nhịp, cảm giác
MOODS: dict = {
    "cozy": {"vi": "ấm cúng", "bpm": 92, "density": 0.4, "brightness": 0.5,
             "prompt": "warm cozy home cooking background music, acoustic guitar, soft piano, light brushed percussion, "
                       "gentle groove, friendly, homey kitchen, instrumental"},
    "upbeat": {"vi": "sôi động", "bpm": 112, "density": 0.55, "brightness": 0.65,
               "prompt": "bright upbeat cooking vlog background music, funky bass, ukulele, light claps, snappy rhythm, "
                         "playful, energetic but not loud, instrumental"},
    "chill": {"vi": "thư giãn", "bpm": 84, "density": 0.35, "brightness": 0.45,
              "prompt": "chill lofi cooking background music, mellow electric piano, soft jazzy chords, laid-back beat, "
                        "relaxed, calm, instrumental"},
}
DEFAULT_MOOD = "cozy"
# chữ trong tiêu đề / mô tả / tên món -> màu nhạc (đếm số chữ khớp, hoà thì cozy)
MOOD_WORDS = {
    "upbeat": ("chiên", "rán", "xào", "nướng", "giòn", "cay", "lẩu", "đường phố", "ăn vặt", "vỉa hè", "bbq", "xiên", "mukbang",
               "review", "thử thách", "siêu tốc", "nhanh", "1 phút", "street food", "fried", "spicy", "grill"),
    "chill": ("trà", "cà phê", "cafe", "sinh tố", "nước ép", "đồ uống", "matcha", "sữa chua", "healthy", "eat clean",
              "salad", "smoothie", "coffee", "tea", "asmr", "detox", "bữa sáng nhẹ"),
    "cozy": ("canh", "cháo", "hầm", "kho", "súp", "chè", "bánh", "mẹ", "gia đình", "cơm nhà", "mùa đông", "trời mưa",
             "ninh", "hấp", "soup", "stew", "bake", "cake"),
}
_FILL: dict = {"task": None, "fail_at": 0.0}


def lyria_lib() -> lyria.Lib:
    return lyria.Lib(dir=LYRIA_DIR, dataset=LYRIA_DATASET, title="rmvideo cook bgm", moods=MOODS, track_secs=LYRIA_SECS,
                     per_mood=LYRIA_PER_MOOD, tag="Nhạc bếp", timeout=LYRIA_SECS + 150, fade_out=3.0)


def mood_for(text: str, mode: str = "cook") -> str:
    """Màu nhạc theo chữ của video (tiêu đề, mô tả, tên món): món chiên xào / đường phố / review -> sôi động, đồ uống / healthy
    -> thư giãn, canh cháo bánh -> ấm cúng; không khớp: review sôi động, còn lại ấm cúng."""
    low = f" {(text or '').lower()} "
    score = {m: sum(1 for w in ws if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", low)) for m, ws in MOOD_WORDS.items()}
    best = max(score, key=lambda m: score[m])
    if score[best] == 0 or list(score.values()).count(score[best]) > 1:
        return "upbeat" if mode == "review" and score.get("upbeat", 0) >= score[best] else DEFAULT_MOOD
    return best


def lyria_items(mood: str = "", remote: bool = False) -> list:
    """Bài Lyria dùng được, dạng mục thư viện {"key", "name", "path", "lyria"}: đúng màu nếu có, không thì mọi màu.
    remote (dựng trên Kaggle): chỉ bài đã có trong dataset (đã đẩy)."""
    data = lyria.load_library(LYRIA_DIR)
    pushed = set(data.get("pushed") or [])
    ok = [t for t in data["tracks"] if not remote or t["file"] in pushed]
    same = [t for t in ok if t.get("mood") == mood]
    return [{"key": "lyria:" + t["file"], "name": f"Lyria {MOODS.get(t.get('mood'), {}).get('vi', t.get('mood'))} "
                                                  f"({t['file']})",
             "path": os.path.join(LYRIA_DIR, t["file"]), "lyria": t["file"]} for t in (same or ok)]


def fill_later(mood: str, log=None) -> bool:
    """Kho thiếu bài màu `mood`: tạo thêm MỘT bài trong nền (không làm chậm job đang chạy) rồi đẩy dataset; màu đã đủ bài
    mà kho có bài CHƯA đẩy lên Kaggle (lần đẩy trước lỗi mạng, hay kho tạo lúc Kaggle tắt) thì chỉ đẩy. Một việc một lúc;
    lỗi thì nghỉ LYRIA_RETRY_AFTER giây. Trả True nếu vừa bắt đầu một việc nền."""
    import kaggle_worker
    if mood not in MOODS:
        return False
    data = lyria.load_library(LYRIA_DIR)
    make = len(lyria.tracks(data, mood)) < LYRIA_PER_MOOD and bool(config.GEMINI_API_KEY)
    push = lyria.changed_since_push(data) and kaggle_worker.is_enabled()
    if make:
        from rmvideo.media import lyria_kaggle
        if lyria_kaggle.proven():                   # Kaggle đã tạo được nhạc (mạng Botkeep không theo kịp Lyria): nhờ Kaggle
            msg = lyria_kaggle.start_later(why=f"kho bếp thiếu bài màu «{mood}»")
            if msg:
                (log or (lambda m: logging.info("%s", m)))(f"Nhạc bếp: {msg}")
            make = False
    if not (make or push):
        return False
    t = _FILL.get("task")
    if (t is not None and not t.done()) or time.time() - float(_FILL.get("fail_at") or 0) < LYRIA_RETRY_AFTER:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _FILL["task"] = loop.create_task(_fill(mood if make else "", log or (lambda m: logging.info("%s", m))))
    return True


async def _fill(mood: str, log) -> None:
    """mood rỗng: chỉ đẩy kho lên Kaggle."""
    lib = lyria_lib()
    try:
        if mood:
            await lyria.generate(lib, mood, log=log)
        await push_lyria(log)
    except Exception as e:
        _FILL["fail_at"] = time.time()
        logging.warning("Nhạc bếp: %s nhạc Lyria lỗi (%s), %d phút sau thử lại", "tạo" if mood else "đẩy",
                        str(e)[:160], LYRIA_RETRY_AFTER // 60)
        if isinstance(e, lyria.Slow):              # mạng Botkeep không theo kịp Lyria: nhờ Kaggle tạo kho
            from rmvideo.media import lyria_kaggle
            msg = lyria_kaggle.start_later(why="Lyria trên Botkeep quá chậm")
            if msg:
                logging.info("Nhạc bếp: %s", msg)


async def push_lyria(log=None) -> dict:
    """Đẩy kho lên Kaggle khi có bài mới và Kaggle đã bật (dựng trên Botkeep thì không cần)."""
    import kaggle_worker
    data = lyria.load_library(LYRIA_DIR)
    if not lyria.changed_since_push(data) or not kaggle_worker.is_enabled():
        return data
    return await asyncio.to_thread(lyria.push_dataset_sync, lyria_lib(), log or (lambda m: logging.info("%s", m)))


async def build_lyria(log=None) -> str:
    """Lệnh console cook_bgm / nút web: làm đủ kho (mỗi màu LYRIA_PER_MOOD bài, ~2,5 phút / bài) rồi đẩy lên Kaggle."""
    log = log or (lambda m: logging.info("%s", m))
    return await lyria.build(lyria_lib(), log=log, push=lambda: push_lyria(log))


def build_lyria_later(log=None) -> str:
    """Bắt đầu làm đủ kho trong nền (lệnh console cook_bgm, nút web); đang làm thì thôi. Trả câu báo."""
    t = _FILL.get("task")
    if t is not None and not t.done():
        return "kho nhạc Lyria của bếp đang được tạo, chờ xong đã"
    if not config.GEMINI_API_KEY:
        return "chưa có GEMINI_API_KEY: Lyria cần key Gemini (free)"
    log = log or (lambda m: logging.info("🎵 %s", m))

    async def run():
        try:
            log(await build_lyria(log))
        except Exception as e:
            _FILL["fail_at"] = time.time()
            log(f"❌ tạo kho nhạc Lyria của bếp lỗi: {str(e)[:200]}")
    _FILL["fail_at"] = 0.0
    _FILL["task"] = asyncio.get_running_loop().create_task(run())
    missing = sum(max(0, LYRIA_PER_MOOD - n) for n in lyria_state()["moods"].values())
    return (f"đang tạo {missing} bài nhạc Lyria cho bếp trong nền (~{missing * (LYRIA_SECS + 15) // 60} phút), xong sẽ đẩy "
            "lên Kaggle" if missing else "kho đã đủ bài, chỉ đẩy lên Kaggle nếu có bài mới")


def lyria_state() -> dict:
    data = lyria.load_library(LYRIA_DIR)
    t = _FILL.get("task")
    return {"source": getattr(config, "BGM_SOURCE", "lyria"),
            "moods": {m: len(lyria.tracks(data, m)) for m in MOODS}, "per_mood": LYRIA_PER_MOOD,
            "pushed": len(data.get("pushed") or []), "busy": bool(t is not None and not t.done())}


def choose(mood: str = "", remote: bool = False, prefer_links: bool = False, exclude=(), rnd: random.Random | None = None,
           mode: str = "cook") -> dict | None:
    """Bài nhạc cho một video theo BGM_SOURCE: lyria (mặc định) = kho Lyria đúng màu (thiếu bài thì tạo thêm trong nền), kho
    chưa có bài dùng được thì thư viện của mình; files = chỉ thư viện của mình (pick như cũ)."""
    if (getattr(config, "BGM_SOURCE", "lyria") or "lyria") == "lyria":
        mood = mood if mood in MOODS else DEFAULT_MOOD
        fill_later(mood)
        items = [x for x in lyria_items(mood, remote) if x["key"] not in exclude]
        if items:
            rnd = rnd or random.Random()
            recent = _recent()[-min(AVOID, max(0, len(items) - 1)):] if len(items) > 1 else []
            return rnd.choice([x for x in items if x["key"] not in recent] or items)
    return pick(rnd, prefer_links=prefer_links, exclude=exclude)


def kaggle_ref(item: dict) -> dict:
    """Đầu vào Kaggle của một bài: bài Lyria -> file trong dataset đã gắn (+ link tạm nếu có PUBLIC_URL); bài của mình ->
    link gốc / link tạm Botkeep (kaggle_worker.ref, ném KaggleError khi file mà thiếu PUBLIC_URL)."""
    import kaggle_worker
    if item.get("lyria"):
        return lyria.kaggle_ref(lyria_lib(), {"file": item["lyria"]})
    return kaggle_worker.ref(item.get("path"), item.get("url", ""), "audio")
