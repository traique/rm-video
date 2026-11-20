"""Bản 28: video KHÔNG LỜI -> chèn nhạc nền thay vì để AI xem hình rồi bịa lời (dễ nói sai sự thật của video gốc).
Thư viện nhạc nền (bgm.py), lớp tiếng nhạc + tiếng gốc (core_video.music_bed), đường xử lý trong pipeline (Botkeep và
Kaggle), việc "bed" trên Kaggle, bài đăng chỉ dựa vào tiêu đề/mô tả gốc, API web."""
import asyncio
import json
import os
import random
import shutil
import subprocess

import aiohttp
import pytest

import bgm
import cache
import config
import core_video
import kaggle_job
import kaggle_worker
import pipeline
import prompt_engine
from queue_manager import Job

LINK = "https://www.tiktok.com/@nhacnen/video/7300000000000000001"


def _video(path, dur=6):
    """Hình test + tiếng gốc là nhiễu hồng (như tiếng xèo): đo được tách bạch với nhạc (sóng sin 440Hz)."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=360x640:rate=25",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.3:duration={dur}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", path], check=True)
    return path


def _song(path, dur=12):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-c:a", "libmp3lame", "-b:a", "96k", path], check=True)
    return path


def _noise_db(path) -> float:
    """Mức tiếng gốc trong bản trộn: chỉ nhiễu hồng có năng lượng trên 2kHz (nhạc thử là sin 440Hz)."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "highpass=f=2000,highpass=f=2000,astats",
                        "-f", "null", "-"], capture_output=True, text=True)
    return float([x for x in r.stderr.splitlines() if "RMS level dB" in x][-1].split()[-1])


@pytest.fixture
def lib(monkeypatch, tmp_path):
    monkeypatch.setattr(bgm, "DIR", str(tmp_path / "bgm"))
    monkeypatch.setattr(bgm, "RECENT_FILE", str(tmp_path / "bgm_recent.json"))
    for k, v in (("BGM_LINKS", ""), ("NO_SPEECH", "music"), ("BGM_ORIG", "off"), ("PUBLIC_URL", "")):
        monkeypatch.setattr(config, k, v)
    return tmp_path


@pytest.fixture
def silent_job(lib, monkeypatch, tmp_path):
    """process() với video không ai nói: bóc lời rỗng; mọi bước AI viết lời / đọc giọng đều bị cấm."""
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "cache"))
    for k, v in (("BRAND_INTRO", "0"), ("QUALITY", "fast"), ("COVER", "0"), ("KAGGLE_ANALYZE", "0")):
        monkeypatch.setattr(config, k, v)

    async def no_words(audio, *a, **k):
        return "", []
    monkeypatch.setattr(pipeline, "transcribe_segments", no_words)

    async def forbidden(*a, **k):
        raise AssertionError("video không lời: không được để AI viết lời đọc / đọc giọng")
    for name in ("generate_timed_any", "generate_scenes", "generate"):
        monkeypatch.setattr(prompt_engine, name, forbidden)
    monkeypatch.setattr(core_video, "synth_clip_ex", forbidden)

    async def post(title, desc, mode):
        return {"title": "Cá kho tộ miền Tây", "caption": "Thơm nức 😋", "hashtags": "#nauan", "hook": "CÁ KHO TỘ"}
    monkeypatch.setattr(prompt_engine, "post_only", post)
    beds = []
    real = core_video.music_bed

    async def spy(*a, **k):
        beds.append(a)
        return await real(*a, **k)
    monkeypatch.setattr(core_video, "music_bed", spy)
    src = _video(str(tmp_path / "in.mp4"))
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src, title_hint="Cá kho tộ")
    work = tmp_path / "w"
    work.mkdir()
    return job, str(work), beds


# ---------------- thư viện nhạc nền ----------------

