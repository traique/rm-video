"""1.5.2: Gemini 503 «high demand» — Google GIỮ yêu cầu 28-48 giây rồi mới trả lỗi (job 15 sáng 27/11): không thử lại model
đang xếp hàng, sổ model quá tải dùng chung kênh bếp / truyện bé."""
import asyncio
import json
import logging

import pytest

import config
import prompt_engine
from rmvideo.kids import pitch, writer

pytestmark = pytest.mark.skipif(prompt_engine.genai is None, reason="cần google-genai")
BUSY = "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}"


class _Resp:
    def __init__(self, text):
        self.text = text


def _clocked_genai(monkeypatch, behaviour, calls, fallback="backup-1"):
    """Giả google.genai với đồng hồ giả: behaviour(model) -> (giây Google giữ yêu cầu, kết quả hoặc lỗi); asyncio.sleep chỉ
    quay đồng hồ. Trả đồng hồ {"t"}."""
    clock = {"t": 1000.0}

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            held, out = behaviour(model)
            clock["t"] += held
            if isinstance(out, Exception):
                raise out
            return out

    class Client:
        def __init__(self, api_key):
            self.aio = type("A", (), {"models": Models()})()

    async def sleep(s):
        clock["t"] += s
    monkeypatch.setattr(prompt_engine.genai, "Client", Client)
    monkeypatch.setattr(prompt_engine.asyncio, "sleep", sleep)
    monkeypatch.setattr(prompt_engine, "_now", lambda: clock["t"])
    monkeypatch.setattr(prompt_engine, "_GONE", set())
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "GEMINI_MODEL", "main-lite")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", fallback)
    return clock


def test_slow_503_switches_model_after_one_try(monkeypatch, caplog):
    """Job 15: Google giữ 28 giây mới trả 503 -> trước đây 5 lượt (~4 phút) trên một model; giờ một lượt bị giữ quá HELD_MAX
    là sang model dự phòng ngay, model ấy vào sổ bận."""
    calls: list = []
    clock = _clocked_genai(monkeypatch, lambda m: (28.0, RuntimeError(BUSY)) if m == "main-lite" else (5.0, _Resp("ok")), calls)
    t0 = clock["t"]
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok"
    assert calls == ["main-lite", "backup-1"] and clock["t"] - t0 == 33.0
    assert prompt_engine.is_busy("main-lite") and not prompt_engine.is_busy("backup-1")
    assert any("sau 28s chờ: không thử lại model này nữa" in r.getMessage() for r in caplog.records)
    assert any("main-lite lỗi (503" in r.getMessage() and "dự phòng backup-1" in r.getMessage() for r in caplog.records)


def test_instant_503_still_gets_five_tries(monkeypatch):
    """Lỗi trả ngay (cơn quá tải ngắn, 1.4.11): vẫn 5 lượt, nghỉ 3+6+12+24 giây rồi mới sang model dự phòng."""
    calls: list = []
    clock = _clocked_genai(monkeypatch, lambda m: (0.5, RuntimeError(BUSY)) if m == "main-lite" else (0.5, _Resp("ok")), calls)
    t0 = clock["t"]
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok"
    assert calls == ["main-lite"] * 5 + ["backup-1"] and clock["t"] - t0 == pytest.approx(5 * 0.5 + 45 + 0.5)


def test_medium_hold_stops_within_budget(monkeypatch):
    """Google giữ 10 giây mỗi lượt: lượt kế dự kiến vượt RETRY_BUDGET thì thôi (3 lượt ~40 giây thay vì 5 lượt ~95 giây)."""
    calls: list = []
    clock = _clocked_genai(monkeypatch, lambda m: (10.0, RuntimeError(BUSY)) if m == "main-lite" else (1.0, _Resp("ok")), calls)
    t0 = clock["t"]
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok"
    assert calls == ["main-lite"] * 3 + ["backup-1"] and clock["t"] - t0 == 40.0


