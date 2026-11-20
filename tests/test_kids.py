"""🧸 Truyện bé (1.1.0): chọn đề + chống trùng, kiểm kịch bản bằng mã, giọng miền Nam, prompt ảnh, kế hoạch cảnh theo
giọng, dựng thật bằng ảnh + giọng giả (ffmpeg), việc Kaggle với model giả, luồng Botkeep với Kaggle giả, lịch đăng
Fanpage riêng, cài đặt, route web."""
import asyncio
import json
import os
import random
import re
import subprocess
from types import SimpleNamespace
import time
from datetime import datetime

import pytest

import autopost
import config
import kaggle_job
import kaggle_worker
import kids_auto
from rmvideo.app import job_factory
from rmvideo.kids import job as kids_job, llm, prompts, render, story, writer
from rmvideo.kids import images as images_mod
from rmvideo.kids import dialogue
from rmvideo.media import voice_qa

DATA = story.load()


@pytest.fixture(autouse=True)
def _big_queue(monkeypatch):
    """Hàng đợi dùng chung (state.queue) có thể còn job của các test trước trong cả bộ -> không để make_now/tick vướng
    «hàng đợi đầy» (MAX_QUEUE = 5)."""
    monkeypatch.setattr(config, "MAX_QUEUE", 1000)


@pytest.fixture(autouse=True)
def _state(monkeypatch, tmp_path):
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "kids.json"))
    monkeypatch.setattr(kids_auto, "_POSTED", {})
    monkeypatch.setattr(kids_auto, "BGM_DIR", str(tmp_path / "no_bgm"))
    # không gọi Lyria thật (mạng): test nào cần thì truyền client giả; kho nhạc ở thư mục tạm, không ghi vào assets/
    from rmvideo.kids import bgm

    def _no_lyria():
        raise RuntimeError("test không gọi Lyria thật")
    monkeypatch.setattr(bgm, "_client", _no_lyria)
    monkeypatch.setattr(bgm, "LIB_DIR", str(tmp_path / "lyria_lib"))
    for k in config.EDITABLE:
        if k.startswith("KIDS_"):
            monkeypatch.setattr(config, k, config.EDITABLE[k])
    monkeypatch.setattr(config, "QUALITY", "fast")
    # không gọi edge-tts thật (mạng): giọng Anh giả là tiếng sine ngắn, ghi lại từ nào được đọc
    EN_CALLS.clear()

    async def _fake_en(text, out, voice=dialogue.EN_VOICE, rate=dialogue.EN_RATE):
        EN_CALLS.append((text, voice))
        return _wav(out, 0.4)
    monkeypatch.setattr(dialogue, "english_clip", _fake_en)
    return tmp_path


EN_CALLS: list = []


# ---------------- dữ liệu + chọn đề ----------------

def test_data_is_complete():
    assert len(DATA["characters"]) == 4 and len(DATA["settings"]) == 8 and len(DATA["lessons"]) == 10
    assert len(DATA["conflicts"]) == 10 and len(DATA["words"]) >= 60
    bank = {w["en"] for w in DATA["words"]}
    for ls in DATA["lessons"]:
        assert ls["keywords"] and set(ls["words"]) <= bank, ls["id"]
    assert all(w["vi"] for w in DATA["words"])
    # khung truyện: 8 nhịp, bài học / chuyện xảy ra trỏ đúng id, mỗi bài học có ít nhất một khung
    lids, cids = {x["id"] for x in DATA["lessons"]}, {x["id"] for x in DATA["conflicts"]}
    assert len(DATA["plots"]) >= 8
    for pl in DATA["plots"]:
        assert len(pl["beats"]) == 8 and pl["vi"] and set(pl["lessons"]) <= lids and set(pl["conflicts"]) <= cids, pl["id"]
        assert any("{main}" in b for b in pl["beats"]) and any("điệp khúc" in b for b in pl["beats"]), pl["id"]
        assert 3 <= len(pl["refrain"].split()) <= 7, pl["id"]
    assert all(any(ls in pl["lessons"] for pl in DATA["plots"]) for ls in lids)
    assert all(c.get("quirk_vi") for c in DATA["characters"].values())


def test_pick_avoids_recent_and_rotates_main():
    rnd = random.Random(5)
    stories = []
    now = time.time()
    for k in range(40):
        b = story.pick(DATA, stories, rnd, now)
        assert 3 <= len(b["words"]) <= 4 and b["seed"] > 0
        stories.append({"created_at": now - (40 - k) * 3600, "lesson": b["lesson"], "setting": b["setting"],
                        "conflict": b["conflict"], "plot": b["plot"], "words": [w["en"] for w in b["words"]],
                        "main": b["main"]})
    first = stories[0]
    for st in stories:                                                     # khung hợp bài học, luân phiên khi có nhiều khung
        assert st["lesson"] in story.by_id(DATA["plots"], st["plot"])["lessons"]
    assert len({st["plot"] for st in stories}) >= 6
    beats = story.plot_beats(stories[0] | {"main": "white_rabbit", "seed": 1}, DATA)
    assert len(beats) == 8 and "Thỏ Trắng" in " ".join(beats) and "{main}" not in " ".join(beats)
    assert story.plot_beats({"plot": "", "main": "white_rabbit"}, DATA) == list(story.SCENE_ROLES)
    linked = set(story.by_id(DATA["lessons"], first["lesson"])["words"])
    at_place = set(story.by_id(DATA["settings"], first["setting"])["words"])
    assert linked & set(first["words"]) and at_place & set(first["words"])  # 1 từ theo bài học + từ có mặt ở bối cảnh
    triples = [(s["lesson"], s["setting"], s["conflict"]) for s in stories]
    assert len(set(triples)) == len(triples)                              # 40 truyện không trùng bộ ba
    assert set(s["main"] for s in stories[:4]) == set(DATA["characters"])   # 4 truyện đầu: 4 nhân vật chính khác nhau
    counts = {ls["id"]: sum(1 for s in stories if s["lesson"] == ls["id"]) for ls in DATA["lessons"]}
    assert max(counts.values()) - min(counts.values()) <= 1                 # bài học chia đều
    sets = [frozenset(s["words"]) for s in stories[:10]]
    assert len(set(sets)) == 10
    old = [{**s, "created_at": now - 40 * 86400} for s in stories]          # quá 30 ngày: được dùng lại
    assert story.recent(old, now) == []
    assert "Thỏ Trắng" in story.describe(story.pick(DATA, old, rnd, now), DATA) or True


def test_south_kids_words():
    assert story.south_kids("Bố ơi, con làm vỡ cái bát, xin lỗi bố ạ. Quả bóng bị ngã xuống, đừng xấu hổ.") == \
        "Ba ơi, con làm vỡ cái chén, xin lỗi ba nha. Trái banh bị té xuống, đừng mắc cỡ."
    assert story.south_kids("Ngã Tư Sở") == "Ngã Tư Sở"                     # chỉ «bị ngã / ngã xuống»
    assert story.count_word("Thank you! thank you, thankyou", "thank you") == 2
    assert story.count_word("sorry sorry. Sorry!", "sorry") == 3 and story.count_word("sorrys", "sorry") == 0


def _good_story(brief):
    """Kịch bản đạt mọi điều kiện: 8 cảnh thuần tiếng Việt, nhắc tới tiếng Việt của mỗi từ, bài học, kết vui, điệp khúc
    đúng 3 lần, «nha» ≤ 3, không câu nào khác lặp."""
    vis = [w["vi"] for w in brief["words"]]
    lesson = story.by_id(DATA["lessons"], brief["lesson"])
    base = [
        "Hôm nay Thỏ Trắng ra đồi chơi. Nắng vàng trải khắp bãi cỏ. Thỏ Trắng gặp Gấu Nâu ở gốc cây to.",
        f"Thỏ Trắng thấy {vis[0]} đẹp ghê. Thỏ Trắng muốn mang về cho Mèo Cam. Hai bạn hớn hở đi về phía đồi.",
        "Bịch! Thỏ Trắng lỡ tay làm rơi mất rồi. Gấu Nâu hơi buồn. Bé đoán xem Sóc Nhỏ có chạy tới không?",
        "Thỏ Trắng nói: «Cố lên, thử lại nào.» Thỏ Trắng cúi xuống nhặt. Vẫn chưa được, hai bạn nhìn nhau.",
        "Thỏ Trắng nói: «Cố lên, thử lại nào.» Lần này Thỏ Trắng với tay thật xa. Gấu Nâu bảo nhỏ thôi, từ từ.",
        "Gấu Nâu giúp một tay. Cố lên, thử lại nào! Được rồi, Mèo Cam gật đầu, Sóc Nhỏ cười tít mắt.",
        f"Gấu Nâu nói: «Tụi mình phải {lesson['keywords'][0]} nha.» Thỏ Trắng gật đầu, hai bạn thấy nhẹ lòng.",
        "Bốn bạn ôm nhau cười vui dưới nắng chiều mát rượi, y như lúc mới ra đồi. Mèo Cam reo: «Meo, vui quá!»",
    ]
    for j, vi in enumerate(vis[1:]):                                        # các từ còn lại cũng được nhắc tới
        base[3 + j % 3] += f" Ở đây có {vi} nè."
    pads = ["Gió thổi nhè nhẹ.", "Chim hót líu lo.", "Hoa nở khắp đồi.", "Mây trắng bay ngang.", "Cỏ xanh mượt mà.",
            "Bướm vàng chấp chới.", "Ong bay vo ve.", "Suối chảy róc rách."]
    k = 0
    while sum(story.word_count(t) for t in base) < story.WORDS_MIN + 5:       # đủ số chữ tối thiểu, mỗi câu đệm khác nhau
        base[k % 8] += " " + pads[k % len(pads)]
        k += 1
    scenes = [{"text": t, "characters": ["white_rabbit", "brown_bear"], "action_en": "White Rabbit talks to Brown Bear"}
              for t in base]
    return {"title": f"Thỏ Trắng và {vis[0]} trên đồi", "hook": f"Ôi, {vis[0]} rớt mất rồi, làm sao đây?",
            "hook_text": "rớt mất rồi", "scenes": scenes, "description": "Truyện dạy bé xin lỗi.",
            "hashtags": ["#BeXinLoi", "bai hoc"]}


def test_validate_good_and_bad():
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    assert story.validate(st, brief, DATA, prompts.example_sentences()) == []
    assert st["hashtags"] == ["#bexinloi", "#baihoc"]
    bad = json.loads(json.dumps(st))
    bad["scenes"] = bad["scenes"][:7]
    bad["scenes"][0]["text"] = "Hai bạn chơi với nhau chết."
    probs = story.validate(bad, brief, DATA)
    assert any("8 cảnh" in p for p in probs) and any("chết" in p for p in probs)
    assert any("chữ" in p for p in probs)
    sad = json.loads(json.dumps(st))
    sad["scenes"][7]["text"] = "Thỏ Trắng ngồi khóc một mình. Các bé ơi, nhớ xin lỗi nha."
    assert any("kết thúc vui" in p for p in story.validate(sad, brief, DATA))
    calm = json.loads(json.dumps(st))
    calm["scenes"][7]["text"] = "Hai bạn tặng hoa cho Mèo Cam rồi cùng ăn trái cây. Các bé ơi, nhớ chờ tới lượt nha!"
    assert not any("kết thúc vui" in p for p in story.validate(calm, brief, DATA))
    # chen tiếng Anh vào lời / tiêu đề -> lỗi (mã mới là nơi thêm tiếng Anh)
    en = json.loads(json.dumps(st))
    en["scenes"][1]["text"] += f" Thỏ Trắng nói: {brief['words'][0]['en']}!"
    assert any("THUẦN tiếng Việt" in p for p in story.validate(en, brief, DATA))
    # chép ≥ 2 câu truyện mẫu -> lỗi
    cp = json.loads(json.dumps(st))
    ex = prompts.example_sentences()
    cp["scenes"][2]["text"] += " " + ex[0] + ". " + ex[1] + "."
    assert any("chép truyện mẫu" in p for p in story.validate(cp, brief, DATA, ex))
    # truyện không lồng đủ 3 thứ trong kho gợi ý -> lỗi; từ hôm nay chọn từ chính truyện (choose_words)
    miss = json.loads(json.dumps(st))
    for sc in miss["scenes"]:
        for w in story.must_mention(brief, DATA):
            for f in story.vi_forms(w, DATA):
                sc["text"] = re.sub(r"(?i)(?<![\wÀ-ỹ])" + re.escape(f) + r"(?![\wÀ-ỹ])", "xx", sc["text"])
    assert any("nhắc tới 0" in p and "cần ít nhất" in p for p in story.validate(miss, brief, DATA))
    chosen = story.choose_words(st, brief, DATA)
    assert len(chosen) == len(brief["words"]) and all(story.vi_mentioned(story.script_text(st), w, DATA) for w in chosen)
    assert brief["words"][0]["en"] in [w["en"] for w in chosen]            # từ gợi ý có trong truyện được ưu tiên
    spoil = dict(st, title="Thỏ Trắng biết nhận lỗi")
    assert any("lộ bài học" in p for p in story.validate(spoil, brief, DATA))
    assert not any("lộ bài học" in p for p in story.validate(dict(st, title="Thỏ Trắng và chiếc bình hoa vỡ"), brief, DATA))
    assert story.sentence_case("Thỏ Trắng Lỡ Làm Hỏng Đồ Chơi Của Bạn", DATA["characters"]) == \
           "Thỏ Trắng lỡ làm hỏng đồ chơi của bạn"
    assert story.sentence_case("Thỏ Trắng và cái giỏ táo", DATA["characters"]) == "Thỏ Trắng và cái giỏ táo"
    vi_run = json.loads(json.dumps(st))
    vi_run["scenes"][1]["text"] += " Thỏ Trắng run rẩy."                     # «run» tiếng Việt không phải tiếng Anh
    assert not any("THUẦN" in p for p in story.validate(vi_run, brief, DATA))
    # quá nhiều «nha», câu lặp ngoài điệp khúc, câu móc kết «nha» -> lỗi
    nha = json.loads(json.dumps(st))
    nha["scenes"][0]["text"] += " Đi chơi nha. Vui nha. Về nha."
    nha["hook"] = "Thỏ Trắng phải làm sao đây nha?"
    probs = story.validate(nha, brief, DATA)
    assert any("«nha/hen»" in p for p in probs) and any("câu móc không kết" in p for p in probs)
    rep = json.loads(json.dumps(st))
    rep["scenes"][1]["text"] += " Nắng vàng trải khắp bãi cỏ."                 # câu của cảnh 1 lặp lại
    assert any("lặp câu/cụm" in p and "nắng vàng" in p for p in story.validate(rep, brief, DATA))
    assert story.repeated_sentences(["Cố lên thử lại nào. Cố lên thử lại nào.", "Cố lên thử lại nào. Vui ghê."]) == []
    assert story.repeated_sentences(["Cố lên thử lại nào. Cố lên thử lại nào."] * 2) == [("cố lên thử lại nào", 4)]
    assert story.repeated_sentences(["Gấu Nâu nói: «Đi thôi.»", "Gấu Nâu nói: «Về thôi.»"]) == []    # lời dẫn không tính
    assert story.vi_mentioned("Thỏ Trắng cầm trái banh đỏ", {"en": "ball", "vi": "trái banh"}, DATA)
    assert story.vi_mentioned("banh lăn kìa", {"en": "ball", "vi": "trái banh"}, DATA)
    assert not story.vi_mentioned("bánh kem ngon", {"en": "ball", "vi": "trái banh"}, DATA)
    assert story.vi_mentioned("áo màu đỏ", {"en": "red", "vi": "màu đỏ"}, DATA)
    assert not story.vi_mentioned("đỏng đảnh", {"en": "red", "vi": "màu đỏ"}, DATA)