def test_library_pick_rotates_and_avoids_recent(lib, monkeypatch):
    monkeypatch.setattr(config, "BGM_LINKS", f"{LINK}, https://youtu.be/abc\nhttps://youtu.be/abc ftp://x.example/a.mp3")
    assert bgm.links() == [LINK, "https://youtu.be/abc"]
    os.makedirs(bgm.DIR)
    for n in ("a.mp3", "b.m4a", "ghi_chu.txt"):
        open(os.path.join(bgm.DIR, n), "wb").write(b"x")
    assert [x["name"] for x in bgm.library()[:2]] == ["tiktok.com @nhacnen 7300000000000000001", "youtu.be abc"]
    assert bgm.link_name("https://www.youtube.com/watch?v=xyz&t=3") == "youtube.com xyz"
    keys = [x["key"] for x in bgm.library()]
    assert keys == [LINK, "https://youtu.be/abc", "file:a.mp3", "file:b.m4a"]
    rnd = random.Random(0)
    seen = []
    for _ in range(9):
        it = bgm.pick(rnd)
        seen.append(it["key"])
        bgm.remember(it)
    assert all(len(set(seen[k:k + 4])) == 4 for k in range(len(seen) - 3))     # 4 bài: không lặp trong 4 lượt liền
    assert all("url" in bgm.pick(rnd, prefer_links=True) for _ in range(6))    # Kaggle không lấy được file khi thiếu PUBLIC_URL
    assert bgm.pick(rnd, exclude=set(keys) - {"file:b.m4a"})["key"] == "file:b.m4a"
    assert bgm.pick(rnd, exclude=set(keys)) is None


def test_save_upload_and_delete(lib):
    fn = bgm.save_upload("../../Nhạc Hay!.MP3", b"x" * 10)
    assert fn == "Nhạc_Hay.mp3" and os.path.isfile(os.path.join(bgm.DIR, fn))
    fn2 = bgm.save_upload("Nhạc Hay!.mp3", b"y")
    assert fn2 != fn and fn2.startswith("Nhạc_Hay_")                           # trùng tên: không ghi đè
    with pytest.raises(ValueError):
        bgm.save_upload("virus.exe", b"x")
    with pytest.raises(ValueError):
        bgm.save_upload("dai.mp3", b"x" * (bgm.MAX_MB * 1024 * 1024 + 1))
    assert not bgm.delete("../settings.json") and not bgm.delete("khong_co.mp3")
    assert bgm.delete(fn) and not os.path.exists(os.path.join(bgm.DIR, fn))


def test_settings_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    for k in ("NO_SPEECH", "BGM_ORIG", "BGM_LINKS"):
        monkeypatch.setattr(config, k, getattr(config, k))                    # trả lại giá trị cũ sau test
    for k, v in (("NO_SPEECH", "im_lang"), ("BGM_ORIG", "to"), ("BGM_LINKS", "nhạc hay")):
        with pytest.raises(ValueError):
            config.save_settings({k: v})
    config.save_settings({"NO_SPEECH": "narrate", "BGM_ORIG": "low",
                          "BGM_LINKS": "https://youtu.be/a\nhttps://youtu.be/a, https://x.example/b.mp3"})
    assert (config.NO_SPEECH, config.BGM_ORIG, config.BGM_LINKS) == \
        ("narrate", "low", "https://youtu.be/a,https://x.example/b.mp3")


def test_no_speech_threshold():
    assert pipeline.no_speech([], "")
    assert pipeline.no_speech([(0, 1, "ừm"), (3, 4, "ngon quá")], "ừm ngon quá")     # vài tiếng cảm thán
    assert pipeline.no_speech([(0, 3, "cho thịt vào chảo rồi đảo đều tay")], "")      # một câu: phần còn lại sẽ là bịa
    assert not pipeline.no_speech([(0, 2, "cho thịt vào chảo"), (3, 5, "đảo đều tay")], "")
    assert pipeline.no_speech([], "cho thịt vào chảo đảo đều tay")                 # lời chỉ nằm ở phần bị cắt
    assert not pipeline.no_speech([], "cho thịt vào chảo đảo đều tay", timed=False)  # nguồn bóc lời không có mốc


# ---------------- lớp tiếng: nhạc nền + tiếng gốc ----------------

