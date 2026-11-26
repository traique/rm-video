"""1.5.0 (video 13 ngày 26/11): giọng người gốc lọt 2 giây cuối (máy nghe bỏ câu «Và đây là series…») -> cổng tắt tiếng gộp
quãng Silero VAD nghe thẳng tiếng gốc; khối điểm danh + tóm tắt nhảy lên giây 4 (máy nghe cắt đoạn liền nhau, _sentence_start
lùi mãi) -> giới hạn lùi; «1.5 đồng» máy nghe nhầm chặn soát lời và bộ kiểm hook; truyện bé: không bị lệnh nghỉ GPU kiểu
«lâu không cấp máy» chặn, profile mã hoá nhẹ, từ khoá tìm kiếm + thẻ YouTube + CTA, giờ YouTube tách khỏi Facebook."""
import asyncio
import os
import random
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

import config
import ingredients
import kaggle_worker
import prompt_engine
from rmvideo.app import youtube
from rmvideo.app.flow import hooks, remake, style_b
from rmvideo.domain import recipe as r
from rmvideo.kids import cards, prompts, story
from rmvideo.media import edl, renderer, vad
from test_ban50 import _fake_ai
from test_youtube import yt  # noqa: F401  (fixture: cài đặt + trạng thái YouTube riêng)

VN = youtube.VN


# ---------------- cổng tiếng gốc: VAD ----------------

def test_vad_windows_and_merge():
    step = vad.STEP
    probs = [0.0] * 10 + [0.9] * 40 + [0.4] * 3 + [0.1] * 20 + [0.95] * 2 + [0.0] * 10 + [0.8] * 30
    wins = vad.windows_from_probs(probs, step=step)
    # quãng 1: vào ở khung 10, 0.4 chưa dưới ngưỡng ra (trễ) nên kéo tới khung 53; quãng 2 chữ 2 khung = 64 ms < MIN_SPEECH bỏ
    assert len(wins) == 2
    assert wins[0] == (round(10 * step - vad.PAD, 2), round(53 * step + vad.PAD, 2))
    assert wins[1][0] == round(85 * step - vad.PAD, 2) and wins[1][1] == round(115 * step + vad.PAD, 2)
    assert vad.windows_from_probs([]) == [] and vad.windows_from_probs([0.2] * 50) == []
    assert vad.merge([(1.0, 2.0)], [(1.9, 3.0), (5.0, 6.0)], [(6.02, 7.0)]) == [(1.0, 3.0), (5.0, 7.0)]
    assert vad.merge([], []) == []


def test_gate_windows_adds_vad_speech(monkeypatch):
    """Mốc lời máy nghe chỉ tới 30s; VAD nghe người gốc nói thêm ở 42.9-45s (job 13) -> cổng tắt cả đoạn đó."""
    notes = []
    job = SimpleNamespace(note=lambda s: notes.append(s))
    rm = SimpleNamespace(speech=[(0.5, 2.8), (16.6, 30.0)], inp="src.mp4", edit=None, stretch=1.0, teaser_T=0.0, job=job)

    async def heard(path, max_sec=0.0):
        assert path == "src.mp4"
        return [(0.3, 2.9), (42.9, 45.0)]
    monkeypatch.setattr(vad, "speech_windows", heard)
    got = asyncio.run(remake.Remake.gate_windows(rm))
    assert got == [(0.3, 2.9), (16.6, 30.0), (42.9, 45.0)] and notes and "VAD" in notes[0]
    # không mốc lời máy nghe: cổng theo VAD (trước đây tắt cả bài); VAD lỗi -> như cũ
    rm.speech = []
    assert asyncio.run(remake.Remake.gate_windows(rm)) == [(0.3, 2.9), (42.9, 45.0)]

    async def nothing(path, max_sec=0.0):
        return []
    monkeypatch.setattr(vad, "speech_windows", nothing)
    assert asyncio.run(remake.Remake.gate_windows(rm)) == remake.FULL_GATE
    # trục ra: qua kế hoạch dựng (đã bỏ 2-4s) + lùi sau đoạn mở đầu 3s
    plan = edl.EditPlan(pieces=[edl.Piece(0.0, 2.0), edl.Piece(4.0, 10.0)])
    assert remake.src_windows_to_out(plan, 1.0, 3.0, [(0.5, 1.0), (5.0, 6.0)]) == [(3.5, 4.0), (6.0, 7.0)]
    assert remake.src_windows_to_out(None, 1.5, 0.0, [(2.0, 4.0)]) == [(3.0, 6.0)]