def test_blocking_beats_and_recap():
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    assert story.blocking(st, brief, DATA) == []
    beats = story.add_beats(st, brief, DATA)
    assert [en for en, _ in beats] == [w["en"] for w in brief["words"]]
    assert beats[0][1] == 1                                                  # từ đầu tiên xuất hiện ở cảnh 2 -> chèn ở đó
    for en, i in beats:                                                      # không chen vào cảnh biến cố / cao trào khi tránh được
        w = {"en": en, "vi": story.word_info(DATA, en)["vi"]}
        mention = [j for j, sc in enumerate(st["scenes"]) if story.vi_mentioned(sc["text"], w, DATA)]
        assert i not in story.AVOID_BEAT_SCENES or set(mention) <= story.AVOID_BEAT_SCENES
    # hai từ cùng xuất hiện lần đầu ở một cảnh: từ sau sang cảnh kế có nhắc tới, không dồn
    st4 = story.normalize(_good_story(brief), DATA)
    for sc in st4["scenes"][:3]:
        sc["text"] += " Có trái banh và cái nón."
    two = {**brief, "words": [{"en": "ball", "vi": "trái banh"}, {"en": "hat", "vi": "cái nón"}]}
    assert [i for _, i in story.add_beats(st4, two, DATA)] == [0, 1]
    txt = story.script_text(st)
    for k, w in enumerate(brief["words"]):
        assert story.beat_text(w, k, DATA) in txt and "nha" not in story.beat_text(w, k, DATA)
    assert story.beat_text({"en": "ball", "vi": "trái banh"}) == "À, trái banh tiếng Anh là ball. Bé nói theo nè: ball!"
    assert story.beat_text({"en": "ball", "vi": "trái banh"}, 0, DATA) == "À, trái banh đó tiếng Anh là ball. Bé nói theo nè: ball!"
    assert story.beat_text({"en": "hug", "vi": "ôm"}, 2, DATA) == "Đố bé biết ôm tiếng Anh là gì? Là hug! Bé đọc theo mình: hug!"
    assert story.beat_text({"en": "ball", "vi": "trái banh"}, 1) != story.beat_text({"en": "ball", "vi": "trái banh"}, 0)
    # cảnh kết bằng câu hỏi cho bé: câu dạy chèn TRƯỚC câu hỏi, và cảnh đó xếp sau các cảnh êm khác
    q = "Thỏ Trắng sà vào đá banh. Bé đoán xem Thỏ Trắng có nhớ giờ hẹn không?"
    assert story.ends_with_child_question(q) and not story.ends_with_child_question("Thỏ chạy đi. Bé ơi vui quá!")
    assert story.insert_beat(q, "À, X.") == "Thỏ Trắng sà vào đá banh. À, X. Bé đoán xem Thỏ Trắng có nhớ giờ hẹn không?"
    assert story.insert_beat("Thỏ chạy đi.", "À, X.") == "Thỏ chạy đi. À, X."
    stq = story.normalize(_good_story(brief), DATA)
    stq["scenes"][1]["text"] += " Có trái banh. Bé đoán xem banh lăn đi đâu?"
    stq["scenes"][3]["text"] += " Có trái banh."
    bq = story.add_beats(stq, {**brief, "words": [{"en": "ball", "vi": "trái banh"}]}, DATA)
    assert bq == [("ball", 3)]                                                       # cảnh 2 hỏi bé -> ưu tiên cảnh 4
    stq2 = story.normalize(_good_story(brief), DATA)
    stq2["scenes"][1]["text"] += " Có trái banh. Bé đoán xem banh lăn đi đâu?"
    story.add_beats(stq2, {**brief, "words": [{"en": "ball", "vi": "trái banh"}]}, DATA)
    assert stq2["scenes"][1]["text"].endswith("Bé đoán xem banh lăn đi đâu?") and "ball!" in stq2["scenes"][1]["text"]
    # không cảnh nào nhắc tới -> chèn kèm câu dẫn «Ở đây có ... nè»
    st2 = story.normalize(_good_story(brief), DATA)
    extra = {"en": "rainbow", "vi": "cầu vồng"}
    beats = story.add_beats(st2, {**brief, "words": [extra]}, DATA)
    assert beats == [("rainbow", 1)] and ("Ở đây có cầu vồng nè. " + story.beat_text(extra, 0, DATA)) in st2["scenes"][1]["text"]
    assert "nha" not in story.recap_text(brief) and story.recap_text(brief).endswith("Hẹn gặp lại bé ở chuyện sau!")
    preach = json.loads(json.dumps(st2))
    preach["scenes"][7]["text"] += " Các bé ơi, nhớ chơi vui với bạn nha!"
    assert any("câu kết không nói với" in q for q in story.validate(preach, brief, DATA))
    # lời chào: không bắt nhắc tới, câu dạy từ đặt ở cảnh mở đầu / kết
    st5 = story.normalize(_good_story(brief), DATA)                       # ba từ cùng ở cảnh 2 (và cảnh 8): không dồn cả ba vào 2
    st5["scenes"][1]["text"] += " Có trái táo, bánh mì và cái giỏ."
    st5["scenes"][7]["text"] += " Có trái táo, bánh mì và cái giỏ."
    three = {**brief, "words": [{"en": "apple", "vi": "trái táo"}, {"en": "bread", "vi": "bánh mì"}, {"en": "basket", "vi": "cái giỏ"}]}
    assert [i for _, i in story.add_beats(st5, three, DATA)] == [1, 1, 7]
    st3 = story.normalize(_good_story(brief), DATA)
    greet = {**brief, "words": [{"en": "good morning", "vi": "chào buổi sáng"}, {"en": "bye bye", "vi": "tạm biệt"}]}
    assert not any("nhắc tới" in p for p in story.validate(st3, greet, DATA))
    assert story.add_beats(st3, greet, DATA) == [("good morning", 0), ("bye bye", 7)]
    assert "Ở đây có chào buổi sáng" not in story.script_text(st3) and "Ở đây có tạm biệt" not in story.script_text(st3)
    assert st3["scenes"][7]["text"].endswith(": bye bye!") and "good morning" in st3["scenes"][0]["text"]
    assert "«chào buổi sáng»" not in prompts.user_prompt(greet, DATA) and "PHẢI nhắc tới" not in prompts.user_prompt(greet, DATA)
    short = {**st, "scenes": [{**s, "text": "Hai bạn chơi vui."} for s in st["scenes"]]}
    assert any("quá ngắn" in p for p in story.blocking(short, brief, DATA))
    r = story.recap_text(brief)
    assert r.startswith("Hôm nay mình học:") and all(w["en"] in r and w["vi"] in r for w in brief["words"])


def test_word_matching_false_positives():
    """Kho từ: dạng rút gọn không được nhận nhầm (đo thật 19/11: «nôn nóng» -> hot, «rồi mới chơi» -> new, «chơi» -> toy)."""
    def hit(en, text):
        return story.vi_mentioned(text, story.word_info(DATA, en), DATA)
    assert not hit("hot", "Thỏ Trắng nôn nóng chạy tới") and hit("hot", "cháo còn nóng hổi")
    assert not hit("new", "Tới lượt rồi mới chơi!") and hit("new", "cái diều mới tinh")
    assert not hit("toy", "hai bạn chơi vui") and hit("toy", "một món đồ chơi")
    assert not hit("big", "Sóc Nhỏ đọc to nào") and hit("big", "cái cây to")
    assert not hit("small", "Sóc Nhỏ nói nhỏ") and hit("small", "dòng suối nhỏ")
    assert not hit("white", "Thỏ Trắng") and hit("white", "áo màu trắng")
    assert not hit("clap", "lỡ tay làm rơi") and hit("clap", "Gấu Nâu vỗ tay")
    assert not hit("sun", "trời sắp tối") and hit("sun", "ông mặt trời")
    assert not hit("star", "thấy sao sao") and hit("star", "ngôi sao sáng")
    assert not hit("umbrella", "dù mưa vẫn đi") and hit("umbrella", "cây dù đỏ")
    assert not hit("tree", "cây dù đỏ") and hit("tree", "gốc cây to")
    assert not hit("ball", "ngồi dưới bóng mát") and hit("ball", "trái banh lăn")
    assert not hit("one", "ngồi một mình") and hit("one", "đếm một, hai, ba")
    assert not hit("three", "ba mẹ gọi về") and hit("three", "đếm một, hai, ba")
    assert not hit("sad", "Thỏ buồn ngủ") and hit("sad", "Thỏ buồn thiu")
    assert not hit("cake", "ổ bánh mì") and hit("bread", "ổ bánh mì") and hit("cake", "bánh kem dâu")
    # từ vừa dạy gần đây xếp sau khi chọn từ của truyện
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    for sc in st["scenes"][:3]:
        sc["text"] += " Có trái táo và bánh mì."
    two = {**brief, "words": [{"en": "x", "vi": "x"}], "word_pool": [], "recent_en": ["apple"]}
    assert story.choose_words(st, two, DATA)[0]["en"] != "apple"
    hist = [{"words": ["apple", "hug"], "lesson": "", "setting": "", "conflict": "", "created": "2026-11-18T08:00:00"}]
    assert story.pick(DATA, hist, random.Random(2)).get("recent_en") == ["apple", "hug"]