def test_music_bed_mix_levels(tmp_path):
    src, song = _video(str(tmp_path / "v.mp4")), _song(str(tmp_path / "s.mp3"))

    async def go():
        out = {}
        for orig in ("off", "low", "keep"):
            out[orig] = await core_video.music_bed(src, song, str(tmp_path / f"{orig}.wav"), 7.0, trim=2.0,
                                                   orig=orig, stretch=7 / 6)
        out["none"] = await core_video.music_bed(src, None, str(tmp_path / "none.wav"), 6.0)
        return out, {k: await core_video.probe_duration(p) for k, p in out.items()}
    beds, durs = asyncio.run(go())
    assert durs == pytest.approx({"off": 7.0, "low": 7.0, "keep": 7.0, "none": 6.0}, abs=0.05)
    off, low, keep, none = (_noise_db(beds[k]) for k in ("off", "low", "keep", "none"))
    assert off < -60                                       # off: chỉ còn nhạc mới
    assert keep - low == pytest.approx(12, abs=2)          # low: tiếng gốc nhỏ hơn ~12 dB
    assert none == pytest.approx(keep, abs=1.5)            # không có nhạc: giữ nguyên tiếng gốc


def test_build_filter_bed_skips_voice_chain():
    fc = core_video.build_filter("cook", 30.0, True, None, None, stretch=1.2, bed=True)
    a = fc.split("[v];", 1)[1]
    assert a.startswith("[2:a]") and core_video.LOUDNORM in a and "[1:a]" not in a and "atempo" not in a


# ---------------- đường xử lý trong pipeline ----------------

def test_no_speech_video_gets_music_not_invented_words(silent_job, tmp_path):
    job, work, beds = silent_job
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "nhac_que.mp3"))
    out = asyncio.run(pipeline.process(job, work))
    assert asyncio.run(core_video.probe_duration(out)) == pytest.approx(6.0, abs=0.3)
    assert beds and beds[0][1].endswith("nhac_que.mp3") and beds[0][5] == "off"
    assert "Video không lời" in job.script and "nhac_que.mp3" in job.script and not job.no_autopost
    assert job.hook == "CÁ KHO TỘ" and job.post.startswith("📌 Cá kho tộ miền Tây")
    assert "chèn nhạc nền" in job.error
    assert json.load(open(bgm.RECENT_FILE)) == ["file:nhac_que.mp3"]


def test_no_speech_without_library_keeps_original_and_blocks_autopost(silent_job):
    job, work, beds = silent_job
    out = asyncio.run(pipeline.process(job, work))
    assert os.path.exists(out) and beds[0][1] is None and beds[0][5] == "keep"
    assert "thư viện nhạc nền trống" in job.no_autopost and "Nhạc nền cho video không lời" in job.script
    assert "giữ nguyên tiếng gốc" in job.error and "chèn nhạc nền" not in job.error


def test_broken_song_falls_back_to_another(silent_job, monkeypatch):
    job, work, beds = silent_job
    monkeypatch.setattr(config, "BGM_LINKS", LINK)
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "du_phong.mp3"))
    monkeypatch.setattr(bgm, "pick", lambda rnd=None, prefer_links=False, exclude=(): next(
        (x for x in bgm.library() if x["key"] not in exclude), None))                     # link trước, file sau

    async def dead(url, tmp):
        raise RuntimeError("video đã bị xoá")
    monkeypatch.setattr(core_video, "download_audio", dead)
    asyncio.run(pipeline.process(job, work))
    assert beds[0][1].endswith("du_phong.mp3") and "du_phong.mp3" in job.script


def test_narrate_setting_and_speech_keep_old_paths(silent_job, monkeypatch):
    job, work, _ = silent_job

    class Stop(Exception):
        pass

    async def stop(*a, **k):
        raise Stop()
    monkeypatch.setattr(pipeline, "_music_only", lambda *a, **k: pytest.fail("NO_SPEECH=narrate mà vẫn chèn nhạc"))
    monkeypatch.setattr(config, "NO_SPEECH", "narrate")
    monkeypatch.setattr(prompt_engine, "generate", stop)
    monkeypatch.setattr(prompt_engine, "generate_scenes", stop)
    with pytest.raises(Stop):                                  # cách cũ: AI viết lời
        asyncio.run(pipeline.process(job, work))
    monkeypatch.setattr(config, "NO_SPEECH", "music")

    async def words(audio, *a, **k):                           # có người nói: luôn viết lại lời như cũ
        segs = [(0.5, 2.5, "cho cá vào nồi đất"), (3.0, 5.0, "kho lửa nhỏ cho thấm")]
        return " ".join(t for *_, t in segs), segs
    monkeypatch.setattr(pipeline, "transcribe_segments", words)
    monkeypatch.setattr(prompt_engine, "generate_timed_any", stop)
    with pytest.raises(Stop):
        asyncio.run(pipeline.process(job, work))


