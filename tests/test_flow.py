"""Bản 25 — từ video thật 12/11/2026 (mì xào bò 173s, dựng trên Kaggle):
(1) cuối video còn giọng người gốc: Whisper bịa "Đừng quên đăng ký kênh..." rồi bỏ qua ~27s lời thật, Kaggle chỉ tách giọng
    các quãng bóc lời thấy có người nói; (2) giọng đọc không trơn: lời gốc bị cắt vụn ("sau" đọc một mình rồi im 6s) và
    16/46 đoạn bị AI nén thành câu cụt."""
import asyncio
import json
import os
import subprocess

import config
import core_video
import prompt_engine
import transcribe
import vocab


# ---------------- bóc lời: câu bịa + nghe lại quãng trống ----------------

def _words(start, text, step=0.4):
    return [{"start": round(start + k * step, 2), "end": round(start + (k + 1) * step, 2), "word": w}
            for k, w in enumerate(text.split())]


def _silent_mp3(path, dur):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", str(dur),
                    path], check=True)


def test_groq_drops_outro_and_shouting_then_rechecks_the_hole(monkeypatch, tmp_path):
    audio = str(tmp_path / "audio.mp3")
    _silent_mp3(audio, 60)
    real = "Đảo đều tay cho bò săn lại."
    outro = "Đừng quên đăng ký kênh để nhận thêm những video mới nhé."
    caps = "CÔNG THỨC MÌ XÀ BÒ CỦA MÌ ĐỪC BÀ NGON ĐỪC BÀ NGON ĐỪC BÀ NGON ĐỪM?"
    calls = []

    async def fake_post(path, prompt, model="", language=""):
        calls.append((path, prompt, os.path.exists(path), model, language))
        if len(calls) == 1:                                    # lượt đầu: như log thật
            return 200, {"duration": 60.0, "language": "vietnamese", "text": f"{real} {outro} {caps}",
                         "segments": [{"start": 0.0, "end": 2.8, "text": real, "no_speech_prob": 0.01,
                                       "avg_logprob": -0.2},
                                      {"start": 10.4, "end": 13.0, "text": outro, "no_speech_prob": 0.05,
                                       "avg_logprob": -0.3},
                                      {"start": 50.1, "end": 56.0, "text": caps, "no_speech_prob": 0.1,
                                       "avg_logprob": -0.4}],
                         "words": _words(0.0, real) + _words(10.4, outro, 0.2) + _words(50.1, caps, 0.3)}
        # nghe lại quãng trống [2.8, 60] (cắt từ 2.5s): lời thật ở giây 13-21 của video + một câu nhạc yếu
        said = "Rắc thêm chút tiêu lên. Nấu nhiều thì cứ nhân công thức lên nha."
        return 200, {"segments": [{"start": 10.5, "end": 18.5, "text": said, "no_speech_prob": 0.02,
                                   "avg_logprob": -0.25, "compression_ratio": 1.3},
                                  {"start": 40.0, "end": 45.0, "text": "Cảm ơn các bạn", "no_speech_prob": 0.7,
                                   "avg_logprob": -1.2, "compression_ratio": 1.0}],
                     "words": _words(10.5, said, 0.55) + _words(40.0, "Cảm ơn các bạn", 1.0)}
    monkeypatch.setattr(transcribe, "_groq_post", fake_post)
    text, segs = asyncio.run(transcribe._groq(audio, "Công thức mì xào bò."))
    assert len(calls) == 2
    assert calls[1][1] == "" and calls[1][2] and not os.path.exists(calls[1][0])   # không kèm prompt, xoá file cắt
    assert calls[0][3] == "" and calls[1][3] == transcribe.HOLE_MODEL and calls[1][4] == "vi"   # model đầy đủ, khoá vi
    joined = " ".join(t for _, _, t in segs)
    assert "đăng ký" not in joined and "ĐỪC" not in joined and "Cảm ơn" not in joined
    assert "tiêu" in joined and "nhân công thức" in joined
    tieu = next(s for s in segs if "tiêu" in s[2])
    assert 12.5 <= tieu[0] <= 13.5                                  # mốc đổi về trục thời gian của cả video
    assert "tiêu" in text and "đăng ký" not in text


