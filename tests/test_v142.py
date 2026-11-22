"""1.4.2 (phản hồi 22/11, job 8 truyện bé + job 9 đồ chua): hook nêu tên món, điểm danh nguyên liệu cuối video, gộp câu
cụt, chia dòng phụ đề, tắt phụ đề, ảnh truyện bé không vẽ thứ không vẽ được."""
import config
import prompt_engine
from rmvideo.app import job_factory
from rmvideo.app.flow import hooks, style_b
from rmvideo.kids import prompts

DISH, TITLE = "Đồ chua cà rốt củ cải", "Làm đồ chua thơm ngon đơn giản tại nhà"


def test_hook_names_dish_by_head_words():
    """Job 9: 8/8 câu «Đồ chua…» bị loại «không nêu tên món» vì chỉ so cả tên món dài / nguyên liệu."""
    assert "đồ chua" in hooks.dish_keys(DISH, TITLE) and hooks.dish_keys("", TITLE) == ["đồ chua"]
    assert hooks.names_dish("Làm đồ chua bị hăng là do chưa biết mẹo trộn muối này.", DISH, TITLE)
    assert hooks.names_dish("Trộn củ cải cà rốt với muối kiểu này hết hăng.", DISH, TITLE)        # nguyên liệu chính
    assert not hooks.names_dish("Ngon lắm luôn nè, ai cũng mê!", DISH, TITLE)
    assert "không nêu tên món" not in hooks.problems("Đồ chua không giòn là do bỏ qua bước trộn muối này.", 11,
                                                     "trộn với muối", "", DISH, TITLE, 7)
    assert hooks.dish_keys("Cá kho tộ", "Cách làm cá kho tộ ngon") == ["cá kho tộ", "cá kho"]
    assert hooks.FALLBACK_SECS > 2.0                  # chưa chọn được câu hook trước: đoạn mở đầu vẫn đủ chỗ một câu


def _seg(i, start, slot, text, orig="x", ost="new"):
    return {"i": i, "start": start, "slot": slot, "span": slot - 0.1, "text": text, "orig": orig, "ost": ost, "copy": 0.1}


def test_join_fragments_reads_one_sentence():
    segs = [_seg(0, 0.0, 2.1, "Đồ chua giòn rụm để lâu hổng sợ mềm.", orig=""),
            _seg(1, 76.3, 3.5, "Công dụng của muối là giúp cho cà rốt và củ cải trắng"),
            _seg(2, 79.8, 3.4, "ra bớt phần nhựa đắng đi, rồi làm rau củ"),
            _seg(3, 83.2, 4.1, "trở nên giòn rụm, thêm nữa là giúp cho"),
            _seg(4, 87.3, 3.1, "cà rốt củ cải hết hẳn mùi hăng."),
            _seg(5, 90.4, 4.3, "Giờ mình để đó cho rau củ nghỉ ngơi khoảng nửa tiếng."),
            _seg(6, 94.7, 3.0, "Trong lúc chờ thì mình cho", orig="Tóm lại: a, b."),
            _seg(7, 97.7, 3.0, "nồi lên bếp.")]
    out = prompt_engine.join_fragments({"segments": segs, "script": ""})["segments"]
    assert [s["i"] for s in out] == list(range(len(out)))
    assert out[0]["text"].startswith("Đồ chua")                                   # câu hook: không gộp
    assert out[1]["text"].endswith("hết hẳn mùi hăng.") and out[1]["start"] == 76.3
    assert out[1]["slot"] == round(87.3 + 3.1 - 76.3, 2)
    assert out[3]["orig"].startswith("Tóm lại:") and out[4]["text"] == "nồi lên bếp."   # khúc tóm tắt: không gộp
    # lời không dùng dấu kết câu (bản giả / kiểu viết khác): không đoán câu cụt
    raw = [_seg(k, k * 3.0, 3.0, f"lời {k}") for k in range(6)]
    assert len(prompt_engine.join_fragments({"segments": raw, "script": ""})["segments"]) == 6


def test_join_fragments_splits_long_runs_at_commas():
    segs = [_seg(k, k * 4.0, 4.0, f"vế {k} của câu" + ("," if k % 2 else "")) for k in range(8)]
    segs[-1]["text"] += "."
    full = [_seg(8 + k, 32.0 + k * 3, 3.0, f"Câu trọn số {k}.") for k in range(9)]    # phần lớn lời có dấu kết câu
    out = prompt_engine.join_fragments({"segments": segs + full, "script": ""})["segments"]
    assert len(out) < len(segs) + len(full)
    assert all(s["slot"] <= prompt_engine.JOIN_MAX + 1e-6 for s in out)
    assert all(s["text"].rstrip().endswith((",", ".")) for s in out)                 # cắt sau dấu phẩy, không giữa vế


