"""Bản 47: soát video «mực hấp gừng» job 4 (17/11/2026, Kaggle, dựng mạnh, màn kết mặc định).

- thẻ TÓM TẮT CÁCH LÀM chiếu 2 giây mà không có lời tóm tắt: khúc tóm tắt chèn GIỮA câu Kaggle gộp "Rất là ngon luôn
  nè Vậy là đã xong..." -> câu đó phủ lên khúc tóm tắt, AI viết một câu đọc đè;
- hai thẻ »» 4 PHÚT SAU cho cùng một lần luộc ("luộc khoảng 4 phút" ... "sau khi luộc khoảng tầm 4 phút");
- gốc "dầu mè khoảng 3-4 giọt", lời đọc "3-4 muỗng dầu mè": bộ kiểm chỉ so con số;
- câu chốt đọc đè lên màn kết (giọng được xếp tới hết cả màn kết), âm hiệu chen vào chữ "nha" cuối;
- 8/26 câu kết bằng "nha"."""
import json
import os

import ingredients
import prompt_engine
import queue_manager
from rmvideo.app.flow import style, style_b
from rmvideo.app.flow.remake import Remake
from rmvideo.domain import recipe
from rmvideo.media import edl

KAGGLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "muc_hap_gung_kaggle.json")


def _kaggle(plate_end=None):
    d = json.load(open(KAGGLE, encoding="utf-8"))
    if plate_end is not None:
        for s in d["facts"]["steps"]:
            if s["kind"] == "plate":
                s["t1"] = plate_end
    return d["end"], [tuple(s) for s in d["segs"]], d["cuts"], recipe.Facts.from_dict(d["facts"]), d["motion"]


def _slots(plan, segs):
    """Như remake.restyle (dựng mạnh): lời gốc theo trục mới + lời khúc phụ."""
    mapped = [(round(plan.to_out(a), 2), round(plan.to_out(b), 2), t) for a, b, t in segs
              if plan.to_out(b) - plan.to_out(a) >= max(0.05, (b - a) * plan.stretch * 0.95)]
    side = [(x["t"], round(x["t"] + x["dur"], 2), x["text"]) for x in plan.texts]
    return sorted(mapped + side)


def test_recap_never_splits_a_sentence():
    """AI ghi bước bày món hết 188,9s (giữa câu 188,2-192,0s), 194s, hết video: khúc tóm tắt luôn đứng riêng, trước
    lời chào kết, không câu nào phủ lên nó."""
    for plate_end in (186.98, 188.9, 190.5, 194.0, 196.5):
        end, segs, cuts, facts, mot = _kaggle(plate_end)
        plan, why = style_b.plan_strong(end, segs, cuts, mot, 1.0, "auto", facts)
        assert why == "" and plan.level == "strong", (plate_end, why)
        slots = _slots(plan, segs)
        k = next(i for i, (_, _, t) in enumerate(slots) if t.startswith(style_b.RECAP_MARK))
        a, b, _ = slots[k]
        for i, (x, y, t) in enumerate(slots):
            if i != k:
                assert y <= a + 0.05 or x >= b - 0.05, (plate_end, t, x, y, a, b)
        assert "Vậy là đã xong" in " ".join(t for _, _, t in slots[k + 1:])


def test_sentence_start_steps_back_over_joined_sentences():
    segs = [(0.0, 2.0, "a"), (3.0, 5.0, "b"), (5.0, 8.0, "c")]
    assert style_b._sentence_start(segs, 6.0) == 2.6           # c nối liền b: lùi về trước b, giữa khoảng lặng
    assert style_b._sentence_start(segs, 2.5) == 2.5           # đang ở khoảng lặng: giữ nguyên
    assert style_b._sentence_start(segs, 0.5) == 0.0


def test_rule_r8_catches_side_piece_inside_sentence():
    segs = [(0.0, 4.0, "câu dài bị cắt đôi"), (5.0, 6.0, "câu sau")]
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 2.0), edl.Piece(10.0, 10.7, role="side", tag="recap"),
                                edl.Piece(2.0, 6.0)], stretch=1.0, level="strong")
    errs = style_b.check_rules(plan, segs, [], [False, False])
    assert any(e.startswith("R8 khúc recap chèn giữa câu") for e in errs)
    sus = next(ops for key, ops in style_b._SUSPECTS if errs[-1].startswith(key))
    assert sus == ("B5",)


