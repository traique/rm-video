"""1.4.0: viết nhiều câu hook nhiều kiểu, mã loại câu sai luật, AI chấm điểm chọn câu mạnh nhất; ghi kiểu hook để so
với lượt xem YouTube; cả luồng pipeline ghi chú + lưu kiểu hook."""
import asyncio
import json

import pytest

import config
import pipeline
import prompt_engine
from queue_manager import Job
from rmvideo.app import job_factory, youtube
from rmvideo.app.flow import hooks
from test_pipeline import _video, fakes  # noqa: F401  (fakes: fixture dùng chung)

ORIG = "Thịt bò ướp 15 phút với tỏi băm"


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOOK_PICK", "1")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(prompt_engine, "genai", object())
    monkeypatch.setattr(hooks, "STATE_FILE", str(tmp_path / "hooks.json"))


def _timed(orig0=""):
    segs = [{"i": 0, "start": 0.0, "slot": 3.0, "span": 3.0, "text": "Bò lúc lắc ngon lắm nè!", "orig": orig0,
             "ost": "new"},
            {"i": 1, "start": 3.0, "slot": 4.0, "span": 4.0, "text": "Mình ướp bò mười lăm phút với tỏi nha.",
             "orig": ORIG, "ost": "new"}]
    return {"segments": segs, "script": " ".join(s["text"] for s in segs), "hook": "BÒ LÚC LẮC NGON"}


def test_problems_rules():
    assert hooks.problems("Bò lúc lắc hay bị dai là do bước này nè!", 10, ORIG, "") == []
    assert hooks.problems("Một hai ba bốn năm sáu bảy tám chín mười mười một mười hai", 8, ORIG, "")   # quá dài
    assert "chào hỏi / kêu gọi" in hooks.problems("Xin chào cả nhà, bò mềm lắm", 10, ORIG, "")
    assert "nói như người nấu / lượt xem bịa" in hooks.problems("Mình thử công thức này và bất ngờ luôn", 10, ORIG, "")
    assert "nói như người nấu / lượt xem bịa" in hooks.problems("Công thức triệu view là đây", 10, ORIG, "")
    assert any("30 phút" in w for w in hooks.problems("Chỉ ướp 30 phút là mềm", 10, ORIG, ""))     # số không có trong gốc
    assert any("tôm" in w for w in hooks.problems("Tôm mềm ngọt hết sảy", 10, ORIG, ""))           # nguyên liệu lạ
    # đoạn 0 có lời gốc: câu hook phải giữ đủ nguyên liệu / số của câu đó
    assert any("mất" in w for w in hooks.problems("Mềm tan trong miệng luôn nè", 10, ORIG, ORIG))
    assert hooks.problems("Bò ướp tỏi 15 phút là mềm tan nè", 12, ORIG, ORIG) == []


def test_budget_follows_slot(monkeypatch):
    monkeypatch.setattr(prompt_engine, "_rate", lambda: 3.0)
    assert hooks.budget({"slot": 3.0, "span": 3.0, "orig": ""}) == 8           # 3s x 3 chữ/giây x 0.92
    assert hooks.budget({"slot": 20.0, "span": 20.0, "orig": ""}) == 15        # khe trống dài: tối đa 5,5 giây đọc
    assert hooks.budget({"slot": 1.0, "span": 1.0, "orig": ""}) == 5


