import asyncio
import itertools
import json
import os
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
    long_mode: str = ""       # "" | "full" (remake nguyên ≤5 phút) | "split60" | "split90" (cắt nhiều phần)        # che phụ đề in cứng của video gốc, đặt phụ đề mới lên đó
    local: str = ""           # đường dẫn file upload qua web
    tg_file_id: str = ""      # file_id video gửi kèm qua Telegram
    music_url: str = ""       # chế độ music: link nhạc mới
    music_local: str = ""     # chế độ music: file nhạc upload qua web
    music_tg_file_id: str = ""  # chế độ music: file nhạc gửi qua Telegram
    title_hint: str = ""      # tên file/caption gốc làm ngữ cảnh cho AI
    source: str = "tg"        # "tg" | "web"
    chat_id: int = 0
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"    # queued | processing | done | error
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
    cut_at: float = 0.0       # mốc cắt đoạn quảng cáo cuối đã quyết ở lượt viết kịch bản (áp lại khi render bản duyệt)


DB_PATH = os.environ.get("JOBS_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")


class JobQueue:
    """Hàng đợi tuần tự: 1 worker duy nhất, không bao giờ render 2 video cùng lúc.
    Lịch sử job lưu SQLite (jobs.db) nên sống qua restart Botkeep."""

    def __init__(self):
        self._q = asyncio.Queue()
        self.jobs: dict[int, Job] = {}   # lịch sử job cho web panel
        self.pending = 0                 # số job đang chờ + đang chạy
        self._db = sqlite3.connect(DB_PATH)
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, data TEXT)")
        self._load()

    def _load(self):
        global _ids
        max_id = 0
        for jid, data in self._db.execute("SELECT id, data FROM jobs ORDER BY id DESC LIMIT 50"):
            try:
                d = json.loads(data)
                if d.get("status") in ("queued", "processing"):   # đang làm dở lúc restart
                    d["status"], d["error"] = "error", "Botkeep khởi động lại giữa chừng, hãy gửi lại."
                d["parts"] = [p for p in d.get("parts", []) if os.path.exists(p.get("path", ""))]
                job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
                self.jobs[job.id] = job
                max_id = max(max_id, job.id)
            except Exception:
                continue
        _ids = itertools.count(max_id + 1)

    def save(self, job: Job):
        try:
            self._db.execute("INSERT OR REPLACE INTO jobs (id, data) VALUES (?, ?)",
                             (job.id, json.dumps(asdict(job), ensure_ascii=False)))
            self._db.commit()
        except Exception:
            pass

    def put(self, job: Job, force: bool = False) -> int:
        """force=True: job đã duyệt kịch bản được vượt giới hạn hàng đợi (người dùng đã chờ, không được mất)."""
        if self.pending >= config.MAX_QUEUE and not force:
            raise QueueFull(f"Hàng đợi đã đầy ({config.MAX_QUEUE} video), thử lại sau.")
        self.pending += 1
        self.jobs[job.id] = job
        for old in sorted(self.jobs)[:-30]:   # giữ 30 job gần nhất trong bộ nhớ
            self.jobs.pop(old, None)
        self._q.put_nowait(job)
        self.save(job)
        return self.pending

    async def get(self) -> Job:
        return await self._q.get()

    def done(self):
        self.pending -= 1
        self._q.task_done()
