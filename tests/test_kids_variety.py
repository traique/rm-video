"""Phản hồi 21/11: truyện bé «lúc nào cũng chạy và té, đã hứa thì giữ lời». Nhớ chi tiết các truyện gần đây và dặn model
không dùng lại; khung truyện không còn viết sẵn «vấp té Bịch!»."""
import json
import random

from rmvideo.kids import prompts, story

DATA = story.load()


def test_plots_no_scripted_falls():
    import re
    for p in DATA["plots"]:
        for b in p["beats"]:
            assert not re.search(r"vấp té|Bịch!|Rầm!|cả hai ngã|trượt chân", b), (p["id"], b)


def test_motifs_and_avoid_in_prompt():
    st = {"scenes": [{"text": "Thế là Gấu Nâu chạy thật nhanh, vấp té Bịch! xuống cỏ."},
                     {"text": "Gấu Nâu gặp trái banh lăn qua, quên mất lời hứa với Thỏ Trắng."}], "prop_vi": "cuốn sách hồng"}
    m = story.motifs(st)
    assert m == ["té ngã / vấp", "chạy vội", "trái banh", "quên lời hứa", "cuốn sách hồng"]
    stories = [{"created_at": 1, "motifs": ["thả diều"]}, {"created_at": 2, "lesson": "sharing"},
               {"created_at": 3, "motifs": m}]
    brief = story.pick(DATA, stories, random.Random(1))
    assert brief["avoid"] == ["thả diều"] + m
    text = prompts.user_prompt(brief, DATA)
    assert "ĐÃ CÓ những chi tiết sau: thả diều, té ngã / vấp" in text and "KHÔNG dùng lại" in text
    assert "Chạy, chạy, chạy nào" not in text.split("Truyện MẪU")[0]          # không gợi ý điệp khúc chạy
    assert "avoid" in story.pick(DATA, [], random.Random(1)) and "ĐÃ CÓ" not in prompts.user_prompt(
        story.pick(DATA, [], random.Random(1)), DATA)


def test_remember_done_records_motifs(monkeypatch, tmp_path):
    import kids_auto
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "k.json"))

    class Job:
        id = 5
    brief = {"lesson": "honesty", "setting": "park", "conflict": "lied", "words": [], "main": "white_rabbit"}
    kids_auto.remember_done(Job, brief, {"title": "T", "prop_vi": "con diều", "scenes": [{"text": "Thỏ vấp té."}]})
    st = json.load(open(tmp_path / "k.json", encoding="utf-8"))
    assert st["stories"][-1]["motifs"] == ["té ngã / vấp", "con diều"]
