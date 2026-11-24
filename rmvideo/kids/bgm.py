"""(Botkeep) Nhạc nền truyện bé tạo bằng Lyria RealTime (Gemini API, model thử nghiệm `lyria-realtime-exp`, free tier,
đo thật 19/11/2026: 24 giây nhạc trong 22 giây, 48 kHz stereo, chỉ nhạc không lời, có watermark SynthID).

Kho nhạc thay vì tạo mỗi video: mỗi KHUNG truyện có một «màu» (data.json plots[].mood: playful / curious / tender), mỗi màu
tối đa TRACKS_PER_MOOD bài ~100 giây. Bài được tạo dần (mỗi truyện thêm một bài cho màu đó tới khi đủ), lưu
assets/kids_bgm/lyria/ + library.json, rồi đẩy lên Kaggle thành dataset riêng tư `rmvideo-kids-bgm` (kaggle_api.dataset_push):
Kaggle gắn dataset vào /kaggle/input, không cần PUBLIC_URL, key Gemini không rời Botkeep. Lyria lỗi / hết lượt thì
không nhạc (video vẫn dựng như trước).

Phần lõi (tạo bài, kho, đẩy dataset) dùng chung với kênh bếp: rmvideo/media/lyria.py; module này giữ giao diện cũ."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Callable
from typing import Any

from rmvideo.media import lyria

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_DIR = os.environ.get("KIDS_BGM_LIB") or os.path.join(BASE, "assets", "kids_bgm", "lyria")
LIB_FILE = lyria.LIB_FILE
DATASET = "rmvideo-kids-bgm"
MODEL = lyria.MODEL
TRACK_SECS = 100                       # video tối đa 88 giây + đuôi
TRACKS_PER_MOOD = 3
RATE, CH = lyria.RATE, lyria.CH
TIMEOUT = 240                          # giây cho một bài (tạo theo thời gian thật ~100 giây + dự phòng)
STALL = 30                             # giây thêm cho bắt tay / dừng phiên ngoài TIMEOUT (xem stream)
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


def _lib(lib_dir: str | None = None) -> lyria.Lib:
    """Kho truyện bé theo các hằng số của module (đọc lúc gọi: test đổi TRACK_SECS / TIMEOUT vẫn có tác dụng)."""
    return lyria.Lib(dir=lib_dir or LIB_DIR, dataset=DATASET, title="rmvideo kids bgm", moods=MOODS, track_secs=TRACK_SECS,
                     per_mood=TRACKS_PER_MOOD, tag="Nhạc bé", timeout=TIMEOUT, stall=STALL)


def mood_for(brief: dict, data: dict) -> str:
    """Màu nhạc theo ý truyện đã chọn (pitch.mood, 1.4.1), không có thì theo khung truyện của đề (plots[].mood); không
    có khung / không ghi: playful."""
    from rmvideo.kids import story as story_mod
    pm = str((brief.get("pitch") or {}).get("mood") or "")
    if pm in MOODS:
        return pm
    plot = story_mod.by_id(data.get("plots") or [], brief.get("plot") or "")
    mood = str((plot or {}).get("mood") or "")
    return mood if mood in MOODS else DEFAULT_MOOD


def lib_path(lib_dir: str | None = None) -> str:
    return lyria.lib_path(lib_dir or LIB_DIR)


def load_library(lib_dir: str | None = None) -> dict:
    return lyria.load_library(lib_dir or LIB_DIR)


def save_library(lib: dict, lib_dir: str | None = None) -> None:
    lyria.save_library(lib, lib_dir or LIB_DIR)


tracks = lyria.tracks
changed_since_push = lyria.changed_since_push


def _client() -> Any:
    return lyria.client()


async def stream(mood: str, secs: float, client: Any = None, log: Log = logging.info) -> bytes:
    """PCM 16-bit 48 kHz stereo dài ≥ secs giây (xem lyria.stream: cả phiên giới hạn TIMEOUT + STALL giây)."""
    return await lyria.stream(MOODS[mood], secs, client or _client(), log, TIMEOUT, STALL, "Nhạc bé")


async def generate(mood: str, lib_dir: str | None = None, secs: float = 0.0, client: Any = None,
                   log: Log = logging.info) -> dict:
    """Tạo một bài cho màu `mood` -> m4a 44,1 kHz stereo trong kho; trả mục library."""
    return await lyria.generate(_lib(lib_dir), mood, secs or TRACK_SECS, client, log, factory=lambda: _client())


async def ensure(mood: str, n: int = 0, lib_dir: str | None = None, client: Any = None,
                 log: Log = logging.info) -> list[dict]:
    """Đủ `n` bài cho màu `mood` (tạo thêm phần thiếu, tuần tự)."""
    return await lyria.ensure(_lib(lib_dir), mood, n or TRACKS_PER_MOOD, client, log, factory=lambda: _client())


def push_dataset_sync(lib_dir: str | None = None, log: Log = logging.info) -> dict:
    """Đẩy cả kho lên Kaggle (dataset riêng tư DATASET), chờ sẵn sàng; ghi «pushed»."""
    return lyria.push_dataset_sync(_lib(lib_dir), log)


async def push_dataset(lib_dir: str | None = None, log: Log = logging.info) -> dict:
    return await asyncio.to_thread(push_dataset_sync, lib_dir, log)


async def pick(brief: dict, data: dict, rnd: random.Random | None = None, lib_dir: str | None = None,
               client: Any = None, log: Log = logging.info) -> dict | None:
    """Đầu vào nhạc cho Kaggle theo màu của khung truyện: {"mounted": dataset, "name": file, "kind": "audio", "dataset":
    slug} (+ "http" nếu có PUBLIC_URL). Kho thiếu thì tạo thêm MỘT bài (≈ 100 giây) rồi đẩy dataset; không có nhạc: None."""
    d = lib_dir or LIB_DIR
    lib = _lib(d)
    mood = mood_for(brief, data)
    have = tracks(load_library(d), mood)
    from rmvideo.media import lyria_kaggle
    if len(have) < TRACKS_PER_MOOD and lyria_kaggle.proven():      # Kaggle đã tạo được nhạc: nhờ Kaggle, khỏi thử Botkeep
        msg = lyria_kaggle.start_later(why=f"kho bé thiếu bài màu «{mood}»")
        if msg:
            log(f"Nhạc bé: {msg}")
    elif len(have) < TRACKS_PER_MOOD:
        for attempt in range(2):                    # Lyria đứt kết nối giữa chừng (1006 abnormal closure, đo thật 20/11): thử lại một lần
            try:
                have = await ensure(mood, len(have) + 1, d, client, log)
                break
            except lyria.Slow as e:                  # mạng chậm: thử lại vô ích -> nhờ Kaggle tạo kho (24/11, cách 1)
                log(f"Nhạc bé: bỏ qua Lyria ({str(e)[:140]})")
                msg = lyria_kaggle.start_later(why="Lyria trên Botkeep quá chậm")
                if msg:
                    log(f"Nhạc bé: {msg}")
                break
            except Exception as e:
                log(f"Nhạc bé: Lyria lỗi ({str(e)[:120]})" + (", thử lại" if attempt == 0 else ""))
    if not have:                                    # màu này chưa có bài nào: lấy bài màu khác còn hơn không nhạc
        have = [t for t in load_library(d).get("tracks") or []]
        if not have:
            return None
        log(f"Nhạc bé: chưa có bài màu «{mood}», dùng bài màu khác")
    data_lib = load_library(d)
    if changed_since_push(data_lib):
        try:
            data_lib = await push_dataset(d, log)
        except Exception as e:
            log(f"Nhạc bé: không đẩy được kho lên Kaggle ({str(e)[:120]})")
            pushed = set(data_lib.get("pushed") or [])
            have = [t for t in have if t["file"] in pushed]          # chỉ dùng bài Kaggle đã có
            if not have:
                return None
    return lyria.kaggle_ref(lib, lyria.choose(lib, have, rnd))


async def build(lib_dir: str | None = None, client: Any = None, log: Log = logging.info) -> str:
    """Lệnh console kids_bgm: làm đủ kho (mỗi màu TRACKS_PER_MOOD bài) rồi đẩy lên Kaggle. Trả tóm tắt."""
    d = lib_dir or LIB_DIR
    return await lyria.build(_lib(d), client, log, factory=lambda: _client(), push=lambda: push_dataset(d, log))
