"""Kaggle: chuẩn hoá chữ cho VieNeu, client API (máy chủ Kaggle giả), phần chạy trên Kaggle (giọng giả, dựng thật),
nối vào pipeline + lùi về Botkeep khi Kaggle lỗi."""
import asyncio
import base64
import io
import json
import os
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import config
import core_video
import kaggle_api
import kaggle_worker
import vi_text
from queue_manager import Job
from test_pipeline import fakes  # noqa: F401  (fixture provider giả dùng chung)


# ---------------- chữ cho VieNeu ----------------

@pytest.mark.parametrize("n,want", [(0, "không"), (5, "năm"), (15, "mười lăm"), (21, "hai mươi mốt"),
                                    (25, "hai mươi lăm"), (105, "một trăm lẻ năm"), (1500, "một ngàn năm trăm"),
                                    (1250000, "một triệu hai trăm năm mươi ngàn")])
def test_number_words_southern(n, want):
    assert vi_text.number_words(n) == want


@pytest.mark.parametrize("s,want", [
    ("Lấy 100g bò", "Lấy một trăm gam bò"), ("cho 1/3 muỗng đường", "cho một phần ba muỗng đường"),
    ("1/2 muỗng muối", "nửa muỗng muối"), ("khoảng 60% thì vớt", "khoảng sáu mươi phần trăm thì vớt"),
    ("nấu 5-7 phút", "nấu năm đến bảy phút"), ("1,5kg gà", "một phẩy năm ký gà"),
    ("200ml nước", "hai trăm mi li lít nước"), ("1.000.000đ", "một triệu đồng"), ("Không có số", "Không có số")])
def test_normalize_for_tts(s, want):
    assert vi_text.normalize(s) == want


# ---------------- giọng ----------------

def test_voice_defaults_and_edge_fallback(monkeypatch):
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    monkeypatch.setattr(config, "VIENEU_VOICE_FEMALE", "Mỹ Duyên")
    monkeypatch.setattr(config, "VIENEU_VOICE_MALE", "Adam")
    assert config.default_voice() == "vieneu:Mỹ Duyên" and config.default_voice(male=True) == "vieneu:Adam"
    assert core_video._edge_fallback_voice("vieneu:Adam") == config.VOICE_MALE
    assert core_video._edge_fallback_voice("vieneu:Mỹ Duyên") == config.VOICE_FEMALE
    assert core_video._capcut_fallback_voice("vieneu:Adam") == config.CAPCUT_VOICE_MALE
    assert kaggle_worker.words_per_sec("vieneu:Mỹ Duyên") == pytest.approx(3.08)
    assert kaggle_worker.words_per_sec(config.VOICE_FEMALE) == kaggle_worker.RATE_EDGE
    assert kaggle_worker.voice_engine("vieneu:Adam") == "vieneu" and kaggle_worker.voice_engine(config.VOICE_MALE) == "edge"
    monkeypatch.setattr(config, "TTS_PROVIDER", "edge")
    assert config.default_voice() == config.VOICE_FEMALE


def test_usable_reasons(monkeypatch):
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "")
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    assert "KAGGLE" in kaggle_worker.usable(job)
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    assert kaggle_worker.usable(job) == ""
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    assert "PUBLIC_URL" in kaggle_worker.usable(Job(url="", mode="cook", duration=0, voice="vieneu:Adam"), "/x.mp4")
    assert kaggle_worker.usable(Job(url="u", mode="cook", duration=0, voice=config.VOICE_FEMALE)) == ""   # edge cũng được
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example.com")
    assert kaggle_worker.usable(Job(url="", mode="cook", duration=0, voice="vieneu:Adam"), "/x.mp4") == ""
    monkeypatch.setattr(config, "KAGGLE_USE", "0")
    assert kaggle_worker.usable(job) == "KAGGLE_USE=0"


# ---------------- notebook: không lộ key, chạy được ----------------

