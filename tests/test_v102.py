"""Bản 1.0.2 — sửa theo soát video job 2 (video mực, bản 1.0.1): bản ngắn đọc «thêm nửa muỗng hạt nêm» hai lần, thẻ
điểm danh bản ngắn chạy trước giọng, «nhìn dĩa mực» khi mực còn trong nồi (bản ngắn bỏ câu «trút ra dĩa»), «lúc luộc
gừng» khi chưa cho gừng, chữ «thử nha.» cuối video chớp 0,1 giây, «thơm lừng» lặp ở hai đoạn liền nhau."""
import asyncio
from types import SimpleNamespace

import pytest

import prompt_engine as pe
from rmvideo.app import assemble
from rmvideo.app.flow import remake, short, style_b
from rmvideo.domain import recipe as r
from rmvideo.media import edl, voiceplan
from test_ban49 import _fake_ai
from test_v101 import _src_with_hook

HAT_NEM = (8.5, 12.0, "thêm nửa muỗng hạt nêm nữa")


# ---------------- nói trước định lượng của đoạn sau ----------------

def test_qty_pairs_read_amount_next_to_item():
    got = pe.qty_pairs("Rồi nêm nửa muỗng bột ngọt, hạt nêm nửa muỗng, 2 muỗng canh nước mắm, luộc mực 4 phút")
    assert [q for q, _ in got] == ["0.5 muỗng", "0.5 muỗng", "2 muỗng canh"]          # «4 phút» là thời gian
    assert pe.qty_pairs("Giờ mình thả mực vô nồi nước sôi") == []


def test_anticipated_amount_is_caught_both_ways():
    # gốc tách «…nửa muỗng đường» | «thêm nửa muỗng hạt nêm nữa»: AI nói luôn hạt nêm ở đoạn trước
    ahead = [(1, "Rồi mình nêm vô nồi nửa muỗng bột ngọt, nửa muỗng đường",
              "Giờ mình thả mực vô nồi, rồi nêm nửa muỗng bột ngọt, nửa muỗng đường, thêm nửa muỗng hạt nêm."),
             (2, HAT_NEM[2], "Thêm nửa muỗng hạt nêm nữa, đun lửa riu riu cho ngấm.")]
    got = pe.anticipated_items(ahead)
    assert list(got) == [1] and pe._qty_vi(got[1]) == "0.5 muỗng hạt nêm"
    assert pe.drop_anticipated(ahead[0][2], got[1]) == \
        "Giờ mình thả mực vô nồi, rồi nêm nửa muỗng bột ngọt, nửa muỗng đường."
    # gốc nói đủ ở một đoạn, AI nói lại ở đoạn sau
    again = [(1, "Rồi mình nêm vô nồi nửa muỗng bột ngọt, thêm nửa muỗng hạt nêm nữa", ahead[0][2]),
             (2, "Đoạn này mọi người để lửa riu riu và nhớ là đừng đảo nha", ahead[1][2])]
    got = pe.anticipated_items(again)
    assert list(got) == [2] and pe.drop_anticipated(again[1][2], got[2]) == "Đun lửa riu riu cho ngấm."


def test_amount_said_once_or_twice_by_source_is_fine():
    once = [(1, "Rồi mình nêm vô nồi nửa muỗng bột ngọt, nửa muỗng đường, thêm nửa muỗng hạt nêm nữa",
             "Thả thẳng mực đông vào nước sôi, rồi nêm thêm nửa muỗng bột ngọt."),
            (2, "", "Tiếp tục cho vô nửa muỗng đường, thêm nửa muỗng hạt nêm nữa nè.")]
    assert pe.anticipated_items(once) == {}                       # chia lại chỗ nói nhưng mỗi thứ vẫn một lần
    twice = [(1, "nêm nửa muỗng hạt nêm", "Nêm nửa muỗng hạt nêm."), (2, "thêm nửa muỗng hạt nêm nữa",
                                                                    "Rồi thêm nửa muỗng hạt nêm nữa cho đậm.")]
    assert pe.anticipated_items(twice) == {}                      # gốc cũng nêm hai lần
    mixed = "Nêm nửa muỗng đường với nửa muỗng hạt nêm."           # vế còn nguyên liệu khác: không bỏ
    assert pe.drop_anticipated(mixed, {("0.5 muỗng", next(iter(pe.ingredients.find("hạt nêm"))))}) == mixed


