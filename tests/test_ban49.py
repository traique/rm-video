"""Bản 49: job 6 (17/11/2026) — Kaggle chờ máy quá 35 phút, lùi về Botkeep, ffmpeg dựng mạnh bị giết giữa chừng (hết
RAM: mỗi khúc một chuỗi màu + đầu vào phụ giải mã theo số lõi máy chủ); lời tóm tắt bị AI đổi thành lời khen chung
chung; "độ chừng" treo sau khi bỏ số bịa; "1/2 muỗng hạt nêm" đọc lặp.
Job 7 (bản 48): phút 1:51 lời kể lại "giờ mình thả mực vô nồi" khi hình là mực đã bung (người xem tưởng video quay
ngược); lời tóm tắt bỏ hẳn bước "đun sôi nước"; lời điểm danh trễ thẻ chữ ~1 giây."""
import asyncio
import json
from types import SimpleNamespace

import config
import ingredients
import prompt_engine
import kaggle_worker
from rmvideo.app.flow import style_b
from rmvideo.media import edl, ff, filtergraph, proc, voiceplan


def _plan(zooms=(1.0, 1.12, 1.0)):
    return edl.EditPlan(pieces=[edl.Piece(k * 2.0, k * 2.0 + 2.0, zoom=z) for k, z in enumerate(zooms)],
                        stretch=1.0, level="strong")


def test_colour_runs_once_after_concat_for_large_sources():
    plan = _plan()
    g = filtergraph._plan_video(plan, "cook", 720, 1280, 1080, 1920, 30, {}, None)
    assert g.count("hqdn3d") == 1 and g.count("unsharp") == 1        # một lần sau concat, không phải mỗi khúc
    assert g.index("concat") < g.index("hqdn3d")
    assert filtergraph.shared_look(plan, 720, 1280, 1080, 1920)


def test_small_sources_keep_per_piece_colour():
    plan = _plan()
    assert not filtergraph.shared_look(plan, 720, 1280, 360, 640)    # phải phóng to: chỉnh màu trước khi phóng
    g = filtergraph._plan_video(plan, "cook", 720, 1280, 360, 640, 30, {}, None)
    assert g.count("hqdn3d") == 3
    assert not filtergraph.shared_look(None, 720, 1280, 1080, 1920)


def test_look_parts_add_up():
    full = filtergraph.look_chain("cook", 720, 1280, 1080, 1920, zoom=1.12)
    geo = filtergraph.look_chain("cook", 720, 1280, 1080, 1920, zoom=1.12, part="geo")
    colour = filtergraph.look_chain("cook", 720, 1280, 1080, 1920, part="color")
    assert full == geo + "," + colour
    assert "hqdn3d" not in geo and "scale=" in geo and "crop=" in geo


def test_every_ffmpeg_input_gets_capped_threads():
    args = ["-y", "-threads", "2", "-i", "a.mp4", "-i", "v.wav", "-ss", "3", "-t", "2", "-i", "a.mp4",
            "-filter_complex", "x", "-threads", "2", "out.mp4"]
    out = ff.cap_input_threads(args, 2)
    assert out == ["-y", "-threads", "2", "-i", "a.mp4", "-threads", "2", "-i", "v.wav", "-ss", "3", "-t", "2",
                   "-threads", "2", "-i", "a.mp4", "-filter_complex", "x", "-threads", "2", "out.mp4"]
    assert ff.cap_input_threads(["-threads", "4", "-i", "a"], 2) == ["-threads", "4", "-i", "a"]   # đặt sẵn: giữ


def test_killed_ffmpeg_says_so():
    e = proc.ProcError(["ffmpeg", "-i", "x"], -9, "frame= 2062 fps=6.6 time=00:01:08.10")
    assert str(e).startswith("ffmpeg bị hệ thống dừng (tín hiệu 9, thường do máy hết RAM): frame=")
    assert str(proc.ProcError(["ffmpeg"], 137, "x")).startswith("ffmpeg bị hệ thống dừng (tín hiệu 9")
    assert str(proc.ProcError(["ffmpeg"], 1, "Invalid data")).startswith("ffmpeg lỗi (mã 1): Invalid data")


