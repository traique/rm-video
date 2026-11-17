"""Bản 44: soát video «mực hấp gừng» chạy thật lần 2 (17/11/2026, Kaggle bóc lời + dựng).

- dựng mạnh lùi về dựng nhẹ vì khúc tóm tắt lấy đúng quãng lớp A đã tua (R5) -> mất điểm danh nguyên liệu + tóm tắt;
- bỏ "chào hỏi" từ giây 0 làm mất cảnh đầu bếp nhấc mực thành phẩm (câu hook), video mở bằng thẻ gia vị;
- gốc "nửa muỗng" (Whisper nghe "nổ muỗng") -> lời đọc "1 muỗng" x3: bộ kiểm bắt được mà vẫn để lọt;
- AI viết ~nửa số chữ, câu cụt kiểu tít báo -> video lặng 5-7 giây giữa các câu, nghe "chất AI".
Dữ liệu thật của video (lời gốc large-v3, kết quả AI đọc công thức, chuyển động): tests/data/muc_hap_gung.json."""
import asyncio
import json
import os

import config
import ingredients
import prompt_engine
import vocab
from rmvideo.app.flow import style_b
from rmvideo.domain import recipe
from rmvideo.media import edl

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "muc_hap_gung.json")


def _real():
    d = json.load(open(DATA, encoding="utf-8"))
    return d["end"], [tuple(s) for s in d["segs"]], recipe.Facts.from_dict(d["facts"]), d["motion"]


def test_real_video_strong_plan_passes_rules():
    end, segs, facts, mot = _real()
    plan, why = style_b.plan_strong(end, segs, [], mot, 1.0, "auto", facts)
    assert why == "" and plan.level == "strong"
    assert not style_b.check_rules(plan, segs, facts.steps, style_b.chatter(segs, facts.steps))
    tags = [p.tag for p in plan.pieces]
    assert tags.count("montage") >= 3 and tags.count("recap") >= 3
    # mọi khúc tóm tắt / điểm danh lặp lại cảnh còn trong video chính, chạy tốc độ thường
    for p in plan.pieces:
        if p.tag in ("montage", "recap"):
            assert any(q.role == "main" and q.speed <= 1.01 and q.a - 1e-6 <= p.a and p.b <= q.b + 1e-6
                       for q in plan.pieces), p


def test_real_video_opens_with_dish_then_ingredients():
    end, segs, facts, mot = _real()
    plan, _ = style_b.plan_strong(end, segs, [], mot, 1.0, "auto", facts)
    first = plan.pieces[0]
    assert first.role == "main" and first.a == 0.0 and first.b >= style_b.HOOK_KEEP      # đầu bếp nhấc mực thành phẩm
    assert plan.pieces[1].tag == "montage"
    assert plan.texts[0]["text"].startswith("Nguyên liệu gồm: 1/2 muỗng bột ngọt")
    assert plan.texts[0]["t"] == round(first.b - first.a, 2)


def test_greeting_first_sentence_is_not_kept_as_hook():
    segs = [(0.3, 2.5, "xin chào cả nhà hôm nay mình làm món"), (3.0, 6.0, "đây là 1 ký cánh gà")]
    assert style_b._hook_end(segs, 2.5) == 0.0
    assert style_b._hook_end([(0.0, 1.4, "khi mọi người hấp"), (1.4, 2.6, "nó còn chín như này")], 2.6) == 2.6


def test_kept_at_avoids_sped_up_and_cut_parts():
    pieces = [edl.Piece(0, 22), edl.Piece(22, 36, speed=3.0, tag="skip"), edl.Piece(40, 60)]
    assert edl.Piece(0, 1).speed == 1.0
    assert style_b._kept_at(pieces, 18.5, 40.8, 0.7) is not None
    a = style_b._kept_at(pieces, 18.5, 40.8, 0.7)
    assert 18.5 <= a and a + 0.7 <= 22.0                       # phần 18.5-22 (tốc độ thường) dài hơn 40-40.8
    assert style_b._kept_at(pieces, 23, 35, 0.7) is None       # chỉ có quãng tua nhanh


