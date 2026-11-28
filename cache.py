"""Cache theo nội dung cho các bước tốn thời gian: đoạn giọng TTS, kết quả phân tích hình, tiếng nền đã tách giọng,
file nguồn của job chờ duyệt. Có trần dung lượng (CACHE_MAX_MB, mặc định 500MB) vì ổ Botkeep chỉ 2GB:
vượt trần thì xoá cái lâu không dùng nhất.

Khoá = hash của mọi thứ ảnh hưởng tới kết quả + STEP_VERSION của bước đó: sửa thuật toán một bước thì tăng
phiên bản bước đó, cache cũ tự mất hiệu lực, không bao giờ dùng nhầm kết quả cũ.
"""
import hashlib
import json
import logging
import os
import shutil
import time

import config

DIR = os.environ.get("CACHE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

STEP_VERSION = {"tts": 1, "scenes": 1, "ambient": 1, "source": 1}


def key(step: str, *parts) -> str:
    h = hashlib.sha256(f"{step}:{STEP_VERSION.get(step, 1)}".encode())
    for p in parts:
        h.update(b"\x00" + json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
    return h.hexdigest()[:32]


def file_hash(path: str, head: int = 8 << 20) -> str:
    """Hash nhanh của một file video: kích thước + 8MB đầu + 1MB cuối (đủ phân biệt, không đọc hết 300MB)."""
    h = hashlib.sha256()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(head))
        if size > head:
            f.seek(max(head, size - (1 << 20)))
            h.update(f.read())
    return h.hexdigest()[:32]


def _path(step: str, k: str, ext: str) -> str:
    return os.path.join(DIR, step, k + ext)


def get(step: str, k: str, ext: str) -> str | None:
    p = _path(step, k, ext)
    if os.path.exists(p):
        try:
            os.utime(p)                         # đánh dấu vừa dùng (LRU theo mtime)
        except OSError:
            pass
        return p
    return None


def put(step: str, k: str, src: str, ext: str, move: bool = False) -> str:
    """Đưa file vào cache (ghi tệp tạm rồi đổi tên: không bao giờ để file dở dang). Trả đường dẫn trong cache."""
    p = _path(step, k, ext)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    (shutil.move if move else shutil.copyfile)(src, tmp)
    os.replace(tmp, p)
    return p


def get_json(step: str, k: str):
    p = get(step, k, ".json")
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def put_json(step: str, k: str, data) -> None:
    p = _path(step, k, ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)


def read_json_file(path: str, default):
    """Đọc tệp JSON trạng thái; không có hoặc hỏng (chết giữa lúc ghi) -> default, để lần ghi sau tự chữa."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_file(path: str, data) -> None:
    """Ghi tệp JSON trạng thái qua tệp tạm + đổi tên: không bao giờ để tệp dở dang."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def size_mb() -> int:
    total = 0
    for r, _, fs in os.walk(DIR):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total >> 20


def prune(max_mb: int | None = None, keep: set = frozenset()) -> int:
    """Xoá file lâu không dùng nhất tới khi cache ≤ max_mb. Trả số file đã xoá."""
    max_mb = config.CACHE_MAX_MB if max_mb is None else max_mb
    files = []
    for r, _, fs in os.walk(DIR):
        for f in fs:
            p = os.path.join(r, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if f.endswith(".tmp") and time.time() - st.st_mtime > 3600:     # tệp tạm mồ côi
                files.append((0.0, st.st_size, p))
            else:
                files.append((st.st_mtime, st.st_size, p))
    total = sum(s for _, s, _ in files)
    limit = max_mb << 20
    removed = 0
    for _, s, p in sorted(files):
        if total <= limit:
            break
        if p in keep:
            continue
        try:
            os.remove(p)
            total -= s
            removed += 1
        except OSError:
            pass
    if removed:
        logging.info("Cache: xoá %d file cũ, còn %dMB", removed, total >> 20)
    return removed
