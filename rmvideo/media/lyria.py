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
    if slow_left():
        raise Slow(f"Lyria tạm nghỉ thêm {slow_left() / 60:.0f} phút ({_SLOW['why']})")
    try:
        return await asyncio.wait_for(_stream(m, secs, cl, log, timeout, tag), timeout + stall)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Lyria không trả nhạc sau {timeout + stall:.0f} giây (kết nối đứng im)") from None


SESSIONS = 4                           # phiên Lyria tối đa cho một bài (phiên đóng giữa chừng thì mở phiên mới nối tiếp)
# Log 24/11 job 2 (sau 1.4.4): phiên đầu chỉ nhận 4 giây nhạc trong 51 giây rồi đứt. Luồng Lyria là PCM 48 kHz stereo
# (~190 KB/s, gửi dạng base64 còn nặng hơn) mà mạng Botkeep chỉ ~80-100 KB/s (pip, tải video Kaggle): máy chủ không đẩy kịp
# thì cắt kết nối. Nhận chậm hơn SLOW_RATE x thời gian thật thì thôi luôn (khỏi nối 4 phiên x 2 lần ≈ 7 phút mỗi job) và
# nghỉ Lyria SLOW_COOLDOWN giây.
SLOW_RATE = 0.3                        # 0: không xét chậm (notebook nhạc trên Kaggle: không có đường lùi, cứ nối phiên)
SLOW_MIN = 20.0                        # phiên ngắn hơn chừng này giây chưa đủ để kết luận chậm
SLOW_COOLDOWN = 6 * 3600
CHUNK_STALL = 30.0                     # giây không nhận gói nào: coi phiên đứng im, ngắt (giữ phần đã nhận), nối phiên mới
_SLOW: dict[str, Any] = {"until": 0.0, "why": ""}


class Slow(RuntimeError):
    """Lyria trả nhạc chậm hơn nhiều so với thời gian thật (mạng không theo kịp): thử lại vô ích."""


def slow_left() -> float:
    """Còn bao nhiêu giây nghỉ Lyria (0: dùng được)."""
    return max(0.0, _SLOW["until"] - time.time())


def _mark_slow(why: str) -> None:
    _SLOW["until"], _SLOW["why"] = time.time() + SLOW_COOLDOWN, why
XFADE = 1.5                            # giây hoà tiếng giữa hai phiên
MIN_PART = 0.4                         # nhận được ít nhất chừng này phần độ dài cần thì vẫn dùng (nhạc nền tự lặp)


