"""Bản 50: số đọc bằng chữ («dầu nóng khoảng một trăm bảy mươi độ»): trước đây bộ kiểm chỉ hiểu MỘT chữ số đứng ngay
trước đơn vị, nên AI viết đúng «170 độ» lại bị coi là bịa số và bị xoá khỏi lời đọc."""
import ingredients


def test_spoken_numbers():
    sn = ingredients.spoken_number
    cases = {"một trăm bảy mươi": 170, "hai trăm": 200, "ba mươi": 30, "mười lăm": 15, "mười": 10,
             "hai mươi lăm": 25, "hai lăm": 25, "hai mươi mốt": 21, "bảy mươi tư": 74, "một trăm rưỡi": 150,
             "trăm rưỡi": 150, "một trăm linh năm": 105, "một trăm lẻ năm": 105, "một trăm hai": 120,
             "một trăm năm mươi": 150, "chín mươi chín": 99, "năm": 5, "ba mốt": 31, "hai chục": 20}
    for words, want in cases.items():
        assert sn(words.split()) == want, words
    for words in ("không", "mươi", "một mốt", "lăm", "chín năm", "rưỡi", "trăm trăm", "hai ba", "năm mươi nghìn"):
        assert sn(words.split()) is None, words


def test_numbers_understand_spelled_quantities():
    n = ingredients.numbers
    assert n("dầu nóng khoảng một trăm bảy mươi độ") == ["170 độ"]
    assert n("hai trăm gam thịt ba chỉ, mười lăm phút") == ["200 gam", "15 phút"]
    assert n("một trăm năm mươi tới hai trăm gam") == ["150-200 gam"]
    assert n("năm mươi nghìn đồng") == ["50 nghìn"]                    # nghìn là đơn vị giá, như AI viết "50 nghìn"
    assert n("luộc chín năm phút") == ["5 phút"]                        # "chín" (chín tới) không ghép vào số
    assert n("ba tới bốn giọt") == ["3-4 giọt"] and n("một con mực") == [] and n("nửa muỗng") == ["0.5 muỗng"]


def test_correct_digits_are_not_called_invented():
    orig = "Rồi mình bắc chảo dầu, đợi dầu nóng khoảng một trăm bảy mươi độ, chiên chừng ba phút là vàng."
    assert ingredients.check_numbers(orig, "Dầu nóng chừng 170 độ thì thả mực vô chiên 3 phút cho vàng giòn.") == \
        ([], [])
    assert ingredients.check_numbers(orig, "Chiên ở 180 độ chừng 3 phút.") == (["170 độ"], ["180 độ"])


def test_spelled_invented_numbers_can_be_dropped():
    d = ingredients.drop_numbers
    assert d("Chiên ở khoảng một trăm bảy mươi độ cho vàng giòn.", {"170 độ"}) == "Chiên ở cho vàng giòn."
    assert d("Chiên ở nhiệt độ một trăm bảy mươi độ cho vàng.", {"170 độ"}) == "Chiên ở nhiệt độ cho vàng."
    assert d("Luộc chín năm phút là được.", {"5 phút"}) == "Luộc chín là được."
    assert d("Cho một trăm năm mươi tới hai trăm gam thịt.", {"150-200 g"}) == "Cho thịt."
    assert d("Thêm hai muỗng nước mắm, ba muỗng đường.", {"2"}) == "Thêm nước mắm, ba muỗng đường."


# ---------------- góc nhìn đạo diễn: job 7 (bản 48) ----------------

import asyncio  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import config  # noqa: E402
import prompt_engine  # noqa: E402
from rmvideo.app import brand  # noqa: E402
from rmvideo.app.flow import remake, style, style_b  # noqa: E402
from rmvideo.domain import recipe  # noqa: E402

DATA = "tests/data/muc_hap_gung_kaggle.json"


def _fake_ai(monkeypatch, replies):
    """replies: danh sách lượt trả lời; mỗi lượt là list lời theo đoạn, hoặc dict trả nguyên (vd {"fixes": [...]})."""
    seen: list = []
    it = iter(replies)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        r = next(it)
        if isinstance(r, dict):
            return json.dumps(r, ensure_ascii=False)
        return json.dumps({"segments": [{"i": k, "text": t} for k, t in enumerate(r)], "title": "t", "caption": "c",
                           "hashtags": "#a", "hook": "mực đông đá khỏi rã đông"}, ensure_ascii=False)
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    return seen


