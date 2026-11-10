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

    async def fake(prompt, images=None):
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
    ('429 {"retryDelay": "900s"}', 0, 60.0),
])
def test_gemini_retry_delay(err, attempt, want):
    """Lỗi N2: 429 phải được thử lại theo retryDelay (trần 60s); lỗi 400 không thử lại."""
    assert prompt_engine.retry_delay(err, attempt) == want


def test_long_video_retries_only_failed_batch(monkeypatch):
    """Lỗi N2: một lô lỗi không được làm mất cả kịch bản khớp hình của video dài."""
    calls = {"n": 0}

    async def fake_timed(title, desc, mode, part, end, images_for=None, extra=""):
        calls["n"] += 1
        if part[0][0] == 28.0 and calls.setdefault("failed", 0) == 0:     # lô thứ 2 lỗi lần đầu
            calls["failed"] = 1
            return None
        return {"segments": [{"i": k, "start": s[0], "slot": 1.0, "text": f"lời {s[0]:.0f}", "orig": s[2]}
                             for k, s in enumerate(part)], "title": "t", "caption": "c", "hashtags": "#a"}
    monkeypatch.setattr(prompt_engine, "generate_timed", fake_timed)
    segs = [(float(i), i + 0.8, f"gốc {i}") for i in range(60)]
    out = asyncio.run(prompt_engine.generate_timed_any("", "", "cook", segs, 61.0))
    assert out is not None and len(out["segments"]) == 60
    assert calls["n"] == 4                                   # 3 lô + 1 lần gọi lại


def test_promo_cut_kept_after_review(monkeypatch):
    """Lỗi N1: bản đã duyệt phải cắt đúng mốc quảng cáo đã quyết lúc viết kịch bản."""
    import main
    from queue_manager import Job
    monkeypatch.setattr(config, "CUT_PROMO", "1")
    approved = Job(url="", mode="cook", duration=0, voice="", approved=[{"i": 0, "text": "x"}], cut_at=41.5)
    assert main.promo_cut(approved, [], 60.0) == 41.5
    approved.cut_at = 0.0
    assert main.promo_cut(approved, [], 60.0) is None
    segs = [(0.0, 5.0, "cho thịt vào chảo"), (35.0, 40.0, "bà con bấm vào giỏ hàng góc trái màn hình"),
            (40.0, 45.0, "giá đang rất tốt nha"), (45.0, 50.0, "mua ngay kẻo hết")]
    fresh = Job(url="", mode="cook", duration=0, voice="")
    assert main.promo_cut(fresh, segs, 50.0) == pytest.approx(34.95)


def test_approve_when_queue_full_keeps_script(monkeypatch):
    """Lỗi #4: duyệt lúc hàng đợi đầy vẫn render được; kịch bản rỗng thì job cũ vẫn chờ duyệt."""
    import main
    from queue_manager import Job
    q = main.queue
    while q.pending < config.MAX_QUEUE:
        q.put(Job(url="https://youtu.be/x", mode="cook", duration=0, voice=""))
    src = Job(url="https://youtu.be/y", mode="cook", duration=0, voice="", status="review", cut_at=12.0,
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "Lời một"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "span": 2.0, "text": "Lời hai"}])
    q.jobs[src.id] = src
    with pytest.raises(ValueError):
        main.approve_job(src, {0: "", 1: " "})
    assert src.status == "review"
    new = main.approve_job(src, {1: "Lời hai đã sửa"})
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
