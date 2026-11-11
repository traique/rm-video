import asyncio
import itertools
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field

import config

_ids = itertools.count(1)


class QueueFull(Exception):
    pass


@dataclass
class Job:
    url: str                  # "" nếu input là file đính kèm
    mode: str                 # "cook" | "review"
    duration: int             # 0 = giữ nguyên, hoặc 30/60/90 giây
    voice: str
    subs: bool = True
    cover: bool = True
    long_mode: str = ""       # "" | "full" (remake nguyên ≤5 phút) | "split60" | "split90" (cắt nhiều phần) | "highlight" (AI chọn đoạn hay)
    local: str = ""           # đường dẫn file upload qua web
    tg_file_id: str = ""      # file_id video gửi kèm qua Telegram
    music_url: str = ""       # chế độ music: link nhạc mới
    music_local: str = ""     # chế độ music: file nhạc upload qua web
    music_tg_file_id: str = ""  # chế độ music: file nhạc gửi qua Telegram
    title_hint: str = ""      # tên file/caption gốc làm ngữ cảnh cho AI
    source: str = "tg"        # "tg" | "web"
    chat_id: int = 0
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"    # queued | processing | review | approved | done | error | cancelled
    step: str = ""
    error: str = ""
    script: str = ""
    post: str = ""      # content đăng bài: tiêu đề + caption + hashtag
    output: str = ""          # file kết quả giữ lại cho web tải về
    created: float = field(default_factory=time.time)
    dl_token: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    parts: list = field(default_factory=list)   # [{"label", "path", "token"}] các file kết quả cho web
    quality: str = ""         # trình tải: mp3 | 2160..360 | "" (tốt nhất)
    cancel: bool = False      # người dùng bấm hủy
    review: bool = False      # dừng sau khi AI viết lời để người dùng duyệt/sửa rồi mới render
    approved: list = field(default_factory=list)  # kịch bản đã duyệt [{i,start,slot,span,text}] -> bỏ qua bước AI
    started_at: float = 0.0
    finished_at: float = 0.0
    out_dur: float = 0.0      # thời lượng video đang làm (để ước tính thời gian)
    timeline: list = field(default_factory=list)  # [(bước, thời điểm)] đo thời gian từng bước
    attempts: int = 0         # số lần đã chạy (bot khởi động lại giữa chừng thì chạy lại, tối đa MAX_ATTEMPTS)
    source_meta: dict = field(default_factory=dict)   # kênh/uploader/link gốc để ghi nguồn + xử lý khiếu nại
    final_segments: list = field(default_factory=list)   # timeline cuối (xuất timeline.json)
    srt: str = ""             # phụ đề SRT của video ra (nhập vào CapCut/Premiere để sửa tay)
    cut_at: float = 0.0       # mốc cắt đoạn quảng cáo cuối đã quyết ở lượt viết kịch bản (áp lại khi render bản duyệt)
    pieces: list = field(default_factory=list)   # chế độ đoạn hay: các đoạn AI đã chọn (chạy lại thì giữ nguyên)
    images: list = field(default_factory=list)   # chế độ 📸 ảnh + nhạc: ảnh tải lên qua web (đường dẫn)
    tg_images: list = field(default_factory=list)   # ... hoặc file_id ảnh gửi qua Telegram
    music_mix: str = ""       # chế độ 🎵: "" = thay nhạc + khớp beat; "bg" = làm nhạc nền, giữ tiếng gốc


# Gom các bước (chữ hiển thị thay đổi theo số đoạn, phần trăm tải...) thành các GIAI ĐOẠN cố định để xem job
# chậm ở đâu. Thứ tự: khớp mẫu đầu tiên. Bước chờ duyệt không tính (đó là thời gian của người dùng).
PHASES = [
    (re.compile(r"^(Tải|Nhận file|Dùng file)"), "Tải/nhận nguồn"),
    (re.compile(r"^Tìm điểm cắt|^Chấm điểm đoạn hay|: cắt \d"), "Tìm & cắt phần"),
    (re.compile(r"^Bóc lời|^Kéo dài|^Dò beat"), "Bóc lời + phân tích hình"),
    (re.compile(r"^(Viết|AI xem)"), "AI viết kịch bản"),
    (re.compile(r"^Rút gọn"), "AI rút gọn"),
    (re.compile(r"^(Đọc giọng|Giọng \d)"), "Đọc giọng + phụ đề"),
    (re.compile(r"^Chờ tách giọng"), "Chờ tách giọng gốc"),
    (re.compile(r"^Render|^Dựng ảnh"), "Render"),
    (re.compile(r"^Phân tích nhạc|^Chọn đoạn nhạc"), "Phân tích nhạc"),
    (re.compile(r"^Tạo ảnh bìa"), "Ảnh bìa"),
    (re.compile(r"^Gửi"), "Gửi kết quả"),
]
_PART = re.compile(r"^Phần \d+/\d+: |^Đoạn hay \d+/\d+: ")


