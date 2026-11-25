"""(Botkeep) Ý truyện bé: AI nghĩ nhiều ý, AI khác chọn ý hay nhất, rồi mới viết truyện (1.4.1).

Phản hồi 21/11 (job 7): truyện «chưa gây cấn, na ná nhau». Nguyên nhân là khuôn: mỗi khung trong data.json quy định từng cảnh
làm gì (thử ba lần, bạn giúp, cảnh 7 nói bài học), khung có khác nhau thì người xem vẫn nhận ra cùng một nhịp. Giờ khung chỉ
là MỘT gợi ý trong số các ý; độ đa dạng đến từ khâu chọn:
  1. Một lượt Gemini nghĩ N_PITCHES ý truyện khác kiểu nhau (bí ẩn, giải cứu, hài, đua giờ, hiểu lầm, phiêu lưu…) cho đúng
     nhân vật chính, bối cảnh, giá trị gợi ý, tránh ý / chi tiết của các truyện gần đây.
  2. Mã bỏ ý thiếu, quá dài, có từ cấm, trùng tiêu đề truyện cũ.
  3. Một lượt Gemini chấm từng ý (mở đầu có chuyện ngay, hồi hộp, bất ngờ bé không đoán được, dễ hiểu cho bé 3-6, khác các
     truyện gần đây) và chọn.
Ý đã chọn vào brief["pitch"]; prompts.user_prompt viết truyện theo ý đó, tự quyết diễn biến từng cảnh. Lỗi gì cũng trả None:
truyện viết theo ý gợi ý của khung như trước (không quy định từng cảnh)."""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from rmvideo.kids import story as story_mod
from rmvideo.kids import writer

N_PITCHES = 4
RECENT = 6                      # số truyện gần nhất đưa vào lời nhắc «đừng lặp ý»
TIMEOUT = 60.0
KINDS = ("bí ẩn", "giải cứu", "hài", "đua giờ", "hiểu lầm", "phiêu lưu nhỏ", "tưởng tượng", "tình bạn", "khám phá")
MOODS = tuple(__import__("rmvideo.kids.bgm", fromlist=["MOODS"]).MOODS)     # màu nhạc = kho nhạc bé (một nguồn)

PITCH_PROMPT = """Bạn là biên kịch truyện tranh hoạt hình ngắn cho bé 3-6 tuổi (video dọc ~80 giây, 8 cảnh, mỗi cảnh một bức tranh có 1-2 nhân vật). Nghĩ {n} Ý TRUYỆN thật khác nhau để chọn ra ý hay nhất.
Thế giới: {world}. Bốn bạn nhỏ: {cast}.
Nhân vật chính: {main}. Bối cảnh: {setting}.
Giá trị nhẹ nhàng truyện gửi gắm (thể hiện qua việc nhân vật làm, không giảng): {lesson}.
Đồ vật / con vật / việc trong danh sách này: {things}.
Một hướng gợi ý (có thể dùng, đổi, hoặc bỏ hẳn): {seed}
Các truyện gần đây (KHÔNG lặp lại ý, rắc rối, đồ vật chính hay cách giải quyết của chúng):
{recent}
Yêu cầu mỗi ý:
- Mỗi ý một KIỂU khác nhau, chọn trong: {kinds}.
- MỞ ĐẦU đã có chuyện xảy ra ngay (điều lạ, rắc rối, tiếng động bất ngờ), có lúc HỒI HỘP, và một BẤT NGỜ mà bé không đoán trước; kết vui hoặc ấm.
- Đơn giản, bé 3-6 tuổi hiểu được; an toàn (không bạo lực, không đáng sợ, không ai bị phạt).
- Không theo khuôn «thử ba lần, bạn tới giúp, nói bài học».
- "premise": 2-3 câu NGẮN tiếng Việt (tối đa 70 chữ) kể chuyện gì xảy ra ngay đầu, căng nhất ở đâu, bất ngờ gì, kết ra sao.
- "things": ĐÚNG 3 thứ chép nguyên văn từ danh sách đồ vật / con vật / việc ở trên mà ý này dùng TỰ NHIÊN (là thứ nhân vật cầm, thấy, làm trong chuyện; bé sẽ học tên tiếng Anh của chúng).
- "title": tiêu đề gây tò mò có tên {main}, không lộ kết.
- "mood": playful (vui nhộn) / curious (tò mò, hồi hộp nhẹ) / tender (ấm áp).
Trả về DUY NHẤT JSON: {{"pitches": [{{"kind": "...", "title": "...", "premise": "...", "mood": "...", "things": ["...", "...", "..."]}}]}}"""

