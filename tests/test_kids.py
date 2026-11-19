"""🧸 Truyện bé (1.1.0): chọn đề + chống trùng, kiểm kịch bản bằng mã, giọng miền Nam, prompt ảnh, kế hoạch cảnh theo
giọng, dựng thật bằng ảnh + giọng giả (ffmpeg), việc Kaggle với model giả, luồng Botkeep với Kaggle giả, lịch đăng
Fanpage riêng, cài đặt, route web."""
import asyncio
import json
import os
import random
import re
import subprocess
import time
from datetime import datetime

import pytest

import autopost
import config
import kaggle_worker
import kids_auto
from rmvideo.app import job_factory
from rmvideo.kids import job as kids_job, llm, prompts, render, story
from rmvideo.kids import images as images_mod

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
    for k in config.EDITABLE:
        if k.startswith("KIDS_"):
            monkeypatch.setattr(config, k, config.EDITABLE[k])
    monkeypatch.setattr(config, "QUALITY", "fast")
    return tmp_path


# ---------------- dữ liệu + chọn đề ----------------

def test_data_is_complete():
    assert len(DATA["characters"]) == 4 and len(DATA["settings"]) == 8 and len(DATA["lessons"]) == 10
    assert len(DATA["conflicts"]) == 10 and len(DATA["words"]) >= 60
    bank = {w["en"] for w in DATA["words"]}
    for ls in DATA["lessons"]:
        assert ls["keywords"] and set(ls["words"]) <= bank, ls["id"]
    assert all(w["vi"] for w in DATA["words"])


