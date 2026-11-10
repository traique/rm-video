"""Test đơn vị cho các hàm thuần quyết định chất lượng khớp giọng–hình (trước đây chỉ có smoke test)."""
import pytest

import align
import beat
import core_video
import scenes
import subs
from transcribe import chunk_units


# ---------- căn chữ kịch bản lên mốc thời gian ----------
def test_align_same_count_uses_word_times():
    timed = [(0.0, 0.3, "mon"), (0.3, 0.6, "nay"), (0.6, 1.0, "ngon")]
    assert align.map_script("Món này ngon.", timed, 0, 1) == [(0.0, 0.3, "Món"), (0.3, 0.6, "này"), (0.6, 1.0, "ngon.")]


def test_align_missing_word_does_not_shift_rest():
    """Lỗi #6 cũ: STT thiếu 1 từ ở đầu thì MỌI từ sau đều lệch mốc. Giờ từ khớp giữ đúng mốc của nó."""
    script = "Đầu tiên mình ướp thịt với tỏi"
    timed = [(0.5, 0.8, "tiên"), (0.8, 1.0, "mình"), (1.0, 1.3, "ướp"), (1.3, 1.6, "thịt"),
             (1.6, 1.8, "với"), (1.8, 2.2, "tỏi")]
    out = align.map_script(script, timed, 0.0, 2.2)
    assert [w for _, _, w in out] == script.split()
    assert out[-1][:2] == (1.8, 2.2) and out[1][0] == 0.5
    assert all(b > a for a, b, _ in out)
    assert all(out[i][0] <= out[i + 1][0] for i in range(len(out) - 1))


def test_align_misheard_words_keep_order():
    script = "Cho nạc vai vào chảo"
    timed = [(0.0, 0.2, "cho"), (0.2, 0.5, "nạt"), (0.5, 0.7, "vai"), (0.7, 0.9, "vô"), (0.9, 1.2, "chảo"),
             (1.2, 1.4, "nha")]
    out = align.map_script(script, timed, 0, 1.4)
    assert [w for _, _, w in out] == script.split()
    assert out[0][0] == 0.0 and out[-1][:2] == (0.9, 1.2)


def test_align_no_times_spreads_in_window():
    out = align.map_script("một hai ba", [], 2.0, 3.0)
    assert out[0][0] == 2.0 and out[-1][1] <= 3.0 and len(out) == 3


def test_align_norm():
    assert align.norm("Đường,") == "duong" and align.norm("Ớt!") == "ot"


# ---------- xếp lịch giọng ----------
def test_plan_voice_never_overlaps_and_flags_too_long():
    segs = [{"start": 0.0, "slot": 2.0, "span": 2.0}, {"start": 2.0, "slot": 2.0, "span": 2.0},
            {"start": 4.0, "slot": 1.0, "span": 1.0}]
    plan, too_long = core_video.plan_voice([1.8, 2.4, 3.5], segs, 5.0)
    for a, b in zip(plan, plan[1:]):
        assert b["start"] >= a["end"]                      # không bao giờ đè lên nhau
    assert all(0.9 <= p["factor"] <= 1.35 for p in plan)
    assert too_long == [2]                                  # đoạn cuối 3.5s không thể nhét vào 1s


def test_plan_voice_natural_when_fits():
    plan, too_long = core_video.plan_voice([1.0], [{"start": 1.0, "slot": 2.0, "span": 1.0}], 5.0)
    assert too_long == [] and plan[0]["start"] == 1.0 and 0.92 <= plan[0]["factor"] <= 1.1


# ---------- chia đoạn theo thao tác ----------
def test_chunk_units_breaks_before_action_words():
    units = [(0.0, 0.3, "đầu"), (0.3, 0.6, "tiên"), (0.6, 0.9, "rửa"), (0.9, 1.2, "sạch"), (1.2, 1.5, "thịt"),
             (1.5, 1.8, "cho"), (1.8, 2.1, "vào"), (2.1, 2.4, "nồi")]
    out = chunk_units(units)
    assert [t for _, _, t in out] == ["đầu tiên rửa sạch thịt", "cho vào nồi"]


def test_chunk_units_cho_den_khi_is_not_action():
    units = [(0.0, 0.4, "đun"), (0.4, 0.8, "sôi"), (0.8, 1.1, "nước"), (1.1, 1.3, "cho"), (1.3, 1.5, "đến"),
             (1.5, 1.8, "khi"), (1.8, 2.0, "sủi")]
    assert len(chunk_units(units)) == 1


# ---------- bám điểm chuyển cảnh, cắt phần ----------
def test_snap_to_cuts_moves_start_to_nearby_cut_once():
    segs = [(1.0, 2.5, "a"), (3.0, 4.0, "b"), (3.6, 5.0, "c")]
    out = scenes.snap_to_cuts(segs, [0.7, 3.4])
    assert out[0][0] == pytest.approx(0.8) and out[1][0] == pytest.approx(3.5)
    assert out[2][0] > out[1][0]                            # mỗi điểm cảnh chỉ dùng 1 lần, giữ thứ tự


def test_split_points_prefers_silence_on_cut():
    b = scenes.split_points(200, 60, cuts=[55.0, 62.0, 118.0], silences=[(61.5, 62.5), (130.0, 131.0)])
    assert b[0] == 0.0 and b[-1] == 200 and 62.0 in b
    assert all(b2 - b1 >= 20 for b1, b2 in zip(b, b[1:]))


def test_scene_segments_merges_short_scenes():
    assert scenes.scene_segments([1.0, 1.5, 6.0], 8.0) == [(0.0, 1.5, ""), (1.5, 6.0, ""), (6.0, 8.0, "")]


# ---------- phụ đề ----------
def test_group_words_balanced_lines():
    words = [(i * 0.3, i * 0.3 + 0.25, w) for i, w in enumerate("một hai ba bốn năm".split())]
    lines = subs.group_words(words)
    assert [len(ln) for _, _, ln in lines] == [3, 2]


def test_ass_escapes_override_codes(tmp_path):
    p = tmp_path / "s.ass"
    subs.write_ass([(0.0, 0.5, "a{\\b1}"), (0.5, 1.0, "b\\N")], str(p))
    body = p.read_text(encoding="utf-8").split("[Events]")[1]
    assert "{\\b1}" not in body and "\\N" not in body


# ---------- tốc độ khung, beat ----------
@pytest.mark.parametrize("src,want", [(30, 30), (25, 25), (50, 25), (60, 30), (59.94, 29.97), (0, 30), (120, 30)])
def test_out_fps(src, want):
    assert core_video.out_fps(src) == pytest.approx(want, abs=0.01)


def test_beat_sync_prefers_closest_ratio():
    s = beat.compute_sync(120, 0.5, 240, 0.25)            # nhạc gấp đôi BPM -> coi như cùng nhịp
    assert s["speed"] == 1.0
    s = beat.compute_sync(100, 0.0, 150, 0.0)
    assert 0.8 <= s["speed"] <= 1.25
