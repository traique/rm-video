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
    assert kaggle_worker.words_per_sec(config.VOICE_FEMALE) is None
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
    assert "link" in kaggle_worker.usable(Job(url="", mode="cook", duration=0, voice="vieneu:Adam"))
    assert kaggle_worker.usable(Job(url="u", mode="cook", duration=0, voice=config.VOICE_FEMALE))


# ---------------- notebook: không lộ key, chạy được ----------------

def test_kernel_text_has_code_and_no_secrets(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-bi-mat")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_bi_mat")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "EAA-bi-mat")
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Adam")
    spec = kaggle_worker.build_spec(job, [{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "Xin chào",
                                          "ost": "new"}], 5.0, 0, 1.0, True, "T", [], None,
                                    {"ok": True, "engines": ["kim"], "windows": [[0, 5]]}, [(0, 2)], 90)
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
    def __init__(self):
        self.calls, self.status = [], ["QUEUED", "RUNNING", "COMPLETE"]


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

    def synth(items, tmp):
        out = {}
        for k, text in items:
            seen.append(text)
            p = os.path.join(tmp, f"v{k}.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.2",
                            "-af", "apad=pad_dur=0.6", "-ar", "48000", p], check=True)
            out[k] = p
        return out
    spec = {"mode": "cook", "out_dur": 6.0, "render_dur": 0, "stretch": 1.0, "allow_hd": True, "title": "",
            "subs": True, "pos_y": None, "bands": [], "sep": {"ok": False}, "speech": [[0.5, 2.0]],
            "segments": [{"i": 0, "start": 0.0, "slot": 3.0, "span": 3.0, "text": "Cho 100g bò vô", "ost": "new"},
                         {"i": 1, "start": 3.0, "slot": 3.0, "span": 3.0, "text": "", "ost": "orig"}]}
    res = asyncio.run(kaggle_job.produce(spec, inp, str(tmp_path), synth))
    assert seen == ["Cho một trăm gam bò vô"]                    # chỉ đoạn có lời mới, số đã thành chữ
    assert os.path.getsize(res["video"]) > 0 and res["plan"][0]["factor"] >= 1.0
    assert res["srt"] and "bò" in res["srt"] and res["amb"] is False


# ---------------- pipeline: chạy trên Kaggle, lỗi thì làm tiếp trên Botkeep ----------------

@pytest.fixture
def kaggle_fakes(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
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
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and kaggle_fakes["tts"] == 0                  # không đọc giọng trên Botkeep
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
            r = await c.get("/api/voices", headers={"X-Auth": "pw-123"})
            return [v["id"] for v in await r.json()]
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    ids = asyncio.run(go())
    assert os.path.exists(kaggle_worker.VOICE_REF) and "vieneu:clone" in ids and "vieneu:Mỹ Duyên" in ids
