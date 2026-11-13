"""Bản 31: các lỗi tìm được khi rà toàn bộ repo (13/11/2026) — mỗi test là một kịch bản thật đã tái hiện trước khi sửa."""
import asyncio
import os
import re
import subprocess

import numpy as np
import pytest

import autopost
import config
import core_video
import cover as cover_mod
import ingredients
import jobs
import kaggle_api
import kaggle_worker
import pipeline
import prompt_engine as pe
import transcribe as tr
from queue_manager import Job
from test_kaggle import _local, _long_video, _video, kaggle_fakes, kaggle_on  # noqa: F401
from test_no_speech import _song, lib, silent_job  # noqa: F401
from test_pipeline import fakes  # noqa: F401
from test_teaser import teaser_job  # noqa: F401


# ---------------- lời thoại ----------------

def test_promo_tail_needs_a_strong_sales_cue():
    """"mua ở chợ", "giá rất rẻ" là lời nấu ăn thường: không được cắt mất bước cuối công thức."""
    segs = [(0, 5, "làm cá kèo chiên"), (10, 15, "cho dầu vào chảo"), (20, 25, "chiên lửa vừa"),
            (32, 36, "cá này mình mua ở chợ về đó"), (37, 41, "chiên vàng là xong rồi")]
    assert pe.find_promo_tail(segs, 42) is None
    assert pe.find_promo_tail(segs[:3] + [(32, 36, "gà các bạn nhớ mua gà ta nha, giá rất rẻ"), segs[4]], 42) is None
    strong = segs[:3] + [(32, 36, "bột này mua ở giỏ hàng góc trái màn hình nha"), (37, 41, "giá rất tốt luôn")]
    assert pe.find_promo_tail(strong, 42) == 31.95                      # có dấu hiệu mạnh: cắt như cũ


def test_strip_cta_keeps_the_cooking_step_in_unpunctuated_line():
    line = "các bạn nhớ đăng ký kênh ủng hộ mình nha rồi cho 2 muỗng đường vào"
    assert pe.strip_sub_cta(line) == "cho 2 muỗng đường vào" and not pe.is_sub_cta_line(line)
    assert pe.strip_sub_cta("Xong rồi, cá giòn rụm, nhớ đăng ký kênh nha") == "Xong rồi, cá giòn rụm."
    assert pe.is_sub_cta_line("nhớ đăng ký kênh ủng hộ mình nha")
    assert not pe.has_sub_cta("Theo dõi để thịt không bị cháy nha.")       # "theo dõi" trong bếp
    assert pe.has_sub_cta("theo dõi để xem thêm video mới") and pe.has_sub_cta("nhớ theo dõi kênh nha")


def test_cta_regex_ignores_ordinary_buying_and_exclusive_recipe():
    assert not pe._CTA_RE.search("Các bạn mua cá kèo về rửa sạch với muối cho hết nhớt nha.")
    assert not pe._CTA_RE.search("Bí quyết độc quyền giúp cá giòn lâu nè!")
    assert pe._CTA_RE.search("bà con nhớ mua ngay sản phẩm") and pe._CTA_RE.search("bột độc quyền của em")


def test_whisper_filters_keep_real_speech():
    assert not tr._outro_only("Vậy là xong rồi, cho thêm 2 muỗng đường nữa, các bạn nhớ đăng ký kênh nha")
    assert tr._outro_only("Đừng quên đăng ký kênh để nhận thêm những video mới nhé.")
    assert tr._loops([{"start": 10, "text": "Cho một muỗng đường"}, {"start": 20, "text": "Cho một muỗng đường nữa"}]) == set()
    same = "Các cá kèo sẽ giòn lâu từ trong ra ngoài"
    assert tr._loops([{"start": 10, "text": same}, {"start": 14, "text": same}]) == {1}
    units = [(k * 0.5, k * 0.5 + 0.4, w) for k, w in
             enumerate("mình mua cá kèo ở chợ Bà Chiểu về rửa sạch với muối cho hết nhớt nha".split())]
    assert [t for *_, t in tr.chunk_units(units)][0] == "mình mua cá kèo ở chợ Bà Chiểu về"   # tên riêng không cắt câu