_SEASON = [(0.0, 2.0, "Mực đông hấp kiểu này ngon ghê"),
           (2.5, 8.0, "Rồi mình nêm vô nồi nửa muỗng bột ngọt, nửa muỗng đường"), HAT_NEM,
           (12.5, 18.0, "Đoạn này mọi người để lửa riu riu và nhớ là đừng đảo nha")]
_SEASON_NEW = ["Mực đông hấp kiểu này giòn ngọt ghê luôn á cả nhà.",
               "Giờ mình nêm nửa muỗng bột ngọt, nửa muỗng đường, thêm nửa muỗng hạt nêm cho đậm đà.",
               "Thêm nửa muỗng hạt nêm nữa, khuấy nhẹ cho tan đều.",
               "Để lửa liu riu thôi, mọi người nhớ là không đảo mạnh tay kẻo rách mực nha."]


def test_anticipated_amount_is_rewritten(monkeypatch):
    good = list(_SEASON_NEW)
    good[1] = "Giờ mình nêm nửa muỗng bột ngọt với nửa muỗng đường, khuấy cho tan đều."
    seen = _fake_ai(monkeypatch, [_SEASON_NEW, good])
    out = asyncio.run(pe.generate_timed("", "", "cook", _SEASON, 19.0))
    assert len(seen) == 2 and "nói TRƯỚC định lượng" in seen[1] and "0.5 muỗng hạt nêm" in seen[1]
    assert out["segments"][1]["text"] == good[1]


def test_anticipated_clause_dropped_when_ai_insists(monkeypatch):
    seen = _fake_ai(monkeypatch, [_SEASON_NEW] * 3)
    out = asyncio.run(pe.generate_timed("", "", "cook", _SEASON, 19.0))
    assert len(seen) == 3
    assert out["segments"][1]["text"] == "Giờ mình nêm nửa muỗng bột ngọt, nửa muỗng đường."
    assert out["segments"][2]["text"] == _SEASON_NEW[2]


# ---------------- thẻ điểm danh bản ngắn: đủ giờ cho giọng ----------------

def test_short_roll_call_cards_get_voice_time():
    segs, f, end = _src_with_hook()
    plan, _ = short.plan_short(end, segs, f, short.budget(0.0))
    cards = [o for o in plan.overlays if o.kind == "ingredient"]
    assert len(cards) >= 3 and short.MONTAGE_LEN == style_b.MONTAGE_LEN
    assert all(b.t - a.t == pytest.approx(style_b.MONTAGE_LEN * plan.stretch, abs=0.011) for a, b in zip(cards, cards[1:]))
    assert all(c.dur >= 0.95 for c in cards)        # mỗi thẻ ~1 giây (trước: 0,8 giây, giọng đọc không kịp)
    side = [p for p in plan.pieces if p.tag == "montage"]
    assert all(p.b - p.a == pytest.approx(style_b.MONTAGE_LEN) for p in side)


def test_short_roll_call_shrinks_instead_of_vanishing():
    """1,2 giây mỗi món cần thêm chỗ: thiếu chỗ thì bớt món ít quan trọng (còn ≥ 3), không bỏ cả khúc điểm danh."""
    segs, f, end = _src_with_hook()
    extra = [r.Item("hành tây", None, "", "ok", seen_at=60.0), r.Item("gừng", None, "", "ok", seen_at=75.0)]
    f5 = r.Facts(is_recipe=True, dish=f.dish, items=list(f.items) + extra, steps=f.steps)
    sizes: dict = {}
    for b in range(450, 600):
        plan, _ = short.plan_short(end, segs, f5, b / 10)
        labels = [o.text for o in plan.overlays if o.kind == "ingredient"] if plan else []
        sizes.setdefault(len(labels), labels)
    assert {3, 4, 5} <= set(sizes)
    assert sizes[3] == ["500 g thịt ba chỉ", "5 tép tỏi", "2 muỗng canh nước mắm"]   # giữ món có định lượng


# ---------------- «dĩa» khi món còn trong nồi: mốc lên dĩa theo thời gian ----------------

_PLATE = [(0.0, 2.0, "Mực đông hấp kiểu này ngon ghê"),
          (3.0, 9.0, "Sắp chín rồi thì rưới chút dầu mè vô cho thơm"),
          (9.5, 14.0, "Cầm nguyên cái râu vẫn thấy chắc thịt"),
          (14.5, 20.0, "Món mực hấp này ngon xuất sắc luôn cả nhà ơi")]
