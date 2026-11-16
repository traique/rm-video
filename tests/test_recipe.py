"""Bản 42 (S1): đọc số tiếng Việt, quy về đơn vị chuẩn Việt Nam, đối chiếu định lượng với lời gốc, cộng dồn, công thức
trong bài đăng, mẹo, lọc video quảng cáo của nguồn."""
import asyncio

import pytest

import config
import prompt_engine
from rmvideo.domain import recipe as r


@pytest.mark.parametrize("text,want", [
    ("nửa ký cánh gà", ["500 g"]),
    ("1 ký rưỡi thịt ba chỉ", ["1,5 kg"]),
    ("một ký rưỡi thịt", ["1,5 kg"]),
    ("hai trăm gam đường", ["200 g"]),
    ("3 lạng tôm", ["300 g"]),
    ("2 thìa nước mắm", ["2 muỗng"]),
    ("2 thìa canh nước mắm", ["2 muỗng canh"]),
    ("1/2 muỗng cà phê tiêu", ["1/2 muỗng cà phê"]),
    ("1500 gam bột", ["1,5 kg"]),
    ("1250 gam bột", ["1250 g"]),
    ("một trăm rưỡi ml nước", ["150 ml"]),
    ("hai mươi lăm gam muối", ["25 g"]),
    ("năm mươi gam", ["50 g"]),
    ("2 kí cua đồng", ["2 kg"]),
    ("0,5 lít nước dừa", ["500 ml"]),
    ("3 quả trứng", ["3 trái"]),
    ("chiên 5 phút", ["5 phút"]),
    ("cho vào 1 cái tô", []),
    ("đợi một lát là được", []),
    ("mình sẽ Cho là 50 mi li lít nước", ["50 ml"]),          # lời bóc thật (Bảy Cua Quán, cánh gà chiên nước mắm)
    ("lửa nhỏ Cho mình là khoảng một phút rưỡi", ["1,5 phút"]),
    ("nêm là một muỗng đường 1/3 muỗng bột ngọt", ["1 muỗng", "1/3 muỗng"]),
    ("tây và 50g đồ hành sắt hút", ["50 g"]),
])
def test_parse_and_standard_units(text, want):
    assert [r.fmt(q) for q in r.parse(text)] == want


TEXTS = ["hôm nay mình làm cánh gà chiên nước mắm nha", "mình có 1 ký cánh gà rửa sạch",
         "ướp với 2 muỗng canh nước mắm, 1 muỗng đường", "tỏi thì mình băm 2 tép",
         "chiên 7 phút cho vàng", "rồi phi thêm 3 tép tỏi", "ai muốn học thì đăng ký khóa học của quán nha"]
AI = {"is_recipe": True, "dish": "Cánh gà chiên nước mắm", "servings": "4 người ăn",
      "ingredients": [{"name": "cánh gà", "qty": "1", "unit": "ký", "seg": 1},
                      {"name": "nước mắm", "qty": "2", "unit": "muỗng canh", "seg": 2},
                      {"name": "đường", "qty": "1", "unit": "muỗng", "seg": 4},          # AI ghi sai đoạn
                      {"name": "tỏi", "qty": "2", "unit": "tép", "seg": 3},
                      {"name": "tỏi", "qty": "3", "unit": "tép", "seg": 5},
                      {"name": "ớt", "qty": "2", "unit": "trái", "seg": 4},               # lời gốc không có
                      {"name": "muối", "qty": "", "unit": "", "seg": -1}],
      "steps": [{"kind": "intro", "from": 0, "to": 0, "text": ""},
                {"kind": "prep", "from": 1, "to": 3, "text": "Rửa 1 kg cánh gà, ướp 2 muỗng canh nước mắm, 1 muỗng đường 15 phút."},
                {"kind": "cook", "from": 4, "to": 5, "text": "Chiên gà 7 phút rồi phi tỏi, thêm bơ."},
                {"kind": "promo", "from": 6, "to": 6, "text": ""}],
      "tips": [{"text": "Ướp gà thật kỹ cho thấm.", "seg": 2}, {"text": "Món này giúp giảm cân.", "seg": -1},
               {"text": "Chiên hai lần cho giòn lâu.", "seg": -1}, {"text": "Chiên lửa vừa 10 phút.", "seg": -1}]}


