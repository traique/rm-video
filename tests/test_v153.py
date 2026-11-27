"""1.5.3: bộ nhớ truyện bé — 3 đồ vật của ý đã chọn tới người viết (flow bỏ rơi từ 1.4.2); truyện viết không qua bước nghĩ ý
vẫn nhớ cốt truyện; 14 truyện gần nhất (một tuần) trong lời nhắc nghĩ ý + lời nhắc viết; trùng tiêu đề so với cả bộ nhớ."""
import asyncio
import json
import random

import config
import prompt_engine
from rmvideo.kids import pitch, prompts, story

DATA = story.load()
GOOD = [{"kind": "bí ẩn", "title": "Truyện 0", "premise": "Hộp bánh biến mất, chỉ còn vụn bánh dẫn ra gốc cây xa.",
         "mood": "curious", "things": ["hộp bánh", "cái giỏ", "trái táo"]},
        {"kind": "hài", "title": "Chiếc nón biết bay",
         "premise": "Gió thổi bay chiếc nón, nón đáp lên đầu đủ thứ buồn cười rồi rơi trúng chỗ không ngờ.", "mood": "playful"},
        {"kind": "hiểu lầm", "title": "Bạn giấu gì sau lưng?",
         "premise": "Gấu Nâu giấu một thứ, Thỏ Trắng tưởng bạn giận mình; hoá ra là quà cho Thỏ Trắng.", "mood": "tender"}]
STORIES = [{"created_at": k, "title": f"Truyện {k}", "premise": f"ý số {k} của truyện"} for k in range(20)]


def _stories_memory():
    return [dict(s, job=k) for k, s in enumerate(STORIES)]


def test_pitch_things_reach_writer_through_flow(monkeypatch):
    """Bước nghĩ ý chọn 3 đồ vật; flow/kids.py chỉ giữ kiểu / tiêu đề / ý / màu nhạc nên «Ý này dùng: …» chưa bao giờ tới
    người viết (job 12-15)."""
    from rmvideo.app.flow import kids as flow
    from rmvideo.kids import llm
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "KIDS_LLM_PROVIDER", "gemini")
    got = {"kind": "bí ẩn", "title": "Gấu Nâu và chiếc giỏ tự chuyển động", "premise": "Cái giỏ tự nhích lên đồi, trong là bé nhím.",
           "mood": "curious", "things": ["cái giỏ", "trái chuối", "nụ cười"], "n": 4, "score": 7.5}

    async def choose(brief, data, stories, models, log=None):
        return dict(got)
    seen = {}

    def generate_story(spec, data, tmp, log, srv=None):
        seen["brief"] = spec["brief"]
        return {"title": got["title"], "scenes": [{"text": "x"}]}, []
    monkeypatch.setattr(pitch, "choose", choose)
    monkeypatch.setattr(llm, "generate_story", generate_story)

    class Job:
        id, step, brief = 15, "", None

        def note(self, s):
            pass
    brief = story.pick(DATA, [], random.Random(5))
    st, _ = asyncio.run(flow.write_on_botkeep(Job(), brief, DATA))
    assert st["title"] == got["title"] and seen["brief"]["pitch"]["things"] == ["cái giỏ", "trái chuối", "nụ cười"]
    assert "Ý này dùng: «cái giỏ», «trái chuối», «nụ cười»" in prompts.user_prompt(seen["brief"], DATA)
    assert "Các truyện gần đây" not in prompts.user_prompt(seen["brief"], DATA)     # có ý rồi: không kèm danh sách truyện cũ


def test_premise_of_sums_opening_and_ending():
    st = {"scenes": [{"text": "  Bỗng   cái giỏ nhích lên đồi! "}, {"text": "Giữa."}, {"text": "Cả bọn cười vang bên bé nhím."}]}
    assert story.premise_of(st) == "Bỗng cái giỏ nhích lên đồi! … Cả bọn cười vang bên bé nhím."
    long = {"scenes": [{"text": "mở " * 200}, {"text": "kết " * 200}]}
    out = story.premise_of(long)
    assert len(out) <= story.LINE_MAX and out.count("…") == 3 and " … " in out      # mỗi nửa cắt một dấu + dấu nối giữa
    assert story.premise_of({"scenes": [{"text": "Một cảnh thôi."}]}) == "Một cảnh thôi." and story.premise_of({}) == ""