_PLATE_NEW = ["Mực đông hấp kiểu này giòn ngọt ghê luôn á cả nhà.",
              "Sắp chín thì mình rưới thêm dầu mè, nhìn dĩa mực bóng bẩy cực kỳ.",
              "Cầm nguyên râu mực lên thấy chín đều, tươi rói luôn nè.",
              "Lên dĩa rồi nè, mực hấp gừng thơm phức, chấm muối ớt là hết sảy luôn."]


def test_plate_time_catches_dish_word_without_plating_line(monkeypatch):
    seen = _fake_ai(monkeypatch, [_PLATE_NEW] * 3)
    out = asyncio.run(pe.generate_timed("", "", "cook", _PLATE, 21.0, plate_at=14.5))
    assert len(seen) == 3 and "CHƯA được bày ra dĩa" in seen[1]
    assert out["segments"][1]["text"] == "Sắp chín thì mình rưới thêm dầu mè, nhìn mẻ mực bóng bẩy cực kỳ."
    assert out["segments"][3]["text"] == _PLATE_NEW[3]            # đoạn từ lúc lên dĩa: được nói «dĩa»
    seen = _fake_ai(monkeypatch, [_PLATE_NEW] * 3)
    out = asyncio.run(pe.generate_timed("", "", "cook", _PLATE, 21.0))   # không biết mốc: như cũ, không bắt
    assert not any("CHƯA được bày ra dĩa" in p for p in seen) and out["segments"][1]["text"] == _PLATE_NEW[1]


def test_plate_time_from_source_maps_into_short_cut():
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 10.0), edl.Piece(30.0, 40.0)], level="short")
    said = SimpleNamespace(raw_segs=[(1.0, 2.0, "Trút ra dĩa nè nhìn ngon chưa"), (20.0, 21.0, "Mình trút ra dĩa")],
                           facts=None, edit=plan, stretch=1.0)
    assert remake.Remake.plate_kw(said, 0.0) == {"plate_at": 10.0}     # câu ở quãng bỏ: đầu khúc kế tiếp; hook bỏ qua
    assert remake.Remake.plate_kw(said, 3.0) == {"plate_at": 13.0}     # + đoạn mở đầu
    steps = [r.Step("intro", 0, 0, t0=0.0, t1=2.0), r.Step("plate", 0, 0, t0=33.0, t1=36.0)]
    seen = SimpleNamespace(raw_segs=[(5.0, 6.0, "Ngon quá")], facts=r.Facts(is_recipe=True, steps=steps), edit=plan,
                           stretch=1.0)
    assert remake.Remake.plate_kw(seen, 0.0) == {"plate_at": 13.0}     # không ai nói: bước «bày món» AI xem thấy
    none = SimpleNamespace(raw_segs=[(5.0, 6.0, "Ngon quá")], facts=None, edit=None, stretch=1.25)
    assert remake.Remake.plate_kw(none, 0.0) == {}
    # video mực: «nó sẽ được đổ ra» (máy nghe nhầm «nở ra») lúc đang luộc, bước bày món thật ở sau: lấy mốc sau
    both = SimpleNamespace(raw_segs=[(5.0, 6.0, "cái mực của mình nó sẽ được đổ ra")], facts=r.Facts(is_recipe=True,
                           steps=steps), edit=plan, stretch=1.0)
    assert remake.Remake.plate_kw(both, 0.0) == {"plate_at": 13.0}


def test_plate_time_wins_over_misheard_plating_line():
    items = [(1, "cái mực của mình nó sẽ được đổ ra", "Mực bung đều, nhìn mê ghê."),
             (2, "Sắp chín rồi thì rưới chút dầu mè vô", "Sắp chín thì rưới dầu mè, nhìn dĩa mực bóng bẩy."),
             (3, "Giờ mình trút ra dĩa", "Trút hết ra dĩa cho gọn nè.")]
    assert pe.early_plate(items) == {}                            # chỉ có câu gốc khớp nhầm ở đoạn 1: lọt
    assert pe.early_plate(items, at_k=3) == {2: "dĩa mực bóng"}    # mốc theo thời gian (đoạn 3) đến sau: bắt được


# ---------------- nhắc nguyên liệu khi chưa cho vào ----------------

