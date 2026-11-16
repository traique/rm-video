"""Hiểu món + đối chiếu định lượng (bản 42, kế hoạch S1): một lượt AI đọc lời gốc (+ ảnh theo mốc giây) rồi MÃ kiểm
từng con số với lời gốc (rmvideo/domain/recipe.py). Kết quả dùng cho: công thức + mẹo trong bài đăng (POST_RECIPE,
TIPS), bảng định lượng ở màn duyệt, chặn tự đăng khi số mâu thuẫn, nhãn bước cho kế hoạch dựng (REMAKE_STYLE=strong).

Có cache theo video + lời gốc (bản đã duyệt / chạy lại không gọi AI lần nữa)."""
from __future__ import annotations

import logging

import cache
import config
import prompt_engine
import scenes
from queue_manager import Job
from rmvideo.domain import recipe
from rmvideo.domain.job import Mode, Source

RECIPE_V = 1          # đổi khi đổi lời nhắc / cách kiểm (cache cũ không dùng nữa)
N_IMAGES = 12


def wanted(job: Job) -> bool:
    """Có cần hiểu món không: chỉ video nấu ăn, khi bật công thức / mẹo / dựng mạnh, hoặc tự đăng có lọc quảng cáo."""
    if job.mode != Mode.COOK:
        return False
    return (config.POST_RECIPE == "1" or config.TIPS != "off" or config.REMAKE_STYLE == "strong"
            or (job.source == Source.AUTO and config.AUTO_SKIP_ADS == "1"))


async def understand(job: Job, inp: str, title: str, desc: str, raw_segs: list, end_src: float,
                     src_hash: str) -> recipe.Facts | None:
    """raw_segs: lời gốc trên trục video GỐC; end_src: hết phần sẽ dựng (đã cắt quảng cáo cuối). None = không làm được."""
    if job.approved and job.recipe:
        return recipe.Facts.from_dict(job.recipe)
    segs = [(a, b, t) for a, b, t in raw_segs if a < end_src and (t or "").strip()]
    if len(segs) < 2:
        return None
    texts = [t for _, _, t in segs]
    k = cache.key("recipe", src_hash, texts, desc or "", RECIPE_V, config.SPEECH_STYLE)
    hit = cache.get_json("recipe", k)
    ai = hit
    if ai is None:
        times = [end_src * (i + 0.5) / N_IMAGES for i in range(N_IMAGES)]
        try:
            images = await scenes.keyframes(inp, [(t, t, "") for t in times])
        except Exception as e:                       # không lấy được ảnh: vẫn đọc công thức từ lời
            logging.info("Không lấy được ảnh cho AI đọc công thức (%s)", str(e)[:100])
            images, times = [], []
        job.step = "AI đọc công thức + đối chiếu định lượng với lời gốc"
        ai = await prompt_engine.recipe_facts(title, desc, texts, images, times)
        if ai is None:
            if config.POST_RECIPE == "1" or config.TIPS != "off":
                job.note("ℹ️ AI đọc công thức lỗi: bài đăng không kèm công thức. ")
            return None
        if images:
            ai["_img_times"] = times
        try:
            cache.put_json("recipe", k, ai)
        except OSError:
            pass
    facts = recipe.verify(ai, texts, desc or "")
    # ảnh đầu tiên thấy nguyên liệu -> giây trên video gốc (cho điểm danh nguyên liệu của kế hoạch dựng)
    img_t = ai.get("_img_times") or []
    for it in facts.items:
        it.seen_at = round(float(img_t[it.first_img]), 2) if 0 <= it.first_img < len(img_t) else -1.0
        it.said_at = round(segs[it.seg][0], 2) if 0 <= it.seg < len(segs) else -1.0
    # chỉ số đoạn của AI là trên danh sách đã lọc -> mốc giây trên video gốc để kế hoạch dựng dùng được
    for st in facts.steps:
        st.t0, st.t1 = round(segs[st.a][0], 2), round(segs[st.b][1], 2)
    return facts


def apply(job: Job, facts: recipe.Facts | None) -> None:
    """Ghi kết quả vào job: bảng định lượng (màn duyệt), ghi chú, chặn tự đăng khi cần."""
    if facts is None:
        return
    job.recipe = facts.to_dict()
    if not facts.is_recipe:
        if job.source == Source.AUTO and config.AUTO_SKIP_ADS == "1":
            job.no_autopost = f"{recipe.NOT_RECIPE} (AI đọc lời gốc)"
        job.note("ℹ️ AI thấy video này không phải công thức nấu ăn: bài đăng không kèm công thức. ")
        return
    job.note(f"ℹ️ {recipe.report(facts)} ")
    if facts.fixes:
        job.note(f"ℹ️ Công thức: đã sửa {len(facts.fixes)} câu AI viết có số / nguyên liệu không có trong video. ")
    if facts.conflicts and config.QTY_CHECK == "strict":
        job.no_autopost = job.no_autopost or ("định lượng mâu thuẫn giữa lời gốc và mô tả: "
                                              + ", ".join(x.name for x in facts.conflicts[:3]))


def post_parts(job: Job) -> tuple[str, str]:
    """(khối công thức cho Facebook / YouTube, một dòng nguyên liệu cho TikTok) theo POST_RECIPE / TIPS."""
    facts = recipe.Facts.from_dict(job.recipe)
    if facts is None or not facts.is_recipe or job.mode != Mode.COOK:
        return "", ""
    strict = config.QTY_CHECK != "soft"
    full = config.POST_RECIPE == "1"
    tips = config.TIPS != "off"
    if not full and not tips:
        return "", ""
    block = recipe.recipe_block(facts, strict=strict, tips=tips, steps=full) if full else \
        "\n".join(f"💡 MẸO: {t['text']}" for t in facts.tips[:2])
    line = recipe.compact_ingredients(facts, strict) if full else ""
    return block, line
