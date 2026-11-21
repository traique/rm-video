"""Đoạn mở đầu: cảnh THÀNH PHẨM hấp dẫn nhất chiếu trước 2 giây kèm câu hook (AI xếp hạng các khung món đã xong, ưu
tiên khoảnh khắc có hành động; so với phần mở đầu gốc để không lặp).

Tách từ pipeline.py (Phase 4); pipeline.py còn là mặt tiền tương thích."""
import asyncio
import logging

import config
import cover as cover_mod
import scenes
from rmvideo.media import filtergraph


OPENS_NOW = 1.0         # khung đầu video gốc có món từ giây này trở về trước = video gốc đã mở ngay bằng thành phẩm
TRY_PICKS = 3           # thử tối đa chừng này khung thành phẩm (theo thứ hạng AI) tới khi có cảnh không lặp phần mở đầu


async def plan_teaser(inp: str, mode: str, title: str, src_dur: float, cuts: list, end: float) -> tuple:
    """Đoạn mở đầu (bản 29; 1.4.0: luôn mở bằng thành phẩm): AI chọn ảnh bìa và trong cùng lượt xếp hạng các khung MÓN
    ĐÃ XONG để mở đầu (ưu tiên cắn thử / kéo sợi / cắt đôi / rưới sốt). Chiếu trước 2 giây cảnh xếp cao nhất (cùng cảnh,
    không vắt qua chuyển cảnh; cảnh quá ngắn thì đứng hình), trừ khi video gốc đã mở NGAY bằng món (≤ OPENS_NOW giây) hay
    mọi cảnh thành phẩm đều là đúng cảnh phần mở đầu gốc đã chiếu. Thời gian theo video gốc; end: hết phần sẽ render (đã
    bỏ đoạn quảng cáo cuối). Trả (đoạn mở đầu {"a", "dur", "still", "t", "cover"} | None, khung đẹp nhất | None, lý do
    không làm)."""
    a = scenes.analyzed(inp)
    m = await cover_mod.best_moment(inp, mode, title, src_dur, cuts, end, a["dir"] if a else None,
                                    a.get("times") if a else None, opening=True)
    if not m:
        return None, None, "không chọn được khung hình"
    if not m["ai"]:
        return None, m, ("cần key Gemini để AI xác nhận cảnh món đã xong" if not config.GEMINI_API_KEY
                         else "AI chọn ảnh lỗi")
    ranked = list(m.get("teaser_ts") or [])
    if not ranked:
        return None, m, "AI không thấy cảnh món đã xong rõ ràng"
    if 0 <= float(m.get("opens_at", -1.0)) <= OPENS_NOW:
        return None, m, "video gốc đã mở đầu ngay bằng món"
    win = cover_mod.opening_window(src_dur)
    picks = [t for t in ranked if t >= win]             # khung nằm ngay phần mở đầu gốc: chiếu trước là lặp
    if not picks:
        return None, m, "cảnh món đã xong chỉ có ở phần mở đầu video gốc"
    T, lead = filtergraph.TEASER_DUR, filtergraph.TEASER_LEAD
    seen = (0.0, 0.0)
    for t in picks[:TRY_PICKS]:
        near = await scenes.cuts_near(inp, t - T - lead - 0.5, min(end, t + T + 0.5))
        known = {c for c in cuts if abs(c - t) <= T + lead + 1}
        start, still = filtergraph.teaser_window(t, sorted(set(near) | known), end, T, lead)
        # lưới an toàn không cần AI: phần mở đầu video gốc dùng lại ĐÚNG cảnh này (thường gặp: chiếu trước cảnh cuối) ->
        # chiếu thêm đoạn mở đầu sẽ lặp y hệt (video cá kèo 13/11/2026: giây 3.5 giống cảnh giây 209.5 tới 0.96)
        try:
            sim, at = await asyncio.to_thread(cover_mod.opening_repeat_sync, inp, start, T, win)
        except Exception as e:
            logging.info("Không so được phần mở đầu (%s)", str(e)[:100])
            sim, at = 0.0, 0.0
        logging.info("Đoạn mở đầu: thành phẩm giây %.1f (cảnh từ %.1fs), giống phần mở đầu gốc %.2f (giây %.1f)",
                     t, start, sim, at)
        if sim < cover_mod.REPEAT_SIM:
            return {"a": start, "dur": T, "still": still, "t": round(t, 3), "cover": round(float(m["t"]), 3)}, m, ""
        seen = (sim, at)
    return None, m, f"video gốc đã chiếu trước cảnh món ở giây {seen[1]:.0f}"