def test_ingredient_and_number_false_alarms():
    cr, cn = ingredients.check_rewrite, ingredients.check_numbers
    assert cr("ướp thịt 10 phút cho thấm", "Tiến hành ướp thịt 10 phút cho thấm gia vị nha") == ([], [])
    assert cr("đập 2 quả trứng vào tô, đánh tan trứng", "Đập 2 trái hột gà vô tô rồi đánh tan nha") == ([], [])
    assert cr("cá kèo chiên", "Theo cá nhân mình cá kèo chiên ngon") == ([], [])
    assert cr("thịt kho", "thịt kho ăn với cơm nóng") == ([], [])
    assert cr("thịt kho trứng", "thịt kho hột vịt")[1] == ["trứng vịt"]            # đổi loại trứng vẫn bắt
    assert cn("cho 1 phần 3 muỗng đường", "Cho 1/3 muỗng đường vô") == ([], [])
    assert cn("đợi một lát cho thấm", "chờ xíu cho thấm") == ([], [])
    assert cn("Cắn một miếng thử", "Cắn thử một miếng") == ([], [])
    assert cn("ướp 2 muỗng đường", "ướp 3 muỗng đường") == (["2 muỗng"], ["3 muỗng"])


def test_word_budget_counts_spoken_words_and_fits_teaser(monkeypatch):
    pe.set_voice_rate(3.1)
    seen = {}

    async def fake(prompt, *a, **k):
        seen["prompt"] = prompt
        raise RuntimeError("stop")
    monkeypatch.setattr(pe, "_call_gemini", fake)
    monkeypatch.setattr(pe, "genai", object())
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    segs = [(32.2, 41.0, "Ướp với 100g đường, 50ml nước mắm, 2 muỗng dầu hào trong 15 phút."), (42.0, 45.0, "Đảo đều lên nha.")]
    asyncio.run(pe.generate_timed("t", "", "cook", segs, 60.0, teaser=3.0))
    lines = [x for x in seen["prompt"].splitlines() if re.match(r"^\d+ \| ", x)]
    assert lines[0].startswith("0 | 0.0s | 7-9 từ")                       # câu hook vừa 3.4s, không phải 5.5s
    lo, hi = map(int, lines[1].split(" | ")[2].split()[0].split("-"))
    assert hi >= 21 and pe.spoken_words(segs[0][2]) == 21                 # "100g" = 3 chữ đọc ra
    pe.set_voice_rate(None)


def test_platform_post_and_word_target():
    post = pe.platform_post("Cá kèo", "Cá kèo chiên giòn rụm, không văng dầu, ai cũng làm được.",
                            "#cakeochiengionkhongvangdau #monngonmoingayhomnay #bepvietnamngon #nauanngon #asmr #fyp")
    tiktok = post.split("▶ TikTok:\n", 1)[1].split("\n", 1)[0]
    assert tiktok.startswith("Cá kèo chiên giòn rụm, không văng dầu, ai cũng làm được.") and len(tiktok) <= 150
    assert pe._word_target(5)[0] == "5-15" and pe._word_target(30)[0] == "69-79"


# ---------------- tự động đăng ----------------

def test_facebook_description_is_only_the_facebook_section():
    job = Job(url="u", mode="cook", duration=0, voice="")
    job.post = pe.platform_post("Cá kèo giòn", "Thơm nức 😋", "#a #b #c #d", credit="")
    title, desc = autopost._post_text(job)
    assert title == "Cá kèo giòn" and desc == "Thơm nức 😋\n\n#a #b #c"
    assert "▶" not in desc and "#Shorts" not in desc and "📌" not in desc
    job.post = "📌 Mì xào bò ngon\nCaption #monngon"                       # định dạng cũ: giữ nguyên
    assert autopost._post_text(job) == ("Mì xào bò ngon", "📌 Mì xào bò ngon\nCaption #monngon")


# ---------------- pipeline ----------------

def test_split90_part_longer_than_90s_is_rendered_whole(fakes, tmp_path, monkeypatch):  # noqa: F811
    import scenes
    monkeypatch.setattr(config, "COVER", "0")
    monkeypatch.setattr(config, "KAGGLE_USE", "0")
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out")
    src = str(tmp_path / "up" / "long.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=200:size=180x320:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=200", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "30", "-c:a", "aac", "-shortest", src], check=True)
    assert scenes.split_points(200.0, 90, [], []) == [0.0, 90.0, 200.0]       # phần 2 dài 110s
    seen = []

    async def fake_render(inp, voice, out, mode, duration=0, *a, **kw):
        seen.append((round(await core_video.probe_duration(inp), 1), duration))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=16x16:rate=5",
                        out], check=True)
        return duration
    monkeypatch.setattr(core_video, "render", fake_render)
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src, long_mode="split90",
              source="web")
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process_split(job, str(tmp_path / "w")))
    assert [round(x) for x, _ in seen] == [90, 110] and all(d == 0 for _, d in seen)   # trước đây phần 2 bị cắt còn 90s


