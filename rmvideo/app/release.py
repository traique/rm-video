"""Báo lên phiên bản (bản 1.0.0): lần khởi động đầu tiên của một phiên bản mới, gửi tóm tắt thay đổi (các dòng «- …» đầu
mục phiên bản đó trong CHANGELOG.md) về Telegram (chat nhận cảnh báo) và Zoom (người nhận thông báo).

Nhớ RIÊNG từng kênh đã báo phiên bản nào (.version_announced.json cạnh bot): kênh gửi hỏng (chưa đặt chat id, chưa
nhắn bot Zoom lần nào...) thì lần khởi động sau báo lại, kênh đã nhận thì không báo lặp.
"""
from __future__ import annotations

import json
import logging
import os
import re

from rmvideo.app import notifier
from rmvideo.services import zoom
from rmvideo.version import VERSION

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHANGELOG = os.path.join(_ROOT, "CHANGELOG.md")
MAX_LINES = 8


def _state_path() -> str:
    return os.environ.get("VERSION_STATE") or os.path.join(_ROOT, ".version_announced.json")


def _load() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _state_path())


def notes(version: str = VERSION, path: str = CHANGELOG, limit: int = MAX_LINES) -> tuple[str, list[str]]:
    """(tiêu đề, các dòng tóm tắt) của mục «## <version> …» trong CHANGELOG: lấy các dòng «- …» liền đầu mục."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return "", []
    head = re.compile(rf"^##\s+v?{re.escape(version)}(?![\d.])\s*(?:[—–-]\s*(.*))?$")
    title, out, inside = "", [], False
    for ln in lines:
        m = head.match(ln)
        if m:
            title, inside = (m.group(1) or "").strip(), True
            continue
        if inside:
            if ln.startswith("## "):
                break
            if ln.startswith(("- ", "* ")):
                out.append(re.sub(r"\*\*|`", "", ln[2:]).strip())
                if len(out) >= limit:
                    break
            elif out and ln.strip() and not ln.startswith("  "):
                break                                    # hết khối tóm tắt đầu mục
    return title, out


def message(version: str = VERSION) -> str:
    title, lines = notes(version)
    body = "\n".join(f"• {x}" for x in lines) or "Xem chi tiết trong CHANGELOG.md."
    return f"🆕 Bot đã lên phiên bản {version}" + (f": {title}" if title else "") + f"\n{body}"


async def announce(version: str = VERSION) -> dict:
    """Báo phiên bản mới cho các kênh chưa nhận. Trả {kênh: "ok" | lý do} của các kênh vừa thử (rỗng = không cần báo)."""
    seen = _load()
    text = message(version)
    out: dict[str, str] = {}
    if seen.get("telegram") != version:
        out["telegram"] = await notifier.telegram_admin(text)
    if notifier.zoom_in_use() and seen.get("zoom") != version:
        out["zoom"] = await zoom.notify(zoom.admin_target(), text, f"rmvideo {version}")
    done = {k for k, v in out.items() if v == "ok"}
    if done:
        try:
            _save({**seen, **{k: version for k in done}})
        except OSError as e:
            logging.warning("Không ghi được trạng thái báo phiên bản: %s", e)
    for k, v in out.items():
        if v != "ok":
            logging.info("Chưa báo được phiên bản %s qua %s: %s (lần khởi động sau thử lại)", version, k, v)
    return out
