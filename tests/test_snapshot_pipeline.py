"""Test "chụp hành vi" (characterization) cho bộ điều phối pipeline — lưới an toàn của đợt refactor (Phase 0).

Mỗi kịch bản chạy TRỌN `pipeline.process` / `pipeline.run_job` với mọi dịch vụ ngoài giả (AI, bóc lời, đọc giọng,
Kaggle, tải video) và GHI LẠI các quyết định: tham số gửi tới render / Kaggle / AI, ghi chú và kết quả trên Job.
Kết quả so với tệp JSON trong tests/snapshots/pipeline/. Refactor đúng thì snapshot GIỐNG HỆT; thay đổi có chủ đích
thì chạy lại với UPDATE_SNAPSHOTS=1 và duyệt diff của tệp JSON trong commit.

Không chạy render thật (đã có test riêng trong test_render / test_teaser...): render, trộn nhạc, dựng ảnh được thay
bằng bản ghi tham số + chép file, nên cả bộ chạy nhanh và không phụ thuộc phiên bản ffmpeg.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import wave

import pytest

import beat
import bgm
import cache
import config
import core_video
import cover as cover_mod
import highlights
import kaggle_api
import kaggle_worker
import metrics
import offline
import pipeline
from rmvideo.app import brand
import prompt_engine
from rmvideo.app.flow import dedupe, style
import review
import scenes
import slideshow
import trendfx
import vocal_sep
from queue_manager import Job

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", "pipeline")
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"
URL = "https://www.facebook.com/reel/1234567890/"
CONCURRENT = {"tts", "metric", "sep"}          # gọi song song: thứ tự giữa các lần chạy không cố định -> so theo tập


# ---------------- tiện ích ----------------

def _video(path, dur=8, size="360x640", rate=25):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size={size}:rate={rate}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", path], check=True)
    return path


def _wav(path, dur, sr=24000):
    """WAV im lặng đúng độ dài (Python thuần: không phụ thuộc phiên bản ffmpeg)."""
    n = max(1, int(round(dur * sr)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return path


def _jpg(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=1", "-frames:v", "1",
                    path], check=True)
    return path


_TOKEN_KEYS = {"token", "amb_token", "dl_token", "seed"}


def _norm(obj, roots):
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, float):
        return round(obj, 3)
    if isinstance(obj, int):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_norm(x, roots) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k in sorted(obj, key=str):
            v = obj[k]
            if k in _TOKEN_KEYS and v not in ("", None, 0):
                out[str(k)] = "<TOKEN>"
            elif k == "settings" and isinstance(v, dict):
                out[str(k)] = sorted(v)                      # chỉ danh sách khoá (giá trị đã cố định ở fixture)
            elif k in ("logo_b64", "ref_b64"):
                out[str(k)] = f"<b64:{len(v)}>"
            else:
                out[str(k)] = _norm(v, roots)
        return out
    if isinstance(obj, str):
        s = obj
        for root, name in roots:
            s = s.replace(root, name)
        s = re.sub(r"/kin/[A-Za-z0-9_-]{20,}/", "/kin/<T>/", s)
        s = re.sub(r"rmv_[A-Za-z0-9_]+", "rmv_<X>", s)
        # tên file bộ nhớ đệm = băm nội dung video thử; FFmpeg mỗi máy mã hoá video thử khác vài byte (CI GitHub đỏ
        # từ bản 41 chỉ vì chỗ này) -> không đưa mã băm vào snapshot
        s = re.sub(r"(/cache/[\w-]+/)[0-9a-f]{16,}", r"\1<HASH>", s)
        return s
    if isinstance(obj, kaggle_worker.Deferred):
        return "<Deferred>"
    return f"<{type(obj).__name__}>"


class Recorder:
    def __init__(self):
        self.calls: dict[str, list] = {}

    def add(self, name, *payload):
        self.calls.setdefault(name, []).append(list(payload))

    def dump(self, roots):
        out = {}
        for name in sorted(self.calls):
            items = [_norm(x, roots) for x in self.calls[name]]
            if name in CONCURRENT:
                items = sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
            out[name] = items
        return out


JOB_FIELDS = ("status", "step", "error", "script", "post", "hook", "out_dur", "cut_at", "teaser", "approved",
              "final_segments", "srt", "no_autopost", "long_mode", "local", "pieces", "source_meta")


def _job_view(job):
    d = {k: getattr(job, k) for k in JOB_FIELDS}
    d["parts"] = [{"label": p["label"], "file": os.path.basename(p["path"])} for p in job.parts]
    for k in ("edit", "recipe"):                     # bản 42: chỉ ghi khi có (snapshot cũ giữ nguyên)
        if getattr(job, k, None):
            d[k] = getattr(job, k)
    return d


def check(name, data, roots):
    data = json.loads(json.dumps(_norm(data, roots), ensure_ascii=False, sort_keys=True))
    path = os.path.join(SNAP_DIR, name + ".json")
    if UPDATE or not os.path.exists(path):
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        if not UPDATE:
            pytest.fail(f"snapshot {name} chưa có: đã tạo, chạy lại để so")
        return
    with open(path, encoding="utf-8") as f:
        want = json.load(f)
    assert data == want, f"hành vi pipeline đổi ở kịch bản {name} (UPDATE_SNAPSHOTS=1 nếu thay đổi có chủ đích)"


# ---------------- môi trường giả ----------------

class Env:
    """Núm chỉnh của một kịch bản + bộ ghi."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.rec = Recorder()
        self.segs = [(0.5, 1.8, "cho thịt vào chảo"), (2.0, 3.4, "đảo đều tay cho thịt săn lại"),
                     (3.6, 4.8, "nêm muối vừa ăn"), (5.0, 7.8, "bà con bấm vào giỏ hàng góc trái màn hình mua ngay")]
        self.cuts = [2.0, 5.0]
        self.bands = [(0.70, 0.80)]
        self.late_cuts: list = []
        self.words = 4                       # số chữ mỗi đoạn AI viết
        self.fresh = 1.0
        self.kaggle_fail = False
        self.batch_fail: set = set()
        self.legacy_fallback = False         # prompt_engine.generate trả kịch bản mẫu
        self.recipe_ai: dict | None = None   # prompt_engine.recipe_facts trả gì (None = AI lỗi / không có key)
        self.src = _video(os.path.join(tmp, "src", "src.mp4"))
        self.roots = [(tmp, "<TMP>")]