def test_batched_kaggle_parts_deliver_their_cover(kaggle_fakes, kaggle_on, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(config, "COVER", "1")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out")
    src = str(tmp_path / "up" / "long.mp4")
    _long_video(src)                                  # 130s -> 2 phần ~65s

    async def fake_batch(specs, tmp, on_step=None):   # như _run_sync: video nằm ở thư mục gốc của job
        res = []
        for k, sp in enumerate(specs):
            if k == 1:
                res.append(kaggle_api.KaggleError("Kaggle hết giờ"))   # phần 2 làm lại trên Botkeep
                continue
            out = os.path.join(tmp, f"output_{k}.mp4")
            _video(out, 3)
            plan = [{"start": s["start"], "factor": 1.0, "end": s["start"] + 1} for s in sp["segments"]]
            res.append({"video": out, "plan": plan, "too_long": [], "srt": "", "amb": False, "notes": [],
                        "timings": {"tts": 1, "render": 2}, "gpu": True})
        return res
    monkeypatch.setattr(kaggle_worker, "produce_batch", fake_batch)
    job = Job(url="", mode="cook", duration=0, voice="vieneu:Mỹ Duyên", local=src, long_mode="split60", source="web")
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process_split(job, str(tmp_path / "w")))
    labels = [p["label"] for p in job.parts]
    n = len([x for x in labels if x.startswith("Phần")])
    assert n >= 2 and all(f"Ảnh bìa Phần {k}/{n}" in labels for k in range(1, n + 1))
    assert all(os.path.exists(p["path"]) for p in job.parts)


def test_kaggle_keeps_fractional_render_duration(tmp_path, monkeypatch):
    import kaggle_job
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    inp = str(tmp_path / "in.mp4")
    _video(inp, 8)

    async def synth(items, tmp):
        out = {}
        for k, _ in items:
            p = os.path.join(tmp, f"v{k}.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.0",
                            "-ar", "48000", p], check=True)
            out[k] = {"path": p, "words": None}
        return out
    spec = {"mode": "review", "out_dur": 5.45, "render_dur": 5.45, "stretch": 1.0, "allow_hd": False, "title": "",
            "subs": False, "pos_y": None, "bands": [], "sep": {"ok": False}, "speech": [],
            "segments": [{"i": 0, "start": 4.2, "slot": 1.25, "span": 1.0, "text": "Xong rồi nè", "ost": "new"}]}
    res = asyncio.run(kaggle_job.produce(spec, inp, str(tmp_path), synth))
    assert asyncio.run(core_video.probe_duration(res["video"])) == pytest.approx(5.45, abs=0.08)   # trước đây 5.00


def test_kaggle_result_from_another_job_is_rejected(monkeypatch, tmp_path):
    spec = kaggle_worker._common({"kind": "remake", "job": 1})
    assert len(spec["token"]) == 16
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 0})
    monkeypatch.setattr(kaggle_api, "push", lambda *a, **k: None)
    monkeypatch.setattr(kaggle_api, "wait", lambda *a, **k: None)
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": [{"fileName": "result.json", "url": "r"},
                                                                       {"fileName": "output.mp4", "url": "v"}]})
    import json

    def dl(url, dst, *a):
        if url == "r":
            json.dump({"ok": True, "token": "khac"}, open(dst, "w"))
        else:
            _video(dst, 2)
    monkeypatch.setattr(kaggle_api, "download", dl)
    res = kaggle_worker._run_sync({**spec, "voice": {"engine": "edge"}}, str(tmp_path), lambda s: None)
    assert isinstance(res[0], kaggle_api.KaggleError) and "không phải của việc này" in str(res[0])


def test_silent_source_goes_to_music_path_without_transcribing(silent_job, monkeypatch, tmp_path):  # noqa: F811
    job, work, beds = silent_job
    silent = str(tmp_path / "silent.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=360x640:rate=25", "-an",
                    "-c:v", "libx264", "-preset", "ultrafast", silent], check=True)
    job.local = silent

    async def boom(*a, **k):
        raise AssertionError("nguồn không có tiếng: không được bóc lời")
    monkeypatch.setattr(pipeline, "transcribe_segments", boom)
    out = asyncio.run(pipeline.process(job, work))
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(6.0, abs=0.3)
    assert beds and beds[0][1] is None and "không lời" in job.script


