"""Bản 1.0.1 — sửa theo soát video job 1 (video mực, bản 1.0.0): thẻ MẸO đè logo, bản ngắn mất câu hook, lời AI lặp
ý, đoạn tĩnh đơn điệu, câu chốt nối dính + tên kênh bị cắt đôi trong phụ đề, phụ đề câu hook chồng chữ hook."""
import re

import pytest

import config
import prompt_engine as pe
import subs
from rmvideo.app import brand
from rmvideo.app.flow import short, style
from rmvideo.domain import recipe as r
from rmvideo.media import edl, filtergraph, renderer


# ---------------- thẻ chữ phía trên không chen vào vùng logo ----------------

def _png(path, w, h):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
                     + b"\x08\x06\x00\x00\x00")
    return str(path)


def test_logo_bottom_follows_logo_shape(tmp_path):
    sq = _png(tmp_path / "sq.png", 512, 512)
    wide = _png(tmp_path / "wide.png", 512, 256)
    assert renderer.logo_bottom(1080, 1920, sq) == pytest.approx(0.09 + 172 / 1920, abs=1e-3)   # ~0.18
    assert renderer.logo_bottom(1080, 1920, wide) < renderer.logo_bottom(1080, 1920, sq)        # logo dẹt: đáy cao hơn
    assert renderer.logo_bottom(1080, 1920, None) == 0.0
    assert renderer._png_ratio(str(tmp_path / "khong_co.png")) == 1.0


def _card_y(fc: str, n: int = 0) -> float:
    return float(re.findall(r":y=h\*([\d.]+)", fc)[n])


def test_top_cards_go_below_logo(tmp_path):
    cards = [edl.Overlay(18.0, 4.0, "tip", "MẸO: Không cần rã đông mực đông đá trước khi luộc để mực không bị teo."),
             edl.Overlay(27.0, 1.5, "skip", "⏩ 4 PHÚT SAU"), edl.Overlay(3.0, 1.0, "ingredient", "Mực")]
    with_logo = renderer.overlay_filter(cards, 1080, 1920, 0.0, str(tmp_path), below=0.18)
    assert _card_y(with_logo, 0) >= 0.19 and _card_y(with_logo, 1) >= 0.19      # mẹo, tua nhanh: dưới logo
    assert _card_y(with_logo, 2) == pytest.approx(0.62)                           # thẻ nguyên liệu: chỗ cũ
    no_logo = renderer.overlay_filter(cards, 1080, 1920, 0.0, str(tmp_path))
    assert _card_y(no_logo, 0) == pytest.approx(0.12) and _card_y(no_logo, 1) == pytest.approx(0.16)


# ---------------- bản ngắn giữ câu hook của nguồn ----------------

def _src_with_hook(shift=3.0):
    """Như tests/test_short.py: video dài nhiều bước, nhưng câu đầu (0,2-2,6s) là đầu bếp khoe món xong."""
    from test_short import _src
    segs, f, end = _src()
    segs = [(0.2, 2.6, "Thịt kho kiểu này ngon hết sảy luôn")] + [(a + shift, b + shift, t) for a, b, t in segs]
    steps = [r.Step("intro", 0, 0, t0=0.0, t1=2.7)] + [
        r.Step(s.kind, 0, 0, s.text, t0=s.t0 + shift, t1=s.t1 + shift) for s in f.steps if s.kind != "intro"]
    items = [r.Item(it.name, it.qty, it.unit, "ok", seen_at=it.seen_at + shift) for it in f.items]
    return segs, r.Facts(is_recipe=True, dish=f.dish, items=items, steps=steps), end + shift


def test_short_opens_with_source_hook_then_roll_call():
    segs, f, end = _src_with_hook()
    plan, why = short.plan_short(end, segs, f, short.budget(0.0))
    assert why == "" and plan.out_dur() <= short.budget(0.0) + 1e-6
    first = plan.pieces[0]
    assert first.role == "main" and first.a < 0.05 and first.b == pytest.approx(2.6)          # câu hook đi đầu
    assert [p.tag for p in plan.pieces[1:4]] == ["montage"] * 3                               # rồi mới điểm danh
    mt = next(x for x in plan.texts if x["kind"] == "montage")
    assert mt["t"] == pytest.approx(2.6) and all(o.t >= 2.6 for o in plan.overlays)
    assert renderer.hook_title_end(0.0, plan) == pytest.approx(2.6)        # chữ hook tắt đúng lúc thẻ «500 g…» hiện
    assert "mở bằng câu hook" in plan.summary
    kept = plan.map_segments(segs)
    assert kept[0][2] == "Thịt kho kiểu này ngon hết sảy luôn" and kept[0][0] == pytest.approx(0.2)


def test_short_with_teaser_keeps_old_layout():
    segs, f, end = _src_with_hook()
    plan, _ = short.plan_short(end, segs, f, short.budget(3.0), hook=False)  # có đoạn mở đầu: đó là hook rồi
    assert plan.pieces[0].tag == "montage"


# ---------------- lời AI lặp ý ----------------