def test_item_named_long_before_it_goes_in():
    first = pe.first_mentions([(0.0, "Mực hấp gừng kiểu này ngon ghê"), (3.0, "Giờ mình thả mực vô nồi nước sôi"),
                               (40.0, "Tới đây mình cho hành tây, gừng, ớt sừng vào")])
    g = next(iter(pe.ingredients.find("gừng")))
    assert first[g] == 40.0                                       # «mực hấp gừng» là tên món, không tính
    items = [(1, 9.5, "Rồi mình đun lửa liu riu, lưu ý lúc luộc gừng thế này mọi người đừng đảo nha."),
             (2, 12.0, "Hôm nay mình làm món mực hấp gừng ngon chuẩn vị."),
             (3, 14.0, "Trong lúc chờ thì thái sẵn gừng với ớt sừng nha."),
             (4, 30.0, "Gần được rồi, chuẩn bị cho gừng vô nha.")]
    assert pe.early_items(items, first) == {1: ["gừng"]}          # tên món, sơ chế, sát lúc cho vào: không bắt
    assert pe.drop_early(items[0][2], ["gừng"]) == \
        "Rồi mình đun lửa liu riu, lưu ý lúc luộc thế này mọi người đừng đảo nha."
    assert pe.drop_early("Thơm mùi gừng ghê.", ["gừng"]) == "Thơm mùi gừng ghê."     # cách nói khác: để AI viết lại


def test_fragrant_is_not_pineapple():
    dua = next(iter(pe.ingredients.find("trái thơm")))
    first = pe.first_mentions([(0.0, "Giờ mình thả mực vô nồi nước sôi"), (151.5, "để cho cái mực nó được thơm")])
    assert dua not in first                                       # «được thơm»: mùi thơm, không phải quả thơm (dứa)
    items = [(1, 60.0, "Rưới dầu mè cho món thêm thơm."), (2, 70.0, "Mực vừa giòn vừa thơm."),
             (3, 80.0, "Nhìn mê ghê, thơm nức mũi luôn.")]
    assert pe.early_items(items, {dua: 151.5}) == {}
    assert [m[2] for m in pe._item_mentions("cho nửa trái thơm vô xào")] == [pe.ingredients.find("trái thơm")]


def test_dish_name_tail_chains():
    tails = [(m[2], m[3]) for m in pe._item_mentions("món mực xào sả ớt với thịt ba chỉ kho tiêu")]
    assert [t for _, t in tails] == [False, True, True, False, True]


_GINGER = [(0.0, 2.5, "Mực hấp gừng kiểu này ngon ghê"), (3.0, 9.0, "Giờ mình thả mực vô nồi nước sôi"),
           (9.5, 15.0, "Đoạn này mọi người để lửa riu riu và nhớ là đừng đảo nha"),
           (15.5, 25.0, "Luộc chừng 4 phút là mực tự tan đông"),
           (40.0, 46.0, "Tới đây mình cho hành tây, gừng, ớt sừng vào cho thơm")]
_GINGER_NEW = ["Mực hấp gừng giòn ngọt, ai ăn cũng mê luôn á.",
               "Giờ mình bắc nồi nước sôi sùng sục rồi thả thẳng mực đông vô luôn nè.",
               "Rồi mình đun lửa liu riu, lưu ý lúc luộc gừng thế này mọi người đừng đảo nha.",
               "Chừng bốn phút là mực tự rã đông, thân mực căng tròn đẹp mắt ghê chưa nè cả nhà.",
               "Giờ cho hành tây, gừng với ớt sừng vô, thơm nức cả gian bếp luôn."]


def test_early_item_is_rewritten_then_dropped(monkeypatch):
    good = list(_GINGER_NEW)
    good[2] = "Rồi mình đun lửa liu riu, lưu ý lúc luộc mực thế này mọi người đừng đảo nha."
    seen = _fake_ai(monkeypatch, [_GINGER_NEW, good])
    out = asyncio.run(pe.generate_timed("", "", "cook", _GINGER, 48.0))
    assert len(seen) == 2 and "nhắc nguyên liệu CHƯA cho vào" in seen[1] and "nhắc «gừng» quá sớm" in seen[1]
    assert out["segments"][2]["text"] == good[2]
    seen = _fake_ai(monkeypatch, [_GINGER_NEW] * 3)
    out = asyncio.run(pe.generate_timed("", "", "cook", _GINGER, 48.0))
    assert out["segments"][2]["text"] == "Rồi mình đun lửa liu riu, lưu ý lúc luộc thế này mọi người đừng đảo nha."


