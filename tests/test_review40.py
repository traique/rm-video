"""Bản 40 — từ log chạy thật 15/11/2026: phiên Kaggle xếp hàng không hiện trạng thái, phiên hỏng không được huỷ, thiếu
PUBLIC_URL không ai biết, "thơm" (tính từ) bị tính là dứa, các cụm nghe nhầm mới, AI chọn ảnh bìa ngoài danh sách."""
import asyncio
import logging
import types

import pytest

import config
import ingredients
import kaggle_api
import kaggle_worker
import vocab


def test_queued_status_reported_and_refreshed(monkeypatch):
    """API Kaggle bỏ trường status khi phiên đang xếp hàng (QUEUED là giá trị mặc định 0 của enum)."""
    monkeypatch.setattr(kaggle_api, "user", lambda: "u")
    replies = iter([{}] * 4 + [{"status": "RUNNING"}, {"status": "COMPLETE"}])
    monkeypatch.setattr(kaggle_api, "call", lambda method, body, **k: next(replies))
    now = [1000.0]
    monkeypatch.setattr(kaggle_api, "time", types.SimpleNamespace(time=lambda: now[0], sleep=lambda s: None))
    seen = []

    def every_200s(secs):
        now[0] += 200
    assert kaggle_api.wait("x", 3600, every=200, on_status=lambda s, t: seen.append((s, round(t))),
                           sleep=every_200s) == "COMPLETE"
    assert seen == [("QUEUED", 0), ("QUEUED", 400), ("RUNNING", 800), ("COMPLETE", 1000)]


def test_failed_wait_cancels_session(monkeypatch):
    cancelled = []

    def too_long(*a, **k):
        raise kaggle_api.KaggleError("Kaggle chạy quá 30 phút (QUEUED)")
    monkeypatch.setattr(kaggle_api, "wait", too_long)
    monkeypatch.setattr(kaggle_worker, "_cancel_session", lambda slug: cancelled.append(slug))
    with pytest.raises(kaggle_api.KaggleError, match="quá 30 phút"):
        kaggle_worker._wait_session(kaggle_worker.SLUG_ANALYZE, 60, lambda s, t: None)
    assert cancelled == [kaggle_worker.SLUG_ANALYZE] and kaggle_worker._ABORT["run"] is None
    monkeypatch.setattr(kaggle_api, "wait", lambda *a, **k: "COMPLETE")
    kaggle_worker._wait_session(kaggle_worker.SLUG, 60, lambda s, t: None)          # xong bình thường: không huỷ
    assert cancelled == [kaggle_worker.SLUG_ANALYZE]


def test_missing_public_url_warns_once(monkeypatch, tmp_path, caplog):
    src = tmp_path / "input.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setitem(kaggle_worker._NOTED, "public_url", False)
    with caplog.at_level(logging.WARNING):
        r1 = kaggle_worker.ref(str(src), url="https://www.facebook.com/reel/1/")
        kaggle_worker.ref(str(src), url="https://www.facebook.com/reel/1/")
    assert "http" not in r1 and r1["url"] == "https://www.facebook.com/reel/1/"
    assert sum("PUBLIC_URL" in m for m in caplog.messages) == 1
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example")
    r3 = kaggle_worker.ref(str(src), url="https://www.facebook.com/reel/1/")
    assert r3["http"].startswith("https://bot.example/kin/")
    kaggle_worker.release(r3)


def test_thom_adjective_is_not_pineapple():
    dua = next(i for i, g in enumerate(ingredients.GROUPS) if g[0] == "dứa")
    for s in ("phi cho nó thơm lên", "rất là thơm luôn", "Sau khi nó thơm mình cho hành", "xào thơm thơm",
              "phi thơm hành tỏi", "thơm nha cả nhà", "vàng rất là thơm luôn nha"):
        assert dua not in ingredients.counts(s), s
    for s in ("cá kho thơm", "200g thơm", "thêm ngò và thơm", "bò xào thơm", "100g khóm"):
        assert dua in ingredients.counts(s), s
    orig = vocab.fix_common("phi cho nó thơm lên. Sau khi nó thơm cho nửa củ hành tây. rất là thơm luôn")
    assert ingredients.check_rewrite(orig, "Phi cho thơm. Thêm nửa củ hành tây. Thơm phức luôn.") == ([], [])
    assert ingredients.counts("chúc các nhà hành công, tiến hành công việc") == {}


def test_new_mishearings_fixed_without_touching_real_words():
    got = vocab.fix_common("1 muỗng sữa mầm. 1 cây sữa cây sắc hút. 1 ít đất sừng sắc lát, 1 ít cỏ phi. "
                           "3 muỗng đường ớt 2 muỗng đường cà. Gau muống, gau gì cũng được. Lẩu đậu cá hồi, nồi lỗ, "
                           "nước lỗ. Một muỗng nấm mặn. Chấm muối điêu chanh. Thêm một ít mờ dừa điều.")
    for want in ("sả bằm", "sả cây xắt khúc", "ớt sừng xắt lát", "tỏi phi", "3 muỗng tương ớt 2 muỗng tương cà",
                 "Rau muống, rau gì", "Lẩu đầu cá hồi, nồi lẩu", "nước lẩu", "mắm mặn", "muối tiêu chanh",
                 "màu dầu điều"):
        assert want in got, (want, got)
    keep = "600g sữa đặc, sữa tươi, đường cát, đậu phộng, cá hồi, sắc bén, gàu múc nước"
    assert vocab.fix_common(keep) == keep
    dua = vocab.fix_common("Rồi mọi người cho mình là 1 muỗng sữa mầm")
    assert ingredients.check_rewrite(dua, "Cho liền 1 muỗng sả bằm") == ([], [])      # log: thiếu sữa, bịa sả


def test_cover_judge_out_of_range_is_logged(monkeypatch, caplog):
    import cover
    import prompt_engine
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images, schema=None, label="", timeout=0):
        return '{"best": 6, "reason": "x"}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(cover.ai_judge([b"a"] * 6, "t", "cook")) is None
    assert any("ngoài danh sách 0-5" in m for m in caplog.messages)