def test_kernel_text_has_code_and_no_secrets(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-bi-mat")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_bi_mat")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "EAA-bi-mat")
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Adam")
    spec = kaggle_worker.build_spec(job, [{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "Xin chào",
                                          "ost": "new"}], 5.0, 0, 1.0, True, "T", [], None,
                                    {"ok_remote": True, "windows": [[0, 5]]}, [(0, 2)], 90)
    text = kaggle_worker.kernel_text(spec)
    compile(text, "kernel.py", "exec")
    for secret in ("AIza-bi-mat", "KGAT_bi_mat", "EAA-bi-mat"):
        assert secret not in text and secret not in json.dumps(spec)
    assert spec["voice"]["name"] == "Adam" and spec["sep"]["ok"] is True
    b64 = text.split('b64decode("', 1)[1].split('"', 1)[0]
    names = set(zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))).namelist())
    assert set(kaggle_worker.BUNDLE) <= names


# ---------------- client API với máy chủ Kaggle giả ----------------

class _Fake:
    def __init__(self, owner=""):
        self.calls, self.status, self.owner = [], ["QUEUED", "RUNNING", "COMPLETE"], owner


@pytest.fixture(autouse=True)
def _no_owner_cache():
    kaggle_api._OWNER.clear()
    yield
    kaggle_api._OWNER.clear()


def _server(fake):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            if not self.headers.get("User-Agent", "").startswith("kaggle-api/"):
                self.send_response(404)
                self.end_headers()
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            method = self.path.rsplit("/", 1)[1]
            fake.calls.append((method, body, self.headers.get("Authorization")))
            if method == "GetKernelSessionStatus":
                out = {"status": fake.status.pop(0) if fake.status else "COMPLETE"}
            elif method == "IntrospectToken":
                out = {"active": True, "username": fake.owner} if fake.owner else {}
            elif method == "SaveKernel" and fake.owner and not body["slug"].startswith(fake.owner + "/"):
                # Kaggle thật: slug sai tên chủ token -> tưởng notebook mới -> trùng tiêu đề (đo 12/11/2026)
                data = json.dumps({"error": {"code": 409, "message": 'The requested title "t" is already in use by '
                                             "a notebook. Please choose another title."}}).encode()
                self.send_response(409)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            else:
                out = {"SaveKernel": {"ref": "/code/u/rmvideo-worker", "versionNumber": 3},
                       "GetAcceleratorQuotaStatistics": {"gpuQuota": {"timeUsed": "600s",
                                                                      "totalTimeAllowed": "108000s"}}}.get(method, {})
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_api_client_against_fake_kaggle(monkeypatch):
    fake = _Fake()
    srv = _server(fake)
    monkeypatch.setattr(kaggle_api, "API", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    try:
        assert kaggle_api.quota()["gpu_left"] == 107400
        kaggle_api.push("rmvideo-worker", "t", "print(1)", gpu=True)
        m, body, auth = fake.calls[-1]
        assert m == "SaveKernel" and body["slug"] == "u/rmvideo-worker" and body["isPrivate"] is True
        assert body["enableGpu"] and body["enableInternet"] and body["machineShape"] == "NvidiaTeslaT4"
        assert auth == "Bearer KGAT_x"
        seen = []
        assert kaggle_api.wait("rmvideo-worker", 60, every=0, on_status=lambda s, t: seen.append(s),
                               sleep=lambda s: None) == "COMPLETE"
        assert seen == ["QUEUED", "RUNNING", "COMPLETE"]
        fake.status = ["RUNNING", "ERROR"]
        with pytest.raises(kaggle_api.KaggleError, match="ERROR"):
            kaggle_api.wait("rmvideo-worker", 60, every=0, sleep=lambda s: None)
    finally:
        srv.shutdown()


@pytest.mark.parametrize("typed", ["https://www.kaggle.com/QueTrai/", "@quetrai", "quetrai1", ""])
def test_username_comes_from_token_owner(monkeypatch, typed):
    """Lỗi thật trên Botkeep: KAGGLE_USERNAME điền sai -> 409 'title already in use' -> mọi job rơi về Botkeep."""
    fake = _Fake(owner="quetrai")
    srv = _server(fake)
    monkeypatch.setattr(kaggle_api, "API", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", typed)
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    try:
        assert kaggle_api.configured()
        kaggle_api.push("rmvideo-worker", "t", "print(1)", gpu=False)
        kaggle_api.status("rmvideo-worker")
        saves = [b for m, b, _ in fake.calls if m == "SaveKernel"]
        assert saves[-1]["slug"] == "quetrai/rmvideo-worker"
        assert fake.calls[-1][1]["userName"] == "quetrai"
        assert sum(m == "IntrospectToken" for m, _, _ in fake.calls) == 1      # hỏi 1 lần rồi nhớ
    finally:
        srv.shutdown()


def test_wrong_username_without_introspect_gives_clear_409(monkeypatch):
    fake = _Fake(owner="quetrai")
    srv = _server(fake)
    monkeypatch.setattr(kaggle_api, "API", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(kaggle_api, "owner", lambda: (_ for _ in ()).throw(kaggle_api.KaggleError("mạng lỗi")))
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "quetrai1")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    try:
        with pytest.raises(kaggle_api.KaggleError, match="KAGGLE_USERNAME"):
            kaggle_api.push("rmvideo-worker", "t", "print(1)", gpu=False)
    finally:
        srv.shutdown()


@pytest.mark.parametrize("v,want", [("https://www.kaggle.com/quetrai", "quetrai"), ("kaggle.com/QueTrai/code", "quetrai"),
                                    ("@quetrai", "quetrai"), (" quetrai ", "quetrai"), ("", "")])
def test_clean_user(v, want):
    assert kaggle_api.clean_user(v) == want


def test_settings_accept_kaggle_profile_link(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "")
    config.save_settings({"KAGGLE_USERNAME": "https://www.kaggle.com/QueTrai"})
    assert json.load(open(tmp_path / "s.json"))["KAGGLE_USERNAME"] == "quetrai"


# ---------------- phần chạy trên Kaggle (giọng giả, dựng thật) ----------------

def _video(path, dur=6):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=360x640:rate=25",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", path], check=True)