def test_no_speech_renders_on_kaggle_cpu(silent_job, monkeypatch, tmp_path):
    job, work, beds = silent_job
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example")
    monkeypatch.setattr(config, "BGM_LINKS", LINK)
    monkeypatch.setattr(kaggle_worker, "enabled", lambda: "")
    song = _song(str(tmp_path / "tai_ve.mp3"))

    async def dl(url, tmp):
        assert url == LINK
        return song
    monkeypatch.setattr(core_video, "download_audio", dl)
    specs = []

    async def produce(spec, tmp, on_step=None):
        specs.append(spec)
        out = os.path.join(tmp, "kaggle_out.mp4")
        shutil.copy(job.local, out)
        return {"ok": True, "video": out, "music": True, "notes": [], "timings": {"render": 21.0}}
    monkeypatch.setattr(kaggle_worker, "produce", produce)
    out = asyncio.run(pipeline.process(job, work))
    assert out.endswith("kaggle_out.mp4") and not beds                       # Botkeep không trộn / dựng gì
    sp = specs[0]
    assert sp["kind"] == "bed" and sp["orig"] == "off" and sp["music"]["url"] == LINK and sp["music"]["kind"] == "audio"
    assert sp["music"]["http"].startswith("https://bot.example/kin/") and sp["title"] == "CÁ KHO TỘ"
    assert not kaggle_worker.needs_gpu(sp) and kaggle_worker.pip_for(sp) == kaggle_worker.PIP_LIGHT
    assert "Kaggle (CPU) 21s" in job.error and json.load(open(bgm.RECENT_FILE)) == [LINK]
    # phần của video dài: chờ chạy chung một phiên Kaggle với các phần khác
    job2 = Job(url="", mode="cook", duration=0, voice=job.voice, local=job.local, title_hint="Cá kho tộ")
    with kaggle_worker.collect():
        d = asyncio.run(pipeline.process(job2, work))
    assert isinstance(d, kaggle_worker.Deferred) and d.spec["kind"] == "bed" and d.approved == []


def test_kaggle_failure_renders_on_botkeep(silent_job, monkeypatch):
    job, work, beds = silent_job
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "a.mp3"))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example")
    monkeypatch.setattr(kaggle_worker, "enabled", lambda: "")

    async def broken(spec, tmp, on_step=None):
        raise kaggle_worker.KaggleError("hết hạn mức")
    monkeypatch.setattr(kaggle_worker, "produce", broken)
    out = asyncio.run(pipeline.process(job, work))
    assert os.path.exists(out) and beds and "Kaggle lỗi (hết hạn mức)" in job.error


# ---------------- việc "bed" trên Kaggle ----------------

def test_kaggle_bed_job(tmp_path, monkeypatch):
    src, song = _video(str(tmp_path / "v.mp4")), _song(str(tmp_path / "s.mp3"))
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setattr(config, "QUALITY", "fast")
    job = Job(url="https://www.facebook.com/reel/1", mode="cook", duration=0, voice="")
    spec = kaggle_worker.bed_spec(job, 7.0, 0, 7 / 6, True, "CÁ KHO", [], 90, None, LINK, 2.5, "low")
    assert spec["src"]["url"] == job.url and spec["music"] == {"url": LINK, "kind": "audio", "max_duration": 600}
    assert len(kaggle_worker.kernel_text(spec).encode()) < 990_000
    with pytest.raises(kaggle_worker.KaggleError):                            # file nhạc trên Botkeep, thiếu PUBLIC_URL
        kaggle_worker.bed_spec(job, 7.0, 0, 1.0, True, "", [], 90, song, "", 0.0, "off")
    got = {}

    async def fetch(ref, tmp, name):
        got[name] = ref
        return {"src": src, "music": song}[name]
    monkeypatch.setattr(kaggle_job, "fetch", fetch)
    res = asyncio.run(kaggle_job.run_one(spec, str(tmp_path / "k")))
    assert res["ok"] and res["music"] and got["music"]["url"] == LINK
    assert asyncio.run(core_video.probe_duration(res["video"])) == pytest.approx(7.0, abs=0.3)
    assert {"download", "music", "render"} <= set(res["timings"])