@pytest.mark.skipif(not os.path.exists(os.path.join(vad._DIR, vad.MODEL_NAME)), reason="chưa có model Silero (tải lúc chạy thật)")
def test_vad_real_model_runs_and_rejects_noise(tmp_path):
    """Model thật (khi đã có trong models/): tiếng ồn trắng nhỏ + im lặng không bị coi là người nói; chạy nhanh."""
    sr = vad.SR
    rnd = np.random.default_rng(1)
    x = np.concatenate([np.zeros(sr), rnd.normal(0, 0.01, sr * 2).astype(np.float32), np.zeros(sr)]).astype(np.float32)
    probs = vad.probabilities(x)
    assert len(probs) == len(x) // vad.WIN and max(probs) < vad.ON
    assert vad.windows_from_probs(probs) == []


# ---------------- khối điểm danh / tóm tắt không nhảy lên đầu video ----------------

def test_tail_block_stays_after_last_steps_with_contiguous_asr_segments():
    """Whisper (Groq) cắt đoạn LIỀN NHAU; bước nấu cuối kết thúc giữa đoạn cuối -> _sentence_start từng lùi tới tận giây 4.9
    (job 13 ngày 26/11: thẻ nguyên liệu + TÓM TẮT chiếu ở giây 4-16, trước khi nấu). Giờ khối cuối đứng sau các bước."""
    segs = [(0.0, 4.4, "Nếu bạn là sinh viên hay người bận rộn thì có thể làm thử món này."),
            (4.9, 5.76, "Nguyên liệu đầu tiên có hành tím băm,"),
            (5.76, 10.88, "tỏi băm và hành lá cắt khúc. Mọi người bắc chảo dầu phi thơm tất cả nguyên liệu."),
            (10.88, 15.92, "Sau đó mọi người bắt đầu nêm nếm gia vị cho mình."),
            (15.92, 19.48, "Sau khi nêm nếm mọi người nấu sôi khoảng một phút cho mình."),
            (19.48, 23.12, "Sau khi nguyên liệu đã hoà tan thì đập vô 4 quả trứng gà."),
            (23.12, 33.0, "Sau đó mọi người đậy nắp và nấu lửa nhỏ cho mình khoảng 1.5 phút là ăn được.")]
    end = 36.2
    steps = [r.Step("intro", 0, 0, t0=0.0, t1=4.4),
             r.Step("prep", 1, 1, "Băm hành tím", ["hành tím"], t0=4.9, t1=5.76),
             r.Step("cook", 2, 2, "Phi thơm hành tỏi và hành lá", ["hành tím", "tỏi", "hành lá"], t0=5.76, t1=10.88),
             r.Step("cook", 3, 3, "Nêm nếm gia vị vừa ăn", [], t0=10.88, t1=15.92),
             r.Step("cook", 4, 4, "Nấu sôi hỗn hợp khoảng một phút", [], t0=15.92, t1=19.48),
             r.Step("cook", 5, 5, "Đập 4 quả trứng gà", ["trứng gà"], t0=19.48, t1=23.12),
             r.Step("cook", 6, 6, "Đậy nắp nấu lửa nhỏ", [], t0=23.12, t1=28.0)]      # kết thúc GIỮA đoạn cuối (23.12-33.0)
    items = [r.Item("trứng gà", 4.0, "quả", "ok", seen_at=19.5), r.Item("hành tím", None, "", "ok", seen_at=4.9),
             r.Item("tỏi", None, "", "ok", seen_at=5.8), r.Item("hành lá", None, "", "ok", seen_at=6.5)]
    facts = r.Facts(is_recipe=True, dish="Trứng kho quẹt", steps=steps, items=items)
    rnd = random.Random(1)
    mot = [0.03 + 0.01 * rnd.random() for _ in range(100)]
    cuts = [2.0, 4.4, 7.0, 9.0, 12.0, 14.0, 17.0, 19.5, 22.0, 25.0, 28.0, 31.0, 33.0, 35.0]
    plan, why = style_b.plan_strong(end, segs, cuts, mot, 1.0, "auto", facts)
    assert why == "" and plan.level == "strong"
    tags = [p.tag for p in plan.pieces]
    assert "montage" in tags and "recap" in tags
    starts = plan.starts()
    first_side = min(k for k, p in enumerate(plan.pieces) if p.role == "side")
    # sau cả câu của bước cuối (23.12-33.0 trên video gốc), ngay trước cảnh món cuối; không còn ở giây 4
    assert starts[first_side] >= plan.to_out(32.9) and all(x["t"] >= plan.to_out(32.9) for x in plan.texts)
    assert plan.pieces[first_side - 1].role == "main" and abs(plan.pieces[first_side - 1].b - 33.0) < 0.1
    assert plan.to_out(4.9) < 6.0                           # lời «hành tím băm» vẫn ở đầu video, không bị khối cuối đẩy
    # _sentence_start: đoạn trước đã kết câu (dấu chấm) thì không lùi qua; không có dấu câu thì lùi tối đa TAIL_BACK_MAX
    assert style_b._sentence_start(segs, 28.0) == 23.12
    nodots = [(a, b, t.rstrip(".,")) for a, b, t in segs]
    assert style_b._sentence_start(nodots, 28.0) >= 28.0 - style_b.TAIL_BACK_MAX - 0.5
    assert style_b._sentence_end(segs, 28.0) == 33.0 and style_b._sentence_end(segs, 34.0) == 34.0
    # đoạn liền kề sau khối chèn không «phủ» lên khối: đầu đoạn sau nằm sau khối (edl.EditPlan.seg_out)
    more = segs + [(33.0, 35.0, "Chúc cả nhà ngon miệng nha.")]
    plan2, _ = style_b.plan_strong(end, more, cuts, mot, 1.0, "auto", facts)
    k2 = min(k for k, p in enumerate(plan2.pieces) if p.role == "side")
    tail_len = sum(p.length for p in plan2.pieces if p.role == "side")
    oa, ob = plan2.seg_out(33.0, 35.0)
    assert oa >= plan2.starts()[k2] + tail_len - 0.05 and ob - oa <= 2.3


