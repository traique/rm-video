"""Nhận diện kênh (bản 42) phía Botkeep: số tập của loạt video (ảnh bìa), câu chốt cuối video, màn kết nối thêm sau
video, áp bộ nhận diện mặc định «Bếp Đong Đủ» (bản 43).

Phần áp lên hình / tiếng (bộ màu, màu kênh, màn kết, âm hiệu) nằm trong rmvideo/media (chạy cả trên Kaggle)."""
from __future__ import annotations

import json
import os
import re
import threading

import logging

import config
from rmvideo.media import ff, renderer

STATE_FILE = os.environ.get("BRAND_STATE") or os.path.join(ff.ROOT, "brand_state.json")
_LOCK = threading.Lock()


def series_label(src_hash: str, path: str | None = None) -> str:
    """"TÊN LOẠT #12" cho ảnh bìa (BRAND_SERIES + COVER_TEMPLATE=kenh). Số tập gắn theo VIDEO NGUỒN: dựng lại / bản
    đã duyệt giữ đúng số cũ, video mới thì số kế tiếp."""
    name = config.brand_text("BRAND_SERIES")
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
    s = " ".join(config.brand_text("BRAND_CLOSE").split())
    if not s or prompt_engine.has_sub_cta(s):
        return ""
    return s if s[-1] in ".!?…" else s + "."


# lời mời làm thử AI tự viết ở cuối ("…, cả nhà tranh thủ làm thử nha."): câu chốt của kênh đã mời rồi
_TRY_RE = re.compile(r"[,;]?\s*(?:(?:cả nhà|mọi người|bà con|các bạn)\s+)?(?:(?:nhớ|tranh thủ|hãy|cùng)\s+)?(?:làm|nấu)"
                     r"\s+thử(?:\s+(?:liền|ngay|luôn|đi|món này|nghen))?(?:\s+(?:nha|nhé|nè))?\s*(?=[.!?…]|$)",
                     re.IGNORECASE)


def _drop_try(text: str) -> str:
    """Bỏ lời mời làm thử. Cả đoạn chỉ là lời mời ("Cả nhà làm thử nha.") -> "" (câu chốt thay chỗ nó); còn lại một
    chữ lẻ thì giữ nguyên đoạn."""
    out = _TRY_RE.sub("", text or "").strip()
    out = re.sub(r"\s+([.!?…])", r"\1", out).lstrip(",; ")
    if not re.search(r"\w", out):
        return ""
    return out if len(out.split()) >= 2 else text


def add_close(segs: list) -> bool:
    """Gắn câu chốt vào đoạn đọc cuối cùng (bản viết mới, chưa duyệt). True nếu đã gắn. Câu chốt có mời nấu thử thì bỏ
    lời mời AI tự viết ở hai đoạn cuối (bản 48: "…cả nhà tranh thủ làm thử nha. Dễ làm nha. Đong đủ là ngon, cả nhà nấu
    thử nha." — hai lời mời liền nhau)."""
    line = close_line()
    if not line:
        return False
    spoken = [sg for sg in segs if sg.get("ost") != "orig" and (sg.get("text") or "").strip()]
    if not spoken:
        return False
    if re.search(r"\b(làm|nấu)\s+thử\b", line, re.IGNORECASE):
        for sg in spoken[-2:]:
            if line.lower() not in sg["text"].lower():
                cut = _drop_try(sg["text"])
                # đoạn áp chót chỉ có lời mời: giữ (không để đoạn đọc trống); đoạn cuối: câu chốt thay chỗ
                if cut or sg is spoken[-1]:
                    sg["text"] = cut
    sg = spoken[-1]
    if line.lower() not in sg["text"].lower():
        sg["text"] = (sg["text"].rstrip() + " " + line).strip()
    return True


# ---------------- màn kết nối thêm (bản 43) ----------------

def end_tail(content: float, cap: float) -> float:
    """Số giây màn kết NỐI THÊM sau video (giữ khung cuối, tối đi, tên kênh + câu dưới tên + âm hiệu). Trước bản 43 màn
    kết đè lên 2,2 giây cuối: che mất cảnh món thành phẩm và câu đọc cuối bị âm hiệu chen vào. 0 khi không có tên kênh,
    video quá ngắn, hoặc thêm vào sẽ vượt `cap` (giới hạn Reels 90 giây / giới hạn độ dài) — khi đó màn kết đè như cũ."""
    t = tail_len()
    return t if t and content > 6 and content + t <= cap + 0.01 else 0.0


def tail_len() -> float:
    """Độ dài màn kết nối thêm nếu có tên kênh (để chừa chỗ trước, vd. bản ngắn ≤ 90 giây)."""
    return renderer.OUTRO_DUR if config.brand_text("BRAND_NAME") else 0.0


# ---------------- bộ nhận diện mặc định (bản 43) ----------------

KIT_ID = "bep_dong_du@43"
# mặc định TRƯỚC bản 43 của các khoá nay mặc định theo bộ «Bếp Đong Đủ» (rmvideo/app/settings.py)
OLD_DEFAULTS = {"BRAND_NAME": "", "BRAND_TAGLINE": "", "BRAND_CLOSE": "", "BRAND_COLOR": "", "BRAND_LOOK": "goc",
                "BRAND_SERIES": "", "COVER_TEMPLATE": "goc", "POST_RECIPE": "0", "TIPS": "off", "REMAKE_STYLE": "off",
                "SHORT_VERSION": "off"}


def apply_default_kit(path: str | None = None) -> list:
    """Chạy MỘT lần lúc khởi động: trang cài đặt lưu cả các ô chọn để nguyên mặc định, nên đổi mặc định trong mã không
    tới được máy đã từng bấm Lưu. Khoá nào đang đúng mặc định CŨ thì bỏ bản lưu để dùng mặc định mới (bộ «Bếp Đong
    Đủ»); khoá bạn đã chọn khác thì giữ nguyên. Trả danh sách khoá đã chuyển."""
    from rmvideo.app import settings as schema
    path = path or STATE_FILE
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        if st.get("kit") == KIT_ID:
            return []
        try:
            over = schema.read_overrides(config.SETTINGS_FILE)
        except Exception as e:                        # settings.json hỏng: để lần sau, không đánh dấu đã làm
            logging.warning("Chưa áp được bộ nhận diện mặc định: %s", e)
            return []
        changed = [k for k, old in OLD_DEFAULTS.items() if k in over and over[k] == old]
        if changed:
            config.save_settings({k: "-" for k in changed})
        st["kit"] = KIT_ID
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, path)
    return changed