def test_improve_picks_best_and_replaces_hook(on, monkeypatch):
    calls = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"hooks": [
                {"kind": "van_de", "text": "Bò lúc lắc hay bị dai là do bước này nè!", "screen": "bò hết dai nhờ bước này"},
                {"kind": "meo", "text": "Mình thử mẹo này và bất ngờ luôn!", "screen": "mẹo bất ngờ"},      # loại
                {"kind": "con_so", "text": "Chỉ ướp 30 phút là mềm!", "screen": "30 phút"},                 # loại: bịa số
                {"kind": "ket_qua", "text": "Bò mềm tan, thấm vị tỏi thơm lừng!", "screen": "bò mềm tan"},
                {"kind": "khac", "text": "Bò lúc lắc hay bị dai là do bước này nè!", "screen": "trùng"}]})  # trùng
        return json.dumps({"scores": [{"i": 0, "score": 5}, {"i": 1, "score": 9}, {"i": 2, "score": 7}], "best": 1})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    monkeypatch.setattr(prompt_engine, "_rate", lambda: 4.0)          # khe 3 giây: tối đa 11 chữ
    timed = _timed()
    got = asyncio.run(hooks.improve(timed, "Bò lúc lắc", "ướp tỏi 15 phút cho bò mềm", teaser=True))
    assert got == {"kind": "van_de", "text": "Bò lúc lắc hay bị dai là do bước này nè!",
                   "screen": "BÒ HẾT DAI NHỜ BƯỚC NÀY", "score": 9.0, "n": 3}
    assert timed["segments"][0]["text"] == got["text"] and timed["hook"] == "BÒ HẾT DAI NHỜ BƯỚC NÀY"
    assert timed["script"].startswith("Bò lúc lắc hay bị dai") and "mười lăm phút" in timed["script"]
    assert "cảnh món thành phẩm" in calls[0] and "«ướp tỏi 15 phút cho bò mềm»" in calls[0] and ORIG in calls[0]
    # danh sách chấm điểm: câu cũ (viết cùng kịch bản) đứng đầu, chỉ các câu qua luật
    assert "0 | viết cùng kịch bản | Bò lúc lắc ngon lắm nè!" in calls[1] and "30 phút" not in calls[1]
    assert "Mình thử" not in calls[1] and calls[1].count("hay bị dai") == 1


def test_improve_keeps_old_hook(on, monkeypatch):
    async def picks_old(prompt, images=None, schema=None, label="", timeout=60):
        if "Viết" in prompt[:200]:
            return json.dumps({"hooks": [{"kind": "to_mo", "text": "Bí mật nằm ở tỏi băm đó nha?", "screen": "bí mật"}]})
        return json.dumps({"scores": [{"i": 0, "score": 8}, {"i": 1, "score": 6}], "best": 0})
    monkeypatch.setattr(prompt_engine, "_call_gemini", picks_old)
    timed = _timed()
    got = asyncio.run(hooks.improve(timed, "Bò lúc lắc"))
    assert got["kind"] == "goc" and timed["segments"][0]["text"] == "Bò lúc lắc ngon lắm nè!"
    assert timed["hook"] == "BÒ LÚC LẮC NGON"

    async def down(*a, **k):
        raise RuntimeError("503 quá tải")
    monkeypatch.setattr(prompt_engine, "_call_gemini", down)
    timed = _timed()
    assert asyncio.run(hooks.improve(timed, "Bò lúc lắc")) is None and timed["segments"][0]["text"] == "Bò lúc lắc ngon lắm nè!"
    monkeypatch.setattr(config, "HOOK_PICK", "0")                     # tắt trong Cài đặt: không gọi AI
    monkeypatch.setattr(prompt_engine, "_call_gemini", None)
    assert asyncio.run(hooks.improve(_timed(), "Bò lúc lắc")) is None


def test_improve_with_original_speech_in_segment0(on, monkeypatch):
    """Đoạn 0 có lời gốc (người gốc nói ngay giây đầu): câu mới phải giữ đủ nguyên liệu / số của câu đó."""
    async def fake(prompt, images=None, schema=None, label="", timeout=60):
        if "Viết" in prompt[:200]:
            assert f"Đoạn 0 có lời gốc «{ORIG}»" in prompt
            return json.dumps({"hooks": [{"kind": "ket_qua", "text": "Mềm tan luôn nè!", "screen": "mềm tan"},
                                         {"kind": "meo", "text": "Bò ướp tỏi 15 phút là mềm tan nè!", "screen": "x"}]})
        assert "Mềm tan luôn" not in prompt
        return json.dumps({"scores": [{"i": 1, "score": 9}], "best": 1})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    monkeypatch.setattr(prompt_engine, "_rate", lambda: 4.0)
    timed = _timed(ORIG)
    got = asyncio.run(hooks.improve(timed, "Bò lúc lắc"))
    assert got["kind"] == "meo" and timed["segments"][0]["copy"] < 0.45