def test_half_spoon_heard_as_no_muong():
    fixed = vocab.fix_common("rồi mọi người bỏ trong này là nổ muỗng bột mọt, nổ muỗng đường, nổ muỗng hạt nêm")
    assert fixed == "rồi mọi người bỏ trong này là nửa muỗng bột ngọt, nửa muỗng đường, nửa muỗng hạt nêm"
    assert ingredients.numbers(fixed) == ["0.5 muỗng"] * 3
    assert vocab.fix_common("Nổ muỗng đường") == "Nửa muỗng đường"
    assert vocab.fix_common("pháo nổ to quá") == "pháo nổ to quá"              # "nổ" không đứng trước đơn vị: giữ
    assert vocab.fix_common("Đặc biệt khi làm mật hấp gừng") == "Đặc biệt khi làm mực hấp gừng"


def test_drop_invented_numbers_keeps_ingredients():
    t = "Nêm thêm một muỗng bột ngọt, một muỗng đường cùng một muỗng hạt nêm."
    assert ingredients.drop_numbers(t, {"1"}) == "Nêm thêm bột ngọt, đường cùng hạt nêm."
    assert ingredients.drop_numbers("Luộc khoảng 4 phút rồi thêm 1 muỗng dầu.", {"1"}) == \
        "Luộc khoảng 4 phút rồi thêm dầu."
    assert ingredients.drop_numbers("Thêm nửa muỗng đường.", {"1"}) == "Thêm nửa muỗng đường."


def _fake(monkeypatch, replies, seen):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())
    it = iter(replies)

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        texts = next(it)
        return json.dumps({"segments": [{"i": k, "text": t} for k, t in enumerate(texts)],
                           "title": "t", "caption": "c", "hashtags": "#a", "hook": "mực hấp gừng"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)


SEGS = [(0.0, 5.6, "Hôm nay mình làm món mực hấp gừng giòn ngọt thơm ngon đơn giản tại nhà nha mọi người."),
        (6.0, 11.6, "Đầu tiên mọi người bắc nồi nước lên bếp, đợi nước sôi thật là sôi rồi mới thả mực vô nha."),
        (12.0, 17.6, "Mọi người nhớ là không được đảo nhiều, đảo nhiều nó bị rách da, ăn không có ngon đâu."),
        (18.0, 23.6, "Rồi mọi người vặn lửa riu riu cho mình, để nó sôi lăn tăn vậy thôi là được rồi đó.")]


def test_thin_narration_is_rewritten_longer(monkeypatch):
    short = ["Mực hấp gừng ngon.", "Bắt nồi nước.", "Đừng đảo nhiều.", "Lửa riu riu."]
    full = ["Mực hấp gừng kiểu này giòn ngọt, thơm lừng mà ở nhà ai cũng làm được hết á.",
            "Giờ đặt cái nồi lên, chờ sôi sùng sục thiệt mạnh, xong cả nhà cho mấy con mực xuống liền.",
            "Lúc này cả nhà ráng để yên, khuấy hoài là lớp vỏ ngoài tróc hết, mất đẹp lắm luôn.",
            "Xong hạ lửa nhỏ xíu xuống, giữ nồi sủi tăm nhè nhẹ, cứ để yên vậy cho mực chín đều."]
    seen: list = []
    _fake(monkeypatch, [short, full], seen)
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", SEGS, 24.0))
    assert len(seen) == 2 and "QUÁ NGẮN" in seen[1]
    assert [s["text"] for s in out["segments"]] == full


def test_invented_numbers_removed_after_rewrites(monkeypatch):
    segs = [(0.0, 5.6, "Ở trong này là nửa muỗng bột ngọt, nửa muỗng đường, nửa muỗng hạt nêm nha mọi người."),
            (6.0, 11.6, "Rồi mọi người vặn lửa riu riu cho mình, để nó sôi lăn tăn vậy thôi là được rồi đó.")]
    bad = ["Mình nêm thêm một muỗng bột ngọt, một muỗng đường cùng một muỗng hạt nêm vô nồi nha.",
           "Rồi mình vặn lửa riu riu thôi, cho nước sôi lăn tăn nhẹ nhàng vậy là được."]
    seen: list = []
    _fake(monkeypatch, [bad, bad, bad], seen)
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 12.0))
    assert len(seen) == 3 and "KHÔNG tự điền" in seen[1]
    assert out["segments"][0]["text"] == "Mình nêm thêm bột ngọt, đường cùng hạt nêm vô nồi nha."