def test_same_wait_labelled_once():
    segs = [(0.0, 2.0, "luộc khoảng 4 phút"), (6.0, 8.0, "sau khi luộc khoảng tầm 4 phút là bung ra")]
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 2.2), edl.Piece(2.2, 5.0, speed=4.0, tag="skip"), edl.Piece(5.0, 8.2),
                                edl.Piece(8.2, 12.0, speed=4.0, tag="skip"), edl.Piece(12.0, 14.0)],
                        stretch=1.0, level="light")
    style.finalize(plan, segs, "auto", 14.0)
    labels = [o.text for o in plan.overlays if o.kind == "skip"]
    assert labels == ["»» 4 PHÚT SAU", "»»"]


def test_unit_change_is_caught_and_only_that_phrase_dropped():
    o = ("rồi anh chị luộc cho mình là khoảng 4 phút. sau khi ăn ra thì anh chị cho mình 1 ít dầu mè lên, khoảng "
         "3-4 giọt để cho thơm. bỏ trong này là nửa muỗng bột ngọt")
    n = "Giờ mình luộc khoảng 4 phút nha. Mọi người rưới thêm 3-4 muỗng dầu mè cho thơm. Nêm nửa muỗng bột ngọt."
    missing, added = ingredients.check_numbers(o, n)
    assert missing == ["4 giọt"] and added == ["4 muỗng"]
    assert ingredients.drop_numbers(n, set(added)) == \
        "Giờ mình luộc khoảng 4 phút nha. Mọi người rưới thêm dầu mè cho thơm. Nêm nửa muỗng bột ngọt."
    assert ingredients.check_numbers(o, n.replace("muỗng dầu", "giọt dầu")) == ([], [])
    assert ingredients.check_numbers("1 muỗng canh nước mắm", "1 muỗng nước mắm") == ([], [])   # cùng loại muỗng
    assert ingredients.check_numbers("2 cái trứng", "2 quả trứng") == ([], [])                  # đơn vị đếm
    assert ingredients.check_numbers("luộc 4 phút", "luộc 4 giờ") == (["4 phút"], ["4 giờ"])
    assert ingredients.drop_numbers("Luộc khoảng 4 phút rồi thêm 1 muỗng dầu.", {"1"}) == "Luộc khoảng 4 phút rồi thêm dầu."


def test_narration_stops_before_end_card():
    r = Remake.__new__(Remake)
    r.out_dur, r.tail = 188.39, 2.2
    assert r.content_end == 186.19
    r.tail = 0.0
    assert r.content_end == 188.39


def test_particles_vary():
    got = {0: "Giờ mình luộc khoảng 4 phút nha.", 1: "Mực bung ra đẹp lắm nha.", 2: "Vớt ra dĩa nha.",
           3: "Nhìn ngon ghê nè.", 4: "Đẹp nha."}
    prompt_engine.vary_particles(got, [0, 1, 2, 3, 4])
    assert got == {0: "Giờ mình luộc khoảng 4 phút nha.", 1: "Mực bung ra đẹp lắm.", 2: "Vớt ra dĩa nha.",
                   3: "Nhìn ngon ghê nè.", 4: "Đẹp nha."}


def test_timing_names_ai_phases():
    assert queue_manager.phase_of("AI đọc công thức + đối chiếu định lượng với lời gốc") == "AI hiểu món"
    assert queue_manager.phase_of("AI chọn cảnh món đã xong (ảnh bìa + đoạn mở đầu)") == "AI chọn cảnh mở đầu"
    assert queue_manager.phase_of("Lập kế hoạch dựng (nhịp, khung hình)") == "Lập kế hoạch dựng"
    assert queue_manager.phase_of("Bản ngắn: AI viết lời 12 đoạn") == "AI viết kịch bản"
