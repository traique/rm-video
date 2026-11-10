"""Kiểm tra định kỳ các dịch vụ ngoài mà bot phụ thuộc, để biết hỏng TRƯỚC khi job thật hỏng.

Mỗi HEALTH_CHECK_HOURS giờ (mặc định 6): thử ffmpeg, đĩa, YouTube (chỉ xem thông tin, không tải), Gemini,
Groq, CapCut TTS, edge-tts. Khâu nào hỏng (hoặc tỉ lệ rơi dự phòng 24 giờ qua > 20%) thì nhắn ADMIN_CHAT_ID.
Lệnh console `health` chạy ngay.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time

import config
import metrics

YT_TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # video công khai 19 giây, có từ 2005
FALLBACK_ALERT = 0.2


async def _timed(coro, timeout: float):
    return await asyncio.wait_for(coro, timeout)


async def check_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise RuntimeError("không tìm thấy ffmpeg/ffprobe")
    return "có"


async def check_disk():
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__))).free >> 20
    if free < 500:
        raise RuntimeError(f"ổ bot chỉ còn {free}MB")
    return f"trống {free}MB"


async def check_youtube():
    import core_video
    with tempfile.TemporaryDirectory() as td:
        d = await _timed(core_video.probe_formats(YT_TEST_URL, td), 90)
    if not d.get("options"):
        raise RuntimeError("không thấy định dạng tải nào")
    return f"{len(d['options'])} định dạng"


async def check_gemini():
    if not config.GEMINI_API_KEY:
        return None
    import prompt_engine
    out = await _timed(prompt_engine._call_gemini("Chỉ trả lời đúng một từ: ok"), 60)
    if not out.strip():
        raise RuntimeError("trả về rỗng")
    return config.GEMINI_MODEL


async def check_groq():
    if not config.GROQ_API_KEY:
        return None
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                         timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
    return "key hợp lệ"


async def check_capcut():
    if config.TTS_PROVIDER != "capcut":
        return None
    import tts_capcut
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.mp3")
        await _timed(tts_capcut.speak("Xin chào bà con", config.CAPCUT_VOICE_FEMALE, p, want_words=False), 90)
        if os.path.getsize(p) < 1000:
            raise RuntimeError("file giọng rỗng")
    return "đọc được"


async def check_edge():
    import core_video
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.mp3")
        await _timed(core_video._edge_once("Xin chào", p, config.VOICE_FEMALE), 60)
        if os.path.getsize(p) < 1000:
            raise RuntimeError("file giọng rỗng")
    return "đọc được"


CHECKS = [("ffmpeg", check_ffmpeg), ("Ổ đĩa", check_disk), ("YouTube", check_youtube), ("Gemini", check_gemini),
          ("Groq", check_groq), ("CapCut TTS", check_capcut), ("edge-tts", check_edge)]


async def run_checks() -> list:
    """[(tên, ok, chi tiết)]; khâu không cấu hình (thiếu key) bị bỏ qua."""
    out = []
    for name, fn in CHECKS:
        try:
            detail = await fn()
            if detail is not None:
                out.append((name, True, detail))
        except Exception as e:
            out.append((name, False, str(e)[:160] or type(e).__name__))
    return out


def report(results: list) -> str:
    lines = [("✅" if ok else "❌") + f" {name}: {detail}" for name, ok, detail in results]
    fb = metrics.summary(24)
    return "\n".join(lines) + "\n\nDịch vụ ngoài 24 giờ qua:\n" + fb


def needs_alert(results: list) -> bool:
    if any(not ok for _, ok, _ in results):
        return True
    st = metrics.stats(24)
    return any(metrics.fallback_rate(k) > FALLBACK_ALERT and k["total"] >= 5 for k in st.values())


class Monitor:
    """Gọi tick() định kỳ; chỉ báo admin khi trạng thái ĐỔI (hỏng mới / dự phòng tăng / đã hồi phục)."""

    def __init__(self, notify):
        self.notify = notify
        self.last_run = 0.0
        self.last_state = None

    async def tick(self, force: bool = False) -> str | None:
        hours = config.HEALTH_CHECK_HOURS
        if not force and (hours <= 0 or time.time() - self.last_run < hours * 3600):
            return None
        self.last_run = time.time()
        results = await run_checks()
        text = report(results)
        logging.info("Kiểm tra dịch vụ:\n%s", text)
        bad = frozenset(n for n, ok, _ in results if not ok)
        state = (bad, needs_alert(results) and not bad)
        if state != self.last_state:
            if bad:
                await self.notify("⚠️ Có dịch vụ ngoài đang hỏng\n\n" + text)
            elif state[1]:
                await self.notify("⚠️ Tỉ lệ rơi về dự phòng cao (dịch vụ chính có thể sắp hỏng)\n\n" + text)
            elif self.last_state is not None:
                await self.notify("✅ Các dịch vụ đã hồi phục\n\n" + text)
        self.last_state = state
        return text