def test_no_recheck_without_any_speech_or_holes(monkeypatch, tmp_path):
    audio = str(tmp_path / "audio.mp3")
    _silent_mp3(audio, 12)
    calls = []

    async def fake_post(path, prompt, **kw):
        calls.append(path)
        said = "Cho bò vào chảo. Đảo đều tay. Nêm chút muối. Tắt bếp."
        return 200, {"duration": 12.0, "text": said,
                     "segments": [{"start": 0.0, "end": 11.5, "text": said, "no_speech_prob": 0.0, "avg_logprob": -0.2}],
                     "words": _words(0.0, said, 1.0)}
    monkeypatch.setattr(transcribe, "_groq_post", fake_post)
    asyncio.run(transcribe._groq(audio, ""))
    assert len(calls) == 1                                          # không có quãng trống ≥ 8s: không gọi thêm
    assert transcribe.find_holes([(0, 1, "a"), (12, 13, "b")], 30) == [(1, 12), (13, 30)]
    assert transcribe.find_holes([], 5) == []


def test_hallucination_detectors_keep_real_speech():
    for fake in ("Đừng quên đăng ký kênh để nhận thêm những video mới nhé.", "Hãy subscribe cho kênh nha",
                 "CÔNG THỨC MÌ XÀO BÒ ĐƠN GIẢN TẠI NHÀ", "ngon ngon quá ngon ngon quá ngon ngon quá"):
        assert transcribe._hallucinated({"text": fake}), fake
    for real in ("Lật mặt lật mặt cho vàng đều.", "Cho 100g bò, 1 muỗng dầu hào.", "Mì xào bò ngon quá trời luôn",
                 "Nhớ nêm nếm lại cho vừa ăn nha"):
        assert not transcribe._hallucinated({"text": real}), real


def test_groq_prompt_lowercases_shouting_title(monkeypatch):
    monkeypatch.setattr(config, "ASR_VOCAB", "")
    p = vocab.groq_prompt("CÔNG THỨC MÌ XÀO BÒ ĐƠN GIẢN TẠI NHÀ 🔥 #mixaobo", "")
    assert p.startswith("Công thức mì xào bò đơn giản tại nhà") and "CÔNG" not in p


def test_old_transcript_cache_is_redone(monkeypatch, tmp_path):
    audio = str(tmp_path / "audio.mp3")
    _silent_mp3(audio, 3)
    cache = str(tmp_path / "stt.json")
    monkeypatch.setattr(transcribe, "_CACHE", cache)
    key = transcribe._cache_key(audio)
    json.dump({key: {"text": "bản cũ thiếu lời", "segs": [[0, 1, "bản cũ thiếu lời"]]}}, open(cache, "w"))
    calls = []

    async def fresh(path, prompt=""):
        calls.append(path)
        return "lời mới đủ", [(0.0, 1.0, "lời mới đủ")]
    monkeypatch.setattr(transcribe, "_transcribe_uncached", fresh)
    text, _ = asyncio.run(transcribe.transcribe_segments(audio))
    assert text == "lời mới đủ" and len(calls) == 1
    text, _ = asyncio.run(transcribe.transcribe_segments(audio))      # bản mới đã lưu: lần sau lấy từ cache
    assert text == "lời mới đủ" and len(calls) == 1


# ---------------- gộp mẩu lời cụt ----------------

def test_merge_fragments_from_real_log():
    segs = [(46.4, 48.0, "để cho cái dao mình được chín điều nha."), (48.6, 49.4, "Sau khi mình"),
            (51.4, 56.0, "luộc cái dao mình nó chính khoảng 60% lúc này mọi người vớt ra cho mình."),
            (60.0, 62.5, "Rồi xong lúc này mọi người cho mình là 1 cối mì."),
            (86.5, 87.5, "cho gau vào."), (89.9, 96.0, "sau"),
            (96.7, 100.5, "khi bò nó tái tái thế này lúc này mình sẽ niêm gia vị nha các nha."),
            (134.8, 37.0 + 100, "rồi lúc này mọi người cho mình là 70g."), (137.4, 139.5, "cái thìa."),
            (139.7, 141.3, "70g cải thảo.")]
    out = prompt_engine.merge_sentences(segs, 173)
    texts = [t for _, _, t in out]
    assert "Sau khi mình luộc cái dao mình nó chính khoảng 60% lúc này mọi người vớt ra cho mình." in texts
    assert "sau khi bò nó tái tái thế này lúc này mình sẽ niêm gia vị nha các nha." in texts
    assert "rồi lúc này mọi người cho mình là 70g. cái thìa." in texts             # "cải thìa" về đúng câu
    assert "70g cải thảo." in texts and "cho gau vào." in texts
    starts = {t: a for a, _, t in out}
    # mẩu cụt được dời tới sát câu sau (không đọc "Sau." một mình rồi im 6 giây)
    assert 95.9 <= starts["sau khi bò nó tái tái thế này lúc này mình sẽ niêm gia vị nha các nha."] <= 96.7
    assert 50.0 <= starts["Sau khi mình luộc cái dao mình nó chính khoảng 60% lúc này mọi người vớt ra cho mình."] < 51.4
    assert not any(len(t.split()) <= 2 for t in texts)