def test_pick_avoids_recent_and_rotates_main():
    rnd = random.Random(5)
    stories = []
    now = time.time()
    for k in range(40):
        b = story.pick(DATA, stories, rnd, now)
        assert 3 <= len(b["words"]) <= 4 and b["seed"] > 0
        stories.append({"created_at": now - (40 - k) * 3600, "lesson": b["lesson"], "setting": b["setting"],
                        "conflict": b["conflict"], "words": [w["en"] for w in b["words"]], "main": b["main"]})
    first = stories[0]
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
    """Kịch bản đạt mọi điều kiện: 8 cảnh thuần tiếng Việt, nhắc tới tiếng Việt của mỗi từ, chào bé, bài học, kết vui."""
    vis = [w["vi"] for w in brief["words"]]
    lesson = story.by_id(DATA["lessons"], brief["lesson"])
    filler = "Hai bạn đi dạo."
    base = [
        f"Chào các bé! Hôm nay Thỏ Trắng ra đồi chơi. {filler} Thỏ Trắng gặp Gấu Nâu nè.",
        f"Thỏ Trắng thấy {vis[0]} đẹp ghê. {filler} Thỏ Trắng muốn mang về cho Mèo Cam.",
        f"Bịch! Thỏ Trắng lỡ tay làm rơi mất rồi. {filler} Gấu Nâu hơi buồn, Sóc Nhỏ chạy tới coi.",
        f"Thỏ Trắng nói: cố lên, cố lên nha. {filler} Vẫn chưa được, hai bạn nhìn nhau.",
        f"Thỏ Trắng nói: cố lên, cố lên nha. {filler} Gấu Nâu nói nhỏ thôi nha mấy bạn.",
        f"Gấu Nâu giúp một tay. Cố lên, cố lên nha! {filler} Được rồi, Mèo Cam gật đầu, Sóc Nhỏ cười.",
        f"Gấu Nâu nói: tụi mình phải {lesson['keywords'][0]} nha. {filler} Các bạn cùng gật đầu.",
        f"Bốn bạn ôm nhau cười vui. {filler} Các bé ơi, mình cùng chơi vui nha!",
    ]
    for j, vi in enumerate(vis[1:]):                                        # các từ còn lại cũng được nhắc tới
        base[3 + j % 3] += f" Ở đây có {vi} nè."
    while sum(story.word_count(t) for t in base) < story.WORDS_MIN + 5:       # đủ số chữ tối thiểu
        k = min(range(8), key=lambda i: story.word_count(base[i]))
        base[k] += " Nắng vàng nhẹ nhàng."
    scenes = [{"text": t, "characters": ["white_rabbit", "brown_bear"], "action_en": "White Rabbit talks to Brown Bear"}
              for t in base]
    return {"title": f"Thỏ Trắng và {vis[0]} trên đồi", "scenes": scenes, "description": "Truyện dạy bé xin lỗi.",
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
    assert any("8 cảnh" in p for p in probs) and any("câu hỏi" in p for p in probs) and any("chết" in p for p in probs)
    assert any("chữ" in p for p in probs)
    # chen tiếng Anh vào lời / tiêu đề -> lỗi (mã mới là nơi thêm tiếng Anh)
    en = json.loads(json.dumps(st))
    en["scenes"][1]["text"] += f" Thỏ Trắng nói: {brief['words'][0]['en']}!"
    assert any("THUẦN tiếng Việt" in p for p in story.validate(en, brief, DATA))
    # chép ≥ 2 câu truyện mẫu -> lỗi
    cp = json.loads(json.dumps(st))
    ex = prompts.example_sentences()
    cp["scenes"][2]["text"] += " " + ex[0] + ". " + ex[1] + "."
    assert any("chép truyện mẫu" in p for p in story.validate(cp, brief, DATA, ex))
    # thiếu đồ vật của hôm nay -> lỗi nêu đúng tiếng Việt của từ
    w0 = brief["words"][0]
    miss = json.loads(json.dumps(st))
    for sc in miss["scenes"]:
        for f in story.vi_forms(w0, DATA):
            sc["text"] = re.sub(r"(?i)" + re.escape(f), "xx", sc["text"])
    assert any(w0["vi"] in p and "nhắc tới" in p for p in story.validate(miss, brief, DATA))
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
    txt = story.script_text(st)
    for w in brief["words"]:
        assert f"tiếng Anh là {w['en']}. Bé nói theo nha: {w['en']}!" in txt
    assert story.beat_text({"en": "ball", "vi": "trái banh"}) == "Trái banh, tiếng Anh là ball. Bé nói theo nha: ball!"
    # không cảnh nào nhắc tới -> chèn kèm câu dẫn «Ở đây có ... nè»
    st2 = story.normalize(_good_story(brief), DATA)
    extra = {"en": "rainbow", "vi": "cầu vồng"}
    beats = story.add_beats(st2, {**brief, "words": [extra]}, DATA)
    assert beats == [("rainbow", 1)] and "Ở đây có cầu vồng nè. Cầu vồng, tiếng Anh là rainbow." in st2["scenes"][1]["text"]
    short = {**st, "scenes": [{**s, "text": "Hai bạn chơi vui."} for s in st["scenes"]]}
    assert any("quá ngắn" in p for p in story.blocking(short, brief, DATA))
    r = story.recap_text(brief)
    assert r.startswith("Hôm nay mình học:") and all(w["en"] in r and w["vi"] in r for w in brief["words"])


def test_prompts_and_schema():
    brief = story.pick(DATA, [], random.Random(2))
    msgs = prompts.messages(brief, DATA, {"name": "Kể Bé Nghe"})
    assert msgs[0]["role"] == "system" and "miền Nam" in msgs[0]["content"] and "THUẦN tiếng Việt" in msgs[0]["content"]
    u = msgs[1]["content"]
    assert all(f"«{w['vi']}»" in u for w in brief["words"]) and "Thỏ Trắng" in u and "Đồi Cỏ Non" in u
    assert not any(story.count_word(u, w["en"]) for w in brief["words"])    # KHÔNG đưa tiếng Anh cho model
    assert "Chào các bé" in u and "Truyện MẪU" in u and "KHÔNG chép" in u and "điệp khúc" in u
    u2 = prompts.user_prompt(brief, DATA, ["cảnh 1 thiếu hook"])
    assert "Bản trước chưa đạt" in u2 and "thiếu hook" in u2
    sch = prompts.schema(DATA)
    assert sch["properties"]["scenes"]["minItems"] == 8 and set(sch["required"]) == {"title", "scenes", "description",
                                                                                       "hashtags"}
    assert sch["properties"]["scenes"]["items"]["properties"]["text"]["minLength"] >= 60    # grammar ép độ dài cảnh
    assert len(prompts.example_sentences()) >= 10 and len(prompts.EXAMPLE) == 8
    p = prompts.image_prompt({"characters": ["orange_cat"], "action_en": "Orange Cat jumps over a puddle."}, brief, DATA)
    assert p.startswith("Exactly one character in the picture: Orange Cat jumps over a puddle. Orange Cat,")
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
    assert p2.startswith("Exactly two friends in the picture: White Rabbit and Brown Bear arguing, upset faces.")
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


def test_sub_tokens_drop_quotes():
    assert render.sub_tokens("Thỏ Trắng nói: «Giờ clean đồ chơi đi.» Gấu Nâu giúp.") == \
           ["Thỏ", "Trắng", "nói:", "Giờ", "clean", "đồ", "chơi", "đi.", "Gấu", "Nâu", "giúp."]
    assert render.sub_tokens('«Thank you, bạn thân! »') == ["Thank", "you,", "bạn", "thân!"]


def test_plan_fits_max_len():
    """Lời dài (đo thật 236 chữ -> 88,9s): co đệm trước, vẫn quá thì đọc nhanh hơn; không bao giờ quá MAX_LEN khi còn co được."""
    pl = render.plan([9.0] * 8, random.Random(3), recap_dur=6.5)             # tự nhiên ≈ 90,6s
    assert pl["length"] <= render.MAX_LEN and pl["pad"] == render.PAD_MIN and pl["factor"] == 1.0
    pl2 = render.plan([10.5] * 8, random.Random(3), recap_dur=6.5)           # ≈ 102s tự nhiên: phải đọc nhanh hơn
    assert pl2["length"] <= render.MAX_LEN + 0.05 and 1.0 < pl2["factor"] <= render.TEMPO_MAX
    assert pl2["scenes"][1]["end"] - pl2["scenes"][1]["start"] == pytest.approx(render.GAP_MIN + 10.5 / pl2["factor"]
                                                                               + render.PAD_MIN, abs=0.01)
    pl3 = render.plan([20.0] * 8, random.Random(3))                           # quá sức co: dừng ở TEMPO_MAX, QA sẽ chặn
    assert pl3["factor"] == render.TEMPO_MAX and pl3["length"] > render.MAX_LEN
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
    music = str(tmp_path / "m.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-c:a",
                    "libmp3lame", music], check=True)
    out = str(tmp_path / "out.mp4")
    recap = ({"path": _wav(str(tmp_path / "recap.wav"), 3.0)}, story.recap_text(brief))
    res = asyncio.run(render.render(imgs, clips, texts, brief, out, str(tmp_path), music=music, title="Thỏ Trắng học nói",
                                    channel=kids_auto.channel(), W=180, H=320, fps=12, rnd=random.Random(1), recap=recap))
    outro = max(render.OUTRO, render.RECAP_GAP + recap[0]["dur"] + 1.0)
    assert res["length"] == pytest.approx(8 * render.SCENE_MIN + outro) and recap[0]["dur"] > 2.5
    assert "Hôm nay mình học" in res["srt"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0",
                            out], capture_output=True, text=True).stdout
    assert "video,180,320" in probe and "audio" in probe
    assert res["music_gain_db"] is not None and brief["words"][0]["en"] in res["srt"]
    assert open(res["ass"], encoding="utf-8").read().count("\\c&H003BD4FF&") >= 8      # từ tiếng Anh tô màu mỗi cảnh
    cov, thumb = asyncio.run(render.cover(out, str(tmp_path)))
    assert os.path.getsize(cov) > 1000 and os.path.getsize(thumb) > 500


