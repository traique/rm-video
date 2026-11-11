"""Duyệt kịch bản: cờ tiếng gốc từng đoạn, cú pháp trả lời trên Telegram, tạo job render từ bản đã duyệt."""
import os
import re
import unicodedata

from queue_manager import Job
from state import queue


OST_CHOICES = ("new", "orig", "both")
OST_TAG = {"orig": "[giữ tiếng gốc] ", "both": "[lót tiếng gốc] "}
REVIEW_HELP = ("Trả lời \"ok\" để render. Sửa thì gửi mỗi dòng một đoạn: \"số: lời mới\"; "
               "\"số: gốc\" = giữ nguyên tiếng gốc đoạn đó (không đọc); \"số: lót gốc\" = đọc lời mới, lót tiếng gốc; "
               "\"số: mới\" = bỏ giữ tiếng gốc.")


def parse_review_reply(text: str) -> dict:
    """Tin trả lời duyệt kịch bản trên Telegram -> edits cho approve_job."""
    edits = {}
    for line in (text or "").splitlines():
        mm = re.match(r"\s*(\d+)\s*[:.)-]\s*(.+)", line)
        if not mm:
            continue
        k, body = int(mm.group(1)), unicodedata.normalize("NFC", mm.group(2).strip())   # bàn phím gõ dấu rời
        low = body.lower()
        if low in ("gốc", "giữ gốc", "tiếng gốc"):
            edits[k] = {"ost": "orig"}
        elif low in ("lót gốc", "cả hai"):
            edits[k] = {"ost": "both"}
        elif low in ("mới", "lời mới"):
            edits[k] = {"ost": "new"}
        else:
            edits[k] = body
    return edits


def spoken_script(segs: list) -> str:
    """Lời sẽ được đọc (bỏ các đoạn giữ nguyên tiếng gốc)."""
    return " ".join(s["text"] for s in segs if s.get("ost") != "orig" and s.get("text"))


def approve_job(src: Job, edits: dict | None = None) -> Job:
    """Tạo job render từ kịch bản đã duyệt. edits: {i: "lời mới"} hoặc {i: {"text": ..., "ost": new|orig|both}}.
    Job cũ chuyển sang 'approved'."""
    segs = []
    for s in src.approved:
        e = (edits or {}).get(int(s["i"]))
        text, ost = s["text"], s.get("ost", "new")
        if isinstance(e, dict):
            text, ost = e.get("text", text), e.get("ost", ost)
        elif isinstance(e, str):
            text = e
        text, ost = str(text or "").strip(), (ost if ost in OST_CHOICES else "new")
        if not text and ost != "orig" and s.get("ost") == "orig":
            raise ValueError(f"Đoạn {s['i']} đang giữ tiếng gốc nên chưa có lời: nhập lời mới cho đoạn này "
                             f"(\"{s['i']}: lời mới\") rồi mới bỏ giữ tiếng gốc.")
        if text or ost == "orig":
            segs.append({"i": s["i"], "start": s["start"], "slot": s["slot"], "span": s.get("span", s["slot"]),
                         "text": text, "orig": s.get("orig", ""), "ost": ost})
    if not segs:
        raise ValueError("Kịch bản trống: cần ít nhất một đoạn có lời.")
    job = Job(url=src.url, mode=src.mode, duration=src.duration, voice=src.voice, subs=src.subs, cover=src.cover,
              long_mode=src.long_mode, local=src.local if src.local and os.path.exists(src.local) else "",
              tg_file_id=src.tg_file_id, title_hint=src.title_hint, music_url=src.music_url,
              music_local="", music_tg_file_id=src.music_tg_file_id, source=src.source, chat_id=src.chat_id,
              approved=segs, cut_at=src.cut_at, source_meta=src.source_meta)
    job.post = src.post
    job.script = spoken_script(segs)
    # put() TRƯỚC, đổi trạng thái job cũ SAU: lỗi gì thì kịch bản vẫn còn ở job cũ để duyệt lại (lỗi #4).
    # Job đã duyệt được vượt giới hạn hàng đợi vì người dùng đã chờ và đã sửa tay.
    queue.put(job, force=True)
    src.status, src.step = "approved", f"Đã duyệt -> render ở job #{job.id}"
    queue.save(src)
    return job