def test_merge_does_not_pull_new_sentence_start_backwards():
    segs = [(0.0, 3.0, "Cho dầu vào chảo."), (4.0, 4.5, "Rồi"), (4.6, 7.0, "phi tỏi cho thơm.")]
    assert [t for *_, t in prompt_engine.merge_sentences(segs, 10)] == ["Cho dầu vào chảo.", "Rồi phi tỏi cho thơm."]


def test_short_slots_merge_into_natural_lines():
    """Người gốc nói liền, máy cắt mỗi ~2s: gộp thành câu 3-6s (giọng mới đủ chỗ đọc câu tự nhiên). Có quãng nghỉ dài
    hoặc câu gộp quá SENT_MAX thì giữ riêng."""
    segs = [(11.5, 13.8, "Mọi người cho mình là 1 phần 3 muỗng đường,"), (13.9, 16.0, "1 phần 4 muỗng dầu hào,"),
            (16.2, 18.1, "1 phần 3 muỗng nước tương."), (18.3, 19.8, "Và mọi người cho mình là 1 muỗng dầu ăn."),
            (30.0, 31.5, "Đảo đều tay nha."), (34.0, 37.0, "Đậy nắp lại chừng 5 phút.")]
    out = prompt_engine.merge_sentences(segs, 60)
    assert [t for *_, t in out] == ["Mọi người cho mình là 1 phần 3 muỗng đường, 1 phần 4 muỗng dầu hào,",
                                    "1 phần 3 muỗng nước tương. Và mọi người cho mình là 1 muỗng dầu ăn.",
                                    "Đảo đều tay nha.", "Đậy nắp lại chừng 5 phút."]
    assert all(b - a <= prompt_engine.SENT_MAX for a, b, _ in out)


# ---------------- xếp giọng: lấn khe đoạn sau trước khi nói nhanh / bắt AI rút gọn ----------------

def test_plan_voice_borrows_next_slack_before_speeding_up():
    segs = [{"start": 0.0, "slot": 3.0, "span": 3.0}, {"start": 3.0, "slot": 3.0, "span": 3.0},
            {"start": 6.0, "slot": 3.0, "span": 3.0}]
    plan, too_long = core_video.plan_voice([3.6, 1.0, 2.0], segs, 9.0)
    base = core_video.voice_base_factor([3.6, 1.0, 2.0], segs, 9.0)
    assert too_long == [] and all(abs(p["factor"] - base) < 1e-6 for p in plan)   # một tốc độ, không nén lời
    assert plan[1]["start"] - 3.0 <= core_video.VOICE_SPILL_MAX + 0.12 + 1e-6      # đoạn sau lùi tối đa ~1s
    assert plan[1]["end"] <= 6.0 and plan[2]["start"] == 6.0                       # và vẫn xong trong khe của nó
    for a, b in zip(plan, plan[1:]):
        assert b["start"] >= a["end"]
    # đoạn sau KHÔNG còn dư khe: chỉ lấn 0.35s như cũ -> đoạn đầu vẫn bị báo quá dài
    _, too_long = core_video.plan_voice([3.6, 2.9, 2.0], segs, 9.0)
    assert too_long == [0]


def test_spoken_words_counts_numbers_as_read():
    assert prompt_engine.spoken_words("Cho 100g bò") == 5                  # cho một trăm gam bò
    assert prompt_engine.spoken_words("Luộc 60% thì vớt") == 7


# ---------------- Kaggle: tách giọng cả bài, song song với đọc giọng ----------------