# ---------------- số liệu máy nghe nhầm: soát lời, kịch bản, hook ----------------

def test_soft_units_and_unit_swap():
    assert ingredients.soft("1.5 đồng") and ingredients.soft("1 người") and ingredients.soft("50 k") and ingredients.soft("3 tuổi")
    assert not ingredients.soft("1.5 phút") and not ingredients.soft("2 muỗng") and not ingredients.soft("4 quả")
    assert ingredients.unit_swap_ok(["1.5 đồng"], ["1.5 phút"])
    assert ingredients.unit_swap_ok(["2 muỗng", "1 phút"], ["2 muỗng", "1 phút"])
    assert not ingredients.unit_swap_ok(["3-4 giọt"], ["3-4 muỗng"])          # đổi giữa hai đơn vị đo
    assert not ingredients.unit_swap_ok(["1.5 đồng"], ["2 phút"])              # đổi số
    assert not ingredients.unit_swap_ok(["1.5 đồng"], [])                      # bỏ mất số
    orig = "Sau đó mọi người đậy nắp và nấu lửa nhỏ cho mình khoảng 1.5 đồng, nêm 2 muỗng nước mắm."
    assert ingredients.check_numbers_for(orig, "Đậy nắp, nấu lửa nhỏ, nêm 2 muỗng nước mắm.", "cook") == ([], [])
    assert ingredients.check_numbers_for(orig, "Đậy nắp, nấu lửa nhỏ, nêm 2 muỗng nước mắm.", "review") == (["1.5 đồng"], [])
    assert ingredients.check_numbers_for(orig, "Nấu 1.5 đồng, nêm 3 muỗng nước mắm.", "cook")[1] == ["3 muỗng"]