def test_batches_share_first_mentions(monkeypatch):
    got: dict = {}

    async def fake(title, desc, mode, part, end, images_for=None, extra="", intro=True, **kw):
        got.setdefault("kw", []).append(kw)
        return {"segments": [{"i": k, "start": s[0], "slot": 1.0, "text": f"lời {s[0]:.0f}", "orig": s[2]}
                             for k, s in enumerate(part)], "title": "t", "caption": "c", "hashtags": "#a"}
    monkeypatch.setattr(pe, "generate_timed", fake)
    segs = [(2.0 * i, 2.0 * i + 1.0, f"gốc số {i}.") for i in range(59)] + [(118.0, 119.0, "cho gừng vào.")]
    out = asyncio.run(pe.generate_timed_any("", "", "cook", segs, 121.0, plate_at=100.0))
    assert out is not None and len(got["kw"]) == 3
    g = next(iter(pe.ingredients.find("gừng")))
    assert all(kw["first_said"] == {g: 118.0} and kw["plate_at"] == 100.0 for kw in got["kw"])


# ---------------- cụm 2 chữ lặp sát nhau ----------------

def test_pair_repeated_next_door_is_caught():
    items = list(enumerate(["Ăn vừa đỡ ngán lại thơm lừng cả gian bếp.",
                            "Rồi rưới chút dầu mè lên cho món mực hấp gừng thơm lừng mũi.",
                            "Xong xuôi hết rồi thì mình nhấc vợt ra ngoài nha."], 1))
    assert pe.repeated_ideas(items) == {2: "thơm lừng"}
    far = items[:1] + [(2, "Nước trong nồi sôi lăn tăn."), (3, "Mực bung ra tròn trịa."), (4, "Vớt ra để ráo.")] + \
        [(5, items[1][1])]
    assert pe.repeated_ideas(far) == {}                            # cách xa: cụm 2 chữ nhắc lại là thường
    talk = list(enumerate(["Mọi người thấy chưa, cực kỳ hấp dẫn.", "Mọi người nhớ đừng đảo, cực kỳ quan trọng."], 1))
    assert pe.repeated_ideas(talk) == {}                           # chữ gọi / chữ nhấn
    qty = list(enumerate(["Rồi nêm thêm nửa muỗng bột ngọt.", "Tiếp tục cho vô nửa muỗng đường, thêm nửa muỗng hạt nêm."]))
    assert pe.repeated_ideas(qty) == {}                            # định lượng không phải ý


# ---------------- câu cuối: lùi xa hơn cho kịp màn kết, phụ đề theo hết giọng ----------------

def test_last_line_starts_earlier_to_end_before_outro():
    segs = [{"start": 168.1, "slot": 3.6, "text": "Làm mực hấp gừng tại nhà xong rồi đó."},
            {"start": 171.7, "slot": 3.06, "text": "Cho mấy điểm nè. Đong đủ là ngon, cả nhà nấu thử nha."}]
    plan, _ = voiceplan.plan_voice([2.5, 4.4], segs, 174.76)
    assert plan[1]["start"] == pytest.approx(170.72) and plan[1]["end"] <= 174.71 + 1e-6
    mid = [dict(segs[0]), dict(segs[1]), {"start": 180.0, "slot": 3.0, "text": "Câu sau."}]
    plan, _ = voiceplan.plan_voice([2.5, 4.4, 1.0], mid, 183.0)
    assert plan[1]["start"] >= 171.7 - voiceplan.VOICE_BACK - 1e-6         # đoạn giữa: vẫn chỉ lùi 0,5 giây


def test_subtitles_follow_voice_past_outro_deadline():
    words = [(173.02, 173.38, "Đong"), (174.64, 174.84, "thử"), (174.84, 175.12, "nha.")]
    assert assemble.subtitle_until(words, 174.76, 177.26) == pytest.approx(175.27)
    assert assemble.subtitle_until(words[:1], 174.76, 177.26) == 174.76       # giọng dứt kịp: tắt đúng mốc
    assert assemble.subtitle_until([(174.5, 174.7, "nha.")], 174.76, 177.26) == 174.76   # như cũ, không kéo thêm
    assert assemble.subtitle_until(words, 177.26, 177.26) is None             # không có màn kết
    assert assemble.subtitle_until([(176.9, 177.5, "nha")], 174.76, 177.26) == 177.26   # không quá hết video