def test_kaggle_separates_whole_track_in_parallel(tmp_path, monkeypatch):
    import kaggle_job
    import vocal_sep
    from test_kaggle import _video
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    inp = str(tmp_path / "in.mp4")
    _video(inp)
    seen = {}

    async def fake_remove_voice(src, out, timeout=0, windows="chưa gọi"):
        seen["windows"] = windows
        seen["sep_started"] = True
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=6",
                        "-ac", "2", out], check=True)
        return True
    monkeypatch.setattr(vocal_sep, "remove_voice", fake_remove_voice)

    async def synth(items, tmp):
        await asyncio.sleep(0.3)
        seen["sep_started_before_tts_done"] = seen.get("sep_started", False)
        p = os.path.join(tmp, "v0.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.2", p],
                       check=True)
        return {0: {"path": p, "words": None}}
    spec = {"mode": "cook", "out_dur": 6.0, "render_dur": 0, "stretch": 1.0, "allow_hd": True, "title": "",
            "subs": False, "pos_y": None, "bands": [], "sep": {"ok": True, "windows": [[0.0, 2.0]]},
            "speech": [[0.5, 2.0]],
            "segments": [{"i": 0, "start": 0.0, "slot": 6.0, "span": 2.0, "text": "Cho bò vô", "ost": "new"}]}
    res = asyncio.run(kaggle_job.produce(spec, inp, str(tmp_path), synth))
    assert seen["windows"] is None                       # cả bài, không theo quãng bóc lời thấy có người nói
    assert seen["sep_started_before_tts_done"] is True   # chạy song song với đọc giọng
    assert res["amb"] is True and "sep" in res["timings"] and os.path.getsize(res["video"]) > 0


def test_kaggle_sep_cancelled_when_tts_fails(tmp_path, monkeypatch):
    import kaggle_job
    import vocal_sep
    state = {}

    async def slow_remove_voice(src, out, timeout=0, windows=None):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return True
    monkeypatch.setattr(vocal_sep, "remove_voice", slow_remove_voice)

    async def bad_synth(items, tmp):
        await asyncio.sleep(0.05)
        raise RuntimeError("VieNeu hỏng")
    spec = {"mode": "cook", "out_dur": 6.0, "sep": {"ok": True}, "segments": [{"i": 0, "start": 0, "slot": 6,
                                                                               "text": "a", "ost": "new"}]}
    try:
        asyncio.run(kaggle_job.produce(spec, "in.mp4", str(tmp_path), bad_synth))
    except RuntimeError as e:
        assert "VieNeu" in str(e)
    assert state.get("cancelled") is True


def test_recheck_asks_gemini_when_whisper_misses_speech(monkeypatch, tmp_path):
    """Video cá kèo 12/11/2026: 0-29s người gốc nói liên tục (đo giọng tách ra: âm tiết 60-140ms như phần có lời) mà
    Whisper lượt đầu bịa "Các bạn hãy đăng ký kênh", nghe lại chỉ ra "Cá vào gãy là" -> nhờ Gemini nghe quãng đó."""
    audio = str(tmp_path / "audio.mp3")
    _silent_mp3(audio, 60)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(config, "ASR_ORDER", "groq,gemini,capcut")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-test")
    real = "Cá vào chảo dầu thì cá kèo sẽ tự động bung ra."
    calls = []

    async def fake_post(path, prompt, model="", language=""):
        calls.append(model)
        if len(calls) == 1:
            return 200, {"duration": 60.0, "language": "vi", "text": "x",
                         "segments": [{"start": 1.0, "end": 9.0, "text": "Các bạn hãy đăng ký kênh để ủng hộ kênh",
                                       "no_speech_prob": 0.1, "avg_logprob": -0.3},
                                      {"start": 30.0, "end": 34.0, "text": real, "no_speech_prob": 0.0,
                                       "avg_logprob": -0.2}],
                         "words": _words(1.0, "Các bạn hãy đăng ký kênh để ủng hộ kênh", 0.8) + _words(30.0, real, 0.35)}
        return 200, {"segments": [{"start": 27.0, "end": 28.0, "text": "Cá vào gãy là", "no_speech_prob": 0.3,
                                   "avg_logprob": -0.6}], "words": _words(27.0, "Cá vào gãy là", 0.25)}
    said = "Hôm nay mình làm món cá kèo chiên giòn rụm nha mọi người, ngâm cá trong nước muối nửa tiếng."
    gem = {}

    async def fake_gemini_words(path):
        gem["path"] = path
        return said, [(2.0 + 0.4 * k, 2.3 + 0.4 * k, w) for k, w in enumerate(said.split())]
    monkeypatch.setattr(transcribe, "_groq_post", fake_post)
    monkeypatch.setattr(transcribe, "_gemini_words", fake_gemini_words)
    text, segs = asyncio.run(transcribe._groq(audio, ""))
    joined = " ".join(t for *_, t in segs)
    assert "đăng ký" not in joined and "gãy" not in joined and "cá kèo chiên giòn" in joined and "nửa tiếng" in joined
    assert 1.5 <= segs[0][0] <= 2.5 and "nửa tiếng" in text and gem["path"].endswith(".mp3")


