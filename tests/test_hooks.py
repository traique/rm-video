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

    async def improve(timed, title, tip="", teaser=False):
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
