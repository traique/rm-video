"""Chuẩn hoá link kênh nguồn cho tự động đăng (Facebook / TikTok) — hàm thuần, không đọc cấu hình.

Trước đây nằm trong autopost.py; config.save_settings phải import trễ autopost để kiểm giá trị, tạo vòng phụ thuộc
config <-> autopost (và kéo autopost -> state -> hàng đợi vào notebook Kaggle). Giờ cả hai cùng dùng module này.
"""
from __future__ import annotations

import re
import urllib.parse

PLATFORMS = ("facebook", "tiktok")


def detect_platform(link: str) -> str:
    """Link thuộc Facebook hay TikTok? ("" nếu không nhận ra)."""
    s = (link or "").strip()
    if not s:
        return ""
    host = (urllib.parse.urlsplit(s if "://" in s else "https://" + s).hostname or "").lower()
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in ("facebook.com", "fb.com", "fb.watch") or host.endswith((".facebook.com", ".fb.com")):
        return "facebook"
    return ""


def normalize_tiktok(s: str) -> str:
    """'quetrai', '@quetrai', 'https://www.tiktok.com/@quetrai?lang=vi' -> https://www.tiktok.com/@quetrai.
    Link một video (…/video/123) không phải link kênh."""
    s = (s or "").strip()
    if not s:
        return ""
    if re.match(r"(?i)^(?:[\w-]+\.)*tiktok\.com/", s):
        s = "https://" + s
    if s.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(s)
        host = (parts.hostname or "").lower()
        if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
            raise ValueError(f"«{s}» không phải kênh TikTok")
        seg = [p for p in parts.path.split("/") if p]
        if not seg or not seg[0].startswith("@") or len(seg) > 1:
            raise ValueError(f"«{s}» là link video/trang khác, không phải link kênh TikTok (dạng tiktok.com/@tên)")
        name = seg[0][1:]
    else:
        name = s.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,24}", name):
        raise ValueError(f"«{s}» không phải tên kênh TikTok")
    return f"https://www.tiktok.com/@{name.lower()}"


def normalize_source(s: str) -> str:
    """'duynauan', '@duynauan', 'https://m.facebook.com/duynauan/reels' -> https://www.facebook.com/duynauan;
    profile.php?id=123 giữ id. Chỉ nhận kênh Facebook."""
    s = (s or "").strip()
    if not s:
        return ""
    if re.match(r"(?i)^(?:[\w-]+\.)*facebook\.com/", s):          # "facebook.com/x", "m.facebook.com/x"
        s = "https://" + s
    elif not s.startswith(("http://", "https://")):
        s = "https://www.facebook.com/" + s.lstrip("@/")
    parts = urllib.parse.urlsplit(s)
    host = (parts.hostname or "").lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com")):
        raise ValueError(f"«{s}» không phải kênh Facebook")
    seg = [p for p in parts.path.split("/") if p]
    if seg and seg[0] == "profile.php":
        pid = urllib.parse.parse_qs(parts.query).get("id", [""])[0]
        if not pid.isdigit():
            raise ValueError(f"«{s}» thiếu id kênh")
        return f"https://www.facebook.com/profile.php?id={pid}"
    if not seg or seg[0] in ("reel", "watch", "share", "videos", "groups", "events", "login"):
        raise ValueError(f"«{s}» là link video/nhóm, không phải link kênh")
    return "https://www.facebook.com/" + seg[0]
