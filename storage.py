"""Quản lý ổ đĩa (ổ Botkeep chỉ 2GB): báo dung lượng, chọn thư mục tạm cho job, dọn video kết quả cũ."""
import glob
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


def clean(log=logging.info, keep_work: bool = False) -> int:
    """Lệnh console clean: xoá thứ tạo lại được — bộ nhớ đệm pip (~/.cache/pip), cache theo bước, thư mục tạm
    work/. KHÔNG xoá video kết quả, cài đặt, jobs.db, model/gói đã cài. Trả MB trống sau khi dọn."""
    before = _free_mb(BASE_DIR)
    for p in ((PIP_CACHE,) if keep_work else (PIP_CACHE, WORK_DIR)):
        if os.path.exists(p):
            log(f"Xoá {p} ({_size_mb(p)}MB)")
            shutil.rmtree(p, ignore_errors=True)
    if keep_work:                       # job đang chạy có thể đang dùng work/ và file trong cache (tiếng nền đã tách)
        log("Đang có job chạy: giữ work/ và cache, chỉ dọn pip")
    else:
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


def _awaiting_post() -> set:
    """File kết quả còn chờ tải lên YouTube / gửi gói đăng TikTok (1.2.0; gói TikTok chờ tới giờ vàng, có khi hơn một
    ngày sau, trong khi OUT_MAX_MB chỉ giữ ~10 video): không xoá."""
    try:
        from rmvideo.app import tiktok_kit, youtube
        return {os.path.abspath(p) for p in youtube.pending_paths() | tiktok_kit.pending_paths()}
    except Exception as e:                       # đọc trạng thái lỗi: dọn như cũ, không làm hỏng job
        logging.warning("Không đọc được danh sách video chờ đăng: %s", str(e)[:120])
        return set()


def make_room(need_mb: int, keep: set = frozenset()) -> int:
    """Xóa video kết quả cũ nhất (và file tải lên mồ côi) tới khi ổ bot trống ≥ need_mb. Trả về MB trống. Video còn chờ
    đăng YouTube / gửi gói TikTok xoá sau cùng: ổ đầy thật thì job mới vẫn có chỗ chạy."""
    keep = {os.path.abspath(p) for p in keep}
    waiting = _awaiting_post() - keep
    busy = {p for j in queue.jobs.values() if j.status in ("queued", "processing", "review")
            for p in (j.local, j.music_local, *j.images) if p}       # cả ảnh + nhạc tải lên của job 📸/🎵 đang chờ
    for f in sorted((os.path.join(config.UPLOAD_DIR, x) for x in os.listdir(config.UPLOAD_DIR)), key=os.path.getmtime):
        if f not in busy and time.time() - os.path.getmtime(f) > 3600:
            os.remove(f)
    if _free_mb(BASE_DIR) < need_mb:
        cache.prune(max_mb=0)              # cache tạo lại được, video kết quả thì không: bỏ cache trước
    for skip in (keep | waiting, keep):
        for f in sorted((os.path.join(OUT_DIR, x) for x in os.listdir(OUT_DIR)), key=os.path.getmtime):
            if _free_mb(BASE_DIR) >= need_mb:
                break
            if os.path.abspath(f) not in skip:
                logging.info("Dọn chỗ: xóa %s (%dMB)%s", os.path.basename(f), _size_mb(f),
                             " — video đang chờ đăng YouTube / TikTok" if os.path.abspath(f) in waiting else "")
                os.remove(f)
    return _free_mb(BASE_DIR)


def sweep_tmp() -> int:
    """Lúc khởi động: xoá thư mục tạm rmv_* còn sót trong /tmp (bot bị giết OOM/SIGKILL thì TemporaryDirectory không kịp
    dọn; tích lại là /tmp đầy và mọi job âm thầm chuyển sang ổ bot 2GB)."""
    n = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "rmv_*")):
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    return n


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
    keep = {os.path.abspath(p) for p in keep} | _awaiting_post()
    files = sorted((os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR)), key=os.path.getmtime, reverse=True)
    total = 0
    for f in files:
        total += os.path.getsize(f)
        if total > config.OUT_MAX_MB * 1024 * 1024 and os.path.abspath(f) not in keep:
            os.remove(f)



# ---------------- xoá job (nút Xóa / Xóa tất cả trên web, 1.4.10) ----------------
ORPHAN_AGE_OUT = 600        # giây: file kết quả mồ côi mới hơn chừng này có thể đang được giao (chưa gắn vào job)
ORPHAN_AGE_UP = 3600        # giây: file tải lên mới hơn chừng này có thể đang tải / sắp thành job (như make_room)

def _job_files(d: dict) -> list[str]:
    """File trên máy của một job (dict như trong jobs.db): video kết quả, các phần (bản ngắn, ảnh bìa...), file tải lên.
    Chỉ file nằm trong thư mục dữ liệu của bot (outputs/, uploads_in/, work/) — không bao giờ ra ngoài."""
    roots = [os.path.abspath(r) + os.sep for r in (OUT_DIR, config.UPLOAD_DIR, WORK_DIR)]
    paths = [d.get("output") or "", d.get("local") or "", d.get("music_local") or "", *(d.get("images") or []),
             *[p.get("path") or "" for p in d.get("parts") or [] if isinstance(p, dict)]]
    out = []
    for p in paths:
        a = os.path.abspath(p) if p else ""
        if a and any(a.startswith(r) for r in roots) and a not in out:
            out.append(a)
    return out


