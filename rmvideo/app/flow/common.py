"""Tiện ích dùng chung của bộ điều phối: chạy song song có huỷ, quyết làm nguyên video dài, kênh được phép, timeline cuối, dòng ghi nguồn.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import re

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


def voice_report(segs: list, plan: list, end: float) -> str:
    """Một dòng chỉ số lời đọc cho ghi chú job (trước đây phải tự đo tay mỗi lượt soát video): số chữ, phần trăm thời
    lượng có giọng, quãng lặng > 4 giây (không tính đoạn giữ tiếng gốc), số câu kết bằng "nha"."""
    import prompt_engine
    spoken = [(sg, p) for sg, p in zip(segs, plan) if sg.get("ost") != "orig" and (sg.get("text") or "").strip()]
    if not spoken or end <= 0:
        return ""
    words = sum(prompt_engine.spoken_words(sg["text"]) for sg, _ in spoken)
    voiced = sum(max(0.0, min(p["end"], end) - p["start"]) for _, p in spoken)
    keep = [(sg["start"], segs[k + 1]["start"] if k + 1 < len(segs) else end)
            for k, sg in enumerate(segs) if sg.get("ost") == "orig"]
    marks = sorted([(p["start"], p["end"]) for _, p in spoken] + keep)
    gaps, prev = 0, 0.0
    for a, b in marks + [(end, end)]:
        if a - prev > 4.0:
            gaps += 1
        prev = max(prev, b)
    sents = [x for sg, _ in spoken for x in re.split(r"(?<=[.!?…])\s+", sg["text"].strip()) if x]
    nha = sum(1 for x in sents if re.search(r"\bnha[.!?…]*$", x.strip(), re.IGNORECASE))
    return (f"ℹ️ Lời đọc: {words} chữ, giọng phủ {voiced / end:.0%} video"
            + (f", {gaps} quãng lặng trên 4 giây" if gaps else "") + f", {nha}/{len(sents)} câu kết «nha». ")


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


SHORTS_MAX = 180.0       # YouTube Shorts: video dọc tối đa 3 phút


def post_for_length(post: str, dur: float) -> str:
    """Bài đăng của bản dài hơn 3 phút (bản 50): YouTube KHÔNG xếp vào Shorts dù gắn #Shorts (job 7 bản đầy đủ 3:08 vẫn
    ghi «YouTube Shorts … #Shorts») -> ghi rõ đăng video thường, lên Shorts thì dùng bản ngắn."""
    if dur <= SHORTS_MAX or "▶ YouTube Shorts:" not in (post or ""):
        return post
    return post.replace("▶ YouTube Shorts:", "▶ YouTube (dài hơn 3 phút nên là video thường; muốn lên Shorts thì đăng bản "
                        "ngắn):").replace(" #Shorts", "")


def post_title(job: Job, title: str) -> str:
    """Chữ in lên màn hình 3 giây đầu + ảnh bìa: câu hook AI viết, không có thì dòng đầu bài đăng, cuối cùng tiêu đề
    gốc. (Trước đây biểu thức này lặp 4 lần trong pipeline.)"""
    return job.hook or (job.post.split("\n", 1)[0].replace("📌", "").strip() if job.post else title)
