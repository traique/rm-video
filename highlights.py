"""Chế độ ✨ Đoạn hay: AI đọc lời thoại (có mốc thời gian) + xem ảnh của video dài, chấm điểm và chọn tối đa
HL_MAX đoạn hay nhất (mỗi đoạn HL_MIN-HL_LEN giây, trọn một ý, không phải quảng cáo/chào hỏi). Ranh giới được dời
về chỗ ngưng nói / chuyển cảnh gần nhất để không cắt ngang câu; các đoạn không chồng nhau.

Không có lời thoại (video ASMR không nói) hoặc AI lỗi: trả [] để caller quay về cắt phần đều nhau như chế độ ✂️.
"""
import logging

import config

HL_MAX = 3
HL_MIN, HL_LEN = 30.0, 90.0
LINE_MAX = 9.0             # gộp đoạn lời thành dòng ≤ 9 giây cho prompt gọn
SNAP = 3.0                 # dời ranh giới tối đa 3 giây về chỗ ngưng nói / chuyển cảnh

HIGHLIGHT_PROMPT = """Bạn là biên tập viên video ngắn. Dưới đây là lời thoại (có mốc thời gian, tính bằng giây) của một video dài {dur:.0f} giây, tiêu đề «{title}». Hãy chọn tối đa {n} ĐOẠN HAY NHẤT để cắt thành video ngắn đăng TikTok/Reels/Shorts, mỗi đoạn dài {lo:.0f}-{hi:.0f} giây.

Tiêu chí một đoạn hay:
- TRỌN VẸN: bắt đầu ở đầu một ý/một bước (không bắt đầu giữa câu), kết thúc khi ý đó xong; người xem không cần xem phần trước vẫn hiểu.
- CÓ ĐIỂM NHẤN: kết quả hấp dẫn (món chín vàng, cắt ra, nếm thử), mẹo hay, thông tin giá trị, khoảnh khắc thú vị.
- MỞ ĐẦU CUỐN: vài giây đầu đã có việc đáng xem.
- KHÔNG chọn đoạn quảng cáo/kêu gọi mua hàng, chào hỏi đầu video, giới thiệu kênh, xin theo dõi.
Các đoạn KHÔNG được chồng lên nhau. Chấm điểm 1-10 (10 = chắc chắn hút người xem). Dùng đúng mốc thời gian trong lời thoại.{img_note}

LỜI THOẠI ([giây bắt đầu-giây kết thúc] lời):
{lines}
{cuts}
Trả về DUY NHẤT JSON: {{"clips": [{{"start": giây, "end": giây, "score": 1-10, "title": "tiêu đề ngắn cho đoạn", "reason": "vì sao hay"}}]}}"""

IMG_NOTE = "\nKèm theo là ảnh chụp video theo thời gian (\"Ảnh k\" chụp ở giây ghi trong danh sách ẢNH): dùng ảnh để biết đoạn nào có hình đẹp/hấp dẫn."

try:
    from pydantic import BaseModel

    class ClipOut(BaseModel):
        start: float
        end: float
        score: int
        title: str
        reason: str

    class HighlightsOut(BaseModel):
        clips: list[ClipOut]
except Exception:
    HighlightsOut = None


def transcript_lines(segs: list, max_len: float = LINE_MAX, gap: float = 1.0) -> list:
    """[(start, end, text)] -> dòng gộp ≤ max_len giây (ngắt ở khoảng lặng ≥ gap)."""
    out = []
    for a, b, t in segs:
        if out and b - out[-1][0] <= max_len and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], b, out[-1][2] + " " + t)
        else:
            out.append((a, b, t))
    return out


def speech_coverage(segs: list, dur: float) -> float:
    return sum(max(0.0, b - a) for a, b, _ in segs) / max(dur, 1.0)


def build_prompt(title: str, dur: float, segs: list, cuts: list, n: int = HL_MAX, lo: float = HL_MIN,
                 hi: float = HL_LEN, image_times: list | None = None) -> str:
    lines = "\n".join(f"[{a:.1f}-{b:.1f}] {t}" for a, b, t in transcript_lines(segs))
    cut_txt = ("\nĐIỂM CHUYỂN CẢNH (giây): " + ", ".join(f"{c:.1f}" for c in cuts[:200]) + "\n") if cuts else ""
    if image_times:
        cut_txt += "\nẢNH: " + ", ".join(f"Ảnh {k} = giây {t:.0f}" for k, t in enumerate(image_times)) + "\n"
    return HIGHLIGHT_PROMPT.format(dur=dur, title=title or "(không có)", n=n, lo=lo, hi=hi, lines=lines,
                                   cuts=cut_txt, img_note=IMG_NOTE if image_times else "")