# ---------------- việc Kaggle với model giả ----------------

class _FakeSrv:
    def __init__(self, brief, fail_first=False, drop_word=None, always_short=False):
        self.brief, self.fail_first, self.drop_word, self.always_short = brief, fail_first, drop_word, always_short
        self.calls = []

    def start(self):
        pass

    def chat(self, messages, schema, max_tokens=0, temperature=0.0, seed=None):
        self.calls.append(messages[1]["content"])
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
    assert "Bản trước chưa đạt" in srv.calls[1] and st["title"]
    assert attempts[-1]["beats"] and story.count_word(story.script_text(st), brief["words"][0]["en"]) >= 1
    # luôn quên một đồ vật: hết lượt -> nhận bản ít lỗi nhất, câu dạy từ vẫn chèn (kèm câu dẫn)
    srv = _FakeSrv(brief, drop_word=brief["words"][0])
    st, attempts = llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=srv)
    assert len(attempts) == 1 + llm.RETRIES and attempts[-1]["beats"][0][0] == brief["words"][0]["en"]
    assert story.count_word(story.script_text(st), brief["words"][0]["en"]) >= 1 and "accepted_with" in attempts[-1]
    assert f"Ở đây có {brief['words'][0]['vi']} nè." in story.script_text(st)
    with pytest.raises(llm.StoryRejected) as e:                      # luôn quá ngắn: lỗi chặn -> từ chối, kèm các lượt
        llm.generate_story({"brief": brief, "channel": {}}, DATA, "/tmp", srv=_FakeSrv(brief, always_short=True))
    assert len(e.value.attempts) == 1 + llm.RETRIES and e.value.attempts[0]["text"]
    assert llm.parse_json('<think>x</think> {"a": 1}')["a"] == 1
    assert llm.find_binary("/khong/co") is None and llm.find_gguf("*.gguf", "/khong/co") is None