def _held(d: dict, waiting: set) -> str:
    """Lý do KHÔNG được xoá job (rỗng = xoá được): đang chạy / chờ / chờ duyệt, video đang chờ đăng YouTube / TikTok /
    Fanpage (tự động đăng bếp, truyện bé)."""
    from queue_manager import ACTIVE
    jid = d.get("id")
    if d.get("status") in ACTIVE:
        return "job đang chờ / chạy / chờ duyệt — bấm Hủy trước"
    if any(f in waiting for f in _job_files(d)):
        return "video đang chờ đăng YouTube / gửi gói TikTok"
    try:
        import autopost
        import kids_auto
        if ((autopost.load_state().get("pending") or {}).get("job") == jid
                or (kids_auto.load_state().get("pending") or {}).get("job") == jid):
            return "video đang chờ tự động đăng Fanpage"
    except Exception as e:
        logging.warning("Không đọc được trạng thái tự động đăng: %s", str(e)[:120])
        return "không kiểm được trạng thái tự động đăng"
    return ""


def _remove(files: list[str]) -> int:
    """Xoá các file, trả số byte giải phóng."""
    freed = 0
    for f in files:
        try:
            if os.path.isfile(f):
                freed += os.path.getsize(f)
                os.remove(f)
        except OSError as e:
            logging.warning("Không xoá được %s: %s", f, e)
    return freed


def _parked(jid) -> bool:
    try:
        import kids_auto
        return kids_auto.parked_info(int(jid)) is not None
    except Exception:
        return False


def delete_job(jid: int) -> tuple[bool, str]:
    """Nút «Xóa» một job: xoá file của nó trên máy + bỏ khỏi danh sách và jobs.db. Truyện bé đang giữ chỗ Kaggle thì bỏ
    luôn mục giữ chỗ (không thu kết quả về nữa). Trả (xoá được không, câu báo)."""
    d = next((x for x in queue.all_saved() if x.get("id") == jid), None)
    job = queue.jobs.get(jid)
    if d is None and job is None:
        return False, f"Không thấy job #{jid}."
    if job is not None:
        from dataclasses import asdict
        d = asdict(job)
    why = _held(d, _awaiting_post())
    if why:
        return False, f"Không xoá job #{jid}: {why}."
    if _parked(jid):
        import kids_auto
        kids_auto.unpark(int(jid))
    freed = _remove(_job_files(d))
    queue.forget([jid])
    logging.info("Đã xoá job #%s (%dMB)", jid, freed >> 20)
    return True, f"Đã xoá job #{jid}" + (f", giải phóng {freed / 2**20:.0f}MB" if freed >= 2**20 else "") + "."


def clear_jobs() -> dict:
    """Nút «Xóa tất cả»: xoá mọi job đã xong / lỗi / đã huỷ (kể cả job cũ chỉ còn trong jobs.db) cùng file của chúng, rồi
    xoá video mồ côi trong outputs/ và file tải lên không job nào dùng. GIỮ: job đang chờ / chạy / chờ duyệt, video đang
    chờ đăng, truyện bé đang giữ chỗ Kaggle. Trả {deleted, kept, freed_mb, msg}."""
    waiting = _awaiting_post()
    ids, kept, freed = [], 0, 0
    keep_files: set[str] = set(waiting)
    for d in queue.all_saved():
        jid = d.get("id")
        if jid in queue.jobs:                                  # bản trong bộ nhớ mới hơn bản trong DB
            from dataclasses import asdict
            d = asdict(queue.jobs[jid])
        if _held(d, waiting) or _parked(jid):
            kept += 1
            keep_files.update(_job_files(d))
            continue
        freed += _remove(_job_files(d))
        ids.append(jid)
    for jid, job in list(queue.jobs.items()):                  # job chỉ có trong bộ nhớ (chưa kịp lưu): giữ file
        if jid not in ids:
            from dataclasses import asdict
            keep_files.update(_job_files(asdict(job)))
    queue.forget(ids)
    now = time.time()
    for folder, min_age in ((OUT_DIR, ORPHAN_AGE_OUT), (config.UPLOAD_DIR, ORPHAN_AGE_UP)):   # file mồ côi (job cũ đã bị
        try:                                                                                  # dọn khỏi danh sách)
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            f = os.path.abspath(os.path.join(folder, name))
            try:
                young = now - os.path.getmtime(f) < min_age     # đang ghi dở (tải lên / giao kết quả): chưa thuộc job nào
            except OSError:
                continue
            if f not in keep_files and os.path.isfile(f) and not young:
                freed += _remove([f])
    if ids:
        queue.compact()
    msg = (f"Đã xoá {len(ids)} job, giải phóng {freed / 2**20:.0f}MB"
           + (f"; giữ {kept} job đang chạy / chờ / chờ đăng" if kept else "") + ".")
    logging.info("Xóa tất cả: %s", msg)
    return {"deleted": len(ids), "kept": kept, "freed_mb": round(freed / 2**20, 1), "msg": msg}
