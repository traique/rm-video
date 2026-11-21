"""Bóc lời + phân tích hình: trên Kaggle (tách giọng GPU -> Whisper large-v3, dò cảnh, chữ in cứng, ảnh thu nhỏ; có cache theo video) hoặc dò chữ OCR trên Botkeep.

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os
import re

import cache
import config
import kaggle_worker
import metrics
import ocr_text
import scenes
import transcribe
from queue_manager import Job


async def ocr_bands(inp: str, src_dur: float, src_hash: str, fallback: list) -> list:
    """Dò dải chữ in cứng bằng model OCR (TEXT_DETECT=ocr); lỗi thì giữ kết quả heuristic."""
    k = cache.key("ocr", src_hash)
    hit = cache.get_json("ocr", k)
    if hit is not None:
        return [tuple(b) for b in hit]
    try:
        bands = await asyncio.to_thread(ocr_text.detect_sync, inp, 0.0, min(src_dur, config.HARD_MAX))
        cache.put_json("ocr", k, [list(b) for b in bands])
        return bands
    except Exception as e:
        logging.warning("Dò chữ OCR lỗi (%s), dùng cách dò cũ", str(e)[:100])
        return fallback


ANALYZE_V = 1        # định dạng kết quả phân tích Kaggle trong cache (đổi khi đổi cách phân tích)


def analyze_remote(job: Job, inp: str, local_only: bool) -> str:
    """'' nếu bóc lời + phân tích hình của job này chạy trên Kaggle; không thì lý do. Phần cắt từ video dài (đang gom
    lô) và bản đã duyệt (lấy từ cache) làm như cũ."""
    if local_only:
        return "chạy trên Botkeep theo yêu cầu"
    if config.KAGGLE_ANALYZE == "0":
        return "KAGGLE_ANALYZE=0"
    if job.approved:
        return "kịch bản đã duyệt"
    if kaggle_worker.collecting():
        return "phần của video dài"
    if job.mode not in ("cook", "review"):
        return "chế độ không cần bóc lời"
    return kaggle_worker.usable(job, inp)


async def kaggle_analysis(job: Job, inp: str, tmp: str, title: str, desc: str, src_hash: str, want_gray: bool,
                          run: bool = True) -> dict | None:
    """Bóc lời + phân tích hình trên Kaggle: tách giọng gốc (GPU) -> Whisper large-v3 nghe giọng SẠCH; dò cảnh, chữ in
    cứng, ảnh thu nhỏ ở mức Kỹ. Botkeep chỉ lọc + chia đoạn (transcribe.from_whisper). Có cache theo video (lần dựng
    bản đã duyệt không chạy lại). run=False: chỉ lấy cache. Lỗi -> None (caller bóc lời trên Botkeep như cũ)."""
    k = cache.key("kaggle_an", src_hash, want_gray, ANALYZE_V)
    res = cache.get_json("kaggle_an", k)
    zp = cache.get("kaggle_thumbs", k, ".zip") if res else None
    fresh = False
    if not res:
        if not run:
            return None
        try:
            spec = kaggle_worker.analyze_spec(job, inp, title, desc, want_gray,
                                              config.LONG_MAX if job.long_mode == "full" else config.MAX_DURATION)
            res = await kaggle_worker.analyze(spec, tmp, on_step=lambda st: setattr(job, "step", st))
        except Exception as e:
            logging.warning("Bóc lời trên Kaggle lỗi, làm trên Botkeep: %s", e)
            metrics.record("kaggle", "analyze", ok=False, detail=str(e))
            job.note(f"ℹ️ Bóc lời trên Kaggle lỗi ({str(e)[:140]}): bóc lời trên Botkeep. ")
            return None
        metrics.record("kaggle", "analyze")
        fresh = True
        try:
            cache.put_json("kaggle_an", k, {x: res.get(x) for x in ("whisper", "cuts", "bands", "dur", "sep", "timings",
                                                                     "token", "inpainted")})   # mã tiếng nền: phiên dựng dùng lại
            zp = res.get("thumbs_zip")
            if zp:
                cache.put("kaggle_thumbs", k, zp, ".zip")
            if res.get("clean"):                        # video đã xoá chữ (LaMa): bản duyệt dựng lại cũng dùng
                cache.put("kaggle_clean", k, res["clean"], ".mp4")
        except OSError:
            pass
    clean = res.get("clean") if fresh else cache.get("kaggle_clean", k, ".mp4")
    if clean and not os.path.exists(clean):
        clean = None
    text, segs = transcribe.from_whisper(res.get("whisper") or {})
    if zp and os.path.exists(zp):                       # ảnh 2 khung/giây cho AI nhìn + chấm ảnh bìa
        import zipfile
        d = os.path.join(tmp, "thumbs_kaggle")
        os.makedirs(d, exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            names = [n for n in z.namelist() if re.fullmatch(r"\d{5}\.jpg", n)]
            for n in names:
                z.extract(n, d)
        scenes.remember_analysis(inp, {"dir": d, "n": len(names)})
    w = res.get("whisper") or {}
    logging.info("Kaggle bóc lời: %d đoạn (%s, %s), %d điểm chuyển cảnh, %d dải chữ | %s", len(segs),
                 w.get("language"), w.get("device"), len(res.get("cuts") or []), len(res.get("bands") or []),
                 res.get("timings"))
    return {"text": text, "segs": segs, "cuts": list(res.get("cuts") or []),
            "bands": [tuple(b) for b in res.get("bands") or []], "ambient": res.get("ambient") if fresh else None,
            "clean": clean, "inpainted": [tuple(b) for b in res.get("inpainted") or []] if clean else [],
            # mã tiếng nền giữ cả khi lấy từ cache: phiên dựng gắn đầu ra notebook phân tích, khớp mã thì dùng lại (bản 32
            # chạy lại video cũ: không có mã -> tách lại trên CPU Kaggle 418s); đã bị video khác ghi đè thì mã lệch, tự tách
            "token": res.get("token", ""), "timings": res.get("timings") or {}}
