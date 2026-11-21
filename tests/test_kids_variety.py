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


def test_gripping_frames_and_rules_21_11():
    """Phản hồi 21/11 (job 7): «câu chuyện chưa gây cấn, na ná nhau, người xem lướt qua». Thêm khung truyện có hồi hộp
    (bí ẩn, giải cứu, đua giờ, hiểu lầm, khoe rồi lật ngược), mở thẳng vào chuyện, câu móc cuối cảnh, bất ngờ ở cảnh 6,
    kết hài; ảnh đổi góc máy theo cảnh."""
    ids = {p["id"] for p in DATA["plots"]}
    assert {"mystery_trail", "rescue_mission", "race_clock", "secret_surprise", "boast_flip"} <= ids
    for pid in ("mystery_trail", "rescue_mission", "race_clock", "secret_surprise", "boast_flip", "lost_quest",
                "broken_confess", "gift_simple"):
        p = story.by_id(DATA["plots"], pid)
        assert p["beats"][0].startswith("MỞ NGAY"), pid                    # mở thẳng vào chuyện, không «đang chơi vui»
    for pid in ("mystery_trail", "rescue_mission", "race_clock", "secret_surprise", "boast_flip"):
        p = story.by_id(DATA["plots"], pid)
        assert "BẤT NGỜ" in p["beats"][5] and "hỏi bé" in " ".join(p["beats"][1:5]), pid
    # khung mới chưa dùng lần nào được chọn trước (khung hợp bài học, lâu chưa dùng nhất)
    used = ["lost_quest", "gift_simple", "help_chain"]
    assert story.pick_plot(DATA, "kindness", "lost_item", used, random.Random(1)) == "mystery_trail"
    brief = story.pick(DATA, [], random.Random(3))
    u = prompts.user_prompt(brief, DATA)
    assert "câu ĐẦU TIÊN đã là điều lạ" in u and "câu móc" in u and "XOAY CHIỀU / BẤT NGỜ" in u and "chi tiết hài" in u
    assert "KHÔNG chung chung kiểu «Mất đồ rồi!»" in u and "KHÔNG đứng yên nhìn thẳng" in u
    assert not prompts.EXAMPLE[0][0].startswith("Hôm nay")                  # truyện mẫu cũng mở thẳng vào chuyện
    assert "2. Câu ĐẦU của cảnh 1" in prompts.REVISE_CHECK
    sc = {"characters": [brief["main"]], "action_en": "Little Squirrel crouches to look at tiny footprints", "text": "x"}
    shots = [prompts.image_prompt(sc, brief, DATA, i) for i in range(8)]
    assert "dynamic low angle shot" in shots[0] and "wide shot" in shots[3] and "close-up on the faces" in shots[2]