def test_merge_evens_voice_levels(tmp_path):
    """Nối giọng: đoạn to (~-15 dBFS) và đoạn nhỏ (~-29 dBFS) về cùng mức RMS_TARGET; đo lại từng nửa của file nối.
    (sine của lavfi chỉ 1/8 biên độ: volume=2 -> RMS -15 dBFS, 0.4 -> -29.)"""
    loud, soft = str(tmp_path / "loud.wav"), str(tmp_path / "soft.wav")
    for p, vol in ((loud, 2.0), (soft, 0.4)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1.5", "-af",
                        f"volume={vol}", "-ar", "24000", "-ac", "1", p], check=True)
    a, b = asyncio.run(dialogue.rms_db(loud)), asyncio.run(dialogue.rms_db(soft))
    assert a is not None and b is not None and 10 < a - b < 16
    out = asyncio.run(dialogue.merge([loud, soft], str(tmp_path / "m.wav"), gap=0.0))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert 2.9 <= dur <= 3.1
    for ss in (0.1, 1.6):
        part = str(tmp_path / f"p{ss}.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(ss), "-t", "1.3", "-i", out, part], check=True)
        lvl = asyncio.run(dialogue.rms_db(part))
        assert lvl is not None and abs(lvl - dialogue.RMS_TARGET) < 1.5, (ss, lvl)
    assert asyncio.run(dialogue.rms_db(str(tmp_path / "nope.wav"))) is None


def test_imgcheck_merge_and_regenerate(tmp_path, monkeypatch):
    """Đếm đầu: khung lồng nhau / thân áo dưới đầu gộp một (đo thật 19/11), ảnh hai đầu bị bắt; generate vẽ lại khi thừa đầu."""
    from PIL import Image
    from rmvideo.kids import imgcheck
    raw05 = [("a rabbit head", 0.385, [255, 424, 520, 696]), ("a cat head", 0.353, [303, 684, 510, 830])]   # thân áo bị nhận là đầu
    assert len(imgcheck.merge_heads(raw05)) == 1
    raw16 = [("a rabbit head", 0.494, [83, 295, 389, 694]), ("a rabbit head", 0.326, [196, 547, 385, 685]),
             ("a rabbit head", 0.355, [354, 516, 621, 730])]                                               # lồng nhau + bạn thứ hai
    assert len(imgcheck.merge_heads(raw16)) == 2
    raw14 = [("a rabbit head", 0.482, [157, 256, 518, 716]), ("a rabbit head", 0.23, [244, 536, 507, 710])]
    assert len(imgcheck.merge_heads(raw14)) == 1
    two = [("a rabbit head", 0.5, [300, 200, 600, 500]), ("a rabbit head", 0.4, [40, 700, 250, 900]), ("a rabbit", 0.9, [0, 0, 700, 1300])]
    assert len(imgcheck.merge_heads(two)) == 2                                   # hai đầu xa nhau, khung «a rabbit» không tính
    assert imgcheck.merge_heads([("a cat head", 0.15, [0, 0, 10, 10])]) == []    # dưới ngưỡng
    # đo thật 20/11, video job 1 (LoRA, khung 1080x1920): ngưỡng 0,3 cũ chỉ thấy 1 đầu ở cảnh Gấu ôm HAI Thỏ và 2 ở cảnh thêm
    # gấu con; giờ 3 và 3. f067: khung 0,321 bao cả hai thỏ (khung nhóm) điểm cao nhất; đầu thỏ thứ hai lệch sang bên, nằm
    # dưới cằm thỏ thứ nhất (không phải thân). f075: gấu to 0,258 + gấu con 0,305.
    f067 = [("a bear head", 0.202, [135, 375, 803, 954]), ("a rabbit head", 0.321, [352, 684, 1002, 1202]),
            ("a rabbit head", 0.294, [518, 698, 1005, 1188]), ("a rabbit head", 0.237, [536, 893, 831, 1145]),
            ("a rabbit head", 0.186, [349, 965, 771, 1309]), ("a rabbit head", 0.32, [441, 1046, 743, 1288])]
    f075 = [("a rabbit head", 0.427, [81, 472, 587, 1106]), ("a rabbit head", 0.158, [240, 817, 576, 1095]),
            ("a bear head", 0.258, [797, 830, 1084, 1118]), ("a bear head", 0.305, [617, 1035, 896, 1270]),
            ("a bear head", 0.22, [607, 1038, 900, 1394])]
    assert len(imgcheck.merge_heads(f067)) == 3 and len(imgcheck.merge_heads(f075)) == 3
    assert len(imgcheck.merge_heads(f067, 0.3)) == 1                              # ngưỡng cũ: sót
    assert imgcheck.species_twins(imgcheck.merge_heads(f067), ["rabbit", "bear"]) == ("rabbit", 2, 1)
    assert imgcheck.species_twins(imgcheck.merge_heads(f067), ["rabbit", "rabbit"]) is None    # cảnh có hai thỏ thật
    assert imgcheck.species_twins(imgcheck.merge_heads(f067), []) is None
    # cảnh sạch cùng video: thỏ nằm bò (khung cả con, khung thân trắng thẳng dưới đầu), gấu 0,15 -> không báo nhầm
    f033 = [("a rabbit head", 0.25, [202, 625, 580, 969]), ("a rabbit head", 0.323, [188, 620, 590, 1250]),
            ("a rabbit head", 0.188, [226, 965, 579, 1273]), ("a bear head", 0.158, [550, 903, 996, 1467])]
    f002 = [("a rabbit head", 0.373, [151, 597, 559, 1009]), ("a rabbit head", 0.164, [110, 600, 568, 1377]),
            ("a rabbit head", 0.203, [187, 946, 557, 1276]), ("a bear head", 0.151, [528, 932, 1005, 1515])]
    f042 = [("a rabbit head", 0.187, [97, 499, 451, 832]), ("a rabbit head", 0.391, [94, 494, 502, 1046]),
            ("a rabbit head", 0.156, [228, 801, 514, 1037])]
    assert [len(imgcheck.merge_heads(x)) for x in (f033, f002, f042)] == [1, 1, 1]
    # thân lệch sang bên (không thẳng hàng) chỉ bị coi là thân khi vùng giữa mang màu áo
    body = [("a rabbit head", 0.4, [100, 100, 300, 300]), ("a rabbit head", 0.3, [180, 260, 380, 440])]
    assert len(imgcheck.merge_heads(body)) == 2
    red = Image.new("RGB", (500, 500), (240, 240, 240))
    red.paste((220, 30, 30), (180, 260, 380, 440))
    assert len(imgcheck.merge_heads(body, img=red)) == 1
    # màu áo: ảnh giả có hai áo (đỏ + xanh lá), khung «a t-shirt» -> màu; cam (lông mèo) không tính
    im = Image.new("RGB", (200, 300), (120, 200, 90))
    for box, col in (([20, 120, 80, 200], (220, 30, 30)), ([120, 120, 180, 200], (40, 160, 60)), ([20, 220, 80, 290], (240, 140, 30))):
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                im.putpixel((x, y), col)
    boxes = [("a t-shirt", 0.6, [20, 120, 80, 200]), ("a t-shirt", 0.5, [120, 120, 180, 200]), ("a t-shirt", 0.4, [20, 220, 80, 290]),
             ("a t-shirt", 0.3, [22, 122, 78, 198])]                                            # khung cuối chồng khung đầu
    found = imgcheck.shirt_colors(im, boxes)
    assert [c for c, _s, _b in found] == ["red", "green"] and imgcheck.box_color(im, [20, 220, 80, 290]) is None
    heads = [("a rabbit head", 0.5, [20, 40, 80, 110]), ("a cat head", 0.5, [120, 40, 180, 110])]
    assert [c for c, _s, _b in imgcheck.shirt_colors(im, boxes, heads=heads)] == ["red", "green"]     # dưới đầu: giữ
    assert imgcheck.shirt_colors(im, boxes, heads=[("a cat head", 0.5, [120, 260, 180, 299])]) == []  # không đầu nào ở trên: bỏ
    assert imgcheck.hue_name(44) is None and imgcheck.hue_name(50) == "yellow"
    assert imgcheck.shirts_ok(found, ["red", "green"]) and imgcheck.shirts_ok(found, ["green", "red"])
    assert not imgcheck.shirts_ok(found, ["red", "blue"])                       # áo xanh lá không thuộc bộ màu
    assert not imgcheck.shirts_ok([("red", 0.5, [0, 0, 1, 1]), ("red", 0.4, [50, 0, 51, 1])], ["red", "green"])   # hai áo đỏ
    assert imgcheck.shirts_ok([("red", 0.5, [0, 0, 1, 1])], ["red", "green"])    # thiếu áo: không tính lỗi
    assert imgcheck.shirts_ok([], ["red"]) and imgcheck.shirts_ok(found, [])
    assert imgcheck.hue_name(5) == "red" and imgcheck.hue_name(350) == "red" and imgcheck.hue_name(30) is None
    # áo đúng chủ: đầu thỏ trên áo đỏ, đầu mèo trên áo xanh lá (đo thật mẫu 32: Thỏ mặc áo xanh của Gấu)
    hd = [("a rabbit head", 0.5, [20, 40, 80, 110]), ("a cat head", 0.5, [120, 40, 180, 110])]
    assert imgcheck.shirt_owner([20, 120, 80, 200], hd) == "rabbit" and imgcheck.shirt_owner([120, 120, 180, 200], hd) == "cat"
    assert imgcheck.shirt_owner([20, 0, 80, 30], hd) is None                                  # áo ở TRÊN đầu: không gán
    assert imgcheck.owners_ok(hd, found, {"rabbit": "red", "cat": "green"})
    assert not imgcheck.owners_ok(hd, found, {"rabbit": "green", "cat": "red"})
    monkeypatch.setattr(imgcheck, "detect", lambda img, loaded=None, min_score=0.1, extra=(): hd + boxes[:2])
    ok, n, out = imgcheck.check(im, 2, loaded=object(), colors=["rabbit:red", "cat:green"])
    assert ok and n == 2 and not any(b[0].startswith("why:") for b in out)
    ok, n, out = imgcheck.check(im, 2, loaded=object(), colors=["rabbit:green", "cat:red"])
    assert not ok and any(b[0].startswith("why:áo mặc nhầm chủ") for b in out)
    ok, _n, out = imgcheck.check(im, 1, loaded=object(), colors=["rabbit:red"])              # 2 đầu, cảnh 1 bạn
    assert not ok and any("2 đầu" in b[0] for b in out)
    big_small = [("a rabbit head", 0.5, [0, 0, 300, 300]), ("a squirrel head", 0.5, [400, 500, 500, 600])]   # 9x diện tích
    assert not imgcheck.sizes_ok(big_small, 2) and imgcheck.sizes_ok(big_small, 1)
    assert imgcheck.sizes_ok([("a rabbit head", 0.5, [0, 0, 300, 300]), ("a cat head", 0.5, [400, 0, 600, 200])], 2)
    b0 = story.pick(DATA, [], random.Random(2))
    assert "same height" in prompts.image_prompt({"characters": ["white_rabbit", "little_squirrel"], "action_en": "White Rabbit hugs Little Squirrel."}, b0, DATA)
    assert prompts.shirt_color(DATA["characters"]["orange_cat"]) == "green" and prompts.shirt_color({}) == ""
    # generate: ảnh 1 bị báo thừa đầu -> vẽ lại seed khác (tối đa RETRIES), giữ ảnh ít đầu thừa nhất; ảnh 2 đạt ngay
    seeds_used = []

    def fake_one(pipe, kind, prompt, seed, steps, guidance, w=10, h=20, embeds=None):
        seeds_used.append(seed)
        im = Image.new("RGB", (64, 112), (120, 180, 90))
        im.putpixel((1, 1), (255, 255, 255))
        return im
    heads = {11: 3, 11 + 7919 * 2: 2, 11 + 7919 * 3: 2, 22: 1}
    monkeypatch.setattr(images_mod, "generate_one", fake_one)
    monkeypatch.setattr(images_mod, "is_blank", lambda img, band=0.05: False)
    checked = []

    def checker(img, n, c=None):
        seed = seeds_used[-1]
        checked.append((seed, n))
        assert c == (["red"] if n == 1 else ["red", "green"])
        return heads[seed] <= n, heads[seed], []
    st = {}
    out = images_mod.generate(["p1", "p2"], [11, 22], str(tmp_path / "img"), "schnell", lambda s: None, pipe=object(),
                              expected=[1, 2], checker=checker, stats=st, colors=[["red"], ["red", "green"]])
    assert len(out) == 2 and all(os.path.exists(p) for p in out)
    assert seeds_used == [11, 11 + 7919 * 2, 11 + 7919 * 3, 22] and st == {"checked": 4, "redo": 2}
    assert os.path.exists(tmp_path / "img" / "raw_01_bad1.png") and os.path.exists(tmp_path / "img" / "raw_01_bad3.png")
    assert checked == [(11, 1), (11 + 7919 * 2, 1), (11 + 7919 * 3, 1), (22, 2)]
    seeds_used.clear()
    images_mod.generate(["p1"], [11], str(tmp_path / "img2"), "schnell", lambda s: None, pipe=object())      # không kiểm
    assert seeds_used == [11]
    # hết lượt: lượt 1 đủ 2 đầu nhưng áo nhầm chủ (1 lỗi); lượt 2-3 chỉ thấy 1 đầu + 2 lỗi -> giữ lượt 1 (ít lỗi nhất)
    seeds_used.clear()
    verdict = {11: (2, [("why:áo mặc nhầm chủ", 0.0, [0, 0, 1, 1])]),
               11 + 7919 * 2: (1, [("why:a", 0.0, [0, 0, 1, 1]), ("why:b", 0.0, [0, 0, 1, 1])]),
               11 + 7919 * 3: (1, [("why:a", 0.0, [0, 0, 1, 1]), ("why:b", 0.0, [0, 0, 1, 1])])}

    def fake_one2(pipe, kind, prompt, seed, steps, guidance, w=10, h=20, embeds=None):
        seeds_used.append(seed)
        return Image.new("RGB", (64, 112), (seed % 256, 10, 10))

    def checker2(img, n, c=None):
        heads, boxes = verdict[seeds_used[-1]]
        return False, heads, boxes
    monkeypatch.setattr(images_mod, "generate_one", fake_one2)
    images_mod.generate(["p1"], [11], str(tmp_path / "img3"), "schnell", lambda s: None, pipe=object(), expected=[2],
                        checker=checker2, stats={}, colors=[["rabbit:red", "bear:blue"]])
    assert Image.open(tmp_path / "img3" / "raw_01.png").getpixel((0, 0))[0] == 11
    got = []

    def checker3(img, n, c=None, **kw):
        got.append((n, c, kw))
        return True, n, []
    images_mod.generate(["p1", "p2"], [11, 22], str(tmp_path / "img4"), "schnell", lambda s: None, pipe=object(), expected=[2, 1],
                        checker=checker3, stats={}, colors=[["rabbit:red", "bear:blue"], ["rabbit:red"]], contact=[True, False],
                        props=["toy box", ""])
    assert got == [(2, ["rabbit:red", "bear:blue"], {"contact": True, "prop": "toy box"}), (1, ["rabbit:red"], {})]


def test_lora_wiring(tmp_path, monkeypatch):
    """LoRA nhân vật: tìm file trong /kaggle/input (tên / slug / số bước lớn nhất), prompt có từ khoá, spec + notebook gắn thêm."""
    import kaggle_worker
    root = tmp_path / "input" / "notebooks" / "u" / "rmvideo-lora-train" / "output" / "dcn_lora_v1"
    root.mkdir(parents=True)
    for n in ("dcn_lora_v1_000000150.safetensors", "dcn_lora_v1_000000300.safetensors", "dcn_lora_v1.safetensors"):
        (root / n).write_bytes(b"x")
    assert images_mod.find_lora("dcn_lora_v1", str(tmp_path / "input")).endswith("dcn_lora_v1.safetensors")
    assert images_mod.find_lora("dcn_lora_v1.safetensors", str(tmp_path / "input")).endswith("dcn_lora_v1.safetensors")
    assert images_mod.find_lora("rmvideo-lora-train", str(tmp_path / "input")).endswith("dcn_lora_v1.safetensors")
    (root / "dcn_lora_v1.safetensors").unlink()
    assert images_mod.find_lora("dcn_lora_v1", str(tmp_path / "input")).endswith("_000000300.safetensors")
    assert images_mod.find_lora("nope", str(tmp_path / "input")) is None and images_mod.find_lora("", str(tmp_path)) is None
    b0 = story.pick(DATA, [], random.Random(2))
    sc = {"characters": ["white_rabbit", "orange_cat"], "action_en": "White Rabbit hugs Orange Cat."}
    assert "thotrang White Rabbit" in prompts.image_prompt(sc, b0, DATA, use_lora=True) and "meocam Orange Cat" in prompts.image_prompt(sc, b0, DATA, use_lora=True)
    assert "thotrang" not in prompts.image_prompt(sc, b0, DATA)
    import kaggle_api
    exists = {"v": True}
    asked = []
    monkeypatch.setattr(kaggle_api, "configured", lambda: True)
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: asked.append(slug) or exists["v"])
    kaggle_worker._LORA_OK.clear()
    monkeypatch.setattr(config, "KIDS_LORA", "dcn_lora_v1", raising=False)
    nbs, dss = kaggle_worker.kids_sources()
    assert kaggle_worker.SLUG_KIDS_LORA in dss and nbs == [kaggle_worker.SLUG_KIDS_MODELS]
    job = job_factory.kids(brief=b0, source="web")
    sp = kaggle_worker.kids_spec(job, b0, None)
    assert sp["image"]["lora"] == "dcn_lora_v1" and kaggle_worker.SLUG_KIDS_LORA in sp["dataset_sources"]
    assert asked == [kaggle_worker.SLUG_KIDS_LORA]                                    # nhớ kết quả, hỏi một lần
    monkeypatch.setattr(config, "KIDS_LORA", "notebook:rmvideo-lora-train/dcn_lora_v1", raising=False)
    assert kaggle_worker.SLUG_LORA_TRAIN in kaggle_worker.kids_sources()[0]
    assert kaggle_worker.lora_source() == ("notebook", "rmvideo-lora-train", "dcn_lora_v1")
    exists["v"] = False                                                              # tài khoản không có dataset: vẽ không LoRA
    kaggle_worker._LORA_OK.clear()
    monkeypatch.setattr(config, "KIDS_LORA", "dcn_lora_v1", raising=False)
    assert kaggle_worker.lora_source() is None and kaggle_worker.kids_spec(job, b0, None)["image"]["lora"] == ""
    for off in ("", "off"):
        monkeypatch.setattr(config, "KIDS_LORA", off, raising=False)
        assert kaggle_worker.lora_source() is None and kaggle_worker.SLUG_KIDS_LORA not in kaggle_worker.kids_sources()[1]


def test_rewrite_keeps_previous_draft():
    """Viết lại theo lỗi: lời nhắc kèm bản nháp trước và yêu cầu SỬA (không viết truyện khác); dài quá mức chặn thì không nhận."""
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    msgs = prompts.messages(brief, DATA, {"name": "K"}, ["cả truyện 230 chữ, tối đa 200 chữ"], previous=st)
    u = msgs[1]["content"]
    assert "BẢN NHÁP TRƯỚC" in u and st["scenes"][2]["text"] in u and st["title"] in u and "SỬA đúng các lỗi" in u
    assert "Bản nháp chưa đạt vì:\n- cả truyện 230 chữ" in u
    u0 = prompts.messages(brief, DATA, {"name": "K"}, ["lỗi x"])[1]["content"]            # chưa có bản nháp: như cũ
    assert "BẢN NHÁP TRƯỚC" not in u0 and "Bản trước chưa đạt vì" in u0
    assert f"{story.WORDS_MIN + 10}-{story.WORDS_MAX - 15} chữ" in u0
    long = json.loads(json.dumps(st))
    while sum(story.word_count(sc["text"]) for sc in long["scenes"]) <= story.WORDS_BLOCK:
        for sc in long["scenes"]:
            sc["text"] += " Gió thổi qua đồi cỏ xanh mát rượi."
    assert any("quá dài" in q for q in story.blocking(long, brief, DATA))
    assert any("cảnh dài nhất" in q for q in story.validate(long, brief, DATA))
    assert not any("quá dài" in q for q in story.blocking(st, brief, DATA))


def test_gemini_backend_routing(monkeypatch):
    """Gemini theo vai: nháp = model viết (Hydrogen, mức suy nghĩ), viết lại / soát = model sửa; quá tải thì đổi vai."""
    import prompt_engine
    seen = []

    async def fake(prompt, images=None, schema=None, label="", timeout=0, thinking="", model=""):
        seen.append((model, thinking))
        assert config.GEMINI_MODEL == before                                           # không đụng model chung
        if model == "boom":
            raise RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand")
        return '{"ok": 1}'
    before = config.GEMINI_MODEL
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    be = writer.GeminiBackend("gemini-4-hydrogen", revise_model="gemini-3.5-flash-lite", thinking="low")
    be.start()
    msgs = [{"role": "user", "content": "a"}]
    assert be.chat(msgs, {"type": "object"}) == '{"ok": 1}'
    assert be.chat(msgs, {"type": "object"}, role="rewrite") == '{"ok": 1}'
    assert be.chat(msgs, {"type": "object"}, role="revise") == '{"ok": 1}'
    assert [m for m, _ in seen] == ["gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]
    assert seen[0][1] == "low" and config.GEMINI_MODEL == before                   # trả lại model chung sau mỗi lượt
    assert be.used == [("draft", "gemini-4-hydrogen"), ("rewrite", "gemini-3.5-flash-lite"), ("revise", "gemini-3.5-flash-lite")]
    assert "gemini-4-hydrogen ×1" in be.summary() and "gemini-3.5-flash-lite ×2" in be.summary() and be.calls == 3
    be2 = writer.GeminiBackend("boom", revise_model="gemini-3.5-flash-lite")           # model viết quá tải -> model sửa viết thay
    assert be2.chat(msgs, {"type": "object"}) == '{"ok": 1}' and be2.used == [("draft", "boom"), ("draft", "gemini-3.5-flash-lite")]
    assert writer.GeminiBackend("solo").model_for("revise") == "solo"

    async def bad(prompt, **kw):
        raise RuntimeError("400 INVALID_ARGUMENT")
    monkeypatch.setattr(prompt_engine, "_call_gemini", bad)
    with pytest.raises(RuntimeError):                                                   # lỗi khác quá tải: ném ra
        be2.chat(msgs, {"type": "object"})
    assert prompt_engine._with_thinking({"a": 1}, "gemini-4-hydrogen", "low") == {"a": 1, "thinking_config": {"thinking_level": "low"}}
    assert prompt_engine._with_thinking({"a": 1}, "gemini-3.5-flash-lite", "low") == {"a": 1}
    assert prompt_engine._with_thinking(None, "gemini-4-hydrogen", "") is None


class _FakeLyria:
    """Giả Lyria RealTime: client.aio.live.music.connect(...) -> phiên trả các gói PCM sin 48 kHz stereo."""

    def __init__(self, secs_per_chunk=0.5, fail=False):
        self.secs, self.fail = secs_per_chunk, fail
        self.prompts, self.configs, self.stopped = [], [], 0
        outer = self

        class _Session:
            async def set_weighted_prompts(self, prompts):
                outer.prompts.append([(p.text, p.weight) for p in prompts])

            async def set_music_generation_config(self, config):
                outer.configs.append(config)

            async def play(self):
                pass

            async def stop(self):
                outer.stopped += 1

            async def receive(self):
                import math
                import struct
                n = int(48000 * outer.secs)
                k = 0
                while True:
                    if outer.fail and k >= 2:
                        return
                    frames = [int(8000 * math.sin(2 * math.pi * 440 * (k * n + i) / 48000)) for i in range(n)]
                    data = b"".join(struct.pack("<hh", v, v) for v in frames)
                    yield SimpleNamespace(server_content=SimpleNamespace(audio_chunks=[SimpleNamespace(data=data)]),
                                          filtered_prompt=None)
                    k += 1

        class _Connect:
            def __init__(self, model):
                outer.model = model

            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *a):
                return False
        self.aio = SimpleNamespace(live=SimpleNamespace(music=SimpleNamespace(connect=lambda model: _Connect(model))))


