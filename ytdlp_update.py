"""Cập nhật yt-dlp an toàn (lệnh console `update_ytdlp`).

YouTube đổi client liên tục (năm 2026 đổi nhiều lần, ép SABR), yt-dlp cũ hơn ~90 ngày thường hỏng.
Cách làm: ghi lại phiên bản cũ -> pip install -U yt-dlp + yt-dlp-ejs -> thử xem thông tin một video YouTube
trong TIẾN TRÌNH RIÊNG (bot đang chạy vẫn giữ bản cũ đã nạp) -> lỗi thì cài lại bản cũ. Qua thì cần
khởi động lại bot để dùng bản mới.
"""
import asyncio
import sys
from importlib import metadata

import potoken_setup

SPEC = "yt-dlp[default,curl-cffi]"          # deno cài riêng bằng setup_youtube (xem requirements.txt)
TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
_PROBE = ("import sys, yt_dlp; y = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True, "
          "'allowed_extractors': ['default', '-generic']}); "
          "i = y.extract_info(sys.argv[1], download=False); print(yt_dlp.version.__version__, len(i.get('formats') or []))")


def installed_version() -> str:
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return ""


async def _probe() -> tuple[bool, str]:
    p = await asyncio.create_subprocess_exec(sys.executable, "-c", _PROBE, TEST_URL,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), 180)
    except asyncio.TimeoutError:
        p.kill()
        return False, "quá 180 giây"
    if p.returncode != 0:
        return False, err.decode(errors="ignore").strip().splitlines()[-1:][0] if err else f"mã {p.returncode}"
    return True, out.decode().strip()


async def update(log) -> bool:
    old = installed_version()
    log(f"yt-dlp hiện tại: {old or 'chưa cài'}. Đang cập nhật...")
    if not await potoken_setup._pip(["-U", SPEC], log, 600):
        log("❌ pip lỗi, giữ nguyên bản cũ.")
        return False
    ok, detail = await _probe()
    if ok:
        log(f"✅ yt-dlp mới chạy được với YouTube ({detail}). Khởi động lại bot để dùng bản mới.")
        return True
    log(f"❌ Bản mới không tải được YouTube ({detail[:200]}), cài lại bản {old}...")
    if old and await potoken_setup._pip([f"yt-dlp=={old}"], log, 600):
        log(f"Đã quay về yt-dlp {old}.")
    return False