def test_record_and_views_by_kind(on, monkeypatch, tmp_path):
    for job, kind in ((1, "van_de"), (2, "van_de"), (3, "ket_qua"), (4, "meo"), (5, "khong_ro")):
        hooks.record(job, kind, f"câu {job}", "Bò")
    hooks.record(2, "van_de", "câu 2 sửa", "Bò")                          # ghi lại cùng job: không nhân đôi
    assert [i["job"] for i in hooks.load_state()["items"]] == [1, 3, 4, 2]
    assert hooks.by_kind({1: 1000, 2: 3000, 3: 500}) == [("van_de", 2000.0, 2), ("ket_qua", 500.0, 1)]
    monkeypatch.setattr(youtube, "load_state", lambda: {"items": [
        {"ch": "cook", "job": 1, "views": 1200}, {"ch": "cook", "job": 2, "views": 2800},
        {"ch": "cook", "job": 4, "views": None}, {"ch": "kids", "job": 3, "views": 99}]})
    assert hooks.status_lines("cook") == ["Hook theo lượt xem YouTube 24h: vấn đề 2.000 (2 video)"]
    monkeypatch.setattr(youtube, "load_state", lambda: {"items": []})
    assert hooks.status_lines("cook") == []


def test_approved_job_keeps_hook_kind():
    src = Job(url="", mode="cook", duration=0, voice="v", hook_kind="meo")
    assert job_factory.approved(src, [{"i": 0, "start": 0.0, "slot": 3.0, "text": "x"}]).hook_kind == "meo"


def test_pipeline_notes_and_records_hook(fakes, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(config, "TEASER", "0")
    monkeypatch.setattr(hooks, "STATE_FILE", str(tmp_path / "hooks.json"))
    monkeypatch.setattr(hooks, "wanted", lambda: True)
    seen = {}

    async def improve(timed, title, tip="", teaser=False, dish=""):
        seen.update(title=title, teaser=teaser)
        timed["segments"][0]["text"] = "Thịt kho hay bị khô là do bước này nè!"
        return {"kind": "van_de", "text": timed["segments"][0]["text"], "screen": "", "score": 8.5, "n": 6}
    monkeypatch.setattr(hooks, "improve", improve)
    src = str(tmp_path / "up" / "in.mp4")
    _video(src)
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert seen["teaser"] is False and job.hook_kind == "van_de"
    assert "Câu hook (vấn đề, 8.5/10, chọn trong 6 câu): «Thịt kho hay bị khô là do bước này nè!»" in job.error
    assert hooks.load_state()["items"][-1]["job"] == job.id and hooks.load_state()["items"][-1]["kind"] == "van_de"


# ---------------- 1.4.1: câu hook chọn trước, đoạn mở đầu dài vừa câu ----------------

def test_short_or_nameless_hooks_are_rejected():
    """Job 6 (21/11): «Đồ chua bị hăng?» — cụt, không hứa gì; câu không nêu món thì người lướt không biết video về gì."""
    orig = "Nguyên liệu gồm 1 kg cà rốt, 1 kg củ cải trắng, 100 g muối hột. Trộn với muối không cần bỏ nước."
    assert "4 từ (cần 7-11)" in hooks.problems("Đồ chua bị hăng?", 11, orig, "", "đồ chua", "", 7)
    assert "không nêu tên món" in hooks.problems("Ngon lắm luôn nè, ai ăn cũng mê hết trơn luôn", 11, orig, "",
                                                  "đồ chua", "", 7)
    assert hooks.problems("Đồ chua giòn sần sật để cả tháng nhờ bước trộn muối này nè.", 13, orig, "", "đồ chua",
                          "", 7) == []
    assert hooks.names_dish("Cà rốt củ cải ngâm giòn rụm luôn nè", "", "Đồ chua cà rốt củ cải")     # nguyên liệu chính
    assert hooks.names_dish("Câu gì cũng được", "", "")                                           # không biết món: bỏ qua


def test_teaser_secs_fit_the_hook():
    assert hooks.teaser_secs("Đồ chua giòn sần sật để cả tháng nhờ bước trộn muối này nè.", 3.1) == 4.0
    assert hooks.teaser_secs("Đồ chua giòn rụm nhờ mẹo này nè.", 3.1) == 2.9
    assert hooks.teaser_secs("Đồ chua nè.", 3.1) == 2.0                                           # không dưới 2 giây
    assert hooks.teaser_words(3.1) == 11


def test_choose_then_apply_into_teaser_slot(on, monkeypatch):
    calls = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"hooks": [
                {"kind": "van_de", "text": "Đồ chua bị hăng?", "screen": "x"},                       # cụt: loại
                {"kind": "ket_qua", "text": "Đồ chua giòn sần sật cả tháng nhờ bước trộn muối.",
                 "screen": "đồ chua giòn cả tháng"},
                {"kind": "meo", "text": "Làm đồ chua mà hổng cần thêm nước, giòn rụm luôn nè.", "screen": "đồ chua"}]})
        return json.dumps({"scores": [{"i": 0, "score": 9}, {"i": 1, "score": 7}], "best": 1})   # «best» lệch điểm
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    got = asyncio.run(hooks.choose("ĐỒ CHUA GIÒN SẦN SẬT", "trộn muối không cần bỏ nước", "đồ chua",
                                   "Nguyên liệu gồm 1 kg cà rốt. Trộn với muối không cần bỏ nước.",
                                   "Nguyên liệu gồm 1 kg cà rốt.", 3.1))
    assert got["kind"] == "ket_qua" and got["secs"] == 3.9 and got["screen"] == "ĐỒ CHUA GIÒN CẢ THÁNG"
    # 1.5.0: xin AI ít hơn trần ASK_SLACK chữ (trần kiểm vẫn 11): AI hay viết dài hơn mức xin 2-3 chữ
    assert "Món: đồ chua" in calls[0] and "Câu ngay SAU câu hook" in calls[0] and "7-9 chữ" in calls[0]
    assert "Đồ chua bị hăng?" not in calls[1] and "tối đa 4 điểm" in calls[1]
    timed = _timed()
    assert hooks.apply(timed, got) and timed["segments"][0]["text"] == got["text"]
    assert timed["hook"] == "ĐỒ CHUA GIÒN CẢ THÁNG" and timed["script"].startswith("Đồ chua giòn")
    assert not hooks.apply(_timed(ORIG), got)                                     # đoạn 0 có lời gốc: không phải khe hook