def test_music_only_template_post_blocks_autopost(silent_job, monkeypatch):  # noqa: F811
    job, work, _ = silent_job
    import bgm
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "nen.mp3"))                           # có nhạc: lý do duy nhất là AI lỗi
    monkeypatch.setattr(config, "SOURCE_CREDIT", "1")
    job.source_meta = {"uploader": "Bếp Quê"}

    async def template(title, desc, mode):
        return {"title": "Video hot hôm nay", "caption": "x", "hashtags": "#a", "hook": "", "fallback": True}
    monkeypatch.setattr(pe, "post_only", template)
    asyncio.run(pipeline.process(job, work))
    assert "AI viết nội dung đăng lỗi" in job.no_autopost
    assert "Nguồn: Bếp Quê" in job.post and "Giọng đọc AI" not in job.post   # không có giọng đọc


def test_teaser_total_never_exceeds_duration_limit(teaser_job, tmp_path, monkeypatch):  # noqa: F811
    src, seen = teaser_job
    monkeypatch.setattr(config, "MAX_DURATION", 10)                 # video 8s + 3s mở đầu > giới hạn 10s
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert job.out_dur == pytest.approx(10.0) and "bớt 1s cuối cho vừa 10s" in job.error
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(10.0, abs=0.15)


def test_budget_timeout_is_not_a_network_timeout():
    assert issubclass(jobs._OverBudget, Exception) and not issubclass(jobs._OverBudget, TimeoutError)


# ---------------- ảnh bìa / đoạn mở đầu ----------------

def test_opening_repeat_ignores_overlap_with_itself(tmp_path):
    parts = []
    for k, srcf in enumerate(("smptebars", "mandelbrot", "testsrc2", "testsrc", "rgbtestsrc", "color=red", "color=blue")):
        p = str(tmp_path / f"{k}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{srcf}{':' if '=' in srcf else '='}s=360x640:r=25",
                        "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", p], check=True)
        parts.append(p)
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    src = str(tmp_path / "all.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", src], check=True)
    sim, _ = cover_mod.opening_repeat_sync(src, 12.5, 3.0, 12.0)      # cảnh sát phần mở đầu: không so với chính nó
    assert sim < cover_mod.REPEAT_SIM
    assert cover_mod.opening_repeat_sync(src, 1.0, 3.0, 12.0)[0] >= cover_mod.REPEAT_SIM   # cảnh nằm ngay phần mở đầu
    assert cover_mod.opening_repeat_sync(src, 0.0, 3.0, 12.0) == (0.0, 0.0)


# ---------------- bản 32: giọng gốc còn sót trong tiếng nền đã tách ----------------

def test_residual_voice_mask_cleans_ambient_but_keeps_noise():
    """Nền = nhiễu hồng + 5% "giọng" (chùm tông 300-2500Hz ngắt quãng); mặt nạ phổ dựa trên giọng tách được phải
    hạ phần giọng ≥ 15 dB mà nhiễu (tiếng xèo) giữ gần nguyên; chỗ không có giọng giữ nguyên hoàn toàn."""
    import vocal_sep
    rng = np.random.default_rng(0)
    sr, n = vocal_sep.SR, vocal_sep.SR * 8
    t = np.arange(n) / sr
    noise = np.cumsum(rng.standard_normal(n)) ; noise = noise - np.convolve(noise, np.ones(400) / 400, "same")
    noise = (noise / np.abs(noise).max() * 0.2).astype(np.float32)
    env = ((np.sin(2 * np.pi * 3 * t) > 0) & (t < 5)).astype(np.float32)        # giọng chỉ trong 5s đầu, ngắt quãng
    voice = (sum(np.sin(2 * np.pi * f * t) for f in (300, 700, 1400, 2500)) / 4 * env).astype(np.float32)
    amb = np.stack([noise + 0.05 * voice] * 2)
    voc = np.stack([voice] * 2)
    out = vocal_sep.suppress_residual(amb, voc)
    sp = slice(sr, 4 * sr)
    gain = lambda a: abs(float(np.dot(a[0, sp], voice[sp]) / np.dot(voice[sp], voice[sp])))
    assert gain(amb) == pytest.approx(0.05, abs=0.01) and gain(out) < 0.05 / 5.6      # giảm > 15 dB
    db = lambda x: 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9)
    assert db(out[0, sp]) > db(noise[sp]) - 3                                          # nhiễu giữ gần nguyên
    q = slice(6 * sr, 8 * sr)
    assert np.allclose(out[:, q], amb[:, q], atol=1e-3)                                # không có giọng: giữ nguyên