def test_gemini_not_used_when_removed_from_asr_order(monkeypatch, tmp_path):
    audio = str(tmp_path / "audio.mp3")
    _silent_mp3(audio, 30)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(config, "ASR_ORDER", "groq,capcut")

    async def boom(path):
        raise AssertionError("không được gọi Gemini")
    monkeypatch.setattr(transcribe, "_gemini_words", boom)
    assert asyncio.run(transcribe._recheck_gemini(audio, 0.0, 0.0, 20.0)) == ([], [])


def test_whisper_repetition_loops_dropped_but_extensions_kept():
    x = "Các cá kèo sẽ giòn lâu từ trong ra ngoài."
    segs = [{"start": 83.0, "text": x}, {"start": 90.4, "text": "Các cá không bị ngấm dầu ăn rất là ngon."},
            {"start": 93.9, "text": x}, {"start": 98.3, "text": f"{x} {x}"},
            {"start": 103.0, "text": "Cho cá vào chảo"}, {"start": 106.0, "text": "Cho cá vào chảo rồi chiên vàng đều hai mặt"},
            {"start": 165.9, "text": "Các bạn có thể nhìn thấy cá kèo,"},
            {"start": 173.2, "text": "đem vào một tí là nó mềm lại."},
            {"start": 176.5, "text": "Các bạn có thể nhìn thấy cá kèo, đem vào một tí là nó mềm lại."}]
    assert transcribe._loops(segs) == {2, 3, 8}


def test_merge_lone_closing_fragment_and_cut_sentence():
    segs = [(185.2, 188.0, "mình sẽ để cho nó ráo dầu"), (190.1, 193.7, "xuống."),
            (193.8, 196.5, "Sau khi các cá kèo đã giòn rồi, mình sẽ cho ra dĩa."),
            (82.1 + 120, 84.5 + 120, "Bởi vì cái lúc chiên cá kèo thì Các cá"),
            (84.6 + 120, 88.9 + 120, "kèo sẽ giòn lâu từ trong ra ngoài.")]
    texts = [t for *_, t in prompt_engine.merge_sentences(segs, 260)]
    assert "mình sẽ để cho nó ráo dầu xuống." in texts
    assert "Bởi vì cái lúc chiên cá kèo thì Các cá kèo sẽ giòn lâu từ trong ra ngoài." in texts
    assert not any(len(t.split()) <= 2 for t in texts)


# ---------------- bản 26: câu mở đầu, khoảng số chữ, phân tích hình nhẹ ----------------

def _fake_ai(monkeypatch, texts):
    seen = {}

    async def fake(prompt, images=None, schema=None, **kw):
        seen["prompt"] = prompt
        return json.dumps({"segments": [{"i": k, "text": t} for k, t in enumerate(texts)],
                           "title": "t", "caption": "c", "hashtags": "#a"})
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "OST_AUTO", "0")
    monkeypatch.setattr(prompt_engine, "genai", object())
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    return seen