def phase_of(step: str) -> str | None:
    if not step or step.startswith("Chờ bạn duyệt"):
        return None
    if _PART.match(step) and ": cắt " not in step:
        step = _PART.sub("", step, count=1)
    for rx, name in PHASES:
        if rx.search(step):
            return name
    return "Khác"


def phase_times(timeline: list, end: float) -> list:
    """timeline [(bước, giây từ lúc bắt đầu)] + mốc kết thúc -> [{"name", "secs"}] theo thứ tự xuất hiện,
    cộng dồn các bước cùng giai đoạn (video cắt nhiều phần: cộng mọi phần)."""
    out: dict = {}
    pts = [(s, float(t)) for s, t in timeline if s]
    for k, (step, t) in enumerate(pts):
        nxt = pts[k + 1][1] if k + 1 < len(pts) else max(end, t)
        name = phase_of(step)
        if name:
            out[name] = out.get(name, 0.0) + max(0.0, nxt - t)
    return [{"name": n, "secs": round(v, 1)} for n, v in out.items() if v >= 0.05]


DB_PATH = os.environ.get("JOBS_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")
MAX_ATTEMPTS = 2            # job đang chạy lúc bot chết: chạy lại 1 lần (lần nữa thì báo lỗi, tránh vòng lặp chết)
KEEP_IN_MEMORY = 30         # số job đã XONG giữ trong bộ nhớ cho web; job đang chờ/chạy/chờ duyệt luôn được giữ
ACTIVE = ("queued", "processing", "review")
REVIEW_TTL = 24 * 3600      # job chờ duyệt quá 24 giờ thì hết hạn, xoá file nguồn giữ lại


def inputs_available(d: dict) -> bool:
    """Job còn đủ đầu vào để chạy lại không (link, file Telegram, hoặc file tải lên còn trên đĩa)."""
    if d.get("mode") == "photo":
        imgs_ok = bool(d.get("tg_images")) or (bool(d.get("images")) and all(os.path.exists(p) for p in d["images"]))
        music_ok = bool(d.get("music_url") or d.get("music_tg_file_id")) or (
            bool(d.get("music_local")) and os.path.exists(d["music_local"]))
        return imgs_ok and music_ok
    if d.get("local"):
        return os.path.exists(d["local"])
    return bool(d.get("url") or d.get("tg_file_id"))


