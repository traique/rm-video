"""1.5.3: bộ nhớ truyện bé — 3 đồ vật của ý đã chọn tới người viết (flow bỏ rơi từ 1.4.2); truyện viết không qua bước nghĩ ý
vẫn nhớ cốt truyện; 14 truyện gần nhất (một tuần) trong lời nhắc nghĩ ý + lời nhắc viết; trùng tiêu đề so với cả bộ nhớ."""
import asyncio
import json
import os
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


# ---------------- soát lại 1.5.0 → 1.5.2 (reviewer độc lập + tự soát) ----------------

def test_strong_segments_keep_short_segment_at_block_boundary():
    """seg_out lùi mốc 0,02-0,04s để đoạn liền kề không phủ lên khối chèn (1.5.0); bộ lọc «đoạn còn nguyên» cũ đòi đủ 95%
    độ dài nên đoạn ngắn («Rồi.» 0,3s) ở đúng ranh giới khối bị bỏ. Đoạn bị kế hoạch cắt thật vẫn bị bỏ."""
    from rmvideo.app.flow import remake
    from rmvideo.media import edl
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 10.0), edl.Piece(0.0, 3.0, role="side", tag="montage"),
                                edl.Piece(10.0, 20.0)], level="strong")       # khối chèn 3s đúng ranh giới 10s
    assert plan.seg_out(10.0, 10.3) == (13.02, 13.3)                           # đầu đoạn lùi 0,02s để nằm sau khối
    out = remake.strong_segments(plan, [(9.0, 10.0, "trước"), (10.0, 10.3, "Rồi."), (10.3, 12.0, "sau")])
    assert out == [(9.0, 10.0, "trước"), (13.02, 13.3, "Rồi."), (13.3, 15.0, "sau")]
    cut = edl.EditPlan(pieces=[edl.Piece(0.0, 10.0), edl.Piece(11.0, 20.0)], level="strong")    # bỏ 10-11s
    assert remake.strong_segments(cut, [(10.5, 12.0, "bị cắt mất nửa"), (12.0, 14.0, "nguyên")]) == [(11.0, 13.0, "nguyên")]


def test_short_pass_recomputes_gate_but_reuses_separated_audio(tmp_path):
    """Bản ngắn: cổng tắt tiếng gốc (trục RA của bản đầy đủ) phải tính lại theo kế hoạch bản ngắn; file tiếng nền đã tách
    (trục gốc) dùng chung được. Trước 1.5.3 _enter_short giữ nguyên việc của bản đầy đủ -> bản ngắn tắt tiếng sai chỗ."""
    from types import SimpleNamespace
    from rmvideo.app.flow import remake
    from rmvideo.media import edl

    async def main():
        r = remake.Remake.__new__(remake.Remake)
        for k in ("segs", "cuts", "speech", "cards", "tasks"):
            setattr(r, k, [])
        r.out_dur, r.render_dur, r.edit, r.tmp, r.ass, r.pre_cover, r.is_long, r.tail = 30.0, 30.0, None, str(tmp_path), "", None, True, 0.0
        r.short_pass = False
        r.job = SimpleNamespace(srt="", final_segments=[], script="", out_dur=30.0)
        plan = edl.EditPlan(pieces=[edl.Piece(0.0, 20.0)])

        async def gate():
            return None, [(1.0, 2.0)]
        r.amb_task = asyncio.create_task(gate())
        await r.amb_task
        saved = r._enter_short(plan, [], [], [(0.5, 1.0)], 0.0)
        assert r.amb_task is None and r.short_pass                            # cổng theo trục ra: bản ngắn tính lại
        await r._exit_short(saved)
        assert r.amb_task is not None and r.amb_task.result() == (None, [(1.0, 2.0)]) and not r.short_pass

        async def sep():
            return str(tmp_path / "amb.wav"), None
        r.amb_task = asyncio.create_task(sep())
        await r.amb_task
        saved = r._enter_short(plan, [], [], [], 0.0)
        assert r.amb_task is not None and r.amb_task.result()[0].endswith("amb.wav")   # đã tách: dùng chung
        await r._exit_short(saved)
    asyncio.run(main())


def test_vad_download_timeout_and_failure_memory(monkeypatch, tmp_path):
    """Tải model Silero: có hạn chờ mỗi lần đọc mạng (urlretrieve cũ không có: mạng treo là job treo), tải hỏng thì một giờ
    sau mới thử lại (mỗi job bếp không tách giọng đều gọi; cổng vẫn theo mốc lời máy nghe)."""
    import io
    import pytest
    from rmvideo.media import vad
    monkeypatch.setattr(vad, "_DIR", str(tmp_path))
    monkeypatch.setattr(vad, "_verified", set())
    monkeypatch.setattr(vad, "_failed", {})
    seen = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(url, timeout=None):
        seen.append((url, timeout))
        raise OSError("mạng treo")
    monkeypatch.setattr(vad.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="không tải được model"):
        vad.model_path()
    assert [u for u, _ in seen] == list(vad.MODEL_URLS) and all(t == vad.DL_TIMEOUT for _, t in seen)
    with pytest.raises(RuntimeError, match="một giờ sau thử lại"):
        vad.model_path()
    assert len(seen) == len(vad.MODEL_URLS)                                   # trong một giờ: không tải lại
    monkeypatch.setattr(vad, "RETRY_SECS", 0.0)
    monkeypatch.setattr(vad.urllib.request, "urlopen", lambda url, timeout=None: _Resp(b"model gia"))
    with pytest.raises(RuntimeError, match="sai checksum"):                   # hết hạn nhớ: tải lại, file sai bị từ chối
        vad.model_path()
    assert not os.path.exists(os.path.join(str(tmp_path), vad.MODEL_NAME)) and vad._failed["why"].startswith("sai checksum")
    vad._fetch("u", str(tmp_path / "f.bin"))
    assert open(tmp_path / "f.bin", "rb").read() == b"model gia"
