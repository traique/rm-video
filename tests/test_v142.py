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


def test_kaggle_push_retries_transient_errors(monkeypatch):
    """22/11 tối (job 12, 13): SaveKernel «Connection aborted» rồi «499» — bot bỏ job ở lần gửi đầu. Giờ gửi lại; Kaggle
    đã nhận (phiên đang chờ máy) thì không gửi lại (khỏi tốn gấp đôi giờ GPU)."""
    import kaggle_api
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    monkeypatch.setattr(kaggle_api, "user", lambda: "u")
    calls = []

    def flaky(method, body, service=kaggle_api.KERNELS, timeout=120):
        calls.append(method)
        if method == "SaveKernel" and calls.count("SaveKernel") <= 2:
            raise kaggle_api.KaggleError("Kaggle lỗi 499 ở SaveKernel: {}" if len(calls) > 1
                                         else "Không gọi được Kaggle (SaveKernel): ('Connection aborted.')")
        if method == "GetKernelSessionStatus":
            return {"status": "COMPLETE"}                           # phiên cũ đã xong: Kaggle chưa nhận bản mới
        return {"ref": "/code/u/rmvideo-kids", "versionNumber": 3}
    monkeypatch.setattr(kaggle_api, "_call", flaky)
    kaggle_api.push("rmvideo-kids", "x", "print(1)")
    assert calls.count("SaveKernel") == 3 and calls.count("GetKernelSessionStatus") == 2

    calls.clear()

    def accepted(method, body, service=kaggle_api.KERNELS, timeout=120):
        calls.append(method)
        if method == "SaveKernel":
            raise kaggle_api.KaggleError("Không gọi được Kaggle (SaveKernel): RemoteDisconnected")
        return {}                                                    # không có trường status = QUEUED: đã nhận
    monkeypatch.setattr(kaggle_api, "_call", accepted)
    assert kaggle_api.push("rmvideo-kids", "x", "print(1)") == {} and calls.count("SaveKernel") == 1

    def bad(method, body, service=kaggle_api.KERNELS, timeout=120):
        raise kaggle_api.KaggleError("Kaggle lỗi 400 ở SaveKernel: bad")                # lỗi thật: không thử lại
    monkeypatch.setattr(kaggle_api, "_call", bad)
    import pytest
    with pytest.raises(kaggle_api.KaggleError):
        kaggle_api.push("rmvideo-kids", "x", "print(1)")
    assert kaggle_api.transient(kaggle_api.KaggleError("Kaggle lỗi 503 ở GetKernelSessionStatus: {}"))
    assert not kaggle_api.transient(kaggle_api.KaggleError("Kaggle lỗi 404 ở GetKernelSessionStatus: {}"))


def test_kaggle_read_calls_retry(monkeypatch):
    import kaggle_api
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    n = {"k": 0}

    def flaky(method, body, service=kaggle_api.KERNELS, timeout=120):
        n["k"] += 1
        if n["k"] < 3:
            raise kaggle_api.KaggleError("Kaggle lỗi 502 ở GetKernelSessionStatus: {}")
        return {"status": "RUNNING"}
    monkeypatch.setattr(kaggle_api, "_call", flaky)
    assert kaggle_api.call("GetKernelSessionStatus", {}) == {"status": "RUNNING"} and n["k"] == 3


def test_kids_model_cooldown(monkeypatch):
    """Gemini 4 quá tải / quá giờ: nhớ 15 phút, lượt sau đổi ngay sang model kia (job 12-13: ~4 phút chờ mỗi truyện)."""
    import asyncio
    import prompt_engine
    from rmvideo.kids import writer
    monkeypatch.setattr(prompt_engine, "_BUSY", {})                 # 1.5.2: sổ chung prompt_engine (trước là writer._DOWN)
    assert writer.err_text(asyncio.TimeoutError()) == "TimeoutError" and writer.model_busy(asyncio.TimeoutError())
    asked = []

    def ask(self, model, prompt, schema, role):
        asked.append(model)
        if model == "g4":
            raise asyncio.TimeoutError()
        return "{}"
    monkeypatch.setattr(writer.GeminiBackend, "_ask", ask)
    b = writer.GeminiBackend("g4", log=lambda s: None, revise_model="lite")
    assert b.chat([{"content": "x"}], {}) == "{}" and asked == ["g4", "lite"] and writer.is_down("g4")
    asked.clear()
    b.chat([{"content": "x"}], {})
    assert asked == ["lite"]                                        # model vừa lỗi: không chờ nó nữa


def test_pitch_things_reach_writer_prompt():
    import json
    from rmvideo.kids import pitch, story
    raw = json.dumps({"pitches": [{"kind": "bí ẩn", "title": "Gấu Nâu và trái táo lăn", "premise": "Trái táo lăn mất, "
                                   "Gấu Nâu đuổi theo tới bụi cây thì thấy Sóc Nhỏ.", "mood": "curious",
                                   "things": ["trái táo", "cái mền", "trái chuối"]}]})
    p = pitch._parse(raw)[0]
    assert p["things"] == ["trái táo", "cái mền", "trái chuối"]
    data = story.load()
    brief = story.pick(data, [], __import__("random").Random(3))
    brief["pitch"] = p
    u = prompts.user_prompt(brief, data)
    assert "Ý này dùng: «trái táo», «cái mền», «trái chuối»" in u
    assert '"things"' in pitch.PITCH_PROMPT and "ĐÚNG 3 thứ" in pitch.PITCH_PROMPT


def test_tail_slot_moves_off_a_sentence():
    """Chỗ chèn khối cuối rơi giữa câu (R8): dời tới khoảng lặng sau câu đó, không bỏ điểm danh + tóm tắt."""
    from rmvideo.media import edl
    pieces = [edl.Piece(0.0, 22.0), edl.Piece(22.0, 30.0, push=0.1)]
    segs = [(18.0, 20.5, "câu một"), (21.0, 25.0, "câu hai vắt qua chỗ chèn"), (25.5, 29.0, "câu ba")]
    assert style_b._cuts_sentence(list(pieces), 1, segs)
    out, ins = style_b._tail_slot(pieces, segs, 22.0, 1)
    assert out[ins - 1].b == 25.25 and not style_b._cuts_sentence(out, ins, segs)
    ok, k = style_b._tail_slot(pieces, [(18.0, 21.5, "a"), (22.5, 29.0, "b")], 22.0, 1)
    assert k == 1 and ok[0].b == 22.0                                    # chỗ đầu đã ổn: giữ nguyên như cũ
