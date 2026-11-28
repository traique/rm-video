"""Kết nối SQLite cho jobs.db (hàng đợi, nhật ký bước, nguồn, số liệu dịch vụ) — một cấu hình chung.

Trước đây hai kết nối (hàng đợi trên vòng lặp sự kiện, số liệu từ luồng phụ) ở chế độ nhật ký mặc định: ghi chồng là
một bên đợi tới 5 giây ("database is locked"), mỗi commit fsync cả tệp trên vòng lặp sự kiện (C10).
WAL: đọc không chặn ghi; synchronous=NORMAL: commit chỉ ghi nối vào tệp -wal (an toàn khi tiến trình chết, chỉ có
thể mất vài giao dịch cuối khi mất điện cả máy — chấp nhận được cho lịch sử job); busy_timeout: chờ thay vì lỗi ngay.
"""
from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def jobs_db_path() -> str:
    """JOBS_DB (test / máy khác) hoặc jobs.db ở gốc bot."""
    return os.environ.get("JOBS_DB") or os.path.join(ROOT, "jobs.db")


def connect(path: str, *, threads: bool = False) -> sqlite3.Connection:
    """threads=True: kết nối dùng từ nhiều luồng (caller tự khoá khi ghi)."""
    conn = sqlite3.connect(path, timeout=10, check_same_thread=not threads)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:              # hệ tệp không hỗ trợ WAL (hiếm): giữ chế độ mặc định
        pass
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