def test_recap_must_keep_its_steps():
    o = "Tóm lại: dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa."
    assert prompt_engine.side_lost("Nấu kiểu này đơn giản lắm, ai làm cũng ngon.", o)
    assert not prompt_engine.side_lost("Tóm lại mình luộc mực đông với nước sôi, thêm hành tây rồi vớt ra dĩa.", o)
    assert not prompt_engine.side_lost("Nấu kiểu này đơn giản lắm.", "Đun sôi nước rồi thả mực vô.")   # đoạn thường
    assert prompt_engine.side_spoken(o) == "Tóm lại là dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra dĩa."
    m = "Nguyên liệu gồm: mực, 1/2 muỗng bột ngọt, hành tây."
    assert prompt_engine.side_spoken(m) == "Chuẩn bị mực, 1/2 muỗng bột ngọt, hành tây."


def test_lost_recap_is_rewritten_then_falls_back(monkeypatch):
    segs = [(0.0, 5.6, "Hôm nay mình làm món mực hấp gừng giòn ngọt thơm ngon đơn giản tại nhà nha mọi người."),
            (6.0, 11.6, "Tóm lại: dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa."),
            (12.0, 17.6, "Vậy là đã xong công thức mực hấp gừng thơm ngon đơn giản tại nhà rồi đó cả nhà ơi.")]
    lost = ["Mực hấp gừng giòn sần sật, nhìn là thèm liền luôn.", "Nấu kiểu này đơn giản lắm, ai làm cũng ngon.",
            "Xong rồi đó, dĩa mực hấp gừng thơm lừng dọn lên được rồi."]
    seen: list = []
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        return json.dumps({"segments": [{"i": k, "text": t} for k, t in enumerate(lost)], "title": "t", "caption": "c",
                           "hashtags": "#a", "hook": "mực hấp"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 18.0))
    assert len(seen) == 3 and "phải đọc ĐỦ các ý" in seen[1] and "ĐOẠN ĐIỂM DANH / TÓM TẮT" in seen[0]
    assert out["segments"][1]["text"] == "Tóm lại là dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra dĩa."


def test_repeated_list_item_and_dangling_approximator():
    assert prompt_engine.dedupe_items("Rồi mình nêm vào 1/2 muỗng đường, 1/2 muỗng hạt nêm, 1/2 muỗng hạt nêm.") == \
        "Rồi mình nêm vào 1/2 muỗng đường, 1/2 muỗng hạt nêm."
    assert ingredients.drop_numbers("Xong mình rưới thêm dầu mè, độ chừng 3-4 muỗng cho dậy mùi.", {"3-4 muỗng"}) == \
        "Xong mình rưới thêm dầu mè, cho dậy mùi."


# ---------------- job 7 ----------------

J7 = ("Mọi người làm theo cách này nha, giờ mình thả mực vô nồi nước đang sôi, mọi người đun chừng 4 phút là mực nở "
      "bung ra, nhìn hấp dẫn ghê.")


def test_add_actions():
    def names(t):
        return {ingredients.GROUPS[g][0] for g in prompt_engine.add_actions(t)}
    assert names(J7) == {"mực"}
    assert names("Tới đây mình nêm tiếp nửa muỗng đường, thêm nửa muỗng hạt nêm vào nồi.") == {"đường", "hạt nêm"}
    assert names("Tầm xíu mình rưới thêm chút dầu mè cho thơm phức.") == {"dầu mè"}
    for t in ("Thêm nữa là mực mua về cứ để nguyên con luộc.", "Luộc cho mực chín đều.", "Bỏ ruột mực đi."):
        assert names(t) == set(), t


