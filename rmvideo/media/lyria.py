"""(Botkeep) Kho nhạc nền tự tạo bằng Lyria RealTime (Gemini API, model thử nghiệm `lyria-realtime-exp`, free tier, đo thật
19/11/2026: 24 giây nhạc trong 22 giây, 48 kHz stereo, chỉ nhạc không lời, có watermark SynthID) — dùng chung cho truyện bé
(rmvideo/kids/bgm.py) và kênh bếp (bgm.py).

Kho thay vì tạo mỗi video: mỗi «màu» nhạc tối đa `per_mood` bài dài `track_secs` giây, tạo dần, lưu `<dir>/` + library.json,
đẩy lên Kaggle thành dataset riêng tư (kaggle_api.dataset_push): Kaggle gắn dataset vào /kaggle/input, không cần PUBLIC_URL,
key Gemini không rời Botkeep. Nhạc tự tạo nên không bị Facebook / TikTok / YouTube nhận diện bản quyền."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import config

MODEL = "models/lyria-realtime-exp"
RATE, CH = 48000, 2                    # Lyria trả PCM 16-bit 48 kHz stereo
LIB_FILE = "library.json"
DATASET_WAIT = 900
Log = Callable[[str], Any]


@dataclass
class Lib:
    """Một kho nhạc: thư mục, dataset Kaggle, các màu nhạc {tên: {vi, bpm, density, brightness, prompt}}."""
    dir: str
    dataset: str
    title: str
    moods: dict[str, dict[str, Any]]
    track_secs: float = 100.0
    per_mood: int = 3
    tag: str = "Nhạc"                  # đầu dòng log («Nhạc bé», «Nhạc bếp»)
    timeout: float = 240.0             # giây cho một bài (tạo theo thời gian thật + dự phòng)
    stall: float = 30.0                # thêm cho bắt tay / dừng phiên ngoài timeout
    fade_out: float = 4.0


def lib_path(d: str) -> str:
    return os.path.join(d, LIB_FILE)


def load_library(d: str) -> dict:
    """{"tracks": [{file, mood, secs, prompt, created}], "pushed": [file...], "dataset": slug, "recent": [file...]};
    chỉ giữ bài còn file trên đĩa."""
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


def save_library(lib: dict, d: str) -> None:
    os.makedirs(d, exist_ok=True)
    tmp = lib_path(d) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    os.replace(tmp, lib_path(d))


def tracks(lib: dict, mood: str) -> list[dict]:
    return [t for t in lib.get("tracks") or [] if t.get("mood") == mood]


def changed_since_push(lib: dict) -> bool:
    return sorted(t["file"] for t in lib.get("tracks") or []) != sorted(lib.get("pushed") or [])


def client() -> Any:
    from google import genai
    if not config.GEMINI_API_KEY:
        raise RuntimeError("Chưa có GEMINI_API_KEY")
    return genai.Client(api_key=config.GEMINI_API_KEY, http_options={"api_version": "v1beta"})


async def stream(m: dict, secs: float, cl: Any, log: Log = logging.info, timeout: float = 240.0, stall: float = 30.0,
                 tag: str = "Nhạc") -> bytes:
    """PCM 16-bit 48 kHz stereo dài ≥ secs giây từ Lyria RealTime (thời gian thật) theo màu `m`. Giới hạn cả phiên
    timeout + stall giây: vòng nhận chỉ xét timeout khi có gói gửi về, kết nối đứng im (không gửi, không đóng) thì treo mãi
    (CI 20/11 treo 30 phút ở 94%)."""
    try:
        return await asyncio.wait_for(_stream(m, secs, cl, log, timeout, tag), timeout + stall)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Lyria không trả nhạc sau {timeout + stall:.0f} giây (kết nối đứng im)") from None


async def _stream(m: dict, secs: float, cl: Any, log: Log, timeout: float, tag: str) -> bytes:
    from google.genai import types
    need = int(secs * RATE * CH * 2)
    chunks: list[bytes] = []
    got = 0
    t0 = time.time()
    async with cl.aio.live.music.connect(model=MODEL) as session:
        await session.set_weighted_prompts(prompts=[types.WeightedPrompt(text=m["prompt"], weight=1.0)])
        await session.set_music_generation_config(config=types.LiveMusicGenerationConfig(
            bpm=m["bpm"], temperature=1.0, density=m["density"], brightness=m["brightness"]))
        await session.play()
        async for msg in session.receive():
            fp = getattr(msg, "filtered_prompt", None)
            if fp:
                log(f"{tag}: Lyria lọc prompt ({str(fp)[:80]})")
            sc = getattr(msg, "server_content", None)
            for ch in (getattr(sc, "audio_chunks", None) or []):
                if ch.data:
                    chunks.append(ch.data)
                    got += len(ch.data)
            if got >= need or time.time() - t0 > timeout:
                break
        try:
            await session.stop()
        except Exception:
            pass
    if got < need * 0.5:
        raise RuntimeError(f"Lyria chỉ trả {got / RATE / CH / 2:.0f} giây nhạc")
    return b"".join(chunks)[:need]


async def generate(lib: Lib, mood: str, secs: float | None = None, cl: Any = None, log: Log = logging.info,
                   factory: Callable[[], Any] = client) -> dict:
    """Tạo một bài cho màu `mood` -> m4a 44,1 kHz stereo (vào 1,5 giây, ra lib.fade_out giây, -20 LUFS) trong kho; trả
    mục library. cl: client Lyria (None thì gọi factory() lúc cần tạo)."""
    from rmvideo.media import ff
    d = lib.dir
    secs = float(secs or lib.track_secs)
    os.makedirs(d, exist_ok=True)
    n = 1 + max([int(t["file"].rsplit("_", 1)[1].split(".")[0]) for t in tracks(load_library(d), mood)
                 if t["file"].rsplit("_", 1)[1].split(".")[0].isdigit()] or [0])
    name = f"{mood}_{n}.m4a"
    t0 = time.time()
    m = lib.moods[mood]
    log(f"{lib.tag}: Lyria tạo bài {name} ({m['vi']}, {secs:.0f} giây)...")
    pcm = await stream(m, secs + 2, cl or factory(), log, lib.timeout, lib.stall, lib.tag)
    raw = os.path.join(d, name + ".wav")
    with wave.open(raw, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    out = os.path.join(d, name)
    fade = min(lib.fade_out, secs / 4)
    await ff._run_ffmpeg("-y", "-i", raw, "-af",
                         f"atrim=0:{secs:.2f},afade=t=in:d={min(1.5, secs / 4):.2f},afade=t=out:st={secs - fade:.2f}:d={fade:.2f},"
                         "loudnorm=I=-20:TP=-2:LRA=9", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", out)
    os.remove(raw)
    item = {"file": name, "mood": mood, "secs": round(secs, 1), "prompt": m["prompt"],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data = load_library(d)
    data["tracks"].append(item)
    save_library(data, d)
    log(f"{lib.tag}: xong {name} trong {time.time() - t0:.0f} giây")
    return item


async def ensure(lib: Lib, mood: str, n: int | None = None, cl: Any = None, log: Log = logging.info,
                 factory: Callable[[], Any] = client) -> list[dict]:
    """Đủ `n` bài (mặc định lib.per_mood) cho màu `mood` (tạo thêm phần thiếu, tuần tự)."""
    n = lib.per_mood if n is None else n
    while len(tracks(load_library(lib.dir), mood)) < n:
        await generate(lib, mood, cl=cl, log=log, factory=factory)
    return tracks(load_library(lib.dir), mood)


def push_dataset_sync(lib: Lib, log: Log = logging.info) -> dict:
    """Đẩy cả kho lên Kaggle (dataset riêng tư lib.dataset, phiên bản mới thay bản cũ), chờ sẵn sàng; ghi «pushed»."""
    import kaggle_api
    data = load_library(lib.dir)
    files = [os.path.join(lib.dir, t["file"]) for t in data["tracks"]]
    if not files:
        raise RuntimeError("kho nhạc trống")
    log(f"{lib.tag}: đẩy {len(files)} bài lên Kaggle (dataset {lib.dataset})...")
    kaggle_api.dataset_push(lib.dataset, lib.title, files, notes=f"{len(files)} bài Lyria")
    kaggle_api.dataset_wait(lib.dataset, timeout=DATASET_WAIT)
    data = load_library(lib.dir)
    data["pushed"] = sorted(t["file"] for t in data["tracks"])
    data["dataset"] = lib.dataset
    save_library(data, lib.dir)
    log(f"{lib.tag}: dataset sẵn sàng")
    return data


def choose(lib: Lib, have: list[dict], rnd: random.Random | None = None, avoid: int = 2) -> dict:
    """Một bài trong `have`, tránh `avoid` bài vừa dùng; ghi vào «recent» của kho."""
    data = load_library(lib.dir)
    recent = data.get("recent") or []
    fresh = [t for t in have if t["file"] not in recent[-avoid:]] or have
    t = (rnd or random.Random()).choice(fresh)
    data["recent"] = [f for f in recent if f != t["file"]][-20:] + [t["file"]]
    save_library(data, lib.dir)
    return t


def kaggle_ref(lib: Lib, t: dict) -> dict:
    """Đầu vào nhạc cho Kaggle: file trong dataset đã gắn (+ link tạm Botkeep nếu có PUBLIC_URL, phòng dataset chưa kịp)."""
    import kaggle_worker
    data = load_library(lib.dir)
    r: dict = {"mounted": lib.dataset, "name": t["file"], "kind": "audio", "dataset": data.get("dataset") or lib.dataset}
    link = kaggle_worker.serve(os.path.join(lib.dir, t["file"]))
    if link:
        r["http"] = link
    return r


async def build(lib: Lib, cl: Any = None, log: Log = logging.info, factory: Callable[[], Any] = client,
                push: Callable[[], Awaitable[dict]] | None = None) -> str:
    """Làm đủ kho (mỗi màu lib.per_mood bài) rồi đẩy lên Kaggle (push: hàm đẩy; None = không đẩy). Trả tóm tắt."""
    for mood in lib.moods:
        await ensure(lib, mood, cl=cl, log=log, factory=factory)
    data = load_library(lib.dir)
    if push is not None and changed_since_push(data):
        data = await push()
    return ("kho nhạc: " + ", ".join(f"{m} {len(tracks(data, m))} bài" for m in lib.moods)
            + f"; dataset {data.get('dataset') or 'chưa đẩy'}")