def test_kaggle_side_produce_with_fake_voice(tmp_path, monkeypatch):
    import kaggle_job
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    inp = str(tmp_path / "in.mp4")
    _video(inp)
    seen = []

    async def synth(items, tmp):
        out = {}
        for k, text in items:
            seen.append(text)
            p = os.path.join(tmp, f"v{k}.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.2",
                            "-af", "apad=pad_dur=0.6", "-ar", "48000", p], check=True)
            out[k] = {"path": p, "words": None}
        return out
    spec = {"mode": "cook", "out_dur": 6.0, "render_dur": 0, "stretch": 1.0, "allow_hd": True, "title": "",
            "subs": True, "pos_y": None, "bands": [], "sep": {"ok": False}, "speech": [[0.5, 2.0]],
            "segments": [{"i": 0, "start": 0.0, "slot": 3.0, "span": 3.0, "text": "Cho 100g bò vô", "ost": "new"},
                         {"i": 1, "start": 3.0, "slot": 3.0, "span": 3.0, "text": "", "ost": "orig"}]}
    res = asyncio.run(kaggle_job.produce(spec, inp, str(tmp_path), synth))
    assert seen == ["Cho 100g bò vô"]                            # chỉ đoạn có lời mới
    assert os.path.getsize(res["video"]) > 0 and res["plan"][0]["factor"] >= 1.0
    assert res["srt"] and "bò" in res["srt"] and res["amb"] is False


# ---------------- pipeline: chạy trên Kaggle, lỗi thì làm tiếp trên Botkeep ----------------

@pytest.fixture
def kaggle_fakes(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "0")      # phiên phân tích: test riêng (test_kaggle_analyze.py)
    return fakes


def _url_job(tmp_path):
    from test_pipeline import _video as mk
    src = str(tmp_path / "up" / "src.mp4")
    mk(src)

    async def fake_download(url, tmpdir, max_duration=90):
        import shutil
        dst = os.path.join(tmpdir, "input.mp4")
        shutil.copy(src, dst)
        return dst, "Món ngon", "", {"uploader": "Kênh", "webpage_url": url}
    return fake_download