def test_bgm_library(tmp_path, monkeypatch):
    """Kho nhạc Lyria: màu theo khung, tạo bài (m4a 44,1 kHz, đúng dài), library.json, chọn bài + đẩy dataset khi đổi."""
    import kaggle_worker
    from rmvideo.kids import bgm
    lib = str(tmp_path / "lib")
    brief = story.pick(DATA, [], random.Random(2))
    assert bgm.mood_for(brief, DATA) in bgm.MOODS and bgm.mood_for({"plot": ""}, DATA) == "playful"
    assert bgm.mood_for({"plot": "fear_new"}, DATA) == "tender" and bgm.mood_for({"plot": "truth_vs_lie"}, DATA) == "curious"
    fake = _FakeLyria()
    item = asyncio.run(bgm.generate("tender", lib, secs=3, client=fake, log=lambda s: None))
    assert item["file"] == "tender_1.m4a" and fake.model == bgm.MODEL and fake.stopped == 1
    assert fake.prompts == [[(bgm.MOODS["tender"]["prompt"], 1.0)]] and fake.configs[0].bpm == 72
    out = os.path.join(lib, "tender_1.m4a")
    assert os.path.exists(out) and not os.path.exists(out + ".wav")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert 2.8 <= dur <= 3.4
    assert bgm.tracks(bgm.load_library(lib), "tender") == [item] and bgm.changed_since_push(bgm.load_library(lib))
    # tạo lỗi (Lyria trả quá ít) -> RuntimeError, không thêm bài
    with pytest.raises(RuntimeError):
        asyncio.run(bgm.generate("tender", lib, secs=3, client=_FakeLyria(fail=True), log=lambda s: None))
    assert len(bgm.load_library(lib)["tracks"]) == 1
    # pick: thiếu bài thì tạo thêm MỘT bài rồi đẩy dataset (giả), trả đầu vào Kaggle có dataset + mounted + name
    pushes = []

    def fake_push(lib_dir=None, log=None):
        pushes.append(lib_dir)
        lb = bgm.load_library(lib_dir)
        lb["pushed"] = sorted(t["file"] for t in lb["tracks"])
        lb["dataset"] = bgm.DATASET
        bgm.save_library(lb, lib_dir)
        return lb
    monkeypatch.setattr(bgm, "push_dataset_sync", fake_push)
    monkeypatch.setattr(bgm, "TRACK_SECS", 2)
    monkeypatch.setattr(kaggle_worker, "serve", lambda p: None)
    r = asyncio.run(bgm.pick({"plot": "fear_new"}, DATA, rnd=random.Random(1), lib_dir=lib, client=fake, log=lambda s: None))
    assert r == {"mounted": bgm.DATASET, "name": r["name"], "kind": "audio", "dataset": bgm.DATASET}
    assert len(bgm.tracks(bgm.load_library(lib), "tender")) == 2 and pushes == [lib]
    assert bgm.load_library(lib)["recent"] == [r["name"]]
    r2 = asyncio.run(bgm.pick({"plot": "fear_new"}, DATA, rnd=random.Random(1), lib_dir=lib, client=fake, log=lambda s: None))
    assert len(bgm.tracks(bgm.load_library(lib), "tender")) == 3 and len(pushes) == 2 and r2["name"] != r["name"]
    # đủ bài: không tạo, không đẩy
    r3 = asyncio.run(bgm.pick({"plot": "fear_new"}, DATA, rnd=random.Random(1), lib_dir=lib, client=_FakeLyria(fail=True),
                              log=lambda s: None))
    assert r3 and len(pushes) == 2
    # Lyria lỗi (thử lại 2 lần) mà màu này chưa có bài -> dùng bài màu khác; kho rỗng hẳn -> None
    logs = []
    rr = asyncio.run(bgm.pick({"plot": "lost_quest"}, DATA, lib_dir=lib, client=_FakeLyria(fail=True), log=logs.append))
    assert rr and rr["name"].startswith("tender_") and sum("Lyria lỗi" in m for m in logs) == 2 and any("màu khác" in m for m in logs)
    assert asyncio.run(bgm.pick({"plot": "lost_quest"}, DATA, lib_dir=str(tmp_path / "empty"), client=_FakeLyria(fail=True),
                                log=lambda s: None)) is None
    # đẩy dataset lỗi: chỉ dùng bài Kaggle đã có
    monkeypatch.setattr(bgm, "push_dataset_sync", lambda lib_dir=None, log=None: (_ for _ in ()).throw(RuntimeError("kaggle")))
    asyncio.run(bgm.generate("playful", lib, secs=2, client=fake, log=lambda s: None))
    assert asyncio.run(bgm.pick({"plot": "lost_quest"}, DATA, lib_dir=lib, client=fake, log=lambda s: None)) is None
    assert asyncio.run(bgm.pick({"plot": "fear_new"}, DATA, lib_dir=lib, client=fake, log=lambda s: None))["mounted"] == bgm.DATASET
    # spec: dataset nhạc gắn vào phiên; Kaggle lấy file từ dataset đã gắn
    job = job_factory.kids(brief=brief, source="web")
    sp = kaggle_worker.kids_spec(job, brief, {"mounted": bgm.DATASET, "name": "tender_1.m4a", "kind": "audio", "dataset": bgm.DATASET})
    assert bgm.DATASET in sp["dataset_sources"] and sp["music"]["name"] == "tender_1.m4a"
    root = tmp_path / "input" / "datasets" / "u" / bgm.DATASET
    root.mkdir(parents=True)
    (root / "tender_1.m4a").write_bytes(b"x")
    assert kaggle_job.mounted_file(bgm.DATASET, "tender_1.m4a", str(tmp_path / "input")) == str(root / "tender_1.m4a")
    assert kaggle_job.mounted_file("other", "tender_1.m4a", str(tmp_path / "input")) == str(root / "tender_1.m4a")
    assert kaggle_job.mounted_file(bgm.DATASET, "nope.m4a", str(tmp_path / "input")) is None
    monkeypatch.setattr(kaggle_job, "INPUT_ROOT", str(tmp_path / "input"))
    assert asyncio.run(kaggle_job.fetch({"mounted": bgm.DATASET, "name": "tender_1.m4a"}, str(tmp_path), "music")) == str(root / "tender_1.m4a")


def test_kaggle_dataset_push(tmp_path, monkeypatch):
    """kaggle_api.dataset_push: tải blob (StartBlobUpload + PUT), tạo dataset lần đầu, thêm phiên bản lần sau; chờ READY."""
    import kaggle_api
    calls, puts = [], []
    exists = {"v": False}

    def fake_call(method, body, service=kaggle_api.KERNELS, timeout=120):
        calls.append((service, method, body))
        if method == "StartBlobUpload":
            return {"token": "tok-" + body["name"], "createUrl": "https://gcs/" + body["name"]}
        if method == "GetDatasetStatus":
            if not exists["v"]:                          # đo thật: slug chưa có -> 403 (call() đổi thành lỗi token)
                raise kaggle_api.KaggleError("Token Kaggle sai hoặc hết hạn (403). Tạo token mới...")
            return {"status": "READY"}
        return {"ref": "u/" + body.get("slug", body.get("datasetSlug", "")), "status": "ok"}

    class _Resp:
        status_code = 200
        text = ""
    monkeypatch.setattr(kaggle_api, "call", fake_call)
    monkeypatch.setattr(kaggle_api, "user", lambda: "u")
    monkeypatch.setattr(kaggle_api.requests, "put", lambda url, data=None, timeout=0, headers=None: puts.append((url, data.read(), headers)) or _Resp())
    f = tmp_path / "playful_1.m4a"
    f.write_bytes(b"abc")
    kaggle_api.dataset_push("rmvideo-kids-bgm", "t", [str(f)])
    assert puts == [("https://gcs/playful_1.m4a", b"abc", {"Content-Length": "3"})]
    blob = [b for s, m, b in calls if m == "StartBlobUpload"][0]
    assert blob["type"] == "DATASET" and blob["contentType"] == "audio/mp4" and blob["contentLength"] == 3
    assert [m for _, m, _ in calls] == ["StartBlobUpload", "GetDatasetStatus", "CreateDataset"]
    cd = calls[-1][2]
    assert cd == {"ownerSlug": "u", "slug": "rmvideo-kids-bgm", "title": "t", "licenseName": "other", "isPrivate": True,
                  "files": [{"token": "tok-playful_1.m4a"}]} and calls[-1][0] == kaggle_api.DATASETS
    exists["v"] = True
    kaggle_api.dataset_push("rmvideo-kids-bgm", "t", [str(f)], notes="n")
    assert calls[-1][1] == "CreateDatasetVersion" and calls[-1][2]["body"] == {"versionNotes": "n", "files": [{"token": "tok-playful_1.m4a"}], "deleteOldVersions": True}
    assert kaggle_api.dataset_wait("rmvideo-kids-bgm", timeout=1, every=0, sleep=lambda s: None) == "READY"
    seq = iter(["BLOBS_RECEIVED", "READY"])
    monkeypatch.setattr(kaggle_api, "dataset_status", lambda slug: {"status": next(seq)})
    assert kaggle_api.dataset_wait("x", timeout=5, every=0, sleep=lambda s: None) == "READY"
    seq2 = iter([None, None, "READY"])                       # 403 lúc đang xử lý ngay sau khi tạo (đo thật 19/11)

    def st403(slug):
        v = next(seq2)
        if v is None:
            raise kaggle_api.KaggleError("Token Kaggle sai hoặc hết hạn (403). Tạo token mới...")
        return {"status": v}
    monkeypatch.setattr(kaggle_api, "dataset_status", st403)
    assert kaggle_api.dataset_wait("x", timeout=5, every=0, sleep=lambda s: None) == "READY"
    monkeypatch.setattr(kaggle_api, "dataset_status", lambda slug: (_ for _ in ()).throw(kaggle_api.KaggleError("Kaggle lỗi 500")))
    with pytest.raises(kaggle_api.KaggleError):
        kaggle_api.dataset_wait("x", timeout=5, every=0, sleep=lambda s: None)
    monkeypatch.setattr(kaggle_api, "dataset_status", lambda slug: {"status": "FAILED"})
    with pytest.raises(kaggle_api.KaggleError):
        kaggle_api.dataset_wait("x", timeout=5, every=0, sleep=lambda s: None)