def test_hook_leans_on_the_verified_tip(monkeypatch):
    seen = _fake_ai(monkeypatch, [["Mực đông đá khỏi rã đông, luộc vầy giòn sần sật mà không rách da nè."]])
    tip = "Mực đông đá không cần xả đông, thả trực tiếp vào nước sôi."
    asyncio.run(prompt_engine.generate_timed_any("", "", "cook", [(0.0, 5.0, "mực này mình để đông đá luôn nha")],
                                                 5.0, hook_tip=tip))
    assert "ĐIỂM ĂN TIỀN" in seen[0] and "Mực đông đá không cần xả đông, thả trực tiếp vào nước sôi»" in seen[0]
    facts = recipe.Facts(tips=[{"text": "Mẹo AI tự nghĩ", "source": "ai"}, {"text": tip, "source": "src"}])
    r = SimpleNamespace(facts=facts)
    assert remake.Remake.hook_tip(r) == tip
    r.facts = recipe.Facts(is_recipe=False, tips=[{"text": tip, "source": "src"}])
    assert remake.Remake.hook_tip(r) == ""


_SEGS = [(0.0, 3.0, "hôm nay mình làm món mực hấp gừng ngon lắm nha mọi người"),
         (3.4, 7.0, "rồi mọi người cho mực vô nồi nước đang sôi luộc khoảng tầm bốn phút nha cái mực nó sẽ bung ra đều "
                    "rất là đẹp luôn mọi người để ý nha"),
         (15.4, 19.0, "sau đó mình vớt mực ra để ráo nước rồi mình trộn chung với hành với gừng cho nó thơm nha mọi "
                      "người làm vậy là mực nó thấm đều luôn"),
         (27.4, 31.0, "tới đây mình đổ ra dĩa cho đẹp nha mọi người"),
         (33.4, 35.0, "vậy là xong rồi")]


def test_long_silence_after_a_line_asks_for_more(monkeypatch):
    """Job 7: 3 quãng lặng 4-5 giây — khe dài 12 giây mà AI viết chưa tới mức tối thiểu, đọc xong video im."""
    prompt_engine.set_voice_rate(2.8)
    head = "Mực hấp gừng kiểu này giòn ngọt, ai ăn cũng mê luôn đó."
    short1 = "Giờ thả mấy con mực vô nồi đang sôi sùng sục, đợi chừng 4 phút là bung đều."
    short2 = "Xong vớt ra để khô bớt, trộn chung hành gừng cho thơm phức."
    long1 = short1 + " Nước sủi lăn tăn, mực co tròn trịa, màu trắng hồng nhìn bắt mắt ghê."
    long2 = short2 + " Mùi gừng bốc lên thơm lừng, từng con bóng lưỡng, nhìn là muốn gắp liền."
    tail = ["Giờ trút hết mực lên dĩa lớn, nhìn hấp dẫn quá chừng.", "Món này làm nhanh gọn, cả nhà lưu lại nha."]
    try:
        seen = _fake_ai(monkeypatch, [[head, short1, short2] + tail, [head, long1, long2] + tail])
        out = asyncio.run(prompt_engine.generate_timed("", "", "cook", _SEGS, 36.0))
        assert len(seen) == 2 and "QUÁ NGẮN" in seen[1] and "1 («Giờ" in seen[1] and "2 («Xong" in seen[1]
        assert out["segments"][1]["text"] == long1
        monkeypatch.setattr(prompt_engine, "DEAD_AIR", 99.0)               # không có luật quãng lặng: không viết lại
        seen2 = _fake_ai(monkeypatch, [[head, short1, short2] + tail])
        asyncio.run(prompt_engine.generate_timed("", "", "cook", _SEGS, 36.0))
        assert len(seen2) == 1
    finally:
        prompt_engine.set_voice_rate(None)