@pytest.fixture
def env(monkeypatch, tmp_path):
    os.makedirs(tmp_path / "src")
    os.makedirs(tmp_path / "up")
    os.makedirs(tmp_path / "out")
    os.makedirs(tmp_path / "w")
    E = Env(str(tmp_path))
    rec = E.rec
    # mọi cài đặt về mặc định của mã (không phụ thuộc settings.json / biến môi trường của máy chạy test)
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    for k, v in (("QUALITY", "fast"), ("BRAND_INTRO", "0"), ("CUT_PROMO", "1"), ("COVER", "0"), ("TEASER", "0"),
                 ("TTS_PROVIDER", "edge"), ("KAGGLE_ANALYZE", "0"), ("BRAND_LOGO", "0"), ("PUBLIC_URL", ""),
                 ("NO_SPEECH", "music"), ("BGM_LINKS", ""), ("REVIEW_SCRIPT", "0"), ("BRAND_STING", "0")):
        monkeypatch.setattr(config, k, v)
    for k, v in brand.OLD_DEFAULTS.items():      # kịch bản cũ: chưa đặt nhận diện kênh (bộ mặc định bản 43: test riêng)
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(bgm, "DIR", str(tmp_path / "bgm"))
    monkeypatch.setattr(bgm, "RECENT_FILE", str(tmp_path / "bgm_recent.json"))
    monkeypatch.setattr(offline, "spleeter_available", lambda: False)
    monkeypatch.setattr(metrics, "record", lambda kind, provider, ok=True, fallback=False, detail="":
                        rec.add("metric", kind, provider, ok, fallback, detail[:60]))
    monkeypatch.setattr(trendfx, "load_recent", lambda *a, **k: ["calm_fade"])
    monkeypatch.setattr(trendfx, "remember", lambda name, path=None: rec.add("trendfx.remember", name))

    # ----- nguồn -----
    async def download(url, tmpdir, max_duration=90):
        rec.add("download", url, max_duration)
        dst = os.path.join(tmpdir, "input.mp4")
        shutil.copy(E.src, dst)
        return dst, "Thịt kho tộ", "Món ngon mỗi ngày", {"uploader": "Kênh Gốc", "webpage_url": url}
    monkeypatch.setattr(core_video, "download", download)

    # ----- phân tích -----
    async def transcribe(audio, title="", desc=""):
        rec.add("asr", os.path.basename(audio), title, desc)
        return " ".join(t for *_, t in E.segs), list(E.segs)
    monkeypatch.setattr(pipeline, "transcribe_segments", transcribe)

    async def analyze_cached(path, tmpdir, dur, want_gray, src_hash, need_thumbs=True):
        rec.add("scenes.analyze_cached", os.path.basename(path), dur, want_gray, need_thumbs)
        return list(E.cuts), list(E.bands)
    monkeypatch.setattr(scenes, "analyze_cached", analyze_cached)

    async def scenes_analyze(path, tmpdir, dur, want_gray, m=""):
        rec.add("scenes.analyze", os.path.basename(path), dur, want_gray, m)
        return list(E.cuts), list(E.bands)
    monkeypatch.setattr(scenes, "analyze", scenes_analyze)

    async def detect_cuts(path):
        rec.add("scenes.detect_cuts", os.path.basename(path))
        return list(E.late_cuts)
    monkeypatch.setattr(scenes, "detect_cuts", detect_cuts)

    async def keyframes(path, segs):
        rec.add("scenes.keyframes", [round(a, 3) for a, *_ in segs])
        return [b"\xff\xd8img%d" % k for k in range(len(segs))]
    monkeypatch.setattr(scenes, "keyframes", keyframes)

    async def silences(path, *a, **k):
        return [(29.0, 31.0), (59.0, 61.0), (88.0, 90.0), (118.0, 120.0)]
    monkeypatch.setattr(core_video, "silences", silences)

    # ----- AI -----
    def _seg_text(k):
        return " ".join(["Lời"] + [f"mới{k}"] * (E.words - 1))

    async def generate_timed_any(title, desc, mode, segments, out_dur, images_for=None, teaser=0.0):
        imgs = await images_for(segments) if images_for else []
        rec.add("ai.timed", title, mode, [list(s) for s in segments], out_dur, teaser, len(imgs))
        out = [{"i": k, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": _seg_text(k), "orig": t,
                "ost": "orig" if k == 1 and E.words == 1 else "new"} for k, (a, b, t) in enumerate(segments)]
        return {"segments": out, "script": " ".join(x["text"] for x in out), "fresh": E.fresh,
                "title": "Thịt kho mềm", "caption": "Ngon bá cháy", "hashtags": "#thitkho", "hook": "THỊT KHO MỀM RỤC"}
    monkeypatch.setattr(prompt_engine, "generate_timed_any", generate_timed_any)

    async def generate_scenes(title, desc, mode, segs, images, out_dur):
        rec.add("ai.scenes", title, mode, [list(s) for s in segs], len(images), out_dur)
        out = [{"i": k, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": _seg_text(k), "orig": ""}
               for k, (a, b, *_) in enumerate(segs)]
        return {"segments": out, "script": " ".join(x["text"] for x in out), "title": "Cảnh đẹp", "caption": "c",
                "hashtags": "#a", "hook": "CẢNH ĐẸP"}
    monkeypatch.setattr(prompt_engine, "generate_scenes", generate_scenes)

    async def generate(title, desc, mode, transcript="", duration=0):
        rec.add("ai.generate", title, mode, transcript, duration)
        if E.legacy_fallback:
            return {"script": "Kịch bản mẫu đọc một mạch.", "title": title, "caption": "", "hashtags": "",
                    "fallback": True}
        return {"script": "Hôm nay làm món thịt kho tộ thơm ngon đậm đà cả nhà cùng xem nha.", "title": "Thịt kho",
                "caption": "Đậm đà", "hashtags": "#thitkho"}
    monkeypatch.setattr(prompt_engine, "generate", generate)

    async def post_only(title, desc, mode):
        rec.add("ai.post_only", title, mode)
        return {"title": "Cảnh nấu ăn", "caption": "Thư giãn", "hashtags": "#asmr", "hook": "XEM LÀ ĐÓI"}
    monkeypatch.setattr(prompt_engine, "post_only", post_only)

    async def shorten(items):
        rec.add("ai.shorten", [list(x) for x in items])
        return {i: " ".join(t.split()[:max(2, n // 2)]) for i, t, n in items}
    monkeypatch.setattr(prompt_engine, "shorten", shorten)

    async def recipe_facts(title, desc, texts, images=None, times=None):
        rec.add("ai.recipe", title, list(texts), len(images or []))
        return json.loads(json.dumps(E.recipe_ai)) if E.recipe_ai is not None else None
    monkeypatch.setattr(prompt_engine, "recipe_facts", recipe_facts)

    async def motion(inp, end, thumbs=None):            # chuyển động: cố định (khoảng 4.0-5.0 gần như đứng yên)
        rec.add("style.motion", round(end, 2))
        return [0.0004 if 13 <= k <= 24 else 0.03 for k in range(int(end * 2) + 2)]
    monkeypatch.setattr(style, "motion", motion)

    async def fingerprint(path, dur=None):              # dấu vân hình: cố định, không đọc autopost.json của máy chạy test
        rec.add("dedupe.fingerprint", round(dur or 0, 2))
        return ["0" * 16 + ":" + "f" * 16] * 8
    monkeypatch.setattr(dedupe, "fingerprint", fingerprint)
    monkeypatch.setattr(dedupe, "known_fingerprints", lambda: {})

    async def choose(title, desc, dur, segs, cuts, sil, images, img_t):
        rec.add("ai.highlights", title, dur, len(segs), list(cuts), len(images))
        return [{"start": 10.0, "end": 45.0, "score": 9, "title": "Đoạn chiên", "reason": "hấp dẫn"},
                {"start": 70.0, "end": 100.0, "score": 7, "title": "Đoạn bày", "reason": ""}]
    monkeypatch.setattr(highlights, "choose", choose)

    # ----- giọng, tách giọng -----
    async def synth(text, voice, path):
        rec.add("tts", text, voice)
        words = [(round(k * 0.3, 2), round(k * 0.3 + 0.25, 2), w) for k, w in enumerate(text.split())]
        _wav(path, 0.3 * len(words) + 0.4)
        return words, "edge"
    monkeypatch.setattr(core_video, "synth_clip_ex", synth)

    async def tts(text, mp3_path, voice, ass_path=None, pos_y=None):
        rec.add("tts.oneshot", text, voice, bool(ass_path), pos_y)
        _wav(mp3_path, 0.3 * len(text.split()) + 0.4)
        if ass_path:
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write("[Script Info]\n")
        return bool(ass_path)
    monkeypatch.setattr(core_video, "tts", tts)

    async def remove_voice(src, out, timeout=0, windows=None):
        rec.add("sep", os.path.basename(src), windows)
        _wav(out, 8.0, 44100)
        return True
    monkeypatch.setattr(vocal_sep, "remove_voice", remove_voice)

    # ----- dựng -----
    def _ass_text(p):
        if not p or not os.path.exists(p):
            return None
        return [ln for ln in open(p, encoding="utf-8").read().splitlines() if ln.startswith("Dialogue")]

    async def render(input_mp4, voice_mp3, output_mp4, mode, duration=0, ass_path=None, cover_bands=None,
                     ambient_wav=None, speech_gate=None, allow_hd=True, title="", tmp=None, stretch=1.0, ost=None,
                     notes=None, music_bed=False, teaser=None, **kw):
        rec.add("render", {"input": input_mp4, "voice": voice_mp3, "out": output_mp4, "mode": mode,
                           "duration": duration, "subs": _ass_text(ass_path), "bands": cover_bands,
                           "ambient": ambient_wav, "gate": speech_gate, "allow_hd": allow_hd, "title": title,
                           "stretch": stretch, "ost": ost, "music_bed": music_bed, "teaser": teaser,
                           **({"edit": kw["edit"]} if kw.get("edit") else {})})
        shutil.copy(input_mp4, output_mp4)
    monkeypatch.setattr(core_video, "render", render)

    async def music_bed(inp, music, out_wav, out_dur, trim=0.0, orig="off", stretch=1.0):
        rec.add("music_bed", inp, music, out_dur, trim, orig, stretch)
        _wav(out_wav, out_dur, 44100)
    monkeypatch.setattr(core_video, "music_bed", music_bed)

    async def render_music(inp, music, out, duration=0, **kw):
        rec.add("render_music", inp, music, duration, kw)
        shutil.copy(inp, out)
    monkeypatch.setattr(core_video, "render_music", render_music)

    async def show(images, music, out, duration=0, *a, **k):
        rec.add("slideshow.render", [os.path.basename(i) for i in images], music, duration)
        shutil.copy(E.src, out)
        return {"length": 9.6, "requested": duration, "bpm": 120.0, "style": "calm",
                "template": next(iter(trendfx.TEMPLATES)), "music_start": 2.0, "slots": [1, 2, 3], "drop": None}
    monkeypatch.setattr(slideshow, "render", show)

    async def beat_analyze(path):
        rec.add("beat.analyze", os.path.basename(path))
        return (120.0, 0.25) if path.endswith(".mp4") else (100.0, 0.5)
    monkeypatch.setattr(beat, "analyze", beat_analyze)
    monkeypatch.setattr(slideshow, "analyze_music", lambda p, max_s=300: {"duration": 30.0})
    monkeypatch.setattr(slideshow, "choose_window", lambda info, length: (6.0, 6.0 + length))
    monkeypatch.setattr(bgm, "best_start", lambda path, length: 3.0)

    # ----- Kaggle -----
    def _remote_result(spec, out):
        kind = spec["kind"]
        if kind == "remake":
            plan = [{"start": s["start"], "end": s["start"] + 1.0, "factor": 1.0} for s in spec["segments"]]
            return {"ok": True, "video": out, "plan": plan, "too_long": [], "srt": "1\n00:00:00,000 --> 00:00:01,000\nx\n",
                    "amb": spec["sep"]["ok"], "notes": ["ℹ️ ghi chú Kaggle. "], "timings": {"tts": 4, "render": 60},
                    "gpu": True}
        if kind == "photo":
            return {"video": out, "plan": {"length": 9.6, "requested": spec["duration"], "bpm": 120.0, "style": "calm",
                                           "template": next(iter(trendfx.TEMPLATES)), "music_start": 2.0,
                                           "slots": [1, 2], "drop": None}, "timings": {"render": 9}}
        return {"ok": True, "video": out, "notes": [], "timings": {"render": 7}}

    async def produce(spec, tmp, on_step=None):
        rec.add("kaggle.produce", spec)
        if E.kaggle_fail:
            raise kaggle_api.KaggleError("Kaggle lỗi 500 ở SaveKernel")
        out = os.path.join(tmp, "kaggle_out.mp4")
        shutil.copy(E.src, out)
        return _remote_result(spec, out)
    monkeypatch.setattr(kaggle_worker, "produce", produce)

    async def produce_batch(specs, tmp, on_step=None):
        rec.add("kaggle.produce_batch", specs)
        res = []
        for k, sp in enumerate(specs):
            if k in E.batch_fail:
                res.append(kaggle_api.KaggleError("Kaggle hết giờ"))
                continue
            out = os.path.join(tmp, f"k{k}.mp4")
            shutil.copy(E.src, out)
            res.append(_remote_result(sp, out))
        return res
    monkeypatch.setattr(kaggle_worker, "produce_batch", produce_batch)

    async def analyze(spec, tmp, on_step=None):
        rec.add("kaggle.analyze", spec)
        words = []
        for a, b, t in E.segs:
            ws = t.split()
            step = (b - a) / len(ws)
            words += [{"start": round(a + k * step, 2), "end": round(a + (k + 1) * step, 2), "word": w}
                      for k, w in enumerate(ws)]
        whisper = {"language": "vi", "device": "cuda", "duration": 8.0,
                   "segments": [{"start": a, "end": b, "text": t, "no_speech_prob": 0.01, "avg_logprob": -0.2}
                                for a, b, t in E.segs], "words": words}
        amb = os.path.join(tmp, "ambient.flac")
        _wav(amb, 8.0, 44100)
        return {"ok": True, "token": spec["token"], "whisper": whisper, "cuts": list(E.cuts), "bands": list(E.bands),
                "dur": 8.0, "sep": True, "timings": {"sep": 34, "asr": 55, "scenes": 59}, "ambient": amb}
    monkeypatch.setattr(kaggle_worker, "analyze", analyze)
    return E


def kaggle_on(monkeypatch, public_url=""):
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USE", "1")
    monkeypatch.setattr(config, "PUBLIC_URL", public_url)


def _job(E, jid, **kw):
    kw.setdefault("url", URL)
    kw.setdefault("mode", "cook")
    kw.setdefault("duration", 0)
    kw.setdefault("voice", config.VOICE_FEMALE)
    kw.setdefault("source", "web")
    job = Job(**kw)
    job.id = jid
    return job


def run(E, job, fn="process", **kw):
    work = os.path.join(E.tmp, "w", str(job.id))
    os.makedirs(work, exist_ok=True)
    if fn == "process":
        res = asyncio.run(pipeline.process(job, work, **kw))
    else:
        res = asyncio.run(pipeline.run_job(job, work))
    return {"result": res, "job": _job_view(job)}


def snap(name, E, *runs):
    check(name, {"runs": list(runs), "calls": E.rec.dump(E.roots)}, E.roots)


# ---------------- kịch bản ----------------

def test_cook_botkeep(env):
    snap("cook_botkeep", env, run(env, _job(env, 101)))


def test_cook_botkeep_shorten(env):
    env.words = 14                                       # lời quá dài: rút gọn trước + sau khi đọc
    snap("cook_botkeep_shorten", env, run(env, _job(env, 102)))


def test_review_mode_botkeep(env):
    snap("review_mode_botkeep", env, run(env, _job(env, 103, mode="review")))


def test_review_gate_then_approved(env):
    job = _job(env, 104, review=True)
    first = run(env, job)
    edits = {0: "Lời sửa tay đoạn đầu", 2: {"ost": "orig"}}
    approved = review.approve_job(job, edits)
    approved.id = 105
    second = run(env, approved)
    snap("review_gate_then_approved", env, first, second)


def test_auto_source_low_fresh(env):
    env.fresh = 0.5
    snap("auto_source_low_fresh", env, run(env, _job(env, 106, source="auto", review=True)))


RECIPE_AI = {"is_recipe": True, "dish": "Thịt xào", "servings": "",
             "ingredients": [{"name": "thịt", "qty": "", "unit": "", "seg": 0},
                             {"name": "muối", "qty": "1", "unit": "muỗng", "seg": 2}],
             "steps": [{"kind": "cook", "from": 0, "to": 1, "text": "Cho thịt vào chảo, đảo đều tay 5 phút."},
                       {"kind": "cook", "from": 2, "to": 2, "text": "Nêm muối vừa ăn."}],
             "tips": [{"text": "Đảo nhanh tay cho thịt không dính chảo.", "seg": -1},
                      {"text": "Ăn món này giúp giảm cân.", "seg": -1}]}


def test_recipe_in_post(env, monkeypatch):
    """Bản 42: công thức + mẹo trong bài đăng. Số không có trong lời gốc bị bỏ ("5 phút" -> "một lúc"); "1 muỗng" muối
    AI nghe nhầm (lời gốc không có số) -> "vừa đủ"; mẹo nói chuyện giảm cân bị loại."""
    monkeypatch.setattr(config, "POST_RECIPE", "1")
    monkeypatch.setattr(config, "TIPS", "post")
    env.recipe_ai = RECIPE_AI
    snap("recipe_in_post", env, run(env, _job(env, 121)))


def test_auto_not_recipe_stops_early(env):
    """Tự đăng + AI thấy không phải công thức (video quảng cáo khóa học): dừng trước khi đọc giọng / dựng."""
    env.recipe_ai = {"is_recipe": False, "dish": "", "ingredients": [], "steps": [], "tips": []}
    job = _job(env, 122, source="auto")
    with pytest.raises(RuntimeError, match="không phải video công thức"):
        run(env, job)
    snap("auto_not_recipe", env, {"result": "RuntimeError", "job": _job_view(job)})


def _style_src(E):
    """Video 20s: hai câu đầu, quãng chờ không lời 6-12.5s (đứng yên), câu cuối."""
    E.src = _video(os.path.join(E.tmp, "src", "long20.mp4"), dur=20)
    E.segs = [(0.5, 3.0, "cho thịt vào chảo đảo đều"), (3.4, 6.0, "đậy nắp hầm 30 phút cho mềm"),
              (12.5, 15.0, "mở nắp ra nêm muối vừa ăn"), (15.4, 18.5, "vậy là xong món thịt kho")]
    E.cuts = [6.5, 12.0]


def test_style_light_botkeep(env, monkeypatch):
    """REMAKE_STYLE=light: kế hoạch dựng chốt trước khi AI viết lời; lời gốc, điểm chuyển cảnh đổi sang trục mới."""
    monkeypatch.setattr(config, "REMAKE_STYLE", "light")
    _style_src(env)
    snap("style_light_botkeep", env, run(env, _job(env, 131)))


def test_style_light_kaggle(env, monkeypatch):
    monkeypatch.setattr(config, "REMAKE_STYLE", "light")
    _style_src(env)
    kaggle_on(monkeypatch)
    snap("style_light_kaggle", env, run(env, _job(env, 132)))


def test_style_strong_botkeep(env, monkeypatch):
    """REMAKE_STYLE=strong: nhãn bước của AI -> tóm tắt trước cảnh cuối (clip phụ) + lời "gốc" do mã viết cho khúc
    đó; luật R1-R7 đạt nên không lùi về lớp A."""
    monkeypatch.setattr(config, "REMAKE_STYLE", "strong")
    _style_src(env)
    env.recipe_ai = {"is_recipe": True, "dish": "Thịt kho", "servings": "",
                     "ingredients": [{"name": "thịt", "qty": "", "unit": "", "seg": 0},
                                     {"name": "muối", "qty": "", "unit": "", "seg": 2}],
                     "steps": [{"kind": "cook", "from": 0, "to": 1, "text": "Cho thịt vào chảo, đậy nắp hầm 30 phút."},
                               {"kind": "cook", "from": 2, "to": 2, "text": "Mở nắp, nêm muối."},
                               {"kind": "plate", "from": 3, "to": 3, "text": "Bày ra dĩa."}], "tips": []}
    snap("style_strong_botkeep", env, run(env, _job(env, 135)))


def test_short_version_after_full(env, monkeypatch):
    """SHORT_VERSION=on: video 130s -> bản đầy đủ giao trước, rồi bản ngắn ≤ 90s (kế hoạch riêng, AI viết lời riêng,
    dựng vào thư mục con); timeline / phụ đề / độ dài của job vẫn là của bản đầy đủ."""
    monkeypatch.setattr(config, "SHORT_VERSION", "on")
    env.src = _video(os.path.join(env.tmp, "src", "long130.mp4"), dur=130)
    env.segs = [(1.0 + 10 * k, 7.0 + 10 * k, t) for k, t in enumerate(
        ["cho 500 gam thịt vào thau", "ướp với 2 muỗng nước mắm", "ôi thơm quá trời", "bắc chảo phi tỏi",
         "cho thịt vào đảo đều", "thêm nước dừa kho lửa nhỏ", "nói chuyện chút xíu nha", "nêm lại cho vừa",
         "rắc tiêu lên", "múc ra dĩa", "ăn với cơm nóng", "vậy là xong rồi"])]
    env.cuts = []
    env.recipe_ai = {"is_recipe": True, "dish": "Thịt kho", "servings": "",
                     "ingredients": [{"name": "thịt", "qty": "500", "unit": "gam", "seg": 0},
                                     {"name": "nước mắm", "qty": "2", "unit": "muỗng", "seg": 1}],
                     "steps": [{"kind": "prep", "from": 0, "to": 1, "text": "Ướp 500 gam thịt với 2 muỗng nước mắm."},
                               {"kind": "reaction", "from": 2, "to": 2, "text": ""},
                               {"kind": "cook", "from": 3, "to": 5, "text": "Phi tỏi, cho thịt vào, thêm nước dừa kho."},
                               {"kind": "wait", "from": 6, "to": 6, "text": ""},
                               {"kind": "cook", "from": 7, "to": 8, "text": "Nêm lại, rắc tiêu."},
                               {"kind": "plate", "from": 9, "to": 11, "text": "Múc ra dĩa."}], "tips": []}
    snap("short_version_after_full", env, run(env, _job(env, 136, duration=0, long_mode="full"), fn="run_job"))


def test_short_version_kaggle(env, monkeypatch):
    """Bản ngắn dựng trên Kaggle: spec riêng mang kế hoạch "short"; bản đầy đủ không bị đè."""
    kaggle_on(monkeypatch)
    monkeypatch.setattr(config, "SHORT_VERSION", "on")
    env.src = _video(os.path.join(env.tmp, "src", "long130.mp4"), dur=130)
    env.segs = [(1.0 + 10 * k, 7.0 + 10 * k, f"bước {k} cho 200 gam thịt") for k in range(12)]
    env.cuts = []
    env.recipe_ai = {"is_recipe": True, "dish": "Thịt", "ingredients": [],
                     "steps": [{"kind": "cook", "from": k, "to": k, "text": f"Bước {k}."} for k in range(12)],
                     "tips": []}
    snap("short_version_kaggle", env, run(env, _job(env, 137, long_mode="full"), fn="run_job"))


def test_style_review_then_approved_reuses_plan(env, monkeypatch):
    monkeypatch.setattr(config, "REMAKE_STYLE", "light")
    _style_src(env)
    job = _job(env, 133, review=True)
    first = run(env, job)
    approved = review.approve_job(job, {})
    approved.id = 134
    second = run(env, approved)
    snap("style_review_then_approved", env, first, second)


def test_stretch(env):
    snap("stretch", env, run(env, _job(env, 107, duration=10)))


def test_cook_kaggle(env, monkeypatch):
    kaggle_on(monkeypatch)
    snap("cook_kaggle", env, run(env, _job(env, 108, voice="vieneu:Mỹ Duyên")))


def test_cook_kaggle_fails_falls_back(env, monkeypatch):
    kaggle_on(monkeypatch)
    env.kaggle_fail = True
    snap("cook_kaggle_fails_falls_back", env, run(env, _job(env, 109, voice="vieneu:Adam")))


def test_cook_kaggle_analyze(env, monkeypatch):
    kaggle_on(monkeypatch)
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "1")
    snap("cook_kaggle_analyze", env, run(env, _job(env, 110, voice="vieneu:Mỹ Duyên")))


def test_cook_local_file_without_public_url(env, monkeypatch):
    kaggle_on(monkeypatch)
    snap("cook_local_file_without_public_url", env,
         run(env, _job(env, 111, url="", local=env.src, voice="vieneu:Mỹ Duyên", title_hint="Tải lên")))


def test_silent_music_botkeep(env):
    env.segs = []
    os.makedirs(bgm.DIR, exist_ok=True)
    _wav(os.path.join(bgm.DIR, "nhac.mp3"), 30.0)
    snap("silent_music_botkeep", env, run(env, _job(env, 112)))


def test_silent_music_kaggle(env, monkeypatch):
    kaggle_on(monkeypatch, "https://bot.example.com")
    env.segs = []
    os.makedirs(bgm.DIR, exist_ok=True)
    _wav(os.path.join(bgm.DIR, "nhac.mp3"), 30.0)
    snap("silent_music_kaggle", env, run(env, _job(env, 113)))


def test_silent_no_music_library(env):
    env.segs = []
    snap("silent_no_music_library", env, run(env, _job(env, 114)))


def test_silent_narrate_scenes(env, monkeypatch):
    monkeypatch.setattr(config, "NO_SPEECH", "narrate")
    env.segs = []
    env.cuts = []
    env.late_cuts = [1.5, 4.0, 6.0]
    snap("silent_narrate_scenes", env, run(env, _job(env, 115)))


def test_legacy_oneshot_path(env, monkeypatch):
    monkeypatch.setattr(config, "NO_SPEECH", "narrate")
    env.segs = [(0.5, 3.0, "thịt kho tộ ngon lắm luôn đó cả nhà ơi")]
    env.cuts = []
    snap("legacy_oneshot_path", env, run(env, _job(env, 116)))


def test_legacy_oneshot_ai_fallback(env, monkeypatch):
    monkeypatch.setattr(config, "NO_SPEECH", "narrate")
    env.segs = [(0.5, 3.0, "thịt kho tộ ngon lắm luôn đó cả nhà ơi")]
    env.cuts = []
    env.legacy_fallback = True
    snap("legacy_oneshot_ai_fallback", env, run(env, _job(env, 117)))


def test_teaser(env, monkeypatch):
    monkeypatch.setattr(config, "TEASER", "1")
    monkeypatch.setattr(config, "CUT_PROMO", "0")

    async def best(*a, **k):
        env.rec.add("cover.best_moment", k.get("opening"))
        return {"t": 4.5, "score": 1.0, "n": 6, "ai": True, "finished": True, "opens_with_dish": False}
    monkeypatch.setattr(cover_mod, "best_moment", best)

    async def near(*a):
        return []
    monkeypatch.setattr(scenes, "cuts_near", near)
    monkeypatch.setattr(cover_mod, "opening_repeat_sync", lambda *a: (0.1, 0.0))
    snap("teaser", env, run(env, _job(env, 118)))


def test_music_replace(env):
    song = _wav(os.path.join(env.tmp, "song.mp3"), 20.0)
    snap("music_replace", env, run(env, _job(env, 119, url="", mode="music", local=env.src, music_local=song, voice="")))


def test_music_bg_kaggle(env, monkeypatch):
    kaggle_on(monkeypatch, "https://bot.example.com")
    song = _wav(os.path.join(env.tmp, "song.mp3"), 20.0)
    snap("music_bg_kaggle", env, run(env, _job(env, 120, url="", mode="music", music_mix="bg", local=env.src,
                                               music_local=song, voice="")))


def test_photo(env):
    song = _wav(os.path.join(env.tmp, "song.mp3"), 20.0)
    img = _jpg(os.path.join(env.tmp, "a.jpg"))
    snap("photo", env, run(env, _job(env, 121, url="", mode="photo", duration=10, images=[img], music_local=song,
                                     voice="", title_hint="Đà Lạt")))


def test_photo_kaggle(env, monkeypatch):
    kaggle_on(monkeypatch, "https://bot.example.com")
    song = _wav(os.path.join(env.tmp, "song.mp3"), 20.0)
    img = _jpg(os.path.join(env.tmp, "a.jpg"))
    snap("photo_kaggle", env, run(env, _job(env, 122, url="", mode="photo", duration=10, images=[img],
                                            music_local=song, voice="", title_hint="Đà Lạt")))


def test_download(env):
    snap("download", env, run(env, _job(env, 123, mode="download", voice="")))


# ----- video dài: cắt phần / đoạn hay (giao từng phần qua deliver) -----

@pytest.fixture(scope="module")
def long_src(tmp_path_factory):
    d = tmp_path_factory.mktemp("long")
    return _video(str(d / "long.mp4"), dur=130, size="180x320", rate=15)


def _long(env, long_src):
    dst = os.path.join(env.tmp, "up", "long.mp4")
    shutil.copy(long_src, dst)
    return dst


def test_split_kaggle_batch_one_piece_fails(env, monkeypatch, long_src):
    kaggle_on(monkeypatch, "https://bot.example.com")
    env.batch_fail = {1}
    src = _long(env, long_src)
    snap("split_kaggle_batch_one_piece_fails", env,
         run(env, _job(env, 124, url="", local=src, long_mode="split60", voice="vieneu:Mỹ Duyên"), fn="run_job"))


def test_highlights_botkeep(env, long_src):
    src = _long(env, long_src)
    snap("highlights_botkeep", env, run(env, _job(env, 125, url="", local=src, long_mode="highlight"), fn="run_job"))


def test_full_long_video_botkeep(env, long_src):
    src = _long(env, long_src)
    snap("full_long_video_botkeep", env, run(env, _job(env, 126, url="", local=src, long_mode="full")))


# ---------------- bản 43: bộ nhận diện «Bếp Đong Đủ» làm mặc định ----------------

def _brand_kit(monkeypatch):
    """Mặc định của mã từ bản 43 (bộ nhận diện + phong cách mạnh + công thức + mẹo + bản ngắn)."""
    for k in brand.OLD_DEFAULTS:
        monkeypatch.setattr(config, k, config.EDITABLE[k])


def test_brand_kit_default_botkeep(env, monkeypatch):
    """Màn kết nối thêm 2,2 giây sau video (khung cuối giữ nguyên), câu chốt cuối, âm hiệu sau câu cuối."""
    _brand_kit(monkeypatch)
    monkeypatch.setattr(config, "CUT_PROMO", "0")
    snap("brand_kit_default_botkeep", env, run(env, _job(env, 140)))


def test_brand_kit_default_kaggle(env, monkeypatch):
    _brand_kit(monkeypatch)
    monkeypatch.setattr(config, "CUT_PROMO", "0")
    kaggle_on(monkeypatch)
    snap("brand_kit_default_kaggle", env, run(env, _job(env, 141, voice="vieneu:Mỹ Duyên")))
