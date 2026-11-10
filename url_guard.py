"""Chặn SSRF: chỉ nhận link của các nền tảng video được hỗ trợ và không bao giờ trỏ vào mạng nội bộ.

Mọi lối vào nhận link (Telegram, web tạo job, web upload, trình tải, link nhạc) và mọi chỗ gọi yt-dlp
đều đi qua đây. yt-dlp còn được tắt extractor "generic" (kẻ tải mọi URL bất kỳ).
"""
import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

# Tên miền gốc được phép (gồm mọi tên miền con: vt.tiktok.com, m.youtube.com, web.facebook.com...)
DEFAULT_DOMAINS = (
    "tiktok.com", "douyin.com", "iesdouyin.com",
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "facebook.com", "fb.watch", "fb.com",
    "instagram.com",
    "soundcloud.com",          # link nhạc cho chế độ 🎵
)

# yt-dlp: mọi extractor mặc định TRỪ generic (generic tải được bất kỳ trang/tệp nào => SSRF)
YTDLP_EXTRACTORS = ["default", "-generic"]


class BlockedURL(ValueError):
    """Link bị từ chối (sai nền tảng, IP nội bộ...). Thông điệp đọc được cho người dùng."""


def allowed_domains() -> tuple:
    extra = [d.strip().lower().lstrip(".") for d in os.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()]
    return DEFAULT_DOMAINS + tuple(extra)


def _host_allowed(host: str, domains) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _ip_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _parse(url: str):
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BlockedURL("Link không hợp lệ.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL("Link phải bắt đầu bằng http:// hoặc https://")
    if parts.username or parts.password:
        raise BlockedURL("Link không được chứa tên đăng nhập/mật khẩu.")
    if port not in (None, 80, 443):
        raise BlockedURL("Link không được dùng cổng lạ.")
    host = parts.hostname
    try:                                   # IP viết thẳng trong link (127.0.0.1, [::1]...)
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise BlockedURL("Không nhận link dạng địa chỉ IP, hãy dùng link gốc của nền tảng.")
    if not _host_allowed(host, allowed_domains()):
        raise BlockedURL("Chỉ nhận link TikTok, YouTube, Facebook, Instagram, Douyin, SoundCloud "
                         f"(«{host}» không nằm trong danh sách; thêm vào ALLOWED_DOMAINS nếu cần).")
    return url, host


def _check_addrs(host: str, addrs) -> None:
    if not addrs:
        raise BlockedURL(f"Không phân giải được tên miền «{host}».")
    for a in addrs:
        if not _ip_public(a):
            raise BlockedURL(f"«{host}» trỏ vào địa chỉ nội bộ ({a}), đã chặn.")


def check_url(url: str, resolve=socket.getaddrinfo) -> str:
    """Bản đồng bộ (dùng trong thread của yt-dlp). Trả lại URL đã gọt khoảng trắng hoặc ném BlockedURL."""
    url, host = _parse(url)
    try:
        addrs = {ai[4][0] for ai in resolve(host, None)}
    except OSError:
        raise BlockedURL(f"Không phân giải được tên miền «{host}».") from None
    _check_addrs(host, addrs)
    return url


async def check_url_async(url: str) -> str:
    """Bản bất đồng bộ cho handler Telegram/web (không chặn vòng lặp sự kiện khi hỏi DNS)."""
    url, host = _parse(url)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        raise BlockedURL(f"Không phân giải được tên miền «{host}».") from None
    _check_addrs(host, {ai[4][0] for ai in infos})
    return url