# ---------------- bài đăng: chỉ dựa vào tiêu đề / mô tả gốc ----------------

def test_post_only_from_title_without_inventing(monkeypatch):
    calls = []

    async def gemini(prompt, images=None, schema=None, **k):
        calls.append((prompt, schema))
        return json.dumps({"title": "Cá kho tộ chuẩn vị, nhớ đăng ký kênh nha", "caption": "Thơm nức 😋",
                           "hashtags": "#nauan #monngon", "hook": "cá kho tộ đậm đà!"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", gemini)
    monkeypatch.setattr(prompt_engine, "genai", object())
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    d = asyncio.run(prompt_engine.post_only("Cá kho tộ", "Món ngon miền Tây", "cook"))
    assert d["hook"] == "CÁ KHO TỘ ĐẬM ĐÀ" and "đăng ký" not in d["title"] and d["title"].startswith("Cá kho tộ")
    assert "KHÔNG CÓ LỜI THOẠI" in calls[0][0] and "KHÔNG bịa" in calls[0][0] and calls[0][1] is prompt_engine.PostOut
    d = asyncio.run(prompt_engine.post_only("", "  ", "cook"))              # không có gì để dựa vào: mẫu, không gọi AI
    assert d.get("fallback") and d["hook"] == "" and len(calls) == 1


# ---------------- web: thư viện nhạc nền ----------------

def test_web_bgm_library(lib, monkeypatch, tmp_path):
    from test_security import _client
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))     # không đụng settings.json thật
    song = _song(str(tmp_path / "Nhạc quê.mp3"))
    open(tmp_path / "rac.mp3", "wb").write(b"khong phai nhac")

    def form(path):
        f = aiohttp.FormData()
        f.add_field("file", open(path, "rb"), filename=os.path.basename(path), content_type="audio/mpeg")
        return f

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            h = {"X-Auth": "pw-123"}
            assert (await c.get("/api/bgm")).status == 401
            d = await (await c.get("/api/bgm", headers=h)).json()
            assert d["items"] == [] and d["mode"] == "music" and d["orig"] == "off"
            assert (await c.post("/api/bgm/link", headers=h, json={"url": "nhạc hay"})).status == 400
            r = await c.post("/api/bgm/link", headers=h, json={"url": LINK})
            assert [x["kind"] for x in (await r.json())["items"]] == ["link"] and config.BGM_LINKS == LINK
            r = await c.post("/api/bgm", headers=h, data=form(str(tmp_path / "rac.mp3")))
            assert r.status == 400 and "rac.mp3" in await r.text() and bgm.files() == []
            r = await c.post("/api/bgm", headers=h, data=form(song))
            d = await r.json()
            assert r.status == 200 and d["saved"] == ["Nhạc_quê.mp3"] and len(d["items"]) == 2
            r = await c.post("/api/bgm/delete", headers=h, json={"key": "file:Nhạc_quê.mp3"})
            assert [x["kind"] for x in (await r.json())["items"]] == ["link"] and bgm.files() == []
            r = await c.post("/api/bgm/delete", headers=h, json={"key": LINK})
            assert (await r.json())["items"] == [] and config.BGM_LINKS == ""
            assert (await c.post("/api/bgm/delete", headers=h, json={"key": LINK})).status == 404
    asyncio.run(go())


# ---------------- kho nhạc Lyria của bếp (1.1.2: làm giống truyện bé) ----------------

@pytest.fixture
def lyria_lib(lib, monkeypatch, tmp_path):
    """Kho Lyria bếp ở thư mục tạm, có sẵn bài (m4a thật ngắn) theo màu; «pushed» = bài đã có trên Kaggle."""
    from rmvideo.media import lyria
    d = str(tmp_path / "lyria")
    monkeypatch.setattr(bgm, "LYRIA_DIR", d)
    monkeypatch.setattr(bgm, "_FILL", {"task": None, "fail_at": 0.0})
    monkeypatch.setattr(config, "BGM_SOURCE", "lyria")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")

    def add(mood, n, pushed=True):
        os.makedirs(d, exist_ok=True)
        name = f"{mood}_{n}.m4a"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=8", "-c:a", "aac",
                        os.path.join(d, name)], check=True)
        data = lyria.load_library(d)
        data["tracks"].append({"file": name, "mood": mood, "secs": 8})
        if pushed:
            data["pushed"] = sorted(set(data["pushed"]) | {name})
            data["dataset"] = bgm.LYRIA_DATASET
        lyria.save_library(data, d)
        return name
    return add