def test_remember_done_keeps_plot_without_pitch(monkeypatch, tmp_path):
    """Job 15 kiểu cũ (nghĩ ý lỗi -> viết thẳng) hay Qwen trên Kaggle: bộ nhớ chỉ còn tiêu đề, lượt nghĩ ý sau không biết
    cốt truyện để tránh. Giờ tóm từ lời kể; có ý thì vẫn ghi ý."""
    import kids_auto
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "k.json"))

    class Job:
        id = 7
    brief = {"lesson": "honesty", "setting": "park", "conflict": "lied", "words": [], "main": "white_rabbit"}
    st = {"title": "Chiếc giỏ", "prop_vi": "cái giỏ",
          "scenes": [{"text": "Cái giỏ tự nhích lên đồi cỏ."}, {"text": "Hoá ra bé nhím trốn trong giỏ, cả bọn cười."}]}
    kids_auto.remember_done(Job, brief, st)
    rec = json.load(open(tmp_path / "k.json", encoding="utf-8"))["stories"][-1]
    assert rec["premise"] == "Cái giỏ tự nhích lên đồi cỏ. … Hoá ra bé nhím trốn trong giỏ, cả bọn cười." and rec["kind"] == ""
    assert story.recent_lines([rec]) == ["Chiếc giỏ — Cái giỏ tự nhích lên đồi cỏ. … Hoá ra bé nhím trốn trong giỏ, cả bọn cười."]
    Job.id = 8
    kids_auto.remember_done(Job, dict(brief, pitch={"kind": "hài", "premise": "Ý đã chọn.", "title": "T"}), st)
    rec = json.load(open(tmp_path / "k.json", encoding="utf-8"))["stories"][-1]
    assert rec["premise"] == "Ý đã chọn." and rec["kind"] == "hài"


def test_choose_sees_a_week_and_rejects_any_old_title(monkeypatch):
    """Lời nhắc nghĩ ý / chấm: 14 truyện gần nhất (trước 6 = 3 ngày); trùng tiêu đề so với CẢ bộ nhớ (400 truyện)."""
    calls = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        calls.append(prompt)
        if prompt.startswith("Bạn là biên kịch truyện tranh"):
            return json.dumps({"pitches": GOOD}, ensure_ascii=False)
        return json.dumps({"scores": [{"i": 0, "score": 6}, {"i": 1, "score": 9}], "best": 1})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    got = asyncio.run(pitch.choose(story.pick(DATA, [], random.Random(4)), DATA, _stories_memory(), ["m1"], log=lambda s: None))
    assert got["title"] == "Bạn giấu gì sau lưng?" and got["n"] == 2          # «Truyện 0» trùng tiêu đề 20 truyện trước: loại
    gen, judge = calls
    assert "Truyện 6 — ý số 6 của truyện" in gen and "Truyện 19 — ý số 19" in gen and "Truyện 5" not in gen
    assert "Truyện 6" in judge and "Truyện 5" not in judge and pitch.RECENT == story.RECENT_IDEAS == 14


def test_writer_prompt_lists_recent_stories_when_no_pitch():
    brief = story.pick(DATA, _stories_memory(), random.Random(2))
    assert brief["recent"] == story.recent_lines(STORIES) and len(brief["recent"]) == 14
    u = prompts.user_prompt(brief, DATA)
    assert "Các truyện gần đây (KHÔNG lặp ý" in u and "- Truyện 19 — ý số 19 của truyện" in u and "Truyện 5" not in u
    assert "Ý gợi ý (có thể đổi cho hay hơn)" in u
    assert "Các truyện gần đây" not in prompts.user_prompt({**brief, "pitch": GOOD[2]}, DATA)
    assert "Các truyện gần đây" not in prompts.user_prompt(story.pick(DATA, [], random.Random(2)), DATA)   # chưa có truyện