def test_merge_sentences_uses_lowercase_starts_without_punctuation():
    """Whisper trên Kaggle: không dấu câu nhưng viết hoa đầu câu — đoạn bắt đầu chữ thường là câu đang nói dở."""
    segs = [(133.0, 135.5, "Rồi xong lúc này mình sẽ đem đi"), (135.7, 139.5, "rửa sạch lại khoảng 3 nước nha"),
            (142.6, 146.0, "Xong mình để vô rổ cho ráo"), (147.0, 149.0, "Rồi mình mới pha giấm")]
    out = prompt_engine.merge_sentences(segs, 200)
    assert out[0][2] == "Rồi xong lúc này mình sẽ đem đi rửa sạch lại khoảng 3 nước nha" and len(out) == 3


def test_recap_step_keeps_whole_ingredient_names():
    assert style_b._short_step("Rửa sạch củ cải và cà rốt khoảng 3 nước", "prep") == "rửa sạch củ cải và cà rốt"
    assert style_b._short_step("Cho củ cải và cà rốt vào hũ nhựa rồi đổ nước giấm", "cook") == \
        "cho củ cải và cà rốt vào hũ"
    assert style_b.RECAP_LEN >= 1.0


def test_subs_settings(monkeypatch):
    monkeypatch.setattr(config, "SUBS", "0", raising=False)
    assert job_factory.remake(mode="cook", voice="x", source="telegram", url="u").subs is False
    assert job_factory.remake(mode="cook", voice="x", source="web", url="u", subs=True).subs is True   # web: theo ô chọn
    assert job_factory.auto(url="u", title="t", duration=30, mode="cook", voice="x", chat_id=0).subs is False
    monkeypatch.setattr(config, "SUBS", "1", raising=False)
    assert job_factory.remake(mode="cook", voice="x", source="telegram", url="u").subs is True


def test_kids_spec_subs_flag(monkeypatch):
    import kaggle_worker
    from rmvideo.kids import story
    brief = story.pick(story.load(), [], __import__("random").Random(1))
    job = job_factory.kids(brief=brief, source="web")
    monkeypatch.setattr(config, "KIDS_SUBS", "0", raising=False)
    assert kaggle_worker.kids_spec(job, brief)["subs"] is False
    monkeypatch.setattr(config, "KIDS_SUBS", "1", raising=False)
    assert kaggle_worker.kids_spec(job, brief)["subs"] is True


def test_image_prompt_drops_undrawable_things():
    """Job 8 cảnh 2: «a huge black shadow falls over the flowers» -> khối tròn đen lơ lửng sau lưng Thỏ."""
    assert prompts.visible_action("White Rabbit startled as a huge black shadow falls over the flower bed") == \
        "White Rabbit startled"
    assert prompts.visible_action("Brown Bear hugging White Rabbit tightly") == "Brown Bear hugging White Rabbit tightly"
    from rmvideo.kids import story
    data = story.load()
    brief = {"main": "white_rabbit", "setting": data["settings"][0]["id"], "words": [], "lesson": data["lessons"][0]["id"]}
    p = prompts.image_prompt({"text": "Thỏ Trắng giật mình.", "characters": ["white_rabbit"],
                              "action_en": "A huge black shadow covers the garden"}, brief, data, 1)
    assert "shadow" not in p.split(". Setting")[0] and "toddler" not in p
    p2 = prompts.image_prompt({"text": "Hai bạn ôm nhau.", "characters": ["white_rabbit", "brown_bear"],
                               "action_en": "White Rabbit hugging Brown Bear"}, brief, data, 6)
    assert "White Rabbit on the left, Brown Bear on the right" in p2 and "toddler" not in p2


def test_release_notes_join_wrapped_lines(tmp_path):
    """Tin báo phiên bản: mục xuống dòng được nối lại (trước chỉ lấy dòng đầu: «Phần đầu chỉ»), mục cha «…:» lấy mục con
    đầu tiên."""
    from rmvideo.app import release
    p = tmp_path / "CHANGELOG.md"
    p.write_text("## 9.9.9 — thử (1/1/2027)\n\n- **Bếp**: điểm danh dời về cuối video,\n  ngay trước tóm tắt. Câu sau.\n"
                 "- Ảnh:\n  - bỏ bóng khỏi prompt,\n    tả phản ứng;\n  - bỏ chữ toddler.\n- `SUBS` mới.\n\nĐoạn khác.\n",
                 encoding="utf-8")
    title, lines = release.notes("9.9.9", str(p))
    assert title == "thử (1/1/2027)"
    assert lines == ["Bếp: điểm danh dời về cuối video, ngay trước tóm tắt.", "Ảnh: bỏ bóng khỏi prompt, tả phản ứng;",
                     "SUBS mới."]