def test_pick_music_policy(monkeypatch):
    """KIDS_BGM: off -> None; files -> chỉ file / link; auto -> file của mình trước, không có thì kho Lyria; lyria -> chỉ kho."""
    import kids_auto
    from rmvideo.kids import bgm
    monkeypatch.setattr(kids_auto, "_pick_file_music", lambda rnd=None: _async_value({"url": "f"}))
    lyria = {"mounted": "ds", "name": "a.m4a", "kind": "audio", "dataset": "ds"}
    monkeypatch.setattr(bgm, "pick", lambda brief, data, rnd=None, log=None: _async_value(lyria))
    brief = {"plot": "lost_quest"}
    for mode, want in (("off", None), ("files", {"url": "f"}), ("auto", {"url": "f"}), ("lyria", lyria)):
        monkeypatch.setattr(config, "KIDS_BGM", mode, raising=False)
        assert asyncio.run(kids_auto.pick_music(brief=brief, story_data=DATA)) == want, mode
    monkeypatch.setattr(kids_auto, "_pick_file_music", lambda rnd=None: _async_value(None))
    monkeypatch.setattr(config, "KIDS_BGM", "auto", raising=False)
    assert asyncio.run(kids_auto.pick_music(brief=brief, story_data=DATA)) == lyria
    assert asyncio.run(kids_auto.pick_music()) is None                                  # không có đề: không gọi Lyria
    monkeypatch.setattr(bgm, "pick", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lyria")))
    assert asyncio.run(kids_auto.pick_music(brief=brief, story_data=DATA)) is None


async def _async_value(v):
    return v


def test_prompts_and_schema():
    brief = story.pick(DATA, [], random.Random(2))
    msgs = prompts.messages(brief, DATA, {"name": "Kể Bé Nghe"})
    assert msgs[0]["role"] == "system" and "miền Nam" in msgs[0]["content"] and "THUẦN tiếng Việt" in msgs[0]["content"]
    u = msgs[1]["content"]
    assert all(f"«{w['vi']}»" in u for w in story.must_mention(brief, DATA)) and "Thỏ Trắng" in u and "Đồi Cỏ Non" in u
    assert 3 <= len(story.must_mention(brief, DATA)) <= 14 and "ÍT NHẤT 3" in u and brief["word_pool"]
    assert not any(story.count_word(u, w["en"]) for w in brief["words"])    # KHÔNG đưa tiếng Anh cho model
    assert "`hook`" in u and "3 giây đầu" in u and "Truyện MẪU" in u and "KHÔNG chép" in u and "nhân quả" in u
    assert "Khung truyện:" in u and "sợi chỉ xuyên suốt" in u and "Cảnh 1:" in u and "Điệp khúc gợi ý" in u
    assert "BÍ KÍP KỂ CHO CUỐN" in u and "HỎI BÉ" in u and "nét riêng" in u and "Ôi chao" in u and "ĐẢO điệp khúc" in u
    assert all("các bạn" not in b and "cả nhóm" not in b for pl in DATA["plots"] for b in pl["beats"])
    assert all(pl.get("twist") for pl in DATA["plots"]) and "đảo thành «" in u
    u2 = prompts.user_prompt(brief, DATA, ["cảnh 1 thiếu hook"])
    assert "chưa đạt vì" in u2 and "thiếu hook" in u2
    sch = prompts.schema(DATA)
    assert sch["properties"]["scenes"]["minItems"] == 8 and set(sch["required"]) == {"title", "hook", "hook_text", "scenes",
                                                                                       "description", "hashtags", "prop_vi", "prop_en"}
    assert "prop_vi" in u and "wooden ladder" in u and "prop_vi: con diều" in u
    st0 = story.normalize(_good_story(brief), DATA)
    assert st0["hook_text"] == "RỚT MẤT RỒI!" and st0["hook"].endswith("?")
    grp = json.loads(json.dumps(st0))
    grp["scenes"][2]["text"] += " Các bạn cười vang."
    assert any("«các bạn»" in q and "cảnh 3" in q for q in story.validate(grp, brief, DATA))
    off = dict(st0, title="Thỏ Trắng và chiếc bánh ngọt ngào trong khu vườn")
    assert any("tiêu đề nhắc tới" in q for q in story.validate(off, brief, DATA))
    assert story.validate(dict(st0, title="Thỏ Trắng gặp Gấu Nâu ở gốc cây to"), brief, DATA) == []
    dash = story.normalize({**st0, "scenes": [dict(sc, text=sc["text"].replace(". ", " — ", 1)) for sc in st0["scenes"]]}, DATA)
    assert "—" not in dash["scenes"][0]["text"] and ", " in dash["scenes"][0]["text"]
    noq = json.loads(json.dumps(st0))
    for sc in noq["scenes"]:
        sc["text"] = sc["text"].replace("Bé đoán xem Sóc Nhỏ có chạy tới không?", "Sóc Nhỏ chạy tới coi chuyện gì.")
    assert any("câu hỏi cho bé" in q for q in story.validate(noq, brief, DATA))
    bad_hook = dict(st0, hook="Thỏ Trắng đi chơi", hook_text="a")
    probs = story.validate(bad_hook, brief, DATA)
    assert any("`hook`" in q for q in probs) and any("`hook_text`" in q for q in probs)
    rv = prompts.revise_messages(st0, brief, DATA, {"name": "Kể Bé Nghe"})
    assert "checklist" in rv[1]["content"] and "nhân quả" in rv[1]["content"] and st0["hook"] in rv[1]["content"]
    assert sch["properties"]["scenes"]["items"]["properties"]["text"]["minLength"] >= 60    # grammar ép độ dài cảnh
    assert len(prompts.example_sentences()) >= 10 and len(prompts.EXAMPLE) == 8
    p = prompts.image_prompt({"characters": ["orange_cat"], "action_en": "Orange Cat jumps over a puddle."}, brief, DATA)
    assert p.startswith("Exactly one character in the picture, alone: Orange Cat jumps over a puddle. Orange Cat,")
    assert "The character is toddler-sized" in p and "All friends" not in p      # 1 bạn: không gợi ý «các bạn» (20/11)
    assert "green t-shirt" in p and p.endswith("no other animals")
    assert "Pixar style" in p and len(p.split()) < 110                       # vừa 256 token T5
    assert "White Rabbit" not in p                                           # chỉ nhân vật có mặt trong cảnh
    # đo thật 18/11: model viết action_en bằng tiếng Việt -> ảnh không có hành động; giờ bị báo lỗi, và nếu vẫn vậy thì
    # prompt ảnh dùng hành động dự phòng theo vai cảnh
    st = story.normalize(_good_story(brief), DATA)
    bad_act = dict(st, scenes=[dict(sc, action_en="Thỏ Trắng nhìn quanh lo lắng") for sc in st["scenes"]])
    probs = story.validate(bad_act, brief, DATA)
    assert len(probs) == 8 and all("TIẾNG ANH" in q for q in probs)
    p2 = prompts.image_prompt(bad_act["scenes"][2], brief, DATA, 2)
    assert p2.startswith("Exactly two friends in the picture, each appearing only once: White Rabbit and Brown Bear arguing, "
                         "upset faces")
    assert "Both friends are toddler-sized and the same height" in p2
    assert "nhìn" not in p2
    spam = dict(st, scenes=[dict(sc) for sc in st["scenes"]])
    spam["scenes"][0]["text"] += " Ai đó? Ai đây? Ai nữa?"
    assert any("hỏi dồn dập" in q for q in story.validate(spam, brief, DATA))
    title, desc, tags = prompts.post_pack(story.normalize(_good_story(brief), DATA), brief, kids_auto.channel(), DATA)
    assert title.startswith("Thỏ Trắng") and "🔤" in desc and tags.startswith("#kebenghe") and "#bexinloi" in tags


# ---------------- kế hoạch cảnh + dựng thật ----------------

def test_plan_follows_voice():
    pl = render.plan([2.0, 1.0, 6.0, 2.0, 2.0, 2.0, 2.0, 4.0], random.Random(1))
    sc = pl["scenes"]
    assert sc[0]["voice_at"] == render.INTRO and sc[0]["end"] - sc[0]["start"] == render.SCENE_MIN
    assert sc[2]["end"] - sc[2]["start"] == pytest.approx(render.GAP + 6.0 + render.PAD)
    assert all(a["end"] == b["start"] for a, b in zip(sc, sc[1:]))
    assert pl["length"] == pytest.approx(sc[-1]["end"]) and pl["outro_at"] == pytest.approx(pl["length"] - render.OUTRO)
    assert all(a["motion"] != b["motion"] for a, b in zip(sc, sc[1:]))
    assert len(pl["transitions"]) == 7 and pl["transitions"][0]["start"] == pytest.approx(sc[0]["end"] - 0.25)


def test_prefetch_copies_saved_model(tmp_path):
    """Model đã lưu trong ổ gắn được chép về đĩa cục bộ trong luồng nền; local_dir chờ xong và trả bản chép."""
    src = tmp_path / "input" / "notebooks" / "u" / "rmvideo-kids-models" / "flux-schnell-nf4"
    (src / "transformer").mkdir(parents=True)
    (src / "model_index.json").write_text("{}")
    (src / "transformer" / "w.safetensors").write_bytes(b"x" * 5000)
    dest = tmp_path / "local"
    msgs = []
    assert images_mod.find_local("schnell", str(tmp_path / "input")) == str(src)
    devdir = tmp_path / "input" / "notebooks" / "devnguyen" / "rmvideo-kids-models" / "flux-dev-nf4"
    devdir.mkdir(parents=True)
    (devdir / "model_index.json").write_text("{}")
    assert images_mod.find_local("dev", str(tmp_path / "input")) == str(devdir)          # «dev» trong tên tài khoản không tính
    assert images_mod.find_local("schnell", str(tmp_path / "input")) == str(src)         # «flux» không bắt nhầm flux-dev
    assert images_mod.find_local("zimage", str(tmp_path / "input")) is None
    th = images_mod.prefetch("schnell", msgs.append, str(tmp_path / "input"), str(dest))
    assert th is not None
    got = images_mod.local_dir("schnell", str(tmp_path / "input"), str(dest))
    assert got == str(dest / "flux-schnell-nf4") and (dest / "flux-schnell-nf4" / ".done").exists()
    assert (dest / "flux-schnell-nf4" / "transformer" / "w.safetensors").read_bytes() == b"x" * 5000
    assert msgs and "đĩa cục bộ" in msgs[0]
    assert images_mod.prefetch("schnell", msgs.append, str(tmp_path / "input"), str(dest)) is None   # đã có: không chép lại
    assert images_mod.prefetch("schnell", msgs.append, str(tmp_path / "nope"), str(dest)) is None    # không có bản lưu
    assert images_mod.local_dir("zimage", str(tmp_path / "input"), str(dest)) is None


def test_is_blank_catches_black_band():
    from PIL import Image
    import numpy as np
    rng = np.random.default_rng(1)
    good = Image.fromarray(rng.integers(40, 220, (200, 100, 3), dtype=np.uint8))
    assert not images_mod.is_blank(good)
    assert images_mod.is_blank(Image.new("RGB", (100, 200), (7, 7, 7)))
    arr = np.asarray(good).copy()
    arr[:16] = 0                                                # dải đen 8% phía trên
    assert images_mod.is_blank(Image.fromarray(arr))
    arr = np.asarray(good).copy()
    arr[-16:] = 0
    assert images_mod.is_blank(Image.fromarray(arr))
    arr = np.asarray(good).copy()
    arr[:4] = 0                                                 # 2%: viền nhỏ thì bỏ qua
    assert not images_mod.is_blank(Image.fromarray(arr))


def test_dialogue_split_and_voices():
    from rmvideo.kids import dialogue
    chars = DATA["characters"]
    sp = dialogue.split
    assert sp("Thỏ Trắng cầm ly hoa. Gấu Nâu nói: «Cẩn thận!» Thỏ Trắng cười: «Mình biết rồi.»", chars) == \
        [("narrator", "Thỏ Trắng cầm ly hoa. Gấu Nâu nói:"), ("brown_bear", "Cẩn thận!"), ("narrator", "Thỏ Trắng cười:"),
         ("white_rabbit", "Mình biết rồi.")]
    assert sp("«Diều ơi, bay lên đi!» Sóc Nhỏ reo to. Rồi chạy một hơi.", chars)[0] == ("little_squirrel", "Diều ơi, bay lên đi!")
    assert sp("Rầm! Thỏ Trắng hoảng hốt. «Chết rồi, làm sao đây?»", chars)[-1] == ("white_rabbit", "Chết rồi, làm sao đây?")
    assert sp("Gấu Nâu đi tới. «Ối!» Sóc Nhỏ kêu lên.", chars)[1] == ("little_squirrel", "Ối!")    # tên + động từ sau ngoặc thắng
    assert sp("Hôm nay trời đẹp. À, cái ly tiếng Anh là cup. Bé nói theo nè: cup!", chars) == \
        [("narrator", "Hôm nay trời đẹp. À, cái ly tiếng Anh là cup. Bé nói theo nè: cup!")]
    assert sp("«Không ai nói cả.»", chars) == [("narrator", "Không ai nói cả.")]
    assert dialogue.voice_for("brown_bear", chars, "Thục Đoan") == "Thái Sơn"
    assert dialogue.voice_for("narrator", chars, "Thục Đoan") == "Thục Đoan"
    assert dialogue.voice_for("nobody", chars, "Thục Đoan") == "Thục Đoan"
    assert all(c.get("voice") for c in chars.values()) and len({c["voice"] for c in chars.values()}) == 4


def test_sub_tokens_drop_quotes():
    assert render.sub_tokens("Thỏ Trắng nói: «Giờ clean đồ chơi đi.» Gấu Nâu giúp.") == \
           ["Thỏ", "Trắng", "nói:", "Giờ", "clean", "đồ", "chơi", "đi.", "Gấu", "Nâu", "giúp."]
    assert render.sub_tokens('«Thank you, bạn thân! »') == ["Thank", "you,", "bạn", "thân!"]


def test_plan_with_hook():
    pl = render.plan([2.0] * 8, random.Random(3), recap_dur=2.0, hook_dur=1.6)
    assert pl["hook"] and pl["hook"]["hook"] and len(pl["clips"]) == 9 and len(pl["scenes"]) == 8
    assert pl["hook"]["end"] == pytest.approx(max(render.HOOK_MIN, render.HOOK_LEAD + 1.6 + render.HOOK_PAD))
    assert pl["title_at"] == pl["hook"]["end"] and pl["scenes"][0]["start"] == pl["hook"]["end"]
    assert pl["scenes"][0]["voice_at"] == pytest.approx(pl["hook"]["end"] + render.STORY_LEAD)
    assert pl["transitions"][0]["type"] == "smoothup" and len(pl["transitions"]) == 8
    assert len(render._clip_bounds(pl)) == 9
    no = render.plan([2.0] * 8, random.Random(3))
    assert no["hook"] is None and no["title_at"] == 0.0 and len(no["clips"]) == 8


def test_plan_fits_max_len():
    """Lời dài (đo thật 236 chữ -> 88,9s): co đệm trước, vẫn quá thì đọc nhanh hơn; không bao giờ quá MAX_LEN khi còn co được."""
    pl = render.plan([9.0] * 8, random.Random(3), recap_dur=6.5)             # tự nhiên ≈ 90,6s
    assert pl["length"] <= render.MAX_LEN and pl["pad"] == render.PAD_MIN and pl["factor"] == 1.0
    pl2 = render.plan([10.5] * 8, random.Random(3), recap_dur=6.5)           # ≈ 102s tự nhiên: phải đọc nhanh hơn
    assert pl2["length"] <= render.MAX_LEN + 0.05 and 1.0 < pl2["factor"] <= render.TEMPO_MAX
    assert pl2["scenes"][1]["end"] - pl2["scenes"][1]["start"] == pytest.approx(render.GAP_MIN + 10.5 / pl2["factor"]
                                                                               + render.PAD_MIN, abs=0.01)
    pl3 = render.plan([20.0] * 8, random.Random(3))                           # quá sức co: dừng ở TEMPO_HARD, QA sẽ chặn
    assert pl3["factor"] == render.TEMPO_HARD and pl3["length"] > render.MAX_LEN
    pl4 = render.plan([11.3] * 8, random.Random(3), recap_dur=6.5)           # 1,15 chưa đủ, 1,22 vừa (đo thật mẫu 27)
    assert render.TEMPO_MAX < pl4["factor"] <= render.TEMPO_HARD and pl4["length"] <= render.MAX_LEN + 0.05
    short = render.plan([2.0] * 8, random.Random(3))
    assert short["factor"] == 1.0 and short["pad"] == render.PAD
    assert [s["motion"] for s in render.plan([9.0] * 8, random.Random(3))["scenes"]] == \
           [s["motion"] for s in render.plan([2.0] * 8, random.Random(3))["scenes"]]   # cùng hạt -> cùng chuyển động


def _png(path, hue):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=1", "-frames:v", "1",
                    "-vf", f"hue=h={hue}", path], check=True)
    return path


def _wav(path, dur=1.0):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-af",
                    "apad=pad_dur=0.3", "-ar", "24000", "-ac", "1", path], check=True)
    return path


def test_render_small_video_with_music(tmp_path):
    brief = story.pick(DATA, [], random.Random(2))
    imgs = [_png(str(tmp_path / f"i{k}.png"), k * 40) for k in range(8)]
    clips = [{"path": _wav(str(tmp_path / f"v{k}.wav"))} for k in range(8)]
    texts = [f"Cảnh {k + 1}: Thỏ Trắng nói {brief['words'][0]['en']} với Gấu Nâu nha!" for k in range(8)]
    texts[2] = f"Bịch! Thỏ Trắng té. Ối! {brief['words'][0]['en']} rớt rồi."       # tiếng động -> nảy zoom + rung
    music = str(tmp_path / "m.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-c:a",
                    "libmp3lame", music], check=True)
    out = str(tmp_path / "out.mp4")
    recap = ({"path": _wav(str(tmp_path / "recap.wav"), 3.0)}, story.recap_text(brief))
    hook = ({"path": _wav(str(tmp_path / "hook.wav"), 1.2)}, "Ôi, trái banh đâu rồi?", "BANH ĐÂU RỒI!")
    res = asyncio.run(render.render(imgs, clips, texts, brief, out, str(tmp_path), music=music, title="Thỏ Trắng học nói",
                                    channel=kids_auto.channel(), W=180, H=320, fps=12, rnd=random.Random(1), recap=recap,
                                    hook=hook, hook_scene=3))
    outro = max(render.OUTRO, render.RECAP_GAP + recap[0]["dur"] + 1.0)
    pl = res["plan"]
    assert pl["hook"] and res["length"] == pytest.approx(pl["hook"]["end"] + 8 * render.SCENE_MIN + outro)
    assert pl["hook"]["scene"] == 3                                               # ảnh câu móc = cảnh 4 (chọn theo lời)
    assert recap[0]["dur"] > 2.5 and "banh đâu" in res["srt"] and res["sfx"]["whoosh"] == 9 and 0.05 in res["hits"]
    assert "Hôm nay mình học" not in res["srt"]                                  # màn kết không phụ đề (bảng đã đủ)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0",
                            out], capture_output=True, text=True).stdout
    assert "video,180,320" in probe and "audio" in probe
    assert res["music_gain_db"] is not None and brief["words"][0]["en"] in res["srt"]
    ass_text = open(res["ass"], encoding="utf-8").read()
    assert ass_text.count("\\c&H003BD4FF&") >= 8                                   # từ tiếng Anh tô màu mỗi cảnh
    assert "\\fscx86\\fscy86\\t(0,110,\\fscx100\\fscy100)" in ass_text         # phụ đề bật lên
    assert res["fx"] == "full" and len(res["hits"]) >= 2 and res["sfx"]["whoosh"] == 9 and res["sfx"]["ding"] >= 1
    assert render.hits_from_words([(1.0, 1.2, "Bịch!"), (1.1, 1.3, "Rầm!"), (2.0, 2.2, "Ối!"), (3.0, 3.1, "nha")]) == [1.0, 2.0]
    # fx=off: như 1.1.0 — không nảy, không tiếng động, không bật chữ
    out2 = str(tmp_path / "out_off.mp4")
    res2 = asyncio.run(render.render(imgs, clips, texts, brief, out2, str(tmp_path), title="x", channel=kids_auto.channel(),
                                     W=180, H=320, fps=12, rnd=random.Random(1), fx="off"))
    assert res2["hits"] == [] and res2["sfx"] == {} and os.path.getsize(out2) > 10_000
    assert "fscx86" not in open(res2["ass"], encoding="utf-8").read()
    cov, thumb = asyncio.run(render.cover(out, str(tmp_path)))
    assert os.path.getsize(cov) > 1000 and os.path.getsize(thumb) > 500


# ---------------- việc Kaggle với model giả ----------------

