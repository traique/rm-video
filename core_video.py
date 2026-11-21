"""Mặt tiền tương thích của tầng media — mã thật nằm trong rmvideo/media/ (Phase 6):

    ff           chạy ffmpeg/ffprobe theo job, thăm dò, cắt, tự kiểm bộ lọc, số lõi / RAM, font + logo
    fetch        tải nguồn (yt-dlp, tikwm, link rút gọn Facebook, nhạc qua HTTP), câu báo lỗi dễ hiểu
    tts_engine   đọc giọng edge / CapCut / Piper, dự phòng chéo, cache
    voiceplan    xếp giọng vào khe, ghép track, mốc từng từ cho phụ đề
    filtergraph  dựng chuỗi bộ lọc (thuần chuỗi)
    renderer     render, độ to hai lượt, nhạc nền, thay nhạc

Trước đây là MỘT tệp 1.802 dòng (tải video, giọng đọc, xếp giọng, đồ thị bộ lọc, render) và còn import cả
kaggle_worker. Tên cũ (`core_video.render`, `core_video.synth_clip_ex`, ...) vẫn dùng được; GHI một tên (kể cả
monkeypatch) chuyển tới đúng module đang dùng tên đó (rmvideo/compat.py).
"""
import asyncio  # noqa: F401  (test vá core_video.asyncio.sleep)
import url_guard  # noqa: F401  (test vá core_video.url_guard.check_url)
from rmvideo import compat
from rmvideo.media import ff, fetch, filtergraph, renderer, tts_engine, voiceplan

compat.install(__name__, compat.routes_of(ff, fetch, tts_engine, voiceplan, filtergraph, renderer))
