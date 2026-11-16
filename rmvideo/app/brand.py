"""Nhận diện kênh (bản 42) phía Botkeep: số tập của loạt video (ảnh bìa), câu chốt cuối video.

Phần áp lên hình / tiếng (bộ màu, màu kênh, màn kết, âm hiệu) nằm trong rmvideo/media (chạy cả trên Kaggle)."""
from __future__ import annotations

import json
import os
import threading

import config
from rmvideo.media import ff

STATE_FILE = os.environ.get("BRAND_STATE") or os.path.join(ff.ROOT, "brand_state.json")
_LOCK = threading.Lock()


def series_label(src_hash: str, path: str | None = None) -> str:
    """"TÊN LOẠT #12" cho ảnh bìa (BRAND_SERIES + COVER_TEMPLATE=kenh). Số tập gắn theo VIDEO NGUỒN: dựng lại / bản
    đã duyệt giữ đúng số cũ, video mới thì số kế tiếp."""
    name = (config.BRAND_SERIES or "").strip()
    if not name or config.COVER_TEMPLATE != "kenh":
        return ""
    path = path or STATE_FILE
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        by = st.setdefault("by_src", {})
        if src_hash not in by:
            by[src_hash] = int(st.get("next", 1))
            st["next"] = by[src_hash] + 1
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f)
            os.replace(tmp, path)
        n = by[src_hash]
    return f"{name.upper()} #{n}"


def close_line() -> str:
    """Câu chốt đọc cuối video (BRAND_CLOSE); bỏ nếu là lời kêu gọi đăng ký / theo dõi (người dùng không muốn có)."""
    import prompt_engine
    s = " ".join((config.BRAND_CLOSE or "").split())
    if not s or prompt_engine.has_sub_cta(s):
        return ""
    return s if s[-1] in ".!?…" else s + "."


def add_close(segs: list) -> bool:
    """Gắn câu chốt vào đoạn đọc cuối cùng (bản viết mới, chưa duyệt). True nếu đã gắn."""
    line = close_line()
    if not line:
        return False
    for sg in reversed(segs):
        if sg.get("ost") != "orig" and (sg.get("text") or "").strip():
            if line.lower() not in sg["text"].lower():
                sg["text"] = sg["text"].rstrip() + " " + line
            return True
    return False