def snap_boundary(t: float, cuts: list, silences: list, window: float = SNAP, limit: float | None = None) -> float:
    """Dời mốc t về chỗ cắt tự nhiên gần nhất trong ±window giây (và ≤ limit nếu có): chuyển cảnh nằm trong
    khoảng lặng (tốt nhất) > giữa khoảng lặng > chuyển cảnh. Không có thì giữ nguyên."""
    best, best_score = t, 0.0
    lim = float("inf") if limit is None else limit
    for c in cuts:
        if abs(c - t) <= window and c <= lim:
            in_sil = any(a - 0.1 <= c <= b + 0.1 for a, b in silences)
            score = (3.0 if in_sil else 1.0) - abs(c - t) / (2 * window)
            if score > best_score:
                best, best_score = c, score
    for a, b in silences:
        m = (a + b) / 2
        if abs(m - t) <= window and m <= lim:
            score = 2.0 + min(b - a, 1.5) / 3 - abs(m - t) / (2 * window)
            if score > best_score:
                best, best_score = m, score
    return round(best, 2)


def normalize(clips: list, dur: float, cuts: list, silences: list, n: int = HL_MAX, lo: float = HL_MIN,
              hi: float = HL_LEN) -> list:
    """Kết quả AI -> danh sách đoạn hợp lệ, điểm cao trước: [{start, end, score, title, reason}].
    Bỏ đoạn sai mốc / quá ngắn (< 70% lo) / dài quá 30%; đoạn hơi dài thì thu về đúng giới hạn (mỗi phần remake tối
    đa MAX_DURATION giây, chừa 1.5s cho việc bám khung khoá); dời ranh giới về chỗ cắt tự nhiên; không chồng nhau."""
    cap = min(hi, config.MAX_DURATION - 1.5)
    out = []
    for c in clips or []:
        try:
            a, b = float(c["start"]), float(c["end"])
            score = max(1, min(10, int(c.get("score") or 5)))
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(0.0, a), min(dur, b)
        if b - a < lo * 0.7 or b - a > hi * 1.3:
            continue
        a = snap_boundary(a, cuts, silences)
        b = snap_boundary(min(b, a + cap), cuts, silences, limit=a + cap)
        b = min(b, a + cap, dur)
        if b - a < lo * 0.7:
            continue
        out.append({"start": round(a, 2), "end": round(b, 2), "score": score,
                    "title": str(c.get("title") or "").strip()[:80], "reason": str(c.get("reason") or "").strip()[:200]})
    out.sort(key=lambda x: -x["score"])
    kept = []
    for c in out:
        if all(c["end"] <= k["start"] + 0.5 or c["start"] >= k["end"] - 0.5 for k in kept):
            kept.append(c)
        if len(kept) >= n:
            break
    return kept


async def choose(title: str, desc: str, dur: float, segs: list, cuts: list, silences: list,
                 images: list | None = None, image_times: list | None = None) -> list:
    """Gọi AI chọn đoạn hay (Gemini kèm ảnh -> OpenRouter chỉ lời). Lỗi/thiếu lời thoại: []."""
    import prompt_engine
    if speech_coverage(segs, dur) < 0.15:
        logging.info("Đoạn hay: video ít lời (%.0f%%), không đủ để AI chấm", speech_coverage(segs, dur) * 100)
        return []
    providers = []
    if prompt_engine.genai and config.GEMINI_API_KEY:
        async def _gem():
            pr = build_prompt(title, dur, segs, cuts, image_times=image_times if images else None)
            try:
                return await prompt_engine._call_gemini(pr, images or None, schema=HighlightsOut, label="Ảnh", timeout=90)
            except Exception as e:
                if not images or prompt_engine.is_quota_error(str(e)):
                    raise
                logging.warning("Gemini không nhận ảnh (%s), chọn đoạn hay chỉ theo lời", str(e)[:80])
                return await prompt_engine._call_gemini(build_prompt(title, dur, segs, cuts), schema=HighlightsOut,
                                                        timeout=90)
        providers.append(("gemini", _gem))
    if config.OPENROUTER_API_KEY:
        providers.append(("openrouter", lambda: prompt_engine._call_openrouter(build_prompt(title, dur, segs, cuts))))
    for name, call in providers:
        try:
            d = prompt_engine.loads_lenient(await call())
            clips = normalize(d.get("clips") or [], dur, cuts, silences)
            if clips:
                for c in clips:
                    logging.info("Đoạn hay %.0f-%.0fs điểm %d: %s (%s)", c["start"], c["end"], c["score"], c["title"],
                                 c["reason"][:100])
                return clips
            logging.warning("AI %s không chọn được đoạn hợp lệ", name)
        except Exception as e:
            logging.warning("AI %s chọn đoạn hay lỗi: %s", name, str(e)[:150])
    return []