# ---------------- bản 33: lượt tách thứ 2 chỉ khi GPU; AI sửa lỗi bóc lời không phải bịa ----------------

def test_second_separation_pass_only_on_gpu(tmp_path, monkeypatch):
    import vocal_sep
    src = str(tmp_path / "s.mp4")
    _video(src, 3)
    calls = []
    monkeypatch.setattr(vocal_sep, "_separate", lambda mix, windows=None: calls.append(1) or np.zeros_like(mix))
    monkeypatch.setenv("SEP_PASSES", "2")
    monkeypatch.setattr(vocal_sep, "gpu_active", lambda: False)          # Kaggle CPU / Botkeep: một lượt + mặt nạ
    vocal_sep._split_sync(src, str(tmp_path / "a.wav"))
    assert len(calls) == 1
    monkeypatch.setattr(vocal_sep, "gpu_active", lambda: True)           # GPU: thêm lượt 2
    vocal_sep._split_sync(src, str(tmp_path / "b.wav"))
    assert len(calls) == 3 and os.path.getsize(str(tmp_path / "b.wav")) > 1000


def test_ai_fixing_transcription_typo_is_not_invention():
    cr, cn = ingredients.check_rewrite, ingredients.check_numbers
    assert cr("Cá kèo chiên giòn này mình sẽ ăn kèm là Một ít rau găm", "Ăn kèm một ít rau răm cho đậm đà") == ([], [])
    assert cr("cho nước mắn vào", "cho nước mắm vào") == ([], [])
    assert cr("cho thịt gà vào", "cho thịt bò vào")[1] == ["thịt bò"]           # đổi thật: vẫn bắt
    assert cn("ngâm khoảng nửa tiếng", "ngâm 30 phút") == ([], [])             # nửa tiếng = 30 phút
    assert cn("tầm 1 tí con cá", "Một lát là cá tự bung") == ([], [])          # "một lát" chỉ thời gian ngắn
    assert cn("để yên", "chờ tầm 1 phút") == ([], ["1 phút"])                   # bịa số thật: vẫn bắt


# ---------------- bản 34: PhoWhisper (tuỳ chọn KAGGLE_ASR) ----------------

def test_phowhisper_option_in_spec_and_mount(tmp_path, monkeypatch):
    import kaggle_job
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="")
    monkeypatch.setattr(config, "KAGGLE_ASR", "phowhisper")
    assert kaggle_worker.analyze_spec(job, None, "Cá kèo chiên giòn", "", True, 90)["asr"] == "phowhisper"
    assert kaggle_worker.analyze_spec(job, None, "Fried fish", "", True, 90)["asr"] == "large-v3"   # không phải tiếng Việt
    monkeypatch.setattr(config, "KAGGLE_ASR", "large-v3")
    assert kaggle_worker.analyze_spec(job, None, "Cá kèo", "", True, 90)["asr"] == "large-v3"
    d = tmp_path / "notebooks" / "u" / "rmvideo-phowhisper" / "phowhisper-large-ct2"
    d.mkdir(parents=True)
    assert kaggle_job.mounted_dir("phowhisper-large-ct2", str(tmp_path)) is None     # chưa có model.bin: không phải model
    (d / "model.bin").write_bytes(b"x")
    assert kaggle_job.mounted_dir("phowhisper-large-ct2", str(tmp_path)) == str(d)
    assert kaggle_job.mounted_dir("khac", str(tmp_path)) is None


def test_analysis_push_falls_back_without_phowhisper_notebook(monkeypatch, tmp_path):
    pushes = []

    def push(slug, title, text, gpu=True, kernel_sources=None):
        pushes.append(kernel_sources)
        if kernel_sources:
            raise kaggle_api.KaggleError("Kaggle không nhận notebook: kernel source not found")
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 99999})
    monkeypatch.setattr(kaggle_api, "push", push)
    monkeypatch.setattr(kaggle_api, "wait", lambda *a, **k: None)
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": []})
    with pytest.raises(kaggle_api.KaggleError, match="không trả kết quả"):
        kaggle_worker._run_analysis_sync({"kind": "analyze", "src": {"url": "u"}, "token": "t", "asr": "phowhisper"},
                                         str(tmp_path), lambda s: None)
    assert pushes == [["rmvideo-phowhisper"], None]                     # thử gắn, lỗi thì chạy large-v3