def test_late_speech_gets_opening_hook(monkeypatch):
    """Video cá kèo 12/11/2026: lời bóc được bắt đầu ở 29.2s -> giọng mới im 29 giây đầu. Thêm câu mở đầu ở 0s."""
    seen = _fake_ai(monkeypatch, ["Cá kèo chiên giòn rụm, làm sao cho cá không nhảy ra khỏi chảo?",
                                  "Thả cá vô chảo dầu thì cá kèo tự bung ra nè.", "Ngâm cá chừng nửa tiếng."])
    segs = [(29.2, 32.0, "Cá vào chảo thì các cá kèo sẽ tự động bung ra."), (38.6, 42.0, "Ngâm khoảng nửa tiếng.")]
    out = asyncio.run(prompt_engine.generate_timed("Cá kèo chiên giòn", "", "cook", segs, 60.0))
    first = out["segments"][0]
    assert first["start"] == 0.0 and first["orig"] == "" and first["ost"] == "new" and "Cá kèo" in first["text"]
    assert out["segments"][1]["start"] == 29.2
    assert any(x.startswith("0 | 0.0s |") and x.endswith("(chưa có lời gốc)") for x in seen["prompt"].splitlines())
    # lời bắt đầu sớm: không thêm
    seen = _fake_ai(monkeypatch, ["Thả cá vô chảo nè.", "Ngâm cá nửa tiếng."])
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", [(1.0, 4.0, "cho cá vào chảo nha."),
                                                                     (9.0, 12.0, "ngâm nửa tiếng.")], 20.0))
    assert out["segments"][0]["start"] == 1.0
    assert not any(x.endswith("(chưa có lời gốc)") for x in seen["prompt"].splitlines())


def test_prompt_gives_word_range_not_only_max(monkeypatch):
    """Bản 25 ghi "tối đa n từ" thì AI chỉ viết ~67% (giọng phủ 53% khe, video lặng nhiều): giao khoảng lo-mx."""
    seen = _fake_ai(monkeypatch, ["Thả cá vô chảo dầu đang nóng già, cá kèo tự bung ra liền nè bà con."])
    asyncio.run(prompt_engine.generate_timed("", "", "cook", [(0.0, 5.5, "cho cá vào chảo dầu nóng thì cá kèo sẽ tự "
                                                                         "động bung ra nha mọi người")], 6.0))
    line = next(x for x in seen["prompt"].splitlines() if x.startswith("0 | 0.0s |"))
    lo, hi = map(int, line.split("|")[2].split()[0].split("-"))
    assert 0.75 * hi <= lo < hi and "tối đa" not in line


def _band_video(path, keyint):
    font = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "DejaVuSans-Bold.ttf")
    txt = f"drawtext=fontfile={font}:text='PHU DE GOC':fontsize=28:fontcolor=white:borderw=3:bordercolor=black:" \
          "x=(w-text_w)/2:y=h*0.15"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x6a8f5c:duration=5:size=360x640:rate=25",
                    "-f", "lavfi", "-i", "color=c=0x2a3f8c:duration=5:size=360x640:rate=25",
                    "-filter_complex", f"[0:v][1:v]concat=n=2:v=1[c];[c]{txt}[v]", "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", str(keyint), "-keyint_min", str(keyint),
                    "-sc_threshold", "0", path], check=True, capture_output=True)


def test_scene_analysis_modes(tmp_path, monkeypatch):
    import scenes
    vid = str(tmp_path / "v.mp4")
    _band_video(vid, 25)                                    # khung chính mỗi giây, đổi cảnh ở giây 5
    res = {}
    for m in ("light", "fast", "full"):
        d = tmp_path / m
        d.mkdir()
        cuts, bands = scenes._analyze_sync(vid, str(d), 10.0, True, 0.3, m)
        res[m] = (cuts, bands, dict(scenes._ANALYZED[vid]))
        assert bands and abs((bands[0][0] + bands[0][1]) / 2 - 0.17) < 0.04, (m, bands)   # chữ in cứng: cả 3 mức
    assert res["light"][0] == [] and len(res["light"][2]["times"]) == 10             # nhẹ: 10 ảnh khung chính có mốc
    assert res["light"][2]["times"][:3] == [0.0, 1.0, 2.0]
    for m in ("fast", "full"):
        assert any(abs(c - 5.0) < 0.3 for c in res[m][0]), (m, res[m][0])           # vừa/kỹ: dò được đổi cảnh
    monkeypatch.setattr(config, "SCENE_ANALYSIS", "light")
    scenes._analyze_sync(vid, str(tmp_path / "light2"), 10.0, False)
    imgs = asyncio.run(scenes.keyframes(vid, [(0.0, 1.0, ""), (6.0, 8.0, "")]))
    assert all(len(i) > 1000 for i in imgs) and imgs[0] != imgs[1]
    import cover
    times, frames = cover.frames_sync(vid, scenes._ANALYZED[vid]["dir"], scenes._ANALYZED[vid]["times"])
    assert list(times[:3]) == [0.0, 1.0, 2.0] and len(frames) == 10