JOB1 = ["Khỏi cần rã đá đâu nha", "Giờ mình bắc nồi nước sôi sùng sục rồi thả trực tiếp vào",
        "Đoạn này mọi người để lửa riu riu và nhớ là đừng đảo nha", "Cứ để lửa riu riu",
        "Luộc chừng 4 phút là con mực tự tan đông", "Luộc kiểu này mực nguyên con không rách da",
        "Bí quyết nhà hàng hải sản đắt khách đó, không rách da", "Đa số mọi người hay luộc khoảng chừng 4 phút",
        "Nhìn mẻ mực tươi ngon ghê chưa", "Giờ mực tươi ngon ghê chưa"]


def test_repeated_ideas_flags_filler():
    got = pe.repeated_ideas(list(enumerate(JOB1, 1)))
    assert got == {4: "lửa riu riu", 7: "không rách da", 8: "4 phút", 10: "mực tươi ngon"}


def test_repeated_ideas_ignores_dish_names_and_paraphrase():
    items = [(1, "Làm món mực hấp gừng giòn ngọt"), (2, "Để món mực hấp gừng ngon thì dùng mực đông đá"),
             (3, "Bắc nồi nước sôi"), (4, "Thả mực vô nồi nước sôi"), (5, "Da nguyên vẹn không hề rách miếng nào"),
             (6, "Luộc chừng bốn phút"), (7, "Thêm hai phút cho thấm")]
    assert pe.repeated_ideas(items) == {}
    assert pe.repeated_ideas([(1, "Luộc chừng bốn phút"), (2, "đợi bốn phút nữa")]) == {2: "bốn phút"}


# ---------------- cảnh tĩnh: đổi khung rõ và dày hơn ----------------

def test_static_shots_get_stronger_denser_zooms():
    # 60 giây người gốc nói liên tục (không có khoảng lặng để tua), hình đứng yên 10-50s
    segs = [(t + 0.1, t + 1.9, "nói tiếp nè") for t in range(0, 60, 2)]
    mot = [5.0] * 20 + [0.2] * 80 + [5.0] * 20                     # 2 điểm / giây
    plan = style.plan_light(60.0, segs, [], mot)
    calm = [p for p in plan.pieces if p.a >= 10.5 and p.b <= 49.5]
    assert calm and max(p.b - p.a for p in calm) <= style.SHOT_MAX_STATIC + 0.05
    assert {p.zoom for p in calm} <= set(style.ZOOMS_STATIC) and max(p.zoom for p in calm) >= 1.25
    busy = [p for p in plan.pieces if p.b <= 9.5 and p.push == 0]
    assert all(p.zoom in style.ZOOMS for p in busy)                 # cảnh có chuyển động: như cũ
    assert not plan.validate()


# ---------------- câu chốt + phụ đề tên kênh + phụ đề câu hook ----------------

@pytest.fixture
def kit(monkeypatch):
    monkeypatch.setattr(config, "BRAND_CLOSE", "Đong đủ là ngon, cả nhà nấu thử nha")
    monkeypatch.setattr(config, "BRAND_NAME", "Bếp Đong Đủ")


def test_close_is_its_own_sentence_without_double_vocative(kit):
    segs = [{"text": "Làm ngay món mực hấp gừng đơn giản thơm ngon này nha cả nhà", "ost": "new"}]
    assert brand.add_close(segs)
    assert segs[0]["text"] == "Làm ngay món mực hấp gừng đơn giản thơm ngon này nha. Đong đủ là ngon, cả nhà nấu thử nha."
    segs = [{"text": "Ngon xuất sắc luôn cả nhà ơi!", "ost": "new"}]
    brand.add_close(segs)
    assert segs[0]["text"] == "Ngon xuất sắc luôn! Đong đủ là ngon, cả nhà nấu thử nha."
    segs = [{"text": "Cả nhà", "ost": "new"}]                      # chỉ còn lời gọi: giữ, không để câu cụt
    brand.add_close(segs)
    assert segs[0]["text"].startswith("Cả nhà.")


def test_subtitles_never_split_brand_name(kit):
    words = [(i * 0.4, i * 0.4 + 0.35, w) for i, w in enumerate("thơm ngon này nha cả nhà Đong đủ là ngon,".split())]
    lines = [" ".join(x[2] for x in ln) for _, _, ln in subs.group_words(words)]
    assert not any(ln.endswith("Đong") for ln in lines) and any(ln.startswith("Đong đủ") for ln in lines)


def test_hook_subtitle_hidden_while_hook_title_shows(monkeypatch):
    monkeypatch.setattr(config, "BRAND_INTRO", "1")
    segs = [{"start": 0.0, "slot": 2.4, "text": "Mực đông hấp kiểu này ngon ghê"},
            {"start": 2.4, "slot": 4.0, "text": "Chuẩn bị mực"}]
    assert filtergraph.subtitle_texts(segs, None, title_shown=True) == ["", "Chuẩn bị mực"]
    assert filtergraph.subtitle_texts(segs, None) == [s["text"] for s in segs]          # không có chữ hook to
    long_hook = [{"start": 0.0, "slot": 6.0, "text": "Câu mở đầu dài hơn lúc chữ hook hiện"}]
    assert filtergraph.subtitle_texts(long_hook, None, title_shown=True) == [long_hook[0]["text"]]
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    assert filtergraph.subtitle_texts(segs, {"dur": 3.0}, title_shown=True)[0] == segs[0]["text"]