# ---------------- bản 45: dữ liệu như phiên Kaggle (lời gộp câu dài, có điểm chuyển cảnh) ----------------

KAGGLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "muc_hap_gung_kaggle.json")


def _kaggle():
    d = json.load(open(KAGGLE, encoding="utf-8"))
    return d["end"], [tuple(s) for s in d["segs"]], d["cuts"], recipe.Facts.from_dict(d["facts"]), d["motion"]


def test_sentence_across_scene_cut_is_not_lost():
    """Lời Kaggle gộp câu 8,56-14,28s vắt qua chuyển cảnh 8,67s: hai khúc liền nhau, câu vẫn còn nguyên. Bản 44 báo
    nhầm "R1 mất câu ở giây 8.6" và bỏ cả lớp B (video thật 17/11 hai lần liền không có điểm danh / tóm tắt)."""
    end, segs, cuts, facts, mot = _kaggle()
    plan, why = style_b.plan_strong(end, segs, cuts, mot, 1.0, "auto", facts)
    assert why == "" and plan.level == "strong"
    tags = [p.tag or p.role for p in plan.pieces]
    assert tags[0] == "main" and tags[1] == "montage" and "recap" in tags


def test_split_at_scene_cut_close_to_sentence_end():
    """Câu mở đầu hết 2,58s, chuyển cảnh 2,7s: chèn điểm danh SAU khúc đầu, không lên đầu video."""
    pieces = [edl.Piece(0.0, 2.7), edl.Piece(2.7, 4.57)]
    assert style_b._split_at(pieces, 2.58) == 1 and len(pieces) == 2
    pieces = [edl.Piece(0.0, 6.0)]
    assert style_b._split_at(pieces, 2.58) == 1 and [(p.a, p.b) for p in pieces] == [(0.0, 2.58), (2.58, 6.0)]


def test_failing_op_is_dropped_not_whole_layer(monkeypatch):
    """Một phép phạm luật: bỏ đúng phép đó, giữ các phép còn lại (trước đây bỏ cả lớp B)."""
    end, segs, cuts, facts, mot = _kaggle()
    real = style_b.check_rules

    def recap_breaks(plan, *a):
        return ["R5 khúc recap ở giây 29.3 không còn trong video chính"] if any(p.tag == "recap" for p in plan.pieces) \
            else real(plan, *a)
    monkeypatch.setattr(style_b, "check_rules", recap_breaks)
    plan, why = style_b.plan_strong(end, segs, cuts, mot, 1.0, "auto", facts)
    assert plan.level == "strong" and why.startswith("bỏ phép tóm tắt (R5")
    tags = [p.tag for p in plan.pieces]
    assert "recap" not in tags and tags.count("montage") >= 3

    monkeypatch.setattr(style_b, "check_rules", lambda plan, *a: ["R6 quá nhiều khúc"])
    plan, why = style_b.plan_strong(end, segs, cuts, mot, 1.0, "auto", facts)
    assert plan.level == "light" and "chỉ áp lớp A" in why                   # bỏ hết vẫn phạm: lớp A như cũ


def test_reduplicated_vua_not_coconut():
    assert vocab.fix_common("nó sâu dừa dừa thôi nha") == "nó sôi vừa vừa thôi nha"