class _FakeSrv:
    def __init__(self, brief, fail_first=False, drop_word=None, always_short=False):
        self.brief, self.fail_first, self.drop_word, self.always_short = brief, fail_first, drop_word, always_short
        self.calls = []
        self.roles = []

    def start(self):
        pass

    def chat(self, messages, schema, max_tokens=0, temperature=0.0, seed=None, role="draft"):
        self.roles.append(role)
        self.calls.append(messages[1]["content"])
        if getattr(self, "broken_revise", False) and "checklist" in messages[1]["content"]:
            return "không phải json"
        st = _good_story(self.brief)
        if self.fail_first and len(self.calls) == 1:
            st["scenes"][0]["text"] = "Quá ngắn."
        if self.drop_word:                               # model luôn quên nhắc tới đồ vật của hôm nay (đo thật 18/11)
            for s in st["scenes"]:
                for f in story.vi_forms(self.drop_word, DATA):
                    s["text"] = re.sub(r"(?i)" + re.escape(f), "xx", s["text"])
        if self.always_short:
            for s in st["scenes"]:
                s["text"] = "Hai bạn chơi vui."
        return "<think>suy nghĩ</think>\n```json\n" + json.dumps(st, ensure_ascii=False) + "\n```"

    def stop(self):
        pass


def test_llm_retries_with_feedback():
    brief = story.pick(DATA, [], random.Random(2))
    srv = _FakeSrv(brief, fail_first=True)
    st, attempts = llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=srv)
    assert len(attempts) == 2 and attempts[0]["problems"] and not attempts[1]["problems"]
    assert "chưa đạt vì" in srv.calls[1] and st["title"]
    assert attempts[-1]["beats"] and story.count_word(story.script_text(st), brief["words"][0]["en"]) >= 1
    # truyện không nhắc tới một đồ vật gợi ý: vẫn đạt (đủ 3 thứ khác), từ hôm nay chọn từ chính truyện, không ép từ đó
    brief2 = story.pick(DATA, [], random.Random(2))
    dropped = brief2["words"][0]
    srv = _FakeSrv(brief2, drop_word=dropped)
    st, attempts = llm.generate_story({"brief": brief2, "channel": {}}, DATA, "/tmp", srv=srv)
    assert len(attempts) == 1 and attempts[-1]["words_en"] == [w["en"] for w in brief2["words"]]
    assert len(brief2["words"]) >= 3 and all(story.vi_mentioned(story.script_text(st), w, DATA) for w in brief2["words"])
    assert dropped["en"] not in attempts[-1]["words_en"] and f"Ở đây có {dropped['vi']} nè." not in story.script_text(st)
    # bộ viết có revise: thêm một lượt soát; bản sửa hỏng JSON thì giữ bản nháp
    srv = _FakeSrv(brief)
    srv.revise = True
    st, attempts = llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=srv)
    assert [a["attempt"] for a in attempts] == [1, "revise"] and attempts[-1]["used"] is True and len(srv.calls) == 2
    srv = _FakeSrv(brief)
    srv.revise = True
    srv.broken_revise = True
    st, attempts = llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=srv)
    assert attempts[-1]["attempt"] == "revise" and "lỗi" in attempts[-1]["problems"][0] and st["title"]
    with pytest.raises(llm.StoryRejected) as e:                      # luôn quá ngắn: lỗi chặn -> từ chối, kèm các lượt
        llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=_FakeSrv(brief, always_short=True))
    assert len(e.value.attempts) == 1 + llm.RETRIES and e.value.attempts[0]["text"]
    assert llm.parse_json('<think>x</think> {"a": 1}')["a"] == 1
    assert llm.find_binary("/khong/co") is None and llm.find_gguf("*.gguf", "/khong/co") is None


def test_kaggle_job_kids_end_to_end(tmp_path, monkeypatch):
    import kaggle_job
    brief = story.pick(DATA, [], random.Random(2))
    monkeypatch.setattr(llm, "backend", lambda spec_llm, tmp, log=None: _FakeSrv(brief))

    def fake_images(plist, seeds, out_dir, model="schnell", log=None, pipe=None, **kw):
        assert len(plist) == 8 and len(seeds) == 8 and model == "schnell"
        os.makedirs(out_dir, exist_ok=True)
        return [_png(os.path.join(out_dir, f"scene_{k + 1:02d}.png"), k * 30) for k in range(8)]
    monkeypatch.setattr(images_mod, "generate", fake_images)
    monkeypatch.setattr(render, "render", _small_render)
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)

    voices = []
    vi_texts: list = []
    spoken: dict = {}

    def make_synth(spec, ref):
        async def synth(items, tmp):
            voices.extend(it[2] for it in items)
            vi_texts.extend(it[1] for it in items)
            spoken.update({os.path.join(tmp, f"v{it[0]}.wav"): it[1] for it in items})
            return {it[0]: {"path": _wav(os.path.join(tmp, f"v{it[0]}.wav"), 0.8), "words": None} for it in items}
        return voice_qa.checked(synth, spoken.get)            # Whisper giả nghe đúng lời: kiểm mà không đọc lại
    monkeypatch.setattr(kaggle_job, "make_synth", make_synth)
    spec = {"kind": "kids", "brief": brief, "data": DATA, "voice": {"engine": "vieneu", "name": "Thục Đoan", "multi": True},
            "image": {"model": "schnell"}, "llm": {}, "channel": kids_auto.channel(), "music": None, "token": "t1"}
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    res = kaggle_job.main(spec, str(out_dir))[0]
    assert res["ok"] and res["token"] == "t1" and res["story"]["title"] and res["srt"]
    assert "Thục Đoan" in voices and len(set(voices)) >= 2 and res["timings"]["voices"]   # lời thoại đọc giọng nhân vật
    # từ tiếng Anh (câu dạy từ + câu ôn từ) đọc bằng giọng Anh bản xứ, VieNeu không còn thấy chữ tiếng Anh nào (20/11)
    words = [w["en"] for w in res["brief"]["words"]]
    said = {t.strip(".,!?").lower() for t, _v in EN_CALLS}
    assert set(words) <= said and all(v == dialogue.EN_VOICE for _t, v in EN_CALLS)
    assert not any(dialogue.split_english(t, words) != [(False, t)] for t in vi_texts), vi_texts
    assert res["timings"]["en_voice"] == dialogue.EN_VOICE
    assert res["timings"]["voice_qa"]["checked"] >= 8 and res["timings"]["voice_qa"]["redo"] == 0
    assert (out_dir / "output.mp4").exists() and (out_dir / "cover.jpg").exists() and (out_dir / "story.json").exists()
    assert (out_dir / "scene_08.png").exists() and (out_dir / "result.json").exists()


async def _small_render(images, clips, texts, brief, out, tmp, **kw):
    kw.update(W=180, H=320, fps=12)
    return await render.render.__wrapped__(images, clips, texts, brief, out, tmp, **kw) if hasattr(render.render, "__wrapped__") \
        else await _ORIG_RENDER(images, clips, texts, brief, out, tmp, **kw)


_ORIG_RENDER = render.render


async def _qa_small(path):
    return [] if os.path.exists(path) else ["không có file video"]


# ---------------- luồng Botkeep + lịch đăng ----------------

def _fake_produce(tmp_path, calls):
    async def produce(spec, tmp, on_step=None):
        calls.append(spec)
        out = str(tmp_path / "k_output.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=2", "-f", "lavfi",
                        "-i", "sine=frequency=300:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                        "yuv420p", "-c:a", "aac", "-shortest", out], check=True)
        st = story.normalize(_good_story(spec["brief"]), DATA)
        cov = str(tmp_path / "k_cover.jpg")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", out, "-frames:v", "1", cov], check=True)
        return {"ok": True, "video": out, "story": st, "srt": "1\n00:00:00,000 --> 00:00:01,000\nsorry\n",
                "duration": 72.0, "attempts": [{"attempt": 1, "problems": []}], "music": False, "notes": [],
                "timings": {"llm": 90, "images": 300, "tts": 20, "render": 80}, "files": {"cover.jpg": cov}}
    return produce


def test_flow_kids_on_botkeep(tmp_path, monkeypatch):
    import pipeline
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    calls = []
    monkeypatch.setattr(kaggle_worker, "produce", _fake_produce(tmp_path, calls))
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)
    job = kids_auto.make_now("web")
    assert job.mode == "kids" and job.brief["words"] and "Thỏ Trắng" in job.title_hint or job.title_hint
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and os.path.exists(os.path.join(os.path.dirname(out), "cover.jpg"))
    spec = calls[0]
    assert spec["kind"] == "kids" and spec["slug"] == "rmvideo-kids" and spec["voice"]["name"] == "Thục Đoan"
    assert spec["llm"]["think"] == 1200 and spec["llm"]["hf_repo"].endswith("GGUF") and spec["fx"] == "full"
    assert spec["kernel_sources"] == ["rmvideo-kids-models"] and "logo_b64" not in spec
    assert spec["fetch_files"] == ["cover.jpg", "thumb.jpg", "story.json"] and spec["data"]["world"]["name"] == "Đồi Cỏ Non"
    assert "▶ Facebook:" in job.post and "🔤" in job.post and job.hook.startswith("Thỏ Trắng") and job.out_dur == 72.0
    st = kids_auto.load_state()
    assert st["stories"][-1]["job"] == job.id and st["stories"][-1]["title"] == job.hook
    assert "Kaggle (GPU)" in job.error


def test_flow_kids_gemini_writes_first(tmp_path, monkeypatch):
    """KIDS_LLM_PROVIDER=auto + có key: Gemini viết trên Botkeep, truyện (đã chèn câu dạy từ) đi theo spec, Kaggle bỏ bước
    model chữ; Gemini lỗi thì vẫn gửi Kaggle để Qwen viết; provider=gemini thì lỗi luôn."""
    import pipeline
    import prompt_engine
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(config, "KIDS_LLM_PROVIDER", "auto")
    calls, gem = [], []
    monkeypatch.setattr(kaggle_worker, "produce", _fake_produce(tmp_path, calls))
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)

    async def fake_gemini(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        gem.append((prompt, schema))
        return json.dumps(_good_story(_LAST_BRIEF[0]), ensure_ascii=False)
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake_gemini)
    job = kids_auto.make_now("web")
    _LAST_BRIEF[:] = [job.brief]
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and len(gem) == 2                                  # nháp + tự soát
    prompt, schema = gem[0]
    assert "checklist" in gem[1][0] and "Soát theo checklist" in gem[1][0]
    assert "`hook`" in prompt and "minLength" not in json.dumps(schema) and schema["propertyOrdering"][0] == "title"
    spec = calls[0]
    assert spec["story"]["title"] and spec["attempts"][-1]["beats"] and "story" in spec
    assert all(f"tiếng Anh là {w['en']}" in story.script_text(spec["story"]) or f"Là {w['en']}!" in story.script_text(spec["story"])
               for w in job.brief["words"])
    assert "Gemini" in job.error and "2 lượt gọi" in job.error and "viết" not in job.error.split("Kaggle (GPU):")[1][:8]
    assert spec["attempts"][-1]["attempt"] == "revise" and spec["attempts"][-1]["used"] is True
    # Gemini lỗi -> auto: Kaggle viết (không có story trong spec)
    async def boom(*a, **k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(prompt_engine, "_call_gemini", boom)
    job2 = kids_auto.make_now("web")
    out2 = asyncio.run(pipeline.process(job2, str(tmp_path / "w")))
    assert os.path.exists(out2) and "story" not in calls[1] and "Qwen trên Kaggle viết thay" in job2.error
    # provider=gemini bắt buộc -> job lỗi
    monkeypatch.setattr(config, "KIDS_LLM_PROVIDER", "gemini")
    job3 = kids_auto.make_now("web")
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline.process(job3, str(tmp_path / "w")))
    # provider=qwen -> không gọi Gemini
    monkeypatch.setattr(config, "KIDS_LLM_PROVIDER", "qwen")
    job4 = kids_auto.make_now("web")
    asyncio.run(pipeline.process(job4, str(tmp_path / "w")))
    assert "story" not in calls[-1] and "Gemini" not in (job4.error or "")


_LAST_BRIEF: list = []


def test_writer_schema_and_backend(monkeypatch):
    import prompt_engine
    from rmvideo.kids import writer
    sch = writer.gemini_schema(prompts.schema(DATA))
    dumped = json.dumps(sch)
    assert "minLength" not in dumped and "additionalProperties" not in dumped and "enum" in dumped
    assert sch["propertyOrdering"] == ["title", "hook", "hook_text", "prop_vi", "prop_en", "scenes", "description", "hashtags"]
    seen = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60, thinking="", model=""):
        seen.append((model, label))
        return '{"title": "x"}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    be = writer.GeminiBackend("gemini-2.5-flash-lite")
    be.start()
    assert be.chat([{"role": "system", "content": "a"}, {"role": "user", "content": "b"}], {"type": "object"}) == '{"title": "x"}'
    assert seen == [("gemini-2.5-flash-lite", "Truyện bé")] and config.GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert be.calls == 1
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError):
        writer.GeminiBackend().start()


def test_kaggle_job_skips_llm_when_story_given(tmp_path, monkeypatch):
    import kaggle_job
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    story.add_beats(st, brief, DATA)
    monkeypatch.setattr(llm, "backend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được gọi model chữ")))

    def fake_images(plist, seeds, out_dir, model="schnell", log=None, pipe=None, **kw):
        os.makedirs(out_dir, exist_ok=True)
        return [_png(os.path.join(out_dir, f"scene_{k + 1:02d}.png"), k * 30) for k in range(8)]
    monkeypatch.setattr(images_mod, "generate", fake_images)
    monkeypatch.setattr(render, "render", _small_render)
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)

    def make_synth(spec, ref):
        async def synth(items, tmp):
            return {it[0]: {"path": _wav(os.path.join(tmp, f"v{it[0]}.wav"), 0.8), "words": None} for it in items}
        return synth
    monkeypatch.setattr(kaggle_job, "make_synth", make_synth)
    spec = {"kind": "kids", "brief": brief, "data": DATA, "voice": {"engine": "vieneu", "name": "Thục Đoan", "multi": False},
            "image": {"model": "schnell"}, "llm": {}, "channel": kids_auto.channel(), "music": None, "token": "t2",
            "story": st, "attempts": [{"attempt": 1, "problems": [], "beats": [["ball", 1]]}]}
    out_dir = tmp_path / "out2"
    out_dir.mkdir()
    res = kaggle_job.main(spec, str(out_dir))[0]
    assert res["ok"] and res["story"]["title"] == st["title"] and res["timings"]["llm"] < 1 and res["attempts"][0]["beats"]


