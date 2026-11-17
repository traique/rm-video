"""Bản 49: job 6 (17/11/2026) — Kaggle chờ máy quá 35 phút, lùi về Botkeep, ffmpeg dựng mạnh bị giết giữa chừng (hết
RAM: mỗi khúc một chuỗi màu + đầu vào phụ giải mã theo số lõi máy chủ); lời tóm tắt bị AI đổi thành lời khen chung
chung; "độ chừng" treo sau khi bỏ số bịa; "1/2 muỗng hạt nêm" đọc lặp."""
import asyncio
import json

import config
import ingredients
import prompt_engine
from rmvideo.media import edl, ff, filtergraph, proc


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
    assert prompt_engine.side_spoken(o) == "Tóm lại là dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa."
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
    assert out["segments"][1]["text"] == "Tóm lại là dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa."


def test_repeated_list_item_and_dangling_approximator():
    assert prompt_engine.dedupe_items("Rồi mình nêm vào 1/2 muỗng đường, 1/2 muỗng hạt nêm, 1/2 muỗng hạt nêm.") == \
        "Rồi mình nêm vào 1/2 muỗng đường, 1/2 muỗng hạt nêm."
    assert ingredients.drop_numbers("Xong mình rưới thêm dầu mè, độ chừng 3-4 muỗng cho dậy mùi.", {"3-4 muỗng"}) == \
        "Xong mình rưới thêm dầu mè, cho dậy mùi."