def test_replayed_step_found_only_when_already_done_and_not_in_source():
    first = (1, "Mọi người bắt nước sôi như thế này. Mọi người bỏ trực tiếp vô luôn.",
             "Bắc nồi nước lên bếp nè, rồi mình thả mực vô nồi nước sôi.")
    later = (3, "đa số người ta giống không à sau khi mình luộc khoảng tầm 4 phút lúc này cái mực mình nó bung ra", J7)
    assert prompt_engine.replayed_steps([first, later]) == {3: prompt_engine.add_actions(J7)}
    assert prompt_engine.replayed_steps([later]) == {}                                   # lần đầu cho vào: đúng
    again = (3, "rồi xong lúc này thì mình sẽ cho là mực vô luộc tiếp", J7)             # gốc cũng cho vào lần nữa
    assert prompt_engine.replayed_steps([first, again]) == {}
    assert prompt_engine.drop_replayed(J7, prompt_engine.add_actions(J7)) == \
        "Mọi người làm theo cách này nha, mọi người đun chừng 4 phút là mực nở bung ra, nhìn hấp dẫn ghê."
    short = "Giờ mình thả mực vô, ngon."
    assert prompt_engine.drop_replayed(short, prompt_engine.add_actions(short)) == short   # còn quá ít chữ: giữ


_J7_SEGS = [(0.0, 5.6, "Mọi người bắt nước sôi như thế này. Mọi người bỏ trực tiếp vô luôn."),
            (6.0, 11.6, "rồi mọi người bỏ trong này là nửa muỗng bột ngọt cho mình nha"),
            (12.0, 22.0, "đa số người ta giống không à sau khi mình luộc khoảng tầm 4 phút lúc này cái mực mình nó bung "
                         "ra thế này rất là ngon luôn")]
_J7_HEAD = ["Giờ mình bắc nồi nước lên bếp, đợi sôi lăn tăn rồi thả mực vô luôn nè.",
            "Tiếp theo mình nêm nửa muỗng bột ngọt vô nồi nước, khuấy nhẹ cho tan đều."]


def _fake_ai(monkeypatch, replies):
    seen: list = []
    it = iter(replies)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        texts = next(it)
        return json.dumps({"segments": [{"i": k, "text": t} for k, t in enumerate(texts)], "title": "t", "caption": "c",
                           "hashtags": "#a", "hook": "mực luộc"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    return seen


def test_replayed_step_is_rewritten(monkeypatch):
    good = "Luộc chừng 4 phút là mực nở bung ra đều, nhìn con nào cũng tròn trịa hấp dẫn ghê luôn á."
    seen = _fake_ai(monkeypatch, [_J7_HEAD + [J7], _J7_HEAD + [good]])
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", _J7_SEGS, 22.0))
    assert "KHÔNG KỂ LẠI BƯỚC ĐÃ LÀM" in seen[0]
    assert len(seen) == 2 and "KỂ LẠI bước cho nguyên liệu vào ĐÃ LÀM" in seen[1] and "đã cho vào trước đó: mực" in seen[1]
    assert out["segments"][2]["text"] == good


def test_replayed_step_clause_dropped_when_ai_insists(monkeypatch):
    seen = _fake_ai(monkeypatch, [_J7_HEAD + [J7]] * 3)
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", _J7_SEGS, 22.0))
    assert len(seen) == 3
    assert out["segments"][2]["text"] == \
        "Mọi người làm theo cách này nha, mọi người đun chừng 4 phút là mực nở bung ra, nhìn hấp dẫn ghê."


def test_recap_missing_one_step_is_lost():
    o = "Tóm lại: dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa."
    assert prompt_engine.side_lost("Dùng mực đông lạnh, trụng hành tây rồi vớt mực, mấy con mực nhìn mê ly ghê á.", o)
    m = "Nguyên liệu gồm: mực, 1/2 muỗng bột ngọt, 1/2 muỗng đường, 1/2 muỗng hạt nêm, hành tây."
    assert not prompt_engine.side_lost("Chuẩn bị mực, nửa muỗng bột ngọt, nửa muỗng đường, nửa muỗng hạt nêm với "
                                       "hành tây.", m)
    assert prompt_engine.side_lost("Chuẩn bị mực, nửa muỗng bột ngọt, nửa muỗng đường với hành tây.", m)


def test_montage_starts_with_first_item(monkeypatch):
    assert prompt_engine.montage_lead("Rồi mình chuẩn bị mực, nửa muỗng bột ngọt.") == "Chuẩn bị mực, nửa muỗng bột ngọt."
    assert prompt_engine.montage_lead("Đầu tiên, mình chuẩn bị mực.") == "Chuẩn bị mực."
    for t in ("Giờ mọi người cần chuẩn bị mực nha.", "Nguyên liệu có mực, hành tây.", "Chuẩn bị mực."):
        assert prompt_engine.montage_lead(t) == t
    segs = [(0.0, 3.0, "Hôm nay mình làm món mực hấp gừng giòn ngọt thơm ngon đơn giản tại nhà nha mọi người."),
            (3.0, 9.0, "Nguyên liệu gồm: mực, 1/2 muỗng bột ngọt, hành tây.")]
    _fake_ai(monkeypatch, [["Mực hấp gừng giòn ngọt, ai ăn cũng mê.",
                            "Rồi mình chuẩn bị mực, nửa muỗng bột ngọt với hành tây."]])
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 9.0))
    assert out["segments"][1]["text"] == "Chuẩn bị mực, nửa muỗng bột ngọt với hành tây."


