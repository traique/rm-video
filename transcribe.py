"""Bóc lời thoại gốc KÈM mốc thời gian từng câu, để kịch bản mới được đặt đúng lúc hình đang diễn ra.
Nguồn: Groq Whisper (verbose_json) nếu có key, không thì STT của CapCut (không cần key).
Trả về (text, segments) với segments = [(start_s, end_s, text)]. Lỗi gì cũng trả ("", [])."""
import asyncio
import hashlib
import json
import logging
import os

import aiohttp

import config
import metrics

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def _groq(audio_path: str):
    f = open(audio_path, "rb")
    try:
        form = aiohttp.FormData()
        form.add_field("model", config.GROQ_WHISPER_MODEL)
        form.add_field("response_format", "verbose_json")
        form.add_field("timestamp_granularities[]", "word")
        form.add_field("file", f, filename="audio.mp3", content_type="audio/mpeg")
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                              data=form, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    body = (await r.text())[:200]
                    logging.warning("Groq Whisper %s: %s", r.status, body)
                    metrics.record("asr", "groq", ok=False, detail=f"{r.status} {body}")
                    return "", []
                d = await r.json()
        metrics.record("asr", "groq")
        units = [(float(x["start"]), float(x["end"]), (x.get("word") or "").strip())
                 for x in d.get("words", []) if (x.get("word") or "").strip()]
        if not units:   # không có mốc từ -> dùng segment của Whisper
            units = [(float(x["start"]), float(x["end"]), (x.get("text") or "").strip())
                     for x in d.get("segments", []) if (x.get("text") or "").strip()]
        return (d.get("text") or "").strip(), chunk_units(units)
    finally:
        f.close()


def _capcut_sync(audio_path: str):
    import tts_capcut
    units = tts_capcut.stt_words(audio_path)
    return " ".join(x[2] for x in units), chunk_units(units)


# Từ mở đầu một thao tác mới trong lời dẫn tiếng Việt: cắt đoạn TRƯỚC các từ này
_ACTION_START = {"cho", "thêm", "sau", "tiếp", "rồi", "xong", "đảo", "lật", "cắt", "thái", "đổ",
                 "bắc", "tắt", "trộn", "ướp", "rửa", "luộc", "chiên", "xào", "nêm", "rắc", "múc",
                 "dùng", "đun", "cuối", "trước", "lấy"}


def chunk_units(units, max_dur: float = 3.2, gap: float = 0.3, min_dur: float = 1.0, hard_max: float = 4.6):
    """Gộp từ thành đoạn ~1.5-3s ứng với MỘT thao tác: cắt ở chỗ ngắt hơi, dấu câu, hoặc trước
    từ mở đầu thao tác mới (khi đoạn hiện tại đã đủ dài). units: [(start, end, text)]."""
    out, cur = [], None
    for idx, (st, en, tx) in enumerate(units):
        first = tx.split()[0].lower().strip(".,!?") if tx.split() else ""
        nxt = units[idx + 1][2].lower().strip(".,!?") if idx + 1 < len(units) else ""
        if first == "cho" and (nxt.startswith("đến") or nxt.startswith("tới") or len(tx.split()) > 1 and tx.split()[1].lower().startswith(("đến", "tới"))):
            first = ""      # "cho đến khi" là từ nối, không phải "cho [nguyên liệu]"
        long_enough = cur is not None and cur[1] - cur[0] >= min_dur
        natural = (st - cur[1] >= gap or cur[2].rstrip().endswith((".", "!", "?", ","))
                   or (long_enough and first in _ACTION_START)) if cur else False
        # quá max_dur mà chưa gặp chỗ ngắt tự nhiên thì đi tiếp (tránh cắt giữa cụm "vàng đều tám | mặt"),
        # chỉ cắt cưỡng bức khi chạm hard_max
        if cur and (natural or en - cur[0] > hard_max):
            out.append(tuple(cur))
            cur = None
        if cur is None:
            cur = [st, en, tx]
        else:
            cur[1], cur[2] = en, cur[2] + " " + tx
    if cur:
        out.append(tuple(cur))
    return [(round(a, 2), round(b, 2), t.strip()) for a, b, t in out if t.strip()]


_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stt_cache.json")


def _cache_key(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(2_000_000))
    return h.hexdigest()[:20]


async def transcribe_segments(audio_path: str):
    """Có cache theo hash audio: chạy lại cùng video (duyệt kịch bản, làm lại) không phải nghe lại."""
    try:
        key = _cache_key(audio_path)
        cache = json.load(open(_CACHE, encoding="utf-8")) if os.path.exists(_CACHE) else {}
        if key in cache:
            return cache[key]["text"], [tuple(x) for x in cache[key]["segs"]]
    except Exception:
        key, cache = "", {}
    text, segs = await _transcribe_uncached(audio_path)
    if key and text:
        try:
            cache[key] = {"text": text, "segs": segs}
            for old in list(cache)[:-40]:
                cache.pop(old)
            json.dump(cache, open(_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return text, segs


async def _transcribe_uncached(audio_path: str):
    """Groq Whisper (có key) -> STT CapCut (không cần key). Video không lời thì trả ("", [])."""
    if config.GROQ_API_KEY:
        try:
            text, segs = await _groq(audio_path)
            if text:
                return text, segs
        except Exception as e:
            metrics.record("asr", "groq", ok=False, detail=str(e))
            logging.exception("Groq Whisper lỗi, thử STT CapCut")
    try:
        text, segs = await asyncio.to_thread(_capcut_sync, audio_path)
        metrics.record("asr", "capcut", fallback=bool(config.GROQ_API_KEY))
        return text, segs
    except Exception as e:
        metrics.record("asr", "capcut", ok=False, detail=str(e))
        logging.exception("Bóc lời thoại lỗi, bỏ qua transcript")
    return "", []


async def transcribe(audio_path: str) -> str:
    """Giữ tương thích: chỉ lấy text."""
    return (await transcribe_segments(audio_path))[0]