def test_pipeline_runs_heavy_part_on_kaggle(kaggle_fakes, monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(core_video, "download", _url_job(tmp_path))
    got = {}

    async def fake_produce(spec, tmp, on_step=None):
        got["spec"] = spec
        out = os.path.join(tmp, "output.mp4")
        _video(out, 4)
        plan = [{"start": s["start"], "factor": 1.0, "end": s["start"] + 1} for s in spec["segments"]]
        return {"ok": True, "video": out, "plan": plan, "too_long": [], "srt": "1\n00:00:00,000 --> 00:00:01,000\nx\n",
                "amb": True, "notes": [], "timings": {"tts": 4, "render": 60}, "gpu": True}
    monkeypatch.setattr(kaggle_worker, "produce", fake_produce)
    shortened = []

    async def fake_shorten(items):
        shortened.extend(i for i, _, _ in items)
        return {i: " ".join(t.split()[:2]) for i, t, _ in items}
    monkeypatch.setattr(pipeline.prompt_engine, "shorten", fake_shorten)
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and kaggle_fakes["tts"] == 0                  # không đọc giọng trên Botkeep
    # Mỹ Duyên ~3.1 chữ/giây: "Lời mới đoạn k nè" (5 chữ ~1.6s) không vừa khe ~1.3s -> rút gọn TRƯỚC khi gửi Kaggle
    assert shortened and all(len(s["text"].split()) <= 2 for s in got["spec"]["segments"] if s["i"] in shortened)
    assert got["spec"]["voice"]["name"] == "Mỹ Duyên" and got["spec"]["segments"]
    assert "Kaggle (GPU)" in job.error and job.srt.startswith("1")


def test_pipeline_falls_back_to_botkeep_when_kaggle_fails(kaggle_fakes, monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(core_video, "download", _url_job(tmp_path))

    async def broken(spec, tmp, on_step=None):
        raise kaggle_api.KaggleError("Kaggle lỗi 500 ở SaveKernel")
    monkeypatch.setattr(kaggle_worker, "produce", broken)
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Adam")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and kaggle_fakes["tts"] > 0                   # đọc giọng trên Botkeep
    assert "Kaggle lỗi" in job.error


def test_voice_ref_upload(monkeypatch, tmp_path):
    from aiohttp import FormData
    from test_security import _client
    monkeypatch.setattr(kaggle_worker, "VOICE_REF", str(tmp_path / "voice_ref.mp3"))
    good, short = str(tmp_path / "g.wav"), str(tmp_path / "s.wav")
    for p, d in ((good, 6), (short, 1)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={d}", p],
                       check=True)

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            for p, want in ((short, 400), (good, 200)):
                fd = FormData()
                fd.add_field("ref", open(p, "rb"), filename=os.path.basename(p))
                r = await c.post("/api/voice_ref", data=fd, headers={"X-Auth": "pw-123"})
                assert r.status == want, await r.text()
            # bấm Lưu 2 lần: 2 lần tải chạy song song không được xoá file tạm của nhau (lỗi thật trên Botkeep)
            async def up():
                fd = FormData()
                fd.add_field("ref", open(good, "rb"), filename="g.wav")
                r = await c.post("/api/voice_ref", data=fd, headers={"X-Auth": "pw-123"})
                return r.status, await r.text()
            for st, txt in await asyncio.gather(up(), up(), up()):
                assert st == 200, txt
            assert not [f for f in os.listdir(tmp_path) if ".upload" in f or ".tmp.mp3" in f]
            r = await c.get("/api/voices", headers={"X-Auth": "pw-123"})
            return [v["id"] for v in await r.json()]
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    ids = asyncio.run(go())
    assert os.path.exists(kaggle_worker.VOICE_REF) and "vieneu:clone" in ids and "vieneu:Mỹ Duyên" in ids


def test_vieneu_synth_normalizes_numbers(monkeypatch, tmp_path):
    import kaggle_job
    got = {}

    class FakeTTS:
        def infer_batch(self, texts, voice):
            got["texts"], got["voice"] = texts, voice
            return [b"x"] * len(texts)

        def save(self, a, p):
            open(p, "wb").write(a)
    monkeypatch.setattr(kaggle_job, "_vieneu", lambda: FakeTTS())
    synth = kaggle_job.make_synth({"voice": {"engine": "vieneu", "name": "Mỹ Duyên"}}, None)
    out = asyncio.run(synth([(0, "Cho 100g bò, 1/3 muỗng đường")], str(tmp_path)))
    assert got == {"texts": ["Cho một trăm gam bò, một phần ba muỗng đường"], "voice": "Mỹ Duyên"}
    assert os.path.exists(out[0]["path"]) and out[0]["words"] is None


def test_edge_synth_on_kaggle_keeps_word_timings(monkeypatch, tmp_path):
    import kaggle_job

    async def fake_ex(text, voice, path):
        open(path, "wb").write(b"x")
        return [(0.0, 0.4, text.split()[0])], "edge"
    monkeypatch.setattr(core_video, "synth_clip_ex", fake_ex)
    synth = kaggle_job.make_synth({"voice": {"engine": "edge", "id": config.VOICE_FEMALE}}, None)
    out = asyncio.run(synth([(0, "Xin chào"), (2, "Tạm biệt")], str(tmp_path)))
    assert out[0]["words"] == [(0.0, 0.4, "Xin")] and out[2]["words"][0][2] == "Tạm"


def test_temporary_input_links(monkeypatch, tmp_path):
    from test_security import _client
    f = tmp_path / "upload video.mp4"
    f.write_bytes(b"VIDEO-DATA")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    with pytest.raises(kaggle_api.KaggleError, match="PUBLIC_URL"):
        kaggle_worker.ref(str(f))
    assert kaggle_worker.ref(str(f), "https://youtu.be/x")["url"] == "https://youtu.be/x"     # có link: không cần
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example.com/")
    r = kaggle_worker.ref(str(f), "https://youtu.be/x")
    assert r["url"] and r["http"].startswith("https://bot.example.com/kin/") and r["name"] == "upload video.mp4"
    tok = r["http"].split("/kin/")[1].split("/")[0]
    assert len(tok) >= 40 and kaggle_worker.served(tok) == str(f)

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            ok = await c.get(f"/kin/{tok}/x.mp4")                     # không cần đăng nhập, chỉ cần đúng mã
            bad = await c.get("/kin/sai-ma/x.mp4")
            return ok.status, await ok.read(), bad.status
    assert asyncio.run(go()) == (200, b"VIDEO-DATA", 404)
    kaggle_worker.release({"src": r})                                  # Kaggle chạy xong: huỷ link
    assert kaggle_worker.served(tok) is None
    r2 = kaggle_worker.ref(str(f))
    tok2 = r2["http"].split("/kin/")[1].split("/")[0]
    monkeypatch.setattr(kaggle_worker.time, "time", lambda: 1e12)        # hết hạn
    assert kaggle_worker.served(tok2) is None


# ---------------- 🎵 / 📸 trên Kaggle (Kaggle giả chạy đúng hàm thật với đầu vào lấy qua link tạm) ----------------

def _local(ref):
    """Kaggle giả: lấy file qua link tạm của Botkeep (đúng như notebook thật)."""
    tok = ref["http"].split("/kin/")[1].split("/")[0]
    path = kaggle_worker.served(tok)
    assert path, "link tạm không trỏ tới file"
    return path


@pytest.fixture
def kaggle_on(monkeypatch):
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USE", "1")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example.com")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "0")