def test_pipeline_teaser_length_follows_chosen_hook(fakes, monkeypatch, tmp_path):  # noqa: F811
    import cover as cover_mod
    import scenes
    monkeypatch.setattr(config, "CUT_PROMO", "0")
    monkeypatch.setattr(config, "TEASER", "1")
    monkeypatch.setattr(hooks, "STATE_FILE", str(tmp_path / "hooks.json"))
    monkeypatch.setattr(hooks, "wanted", lambda: True)

    async def best(*a, **k):
        return {"t": 4.5, "score": 1.0, "n": 6, "ai": True, "finished": True, "opens_with_dish": False,
                "teaser_ts": [4.5], "opens_at": -1.0}
    monkeypatch.setattr(cover_mod, "best_moment", best)

    async def near(*a):
        return []
    monkeypatch.setattr(scenes, "cuts_near", near)
    monkeypatch.setattr(cover_mod, "opening_repeat_sync", lambda *a: (0.1, 0.0))
    hook = {"kind": "ket_qua", "text": "Thịt kho mềm rục, thấm màu đẹp nhờ mẹo thắng đường này nè.",
            "screen": "THỊT KHO MỀM RỤC", "score": 9.0, "n": 5, "secs": 3.2}

    async def choose(title, tip, dish, orig_all, after, rate):
        return dict(hook)
    monkeypatch.setattr(hooks, "choose", choose)

    async def improve(*a, **k):
        raise AssertionError("đã có câu hook chọn trước: không viết lại")
    monkeypatch.setattr(hooks, "improve", improve)
    seen = {}

    async def timed(title, desc, mode, segs, out_dur, images_for=None, teaser=0.0):
        seen["teaser"] = teaser
        out = [{"i": 0, "start": 0.0, "slot": segs[0][0], "span": segs[0][0], "text": "Câu AI viết cùng kịch bản.",
                "orig": ""}]
        out += [{"i": k + 1, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": f"Lời mới đoạn {k} nè",
                 "orig": t} for k, (a, b, t) in enumerate(segs)]
        return {"segments": out, "script": " ".join(x["text"] for x in out), "fresh": 1.0,
                "title": "Thịt kho", "caption": "Ngon", "hashtags": "#a", "hook": "CHỮ CŨ"}
    monkeypatch.setattr(prompt_engine, "generate_timed_any", timed)
    src = str(tmp_path / "up" / "in.mp4")
    _video(src)
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert job.teaser["dur"] == 3.2 and seen["teaser"] == 3.2                    # đoạn mở đầu dài vừa câu hook
    assert job.final_segments[0]["text"] == hook["text"] and job.hook == "THỊT KHO MỀM RỤC"
    assert job.hook_kind == "ket_qua" and "Câu hook (kết quả, 9/10, chọn trong 5 câu)" in job.error