def test_cook_mood_from_dish_text():
    assert bgm.mood_for("Cách làm gà chiên giòn siêu ngon") == "upbeat"
    assert bgm.mood_for("Quán ốc vỉa hè Sài Gòn") == "upbeat"
    assert bgm.mood_for("Canh chua cá lóc miền Tây") == "cozy" and bgm.mood_for("Bò kho") == "cozy"
    assert bgm.mood_for("Trà sữa matcha tại nhà") == "chill"
    assert bgm.mood_for("") == "cozy" and bgm.mood_for("", "review") == "upbeat"
    assert bgm.mood_for("Khoai lang") == bgm.DEFAULT_MOOD                       # không khớp chữ nào
    assert set(bgm.MOODS) == set(bgm.MOOD_WORDS) and all("instrumental" in m["prompt"] for m in bgm.MOODS.values())


def test_cook_choose_lyria_then_own_library(lyria_lib, monkeypatch):
    os.makedirs(bgm.DIR)
    _song(os.path.join(bgm.DIR, "cua_minh.mp3"), 3)
    # kho Lyria trống: dùng thư viện của mình
    assert bgm.choose("cozy")["key"] == "file:cua_minh.mp3"
    up = lyria_lib("upbeat", 1)
    lyria_lib("cozy", 1, pushed=False)
    # đúng màu; trên Kaggle chỉ bài đã đẩy (cozy_1 chưa có trong dataset -> lấy bài màu khác đã đẩy)
    assert bgm.choose("cozy")["lyria"] == "cozy_1.m4a"
    it = bgm.choose("cozy", remote=True)
    assert it["lyria"] == up and it["path"].endswith(up) and it["key"] == "lyria:" + up
    ref = bgm.kaggle_ref(it)
    assert ref == {"mounted": bgm.LYRIA_DATASET, "name": up, "kind": "audio", "dataset": bgm.LYRIA_DATASET}
    # BGM_SOURCE=files: như trước, không đụng kho Lyria
    monkeypatch.setattr(config, "BGM_SOURCE", "files")
    assert bgm.choose("upbeat")["key"] == "file:cua_minh.mp3"
    st = bgm.lyria_state()
    assert st["moods"] == {"cozy": 1, "upbeat": 1, "chill": 0} and st["pushed"] == 1 and not st["busy"]


def test_cook_lyria_fills_in_background(lyria_lib, monkeypatch):
    """Màu thiếu bài: tạo thêm MỘT bài trong nền (job không chờ), xong thì đẩy dataset; một việc một lúc; lỗi thì nghỉ."""
    from rmvideo.media import lyria
    made, pushed = [], []

    async def fake_gen(lib, mood, secs=None, cl=None, log=None, factory=None):
        assert lib.dataset == bgm.LYRIA_DATASET and lib.track_secs == bgm.LYRIA_SECS
        made.append(mood)
        return {"file": f"{mood}_9.m4a", "mood": mood}

    async def fake_push(log=None):
        pushed.append(1)
        return {}
    monkeypatch.setattr(lyria, "generate", fake_gen)
    monkeypatch.setattr(bgm, "push_lyria", fake_push)
    assert not bgm.fill_later("chill")                         # không có key Gemini: không tạo
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")

    async def run():
        assert bgm.fill_later("chill") and not bgm.fill_later("chill")       # đang tạo: không chồng việc
        await bgm._FILL["task"]
    asyncio.run(run())
    assert made == ["chill"] and pushed == [1]
    # Lyria lỗi -> nghỉ LYRIA_RETRY_AFTER giây mới thử lại
    async def broken(*a, **k):
        raise RuntimeError("1006")
    monkeypatch.setattr(lyria, "generate", broken)

    async def run2():
        assert bgm.fill_later("upbeat")
        await bgm._FILL["task"]
        return bgm.fill_later("upbeat")
    assert asyncio.run(run2()) is False and bgm._FILL["fail_at"] > 0
    assert not bgm.fill_later("nope")                           # màu lạ
    for n in range(1, bgm.LYRIA_PER_MOOD + 1):
        lyria_lib("cozy", n)
    monkeypatch.setattr(bgm, "_FILL", {"task": None, "fail_at": 0.0})
    assert not asyncio.run(_call(bgm.fill_later, "cozy"))        # đủ bài: không tạo