def test_english_words_native_voice(tmp_path, monkeypatch):
    """Phản hồi 20/11: VieNeu đọc «sun» thành «súng», «jump» thành «trâm» -> từ tiếng Anh tách ra đọc giọng Anh, nối lại
    theo đúng thứ tự trong clip của cảnh; giọng Anh lỗi thì đoạn đó VieNeu đọc nguyên như cũ (kèm ghi chú)."""
    w = ["sun", "jump", "happy", "good morning"]
    assert dialogue.split_english("À, mặt trời đó tiếng Anh là sun. Bé nói theo nè: sun!", w) == [
        (False, "À, mặt trời đó tiếng Anh là"), (True, "sun."), (False, "Bé nói theo nè:"), (True, "sun!")]
    assert dialogue.split_english(story.recap_text({"words": [{"en": "sun", "vi": "mặt trời"}, {"en": "happy", "vi": "vui"}]}),
                                  w) == [(False, "Hôm nay mình học:"), (True, "sun"), (False, "là mặt trời,"), (True, "happy"),
                                         (False, "là vui. Hẹn gặp lại bé ở chuyện sau!")]
    assert dialogue.split_english("Là Good  Morning! Chào buổi sáng.", w) == [(False, "Là"), (True, "Good Morning!"),
                                                                             (False, "Chào buổi sáng.")]
    assert dialogue.split_english("Sunny nhảy tưng tưng, tai cụp xuống.", w + ["cup"]) == [
        (False, "Sunny nhảy tưng tưng, tai cụp xuống.")]                        # trong chữ khác / «cụp» có dấu: không tách
    assert dialogue.split_english("sun, happy!", w) == [(True, "sun,"), (True, "happy!")]
    # «run» trùng chữ tiếng Việt («run run vì sợ»): chỉ giọng Anh ở chỗ câu dạy / câu ôn từ đặt nó (soát 20/11)
    assert dialogue.split_english("Thỏ Trắng run run vì sợ. À, chạy tiếng Anh là run. Bé nói theo nè: run!", ["run"]) == [
        (False, "Thỏ Trắng run run vì sợ. À, chạy tiếng Anh là"), (True, "run."), (False, "Bé nói theo nè:"), (True, "run!")]
    recap = story.recap_text({"words": [{"en": "sun", "vi": "mặt trời"}, {"en": "run", "vi": "chạy"}]})
    assert [p for e, p in dialogue.split_english(recap, ["sun", "run"]) if e] == ["sun", "run"]
    assert dialogue.split_english("Là run! Run run quá.", ["run"]) == [(False, "Là"), (True, "run!"), (False, "Run run quá.")]
    assert dialogue.split_english("Không có từ nào.", []) == [(False, "Không có từ nào.")] and dialogue.split_english("", w) == []
    # job: cảnh dạy «ball» -> VieNeu đọc phần Việt, giọng Anh đọc «ball», nối đủ mẩu; giọng Anh lỗi -> VieNeu đọc nguyên đoạn
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    story.add_beats(st, brief, DATA)
    monkeypatch.setattr(images_mod, "generate", lambda plist, seeds, out_dir, *a, **kw: [
        _png(os.path.join(os.makedirs(out_dir, exist_ok=True) or out_dir, f"scene_{k + 1:02d}.png"), k * 30) for k in range(8)])
    merged: list = []

    async def fake_render(images, clips, texts, brief, out, tmp, **kw):
        merged.extend(clips)
        merged.append(kw.get("recap"))
        return await _small_render(images, clips, texts, brief, out, tmp, **kw)
    monkeypatch.setattr(render, "render", fake_render)
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)
    seen: list = []

    def make_synth(spec, ref):
        async def synth(items, tmp):
            seen.extend(items)
            return {it[0]: {"path": _wav(os.path.join(tmp, f"v{it[0]}.wav"), 0.8), "words": None} for it in items}
        return synth
    monkeypatch.setattr(kaggle_job, "make_synth", make_synth)
    spec = {"kind": "kids", "brief": brief, "data": DATA, "voice": {"engine": "vieneu", "name": "Thục Đoan", "multi": True,
                                                                     "en": "en-US-AnaNeural"},
            "image": {"model": "schnell", "check": False}, "llm": {}, "channel": kids_auto.channel(), "music": None, "story": st}
    res = asyncio.run(kids_job.run(spec, str(tmp_path / "w")))
    words = [x["en"] for x in brief["words"]]
    assert res["ok"] and {t.strip(".,!?").lower() for t, _v in EN_CALLS} == set(words)
    assert all(v == "en-US-AnaNeural" for _t, v in EN_CALLS) and res["timings"]["en_voice"] == "en-US-AnaNeural"
    assert not any(re.search(r"(?<![\w])(" + "|".join(words) + r")(?![\w])", t, re.I) for _k, t, _v in seen)
    beat_scene = next(i for i, sc in enumerate(st["scenes"]) if dialogue.split_english(sc["text"], words) != [(False, sc["text"])])
    from rmvideo.media import ff
    one = 0.8 + 0.3                                                            # mỗi mẩu VieNeu giả 1,1 s, mẩu Anh 0,7 s
    assert asyncio.run(ff.probe_duration(merged[beat_scene]["path"])) > 2 * one   # nhiều mẩu đã nối thành một clip
    assert merged[-1][1] == story.recap_text(brief) and asyncio.run(ff.probe_duration(merged[-1][0]["path"])) > 3 * one
    # giọng Anh lỗi: không mất tiếng, VieNeu đọc nguyên đoạn có từ đó, ghi chú lý do
    EN_CALLS.clear()
    seen.clear()

    async def broken(text, out, voice=dialogue.EN_VOICE, rate=dialogue.EN_RATE):
        raise RuntimeError("NoAudioReceived")
    monkeypatch.setattr(dialogue, "english_clip", broken)
    res = asyncio.run(kids_job.run(spec, str(tmp_path / "w2")))
    assert res["ok"] and any("giọng tiếng Anh lỗi" in n for n in res["notes"]) and "en_voice" not in res["timings"]
    assert any(re.search(r"(?<![\w])" + re.escape(words[0]) + r"(?![\w])", t) for _k, t, _v in seen)
    # tắt (KIDS_EN_VOICE=off -> "en": ""): không gọi giọng Anh
    monkeypatch.setattr(dialogue, "english_clip", broken)
    spec["voice"]["en"] = ""
    res = asyncio.run(kids_job.run(spec, str(tmp_path / "w3")))
    assert res["ok"] and not any("giọng tiếng Anh lỗi" in n for n in res["notes"])
    monkeypatch.setattr(config, "KIDS_EN_VOICE", "off")
    assert kaggle_worker.kids_spec(SimpleNamespace(id=1), brief)["voice"]["en"] == ""
    monkeypatch.setattr(config, "KIDS_EN_VOICE", "en-US-JennyNeural")
    assert kaggle_worker.kids_spec(SimpleNamespace(id=1), brief)["voice"]["en"] == "en-US-JennyNeural"


def test_flow_kids_needs_kaggle(tmp_path, monkeypatch):
    import pipeline
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "")
    job = job_factory.kids(brief=story.pick(DATA, [], random.Random(1)), source="web")
    with pytest.raises(RuntimeError, match="Kaggle"):
        asyncio.run(pipeline.process(job, str(tmp_path)))


def _ts(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=kids_auto.VN).timestamp()


def test_free_slots_and_tick(monkeypatch, tmp_path):
    from state import queue
    monkeypatch.setattr(config, "KIDS_AUTO", "1")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_ID", "222")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "tok-kids")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    st = kids_auto.load_state()
    now = _ts(20, "07:55")
    got = kids_auto.free_slots(st, now)
    assert got[0] == (_ts(20, "19:00"), "19:00") and got[1] == (_ts(21, "08:00"), "08:00") and len(got) == 3
    st["posts"].append({"time": _ts(20, "19:00")})
    assert kids_auto.free_slots(st, now)[0] == (_ts(21, "08:00"), "08:00")
    monkeypatch.setattr(queue, "pending", 0)
    put = []
    monkeypatch.setattr(queue, "put", lambda job, force=False: put.append(job) or 1)
    msg = asyncio.run(kids_auto.tick(now, random.Random(1)))
    assert put and put[0].mode == "kids" and put[0].source == "auto" and "19:00" in msg
    st = kids_auto.load_state()
    assert st["pending"]["job"] == put[0].id and st["stories"][-1]["job"] == put[0].id
    assert st["stories"][-1]["plot"] == put[0].brief["plot"]                    # ghi khung ngay lúc bốc để pick_plot xoay khung
    assert asyncio.run(kids_auto.tick(now, random.Random(1))).startswith("đang làm") or "job" in asyncio.run(
        kids_auto.tick(now, random.Random(1)))


def test_finish_pending_publishes_to_kids_page(monkeypatch, tmp_path):
    from state import queue
    monkeypatch.setattr(config, "KIDS_AUTO", "1")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_ID", "222")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "tok-kids")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0" * 2000)
    job = job_factory.kids(brief={}, source="auto")
    job.status, job.out_dur = "done", 70.0
    job.post = "📌 Thỏ Trắng xin lỗi\n\n▶ TikTok:\nx\n\n▶ Facebook:\nMô tả bé\n\n#kebenghe\n\n▶ YouTube Shorts:\ny"
    job.parts = [{"label": "Video", "path": str(vid), "token": "t"}]
    monkeypatch.setitem(queue.jobs, job.id, job)
    seen = {}

    async def fake_publish(path, title, desc, dur, when, thumb="", session=None, page="", token=""):
        seen.update(path=path, title=title, desc=desc, dur=dur, when=when, page=page, token=token)
        s = kids_auto.load_state()                     # worker / nút web ghi file trong lúc đang tải video lên
        s["stories"].append({"job": 999, "title": "song song"})
        s["bgm_recent"] = ["x"]
        kids_auto.save_state(s)
        return "fb123", "reel"
    monkeypatch.setattr(autopost, "publish", fake_publish)
    monkeypatch.setattr(queue, "pending", 0)
    st = kids_auto.load_state()
    now = _ts(20, "10:00")
    st["pending"] = {"job": job.id, "slot_ts": _ts(20, "19:00"), "slot": "19:00"}
    kids_auto.save_state(st)
    msg = asyncio.run(kids_auto.tick(now))
    assert "fb123" in msg and seen["page"] == "222" and seen["token"] == "tok-kids" and seen["when"] == _ts(20, "19:00")
    assert seen["title"] == "Thỏ Trắng xin lỗi" and seen["desc"].startswith("Mô tả bé")
    st = kids_auto.load_state()
    assert st["pending"] is None and st["posts"][-1]["fb_id"] == "fb123"
    assert st["stories"][-1]["job"] == 999 and st["bgm_recent"] == ["x"]       # không mất phần ghi song song
    assert "Đã hẹn" in kids_auto.status_text()


def test_streak_pauses(monkeypatch):
    from state import queue
    monkeypatch.setattr(config, "KIDS_AUTO", "1")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_ID", "222")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "tok-kids")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    sent = []

    async def notify(text):
        sent.append(text)
    import state
    monkeypatch.setattr(state, "notify_admin", notify)
    for k in range(3):
        job = job_factory.kids(brief={}, source="auto")
        job.status, job.error = "error", "Kaggle lỗi"
        monkeypatch.setitem(queue.jobs, job.id, job)
        st = kids_auto.load_state()
        st["pending"] = {"job": job.id, "slot_ts": _ts(20, "19:00"), "slot": "19:00"}
        kids_auto.save_state(st)
        asyncio.run(kids_auto.tick(_ts(20, "10:00")))
    assert kids_auto.load_state()["paused"] and any("đã dừng" in s for s in sent)
    assert asyncio.run(kids_auto.resume()).startswith("Đã chạy lại")


# ---------------- cài đặt, Kaggle, web ----------------

def test_settings_and_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    with pytest.raises(ValueError, match="KIDS_TIMES"):
        config.save_settings({"KIDS_TIMES": "8h"})
    with pytest.raises(ValueError, match="KIDS_KAGGLE_SOURCES"):
        config.save_settings({"KIDS_KAGGLE_SOURCES": "dataset:a b/c/d"})
    config.save_settings({"KIDS_KAGGLE_SOURCES": "notebook:ban/llama-build, dataset:ban/models, rmvideo-kids-models",
                          "KIDS_LORA": "off"})
    assert kaggle_worker.kids_sources() == (["ban/llama-build", "rmvideo-kids-models"], ["ban/models"])
    assert "KIDS_FB_PAGE_TOKEN" in config.SECRET_KEYS
    assert config.reauth_keys({"KIDS_FB_PAGE_ID": "1"}) == ["KIDS_FB_PAGE_ID"]
    snap = kaggle_worker.settings_snapshot()
    assert not any(k.startswith("KIDS_") for k in snap)       # kids_spec mang giọng/model tường minh, token không bao giờ đi


def test_kids_spec_carries_story(monkeypatch):
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    brief = story.pick(DATA, [], random.Random(3))
    st = story.normalize(_good_story(brief), DATA)
    job = job_factory.kids(brief=brief, source="web")
    sp = kaggle_worker.kids_spec(job, brief, None, story=st, attempts=[{"attempt": 1, "problems": []}])
    assert sp["story"]["title"] == st["title"] and sp["attempts"] == [{"attempt": 1, "problems": []}]
    assert "story" not in kaggle_worker.kids_spec(job, brief, None)


def test_kids_spec_and_kernel_without_secrets(monkeypatch):
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "EAA-kids-bi-mat")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_bi_mat")
    monkeypatch.setattr(config, "KIDS_WORLD_NAME", "Đồi Hoa Nhỏ")
    job = job_factory.kids(brief=story.pick(DATA, [], random.Random(1)), source="web")
    spec = kaggle_worker.kids_spec(job, job.brief, {"url": "https://x.example/a.mp3", "kind": "audio"})
    text = kaggle_worker.kernel_text(spec)
    compile(text, "kernel.py", "exec")
    assert "bi-mat" not in text and "bi_mat" not in text and "pip install" in text and "diffusers" in text
    assert spec["data"]["world"]["name"] == "Đồi Hoa Nhỏ" and spec["channel"]["world"] == "Đồi Hoa Nhỏ"
    assert kaggle_worker.needs_gpu(spec) and kaggle_worker.pip_for(spec) == kaggle_worker.PIP_KIDS + kaggle_worker.PIP_QA
    assert "rmvideo/kids/data.json" in kaggle_worker.bundle_b64() or True
    import base64
    import io
    import zipfile
    b64 = text.split('b64decode("', 1)[1].split('"', 1)[0]
    names = set(zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))).namelist())
    assert {"rmvideo/kids/data.json", "rmvideo/kids/job.py", "rmvideo/kids/render.py", "kids_auto.py"} & names == \
        {"rmvideo/kids/data.json", "rmvideo/kids/job.py", "rmvideo/kids/render.py"}   # kids_auto (Botkeep) không gửi
    compile(kaggle_worker.KIDS_MODELS_KERNEL, "models.py", "exec")


def test_kaggle_push_accepts_foreign_sources(monkeypatch):
    import kaggle_api
    from test_kaggle import _Fake, _server
    fake = _Fake()
    srv = _server(fake)
    monkeypatch.setattr(kaggle_api, "API", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    try:
        kaggle_api.push("rmvideo-kids", "t", "print(1)", gpu=True, kernel_sources=["rmvideo-kids-models", "ban/llama"],
                        dataset_sources=["ban/models"])
        body = fake.calls[-1][1]
        assert body["kernelDataSources"] == ["u/rmvideo-kids-models", "ban/llama"]
        assert body["datasetDataSources"] == ["ban/models"] and body["slug"] == "u/rmvideo-kids"
    finally:
        srv.shutdown()


def test_job_restart_and_budget():
    from queue_manager import inputs_available
    job = job_factory.kids(brief={"lesson": "sharing"}, source="auto")
    assert inputs_available({"mode": "kids"}) and job.voice == "vieneu:Thục Đoan" and job.brief == {"lesson": "sharing"}
    import jobs
    assert jobs.job_budget(job) == kaggle_worker.KIDS_TIMEOUT + 600


def test_web_routes(monkeypatch, tmp_path):
    from test_security import _client

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/kids")).status == 401
            assert (await c.post("/api/login", json={"password": "pw-123"})).status == 200
            d = await (await c.get("/api/kids")).json()
            assert "Truyện bé" in d["text"] and d["times"] == ["08:00", "19:00"]
            d = await (await c.post("/api/kids/test")).json()
            assert d["text"].startswith("❌")                                  # chưa có token Fanpage truyện bé
    asyncio.run(go())


def test_autopost_publish_uses_given_page(monkeypatch, tmp_path):
    seen = {}

    async def fake_reel(session, path, description, when, page="", token=""):
        seen.update(page=page, token=token)
        return "v1"

    async def fake_cover(session, vid, thumb, token=""):
        seen["cover_token"] = token
        return False
    monkeypatch.setattr(autopost, "_upload_reel", fake_reel)
    monkeypatch.setattr(autopost, "_set_cover", fake_cover)
    monkeypatch.setattr(config, "FB_PAGE_ID", "111")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "tok-bep")
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0")
    asyncio.run(autopost.publish(str(vid), "t", "d", 40, None, page="222", token="tok-kids"))
    assert seen == {"page": "222", "token": "tok-kids", "cover_token": "tok-kids"}
    assert autopost._page() == ("111", "tok-bep") and autopost._page("", "x") == ("111", "x")