def test_light_analysis_falls_back_when_keyframes_too_sparse(tmp_path):
    import scenes
    vid = str(tmp_path / "v.mp4")
    _band_video(vid, 1000)                                  # chỉ 1 khung chính cho cả video
    cuts, bands = scenes._analyze_sync(vid, str(tmp_path), 10.0, True, 0.3, "light")
    assert any(abs(c - 5.0) < 0.3 for c in cuts) and bands  # tự làm mức vừa
    assert not scenes._ANALYZED[vid].get("times")


def test_scene_analysis_setting_validated(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(config, "SCENE_ANALYSIS", "light")
    with pytest.raises(ValueError):
        config.save_settings({"SCENE_ANALYSIS": "nhanh"})
    config.save_settings({"SCENE_ANALYSIS": "fast"})
    assert config.SCENE_ANALYSIS == "fast"


def test_ingredient_rewrite_check_skips_asr_false_alarms():
    """Log thật: mỗi lần chạy đều có báo động giả do lỗi bóc lời (thiếu 'cua' từ "Các c. cua, cá kèo", 'mè' + bịa
    'dầu mè' từ "dòng mè", 'ớt' từ "ớt" = ướp) -> 2 lượt AI viết lại vô ích, AI có thể nhét cua vào món cá."""
    import ingredients as ing
    assert ing.check_rewrite("cho 5 giọt dòng mè", "chế 5 giọt dầu mè") == ([], [])
    assert ing.check_rewrite("Các c. cua, cá kèo. cá kèo giòn", "Cá kèo tươi ngon, cá kèo giòn") == ([], [])
    assert ing.check_rewrite("dừa nạo, vắt dừa", "vắt nước cốt dừa") == ([], [])
    # vẫn bắt: đổi sang thứ khác (kể cả tên này nằm trong tên kia) và bỏ nguyên liệu nhắc nhiều lần
    assert ing.check_rewrite("thêm gà vào, đảo gà", "thêm trứng gà vào") == (["thịt gà"], ["trứng gà"])
    assert ing.check_rewrite("hoa hồi, hoa hồi", "thả cá hồi") == (["hoa hồi"], ["cá hồi"])
    assert ing.check_rewrite("đập trứng gà vào", "đập trứng vịt vào")[1] == ["trứng vịt"]
    assert ing.check("cho 5 giọt dòng mè", "chế 5 giọt dầu mè") == (["mè"], ["dầu mè"])   # check() cũ không đổi


def test_gemini_recheck_has_time_cap(monkeypatch, tmp_path):
    audio = str(tmp_path / "a.mp3")
    _silent_mp3(audio, 5)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(config, "ASR_ORDER", "groq,gemini")
    monkeypatch.setattr(transcribe, "HOLE_GEMINI_TIMEOUT", 0.2)

    async def hang(path):
        await asyncio.sleep(30)
    monkeypatch.setattr(transcribe, "_gemini_words", hang)
    import time
    t0 = time.time()
    assert asyncio.run(transcribe._recheck_gemini(audio, 0.0, 0.0, 5.0)) == ([], [])
    assert time.time() - t0 < 5


def test_keep_length_long_video_becomes_full_remake_with_kaggle(monkeypatch):
    """Log 12/11/2026: «Giữ nguyên» + video 220s -> "vượt giới hạn 90s", gửi lại link 3 lần mới chạy."""
    import pipeline
    from queue_manager import Job
    monkeypatch.setattr(config, "KAGGLE_USE", "1")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    job = Job(url="https://www.facebook.com/reel/1", mode="cook", duration=0, voice="")
    assert pipeline.auto_full(job)
    assert not pipeline.auto_full(Job(url="u", mode="cook", duration=60, voice=""))        # chọn 60 giây: giữ
    assert not pipeline.auto_full(Job(url="u", mode="cook", duration=0, voice="", long_mode="split60"))
    assert not pipeline.auto_full(Job(url="u", mode="music", duration=0, voice=""))
    monkeypatch.setattr(config, "KAGGLE_USE", "0")
    assert not pipeline.auto_full(job)                                                   # không Kaggle: như cũ
