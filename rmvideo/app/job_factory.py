"""Một chỗ dựng Job từ mọi đường vào: form web, tải file lên, Telegram, tự động đăng, bản đã duyệt, phần cắt từ
video dài.

Trước đây 11 lời gọi `Job(...)` rải ở 6 tệp, mỗi nơi tự nhớ quy tắc (chế độ nào hợp lệ, "music_mix" chỉ cho 🎵,
thời lượng 30/60/90 hay chế độ video dài, giọng mặc định theo nhà cung cấp, đoạn đã duyệt mang những trường nào...).
Thêm một trường cho Job giờ chỉ sửa ở đây.
"""
from __future__ import annotations

import os
from typing import Any

import config
from queue_manager import Job
from rmvideo.domain.job import WEB_VIDEO_MODES, LongMode, Mode, Source

DURATIONS = (0, 30, 60, 90)
PHOTO_DURATIONS = ("0", "10", "15", "30", "60")     # 0 = tự động theo nhạc
LONG_MODES = tuple(m.value for m in LongMode if m.value)
APPROVED_KEYS = ("i", "start", "slot", "span", "text", "orig", "ost")


def parse_duration(v: Any) -> tuple[int, str]:
    """'30'/'60'/'90'/'0' -> (giây, ""); 'full'/'split60'/'split90'/'highlight' -> (0, chế độ video dài)."""
    v = str(v or "0").strip()
    if v in LONG_MODES:
        return 0, v
    try:
        d = int(v)
    except ValueError:
        return 0, ""
    return (d if d in DURATIONS else 0), ""


def pick_voice(v: str) -> str:
    """'male' / 'female' / '' -> giọng mặc định theo nhà cung cấp; còn lại là id giọng cụ thể (dropdown)."""
    if v == "male":
        return config.default_voice(male=True)
    if not v or v == "female":
        return config.default_voice()
    return v[:80]


def telegram_voice(mode: str) -> str:
    """Telegram không có ô chọn giọng: CapCut thì nữ cho nấu ăn, nam cho review; nhà cung cấp khác dùng giọng nữ."""
    if config.TTS_PROVIDER == "capcut":
        return config.CAPCUT_VOICE_FEMALE if mode == Mode.COOK else config.CAPCUT_VOICE_MALE
    return config.default_voice()


def video_mode(v: Any) -> str:
    """Chế độ từ form video (📸 đi đường ảnh riêng); lạ -> nấu ăn."""
    return str(v) if v in WEB_VIDEO_MODES else Mode.COOK.value


def approved_segments(segs: list) -> list:
    """Bản ghi đoạn đã chốt (duyệt kịch bản / làm lại phần trên Botkeep không gọi AI lần nữa) — một định dạng."""
    return [{"i": s["i"], "start": s["start"], "slot": s["slot"], "span": s.get("span", s["slot"]),
             "text": s["text"], "orig": s.get("orig", ""), "ost": s.get("ost", "new")} for s in segs]


def remake(*, mode: str, voice: str, source: str, url: str = "", local: str = "", duration: int = 0,
           long_mode: str = "", subs: bool = True, cover: bool = True, review: bool = False, quality: str = "",
           music_url: str = "", music_local: str = "", music_tg_file_id: str = "", music_mix: str = "",
           tg_file_id: str = "", title_hint: str = "", chat_id: int = 0, zoom: dict | None = None) -> Job:
    """Job làm lại video (nấu ăn / review / 🎵 / tải về) từ link, file tải lên hoặc file Telegram.
    zoom: nơi trả lời {to, account, user} khi job gửi từ Zoom (báo xong / lỗi vào đúng cuộc trò chuyện đó)."""
    mode = video_mode(mode)
    # file nhạc tải lên đi kèm job (kể cả khi không dùng) để worker xoá cùng đầu vào khi job xong
    return Job(url=url, mode=mode, duration=0 if long_mode else duration, voice=voice, subs=subs, cover=cover,
               long_mode=long_mode, local=local, tg_file_id=tg_file_id, review=review, quality=str(quality or "")[:8],
               music_url=music_url, music_local=music_local, music_tg_file_id=music_tg_file_id,
               music_mix="bg" if mode == Mode.MUSIC and music_mix == "bg" else "",
               title_hint=title_hint, source=source, chat_id=chat_id, zoom=dict(zoom or {}))