def test_plate_named_before_plating(monkeypatch):
    """Job 7, 2:28: «Dĩa mực nóng hổi» khi mực còn trong vợt; gốc tới 2:43 mới «đổ ra»."""
    items = [(1, "sau khi tất cả ok xong lúc này mình sẽ vớt ra", "Mình vớt mực ra liền."),
             (2, "nè lúc này mọi người thấy không cái mực của mình nó rất là đẹp luôn",
              "Dĩa mực nóng hổi trông bắt mắt quá nè cả nhà."),
             (3, "rồi xong lúc này mình sẽ đổ ra cho mình", "Rồi mình trút hết ra dĩa lớn.")]
    assert prompt_engine.early_plate(items) == {2: "Dĩa mực nóng"}
    assert prompt_engine.unplate(items[1][2]) == "Mẻ mực nóng hổi trông bắt mắt quá nè cả nhà."
    assert prompt_engine.early_plate(items[:2]) == {}                       # gốc không bao giờ bày ra: không đoán
    side = [(1, "chuẩn bị dĩa rau muống luộc", "Dĩa rau muống luộc để sẵn nè."), items[2]]
    assert prompt_engine.early_plate(side) == {}                            # gốc cũng nói dĩa
    segs = [(0.0, 4.0, "hôm nay mình làm món mực hấp gừng ngon lắm nha"),
            (4.5, 8.5, "nè lúc này mọi người thấy không cái mực của mình nó rất là đẹp luôn nha"),
            (9.0, 13.0, "rồi xong lúc này mình sẽ đổ ra cho mình nha mọi người")]
    first = ["Mực hấp gừng giòn ngọt, ai ăn cũng mê liền luôn.", "Dĩa mực nóng hổi trông bắt mắt quá nè cả nhà ơi.",
             "Giờ mình trút hết mực ra dĩa cho đẹp mắt nè."]
    fixed = list(first)
    fixed[1] = "Mấy con mực trong vợt nóng hổi, trông bắt mắt quá nè cả nhà."
    seen = _fake_ai(monkeypatch, [first, fixed])
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 13.0))
    assert len(seen) == 2 and "CHƯA được bày ra dĩa" in seen[1] and out["segments"][1]["text"] == fixed[1]
    seen = _fake_ai(monkeypatch, [first] * 3)                              # AI vẫn nói dĩa: đổi thành «mẻ»
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 13.0))
    assert out["segments"][1]["text"] == "Mẻ mực nóng hổi trông bắt mắt quá nè cả nhà ơi."


def test_proofread_fixes_only_small_safe_slips(monkeypatch):
    """Job 7, 3:01: «Hoàn thành dĩa mực hấp gừng thơm lừng rảnh rỗi nè»."""
    segs = [{"text": "Hoàn thành dĩa mực hấp gừng thơm lừng rảnh rỗi nè.", "orig": "rồi đổ ra dĩa", "ost": "new"},
            {"text": "Thêm 2 muỗng nước mắm cho đậm đà nha.", "orig": "thêm 2 muỗng nước mắm", "ost": "new"},
            {"text": "Mực bung đều, nhìn ngon ghê.", "orig": "mực bung ra", "ost": "new"},
            {"text": "Tóm lại là luộc mực rồi vớt ra.", "orig": "Tóm lại: luộc mực, vớt ra.", "ost": "new"}]
    fixes = {"fixes": [{"i": 0, "text": "Hoàn thành dĩa mực hấp gừng thơm lừng rồi nè."},
                       {"i": 1, "text": "Thêm 3 muỗng nước mắm cho đậm đà nha."},              # đổi số: bỏ
                       {"i": 2, "text": "Tôm bung đều, nhìn ngon ghê."},                       # đổi nguyên liệu: bỏ
                       {"i": 3, "text": "Tóm lại là luộc mực xong vớt ra."}]}                 # khúc tóm tắt: không soát
    seen = _fake_ai(monkeypatch, [fixes])
    done = asyncio.run(prompt_engine.proofread(segs))
    assert "rảnh rỗi" in seen[0] and "3 | " not in seen[0]
    assert done == [(0, "Hoàn thành dĩa mực hấp gừng thơm lừng rảnh rỗi nè.",
                     "Hoàn thành dĩa mực hấp gừng thơm lừng rồi nè.")]
    assert [s["text"] for s in segs[1:3]] == ["Thêm 2 muỗng nước mắm cho đậm đà nha.", "Mực bung đều, nhìn ngon ghê."]
    big = {"fixes": [{"i": 2, "text": "Mấy con mực bung nở đều tăm tắp, trắng hồng bóng lưỡng, nhìn ngon ghê."}]}
    _fake_ai(monkeypatch, [big])
    assert asyncio.run(prompt_engine.proofread(segs)) == []                 # viết lại cả câu: không phải soát
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert asyncio.run(prompt_engine.proofread(segs)) == []