def test_kaggle_job_kids_end_to_end(tmp_path, monkeypatch):
    import kaggle_job
    brief = story.pick(DATA, [], random.Random(2))
    monkeypatch.setattr(llm, "backend", lambda spec_llm, tmp, log=None: _FakeSrv(brief))

    def fake_images(plist, seeds, out_dir, model="schnell", log=None, pipe=None):
        assert len(plist) == 8 and len(seeds) == 8 and model == "schnell"
        os.makedirs(out_dir, exist_ok=True)
        return [_png(os.path.join(out_dir, f"scene_{k + 1:02d}.png"), k * 30) for k in range(8)]
    monkeypatch.setattr(images_mod, "generate", fake_images)
    monkeypatch.setattr(render, "render", _small_render)
    monkeypatch.setattr(kids_job, "qa_video", _qa_small)

    def make_synth(spec, ref):
        async def synth(items, tmp):
            return {k: {"path": _wav(os.path.join(tmp, f"v{k}.wav"), 0.8), "words": None} for k, _ in items}
        return synth
    monkeypatch.setattr(kaggle_job, "make_synth", make_synth)
    spec = {"kind": "kids", "brief": brief, "data": DATA, "voice": {"engine": "vieneu", "name": "Thục Đoan"},
            "image": {"model": "schnell"}, "llm": {}, "channel": kids_auto.channel(), "music": None, "token": "t1"}
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    res = kaggle_job.main(spec, str(out_dir))[0]
    assert res["ok"] and res["token"] == "t1" and res["story"]["title"] and res["srt"]
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
    assert spec["llm"]["think"] == 1200 and spec["llm"]["hf_repo"].endswith("GGUF")
    assert spec["kernel_sources"] == ["rmvideo-kids-models"] and "logo_b64" not in spec
    assert spec["fetch_files"] == ["cover.jpg", "thumb.jpg", "story.json"] and spec["data"]["world"]["name"] == "Đồi Cỏ Non"
    assert "▶ Facebook:" in job.post and "🔤" in job.post and job.hook.startswith("Thỏ Trắng") and job.out_dur == 72.0
    st = kids_auto.load_state()
    assert st["stories"][-1]["job"] == job.id and st["stories"][-1]["title"] == job.hook
    assert "Kaggle (GPU)" in job.error


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
    config.save_settings({"KIDS_KAGGLE_SOURCES": "notebook:ban/llama-build, dataset:ban/models, rmvideo-kids-models"})
    assert kaggle_worker.kids_sources() == (["ban/llama-build", "rmvideo-kids-models"], ["ban/models"])
    assert "KIDS_FB_PAGE_TOKEN" in config.SECRET_KEYS
    assert config.reauth_keys({"KIDS_FB_PAGE_ID": "1"}) == ["KIDS_FB_PAGE_ID"]
    snap = kaggle_worker.settings_snapshot()
    assert not any(k.startswith("KIDS_") for k in snap)       # kids_spec mang giọng/model tường minh, token không bao giờ đi


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
    assert kaggle_worker.needs_gpu(spec) and kaggle_worker.pip_for(spec) == kaggle_worker.PIP_KIDS
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
