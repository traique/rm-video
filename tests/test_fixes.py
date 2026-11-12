"""Giai đoạn 0 — mỗi lỗi logic đã sửa có một test chặn tái phát."""
import asyncio
import json

import pytest

import config
import prompt_engine


def _fake_gemini(monkeypatch, reply_for):
    """Thay Gemini bằng hàm giả: reply_for(prompt) -> chuỗi JSON."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        return reply_for(prompt)
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)


def test_review_shows_orig_of_merged_segment(monkeypatch):
    """Lỗi #3: đoạn bị gộp (bắt đầu cách nhau < 1.1s) phải mang ĐÚNG lời gốc đã gộp."""
    segs = [(0.0, 0.8, "đầu tiên ướp thịt"), (0.9, 2.5, "với muối"), (3.2, 6.0, "sau đó chiên vàng"),
            (6.5, 9.0, "cuối cùng bày ra đĩa")]
    texts = {0: "Thịt ướp muối kỹ nè.", 1: "Chiên vàng đều hai mặt.", 2: "Bày ra đĩa thôi bà con."}
    _fake_gemini(monkeypatch, lambda p: json.dumps({"segments": [{"i": k, "text": t} for k, t in texts.items()],
                                                    "title": "t", "caption": "c", "hashtags": "#a"}))
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 10.0))
    origs = [s["orig"] for s in out["segments"]]
    assert origs == ["đầu tiên ướp thịt với muối", "sau đó chiên vàng", "cuối cùng bày ra đĩa"]


@pytest.mark.parametrize("err,attempt,want", [
    ('429 RESOURCE_EXHAUSTED {"retryDelay": "17s"}', 0, 18.0),
    ("429 Too Many Requests. Please retry in 5.5s", 0, 6.5),
    ("429 RESOURCE_EXHAUSTED", 1, 20.0),
    ("503 UNAVAILABLE model overloaded", 0, 3.0),
    ("400 INVALID_ARGUMENT", 0, None),
    ('429 {"retryDelay": "900s"}', 0, None),          # hết hạn mức ngày: không chờ, rơi sang dự phòng
])
def test_gemini_retry_delay(err, attempt, want):
    """Lỗi N2: 429 phải được thử lại theo retryDelay (trần 60s); lỗi 400 không thử lại."""
    assert prompt_engine.retry_delay(err, attempt) == want


def test_long_video_retries_only_failed_batch(monkeypatch):
    """Lỗi N2: một lô lỗi không được làm mất cả kịch bản khớp hình của video dài."""
    calls = {"n": 0}

    async def fake_timed(title, desc, mode, part, end, images_for=None, extra=""):
        calls["n"] += 1
        if part[0][0] == 56.0 and calls.setdefault("failed", 0) == 0:     # lô thứ 2 lỗi lần đầu
            calls["failed"] = 1
            return None
        return {"segments": [{"i": k, "start": s[0], "slot": 1.0, "text": f"lời {s[0]:.0f}", "orig": s[2]}
                             for k, s in enumerate(part)], "title": "t", "caption": "c", "hashtags": "#a"}
    monkeypatch.setattr(prompt_engine, "generate_timed", fake_timed)
    segs = [(2.0 * i, 2.0 * i + 1.0, f"gốc số {i}.") for i in range(60)]   # câu trọn, nghỉ 1s giữa câu: không gộp
    out = asyncio.run(prompt_engine.generate_timed_any("", "", "cook", segs, 121.0))
    assert out is not None and len(out["segments"]) == 60
    assert calls["n"] == 4                                   # 3 lô + 1 lần gọi lại


def test_promo_cut_kept_after_review(monkeypatch):
    """Lỗi N1: bản đã duyệt phải cắt đúng mốc quảng cáo đã quyết lúc viết kịch bản."""
    import pipeline
    from queue_manager import Job
    monkeypatch.setattr(config, "CUT_PROMO", "1")
    approved = Job(url="", mode="cook", duration=0, voice="", approved=[{"i": 0, "text": "x"}], cut_at=41.5)
    assert pipeline.promo_cut(approved, [], 60.0) == 41.5
    approved.cut_at = 0.0
    assert pipeline.promo_cut(approved, [], 60.0) is None
    segs = [(0.0, 5.0, "cho thịt vào chảo"), (35.0, 40.0, "bà con bấm vào giỏ hàng góc trái màn hình"),
            (40.0, 45.0, "giá đang rất tốt nha"), (45.0, 50.0, "mua ngay kẻo hết")]
    fresh = Job(url="", mode="cook", duration=0, voice="")
    assert pipeline.promo_cut(fresh, segs, 50.0) == pytest.approx(34.95)


def test_approve_when_queue_full_keeps_script(monkeypatch):
    """Lỗi #4: duyệt lúc hàng đợi đầy vẫn render được; kịch bản rỗng thì job cũ vẫn chờ duyệt."""
    import review
    import state
    from queue_manager import Job
    q = state.queue
    while q.pending < config.MAX_QUEUE:
        q.put(Job(url="https://youtu.be/x", mode="cook", duration=0, voice=""))
    src = Job(url="https://youtu.be/y", mode="cook", duration=0, voice="", status="review", cut_at=12.0,
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "Lời một"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "span": 2.0, "text": "Lời hai"}])
    q.jobs[src.id] = src
    with pytest.raises(ValueError):
        review.approve_job(src, {0: "", 1: " "})
    assert src.status == "review"
    new = review.approve_job(src, {1: "Lời hai đã sửa"})
    assert src.status == "approved" and new.status == "queued"
    assert new.cut_at == 12.0 and new.approved[1]["text"] == "Lời hai đã sửa"