# ---------------- soát độc lập 1.0.2: các ca bắt nhầm / bỏ sót ----------------

def test_plating_slot_itself_counts_as_plated(monkeypatch):
    """Mốc đầu đoạn bị dời sớm (bám điểm chuyển cảnh, gộp câu): đoạn CHỨA mốc lên dĩa không bị bắt «dĩa» sớm."""
    segs = [(0.0, 2.0, "Mực đông hấp kiểu này ngon ghê"), (3.0, 9.0, "Sắp chín rồi thì rưới chút dầu mè vô cho thơm"),
            (9.4, 14.0, "Rồi xong lúc này mình sẽ đổ ra cho mình")]
    new = ["Mực đông hấp kiểu này giòn ngọt ghê luôn á cả nhà.", "Sắp chín thì rưới dầu mè, mùi thơm bay lên phức luôn.",
           "Giờ đổ hết ra dĩa, dĩa mực hấp gừng nóng hổi thơm phức nè cả nhà."]
    seen = _fake_ai(monkeypatch, [new] * 3)
    out = asyncio.run(pe.generate_timed("", "", "cook", segs, 15.0, plate_at=10.2))   # mốc nằm trong đoạn 2 (9.4-…)
    assert not any("CHƯA được bày ra dĩa" in p for p in seen) and out["segments"][2]["text"] == new[2]


def test_amount_goes_to_the_right_item_in_unpunctuated_speech():
    pairs = pe.qty_pairs("giờ mình nêm đường 1 muỗng nước mắm 2 muỗng")
    assert [(q, pe.ingredients.GROUPS[g][0]) for q, g in pairs] == [("1 muỗng", "đường"), ("2 muỗng", "nước mắm")]
    items = [(1, "giờ mình nêm đường 1 muỗng nước mắm 2 muỗng", "Giờ mình nêm 1 muỗng đường, 2 muỗng nước mắm cho đậm đà."),
             (2, "rồi thêm 2 muỗng nước mắm nữa cho đậm", "Rồi thêm 2 muỗng nước mắm nữa cho đậm vị nha.")]
    assert pe.anticipated_items(items) == {}                      # gốc cũng nêm nước mắm hai lần


def test_teaspoon_is_a_unit_not_coffee():
    text = "Giờ mình thả mực vô nồi, rồi nêm nửa muỗng cà phê bột ngọt, nửa muỗng cà phê đường, thêm nửa muỗng cà phê hạt nêm."
    got = pe.qty_pairs(text)
    assert {pe.ingredients.GROUPS[g][0] for _q, g in got} == {"bột ngọt", "đường", "hạt nêm"}
    hat_nem = {p for p in got if pe.ingredients.GROUPS[p[1]][0] == "hạt nêm"}
    assert pe.drop_anticipated(text, hat_nem) == \
        "Giờ mình thả mực vô nồi, rồi nêm nửa muỗng cà phê bột ngọt, nửa muỗng cà phê đường."
    assert pe._item_mentions("nêm nửa muỗng cà phê đường") and all(
        "cà phê" not in pe.ingredients.GROUPS[next(iter(m[2]))] for m in pe._item_mentions("nêm nửa muỗng cà phê đường"))


def test_restating_main_ingredient_amount_is_fine():
    items = [(1, "chuẩn bị 1 kg mực đông đá", "Chuẩn bị sẵn 1 kg mực đông đá nha."),
             (2, "thả hết vô nồi nước sôi", "Thả hết 1 kg mực vô nồi nước sôi luôn.")]
    assert pe.anticipated_items(items) == {}                      # chỉ định lượng nêm nếm (muỗng, giọt…) mới tính


def test_early_items_keep_word_context():
    first = {next(iter(pe.ingredients.find(n))): 150.0 for n in ("rau mùi", "hành lá", "cơm trắng")}
    first[next(iter(pe.ingredients.find("mực")))] = 0.0
    items = [(1, 10.0, "Mực luộc khéo thì khử sạch mùi."), (2, 12.0, "Món này mà ăn kèm với cơm trắng là hết sảy."),
             (3, 14.0, "Rồi mình tiến hành luộc mực nha."),
             (4, 16.0, "Trong lúc chờ mực chín thì mình tranh thủ thái sẵn ít gừng, hành tây với ớt sừng nha."),
             (5, 18.0, "Hình là gừng, hành tây, ớt sừng mình đã chuẩn bị sẵn.")]
    first.update({next(iter(pe.ingredients.find(n))): 150.0 for n in ("gừng", "hành tây", "ớt sừng")})
    assert pe.early_items(items, first) == {}