class JobQueue:
    """Hàng đợi tuần tự: 1 worker duy nhất, không bao giờ render 2 video cùng lúc.
    Lịch sử job lưu SQLite (jobs.db) nên sống qua restart: job đang chờ được chạy tiếp, job đang chạy dở
    được chạy lại một lần, job chờ duyệt vẫn duyệt được. Bảng steps ghi thời gian từng bước của mỗi job."""

    def __init__(self, db_path: str | None = None):
        self._q = asyncio.Queue()
        self.jobs: dict[int, Job] = {}   # lịch sử job cho web panel
        self.pending = 0                 # số job đang chờ + đang chạy
        self._db = sqlite3.connect(db_path or DB_PATH)
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, data TEXT)")
        self._db.execute("CREATE TABLE IF NOT EXISTS steps (job_id INTEGER, step TEXT, start REAL, end REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS sources (job_id INTEGER, created REAL, source_url TEXT, "
                         "uploader TEXT, output TEXT, label TEXT)")
        self._db.commit()
        self.restored: list[Job] = []    # job được đưa lại vào hàng đợi lúc khởi động (để báo người dùng)
        self._load()

    def _rows(self) -> list:
        """50 job gần nhất + MỌI job còn dở (chờ/chạy/chờ duyệt) dù cũ hơn: không được bỏ rơi job nào."""
        rows = dict(self._db.execute("SELECT id, data FROM jobs ORDER BY id DESC LIMIT 50").fetchall())
        try:
            rows.update(self._db.execute(
                "SELECT id, data FROM jobs WHERE json_extract(data, '$.status') IN ('queued', 'processing', 'review')"
            ).fetchall())
        except sqlite3.Error:                     # SQLite thiếu JSON1: chỉ còn 50 job gần nhất như cũ
            pass
        return sorted(rows.items(), reverse=True)

    def _load(self):
        global _ids
        max_id, again = 0, []
        for _jid, data in self._rows():
            try:
                d = json.loads(data)
                st0 = st = d.get("status")
                if st == "processing":                     # đang chạy dở lúc bot chết
                    d["attempts"] = int(d.get("attempts", 0)) + 1
                    st = "queued" if d["attempts"] < MAX_ATTEMPTS else "error"
                    if st == "error":
                        d["error"] = "Bot khởi động lại giữa chừng 2 lần ở job này, đã dừng để tránh lặp lỗi."
                if st == "queued" and d.get("cancel"):           # đã bấm huỷ trước khi bot khởi động lại
                    st, d["error"] = "cancelled", "Đã hủy trước khi chạy."
                if st == "queued" and not inputs_available(d):
                    st, d["error"] = "error", "Bot khởi động lại và file tải lên không còn, hãy gửi lại."
                d["status"] = st
                if st == "queued":
                    d["step"] = "Chạy tiếp sau khi bot khởi động lại"
                d["parts"] = [p for p in d.get("parts", []) if os.path.exists(p.get("path", ""))]
                job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
                self.jobs[job.id] = job
                max_id = max(max_id, job.id)
                if st == "queued":
                    again.append(job)
                elif st != st0:
                    self.save(job)                # trạng thái mới (lỗi) phải vào DB, không thì lần sau lại xét lại
            except Exception:
                continue
        _ids = itertools.count(max_id + 1)
        for job in sorted(again, key=lambda j: j.id):
            self.pending += 1
            self._q.put_nowait(job)
            self.save(job)
        self.restored = again

    def save(self, job: Job):
        try:
            self._db.execute("INSERT OR REPLACE INTO jobs (id, data) VALUES (?, ?)",
                             (job.id, json.dumps(asdict(job), ensure_ascii=False)))
            self._db.commit()
        except Exception:
            logging.exception("Không lưu được job %s", job.id)

    def _evict(self):
        """Giữ KEEP_IN_MEMORY job đã xong gần nhất; KHÔNG bao giờ bỏ job đang chờ/chạy/chờ duyệt
        (trước đây job chờ duyệt bị đẩy khỏi bộ nhớ sau 30 job mới là không duyệt được nữa)."""
        done = sorted(j for j, job in self.jobs.items() if job.status not in ACTIVE)
        for old in done[:-KEEP_IN_MEMORY]:
            self.jobs.pop(old, None)

    def put(self, job: Job, force: bool = False) -> int:
        """force=True: job đã duyệt kịch bản được vượt giới hạn hàng đợi (người dùng đã chờ, không được mất)."""
        if self.pending >= config.MAX_QUEUE and not force:
            raise QueueFull(f"Hàng đợi đã đầy ({config.MAX_QUEUE} video), thử lại sau.")
        self.pending += 1
        self.jobs[job.id] = job
        self._evict()
        self._q.put_nowait(job)
        self.save(job)
        return self.pending

    async def get(self) -> Job:
        return await self._q.get()

    def done(self):
        self.pending -= 1
        self._q.task_done()

    def review_job_for(self, chat_id: int) -> Job | None:
        """Job Telegram mới nhất của chat đang chờ duyệt (lưu trong jobs nên sống qua restart)."""
        js = [j for j in self.jobs.values() if j.status == "review" and j.source == "tg" and j.chat_id == chat_id]
        return max(js, key=lambda j: j.id) if js else None

    def expire_reviews(self, now: float | None = None) -> list:
        """Job chờ duyệt quá REVIEW_TTL: hết hạn, xoá file nguồn đang giữ. Trả về các job vừa hết hạn."""
        now = now or time.time()
        out = []
        for job in list(self.jobs.values()):
            if job.status == "review" and now - (job.finished_at or job.created) > REVIEW_TTL:
                job.status, job.step = "error", ""
                job.error = "Hết hạn duyệt kịch bản (24 giờ), hãy gửi lại video."
                if job.local and os.path.exists(job.local):
                    os.remove(job.local)
                self.save(job)
                out.append(job)
        return out

    def log_steps(self, job: Job):
        """timeline [(bước, giây từ lúc bắt đầu)] -> bảng steps (bước, bắt đầu, kết thúc)."""
        tl = [(s, float(t)) for s, t in job.timeline if s]
        if not tl:
            return
        ends = [t for _, t in tl[1:]] + [max(tl[-1][1], (job.finished_at or time.time()) - (job.started_at or 0))]
        try:
            self._db.executemany("INSERT INTO steps (job_id, step, start, end) VALUES (?, ?, ?, ?)",
                                 [(job.id, s, t, e) for (s, t), e in zip(tl, ends)])
            self._db.commit()
        except Exception:
            logging.exception("Không ghi được bảng steps")

    def log_source(self, job: Job, output: str, label: str):
        """Nhật ký link nguồn <-> video ra, để xử lý khiếu nại bản quyền."""
        meta = job.source_meta or {}
        try:
            self._db.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                             (job.id, time.time(), meta.get("webpage_url") or job.url or job.title_hint,
                              meta.get("uploader") or "", os.path.basename(output), label))
            self._db.commit()
        except Exception:
            logging.exception("Không ghi được nhật ký nguồn")
