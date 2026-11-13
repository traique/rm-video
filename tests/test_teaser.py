"""Bản 29: video nấu ăn có lời mở đầu bằng 3 giây cảnh MÓN ĐÃ XONG (AI chọn, cũng là ảnh bìa) kèm câu hook có giọng.
Chọn cửa sổ trong cùng một cảnh, AI xác nhận món đã xong / video gốc chưa mở đầu bằng món, dựng thật bằng FFmpeg
(hình, tiếng nền, độ dài), cả luồng pipeline (mốc lùi 3 giây, giữ ≤ 90s cho Reels, bản duyệt dùng lại), Kaggle."""
import asyncio
import os
import subprocess

import pytest

import config
import core_video
import cover as cover_mod
import kaggle_worker
import pipeline
import prompt_engine
import review
import scenes
import state
from queue_manager import Job
from test_pipeline import ORIG, _video, fakes  # noqa: F401  (fakes: fixture dùng chung)

HOOK = "Thịt kho mềm rục nè bà con!"


def _red_blue(path):
    """Nguồn 12s: 0-6s đỏ, 6-12s xanh; tiếng là nhiễu hồng (như tiếng xèo)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=360x640:r=25:d=6", "-f", "lavfi",
                    "-i", "color=blue:s=360x640:r=25:d=6", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3:d=12",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", path], check=True)
    return path


def _color(path, t) -> str:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1", "-vf", "scale=8:8",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    r, b = sum(raw[0::3]) / 64, sum(raw[2::3]) / 64
    return "R" if r > b + 40 else "B" if b > r + 40 else "?"


def _noise_db(path, a, b) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-vn", "-af",
                        f"atrim={a}:{b},highpass=f=2000,highpass=f=2000,astats", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float([x for x in r.stderr.splitlines() if "RMS level dB" in x][-1].split()[-1])


# ---------------- cửa sổ + phụ đề + đồ thị lọc ----------------

def test_teaser_window_stays_in_one_scene():
    w = core_video.teaser_window
    assert w(40.0, [30.0, 50.0], 60.0) == (40.0, False)      # đủ chỗ: bắt đầu đúng khung AI chọn (khung đầu = ảnh bìa)
    assert w(48.5, [30.0, 50.0], 60.0) == (46.85, False)     # sát cuối cảnh: lùi về trước, không vắt sang cảnh sau
    assert w(41.0, [40.0, 42.5], 60.0) == (41.0, True)       # cảnh 2.5s < 3s: đứng hình tại khung đó
    assert w(57.5, [], 59.0) == (55.85, False)               # không vượt quá phần sẽ render (đã cắt quảng cáo cuối)


def test_hook_subtitle_hidden_only_under_big_hook_text(monkeypatch):
    segs = [{"start": 0.0, "text": HOOK}, {"start": 3.1, "text": "Cho thịt vào nồi"},
            {"start": 6.0, "text": "", "ost": "orig"}]
    monkeypatch.setattr(config, "BRAND_INTRO", "1")
    assert core_video.subtitle_texts(segs, {"dur": 3.0}) == ["", "Cho thịt vào nồi", ""]
    assert core_video.subtitle_texts(segs, None) == [HOOK, "Cho thịt vào nồi", ""]
    monkeypatch.setattr(config, "BRAND_INTRO", "0")                  # không có chữ hook to: giữ phụ đề câu hook
    assert core_video.subtitle_texts(segs, {"dur": 3.0}) == [HOOK, "Cho thịt vào nồi", ""]


def test_build_filter_teaser_graph():
    fc = core_video.build_filter("cook", 20.0, True, None, None, ambient_src="[2:a]", stretch=1.2,
                                 ost=[(5.0, 7.0, 1.0)], teaser={"iv": 4, "ia": 3, "dur": 3.0, "still": False})
    v, a = fc.split("[v];", 1)
    assert v.startswith("[4:v]") and "[tzv][mnv]concat=n=2:v=1:a=0" in v and "eval=frame" in v and "tpad" not in v
    assert "[3:a]" in a and "[tza][mna]concat=n=2:v=0:a=1[ambx]" in a and "adelay=3000:all=1" in a
    fc2 = core_video.build_filter("cook", 20.0, True, None, None, speech_gate=[(4.0, 6.0)],
                                  teaser={"iv": 3, "ia": None, "dur": 3.0, "still": True})
    assert "anullsrc" in fc2 and "tpad=stop_mode=clone" in fc2         # không có bản tách giọng: đoạn mở đầu im


def test_render_teaser_for_real(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "1")
    src = _red_blue(str(tmp_path / "src.mp4"))
    voice, amb = str(tmp_path / "voice.wav"), str(tmp_path / "amb.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=15", "-ar", "24000",
                    "-ac", "1", voice], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "2", "-ar", "44100", amb], check=True)

    async def go(name, **kw):
        out = str(tmp_path / f"{name}.mp4")
        await core_video.render(src, voice, out, "cook", 0, None, None, kw.pop("amb", amb), kw.pop("gate", None),
                                allow_hd=False, title="THỊT KHO", tmp=str(tmp_path), **kw)
        return out, await core_video.probe_duration(out)
    tz = {"a": 7.0, "dur": 3.0, "still": False}
    out, d = asyncio.run(go("teaser", teaser=tz))
    assert d == pytest.approx(15.0, abs=0.1)                                  # 12s + 3s mở đầu
    assert [_color(out, t) for t in (0.05, 2.5, 3.5, 9.5)] == ["B", "B", "R", "B"]   # cảnh giây 7 trước, rồi từ đầu
    assert _noise_db(out, 0.3, 2.7) > -45                                     # tiếng xèo của chính cảnh đó dưới giọng
    out, d = asyncio.run(go("stretch", teaser=tz, stretch=1.2))
    assert d == pytest.approx(17.4, abs=0.1) and _color(out, 0.05) == "B" and _color(out, 9.5) == "R"
    out, d = asyncio.run(go("gate", teaser={**tz, "still": True}, amb=None, gate=[(4.0, 6.0)]))
    assert d == pytest.approx(15.0, abs=0.1) and _color(out, 0.05) == "B" and _color(out, 3.5) == "R"
    assert _noise_db(out, 0.3, 2.7) < -55                                     # chưa tách giọng: đoạn mở đầu không có tiếng gốc


# ---------------- AI: món đã xong? video gốc đã mở đầu bằng món? ----------------

def test_ai_judge_with_opening_frame(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(prompt_engine, "genai", object())
    seen = {}

    async def fake(prompt, images, schema=None, label="", timeout=60):
        seen.update(n=len(images), prompt=prompt)
        return '{"best": 1, "reason": "dĩa thịt kho", "finished": true, "opens_with_dish": false}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    heads = [b"o1", b"o2", b"o3", b"o4"]                                     # 4 khung trải trên 12 giây đầu video gốc
    d = asyncio.run(cover_mod.ai_judge([b"a", b"b", b"c"], "Thịt kho", "cook", opening=heads, opening_secs=12))
    assert d == {"best": 1, "finished": True, "opens_with_dish": False} and seen["n"] == 7
    assert "Ảnh 3 đến Ảnh 6 (các ảnh cuối) là các khung trong 12 giây ĐẦU" in seen["prompt"]
    assert "kể cả chỉ thoáng qua" in seen["prompt"] and seen["prompt"].count("Trả về DUY NHẤT JSON") == 1

    async def picks_opening(*a, **k):                                         # chọn nhầm khung đầu video: không hợp lệ
        return '{"best": 3, "reason": "", "finished": true, "opens_with_dish": false}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", picks_opening)
    assert asyncio.run(cover_mod.ai_judge([b"a", b"b", b"c"], "Thịt kho", "cook", opening=heads)) is None
    assert asyncio.run(cover_mod.ai_pick([b"a", b"b", b"c"], "Thịt kho", "cook")) is None


@pytest.mark.parametrize("moment, key, why", [
    (None, "k", "không chọn được khung hình"),
    ({"ai": False}, "", "cần key Gemini"),
    ({"finished": False}, "k", "AI không thấy cảnh món đã xong"),
    ({"opens_with_dish": True}, "k", "video gốc đã mở đầu bằng món"),
    ({"t": 2.0}, "k", "video gốc đã mở đầu bằng món"),
])
def test_plan_teaser_skips(monkeypatch, moment, key, why):
    monkeypatch.setattr(config, "GEMINI_API_KEY", key)

    async def best(*a, **k):
        assert k.get("opening") is True
        return None if moment is None else {"t": 40.0, "score": 1.0, "n": 6, "ai": True, "finished": True,
                                             "opens_with_dish": False, **moment}
    monkeypatch.setattr(cover_mod, "best_moment", best)
    tz, m, msg = asyncio.run(pipeline.plan_teaser("x.mp4", "cook", "Thịt kho", 60.0, [], 58.0))
    assert tz is None and why in msg and (m is None) == (moment is None)


def test_plan_teaser_picks_window(monkeypatch):
    async def best(*a, **k):
        return {"t": 40.0, "score": 1.0, "n": 6, "ai": True, "finished": True, "opens_with_dish": False}
    monkeypatch.setattr(cover_mod, "best_moment", best)

    async def near(path, t0, t1):
        assert t0 < 40.0 < t1
        return [41.5]                                                         # cảnh đổi 1.5s sau khung đẹp nhất
    monkeypatch.setattr(scenes, "cuts_near", near)
    seen = []
    monkeypatch.setattr(cover_mod, "opening_repeat_sync", lambda src, a, dur, win: seen.append((a, dur, win)) or (0.31, 2.0))
    tz, m, msg = asyncio.run(pipeline.plan_teaser("x.mp4", "cook", "Thịt kho", 60.0, [12.0], 58.0))
    assert tz == {"a": 38.35, "dur": 3.0, "still": False, "t": 40.0} and msg == ""
    assert seen == [(38.35, 3.0, 9.0)]                                       # so với 9 giây đầu (15% của 60s)
    # phần mở đầu video gốc dùng lại đúng cảnh này (video cá kèo: giây 3.5 giống cảnh giây 209.5 tới 0.97) -> bỏ
    monkeypatch.setattr(cover_mod, "opening_repeat_sync", lambda *a: (0.97, 3.5))
    tz, m, msg = asyncio.run(pipeline.plan_teaser("x.mp4", "cook", "Thịt kho", 60.0, [12.0], 58.0))
    assert tz is None and m["t"] == 40.0 and msg == "video gốc đã chiếu trước cảnh món ở giây 4"


def test_opening_window_and_times():
    assert [cover_mod.opening_window(d) for d in (8, 30, 60, 220)] == [3.0, 4.5, 9.0, 12.0]
    assert cover_mod.opening_times(12.0) == [0.5, 4.17, 7.83, 11.5]


def _clip(path, src):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s=360x640:r=25", "-t", "3", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path], check=True)


def test_opening_repeat_detects_reused_final_shot(tmp_path):
    """Video gốc mở đầu bằng đúng cảnh cuối (kiểu "xem trước món") -> giống ~1; mở đầu bằng cảnh khác -> thấp."""
    a, b, c = (str(tmp_path / f"{k}.mp4") for k in "abc")
    _clip(a, "smptebars")
    _clip(b, "mandelbrot")
    _clip(c, "testsrc2")

    def join(out, parts):
        lst = tmp_path / "l.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", out],
                       check=True)
        return out
    reused = join(str(tmp_path / "reused.mp4"), [a, b, b, b, a])      # 0-3s cảnh A ... 12-15s cảnh A
    fresh = join(str(tmp_path / "fresh.mp4"), [c, b, b, b, a])        # mở đầu bằng cảnh khác
    sim, at = cover_mod.opening_repeat_sync(reused, 12.0, 3.0, 4.0)
    assert sim >= cover_mod.REPEAT_SIM and at < 3.0
    sim, _ = cover_mod.opening_repeat_sync(fresh, 12.0, 3.0, 4.0)
    assert sim < cover_mod.REPEAT_SIM
    assert cover_mod.opening_repeat_sync(str(tmp_path / "khong_co.mp4"), 12.0, 3.0, 4.0) == (0.0, 0.0)


def test_best_moment_sends_opening_frames_to_ai(tmp_path, monkeypatch):
    src = _red_blue(str(tmp_path / "rb.mp4"))
    thumbs = tmp_path / "th"
    thumbs.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "fps=2,scale=144:256", str(thumbs / "%05d.jpg")],
                   check=True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    got = {}

    async def judge(images, title, mode, opening=None, opening_secs=0.0):
        got.update(n=len(images), heads=len(opening or []), secs=opening_secs)
        return {"best": 0, "finished": True, "opens_with_dish": False}
    monkeypatch.setattr(cover_mod, "ai_judge", judge)
    m = asyncio.run(cover_mod.best_moment(src, "cook", "Cá", 12.0, thumbs_dir=str(thumbs), opening=True))
    assert m["ai"] and m["finished"] and got["n"] >= 2 and got["heads"] == 4 and got["secs"] == 3.0
    m = asyncio.run(cover_mod.best_moment(src, "cook", "Cá", 12.0, thumbs_dir=str(thumbs)))   # ảnh bìa thường
    assert got["heads"] == 0


def test_cuts_near_reads_only_the_window(tmp_path):
    src = _red_blue(str(tmp_path / "rb.mp4"))
    assert scenes._cuts_near_sync(src, 4.0, 8.0) == [pytest.approx(6.0, abs=0.1)]
    assert scenes._cuts_near_sync(src, 7.0, 11.0) == []


# ---------------- cả luồng pipeline ----------------

@pytest.fixture
def teaser_job(fakes, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(config, "CUT_PROMO", "0")
    monkeypatch.setattr(config, "BRAND_INTRO", "1")
    monkeypatch.setattr(config, "TEASER", "1")
    seen = {"moment": 0, "covers": [], "timed": {}, "render": {}}

    async def best(*a, **k):
        seen["moment"] += 1 if k.get("opening") else 0         # chỉ đếm lượt hỏi cho đoạn mở đầu (ảnh bìa thường thì không)
        return {"t": 4.5, "score": 1.0, "n": 6, "ai": True, "finished": True, "opens_with_dish": False}
    monkeypatch.setattr(cover_mod, "best_moment", best)

    async def near(*a):
        return []
    monkeypatch.setattr(scenes, "cuts_near", near)
    # video thử (testsrc) gần như đứng yên: mọi khung giống nhau, không phải "video gốc chiếu trước món"
    monkeypatch.setattr(cover_mod, "opening_repeat_sync", lambda *a: (0.1, 0.0))

    async def timed(title, desc, mode, segs, out_dur, images_for=None, teaser=0.0):
        seen["timed"] = {"segs": list(segs), "out_dur": out_dur, "teaser": teaser,
                         "images": await images_for([(0.0, 3.0, "")] + list(segs))}
        out = [{"i": 0, "start": 0.0, "slot": segs[0][0], "span": segs[0][0], "text": HOOK, "orig": ""}]
        out += [{"i": k + 1, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": f"Lời mới đoạn {k} nè",
                 "orig": t} for k, (a, b, t) in enumerate(segs)]
        return {"segments": out, "script": " ".join(x["text"] for x in out), "fresh": 1.0,
                "title": "Thịt kho", "caption": "Ngon", "hashtags": "#a", "hook": "THỊT KHO MỀM RỤC"}
    monkeypatch.setattr(prompt_engine, "generate_timed_any", timed)
    real_render, real_cover = core_video.render, cover_mod.make_cover

    async def render_spy(*a, **kw):
        seen["render"] = kw
        return await real_render(*a, **kw)
    monkeypatch.setattr(core_video, "render", render_spy)

    async def cover_spy(*a, **kw):
        seen["covers"].append(kw.get("at"))
        return await real_cover(*a, **kw)
    monkeypatch.setattr(cover_mod, "make_cover", cover_spy)
    src = str(tmp_path / "up" / "in.mp4")
    _video(src)                                                               # 8s
    return src, seen


def test_pipeline_opens_with_finished_dish_and_hook(teaser_job, tmp_path):
    src, seen = teaser_job
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert job.teaser == {"a": 4.5, "dur": 3.0, "still": False, "t": 4.5}
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(11.0, abs=0.15)   # 8s + 3s mở đầu
    t = seen["timed"]
    assert t["teaser"] == 3.0 and t["out_dur"] == pytest.approx(11.0)
    assert t["segs"][0][0] == pytest.approx(3.1, abs=0.01)    # câu gốc đầu tiên vào đúng lúc video chính bắt đầu
    assert [s[2] for s in t["segs"]] == [x for *_, x in ORIG]
    assert all(t["images"])                                    # ảnh cho AI: đoạn hook lấy cảnh món đã xong
    assert seen["render"]["teaser"]["a"] == 4.5 and seen["covers"] == [4.5]           # ảnh bìa = đúng khung đó
    assert job.final_segments[0]["start"] < 0.05 and HOOK.split()[0] not in job.srt  # giọng hook ngay giây 0, không phụ đề
    assert "Lời" in job.srt and "Mở đầu bằng 3 giây cảnh món đã xong" in job.error and job.hook == "THỊT KHO MỀM RỤC"
    assert seen["moment"] == 1


def test_teaser_kept_for_approved_script_and_reel_limit(teaser_job, tmp_path, monkeypatch):
    src, seen = teaser_job
    monkeypatch.setattr(pipeline, "REEL_MAX", 10)              # video ≤ "90s" thì thêm mở đầu cũng không vượt "90s"
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src, review=True)
    (tmp_path / "w1").mkdir()
    assert asyncio.run(pipeline.process(job, str(tmp_path / "w1"))) == "REVIEW"
    assert job.approved[0]["text"] == HOOK and job.teaser["a"] == 4.5 and job.out_dur == pytest.approx(10.0)
    job.status = "review"
    state.queue.jobs[job.id] = job
    new = review.approve_job(job, {1: "Cho thịt vô nồi đất nha"})
    assert new.teaser == job.teaser and new.hook == job.hook == "THỊT KHO MỀM RỤC"
    (tmp_path / "w2").mkdir()
    out = asyncio.run(pipeline.process(new, str(tmp_path / "w2")))
    assert seen["moment"] == 1                                 # bản duyệt dùng lại đúng đoạn mở đầu, không hỏi AI lại
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(10.0, abs=0.15)
    assert seen["render"]["teaser"]["a"] == 4.5 and "bớt 1s cuối cho vừa 10s" in new.error


@pytest.mark.parametrize("change", ["review_mode", "setting_off", "part_of_long_video", "no_speech_narrate"])
def test_teaser_only_for_narrated_cooking_videos(teaser_job, tmp_path, monkeypatch, change):
    src, seen = teaser_job
    job = Job(url="", mode="review" if change == "review_mode" else "cook", duration=0,
              voice=config.CAPCUT_VOICE_FEMALE, local=src)
    kw = {}
    if change == "setting_off":
        monkeypatch.setattr(config, "TEASER", "0")
    if change == "part_of_long_video":
        kw["teaser_ok"] = False
    if change == "no_speech_narrate":                          # video không lời: không làm mở đầu có giọng
        monkeypatch.setattr(config, "NO_SPEECH", "narrate")

        async def no_words(audio, *a, **k):
            return "", []
        monkeypatch.setattr(pipeline, "transcribe_segments", no_words)

        class Stop(Exception):
            pass

        async def stop(*a, **k):
            raise Stop()
        monkeypatch.setattr(prompt_engine, "generate_scenes", stop)
        monkeypatch.setattr(prompt_engine, "generate", stop)
        (tmp_path / "w").mkdir()
        with pytest.raises(Stop):
            asyncio.run(pipeline.process(job, str(tmp_path / "w"), **kw))
        assert seen["moment"] == 0 and not job.teaser
        return
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w"), **kw))
    assert seen["moment"] == 0 and not job.teaser and not seen["render"].get("teaser")
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(8.0, abs=0.15)


def test_no_teaser_note_when_ai_says_original_opens_with_dish(teaser_job, tmp_path, monkeypatch):
    src, seen = teaser_job

    async def opens(*a, **k):
        return {"t": 5.0, "score": 1.0, "n": 6, "ai": True, "finished": True, "opens_with_dish": True}
    monkeypatch.setattr(cover_mod, "best_moment", opens)
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(8.0, abs=0.15) and job.teaser == {}
    assert "Không chiếu trước cảnh món (video gốc đã mở đầu bằng món)" in job.error
    assert seen["covers"] == [5.0]                             # ảnh bìa vẫn là khung AI chọn, không hỏi AI lần nữa


# ---------------- Kaggle ----------------

def test_kaggle_spec_and_render_carry_teaser(tmp_path, monkeypatch):
    import kaggle_job
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "1")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    tz = {"a": 4.5, "dur": 3.0, "still": False, "t": 4.5}
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE)
    segs = [{"i": 0, "start": 0.0, "slot": 3.1, "span": 3.1, "text": HOOK, "ost": "new"},
            {"i": 1, "start": 3.1, "slot": 3.0, "span": 3.0, "text": "Cho thịt vào nồi đất", "ost": "new"}]
    spec = kaggle_worker.build_spec(job, segs, 11.0, 0, 1.0, True, "THỊT KHO", [], None, {"ok_remote": False},
                                    [(3.5, 5.0)], 90, teaser=tz)
    assert spec["teaser"] == {"a": 4.5, "dur": 3.0, "still": False}
    inp = str(tmp_path / "in.mp4")
    _video(inp)
    seen = {}
    real = core_video.render

    async def spy(*a, **kw):
        seen.update(kw)
        return await real(*a, **kw)
    monkeypatch.setattr(core_video, "render", spy)

    async def synth(items, tmp):
        out = {}
        for k, _ in items:
            p = os.path.join(tmp, f"v{k}.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.5",
                            "-ar", "48000", p], check=True)
            out[k] = {"path": p, "words": None}
        return out
    res = asyncio.run(kaggle_job.produce(spec, inp, str(tmp_path), synth))
    assert seen["teaser"] == spec["teaser"]
    assert asyncio.run(core_video.probe_duration(res["video"])) == pytest.approx(11.0, abs=0.15)
    assert "Cho" in res["srt"] and "Thịt" not in res["srt"]   # câu hook không có phụ đề (đã có chữ hook to)