def test_photo_mode_on_kaggle_keeps_effect_history(kaggle_on, monkeypatch, tmp_path):
    import pipeline
    import slideshow
    import trendfx
    from test_photo import _img, _song
    song = _song(tmp_path / "song.mp3")
    seen = {}

    async def fake_produce(spec, tmp, on_step=None):
        seen["spec"] = spec
        imgs = [_local(r) for r in spec["images"]]
        out = os.path.join(tmp, "kaggle.mp4")
        plan = await slideshow.render(imgs, _local(spec["music"]), out, spec["duration"], seed=spec["seed"],
                                      recent=spec["recent"])
        return {"video": out, "plan": json.loads(json.dumps(plan, default=float)), "timings": {"render": 9}}
    monkeypatch.setattr(kaggle_worker, "produce", fake_produce)
    remembered = []
    monkeypatch.setattr(trendfx, "remember", lambda name, path=None: remembered.append(name))
    job = Job(url="", mode="photo", duration=8, voice="", images=[_img(tmp_path / "a.jpg", "1080x1350")],
              music_local=song, title_hint="Đà Lạt", source="web")
    out = asyncio.run(pipeline.process(job, str(tmp_path)))
    assert out.endswith("kaggle.mp4") and seen["spec"]["kind"] == "photo" and isinstance(seen["spec"]["seed"], int)
    assert remembered and "Kaggle (CPU)" in job.error and "BPM" in job.script