def test_proofread_accepts_unit_fix_but_not_measure_change(monkeypatch):
    """Job 13 ngày 26/11: soát lời sửa «1.5 đồng» -> «1.5 phút» bị bỏ -> video đọc «nấu chừng 1.5 đồng là ăn được»."""
    old = "Kế tiếp đậy nắp lại, vặn lửa riu riu nấu chừng 1.5 đồng là ăn được."
    new = "Kế tiếp đậy nắp lại, vặn lửa riu riu nấu chừng 1.5 phút là ăn được."
    segs = [{"text": old, "orig": "Sau đó mọi người đậy nắp và nấu lửa nhỏ cho mình khoảng 1.5 đồng.", "ost": "new"},
            {"text": "Rưới 3-4 giọt dầu mè cho thơm nha.", "orig": "rưới 3-4 giọt dầu mè", "ost": "new"}]
    _fake_ai(monkeypatch, [{"fixes": [{"i": 0, "text": new}, {"i": 1, "text": "Rưới 3-4 muỗng dầu mè cho thơm nha."}]}])
    assert asyncio.run(prompt_engine.proofread(segs)) == [(0, old, new)]
    assert segs[0]["text"] == new and segs[1]["text"] == "Rưới 3-4 giọt dầu mè cho thơm nha."


def test_hook_rules_ignore_misheard_people_count_and_rice(monkeypatch):
    orig0 = "Nếu bạn là xin duyên hay là một người bạn gọn mà tới nay không biết ăn gì thì có thể làm thử cái món này."
    orig_all = orig0 + " Phi thơm hành tím, tỏi và hành lá rồi đập vô 4 quả trứng gà, nấu lửa nhỏ."
    assert hooks.problems("Trứng kho quẹt dậy mùi thơm nhờ phi hành tỏi trước nè.", 11, orig_all, orig0, "trứng kho quẹt", "", 7) == []
    assert hooks.problems("Trứng kho quẹt đậm đà đưa cơm ăn hoài không ngán.", 11, orig_all, orig0, "trứng kho quẹt", "", 7) == []
    assert any("bịa" in w for w in hooks.problems("Trứng kho quẹt ăn với bún là ngon nhất nè.", 11, orig_all, orig0,
                                                   "trứng kho quẹt", "", 7))
    # lời gốc đoạn 0 có định lượng thật thì vẫn phải giữ
    o = "Thịt bò ướp 15 phút với tỏi băm"
    assert any("mất" in w for w in hooks.problems("Bò mềm tan trong miệng luôn nè", 10, o, o))


def test_hook_prompt_asks_fewer_words_than_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, **kw):
        seen.append(prompt)
        return '{"hooks": []}'
    monkeypatch.setattr(prompt_engine, "call_gemini", fake)
    got = asyncio.run(hooks._pick(title="Trứng kho quẹt", tip="", dish="trứng kho quẹt", orig_all="x", after="y", teaser=True,
                                  mx=11, mn=7))
    assert got is None and seen and "7-9 chữ" in seen[0] and "7-11" not in seen[0]


# ---------------- truyện bé: nghỉ GPU, mã hoá, bài đăng, CTA, giờ YouTube ----------------

def test_kids_not_blocked_by_queue_cooldown(monkeypatch):
    monkeypatch.setattr(kaggle_worker, "_sync_account", lambda: None)
    monkeypatch.setattr(kaggle_worker, "_COOL", {"until": __import__("time").time() + 3600, "why": "Kaggle lâu không cấp máy GPU"})
    assert "tạm nghỉ" in kaggle_worker.gpu_cool() and kaggle_worker.gpu_cool("kids") == ""
    kaggle_worker._COOL["why"] = kaggle_worker.NO_GPU_MARK + " — tài khoản đã xác minh số điện thoại chưa?"
    assert "tạm nghỉ" in kaggle_worker.gpu_cool("kids")
    kaggle_worker._COOL["until"] = 0.0
    assert kaggle_worker.gpu_cool("kids") == "" and kaggle_worker.gpu_cool() == ""


def test_kids_encode_profile(monkeypatch):
    monkeypatch.setattr(config, "QUALITY", "balanced")
    monkeypatch.setattr(renderer, "nvenc", lambda: None)
    args = renderer.encode_args(1920, 30, quality="kids")
    assert args[args.index("-maxrate") + 1] == "5500k" and args[args.index("-crf") + 1] == "21"
    assert renderer.encode_args(1920, 30)[renderer.encode_args(1920, 30).index("-maxrate") + 1] == "9000k"
    assert renderer.encode_args(1280, 30, quality="kids")[renderer.encode_args(1280, 30, quality="kids").index("-maxrate") + 1] == "3000k"