JUDGE_PROMPT = """Bạn là biên tập viên kênh truyện hoạt hình cho bé có hàng triệu lượt xem. Ba mẹ và bé đang lướt video: chỉ dừng lại nếu ngay giây đầu có chuyện, và xem tới cuối nếu truyện hồi hộp, có bất ngờ. Chấm từng ý truyện 1-10:
(1) mở đầu có chuyện xảy ra ngay, khiến muốn biết chuyện gì tiếp theo;
(2) có hồi hộp và một bất ngờ bé không đoán được (ý theo khuôn «thử ba lần, bạn giúp, rút bài học» tối đa 5 điểm);
(3) bé 3-6 tuổi hiểu được, kể vừa 8 cảnh, mỗi cảnh 1-2 nhân vật;
(4) khác hẳn các truyện gần đây: {recent}
{items}
Trả về DUY NHẤT JSON: {{"scores": [{{"i": số ý, "score": điểm}}], "best": số ý tốt nhất}}"""

try:
    from pydantic import BaseModel

    class _Pitch(BaseModel):
        kind: str
        title: str
        premise: str
        mood: str = "playful"
        things: list[str] = []

    class PitchesOut(BaseModel):
        pitches: list[_Pitch]

    class _Score(BaseModel):
        i: int
        score: float

    class JudgeOut(BaseModel):
        scores: list[_Score]
        best: int
except Exception:                                        # pragma: no cover
    PitchesOut = JudgeOut = None  # type: ignore[assignment,misc]


def recent_lines(stories: Sequence[dict], n: int = RECENT) -> list[str]:
    """«Tiêu đề — ý» của n truyện gần nhất đã làm xong (bộ nhớ kids_auto)."""
    done = [s for s in sorted(stories, key=lambda s: float(s.get("created_at") or 0)) if s.get("title")][-n:]
    out = []
    for s in done:
        line = str(s["title"])
        if s.get("premise"):
            line += f" — {s['premise']}"
        out.append(line[:240])
    return out


def seed_text(brief: dict, data: dict) -> str:
    """Ý gợi ý của khung truyện đề đã bốc (điền tên nhân vật); không có khung thì «không có»."""
    return story_mod.plot_premise(brief, data) or "(không có)"


PREMISE_MAX = 120               # chữ; ý dài hơn là lỗi MỀM: chỉ loại khi còn ý khác đạt (job 3 ngày 24/11: 4/4 ý 98-110 chữ bị loại hết)
SOFT = ("ý ",)


def problems(p: dict, recent_titles: Sequence[str]) -> list[str]:
    why = []
    prem, title = p.get("premise", ""), p.get("title", "")
    if not 8 <= len(prem.split()) <= PREMISE_MAX:
        why.append(f"ý {len(prem.split())} chữ")
    if not 6 <= len(title) <= story_mod.TITLE_MAX:
        why.append("tiêu đề")
    low = f"{title} {prem}".lower()
    bad = [b for b in story_mod.BANNED if b in low]
    if bad:
        why.append("từ cấm " + ", ".join(bad))
    if title.strip().lower() in {t.strip().lower() for t in recent_titles}:
        why.append("trùng tiêu đề truyện cũ")
    return why


def _parse(raw: str) -> list[dict]:
    import prompt_engine
    out = []
    for x in prompt_engine.loads_lenient(raw).get("pitches") or []:
        if not isinstance(x, dict):
            continue
        p: dict[str, Any] = {k: story_mod.south_kids(re.sub(r"\s+", " ", str(x.get(k) or "")).strip().strip('"«»'))
                             for k in ("kind", "title", "premise")}
        p["mood"] = str(x.get("mood") or "").strip().lower()
        if p["mood"] not in MOODS:
            p["mood"] = "playful"
        p["things"] = [re.sub(r"\s+", " ", str(t)).strip().strip('"«»') for t in (x.get("things") or [])
                       if str(t).strip()][:4]
        if p["premise"]:
            out.append(p)
    return out