def test_music_mode_on_kaggle_and_fallback(kaggle_on, monkeypatch, tmp_path):
    import pipeline
    from test_photo import _song
    song = _song(tmp_path / "song.mp3")
    vid = str(tmp_path / "dance.mp4")
    _video(vid, 8)
    seen = []

    async def fake_produce(spec, tmp, on_step=None):
        seen.append(spec)
        out = os.path.join(tmp, "kaggle.mp4")
        await core_video.render_music(_local(spec["src"]), _local(spec["music"]), out, spec["duration"],
                                      **spec["music_args"])
        return {"video": out, "timings": {"render": 5}}
    monkeypatch.setattr(kaggle_worker, "produce", fake_produce)
    job = Job(url="", mode="music", duration=0, voice="", local=vid, music_local=song, music_mix="bg", source="web")
    (tmp_path / "a").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "a")))
    assert out.endswith("kaggle.mp4") and seen[0]["kind"] == "music" and seen[0]["music_args"]["keep_orig"] is True

    async def broken(spec, tmp, on_step=None):
        raise kaggle_api.KaggleError("hết hạn mức")
    monkeypatch.setattr(kaggle_worker, "produce", broken)
    job2 = Job(url="", mode="music", duration=0, voice="", local=vid, music_local=song, source="web")
    (tmp_path / "b").mkdir()
    out2 = asyncio.run(pipeline.process(job2, str(tmp_path / "b")))
    assert os.path.exists(out2) and not out2.endswith("kaggle.mp4") and "Kaggle lỗi" in job2.error


def test_music_mode_without_public_url_stays_local(kaggle_on, monkeypatch, tmp_path):
    import pipeline
    from test_photo import _song
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    song = _song(tmp_path / "song.mp3")
    vid = str(tmp_path / "dance.mp4")
    _video(vid, 6)

    async def never(spec, tmp, on_step=None):
        raise AssertionError("không được gọi Kaggle khi Kaggle không lấy được file")
    monkeypatch.setattr(kaggle_worker, "produce", never)
    job = Job(url="", mode="music", duration=0, voice="", local=vid, music_local=song, music_mix="bg", source="web")
    out = asyncio.run(pipeline.process(job, str(tmp_path)))
    assert os.path.exists(out) and "PUBLIC_URL" in job.error


# ---------------- video dài: mọi phần chung MỘT phiên Kaggle ----------------

def _long_video(path, dur=130):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=180x320:rate=15",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", path], check=True)


@pytest.mark.parametrize("fail_second", [False, True])
def test_split_runs_all_pieces_in_one_kaggle_session(kaggle_fakes, kaggle_on, monkeypatch, tmp_path, fail_second):
    import pipeline
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out")
    src = str(tmp_path / "up" / "long.mp4")
    _long_video(src)
    calls = []

    async def fake_batch(specs, tmp, on_step=None):
        calls.append(specs)
        res = []
        for k, sp in enumerate(specs):
            assert sp["kind"] == "remake" and _local(sp["src"]).endswith("piece.mp4")   # đúng file phần đã cắt
            if fail_second and k == 1:
                res.append(kaggle_api.KaggleError("Kaggle hết giờ"))
                continue
            out = os.path.join(tmp, f"k{k}.mp4")
            _video(out, 3)
            plan = [{"start": s["start"], "factor": 1.0, "end": s["start"] + 1} for s in sp["segments"]]
            res.append({"video": out, "plan": plan, "too_long": [], "srt": "", "amb": False, "notes": [],
                        "timings": {"tts": 1, "render": 2}, "gpu": True})
        return res

    async def never(spec, tmp, on_step=None):
        raise AssertionError("video dài phải gom lô, không gửi từng phần")
    monkeypatch.setattr(kaggle_worker, "produce_batch", fake_batch)
    monkeypatch.setattr(kaggle_worker, "produce", never)
    job = Job(url="", mode="cook", duration=0, voice="vieneu:Mỹ Duyên", local=src, long_mode="split60", source="web")
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process_split(job, str(tmp_path / "w")))
    videos = [p for p in job.parts if p["label"].startswith("Phần")]
    assert len(calls) == 1 and len(calls[0]) == len(videos) >= 2                    # một phiên cho mọi phần
    assert (kaggle_fakes["tts"] > 0) == fail_second                                  # phần lỗi làm lại trên Botkeep


