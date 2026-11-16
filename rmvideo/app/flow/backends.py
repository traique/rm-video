"""Vỏ chạy Kaggle dùng chung: gửi việc, lỗi thì ghi số liệu + ghi chú và trả None để caller dựng trên Botkeep.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import logging

import kaggle_worker
import metrics
from queue_manager import Job


async def kaggle_render(job: Job, tmp: str, make_spec, what: str):
    """Dựng trên Kaggle (CPU 4 lõi, không tốn giờ GPU) cho chế độ 🎵 / 📸. Trả (video, kết quả) hoặc (None, None) nếu
    không dùng được / lỗi (ghi chú lý do, caller dựng trên Botkeep như cũ)."""
    if not kaggle_worker.is_enabled():
        return None, None
    try:
        spec = make_spec()
    except Exception as e:
        job.note(f"ℹ️ {what} chưa dựng được trên Kaggle ({str(e)[:140]}): dựng trên Botkeep. ")
        return None, None
    res = await try_produce(job, spec, tmp, FAIL_NOTE, what)
    if res is None:
        return None, None
    metrics.record("kaggle", "kaggle")
    job.note(f"ℹ️ Dựng trên Kaggle (CPU) {(res.get('timings') or {}).get('render', 0):.0f}s. ")
    return res["video"], res


FAIL_NOTE = "ℹ️ Kaggle lỗi ({err:.140}): dựng trên Botkeep. "


async def try_produce(job: Job, spec: dict, tmp: str, fail_note: str = FAIL_NOTE, what: str = "") -> dict | None:
    """Gửi một việc sang Kaggle (tiến độ hiện lên job.step). Lỗi: ghi số liệu, log, ghi chú `fail_note` (có thể chứa
    {err}) rồi trả None — caller làm tiếp trên Botkeep. Thành công: trả kết quả, caller tự ghi số liệu khi dùng xong
    (phần của video dài ghi lúc nhận kết quả lô)."""
    try:
        return await kaggle_worker.produce(spec, tmp, on_step=lambda s: setattr(job, "step", s))
    except Exception as e:
        logging.warning("Kaggle lỗi%s, làm tiếp trên Botkeep: %s", f" ({what})" if what else "", e)
        metrics.record("kaggle", "kaggle", ok=False, detail=str(e))
        job.note(fail_note.format(err=str(e)))
        return None