def test_recap_cta_and_end_card(tmp_path):
    brief = {"words": [{"en": "ball", "vi": "trái banh"}, {"en": "share", "vi": "chia sẻ"}]}
    assert story.recap_text(brief).endswith("Hẹn gặp lại bé ở chuyện sau!")
    assert story.recap_text(brief, cta="Theo dõi Kể Bé Nghe để mai nghe truyện mới nha") == \
        "Hôm nay mình học: ball là trái banh, share là chia sẻ. Theo dõi Kể Bé Nghe để mai nghe truyện mới nha!"
    assert story.recap_text({"words": []}, cta="x") == ""
    from PIL import Image
    words = brief["words"]
    plain = cards.end_card(words, {"name": "Kể Bé Nghe", "tagline": "Mỗi ngày một chuyện"}, 540, 960, str(tmp_path / "a.png"))
    with_cta = cards.end_card(words, {"name": "Kể Bé Nghe", "tagline": "Mỗi ngày một chuyện",
                                      "cta": "Theo dõi Kể Bé Nghe để mai nghe truyện mới nha!"}, 540, 960, str(tmp_path / "b.png"))
    assert Image.open(with_cta).size[1] > Image.open(plain).size[1]


def test_post_pack_keywords_cta_title_tag():
    from test_kids import DATA, _good_story
    brief = story.pick(DATA, [], random.Random(2))
    st = story.normalize(_good_story(brief), DATA)
    chan = {"name": "Kể Bé Nghe", "tagline": "Mỗi ngày một chuyện nhỏ", "hashtags": "#truyenchobe,#hoctienganhchobe,#kebenghe,#x",
            "cta": "Theo dõi Kể Bé Nghe để mai nghe truyện mới nha!", "keywords": ["truyện cho bé", "học tiếng Anh cho bé"],
            "title_tag": "Truyện kể bé nghe, học tiếng Anh"}
    title, desc, tags = prompts.post_pack(st, brief, chan, DATA)
    assert title.startswith(st["title"]) and title.endswith(" | Truyện kể bé nghe, học tiếng Anh")
    assert len(title + " #Shorts") <= prompts.YT_TITLE_MAX
    assert "👉 Theo dõi Kể Bé Nghe" in desc and desc.rstrip().endswith("🔎 truyện cho bé, học tiếng Anh cho bé.")
    tl = tags.split()
    assert tl[:2] == ["#truyenchobe", "#hoctienganhchobe"] and tl[2] == st["hashtags"][0] and tl[-1] == "#x"
    # tiêu đề dài: không nối đuôi; tắt CTA / từ khoá: như cũ
    long_st = dict(st, title="T" * 80)
    title2, desc2, _ = prompts.post_pack(long_st, brief, dict(chan, cta="", keywords=[]), DATA)
    assert title2 == "T" * 80 and "👉" not in desc2 and "🔎" not in desc2