async def _ask(prompt: str, schema: Any, models: Sequence[str]) -> str:
    """Gọi Gemini lần lượt theo danh sách model (model viết truyện trước, model dự phòng sau)."""
    import prompt_engine
    last: Exception | None = None
    order = [m for m in models if not writer.is_down(m)] or list(models)   # model vừa quá tải: bỏ qua (writer.DOWN_SECS)
    for m in order:
        try:
            return await prompt_engine.call_gemini(prompt, schema=schema, label="Ý truyện", timeout=TIMEOUT, model=m)
        except Exception as e:
            last = e
            if writer.model_busy(e):
                writer.mark_down(m)
            logging.warning("Ý truyện: %s lỗi (%s)", m or "model mặc định", writer.err_text(e))
    raise last or RuntimeError("không có model")


async def choose(brief: dict, data: dict, stories: Sequence[dict], models: Sequence[str] = ("",),
                 log: Any = logging.info) -> dict | None:
    """Nghĩ N_PITCHES ý truyện, chấm, trả ý thắng {"kind", "title", "premise", "mood", "score", "n"} hoặc None."""
    chars = data["characters"]
    main = chars.get(brief.get("main", ""), {}).get("name_vi", "")
    lesson = story_mod.by_id(data["lessons"], brief.get("lesson", "")).get("vi", "") or "(tự chọn)"
    setting = story_mod.by_id(data["settings"], brief.get("setting", "")).get("vi", "")
    cast = "; ".join(f"{c['name_vi']} ({c['personality_vi']}; nét riêng: {c.get('quirk_vi', '')})" for c in chars.values())
    things = ", ".join(w["vi"] for w in story_mod.must_mention(brief, data)) or "(tự chọn)"
    recent = recent_lines(stories)
    avoid = [str(x) for x in brief.get("avoid") or [] if str(x).strip()]
    recent_txt = "\n".join(f"- {x}" for x in recent) or "- (chưa có)"
    if avoid:
        recent_txt += "\n- chi tiết đã dùng gần đây: " + ", ".join(avoid)
    models = [m for m in dict.fromkeys(models)] or [""]
    try:
        raw = await _ask(PITCH_PROMPT.format(n=N_PITCHES, world=data["world"]["name"], cast=cast, main=main, setting=setting,
                                             lesson=lesson, things=things, seed=seed_text(brief, data), recent=recent_txt,
                                             kinds=", ".join(KINDS)), PitchesOut, models)
        pool = _parse(raw)
    except Exception as e:
        log(f"Ý truyện: AI nghĩ ý lỗi ({writer.err_text(e)}), viết theo ý gợi ý của khung")
        return None
    titles = [x.split(" — ")[0] for x in recent]
    ok, soft = [], []
    for p in pool:
        why = problems(p, titles)
        log(f"Ý truyện ({p['kind']}){' LOẠI: ' + '; '.join(why) if why else ''}: {p['title']} — {p['premise'][:160]}")
        if not why:
            ok.append(p)
        elif all(w.startswith(SOFT) for w in why):
            soft.append(p)
    if not ok and soft:                            # mọi ý đều chỉ lỗi mềm (dài): vẫn dùng, còn hơn viết không có ý
        log(f"Ý truyện: {len(soft)} ý chỉ dài quá, vẫn chấm")
        ok = soft
    if not ok:
        return None
    best, scores = 0, {}
    if len(ok) >= 2:
        items = "\n".join(f"{k} | {p['kind']} | {p['title']} — {p['premise']}" for k, p in enumerate(ok))
        try:
            import prompt_engine
            d = prompt_engine.loads_lenient(await _ask(JUDGE_PROMPT.format(recent="; ".join(titles) or "(chưa có)",
                                                                           items=items), JudgeOut, models))
        except Exception as e:
            log(f"Ý truyện: AI chấm lỗi ({writer.err_text(e)}), lấy ý đầu")
            d = {}
        for x in d.get("scores") or []:
            try:
                i = int(x["i"])
                if 0 <= i < len(ok):
                    scores[i] = float(x["score"])
            except (TypeError, ValueError, KeyError):
                continue
        try:
            best = int(d.get("best", -1))
        except (TypeError, ValueError):
            best = -1
        if scores and (not 0 <= best < len(ok) or scores.get(best, 0) < max(scores.values())):
            best = max(scores, key=lambda k: scores[k])
        if not 0 <= best < len(ok):
            best = 0
    p = dict(ok[best], score=scores.get(best), n=len(ok))
    log(f"Ý truyện chọn ({p['kind']}, {p['score'] if p['score'] is not None else '?'} điểm): {p['title']}")
    return p
