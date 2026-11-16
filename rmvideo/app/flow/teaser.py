"""Đoạn mở đầu: cảnh món đã xong chiếu trước 3 giây kèm câu hook (AI chọn khung, so với phần mở đầu gốc để không lặp).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging

import config
from rmvideo.media import filtergraph
import cover as cover_mod
import scenes


async def plan_teaser(inp: str, mode: str, title: str, src_dur: float, cuts: list, end: float) -> tuple:
    """Đoạn mở đầu (bản 29): AI chọn khung đẹp nhất (cũng là ảnh bìa) trong cùng một lượt xác nhận đó là MÓN ĐÃ
    XONG và video gốc chưa tự mở đầu bằng món -> chiếu trước 3 giây cảnh đó (cùng cảnh, không vắt qua chuyển cảnh;
    cảnh quá ngắn thì đứng hình). Thời gian theo video gốc; end: hết phần sẽ render (đã bỏ đoạn quảng cáo cuối).
    Trả (đoạn mở đầu {"a", "dur", "still", "t"} | None, khung đẹp nhất | None, lý do không làm)."""
    a = scenes.analyzed(inp)
    m = await cover_mod.best_moment(inp, mode, title, src_dur, cuts, end, a["dir"] if a else None,
                                    a.get("times") if a else None, opening=True)
    if not m:
        return None, None, "không chọn được khung hình"
    if not m["ai"]:
        return None, m, ("cần key Gemini để AI xác nhận cảnh món đã xong" if not config.GEMINI_API_KEY
                         else "AI chọn ảnh lỗi")
    if not m["finished"]:
        return None, m, "AI không thấy cảnh món đã xong rõ ràng"
    win = cover_mod.opening_window(src_dur)
    if m["opens_with_dish"] or m["t"] < win:          # khung đẹp nhất nằm ngay phần mở đầu cũng vậy
        return None, m, "video gốc đã mở đầu bằng món"
    T = filtergraph.TEASER_DUR
    near = await scenes.cuts_near(inp, m["t"] - T - 0.5, min(end, m["t"] + T + 0.5))
    known = {c for c in cuts if abs(c - m["t"]) <= T + 1}
    start, still = filtergraph.teaser_window(m["t"], sorted(set(near) | known), end, T)
    # lưới an toàn không cần AI: phần mở đầu video gốc dùng lại ĐÚNG cảnh này (thường gặp: chiếu trước cảnh cuối) ->
    # chiếu thêm đoạn mở đầu sẽ lặp y hệt (video cá kèo 13/11/2026: giây 3.5 giống cảnh giây 209.5 tới 0.96)
    try:
        sim, at = await asyncio.to_thread(cover_mod.opening_repeat_sync, inp, start, T, win)
    except Exception as e:
        logging.info("Không so được phần mở đầu (%s)", str(e)[:100])
        sim, at = 0.0, 0.0
    logging.info("Đoạn mở đầu: cảnh %.1fs, giống phần mở đầu gốc %.2f (giây %.1f)", start, sim, at)
    if sim >= cover_mod.REPEAT_SIM:
        return None, m, f"video gốc đã chiếu trước cảnh món ở giây {at:.0f}"
    return {"a": start, "dur": T, "still": still, "t": round(m["t"], 3)}, m, ""