def test_drop_early_keeps_longer_names():
    text = "Xào hành tây với mực cho thơm, rồi mình xào hành luôn nha."
    assert pe.drop_early(text, ["hành"]) == "Xào hành tây với mực cho thơm, rồi mình xào luôn nha."


def test_plate_time_picks_plating_from_the_plate_step_within_cut():
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 200.0)], level="light")
    steps = [r.Step("plate", 0, 0, t0=99.0, t1=105.0)]
    base = dict(facts=r.Facts(is_recipe=True, steps=steps), edit=plan, stretch=1.0, end_src=200.0)
    sauce = SimpleNamespace(raw_segs=[(58.0, 59.0, "nó sẽ được đổ ra"), (100.0, 101.0, "múc ra dĩa"),
                                      (140.0, 141.0, "Giờ pha chút nước chấm, mình múc ra chén nha")], **base)
    assert remake.Remake.plate_kw(sauce, 0.0) == {"plate_at": 100.0}    # câu đầu tiên từ lúc bước bày món bắt đầu
    late = SimpleNamespace(raw_segs=[(58.0, 59.0, "nó sẽ được đổ ra")], **base)
    assert remake.Remake.plate_kw(late, 0.0) == {"plate_at": 99.0}      # không có câu nào sau đó: lấy bước bày món
    cut = SimpleNamespace(raw_segs=[(150.0, 151.0, "múc ra dĩa")], facts=None, edit=None, stretch=1.25, end_src=120.0)
    assert remake.Remake.plate_kw(cut, 2.0) == {}                       # câu nằm sau phần được dựng: bỏ
    loose = SimpleNamespace(raw_segs=[(100.0, 101.0, "múc ra dĩa")], facts=None, edit=None, stretch=1.25, end_src=0.0)
    assert remake.Remake.plate_kw(loose, 2.0) == {"plate_at": 127.0}    # không kế hoạch dựng: kéo chậm + đoạn mở đầu


def test_cross_batch_anticipated_amount_dropped(monkeypatch):
    async def fake(title, desc, mode, part, end, images_for=None, extra="", intro=True, **kw):
        out = []
        for k, s in enumerate(part):
            text = f"Lời mới cho đoạn số {s[0]:.0f} nè."
            if s[0] == 54.0:                                       # đoạn cuối lô 1 nói trước hạt nêm của lô 2
                text = "Giờ mình nêm nửa muỗng đường, thêm nửa muỗng hạt nêm cho đậm."
            if s[0] == 56.0:
                text = "Thêm nửa muỗng hạt nêm nữa, khuấy cho tan đều."
            out.append({"i": k, "start": s[0], "slot": 1.0, "text": text, "orig": s[2], "ost": "new"})
        return {"segments": out, "title": "t", "caption": "c", "hashtags": "#a"}
    monkeypatch.setattr(pe, "generate_timed", fake)
    segs = [(2.0 * i, 2.0 * i + 1.0, f"gốc số {i}.") for i in range(60)]
    segs[27] = (54.0, 55.0, "Rồi mình nêm nửa muỗng đường.")
    segs[28] = (56.0, 57.0, "Thêm nửa muỗng hạt nêm nữa.")
    out = asyncio.run(pe.generate_timed_any("", "", "cook", segs, 121.0))
    by_start = {s["start"]: s["text"] for s in out["segments"]}
    assert by_start[54.0] == "Giờ mình nêm nửa muỗng đường." and by_start[56.0].startswith("Thêm nửa muỗng hạt nêm")


def test_recap_at_the_end_does_not_start_early():
    segs = [{"start": 168.1, "slot": 3.6, "text": "Làm mực hấp gừng tại nhà xong rồi đó."},
            {"start": 171.7, "slot": 3.06, "text": "Tóm lại là luộc mực 4 phút rồi cho hành tây vô.",
             "orig": "Tóm lại: luộc mực 4 phút, cho hành tây vô."}]
    plan, _ = voiceplan.plan_voice([2.5, 4.4], segs, 174.76)
    assert plan[1]["start"] >= 171.7 - voiceplan.VOICE_BACK - 1e-6     # thẻ TÓM TẮT hiện đúng mốc: không lùi xa
