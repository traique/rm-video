"""Tiện ích dùng chung của bộ điều phối: chạy song song có huỷ, quyết làm nguyên video dài, kênh được phép, timeline cuối, dòng ghi nguồn.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio

import config
import kaggle_worker
from queue_manager import Job


async def gather_all(coros: list) -> list:
    """Như asyncio.gather nhưng một việc lỗi thì huỷ các việc còn lại. Trước đây các đoạn đọc giọng khác vẫn chạy tiếp
    sau khi job đã hỏng, gọi TTS vô ích và ghi vào thư mục tạm đã bị xoá ("No such file or directory: .../clip_13_...")."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def auto_full(job: Job) -> bool:
    """«Giữ nguyên» (không chọn thời lượng / chế độ dài) + Kaggle bật: video dài ≤ LONG_MAX làm NGUYÊN cả video thay vì báo
    lỗi "vượt giới hạn 90s" (log 12/11/2026: gửi lại link 3 lần mới chạy). Giới hạn 90s là vì Botkeep dựng chậm; không có
    Kaggle thì giữ như cũ (5 phút dựng trên Botkeep mất cả tiếng)."""
    return (not job.long_mode and not job.duration and job.source != "auto" and job.mode in ("cook", "review")
            and kaggle_worker.is_enabled())


def check_source_allowed(job: Job):
    """ALLOWED_SOURCES (Cài đặt): chỉ remake video của các kênh đã được phép (kênh của bạn, kênh đã cấp phép).
    Trống = không giới hạn. So khớp không phân biệt hoa thường với uploader / uploader_id."""
    allowed = {x.strip().lstrip("@").lower() for x in config.ALLOWED_SOURCES.split(",") if x.strip()}
    if not allowed or job.mode == "download":
        return
    meta = job.source_meta or {}
    names = {str(meta.get(k) or "").lstrip("@").lower() for k in ("uploader", "uploader_id")} - {""}
    if not names & allowed:
        raise RuntimeError(f"Kênh nguồn «{meta.get('uploader') or '?'}» không nằm trong danh sách ALLOWED_SOURCES "
                           "(Cài đặt). Chỉ remake video của kênh đã được phép.")


def final_timeline(segs: list, plan: list, out_dur: float) -> list:
    """Timeline cuối của video (xuất timeline.json để dựng tay / lưu trữ): mỗi đoạn có khe thời gian, cờ tiếng
    gốc, lời gốc, lời mới và chỗ giọng mới thật sự nằm (đầu, cuối, hệ số tốc độ)."""
    out = []
    for k, (sg, p) in enumerate(zip(segs, plan)):
        nxt = segs[k + 1]["start"] if k + 1 < len(segs) else out_dur
        spoken = sg.get("ost") != "orig"
        out.append({"i": k, "start": round(sg["start"], 2), "end": round(min(nxt, out_dur), 2),
                    "ost": sg.get("ost", "new"), "orig": sg.get("orig", ""), "text": sg.get("text", ""),
                    "voice": ({"start": p["start"], "end": p["end"], "speed": p["factor"]} if spoken else None)})
    return out


def credit_line(job: Job, narrated: bool = True) -> str:
    """'🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI' cho nội dung đăng (tắt bằng SOURCE_CREDIT=0). narrated=False: video
    không có giọng đọc (chỉ nhạc nền)."""
    if config.SOURCE_CREDIT == "0":
        return ""
    parts = []
    uploader = (job.source_meta or {}).get("uploader")
    if uploader:
        parts.append(f"🎬 Nguồn: {uploader}")
    if job.mode != "music" and narrated:
        parts.append("🔊 Giọng đọc AI")
    return " · ".join(parts)
