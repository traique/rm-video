"""Quản lý ổ đĩa (ổ Botkeep chỉ 2GB): báo dung lượng, chọn thư mục tạm cho job, dọn video kết quả cũ."""
import logging
import os
import shutil
import tempfile
import time

import cache
import config
from state import BASE_DIR, OUT_DIR, WORK_DIR, queue


def _free_mb(path: str) -> int:
    try:
        return shutil.disk_usage(path).free >> 20
    except Exception:
        return 0


def _size_mb(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path) >> 20
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total >> 20


def disk_report() -> str:
    """Ổ bot + /tmp còn trống bao nhiêu, thư mục nào chiếm chỗ (in lúc khởi động và lệnh status)."""
    tmp = tempfile.gettempdir()
    parts = [f"ổ bot trống {_free_mb(BASE_DIR)}MB", f"{tmp} trống {_free_mb(tmp)}MB"]
    for name in ("outputs", "uploads_in", "work", "cache", "models", "potoken", "tools", ".cache", ".local"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            parts.append(f"{name} {_size_mb(p)}MB")
    return " | ".join(parts)


PIP_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "pip")


def clean(log=logging.info) -> int:
    """Lệnh console clean: xoá thứ tạo lại được — bộ nhớ đệm pip (~/.cache/pip), cache theo bước, thư mục tạm
    work/. KHÔNG xoá video kết quả, cài đặt, jobs.db, model/gói đã cài. Trả MB trống sau khi dọn."""
    before = _free_mb(BASE_DIR)
    for p in (PIP_CACHE, WORK_DIR):
        if os.path.exists(p):
            log(f"Xoá {p} ({_size_mb(p)}MB)")
            shutil.rmtree(p, ignore_errors=True)
    cache.prune(max_mb=0)
    after = _free_mb(BASE_DIR)
    log(f"Dọn xong: trống {before}MB -> {after}MB. {disk_report()}")
    return after


LOW_DISK_MB = 400


def warn_low_disk(log=logging.warning):
    """Lúc khởi động: ổ gần đầy thì nói rõ thư mục nào chiếm chỗ và cách dọn (lần cài thư viện sau có thể hỏng)."""
    free = _free_mb(BASE_DIR)
    if free < LOW_DISK_MB:
        log(f"Ổ bot chỉ còn {free}MB trống (lần cài/cập nhật thư viện sau có thể lỗi 'No space left on device'). "
            f"{disk_report()}. Gõ  clean  để xoá bộ nhớ đệm pip + cache; cần thêm thì xoá video cũ trong outputs/, "
            "gỡ gói không dùng: remove_offline, remove_potoken, remove_youtube.")
    return free


def make_room(need_mb: int, keep: set = frozenset()) -> int:
    """Xóa video kết quả cũ nhất (và file tải lên mồ côi) tới khi ổ bot trống ≥ need_mb. Trả về MB trống."""
    busy = {p for j in queue.jobs.values() if j.status in ("queued", "processing", "review")
            for p in (j.local, j.music_local, *j.images) if p}       # cả ảnh + nhạc tải lên của job 📸/🎵 đang chờ
    for f in sorted((os.path.join(config.UPLOAD_DIR, x) for x in os.listdir(config.UPLOAD_DIR)), key=os.path.getmtime):
        if f not in busy and time.time() - os.path.getmtime(f) > 3600:
            os.remove(f)
    if _free_mb(BASE_DIR) < need_mb:
        cache.prune(max_mb=0)              # cache tạo lại được, video kết quả thì không: bỏ cache trước
    for f in sorted((os.path.join(OUT_DIR, x) for x in os.listdir(OUT_DIR)), key=os.path.getmtime):
        if _free_mb(BASE_DIR) >= need_mb:
            break
        if f not in keep:
            logging.info("Dọn chỗ: xóa %s (%dMB)", os.path.basename(f), _size_mb(f))
            os.remove(f)
    return _free_mb(BASE_DIR)


def pick_workdir(need_mb: int) -> str | None:
    """Chọn nơi đặt file tạm của job: /tmp hay work/ trong ổ bot, chỗ nào đủ rộng. Thiếu cả hai thì dọn ổ bot."""
    tmp = tempfile.gettempdir()
    if _free_mb(tmp) >= need_mb:
        return tmp
    os.makedirs(WORK_DIR, exist_ok=True)
    if _free_mb(BASE_DIR) >= need_mb or make_room(need_mb + 150) >= need_mb:   # +150: chừa chỗ cho file kết quả
        return WORK_DIR
    return None


def _cleanup_outputs(keep: set = frozenset()):
    """Giữ video mới nhất cho web tải về, xóa cũ khi tổng vượt OUT_MAX_MB (ổ Botkeep chỉ 2GB)."""
    files = sorted((os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR)), key=os.path.getmtime, reverse=True)
    total = 0
    for f in files:
        total += os.path.getsize(f)
        if total > config.OUT_MAX_MB * 1024 * 1024 and f not in keep:
            os.remove(f)