# ---------------- rà soát trước deploy (lần 2) ----------------

@pytest.mark.parametrize("s,want", [
    ("12.5.2026", "mười hai chấm năm chấm hai ngàn không trăm hai mươi sáu"),     # không bao giờ ném lỗi
    ("1.000,5", "một chấm không chấm năm"),
    ("5-7kg", "năm đến bảy ký"), ("50-60%", "năm mươi đến sáu mươi phần trăm"),
    ("ngày 12/11", "ngày mười hai tháng mười một"), ("lúc 10:30", "lúc mười giờ ba mươi"),
    ("sinh 1/2/2026", "sinh ngày một tháng hai năm hai ngàn không trăm hai mươi sáu")])
def test_normalize_hard_cases(s, want):
    assert vi_text.normalize(s) == want


def test_login_page_has_no_next_redirect():
    html = open(os.path.join(kaggle_worker.BASE, "static", "login.html"), encoding="utf-8").read()
    assert "URLSearchParams" not in html and "const next='/'" in html


def test_batch_shares_clone_ref_and_catches_any_error(monkeypatch, tmp_path):
    ref = tmp_path / "ref.mp3"
    ref.write_bytes(os.urandom(150_000))
    monkeypatch.setattr(kaggle_worker, "VOICE_REF", str(ref))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://bot.example.com")
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:clone")
    specs = [kaggle_worker.build_spec(job, [{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "a", "ost": "new"}],
                                      5.0, 0, 1.0, True, "T", [], None, {}, [], 90) for _ in range(6)]
    seen = {}

    def fake_run(spec, tmp, on_step):
        seen["spec"] = spec
        return [{"video": "x"}] * len(spec["jobs"])
    monkeypatch.setattr(kaggle_worker, "_run_sync", fake_run)
    asyncio.run(kaggle_worker.produce_batch(specs, str(tmp_path)))
    assert "ref_b64" in seen["spec"] and all("ref_b64" not in j for j in seen["spec"]["jobs"])   # gói một lần
    assert len(kaggle_worker.kernel_text(seen["spec"]).encode()) < 990_000

    def boom(spec, tmp, on_step):
        raise TypeError("spec không json hoá được")
    monkeypatch.setattr(kaggle_worker, "_run_sync", boom)
    res = asyncio.run(kaggle_worker.produce_batch(specs[:2], str(tmp_path)))
    assert len(res) == 2 and all(isinstance(r, kaggle_api.KaggleError) for r in res)


def test_piece_retry_keeps_script_and_cancel_aborts(kaggle_fakes, kaggle_on, monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(pipeline, "OUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out")
    src = str(tmp_path / "up" / "long.mp4")
    _long_video(src, 70)

    async def all_fail(specs, tmp, on_step=None):
        return [kaggle_api.KaggleError("hết giờ")] * len(specs)
    monkeypatch.setattr(kaggle_worker, "produce_batch", all_fail)
    job = Job(url="", mode="cook", duration=0, voice="vieneu:Adam", local=src, long_mode="split60", source="web")
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process_split(job, str(tmp_path / "w")))
    n_parts = len([p for p in job.parts if p["label"].startswith("Phần")])
    assert n_parts >= 1 and kaggle_fakes["llm"] == n_parts          # làm lại trên Botkeep KHÔNG gọi AI thêm lần nào
    # huỷ job đang chờ Kaggle: dừng chờ ngay + gọi huỷ phiên
    calls = []
    monkeypatch.setattr(kaggle_api, "cancel", lambda slug: calls.append(slug))
    kaggle_worker._ABORT.update(flag=False, running=True)
    kaggle_worker.abort()
    assert calls == [kaggle_worker.SLUG] and kaggle_worker._ABORT["flag"]
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("RUNNING", ""))
    with pytest.raises(kaggle_api.KaggleError, match="huỷ"):
        kaggle_api.wait("x", 60, every=0, sleep=lambda s: None, stop=lambda: True)
