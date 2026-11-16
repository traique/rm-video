"""Dựng trên Kaggle cho chế độ 🎵 / 📸: lỗi thì ghi chú và trả None để caller dựng trên Botkeep.

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
    try:
        res = await kaggle_worker.produce(spec, tmp, on_step=lambda s: setattr(job, "step", s))
    except Exception as e:
        logging.warning("Kaggle lỗi (%s), dựng trên Botkeep: %s", what, e)
        metrics.record("kaggle", "kaggle", ok=False, detail=str(e))
        job.note(f"ℹ️ Kaggle lỗi ({str(e)[:140]}): dựng trên Botkeep. ")
        return None, None
    metrics.record("kaggle", "kaggle")
    job.note(f"ℹ️ Dựng trên Kaggle (CPU) {(res.get('timings') or {}).get('render', 0):.0f}s. ")
    return res["video"], res