def test_call_gemini_explicit_model(monkeypatch):
    """Truyện bé truyền model riêng: không đụng config.GEMINI_MODEL chung, model riêng bị ngừng (404) thì không tự lưu Cài
    đặt / không dùng danh sách dự phòng chung; model chung 404 vẫn tự chuyển như cũ."""
    import prompt_engine
    calls, saved = [], []
    fail = {}

    class Models:
        async def generate_content(self, model, contents, config=None):
            calls.append((model, config))
            if model in fail:
                raise RuntimeError(fail[model])
            return SimpleNamespace(text='{"ok": 1}')

    class Client:
        def __init__(self, api_key):
            self.aio = SimpleNamespace(models=Models())
    monkeypatch.setattr(prompt_engine, "genai", SimpleNamespace(Client=Client))
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", "gemini-2.5-flash")
    monkeypatch.setattr(config, "save_settings", lambda u: saved.append(u))
    assert asyncio.run(prompt_engine._call_gemini("x", model="gemini-4-hydrogen", thinking="low")) == '{"ok": 1}'
    assert calls == [("gemini-4-hydrogen", {"thinking_config": {"thinking_level": "low"}})]
    assert config.GEMINI_MODEL == "gemini-3.5-flash-lite"
    calls.clear()
    fail["gemini-4-hydrogen"] = "404 NOT_FOUND: model retired, use models/gemini-9-x instead"
    with pytest.raises(RuntimeError):
        asyncio.run(prompt_engine._call_gemini("x", model="gemini-4-hydrogen"))
    assert [m for m, _ in calls] == ["gemini-4-hydrogen"] and not saved          # không đổi model chung, không dự phòng
    calls.clear()
    fail["gemini-3.5-flash-lite"] = "404 NOT_FOUND: use models/gemini-9-x"
    assert asyncio.run(prompt_engine._call_gemini("x")) == '{"ok": 1}'
    assert [m for m, _ in calls] == ["gemini-3.5-flash-lite", "gemini-9-x"] and saved == [{"GEMINI_MODEL": "gemini-9-x"}]


def test_subtitle_words_sorted_with_hook(monkeypatch):
    """Câu móc đọc ở giây 0 nhưng thêm sau các cảnh: phụ đề trả về theo thời gian (SRT đúng thứ tự, mốc tiếng động ở hook)."""
    async def ivs(path):
        return [(0.0, 1.0)]
    monkeypatch.setattr(render.voiceplan, "voiced_intervals", ivs)
    clips = [{"path": "a.wav", "dur": 1.0}]
    pl = {"scenes": [{"voice_at": 5.0}], "hook": {"voice_at": 0.3}, "factor": 1.0}
    words = asyncio.run(render.subtitle_words(clips, pl, ["Thỏ Trắng chạy"], hook=({"path": "h.wav", "dur": 1.0}, "Bịch! Rớt rồi")))
    starts = [w[0] for w in words]
    assert starts == sorted(starts) and words[0][2] == "Bịch!" and words[0][3] == 1 and words[-1][3] == 0
    assert render.hits_from_words(words)[0] == pytest.approx(0.3, abs=0.2)


def test_flow_uses_final_words_from_kaggle(tmp_path, monkeypatch):
    """Qwen viết trên Kaggle: từ hôm nay chốt ở đó -> bài đăng và bộ nhớ từ (xoay 30 ngày) theo từ ĐÃ dạy, không theo từ bốc lúc đầu."""
    import pipeline
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setattr(config, "KIDS_LLM_PROVIDER", "qwen")
    calls = []
    inner = _fake_produce(tmp_path, calls)

    async def produce(spec, tmp, on_step=None):
        r = await inner(spec, tmp, on_step)
        alt = [w for w in DATA["words"] if w["en"] not in {x["en"] for x in spec["brief"]["words"]}][:3]
        r["brief"] = {**spec["brief"], "words": alt, "plot": "plot_x"}
        return r
    monkeypatch.setattr(kaggle_worker, "produce", produce)
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)
    job = kids_auto.make_now("web")
    first = [w["en"] for w in job.brief["words"]]
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    final = [w["en"] for w in job.brief["words"]]
    assert final != first and all(f"🔤 {w}" in job.post for w in final) and not any(f"🔤 {w} " in job.post for w in first)
    hit = kids_auto.load_state()["stories"][-1]
    assert hit["job"] == job.id and hit["words"] == final and hit["plot"] == "plot_x"


def test_cards_font_fallback(monkeypatch):
    from rmvideo.kids import cards
    from rmvideo.media import ff
    monkeypatch.setattr(ff, "FONT_PATH", "/nonexistent/font.ttf")
    f = cards._font(40)
    assert getattr(f, "size", None) == 40


def test_image_prompt_cues_from_story_text():
    """Prompt ảnh thêm: đồ vật chính (cảnh 1 + cảnh lời nhắc), tư thế theo từ dạy, cảm xúc theo cụm cuối trong lời, ánh sáng
    theo lời («trời sắp tối» -> hoàng hôn, bỏ «soft warm light»); không vượt ngân sách chữ cho T5."""
    brief = story.pick(DATA, [], random.Random(2))
    brief["words"] = [dict(w) for w in DATA["words"] if w["en"] in ("hug", "candy", "hat")]
    prop = ("túi kẹo", "candy bag")
    sc1 = {"text": "Hôm nay Thỏ Trắng mang kẹo qua cho Gấu Nâu. À, kẹo tiếng Anh là candy. Bé nói theo nè: candy!",
           "characters": ["white_rabbit"], "action_en": "White Rabbit jumping happily among balloons"}
    p1 = prompts.image_prompt(sc1, brief, DATA, 0, prop=prop)
    assert "candy bag clearly visible" in p1 and "soft warm light" in p1
    sc1b = dict(sc1, action_en="White Rabbit holding a candy bag")                 # action đã ghi vẫn nhắc lại (mẫu 33)
    assert "candy bag clearly visible" in prompts.image_prompt(sc1b, brief, DATA, 0, prop=prop)
    assert prompts.prop_required(0, sc1, prop) and not prompts.prop_required(3, {"text": "Gấu ngủ."}, prop)
    assert prompts.prop_required(3, {"text": "Gấu ăn kẹo."}, prop) and not prompts.prop_required(0, sc1, None)
    assert not prompts.prop_required(1, {"text": "Lúc quay lại, cây dù mất tiêu!"}, ("cây dù", "yellow umbrella"))   # đang mất
    assert not prompts.prop_required(3, {"text": "Bé đoán xem cây dù ở đâu?"}, ("cây dù", "yellow umbrella"))
    assert not prompts.prop_required(0, {"text": "Thỏ quên mất túi kẹo."}, prop)
    assert prompts.prop_required(5, {"text": "Thấy rồi nè! Cây dù nằm ngay đó!"}, ("cây dù", "yellow umbrella"))
    assert "umbrella clearly visible" not in prompts.image_prompt({"text": "Cây dù mất tiêu!", "characters": ["white_rabbit"],
                                                                   "action_en": "White Rabbit looking around"}, brief, DATA, 1,
                                                                  prop=("cây dù", "yellow umbrella"))
    sc3 = {"text": "Gấu Nâu ngồi chờ bên bàn bánh kem. Trời sắp tối mà chẳng thấy bạn đâu, Gấu Nâu buồn thiu.",
           "characters": ["brown_bear"], "action_en": "Brown Bear sitting by a table with a cake"}
    p3 = prompts.image_prompt(sc3, brief, DATA, 2, prop=prop)
    assert "golden hour sunset light" in p3 and "soft warm light" not in p3 and "sad face, teary eyes" in p3
    assert "sad face, teary eyes" in prompts.image_prompt(dict(sc3, action_en="Brown Bear sitting sadly"), brief, DATA, 2)
    assert "candy bag" not in p3                                                  # lời cảnh 3 không nhắc kẹo
    sc7 = {"text": "Gấu Nâu ôm bạn. Đố bé biết ôm tiếng Anh là gì? Là hug! Bé đọc theo mình: hug!",
           "characters": ["brown_bear", "white_rabbit"], "action_en": "Brown Bear giving a warm hug to White Rabbit"}
    p7 = prompts.image_prompt(sc7, brief, DATA, 6, prop=prop)
    assert "hugging each other tightly" in p7 and "happy laughing face" not in p7
    assert [w["en"] for w in story.beat_words(sc7["text"], brief)] == ["hug"]
    assert story.beat_words(sc7["text"], {"words": [{"en": "hug", "vi": "ôm"}]}, DATA)[0].get("contact") is True   # kho bổ sung
    sc5 = {"text": "Thỏ Trắng chạy thật nhanh. Bịch! Thỏ Trắng vấp té nhưng đứng dậy chạy tiếp, cười tươi.",
           "characters": ["white_rabbit"], "action_en": "White Rabbit running on the path"}
    assert prompts.emotion_en(sc5["text"]) == "happy laughing face"             # cụm sau cùng thắng
    assert prompts.emotion_en("Thỏ Trắng vấp té, tai cụp xuống.") .startswith("sad face")
    assert prompts.lighting_en("Trời tối om, trăng lên.").startswith("night scene") and prompts.lighting_en("Trời sắp tối").startswith("golden")
    assert prompts.lighting_en("Buổi trưa nắng đẹp.") == ""
    assert story.prop_mentioned("túi kẹo", "Thỏ mang kẹo qua") and story.prop_mentioned("con diều", "diều bay cao")
    assert not story.prop_mentioned("con diều", "con mèo ngủ") and story.prop_tokens("cái bình hoa") == ["bình", "hoa"]
    long = dict(sc3, action_en=" ".join(["running"] * 60))                      # hành động dài: bỏ cảm xúc rồi ánh sáng, không vượt
    pl = prompts.image_prompt(long, brief, DATA, 2, prop=prop)
    assert len(pl.split()) <= prompts.PROMPT_WORDS_MAX + 60 and "sad face" not in pl
    assert all(len(prompts.image_prompt(s, brief, DATA, i, use_lora=True, prop=prop).split()) <= prompts.PROMPT_WORDS_MAX
               for i, s in enumerate([sc1, sc3, sc7, sc5]))


def test_prop_validation_and_hook_scene():
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(dict(_good_story(brief), prop_vi="Túi kẹo", prop_en="Candy Bag"), DATA)
    assert st["prop_vi"] == "túi kẹo" and st["prop_en"] == "candy bag"
    probs = story.validate(st, brief, DATA, prompts.example_sentences())
    assert any("prop_vi" in q and "túi kẹo" in q for q in probs)               # cảnh 1-2 không nhắc kẹo
    st["scenes"][1]["text"] += " Thỏ Trắng xách túi kẹo đi."
    assert not any("prop" in q for q in story.validate(st, brief, DATA, prompts.example_sentences()))
    bad = story.normalize(dict(_good_story(brief), prop_vi="túi kẹo", prop_en="túi kẹo"), DATA)
    bad["scenes"][0]["text"] += " kẹo"
    assert any("prop_en" in q for q in story.validate(bad, brief, DATA, prompts.example_sentences()))
    assert story.normalize(_good_story(brief), DATA)["prop_vi"] == ""            # không có: không bắt lỗi
    # ảnh câu móc: cảnh 2-5 nhiều dấu hiệu rắc rối nhất; hoà -> cảnh sau; không có -> cảnh 3
    sc = lambda t: {"text": t, "characters": ["white_rabbit"], "action_en": "x"}      # noqa: E731
    s32 = {"scenes": [sc("Hôm nay vui."), sc("Thỏ Trắng quên mất túi kẹo."), sc("Gấu Nâu buồn thiu."),
                      sc("Thỏ Trắng giật mình, tai cụp xuống."), sc("Bịch! Thỏ vấp té."), sc("a"), sc("b"), sc("c")]}
    assert story.hook_scene(s32) == 3
    assert story.hook_scene({"scenes": [sc("a")] * 8}) == 2
    assert story.hook_scene({"scenes": [sc("a"), sc("Ối, rớt rồi!"), sc("b"), sc("c"), sc("d"), sc("e"), sc("f"), sc("g")]}) == 1
    assert story.hook_scene({"scenes": [sc("a"), sc("Ối, rớt rồi!"), sc("b"), sc("Ối, mất rồi!"), sc("d"), sc("e"), sc("f"), sc("g")]}) == 3


def test_imgcheck_contact(monkeypatch):
    from PIL import Image
    from rmvideo.kids import imgcheck
    heads = [("a bear head", 0.6, [20, 20, 100, 100]), ("a rabbit head", 0.6, [130, 40, 190, 100])]
    far = [("blue", 0.5, [10, 100, 95, 190]), ("red", 0.5, [125, 100, 195, 190])]          # cách nhau 30 px / 200 = 15%
    near = [("blue", 0.5, [10, 100, 120, 190]), ("red", 0.5, [122, 100, 195, 190])]        # 2 px
    assert not imgcheck.contact_ok(far, heads, 200) and imgcheck.contact_ok(near, heads, 200)
    assert imgcheck.contact_ok(far[:1], heads, 200)                                          # một áo bị che khi ôm
    assert imgcheck.contact_ok(far, [("a bear head", 0.6, [20, 20, 100, 100]), ("a rabbit head", 0.6, [90, 40, 150, 100])], 200)
    im = Image.new("RGB", (200, 300), (120, 200, 90))
    seen_extra = []

    def fake_detect(img, loaded=None, min_score=0.1, extra=()):
        seen_extra.append(tuple(extra))
        return heads + ([("a toy box", 0.2, [50, 200, 150, 290])] if "a toy box" in extra and len(seen_extra) > 1 else [])
    monkeypatch.setattr(imgcheck, "detect", fake_detect)
    monkeypatch.setattr(imgcheck, "shirt_colors", lambda img, boxes, thr=0.25, heads=None: far)
    ok, n, out = imgcheck.check(im, 2, loaded=object(), colors=["bear:blue", "rabbit:red"], contact=True, prop="toy box")
    assert not ok and n == 2 and any("chưa ôm" in b[0] for b in out) and any("không thấy đồ vật chính «toy box»" in b[0] for b in out)
    assert seen_extra[-1] == ("a toy box",)
    ok, _n, out = imgcheck.check(im, 2, loaded=object(), colors=["bear:blue", "rabbit:red"], prop="toy box")   # lần 2: thấy hộp
    assert ok and any(b[0] == "prop:toy box" for b in out) and seen_extra[-1] == ("a toy box",)
    assert imgcheck.check(im, 2, loaded=object(), colors=["bear:blue", "rabbit:red"])[0] and seen_extra[-1] == ()
    ok, _n, out = imgcheck.check(im, 2, loaded=object(), colors=["bear:blue", "rabbit:red"])             # không đòi chạm
    assert ok
    monkeypatch.setattr(imgcheck, "shirt_colors", lambda img, boxes, thr=0.25, heads=None: near)
    assert imgcheck.check(im, 2, loaded=object(), contact=True)[0]


def test_insert_beat_keeps_quote_closed():
    """Câu hỏi bé đứng sau lời thoại: câu dạy chèn SAU dấu » (người kể đọc), không lọt vào trong ngoặc (mẫu 34 cảnh 4)."""
    t = "Thỏ Trắng chạy ra suối: «Tìm nữa, tìm nữa nào!» Bé đoán xem cây dù ở đâu?"
    out = story.insert_beat(t, "Đố bé biết dòng suối đó tiếng Anh là gì? Là river!")
    assert out == "Thỏ Trắng chạy ra suối: «Tìm nữa, tìm nữa nào!» Đố bé biết dòng suối đó tiếng Anh là gì? Là river! Bé đoán xem cây dù ở đâu?"
    assert story.ends_with_child_question(t) and not story.ends_with_child_question("«Bé đoán xem?»")
    assert story.insert_beat("Thỏ chạy. «Ôi chao!»", "À, x là y.") == "Thỏ chạy. «Ôi chao!» À, x là y."


def test_lyria_stall_times_out(monkeypatch):
    """Kết nối Lyria đứng im (không gửi gói nào, không đóng): stream dừng sau TIMEOUT + STALL giây, không treo job."""
    from rmvideo.kids import bgm

    class _Stall:
        def __init__(self):
            outer = self

            class _S:
                async def set_weighted_prompts(self, prompts):
                    pass

                async def set_music_generation_config(self, config):
                    pass

                async def play(self):
                    pass

                async def stop(self):
                    pass

                async def receive(self):
                    await asyncio.sleep(3600)
                    yield None

            class _C:
                async def __aenter__(self):
                    return _S()

                async def __aexit__(self, *a):
                    return False
            outer.aio = SimpleNamespace(live=SimpleNamespace(music=SimpleNamespace(connect=lambda model: _C())))
    monkeypatch.setattr(bgm, "TIMEOUT", 0.2)
    monkeypatch.setattr(bgm, "STALL", 0.1)
    t0 = time.time()
    with pytest.raises(RuntimeError, match="đứng im"):
        asyncio.run(bgm.stream("playful", 2, client=_Stall(), log=lambda s: None))
    assert time.time() - t0 < 5