def test_model_checksum(monkeypatch, tmp_path):
    """Lỗi N3: model tải về sai checksum bị xoá và không được dùng."""
    import vocal_sep
    monkeypatch.setattr(vocal_sep, "_DIR", str(tmp_path))
    monkeypatch.setattr(vocal_sep, "_verified", set())

    def fake_dl(url, dst):
        with open(dst, "wb") as f:
            f.write(b"model bi tao")
    monkeypatch.setattr(vocal_sep.urllib.request, "urlretrieve", fake_dl)
    with pytest.raises(RuntimeError, match="checksum"):
        vocal_sep._model_path()
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(vocal_sep, "MODEL_SHA256", vocal_sep.hashlib.sha256(b"model bi tao").hexdigest())
    p = vocal_sep._model_path()
    assert open(p, "rb").read() == b"model bi tao"


class _FakeGenai:
    """Giả google.genai.Client: ghi lại config mỗi lần gọi; có thể giả lỗi theo thứ tự."""
    def __init__(self, replies):
        self.replies, self.configs = list(replies), []
        outer = self

        class _Models:
            async def generate_content(self, model, contents, config):
                outer.configs.append(config)
                r = outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return type("R", (), {"text": r})()

        class _Client:
            def __init__(self, api_key):
                self.aio = type("A", (), {"models": _Models()})()
        self.Client = _Client


def test_gemini_uses_schema(monkeypatch):
    """Structured output: Gemini nhận schema pydantic, trả JSON đúng khuôn."""
    fake = _FakeGenai(['{"segments": [{"i": 0, "text": "Ngắn gọn nè"}]}'])
    monkeypatch.setattr(prompt_engine, "genai", fake)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    out = asyncio.run(prompt_engine.shorten([(0, "Một câu dài thật là dài", 3)]))
    assert out == {0: "Ngắn gọn nè"}
    assert fake.configs[0]["response_schema"] is prompt_engine.ShortOut


def test_gemini_schema_rejected_falls_back_to_json_mode(monkeypatch):
    fake = _FakeGenai([RuntimeError("400 INVALID_ARGUMENT: response_schema not supported"),
                       '{"script": "a", "title": "t", "caption": "c", "hashtags": "#x"}'])
    monkeypatch.setattr(prompt_engine, "genai", fake)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    pack = asyncio.run(prompt_engine.generate("Tiêu đề", "", "cook", "lời gốc", 30))
    assert pack["script"] == "a" and not pack.get("fallback")
    assert "response_schema" in fake.configs[0] and "response_schema" not in fake.configs[1]


def test_gemini_transcribe_parsing():
    """gemini-3.5-transcribe: mốc dạng '1.600s'/'1s', dấu câu dính từ -> chia đoạn tại dấu chấm."""
    import transcribe
    from types import SimpleNamespace as NS
    words = [("Cho", "0.2s", "0.4s"), ("300g", "0.4s", "1s"), ("thịt.", "1s", "1.300s"), ("Đảo", "2.100s", "2.400s"),
             ("đều", "2.400s", "2.700s"), ("", "2.7s", "2.8s")]
    tr = NS(words=[NS(word=w, start_offset=a, end_offset=b) for w, a, b in words])
    resp = NS(candidates=[NS(content=NS(parts=[NS(audio_transcription=tr), NS(audio_transcription=None)]))])
    units = transcribe.gemini_units(resp)
    assert units[1] == (0.4, 1.0, "300g") and len(units) == 5
    assert [t for *_, t in transcribe.chunk_units(units)] == ["Cho 300g thịt.", "Đảo đều"]
    assert transcribe.parse_offset("2s") == 2.0 and transcribe.parse_offset(None) == 0.0


def test_asr_order_skips_missing_keys(monkeypatch):
    import transcribe
    monkeypatch.setattr(config, "ASR_ORDER", "groq,gemini,capcut")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    assert transcribe.asr_order() == ["gemini", "capcut"]
    with pytest.raises(ValueError):
        config.save_settings({"ASR_ORDER": "groq,whisper"})


def test_asr_falls_through_to_next_provider(monkeypatch):
    import transcribe
    monkeypatch.setattr(config, "ASR_ORDER", "gemini,capcut")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    seen = []

    async def fake(name, path, prompt=""):
        seen.append(name)
        if name == "gemini":
            raise RuntimeError("503 UNAVAILABLE")
        return "lời", [(0.0, 1.0, "lời")]
    monkeypatch.setattr(transcribe, "_run_provider", fake)
    assert asyncio.run(transcribe._transcribe_uncached("x.mp3")) == ("lời", [(0.0, 1.0, "lời")])
    assert seen == ["gemini", "capcut"]


def test_hashtags_always_have_hash():
    post = prompt_engine.platform_post("T", "C", "amthuc #monngon, nauan")
    assert "#amthuc #monngon #nauan" in post
