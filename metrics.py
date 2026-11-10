"""Đo từng khâu gọi dịch vụ ngoài: dùng provider nào, có rơi về dự phòng không, có lỗi không.

Ghi vào bảng `events` trong jobs.db. Lệnh console `status`, job kiểm tra định kỳ và cảnh báo admin đọc
từ đây: tỉ lệ rơi dự phòng tăng vọt là dấu hiệu một dịch vụ ngoài (CapCut, tikwm, Gemini...) sắp hỏng hẳn.
Không bao giờ ném lỗi ra ngoài: đo đạc hỏng không được làm hỏng job.
"""
import logging
import sqlite3
import threading
import time

import queue_manager

_lock = threading.Lock()
_conn = None
KINDS = {"download": "Tải video", "asr": "Bóc lời", "llm": "Viết kịch bản", "tts": "Đọc giọng", "sep": "Tách giọng"}


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(queue_manager.DB_PATH, check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, provider TEXT, ok INTEGER, "
                      "fallback INTEGER, detail TEXT)")
        _conn.commit()
    return _conn


def record(kind: str, provider: str, ok: bool = True, fallback: bool = False, detail: str = ""):
    """kind: download | asr | llm | tts | sep. fallback=True: đây là đường dự phòng, đường chính đã hỏng."""
    try:
        with _lock:
            db = _db()
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                       (time.time(), kind, provider, int(bool(ok)), int(bool(fallback)), (detail or "")[:200]))
            db.commit()
    except Exception:
        logging.debug("metrics.record lỗi", exc_info=True)


def stats(hours: float = 24, now: float | None = None) -> dict:
    """{kind: {"total", "fail", "fallback", "providers": {tên: số lần thành công}}} trong `hours` giờ qua."""
    since = (now or time.time()) - hours * 3600
    out: dict = {}
    try:
        with _lock:
            rows = _db().execute("SELECT kind, provider, ok, fallback FROM events WHERE ts >= ?", (since,)).fetchall()
    except Exception:
        return out
    for kind, prov, ok, fb in rows:
        k = out.setdefault(kind, {"total": 0, "fail": 0, "fallback": 0, "providers": {}})
        k["total"] += 1
        k["fail"] += 0 if ok else 1
        k["fallback"] += 1 if (fb and ok) else 0
        if ok:
            k["providers"][prov] = k["providers"].get(prov, 0) + 1
    return out


def fallback_rate(kind_stats: dict) -> float:
    ok = kind_stats["total"] - kind_stats["fail"]
    return kind_stats["fallback"] / ok if ok else 0.0


def summary(hours: float = 24) -> str:
    """Một dòng mỗi khâu: '✅ Đọc giọng: 120 lần, dự phòng 3%, lỗi 0 (capcut 116, edge 4)'."""
    st = stats(hours)
    if not st:
        return f"Chưa có số liệu trong {hours:g} giờ qua."
    lines = []
    for kind, label in KINDS.items():
        k = st.get(kind)
        if not k:
            continue
        rate = fallback_rate(k)
        icon = "⚠️" if rate > 0.2 or k["fail"] > k["total"] / 2 else "✅"
        provs = ", ".join(f"{p} {n}" for p, n in sorted(k["providers"].items(), key=lambda x: -x[1]))
        lines.append(f"{icon} {label}: {k['total']} lần, dự phòng {rate * 100:.0f}%, lỗi {k['fail']} ({provs})")
    return "\n".join(lines)


def prune(days: float = 14):
    try:
        with _lock:
            db = _db()
            db.execute("DELETE FROM events WHERE ts < ?", (time.time() - days * 86400,))
            db.commit()
    except Exception:
        pass