def test_sentence_before_montage_does_not_spill_into_it():
    m = "Nguyên liệu gồm: mực, 1/2 muỗng bột ngọt, hành tây."
    assert voiceplan.SIDE_MARKS == prompt_engine.SIDE_MARKS == (style_b.MONTAGE_MARK, style_b.RECAP_MARK)
    segs = [{"start": 0.72, "slot": 2.28, "span": 2.28}, {"start": 3.0, "slot": 6.0, "span": 6.0, "orig": m},
            {"start": 9.0, "slot": 6.0, "span": 6.0}]
    plan, _ = voiceplan.plan_voice([2.74, 3.0, 2.0], segs, 15.0)
    assert plan[0]["end"] <= 3.0 and plan[1]["start"] <= 3.12              # job 7: hook dứt 3.46, điểm danh 3.50
    free = [dict(s, orig="") for s in segs]
    plan2, _ = voiceplan.plan_voice([2.74, 3.0, 2.0], free, 15.0)
    assert plan2[0]["end"] > plan[0]["end"] - 1e-6                          # đoạn thường vẫn được lấn như cũ


def test_kaggle_spec_carries_side_orig_only():
    job = SimpleNamespace(id=7, mode="cook", url="u", local=None, voice="", subs=True, max_duration=0)
    segs = [{"i": 0, "start": 0.0, "slot": 3.0, "span": 3.0, "text": "a", "ost": "new", "orig": "câu gốc dài"},
            {"i": 1, "start": 3.0, "slot": 6.0, "span": 6.0, "text": "b", "ost": "new",
             "orig": "Nguyên liệu gồm: mực, hành tây."}]
    spec = kaggle_worker.build_spec(job, segs, 9.0, 9.0, 1.0, True, "T", [], None, {}, [], 0)
    assert "orig" not in spec["segments"][0] and spec["segments"][1]["orig"] == "Nguyên liệu gồm: mực, hành tây."


def test_half_written_as_fraction_or_word_is_the_same_amount():
    assert ingredients.check_numbers("Nguyên liệu gồm: mực, 1/2 muỗng bột ngọt", "Chuẩn bị mực, nửa muỗng bột ngọt") == \
        ([], [])
    assert ingredients.numbers("3/4 chén, 1/3 muỗng") == ["0.75 chén", "1/3 muỗng"]
    assert ingredients.drop_numbers("Thêm 1/2 muỗng đường, 2 muỗng muối.", {"2"}) == "Thêm 1/2 muỗng đường, muối."
    assert ingredients.drop_numbers("Thêm 1/2 muỗng đường, 2 muỗng muối.", {"0.5"}) == "Thêm đường, 2 muỗng muối."