def test_youtube_tags_and_kids_slot(monkeypatch):
    monkeypatch.setattr(config, "KIDS_KEYWORDS", "truyện cho bé, học tiếng Anh cho bé", raising=False)
    monkeypatch.setattr(config, "KIDS_CHANNEL_NAME", "Kể Bé Nghe")
    desc = "Mô tả\n\n#truyenchobe #kebenghe #<x>"
    assert youtube.video_tags("kids", desc) == ["truyenchobe", "kebenghe", "truyện cho bé", "học tiếng Anh cho bé", "Kể Bé Nghe"]
    assert youtube.video_tags("cook", "#bokho #bokho #Shorts") == ["bokho", "Shorts"]
    many = youtube.video_tags("kids", " ".join(f"#tag{i}" for i in range(200)))
    assert 0 < len(many) < 200 and sum(len(t) + 1 for t in many) <= youtube.TAGS_MAX_CHARS
    body = youtube.video_body("kids", "T", desc, None, 0.0, tags=["a", "b"])
    assert body["snippet"]["tags"] == ["a", "b"] and "tags" not in youtube.video_body("kids", "T", "D", None, 0.0)["snippet"]
    # giờ YouTube: khung Facebook thứ k -> khung YouTube thứ k cùng ngày; trống = cùng giờ; đã qua = đăng ngay
    monkeypatch.setattr(config, "KIDS_TIMES", "11:30,19:30")
    monkeypatch.setattr(config, "KIDS_YT_TIMES", "07:30,20:30", raising=False)

    def ts(hm, day=28):
        return datetime(2026, 11, day, int(hm[:2]), int(hm[3:]), tzinfo=VN).timestamp()
    now = ts("06:00")
    assert youtube.slot_for("kids", ts("11:30"), now) == ts("07:30")
    assert youtube.slot_for("kids", ts("19:30"), now) == ts("20:30")
    assert youtube.slot_for("kids", ts("16:00"), now) == ts("20:30")            # ngoài khung: khung YouTube gần nhất
    assert youtube.slot_for("kids", ts("11:30"), ts("07:20")) is None          # giờ YouTube đã qua / quá sát: đăng ngay
    assert youtube.slot_for("cook", ts("11:30"), now) == ts("11:30") and youtube.slot_for("kids", None, now) is None
    monkeypatch.setattr(config, "KIDS_YT_TIMES", "", raising=False)
    assert youtube.slot_for("kids", ts("11:30"), now) == ts("11:30")


@pytest.mark.usefixtures("yt")
def test_after_facebook_uses_kids_slot_and_tags(monkeypatch, tmp_path):
    config.save_settings({"KIDS_YT_REFRESH_TOKEN": "RT-kids", "KIDS_TIMES": "11:30,19:30", "KIDS_YT_TIMES": "07:30,20:30"})
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 10)
    post = prompt_engine.platform_post("Thỏ Trắng và chiếc mền | Truyện kể bé nghe", "Mô tả bé", "#truyenchobe,#kebenghe")
    job = SimpleNamespace(id=77, post=post)
    when = datetime(2026, 11, 28, 19, 30, tzinfo=VN).timestamp()
    monkeypatch.setattr(youtube.time, "time", lambda: when - 10 * 3600)
    assert youtube.after_facebook("kids", job, str(vid), "T", "D", when)
    it = youtube.load_state()["items"][0]
    assert it["when"] == datetime(2026, 11, 28, 20, 30, tzinfo=VN).timestamp()
    assert it["title"].startswith("Thỏ Trắng và chiếc mền") and it["tags"][:2] == ["truyenchobe", "kebenghe"]
    assert "Kể Bé Nghe" in it["tags"]


def test_settings_new_keys_validate():
    import rmvideo.app.settings as s
    assert s.BY_KEY["KIDS_YT_TIMES"].check("") == "" and s.BY_KEY["KIDS_YT_TIMES"].check("07:30,20:30") == "07:30,20:30"
    with pytest.raises(ValueError, match="KIDS_YT_TIMES"):
        s.BY_KEY["KIDS_YT_TIMES"].check("7h")
    assert config.EDITABLE["KIDS_TIMES"] == "11:30,19:30" and config.EDITABLE["KIDS_CTA"].startswith("Theo dõi {name}")
    import kids_auto
    assert kids_auto.channel()["cta"].startswith("Theo dõi " + config.KIDS_CHANNEL_NAME)
    assert "truyện cho bé" in kids_auto.channel()["keywords"] and kids_auto.channel()["title_tag"]


# ---------------- lời bám hình: lùi lời mới tới lúc người gốc nói tới việc đó ----------------