def test_quota_and_other_errors_keep_old_rules(monkeypatch):
    """429 có retryDelay ngắn vẫn chờ rồi gọi lại cùng model; lỗi 400 (không tạm) vẫn ném ngay, không vào sổ bận."""
    calls: list = []
    n = {"k": 0}

    def quota_then_ok(m):
        n["k"] += 1
        return (0.2, RuntimeError("429 RESOURCE_EXHAUSTED retryDelay: 4s")) if n["k"] == 1 else (0.2, _Resp("ok"))
    clock = _clocked_genai(monkeypatch, quota_then_ok, calls)
    t0 = clock["t"]
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok" and calls == ["main-lite"] * 2
    assert clock["t"] - t0 == pytest.approx(0.4 + 5.0) and not prompt_engine.is_busy("main-lite")
    calls.clear()
    _clocked_genai(monkeypatch, lambda m: (0.2, RuntimeError("400 INVALID_ARGUMENT")), calls)
    with pytest.raises(RuntimeError, match="400"):
        asyncio.run(prompt_engine.call_gemini("viết"))
    assert calls == ["main-lite"] and not prompt_engine.is_busy("main-lite")


def test_busy_model_skipped_until_window_passes(monkeypatch, caplog):
    """Trong BUSY_SECS, lượt gọi sau đi thẳng model dự phòng (kênh bếp ~10 lượt Gemini mỗi job, trước đây lượt nào cũng
    chờ model chính); hết hạn thì gọi lại model chính."""
    calls: list = []
    clock = _clocked_genai(monkeypatch, lambda m: (1.0, _Resp("ok")), calls)
    prompt_engine.note_busy("main-lite")
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok"
    assert calls == ["backup-1"] and "main-lite vừa quá tải / hết lượt (bỏ qua 15 phút), dùng model dự phòng backup-1" in caplog.text
    clock["t"] += prompt_engine.BUSY_SECS
    calls.clear()
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok" and calls == ["main-lite"]


def test_all_models_busy_still_tried(monkeypatch):
    calls: list = []
    _clocked_genai(monkeypatch, lambda m: (1.0, _Resp("ok")), calls)
    prompt_engine.note_busy("main-lite")
    prompt_engine.note_busy("backup-1")
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok" and calls == ["main-lite"]


def test_gone_backup_skipped_when_main_busy(monkeypatch):
    """Model chính trong sổ bận, dự phòng 1 đã bị Google gỡ (404): bỏ nó, dùng dự phòng 2; chỉ còn dự phòng đã gỡ thì đành
    gọi model chính."""
    calls: list = []

    def behaviour(m):
        if m == "old-2":
            return 0.2, RuntimeError("404 NOT_FOUND. This model models/old-2 is no longer available")
        return 0.2, _Resp("ok")
    _clocked_genai(monkeypatch, behaviour, calls, fallback="old-2,backup-1")
    prompt_engine.note_busy("main-lite")
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok" and calls == ["old-2", "backup-1"]
    assert "old-2" in prompt_engine._GONE
    calls.clear()
    monkeypatch.setattr(prompt_engine, "_GONE", set())
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", "old-2")
    assert asyncio.run(prompt_engine.call_gemini("viết")) == "ok" and calls == ["old-2", "main-lite"]


def test_explicit_model_not_skipped_but_noted(monkeypatch):
    """Model riêng (truyện bé): không có danh sách dự phòng chung, lỗi quá tải ném ra cho pitch / writer đổi model — nhưng
    vẫn ghi sổ, và sổ ấy chính là writer.is_down / mark_down (một sổ cho cả bot)."""
    calls: list = []
    _clocked_genai(monkeypatch, lambda m: (30.0, RuntimeError(BUSY)), calls)
    prompt_engine.note_busy("g4")                                   # model riêng không bị bỏ qua: người gọi tự chọn
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(prompt_engine.call_gemini("viết", model="g4"))
    assert calls == ["g4"] and writer.is_down("g4") and prompt_engine.is_busy("g4")
    writer.mark_down("lite")
    assert prompt_engine.is_busy("lite") and not writer.is_down("") and not prompt_engine.is_busy("")


def test_pitch_skips_model_in_shared_registry(monkeypatch):
    """Ý truyện: Hydrogen vừa quá tải ở lượt khác (kể cả lượt của kênh bếp ghi) -> hỏi flash-lite ngay, không chờ."""
    seen = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        seen.append(model)
        return json.dumps({"pitches": []})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    prompt_engine.note_busy("g4")
    asyncio.run(pitch._ask("p", None, ["g4", "lite", ""]))
    assert seen == ["lite"]
