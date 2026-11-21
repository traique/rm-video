"""1.4.1: truyện bé — AI nghĩ nhiều ý truyện, AI khác chọn ý hay nhất, rồi mới viết; lời nhắc không còn quy định từng cảnh
(phản hồi 21/11: «chưa gây cấn, na ná nhau», «ép khung có cứng nhắc không»)."""
import asyncio
import json
import random

import config
import prompt_engine
from rmvideo.kids import bgm, pitch, prompts, story

DATA = story.load()
GOOD = [{"kind": "bí ẩn", "title": "Ai lấy mất hộp bánh của Gấu Nâu?",
         "premise": "Hộp bánh biến mất, chỉ còn vụn bánh dẫn ra gốc cây. Hoá ra một chú nhím con đang đói.", "mood": "curious"},
        {"kind": "hài", "title": "Chiếc nón biết bay",
         "premise": "Gió thổi bay chiếc nón, nón đáp lên đầu đủ thứ buồn cười rồi rơi trúng chỗ không ngờ.", "mood": "playful"},
        {"kind": "giải cứu", "title": "Đêm trăng của Sóc Nhỏ",
         "premise": "Một con ma xuất hiện trong rừng làm cả xóm sợ hãi, cuối cùng hoá ra là chiếc khăn bay.", "mood": "curious"},
        {"kind": "hiểu lầm", "title": "Bạn giấu gì sau lưng?",
         "premise": "Gấu Nâu giấu giấu một thứ, Thỏ Trắng tưởng bạn giận mình; hoá ra là quà cho Thỏ Trắng.", "mood": "tender"}]


def _brief():
    b = story.pick(DATA, [], random.Random(4))
    return b


def test_choose_filters_and_judges(monkeypatch):
    calls = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        calls.append((prompt, model))
        if prompt.startswith("Bạn là biên kịch truyện tranh"):
            return json.dumps({"pitches": GOOD}, ensure_ascii=False)
        return json.dumps({"scores": [{"i": 0, "score": 6}, {"i": 1, "score": 9.5}, {"i": 7, "score": 10}], "best": 0})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    stories = [{"created_at": 1, "title": "Chiếc nón biết bay", "premise": "Nón bay khắp xóm."},
               {"created_at": 2, "title": "Sóc Nhỏ và chiếc hộp đồ chơi bị mất"}]
    brief = _brief()
    got = asyncio.run(pitch.choose(brief, DATA, stories, ["m1", "m2"], log=lambda s: None))
    # «con ma» là từ cấm, «Chiếc nón biết bay» trùng truyện cũ -> còn 2 ý; «best» lệch điểm chấm thì theo điểm
    assert got["title"] == "Bạn giấu gì sau lưng?" and got["mood"] == "tender" and got["n"] == 2 and got["score"] == 9.5
    gen = calls[0][0]
    assert "Chiếc nón biết bay — Nón bay khắp xóm." in gen and "Sóc Nhỏ và chiếc hộp đồ chơi bị mất" in gen
    assert "Không theo khuôn «thử ba lần, bạn tới giúp, nói bài học»" in gen and calls[0][1] == "m1"
    main = DATA["characters"][brief["main"]]["name_vi"]
    assert f"Nhân vật chính: {main}" in gen and "Một hướng gợi ý" in gen
    assert "con ma" not in calls[1][0] and "tối đa 5 điểm" in calls[1][0]


def test_choose_falls_back_model_then_gives_up(monkeypatch):
    seen = []

    async def busy_first(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        seen.append(model)
        if model == "m1":
            raise RuntimeError("503 UNAVAILABLE")
        if prompt.startswith("Bạn là biên kịch truyện tranh"):
            return json.dumps({"pitches": GOOD[:1]}, ensure_ascii=False)
        raise AssertionError("một ý hợp lệ: không cần chấm")
    monkeypatch.setattr(prompt_engine, "_call_gemini", busy_first)
    got = asyncio.run(pitch.choose(_brief(), DATA, [], ["m1", "m2"], log=lambda s: None))
    assert got["kind"] == "bí ẩn" and seen == ["m1", "m2"]

    async def down(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(prompt_engine, "_call_gemini", down)
    assert asyncio.run(pitch.choose(_brief(), DATA, [], ["m1"], log=lambda s: None)) is None


def test_prompt_follows_pitch_not_scene_beats():
    brief = _brief()
    u = prompts.user_prompt(brief, DATA)
    beats = story.plot_beats(brief, DATA)
    assert not any(b in u for b in beats)                                    # không còn 8 nhịp quy định từng cảnh
    assert "Ý gợi ý (có thể đổi cho hay hơn)" in u and story.plot_premise(brief, DATA) in u
    assert "KHÔNG theo khuôn «thử ba lần, bạn tới giúp, nói bài học»" in u and "BỘ XƯƠNG" in u
    brief["pitch"] = {k: GOOD[0][k] for k in ("kind", "title", "premise", "mood")}
    u2 = prompts.user_prompt(brief, DATA)
    assert "Ý TRUYỆN đã chọn" in u2 and GOOD[0]["premise"] in u2 and "Chuyện xảy ra:" not in u2
    assert "Ý gợi ý" not in u2 and "điệp khúc kiểu" not in u2
    assert story.describe(brief, DATA).count("ý: Ai lấy mất hộp bánh") == 1
    assert bgm.mood_for(brief, DATA) == "curious"
    brief["pitch"]["mood"] = "lạ"
    assert bgm.mood_for(brief, DATA) == bgm.mood_for({"plot": brief["plot"]}, DATA)


def test_recent_lines_and_premises():
    stories = [{"created_at": k, "title": f"Truyện {k}", "premise": "ý " * 3} for k in range(10)] + [{"created_at": 99}]
    lines = pitch.recent_lines(stories)
    assert len(lines) == pitch.RECENT and lines[-1].startswith("Truyện 9 — ")
    assert all(p.get("premise") and "{main}" in p["premise"] or "{friend}" in p["premise"] for p in DATA["plots"])
    assert config.KIDS_GEMINI_MODEL is not None