def test_content_delay_follows_original_action_word():
    from rmvideo.media import voiceplan as vp
    o = "Sau khi nguyên liệu đã hòa tan thì mọi người đập vô cho mình 4 quả trứng gà."
    n = "Rồi mình đập trực tiếp 4 trái trứng gà vào nồi luôn."
    assert vp.content_anchor(o)[2:] == ("verb", "đập") and vp.content_anchor(n)[2:] == ("verb", "đập")
    assert 1.2 <= vp.content_delay(o, n, 3.64, 3.5) <= 1.6          # «đập» gốc ở giây ~2 của câu, lời mới nói ngay
    # con số / nguyên liệu: khớp theo khoá; lời mới nói việc đó muộn hơn gốc thì không lùi; không có chữ chung: 0
    assert vp.content_anchor("Cho 2 muỗng nước mắm vào.")[2:] == ("verb", "cho")        # chữ nội dung ĐẦU TIÊN
    assert vp.content_anchor("Khoảng 2 muỗng nước mắm là vừa.")[2:] == ("num", "2")
    assert vp.content_anchor("Đợi một lát cho ráo")[2:] == ("verb", "cho")              # «một lát» không phải con số
    assert vp.content_delay("Nêm 2 muỗng nước mắm vào", "Giờ thì mình nêm 2 muỗng nước mắm", 2.0, 2.0) == 0.0
    assert vp.content_delay("Giờ nêm 2 muỗng nước mắm", "Nêm 2 muỗng nước mắm vô nha", 2.0, 2.0) == pytest.approx(2.0 / 6, abs=0.01)
    assert vp.content_delay("Nếu bạn bận rộn thì làm thử món này", "Món kho quẹt này thơm nức mũi", 4.4, 3.0) == 0.0
    assert vp.content_delay("Nguyên liệu gồm: trứng gà", "Nguyên liệu gồm trứng gà", 4.8, 4.0) == 0.0
    assert vp.content_delay("Lúc này mình cho hành tím vào nha", "Hành tím băm nhỏ cho vào chảo", 3.0, 3.0) == 0.0
    assert vp.content_delay("", "x", 1.0, 1.0) == 0.0 and vp.content_delay("x", "", 1.0, 1.0) == 0.0
    assert vp.content_delay("Rồi mình đợi một lát rồi mới đập trứng vào", "Đập trứng vào", 10.0, 1.0) == vp.DELAY_MAX


def test_plan_voice_applies_delay_within_slot():
    from rmvideo.media import voiceplan as vp
    o = "Sau khi nguyên liệu đã hòa tan thì mọi người đập vô cho mình 4 quả trứng gà."
    n = "Rồi mình đập trực tiếp 4 trái trứng gà vào nồi luôn."
    segs = [{"start": 0.5, "slot": 3.5, "span": 3.0, "orig": "Mình chào cả nhà nha", "text": "Chào cả nhà"},
            {"start": 4.0, "slot": 8.0, "span": 3.64, "orig": o, "text": n},
            {"start": 12.0, "slot": 4.0, "span": 3.0, "orig": "Rồi mình đậy nắp lại nấu thêm xíu", "text": "Đậy nắp nấu thêm xíu"}]
    plan, _ = vp.plan_voice([2.0, 3.5, 2.0], segs, 16.0)
    assert plan[0]["start"] == 0.5 and 5.2 <= plan[1]["start"] <= 5.6          # lùi ~1.4s, khe 8s còn chỗ
    assert plan[1]["end"] <= 12.0 - 0.1 and plan[2]["start"] == pytest.approx(12.0 + vp.content_delay(
        segs[2]["orig"], segs[2]["text"], 3.0, 2.0), abs=0.01)
    tight = [dict(segs[1], slot=4.5)] + [dict(segs[2], start=8.5, slot=7.5)]
    plan2, _ = vp.plan_voice([3.5, 2.0], tight, 16.0)
    assert plan2[0]["start"] <= 4.0 + 4.5 - 3.5 - 0.15 + 0.01                # khe chật: lùi ít hơn, không lấn khe sau
    plain = [{"start": 4.0, "slot": 8.0, "span": 3.64, "text": n}]           # không có lời gốc (bản cũ): như trước
    assert vp.plan_voice([3.5], plain, 16.0)[0][0]["start"] == 4.0


def test_montage_prefers_said_time_over_intro_dish_shot():
    items = [r.Item("hành tím", None, "", "ok", seen_at=2.5, said_at=4.9), r.Item("tỏi", None, "", "ok", seen_at=7.4, said_at=9.0),
             r.Item("trứng gà", 4.0, "quả", "ok", seen_at=2.6, said_at=-1.0)]
    got = style_b.montage_times(items, 36.0, 1.2)
    assert [t for _, t in got] == [4.9, 7.4, 2.6]        # mở đầu là món xong: lấy lúc nói; không nói tới thì vẫn lấy ảnh