def test_voice_breathes_before_the_end_card():
    r = SimpleNamespace(out_dur=188.39, tail=2.2)
    assert remake.Remake.content_end.fget(r) == round(188.39 - 2.2 - brand.END_BREATH, 3)
    r.tail = 0.0
    assert remake.Remake.content_end.fget(r) == 188.39


def test_no_framing_holds_longer_than_shot_max():
    d = json.load(open(DATA, encoding="utf-8"))
    plan, why = style_b.plan_strong(d["end"], [tuple(s) for s in d["segs"]], d["cuts"], d["motion"], 1.0, "auto",
                                    recipe.Facts.from_dict(d["facts"]))
    mains = [p for p in plan.pieces if p.role == "main" and p.tag != "skip"]
    assert why == "" and not plan.validate() and max(p.b - p.a for p in mains) <= style.SHOT_MAX + 1e-6
    assert style._split_long([0.0, 13.2, 15.0]) == [0.0, 4.4, 8.8, 13.2, 15.0]          # không có câu: chia đều
    assert math.isclose(style._split_long([0.0, 6.1])[1], 3.05)
    # có câu: dời về khoảng lặng gần nhất; không có khoảng lặng thì tránh 0,6 giây cuối câu (khúc tóm tắt chèn sau câu
    # đó bị kéo lùi về ranh giới này và cắt đôi câu — video mực, 174,0s)
    assert style._split_long([0.0, 13.2], [(0.0, 3.9), (4.5, 13.0)]) == [0.0, 4.2, 8.8, 13.2]
    t = style._calm_point(174.44, 170.08, 178.81, [(170.3, 171.66), (174.02, 174.92)])
    assert t is not None and not 174.32 <= t < 174.92


# ---------------- màn kết sáng, logo, YouTube (góc nhìn đạo diễn TVC / YouTube) ----------------

def test_end_card_keeps_the_dish_bright_and_logo_clears_app_buttons():
    from rmvideo.media import renderer
    f = renderer._brand_filter(1080, 1920, 60.0, None, "/tmp/outro.txt", 3)
    assert "black@0.72" not in f and "drawbox=x=0:y=0:w=iw:h=ih" not in f   # không tối cả khung
    assert "y=ih*0.40:w=iw:h=ih*0.20" in f                                    # chỉ một dải sau tên kênh
    assert f"overlay=W-w-{int(24 * 1920 / 1280)}:{int(1920 * renderer.LOGO_Y)}" in f and renderer.LOGO_Y >= 0.08


def test_long_full_version_is_not_labelled_youtube_shorts():
    from rmvideo.app.flow import common
    post = prompt_engine.platform_post("Mực hấp gừng", "Ngon lắm", "#muc #monngon")
    assert "▶ YouTube Shorts:" in post and "#Shorts" in post
    long = common.post_for_length(post, 188.39)
    assert "YouTube Shorts" not in long and "#Shorts" not in long and "đăng bản ngắn" in long
    assert common.post_for_length(post, 89.0) == post and common.post_for_length("", 200.0) == ""


def test_low_ram_machine_renders_720p(monkeypatch, tmp_path):
    """Đo thật: bản ngắn 1080p dựng mạnh ngốn 1,2 GB, bản đầy đủ preset medium 2,5 GB; Botkeep 2 GB bị giết từ ~1 GB."""
    from rmvideo.media import ff, renderer
    monkeypatch.setattr(renderer, "SPEED_FILE", str(tmp_path / "rs.json"))
    monkeypatch.setattr(config, "OUTPUT_HD", True)
    for q, free, want in (("balanced", 1300, (720, 1280)), ("balanced", 2500, (1080, 1920)), ("high", 2500, (720, 1280)),
                          ("high", 6000, (1080, 1920))):
        monkeypatch.setattr(config, "QUALITY", q)
        monkeypatch.setattr(ff, "mem_available_mb", lambda free=free: free)
        notes: list = []
        assert renderer.output_size(1920, True, 80, notes) == want, (q, free)
        assert bool(notes) == (want == (720, 1280)) and (not notes or "RAM" in notes[0])
    monkeypatch.setattr(ff, "mem_available_mb", lambda: None)                 # không đọc được RAM: như trước
    assert renderer.output_size(1920, True, 80) == (1080, 1920)