def download(*, source: str, url: str = "", quality: str = "", tg_file_id: str = "", title_hint: str = "",
             chat_id: int = 0, zoom: dict | None = None) -> Job:
    """Chỉ tải về (không làm lại). quality: mp3 | 2160..360 | '' (tốt nhất)."""
    return Job(url=url, mode=Mode.DOWNLOAD.value, duration=0, voice="", source=source, chat_id=chat_id,
               quality="" if quality in ("", "best") else str(quality)[:8], tg_file_id=tg_file_id, title_hint=title_hint,
               zoom=dict(zoom or {}))


def photo(*, source: str, duration: int, images: list | None = None, tg_images: list | None = None,
          music_url: str = "", music_local: str = "", music_tg_file_id: str = "", title_hint: str = "",
          chat_id: int = 0) -> Job:
    """📸 1-4 ảnh + nhạc."""
    return Job(url="", mode=Mode.PHOTO.value, duration=duration, voice="", source=source, chat_id=chat_id,
               images=list(images or []), tg_images=list(tg_images or []), music_url=music_url,
               music_local=music_local, music_tg_file_id=music_tg_file_id, title_hint=title_hint[:200])


def auto(*, url: str, title: str, duration: float, mode: str, voice: str, chat_id: int) -> Job:
    """🤖 Tự động đăng: video > 90 giây làm NGUYÊN video (không cắt 90 giây đầu)."""
    return Job(url=url, mode=mode, duration=0, voice=voice, source=Source.AUTO.value,
               long_mode=LongMode.FULL.value if duration > config.MAX_DURATION else "", chat_id=chat_id,
               title_hint=(title or "")[:200])


def kids(*, brief: dict, source: str, chat_id: int = 0, title_hint: str = "") -> Job:
    """🧸 Truyện bé: không có nguồn; đề đã chọn (rmvideo/kids/story.pick) nằm trong job.brief; giọng VieNeu kể chuyện
    (KIDS_VOICE), dựng trên Kaggle."""
    return Job(url="", mode=Mode.KIDS.value, duration=0, voice="vieneu:" + config.KIDS_VOICE, subs=True, cover=False,
               source=source, chat_id=chat_id, title_hint=title_hint[:200], brief=dict(brief or {}))


def piece(parent: Job, piece_path: str, title: str, remote_src: dict) -> Job:
    """Một phần cắt từ video dài: làm TRỌN phần đã cắt (long_mode=full), mang mã job cha (link tải, nhật ký)."""
    sub = Job(url="", mode=parent.mode, duration=0, voice=parent.voice, subs=parent.subs, cover=parent.cover,
              long_mode=LongMode.FULL.value, local=piece_path, title_hint=title, source=parent.source,
              chat_id=parent.chat_id, source_meta=parent.source_meta, remote_src=remote_src, zoom=dict(parent.zoom or {}))
    sub.id = parent.id
    return sub


def approved(src: Job, segs: list) -> Job:
    """Job render từ kịch bản đã duyệt: giữ nguồn đã tải (không tải lại), mốc cắt quảng cáo, đoạn mở đầu, chữ hook."""
    job = Job(url=src.url, mode=src.mode, duration=src.duration, voice=src.voice, subs=src.subs, cover=src.cover,
              long_mode=src.long_mode, local=src.local if src.local and os.path.exists(src.local) else "",
              tg_file_id=src.tg_file_id, title_hint=src.title_hint, music_url=src.music_url,
              music_local="", music_tg_file_id=src.music_tg_file_id, source=src.source, chat_id=src.chat_id,
              approved=segs, cut_at=src.cut_at, source_meta=src.source_meta, teaser=dict(src.teaser or {}),
              edit=dict(src.edit or {}), recipe=dict(src.recipe or {}), zoom=dict(src.zoom or {}))
    job.post = src.post
    job.hook = src.hook          # chữ hook in màn hình + ảnh bìa (trước đây bản duyệt mất chữ hook, lấy tiêu đề bài)
    return job
