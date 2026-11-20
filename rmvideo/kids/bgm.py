"""(Botkeep) Nhạc nền truyện bé tạo bằng Lyria RealTime (Gemini API, model thử nghiệm `lyria-realtime-exp`, free tier,
đo thật 19/11/2026: 24 giây nhạc trong 22 giây, 48 kHz stereo, chỉ nhạc không lời, có watermark SynthID).

Kho nhạc thay vì tạo mỗi video: mỗi KHUNG truyện có một «màu» (data.json plots[].mood: playful / curious / tender), mỗi màu
tối đa TRACKS_PER_MOOD bài ~100 giây. Bài được tạo dần (mỗi truyện thêm một bài cho màu đó tới khi đủ), lưu
assets/kids_bgm/lyria/ + library.json, rồi đẩy lên Kaggle thành dataset riêng tư `rmvideo-kids-bgm` (kaggle_api.dataset_push):
Kaggle gắn dataset vào /kaggle/input, không cần PUBLIC_URL, key Gemini không rời Botkeep. Lyria lỗi / hết lượt thì
không nhạc (video vẫn dựng như trước)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import wave
from collections.abc import Callable
from typing import Any

import config

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_DIR = os.environ.get("KIDS_BGM_LIB") or os.path.join(BASE, "assets", "kids_bgm", "lyria")
LIB_FILE = "library.json"
DATASET = "rmvideo-kids-bgm"
MODEL = "models/lyria-realtime-exp"
TRACK_SECS = 100                       # video tối đa 88 giây + đuôi
TRACKS_PER_MOOD = 3
RATE, CH = 48000, 2                    # Lyria trả PCM 16-bit 48 kHz stereo
TIMEOUT = 240                          # giây cho một bài (tạo theo thời gian thật ~100 giây + dự phòng)
STALL = 30                             # giây thêm cho bắt tay / dừng phiên ngoài TIMEOUT (xem stream)
DATASET_WAIT = 900
MOODS: dict[str, dict[str, Any]] = {
    "playful": {"vi": "vui tươi", "bpm": 104, "density": 0.4, "brightness": 0.55,
                "prompt": "playful cheerful children's storybook music, ukulele, glockenspiel, pizzicato strings, light "
                          "woodblock, bouncy, simple, bright, warm, gentle, no drums, instrumental"},
    "curious": {"vi": "tò mò, hồi hộp nhẹ", "bpm": 88, "density": 0.3, "brightness": 0.45,
                "prompt": "gentle curious children's storybook music, music box, soft piano, pizzicato strings, light "
                          "bells, a little mysterious but warm and safe, slow, no drums, instrumental"},
    "tender": {"vi": "dịu dàng, ấm áp", "bpm": 72, "density": 0.25, "brightness": 0.4,
               "prompt": "warm tender lullaby for children, soft piano, music box, soft string pad, gentle harp, calm, "
                         "hopeful, loving, slow, no drums, instrumental"},
}
DEFAULT_MOOD = "playful"
Log = Callable[[str], Any]


def mood_for(brief: dict, data: dict) -> str:
    """Màu nhạc theo khung truyện của đề (plots[].mood); không có khung / không ghi: playful."""
    from rmvideo.kids import story as story_mod
    plot = story_mod.by_id(data.get("plots") or [], brief.get("plot") or "")
    mood = str((plot or {}).get("mood") or "")
    return mood if mood in MOODS else DEFAULT_MOOD


def lib_path(lib_dir: str | None = None) -> str:
    return os.path.join(lib_dir or LIB_DIR, LIB_FILE)


def load_library(lib_dir: str | None = None) -> dict:
    """{"tracks": [{file, mood, secs, prompt, created}], "pushed": [file...], "dataset": slug, "recent": [file...]};
    chỉ giữ bài còn file trên đĩa."""
    d = lib_dir or LIB_DIR
    try:
        with open(lib_path(d), encoding="utf-8") as f:
            lib = json.load(f)
    except (OSError, ValueError):
        lib = {}
    lib.setdefault("tracks", [])
    lib["tracks"] = [t for t in lib["tracks"] if os.path.isfile(os.path.join(d, t.get("file", "")))]
    lib.setdefault("pushed", [])
    lib.setdefault("recent", [])
    return lib


def save_library(lib: dict, lib_dir: str | None = None) -> None:
    d = lib_dir or LIB_DIR
    os.makedirs(d, exist_ok=True)
    tmp = lib_path(d) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    os.replace(tmp, lib_path(d))


def tracks(lib: dict, mood: str) -> list[dict]:
    return [t for t in lib.get("tracks") or [] if t.get("mood") == mood]


def changed_since_push(lib: dict) -> bool:
    return sorted(t["file"] for t in lib.get("tracks") or []) != sorted(lib.get("pushed") or [])


def _client() -> Any:
    from google import genai
    if not config.GEMINI_API_KEY:
        raise RuntimeError("Chưa có GEMINI_API_KEY")
    return genai.Client(api_key=config.GEMINI_API_KEY, http_options={"api_version": "v1beta"})


async def stream(mood: str, secs: float, client: Any = None, log: Log = logging.info) -> bytes:
    """PCM 16-bit 48 kHz stereo dài ≥ secs giây từ Lyria RealTime (thời gian thật). Giới hạn cả phiên TIMEOUT + STALL giây:
    vòng nhận chỉ xét TIMEOUT khi có gói gửi về, kết nối đứng im (không gửi, không đóng) thì treo mãi — job truyện bé kẹt ở
    «chọn nhạc nền» (CI 20/11 treo 30 phút ở 94%, test luồng truyện bé gọi Lyria thật)."""
    try:
        return await asyncio.wait_for(_stream(mood, secs, client, log), TIMEOUT + STALL)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Lyria không trả nhạc sau {TIMEOUT + STALL} giây (kết nối đứng im)") from None


async def _stream(mood: str, secs: float, client: Any, log: Log) -> bytes:
    from google.genai import types
    m = MOODS[mood]
    client = client or _client()
    need = int(secs * RATE * CH * 2)
    chunks: list[bytes] = []
    got = 0
    t0 = time.time()
    async with client.aio.live.music.connect(model=MODEL) as session:
        await session.set_weighted_prompts(prompts=[types.WeightedPrompt(text=m["prompt"], weight=1.0)])
        await session.set_music_generation_config(config=types.LiveMusicGenerationConfig(
            bpm=m["bpm"], temperature=1.0, density=m["density"], brightness=m["brightness"]))
        await session.play()
        async for msg in session.receive():
            fp = getattr(msg, "filtered_prompt", None)
            if fp:
                log(f"Nhạc bé: Lyria lọc prompt ({str(fp)[:80]})")
            sc = getattr(msg, "server_content", None)
            for ch in (getattr(sc, "audio_chunks", None) or []):
                if ch.data:
                    chunks.append(ch.data)
                    got += len(ch.data)
            if got >= need or time.time() - t0 > TIMEOUT:
                break
        try:
            await session.stop()
        except Exception:
            pass
    if got < need * 0.5:
        raise RuntimeError(f"Lyria chỉ trả {got / RATE / CH / 2:.0f} giây nhạc")
    return b"".join(chunks)[:need]


async def generate(mood: str, lib_dir: str | None = None, secs: float = TRACK_SECS, client: Any = None,
                   log: Log = logging.info) -> dict:
    """Tạo một bài cho màu `mood` -> m4a 44,1 kHz stereo (vào 1,5 giây, ra 4 giây, -20 LUFS) trong kho; trả mục library."""
    from rmvideo.media import ff
    d = lib_dir or LIB_DIR
    os.makedirs(d, exist_ok=True)
    n = 1 + max([int(t["file"].rsplit("_", 1)[1].split(".")[0]) for t in tracks(load_library(d), mood)
                 if t["file"].rsplit("_", 1)[1].split(".")[0].isdigit()] or [0])
    name = f"{mood}_{n}.m4a"
    t0 = time.time()
    log(f"Nhạc bé: Lyria tạo bài {name} ({MOODS[mood]['vi']}, {secs:.0f} giây)...")
    pcm = await stream(mood, secs + 2, client, log)
    raw = os.path.join(d, name + ".wav")
    with wave.open(raw, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    out = os.path.join(d, name)
    fade = min(4.0, secs / 4)
    await ff._run_ffmpeg("-y", "-i", raw, "-af",
                         f"atrim=0:{secs:.2f},afade=t=in:d={min(1.5, secs / 4):.2f},afade=t=out:st={secs - fade:.2f}:d={fade:.2f},"
                         "loudnorm=I=-20:TP=-2:LRA=9", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", out)
    os.remove(raw)
    item = {"file": name, "mood": mood, "secs": round(secs, 1), "prompt": MOODS[mood]["prompt"],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    lib = load_library(d)
    lib["tracks"].append(item)
    save_library(lib, d)
    log(f"Nhạc bé: xong {name} trong {time.time() - t0:.0f} giây")
    return item


async def ensure(mood: str, n: int = TRACKS_PER_MOOD, lib_dir: str | None = None, client: Any = None,
                 log: Log = logging.info) -> list[dict]:
    """Đủ `n` bài cho màu `mood` (tạo thêm phần thiếu, tuần tự)."""
    d = lib_dir or LIB_DIR
    while len(tracks(load_library(d), mood)) < n:
        await generate(mood, d, client=client, log=log)
    return tracks(load_library(d), mood)


def push_dataset_sync(lib_dir: str | None = None, log: Log = logging.info) -> dict:
    """Đẩy cả kho lên Kaggle (dataset riêng tư DATASET, phiên bản mới xoá bản cũ), chờ sẵn sàng; ghi «pushed»."""
    import kaggle_api
    d = lib_dir or LIB_DIR
    lib = load_library(d)
    files = [os.path.join(d, t["file"]) for t in lib["tracks"]]
    if not files:
        raise RuntimeError("kho nhạc trống")
    log(f"Nhạc bé: đẩy {len(files)} bài lên Kaggle (dataset {DATASET})...")
    kaggle_api.dataset_push(DATASET, "rmvideo kids bgm", files, notes=f"{len(files)} bài Lyria")
    kaggle_api.dataset_wait(DATASET, timeout=DATASET_WAIT)
    lib = load_library(d)
    lib["pushed"] = sorted(t["file"] for t in lib["tracks"])
    lib["dataset"] = DATASET
    save_library(lib, d)
    log("Nhạc bé: dataset sẵn sàng")
    return lib


async def push_dataset(lib_dir: str | None = None, log: Log = logging.info) -> dict:
    return await asyncio.to_thread(push_dataset_sync, lib_dir, log)


async def pick(brief: dict, data: dict, rnd: random.Random | None = None, lib_dir: str | None = None,
               client: Any = None, log: Log = logging.info) -> dict | None:
    """Đầu vào nhạc cho Kaggle theo màu của khung truyện: {"mounted": dataset, "name": file, "kind": "audio", "dataset":
    slug} (+ "http" nếu có PUBLIC_URL). Kho thiếu thì tạo thêm MỘT bài (≈ 100 giây) rồi đẩy dataset; không có nhạc: None."""
    import kaggle_worker
    d = lib_dir or LIB_DIR
    mood = mood_for(brief, data)
    rnd = rnd or random.Random()
    have = tracks(load_library(d), mood)
    if len(have) < TRACKS_PER_MOOD:
        for attempt in range(2):                    # Lyria đứt kết nối giữa chừng (1006 abnormal closure, đo thật 20/11): thử lại một lần
            try:
                have = await ensure(mood, len(have) + 1, d, client, log)
                break
            except Exception as e:
                log(f"Nhạc bé: Lyria lỗi ({str(e)[:120]})" + (", thử lại" if attempt == 0 else ""))
    if not have:                                    # màu này chưa có bài nào: lấy bài màu khác còn hơn không nhạc
        have = [t for t in load_library(d).get("tracks") or []]
        if not have:
            return None
        log(f"Nhạc bé: chưa có bài màu «{mood}», dùng bài màu khác")
    lib = load_library(d)
    if changed_since_push(lib):
        try:
            lib = await push_dataset(d, log)
        except Exception as e:
            log(f"Nhạc bé: không đẩy được kho lên Kaggle ({str(e)[:120]})")
            pushed = set(lib.get("pushed") or [])
            have = [t for t in have if t["file"] in pushed]          # chỉ dùng bài Kaggle đã có
            if not have:
                return None
    recent = lib.get("recent") or []
    fresh = [t for t in have if t["file"] not in recent[-2:]] or have
    t = rnd.choice(fresh)
    lib["recent"] = [f for f in recent if f != t["file"]][-20:] + [t["file"]]
    save_library(lib, d)
    r: dict = {"mounted": DATASET, "name": t["file"], "kind": "audio", "dataset": lib.get("dataset") or DATASET}
    link = kaggle_worker.serve(os.path.join(d, t["file"]))
    if link:
        r["http"] = link
    return r


async def build(lib_dir: str | None = None, client: Any = None, log: Log = logging.info) -> str:
    """Lệnh console kids_bgm: làm đủ kho (mỗi màu TRACKS_PER_MOOD bài) rồi đẩy lên Kaggle. Trả tóm tắt."""
    d = lib_dir or LIB_DIR
    for mood in MOODS:
        await ensure(mood, TRACKS_PER_MOOD, d, client, log)
    lib = load_library(d)
    if changed_since_push(lib):
        lib = await push_dataset(d, log)
    return "kho nhạc: " + ", ".join(f"{m} {len(tracks(lib, m))} bài" for m in MOODS) + f"; dataset {lib.get('dataset') or 'chưa đẩy'}"