def test_verify_against_transcript():
    f = r.verify(AI, TEXTS, "")
    st = {(x.name, x.raw): x.status for x in f.items}
    assert st[("cánh gà", "1 ký")] == "ok" and st[("nước mắm", "2 muỗng canh")] == "ok"
    assert st[("đường", "1 muỗng")] == "ok"                         # AI ghi sai đoạn: tìm thấy ở đoạn khác
    assert st[("ớt", "2 trái")] == "unverified"                      # không có trong lời gốc: không in số
    assert st[("muối", "")] == "free"
    assert f.servings == ""                                          # "4 người" không có trong lời gốc / mô tả
    assert [s.kind for s in f.steps] == ["intro", "prep", "cook", "promo"]
    assert "một lúc" in f.steps[1].text and "15 phút" not in f.steps[1].text   # số lạ -> bỏ
    assert f.steps[2].text == ""                                     # nguyên liệu lạ (bơ) -> bỏ câu
    tips = [t["text"] for t in f.tips]
    assert tips[0] == "Ướp gà thật kỹ cho thấm." and "Chiên hai lần cho giòn lâu." in tips
    assert not any("giảm cân" in t or "10 phút" in t for t in tips) and sum(t["source"] == "ai" for t in f.tips) == 1


def test_recipe_block_aggregates_and_strict_soft():
    f = r.verify(AI, TEXTS, "")
    block = r.recipe_block(f)
    assert "• Tỏi: 5 tép" in block and "• Cánh gà: 1 kg" in block and "• Ớt: vừa đủ" in block
    assert "👩‍🍳 CÁCH LÀM\n1. Rửa 1 kg cánh gà" in block and "💡 MẸO: Ướp gà thật kỹ cho thấm." in block
    assert "• Ớt: 2 trái (ước lượng)" in r.recipe_block(f, strict=False)
    assert r.compact_ingredients(f).startswith("🧾 Cánh gà 1 kg, nước mắm 2 muỗng canh")
    assert r.report(f).startswith("Định lượng: 5/6 khớp lời gốc, 1 chưa đối chiếu được (ớt 2 trái)")
    assert r.Facts.from_dict(f.to_dict()).to_dict() == f.to_dict()


def test_conflict_between_speech_and_description():
    ai = {"is_recipe": True, "dish": "Thịt kho", "ingredients": [
        {"name": "thịt ba chỉ", "qty": "500", "unit": "gam", "seg": 0},
        {"name": "thịt ba chỉ", "qty": "600", "unit": "gam", "seg": -1}], "steps": [], "tips": []}
    f = r.verify(ai, ["mình dùng 500 gam thịt ba chỉ"], "Nguyên liệu: 600g thịt ba chỉ")
    assert [x.status for x in f.items] == ["conflict", "conflict"]
    assert "mâu thuẫn" in r.report(f)


def test_unassigned_numbers_reported():
    ai = {"is_recipe": True, "dish": "Canh", "ingredients": [], "steps": [], "tips": []}
    f = r.verify(ai, ["cho 300 gam bí đỏ vào nồi", "nấu 10 phút"], "")
    assert f.unassigned == ["300 g"]


def test_looks_like_ad():
    assert r.looks_like_ad("Khóa tiệc cưới tháng 12", 17)
    assert r.looks_like_ad("Nay học viên ra trường khóa học ốc lẩu nướng", 24)
    assert r.looks_like_ad("Tiệc trọn gói tại tư gia 120 khách", 74)
    assert r.looks_like_ad("", 30)
    assert not r.looks_like_ad("", 150)
    assert not r.looks_like_ad("Công thức mực nhúng giấm thơm ngon đơn giản tại nhà. #food", 126)