def _xfade(a: bytes, b: bytes, secs: float = XFADE) -> bytes:
    """Nối hai khúc PCM 16-bit stereo, hoà tiếng `secs` giây ở chỗ nối (khỏi «bụp» giữa hai phiên)."""
    from array import array
    n = min(int(secs * RATE) * CH, len(a) // 2, len(b) // 2)
    n -= n % CH
    if n <= 0:
        return a + b
    ta, tb = array("h"), array("h")
    ta.frombytes(a[len(a) - n * 2:])
    tb.frombytes(b[:n * 2])
    frames = n // CH
    out = array("h", [int(ta[i] + (tb[i] - ta[i]) * (i // CH) / frames) for i in range(n)])
    return a[:len(a) - n * 2] + out.tobytes() + b[n * 2:]


async def _stream(m: dict, secs: float, cl: Any, log: Log, timeout: float, tag: str) -> bytes:
    """Log 22-24/11 (job 13, job 1 truyện bé): mỗi phiên đóng «1006 abnormal closure» sau ~51 giây, cả hai lần thử đều
    vứt hết phần đã nhận -> kho nhạc bé trống, video không nhạc. Giờ giữ phần đã nhận, mở phiên mới nối tiếp (hoà tiếng
    XFADE giây), đủ MIN_PART độ dài thì dùng được (nhạc nền lặp lại khi dựng)."""
    need = int(secs * RATE * CH * 2)
    pcm = b""
    t0 = time.time()
    err: Exception | None = None
    for k in range(SESSIONS):
        part: list[bytes] = []
        ts = time.time()
        try:
            await _session(m, cl, log, tag, part, need - len(pcm) + (int(XFADE * RATE) * CH * 2 if pcm else 0),
                           t0 + timeout)
        except Exception as e:
            err = e
        got = b"".join(part)
        if got:
            pcm = _xfade(pcm, got) if pcm else got
        if len(pcm) >= need or time.time() - t0 > timeout:
            break
        dt, gs = time.time() - ts, len(got) / (RATE * CH * 2)
        if SLOW_RATE > 0 and dt >= SLOW_MIN and gs < SLOW_RATE * dt:
            why = f"chỉ nhận {gs:.0f} giây nhạc trong {dt:.0f} giây — mạng không theo kịp luồng nhạc Lyria"
            _mark_slow(why)
            if len(pcm) >= need * MIN_PART:
                break
            raise Slow(why)
        if err is not None and not got and k:          # phiên mới không trả gì: thôi
            break
        if err is not None:
            log(f"{tag}: Lyria đứt ở {len(pcm) / RATE / CH / 2:.0f}/{secs:.0f} giây ({str(err)[:80]}), nối phiên mới")
        err = None
    if len(pcm) < need * MIN_PART:
        if err is not None:
            raise err
        raise RuntimeError(f"Lyria chỉ trả {len(pcm) / RATE / CH / 2:.0f} giây nhạc")
    return pcm[:need]


async def _session(m: dict, cl: Any, log: Log, tag: str, part: list[bytes], need: int, deadline: float) -> None:
    """Một phiên Lyria: ghi dần khúc PCM vào `part` (lỗi giữa chừng thì phần đã ghi vẫn còn). Không có gói nào trong
    CHUNK_STALL giây (notebook nhạc 24/11: một bài «kết nối đứng im» 250 giây, mất cả phần đã nhận) hay nhận chậm rõ
    rệt (mạng Botkeep) thì ngắt ngay để _stream nối phiên mới / kết luận chậm."""
    from google.genai import types
    got, t0 = 0, time.time()
    async with cl.aio.live.music.connect(model=MODEL) as session:
        await session.set_weighted_prompts(prompts=[types.WeightedPrompt(text=m["prompt"], weight=1.0)])
        await session.set_music_generation_config(config=types.LiveMusicGenerationConfig(
            bpm=m["bpm"], temperature=1.0, density=m["density"], brightness=m["brightness"]))
        await session.play()
        it = session.receive().__aiter__()
        while True:
            try:
                msg = await asyncio.wait_for(it.__anext__(), CHUNK_STALL)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise RuntimeError(f"Lyria không gửi gói nào trong {CHUNK_STALL:.0f} giây") from None
            fp = getattr(msg, "filtered_prompt", None)
            if fp:
                log(f"{tag}: Lyria lọc prompt ({str(fp)[:80]})")
            sc = getattr(msg, "server_content", None)
            for ch in (getattr(sc, "audio_chunks", None) or []):
                if ch.data:
                    part.append(ch.data)
                    got += len(ch.data)
            el = time.time() - t0
            if got >= need or time.time() > deadline:
                break
            if SLOW_RATE > 0 and el >= SLOW_MIN and got < SLOW_RATE * el * RATE * CH * 2:
                raise RuntimeError(f"nhận {got / RATE / CH / 2:.0f} giây nhạc trong {el:.0f} giây")
        try:
            await session.stop()
        except Exception:
            pass


async def generate(lib: Lib, mood: str, secs: float | None = None, cl: Any = None, log: Log = logging.info,
                   factory: Callable[[], Any] = client) -> dict:
    """Tạo một bài cho màu `mood` -> m4a 44,1 kHz stereo (vào 1,5 giây, ra lib.fade_out giây, -20 LUFS) trong kho; trả
    mục library. cl: client Lyria (None thì gọi factory() lúc cần tạo)."""
    from rmvideo.media import ff
    d = lib.dir
    secs = float(secs or lib.track_secs)
    os.makedirs(d, exist_ok=True)
    name = next_names(d, mood, 1)[0]
    t0 = time.time()
    m = lib.moods[mood]
    if slow_left():                                    # vừa thấy mạng không theo kịp: khỏi mở phiên, khỏi ghi log «tạo bài»
        raise Slow(f"Lyria tạm nghỉ thêm {slow_left() / 60:.0f} phút ({_SLOW['why']})")
    log(f"{lib.tag}: Lyria tạo bài {name} ({m['vi']}, {secs:.0f} giây)...")
    pcm = await stream(m, secs + 2, cl or factory(), log, lib.timeout, lib.stall, lib.tag)
    secs = min(secs, len(pcm) / (RATE * CH * 2))       # phiên đứt sớm: bài ngắn hơn (nhạc nền tự lặp khi dựng)
    raw = os.path.join(d, name + ".wav")
    with wave.open(raw, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    out = os.path.join(d, name)
    await ff._run_ffmpeg(*encode_args(raw, out, secs, lib.fade_out))
    os.remove(raw)
    item = add_track(d, name, mood, secs, m["prompt"])
    log(f"{lib.tag}: xong {name} trong {time.time() - t0:.0f} giây")
    return item


def encode_args(raw: str, out: str, secs: float, fade_out: float) -> list[str]:
    """Đối số ffmpeg: PCM wav -> m4a 44,1 kHz stereo 128k (vào 1,5 giây, ra fade_out giây, -20 LUFS). Dùng chung cho
    notebook nhạc trên Kaggle (rmvideo/media/lyria_kaggle.py)."""
    fade = min(fade_out, secs / 4)
    return ["-y", "-i", raw, "-af",
            f"atrim=0:{secs:.2f},afade=t=in:d={min(1.5, secs / 4):.2f},afade=t=out:st={secs - fade:.2f}:d={fade:.2f},"
            "loudnorm=I=-20:TP=-2:LRA=9", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", out]


def add_track(d: str, name: str, mood: str, secs: float, prompt: str, **extra: Any) -> dict:
    """Ghi một bài (file đã nằm trong kho `d`) vào library.json; trả mục vừa ghi."""
    item = {"file": name, "mood": mood, "secs": round(secs, 1), "prompt": prompt,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}
    data = load_library(d)
    data["tracks"] = [t for t in data["tracks"] if t.get("file") != name] + [item]
    save_library(data, d)
    return item


def next_names(d: str, mood: str, n: int) -> list[str]:
    """Tên `n` bài kế tiếp của màu `mood` trong kho (mood_<số>.m4a, số tiếp theo số lớn nhất đang có)."""
    nums = [int(t["file"].rsplit("_", 1)[1].split(".")[0]) for t in tracks(load_library(d), mood)
            if t["file"].rsplit("_", 1)[1].split(".")[0].isdigit()]
    top = max(nums or [0])
    return [f"{mood}_{top + i}.m4a" for i in range(1, n + 1)]


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