async def _call(fn, *a):
    return fn(*a)


def test_cook_lyria_specs_mount_dataset(lyria_lib, monkeypatch):
    """Bài Lyria sang Kaggle bằng dataset gắn vào phiên (không cần PUBLIC_URL), cả nhạc lót lẫn video không lời, cả lô."""
    up = lyria_lib("upbeat", 1)
    job = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="female")
    spec = kaggle_worker.build_spec(job, [], 30.0, 0, 1.0, True, "t", [], None, {}, [], 90,
                                    voice_bed={"path": os.path.join(bgm.LYRIA_DIR, up), "url": "", "trim": 2.0,
                                               "name": "Lyria", "lyria": up})
    assert spec["voice_bed"]["music"]["mounted"] == bgm.LYRIA_DATASET and spec["dataset_sources"] == [bgm.LYRIA_DATASET]
    ref = bgm.kaggle_ref({"lyria": up})
    bspec = kaggle_worker.bed_spec(job, 30.0, 0, 1.0, True, "t", [], 90, os.path.join(bgm.LYRIA_DIR, up), "", 0.0, "off",
                                   music_ref=ref)
    assert bspec["music"] == ref and bspec["dataset_sources"] == [bgm.LYRIA_DATASET]
    plain = kaggle_worker.build_spec(job, [], 30.0, 0, 1.0, True, "t", [], None, {}, [], 90)
    assert "dataset_sources" not in plain
    seen = []
    monkeypatch.setattr(kaggle_worker, "_run_sync", lambda sp, tmp, on_step: seen.append(sp) or [{"ok": True}, {"ok": True}])
    asyncio.run(kaggle_worker.produce_batch([spec, plain], "/tmp"))
    assert seen[0]["dataset_sources"] == [bgm.LYRIA_DATASET]


def test_no_speech_video_uses_lyria_track(silent_job, lyria_lib, monkeypatch):
    """Video không lời, BGM_SOURCE=lyria: nhạc lấy từ kho Lyria theo món (dựng trên Botkeep dùng file cục bộ)."""
    job, work, beds = silent_job
    name = lyria_lib("cozy", 1)
    out = asyncio.run(pipeline.process(job, work))
    assert os.path.exists(out) and beds[0][1].endswith(name)
    assert "Lyria" in job.script and not job.no_autopost
    assert json.load(open(bgm.RECENT_FILE)) == ["lyria:" + name]


def test_cook_lyria_full_but_unpushed_gets_pushed(lyria_lib, monkeypatch):
    """Soát 20/11: kho đủ bài nhưng có bài chưa đẩy lên Kaggle (lần đẩy trước lỗi, kho tạo lúc Kaggle tắt) -> đẩy trong nền
    (không tạo bài mới); Kaggle tắt / đã đẩy hết -> không làm gì."""
    from rmvideo.media import lyria
    for n in range(1, bgm.LYRIA_PER_MOOD + 1):
        lyria_lib("cozy", n, pushed=(n < bgm.LYRIA_PER_MOOD))
    made, pushed = [], []

    async def fake_gen(*a, **k):
        made.append(1)

    async def fake_push(log=None):
        pushed.append(1)
        return {}
    monkeypatch.setattr(lyria, "generate", fake_gen)
    monkeypatch.setattr(bgm, "push_lyria", fake_push)
    monkeypatch.setattr(kaggle_worker, "is_enabled", lambda: False)
    assert not asyncio.run(_call(bgm.fill_later, "cozy"))
    monkeypatch.setattr(kaggle_worker, "is_enabled", lambda: True)

    async def run():
        assert bgm.fill_later("cozy")
        await bgm._FILL["task"]
    asyncio.run(run())
    assert made == [] and pushed == [1]