def test_platform_post_with_recipe_keeps_old_output_without():
    old = prompt_engine.platform_post("Tiêu đề", "Caption", "#a #b", "")
    assert prompt_engine.platform_post("Tiêu đề", "Caption", "#a #b", "", "", "") == old
    new = prompt_engine.platform_post("Tiêu đề", "Caption", "#a #b", "", "🧾 NGUYÊN LIỆU\n• Tỏi: 5 tép", "🧾 Tỏi 5 tép")
    fb = new.split("▶ Facebook:\n", 1)[1].split("\n\n▶ ", 1)[0]
    assert fb == "Caption\n\n🧾 NGUYÊN LIỆU\n• Tỏi: 5 tép\n\n#a #b"
    assert "#a #b\n🧾 Tỏi 5 tép" in new


def test_recipe_prompt_and_normalisation(monkeypatch):
    calls = []

    async def fake(prompt, images=None, schema=None, label="", timeout=60):
        calls.append((prompt, len(images or []), getattr(schema, "__name__", None), label))
        return ('{"is_recipe": true, "dish": "Gà", "ingredients": [], '
                '"steps": [{"kind": "cook", "a": 0, "b": 1, "text": "Chiên gà"}], "tips": []}')
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    monkeypatch.setattr(prompt_engine, "genai", object())
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "SPEECH_STYLE", "mien_nam")
    out = asyncio.run(prompt_engine.recipe_facts("Gà chiên", "", ["cho gà vào", "chiên vàng"], [b"x", b"y"], [1.0, 2.0]))
    assert out["steps"][0]["from"] == 0 and out["steps"][0]["to"] == 1
    prompt, n_img, schema, label = calls[0]
    assert n_img == 2 and schema == "RecipeOut" and label == "Ảnh"
    assert "0 | cho gà vào\n1 | chiên vàng" in prompt and "Ảnh 1: giây 2" in prompt and "giọng miền Nam" in prompt


def test_autopost_skips_not_recipe_without_streak(monkeypatch, tmp_path):
    import autopost
    st = autopost.load_state(str(tmp_path / "a.json"))
    st["pending"] = {"vid": "tt:1", "source": "@x", "job": 7}

    class J:
        id, status, error, no_autopost = 7, "error", f"{r.NOT_RECIPE}: dừng sớm", ""
    import state
    monkeypatch.setattr(state.queue, "jobs", {7: J()})
    msg = asyncio.run(autopost._finish_pending(st, 1000.0, None))
    assert r.NOT_RECIPE in msg and st["pending"] is None and st.get("streak", 0) == 0
    assert st["used"]["tt:1"]["skip"] == r.NOT_RECIPE


def test_real_transcript_quantities_with_asr_errors():
    """Lời bóc thật: "1 muỗng nấm mặn" là nước mắm máy nghe sai; câu trích nguyên văn có trong lời gốc thì tin số."""
    texts = ["mọi người Cho là nửa muỗng tỏi", "một muỗng tương ớt", "một muỗng nấm mặn", "Cho là 50 mi li lít nước"]
    ai = {"is_recipe": True, "dish": "Cánh gà chiên nước mắm", "steps": [], "tips": [], "ingredients": [
        {"name": "tỏi", "qty": "1/2", "unit": "muỗng", "seg": 0, "quote": "nửa muỗng tỏi"},
        {"name": "tương ớt", "qty": "1", "unit": "muỗng", "seg": 1, "quote": "một muỗng tương ớt"},
        {"name": "nước mắm", "qty": "1", "unit": "muỗng", "seg": 2, "quote": "một muỗng nấm mặn"},
        {"name": "nước", "qty": "50", "unit": "ml", "seg": 3, "quote": "50 mi li lít nước"},
        {"name": "nước mắm", "qty": "2", "unit": "muỗng", "seg": 2, "quote": "hai muỗng nước mắm"}]}
    f = r.verify(ai, texts, "")
    assert [x.status for x in f.items] == ["ok", "ok", "ok", "ok", "unverified"]
    assert "• Tỏi: 1/2 muỗng" in r.recipe_block(f) and "• Nước: 50 ml" in r.recipe_block(f)
