"""Bản 27: bóc lời + phân tích hình trên Kaggle (Whisper large-v3 nghe giọng đã tách), hook mở đầu, bỏ lời kêu gọi
đăng ký kênh. Số liệu thật (Kaggle T4, video cá kèo 220s, 13/11/2026): tách giọng GPU 34s, Whisper 55s, dò cảnh 59s
song song, cả phiên 3.6-3.9 phút; bóc được 0-29s và 52-83s mà Groq bỏ sót."""
import asyncio
import io
import json
import os
import subprocess
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import pytest

import cache
import config
import core_video
import kaggle_api
import kaggle_worker
import prompt_engine
import transcribe
from queue_manager import Job
from test_pipeline import fakes  # noqa: F401  (fixture provider giả dùng chung)


def _w(start, text, step=0.4):
    return [{"start": round(start + k * step, 2), "end": round(start + (k + 1) * step, 2), "word": w}
            for k, w in enumerate(text.split())]


def _whisper():
    """Kết quả asr_local giống thật: câu đầu có dấu, các khung sau không dấu, một câu chào kết bịa, một câu lặp vòng,
    một câu dài 12s."""
    lines = [(1.9, 5.3, "Hôm nay mình chỉ mọi người cái mẹo làm cá kèo chiên giòn mà nó không có nhảy ra."),
             (5.4, 8.2, "Đặc biệt là cách này cá kèo ăn giòn tan trong miệng."),
             (10.5, 15.1, "đầu tiên mình lấy cá kèo về thì mình phải rửa sạch cho mình nha"),
             (15.7, 18.5, "Đừng quên đăng ký kênh để nhận thêm những video mới nhé"),
             (20.0, 32.0, "lúc này mình sẽ thả cá vào chảo dầu thật nóng và để yên như vậy khoảng hai phút cho cá "
                          "cứng lại rồi mới bắt đầu đảo nhẹ tay cho cá không bị gãy"),
             (33.0, 36.0, "Như vậy là cá kèo mình sẽ giòn lâu từ trong ra ngoài"),
             (37.0, 40.0, "Như vậy là cá kèo mình sẽ giòn lâu từ trong ra ngoài"),
             (41.0, 44.0, "nhớ bấm theo dõi kênh của mình nha")]
    segs, words = [], []
    for a, b, t in lines:
        segs.append({"start": a, "end": b, "text": t, "avg_logprob": -0.15, "no_speech_prob": 0.05,
                     "compression_ratio": 1.4})
        step = (b - a) / len(t.split())
        ws = _w(a, t, step)
        if a == 20.0:                                   # quãng nghỉ lớn giữa câu dài (vẫn trong câu)
            for w in ws[:10]:
                w["start"], w["end"] = w["start"] - 0.3, w["end"] - 0.3
            for w in ws[10:]:
                w["start"], w["end"] = w["start"] + 0.5, min(b, w["end"] + 0.5)
        words += ws
    return {"segments": segs, "words": words, "language": "vi", "duration": 45.0, "device": "cuda"}


def test_from_whisper_filters_and_keeps_sentences():
    text, segs = transcribe.from_whisper(_whisper())
    joined = " | ".join(t for *_, t in segs)
    assert "đăng ký kênh" not in joined                     # câu chào kết Whisper bịa (lời kêu gọi thật lọc ở AI)
    assert joined.count("giòn lâu từ trong ra ngoài") == 1                            # câu lặp vòng bỏ
    assert segs[0][0] == 1.9 and "Hôm nay" in segs[0][2]
    long_parts = [s for s in segs if 20.0 <= s[0] < 33.0]
    assert len(long_parts) == 2 and all(b - a <= 9.5 for a, b, _ in long_parts)      # câu 12s tách ở quãng nghỉ
    assert "rửa sạch" in text


# ---------------- phía Kaggle ----------------

def _video(path, dur=6):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=360x640:rate=25",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "25", "-c:a", "aac", "-shortest", path], check=True)


def test_kaggle_side_analyze(tmp_path, monkeypatch):
    import asr_local
    import kaggle_job
    import vocal_sep
    vid = str(tmp_path / "in.mp4")
    _video(vid)

    async def fake_fetch(ref, tmp, name):
        return vid
    seen = {}

    async def fake_sep(src, amb, timeout=0, windows=None, vocals_wav=""):
        seen["windows"], seen["vocals"] = windows, vocals_wav
        for p in (amb, vocals_wav):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=6", p],
                           check=True)
        return True

    def fake_asr(path, language="", prompt=""):
        seen["asr"] = (os.path.basename(path), language, prompt)
        return _whisper()
    monkeypatch.setattr(kaggle_job, "fetch", fake_fetch)
    monkeypatch.setattr(vocal_sep, "remove_voice", fake_sep)
    monkeypatch.setattr(asr_local, "transcribe", fake_asr)
    spec = {"kind": "analyze", "src": {"url": "u"}, "want_gray": True, "language": "vi", "prompt": "Cá kèo.",
            "keep_ambient": True, "token": "tok1"}
    res = asyncio.run(kaggle_job.analyze(spec, str(tmp_path / "w")))   # tự tạo thư mục tạm
    assert res["ok"] and res["token"] == "tok1" and res["sep"] is True
    assert seen["windows"] is None and seen["vocals"].endswith(".wav")          # tách cả bài, ghi giọng sạch
    assert seen["asr"] == ("vocals16k.wav", "vi", "Cá kèo.")                    # Whisper nghe GIỌNG ĐÃ TÁCH
    assert set(res["files"]) == {"thumbs.zip", "ambient.flac"}
    with zipfile.ZipFile(res["files"]["thumbs.zip"]) as z:
        assert len(z.namelist()) >= 10                                           # ảnh 2 khung/giây
    assert {"download", "sep", "asr", "scenes"} <= set(res["timings"])


def test_mounted_ambient_matches_token(tmp_path):
    import kaggle_job
    d = tmp_path / "notebooks" / "u" / "rmvideo-analyze"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"ok": True, "token": "abc"}))
    (d / "ambient.flac").write_bytes(b"fLaC")
    assert kaggle_job.mounted_ambient("abc", str(tmp_path)) == str(d / "ambient.flac")
    assert kaggle_job.mounted_ambient("khac", str(tmp_path)) is None              # của job khác: tự tách lại
    assert kaggle_job.mounted_ambient("", str(tmp_path)) is None


def test_render_reuses_mounted_ambient(tmp_path, monkeypatch):
    import kaggle_job
    import vocal_sep
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    vid = str(tmp_path / "in.mp4")
    _video(vid)
    amb = str(tmp_path / "amb.flac")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=150:duration=6", "-ac", "2",
                    amb], check=True)
    monkeypatch.setattr(kaggle_job, "mounted_ambient", lambda tok, root="/kaggle/input": amb if tok == "t1" else None)

    async def no_sep(*a, **k):
        raise AssertionError("không được tách giọng lần nữa")
    monkeypatch.setattr(vocal_sep, "remove_voice", no_sep)

    async def synth(items, tmp):
        p = os.path.join(tmp, "v0.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.2", p],
                       check=True)
        return {0: {"path": p, "words": None}}
    spec = {"mode": "cook", "out_dur": 6.0, "render_dur": 0, "stretch": 1.0, "allow_hd": True, "title": "", "subs": False,
            "pos_y": None, "bands": [], "sep": {"ok": True}, "speech": [[0.5, 2.0]], "amb_token": "t1",
            "segments": [{"i": 0, "start": 0.0, "slot": 6.0, "span": 2.0, "text": "Cho cá vô", "ost": "new"}]}
    res = asyncio.run(kaggle_job.produce(spec, vid, str(tmp_path), synth))
    assert res["amb"] is True and res["timings"]["sep"] == 0.0 and os.path.getsize(res["video"]) > 0


def test_analysis_notebook_bundle_and_gpu():
    spec = kaggle_worker._common({"kind": "analyze", "src": {"url": "u"}, "token": "x"})
    text = kaggle_worker.kernel_text(spec)
    assert kaggle_worker.needs_gpu(spec) and "faster-whisper" in kaggle_worker.pip_for(spec)
    assert "onnxruntime-gpu" in kaggle_worker.pip_for(spec) and "edge-tts" in kaggle_worker.pip_for(spec)
    import base64
    b64 = text.split('b64decode("', 1)[1].split('"', 1)[0]
    names = set(zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))).namelist())
    assert {"asr_local.py", "scenes.py", "textzone.py", "vocal_sep.py"} <= names


# ---------------- Botkeep <-> Kaggle (máy chủ Kaggle giả) ----------------

class _Fake:
    def __init__(self, token="tok"):
        self.calls, self.token, self.base = [], token, ""


def _server(fake):
    class H(BaseHTTPRequestHandler):
        def _send(self, data: bytes, ctype="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):                               # link tải file output
            name = self.path.rsplit("/", 1)[1]
            if name == "result.json":
                self._send(json.dumps({"ok": True, "token": fake.token, "whisper": _whisper(), "cuts": [3.0],
                                       "bands": [[0.1, 0.2]], "dur": 45.0, "sep": True,
                                       "timings": {"sep": 34, "asr": 55, "scenes": 59}}).encode())
            elif name == "thumbs.zip":
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    for k in range(1, 5):
                        z.writestr(f"{k:05d}.jpg", b"\xff\xd8jpeg")
                self._send(buf.getvalue(), "application/zip")
            else:
                self._send(b"fLaC-ambient", "audio/flac")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            method = self.path.rsplit("/", 1)[1]
            fake.calls.append((method, body))
            out = {"GetKernelSessionStatus": {"status": "COMPLETE"},
                   "GetAcceleratorQuotaStatistics": {"gpuQuota": {"timeUsed": "0s", "totalTimeAllowed": "108000s"}},
                   "IntrospectToken": {"username": "u"},
                   "ListKernelSessionOutput": {"files": [{"fileName": n, "url": f"{fake.base}/files/{n}"}
                                                         for n in ("result.json", "thumbs.zip", "ambient.flac")]},
                   "SaveKernel": {"ref": "/code/u/x", "versionNumber": 1}}.get(method, {})
            self._send(json.dumps(out).encode())

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    fake.base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def fake_kaggle(monkeypatch):
    fake = _Fake()
    srv = _server(fake)
    monkeypatch.setattr(kaggle_api, "API", f"{fake.base}/v1")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    kaggle_api._OWNER.clear()
    yield fake
    srv.shutdown()
    kaggle_api._OWNER.clear()


def test_worker_runs_analysis_notebook(fake_kaggle, tmp_path):
    spec = {"kind": "analyze", "src": {"url": "u"}, "token": "tok"}
    res = asyncio.run(kaggle_worker.analyze(spec, str(tmp_path)))
    saves = [b for m, b in fake_kaggle.calls if m == "SaveKernel"]
    assert saves[0]["slug"] == "u/rmvideo-analyze" and saves[0]["enableGpu"] is True
    assert os.path.exists(res["thumbs_zip"]) and open(res["ambient"], "rb").read() == b"fLaC-ambient"
    assert res["whisper"]["segments"]
    fake_kaggle.token = "cua-job-khac"                  # notebook bị việc khác ghi đè: không dùng nhầm
    with pytest.raises(kaggle_api.KaggleError, match="không phải của job này"):
        asyncio.run(kaggle_worker.analyze(spec, str(tmp_path)))


def test_render_mounts_analysis_output_only_when_reusing(fake_kaggle, tmp_path):
    spec = {"kind": "remake", "src": {"url": "u"}, "segments": [], "voice": {"engine": "edge"}, "amb_token": "tok"}
    res = kaggle_worker._run_sync(spec, str(tmp_path), lambda s: None)   # máy giả không trả video: chỉ xem lệnh gửi
    assert isinstance(res[0], kaggle_api.KaggleError)
    save = [b for m, b in fake_kaggle.calls if m == "SaveKernel"][-1]
    assert save["slug"] == "u/rmvideo-worker" and save["kernelDataSources"] == ["u/rmvideo-analyze"]
    spec.pop("amb_token")
    kaggle_worker._run_sync(spec, str(tmp_path), lambda s: None)
    assert [b for m, b in fake_kaggle.calls if m == "SaveKernel"][-1]["kernelDataSources"] == []


# ---------------- pipeline ----------------

@pytest.fixture
def analyze_on(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "TTS_PROVIDER", "vieneu")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "1")
    return fakes


def _url_job(tmp_path):
    from test_pipeline import _video as mk
    src = str(tmp_path / "up" / "src.mp4")
    mk(src)

    async def fake_download(url, tmpdir, max_duration=90):
        import shutil
        dst = os.path.join(tmpdir, "input.mp4")
        shutil.copy(src, dst)
        return dst, "Cá kèo chiên giòn", "", {"uploader": "Kênh", "webpage_url": url}
    return fake_download


def test_pipeline_uses_kaggle_analysis_then_render(analyze_on, monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(core_video, "download", _url_job(tmp_path))
    got = {"an": 0}

    async def fake_analyze(spec, tmp, on_step=None):
        got["an"] += 1
        got["an_spec"] = spec
        zp = os.path.join(tmp, "kaggle_thumbs.zip")
        with zipfile.ZipFile(zp, "w") as z:
            for k in range(1, 9):
                z.writestr(f"{k:05d}.jpg", b"\xff\xd8x")
        return {"ok": True, "token": spec["token"], "whisper": _whisper(), "cuts": [3.0], "bands": [],
                "dur": 8.0, "sep": True, "timings": {"sep": 34, "asr": 55, "scenes": 59}, "thumbs_zip": zp}

    async def local_asr(*a, **k):
        raise AssertionError("đã bóc lời trên Kaggle, không bóc lại trên Botkeep")

    async def fake_produce(spec, tmp, on_step=None):
        got["spec"] = spec
        out = os.path.join(tmp, "output.mp4")
        _video(out, 4)
        plan = [{"start": s["start"], "factor": 1.0, "end": s["start"] + 1} for s in spec["segments"]]
        return {"ok": True, "video": out, "plan": plan, "too_long": [], "srt": "", "amb": True, "notes": [],
                "timings": {"tts": 4, "render": 60}, "gpu": True}
    seen = {}

    async def fake_timed(title, desc, mode, segs, out_dur, images_for=None):
        seen["segs"] = segs
        out = [{"i": k, "start": a, "slot": max(1.0, b - a), "span": b - a, "text": f"Lời mới {k}", "orig": t}
               for k, (a, b, t) in enumerate(segs)]
        return {"segments": out, "script": "x", "fresh": 1.0, "title": "Cá kèo giòn", "caption": "c",
                "hashtags": "#a", "hook": "CÁ KÈO GIÒN KHÔNG NHẢY CHẢO"}
    monkeypatch.setattr(kaggle_worker, "analyze", fake_analyze)
    monkeypatch.setattr(pipeline, "transcribe_segments", local_asr)
    monkeypatch.setattr(kaggle_worker, "produce", fake_produce)
    monkeypatch.setattr(prompt_engine, "generate_timed_any", fake_timed)
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and got["an"] == 1
    assert got["an_spec"]["language"] == "vi" and got["an_spec"]["kind"] == "analyze"
    assert any("Hôm nay mình chỉ" in t for *_, t in seen["segs"])                # lời bóc trên Kaggle tới AI
    assert got["spec"]["amb_token"] == got["an_spec"]["token"]                     # phiên dựng dùng lại tiếng nền
    assert got["spec"]["title"] == "CÁ KÈO GIÒN KHÔNG NHẢY CHẢO" and job.hook     # chữ hook 3 giây đầu
    assert "Bóc lời + phân tích hình trên Kaggle" in job.error
    # bản duyệt / chạy lại cùng video: lấy kết quả phân tích từ cache, không mở phiên Kaggle mới
    job2 = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    (tmp_path / "w2").mkdir()
    asyncio.run(pipeline.process(job2, str(tmp_path / "w2")))
    assert got["an"] == 1
    # phân tích lấy từ cache vẫn mang mã tiếng nền: phiên dựng gắn đầu ra notebook phân tích, khớp mã thì khỏi tách lại
    # (bản 32 chạy lại video cũ: không có mã -> tách lại trên CPU Kaggle 418s)
    assert got["spec"]["amb_token"] == got["an_spec"]["token"]


def test_pipeline_falls_back_when_kaggle_analysis_fails(analyze_on, monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(core_video, "download", _url_job(tmp_path))

    async def broken(spec, tmp, on_step=None):
        raise kaggle_api.KaggleError("hết giờ GPU tuần này")
    monkeypatch.setattr(kaggle_worker, "analyze", broken)

    async def fake_produce(spec, tmp, on_step=None):
        out = os.path.join(tmp, "output.mp4")
        _video(out, 4)
        plan = [{"start": s["start"], "factor": 1.0, "end": s["start"] + 1} for s in spec["segments"]]
        return {"ok": True, "video": out, "plan": plan, "too_long": [], "srt": "", "amb": True, "notes": [],
                "timings": {}, "gpu": True}
    monkeypatch.setattr(kaggle_worker, "produce", fake_produce)
    job = Job(url="https://www.facebook.com/reel/1/", mode="cook", duration=0, voice="vieneu:Mỹ Duyên")
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and "Bóc lời trên Kaggle lỗi" in job.error


def test_job_budget_counts_analysis_session(monkeypatch):
    import jobs
    monkeypatch.setattr(config, "KAGGLE_USE", "1")
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    job = Job(url="u", mode="cook", duration=0, voice="")
    job.out_dur = 220
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "0")
    a = jobs.job_budget(job)
    monkeypatch.setattr(config, "KAGGLE_ANALYZE", "1")
    assert jobs.job_budget(job) - a == kaggle_worker.TIMEOUT_ANALYZE


# ---------------- hook + không kêu gọi đăng ký ----------------

def test_subscribe_cta_detection():
    for cta in ("Nhớ bấm theo dõi kênh để xem thêm món ngon nha.", "Đăng ký kênh ủng hộ mình nha", "Like và share giùm",
                "Follow mình để xem thêm nhiều video", "Hẹn gặp lại cả nhà trong video sau"):
        assert prompt_engine.has_sub_cta(cta), cta
    for ok in ("Theo dõi nhiệt độ dầu cho kỹ nha.", "Đảo đều tay cho cá không gãy.", "Nhớ lưu lại công thức này nha."):
        assert not prompt_engine.has_sub_cta(ok), ok
    assert prompt_engine.strip_sub_cta("Vậy là xong dĩa cá kèo giòn rụm. Nhớ đăng ký kênh nha!") == \
        "Vậy là xong dĩa cá kèo giòn rụm."
    assert prompt_engine.is_sub_cta_line("nhớ bấm theo dõi kênh của mình nha")
    assert not prompt_engine.is_sub_cta_line("Vậy là xong rồi, cá kèo giòn rụm, nhớ đăng ký kênh nha")
    assert prompt_engine.clean_hook("Cá kèo giòn không nhảy chảo!") == "CÁ KÈO GIÒN KHÔNG NHẢY CHẢO"
    assert prompt_engine.clean_hook("Nhớ đăng ký kênh") == ""


def _fake_ai(monkeypatch, replies):
    calls = []

    async def fake(prompt, images=None, schema=None, **kw):
        calls.append(prompt)
        return json.dumps(replies[min(len(calls), len(replies)) - 1])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "OST_AUTO", "0")
    monkeypatch.setattr(prompt_engine, "genai", object())
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    return calls


def test_hook_rule_greeting_rewrite_and_no_cta(monkeypatch):
    segs = [(1.9, 5.3, "Hôm nay mình chỉ mọi người cái mẹo làm cá kèo chiên giòn."),
            (10.5, 15.1, "Đầu tiên mình rửa sạch cá kèo cho mình nha."),
            (20.0, 24.0, "Như vậy là đã xong công thức cá kèo chiên giòn."),
            (25.0, 28.0, "Nhớ bấm theo dõi kênh của mình nha.")]          # câu gốc chỉ là lời kêu gọi: bỏ trước
    bad = {"segments": [{"i": 0, "text": "Xin chào cả nhà, hôm nay làm cá kèo chiên giòn."},
                        {"i": 1, "text": "Rửa thiệt sạch mấy con cá kèo trước nghen."},
                        {"i": 2, "text": "Xong dĩa cá kèo giòn rụm rồi nè. Nhớ đăng ký kênh nha!"}],
           "title": "t", "caption": "c", "hashtags": "#a", "hook": "cá kèo giòn rụm"}
    good = {"segments": [{"i": 0, "text": "Cá kèo chiên kiểu này giòn rụm mà hổng nhảy khỏi chảo nè!"},
                         {"i": 1, "text": "Rửa thiệt sạch mấy con cá kèo trước nghen."},
                         {"i": 2, "text": "Xong dĩa cá kèo giòn rụm rồi nè. Nhớ đăng ký kênh nha!"}],
            "title": "t", "caption": "c", "hashtags": "#a", "hook": "Cá kèo giòn không nhảy chảo"}
    calls = _fake_ai(monkeypatch, [bad, good])
    out = asyncio.run(prompt_engine.generate_timed("Cá kèo chiên giòn", "", "cook", segs, 30.0))
    assert "CÂU HOOK" in calls[0] and len(calls) >= 2
    assert "CÂU HOOK" in calls[1] and "không chào hỏi" in calls[1] and "đăng ký" in calls[1]   # lý do viết lại
    texts = [s["text"] for s in out["segments"]]
    assert len(texts) == 3 and texts[0].startswith("Cá kèo chiên kiểu này")
    assert not any(prompt_engine.has_sub_cta(t) for t in texts)                    # lọc lần cuối
    assert texts[2] == "Xong dĩa cá kèo giòn rụm rồi nè." and out["hook"] == "CÁ KÈO GIÒN KHÔNG NHẢY CHẢO"
    # lô sau (không phải đầu video): không có luật hook, không trả hook
    calls = _fake_ai(monkeypatch, [good])
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs[1:3], 30.0, extra="PHẦN TIẾP THEO", intro=False))
    assert "CÂU HOOK" not in calls[0] and out["hook"] == ""


def test_outro_card_has_no_follow_call():
    f = core_video._brand_filter(720, 1280, 30.0, None, "/tmp/outro.txt", None)
    assert "Theo dõi" not in f and "outro.txt" in f


def test_cut_sentence_without_punctuation_is_merged():
    segs = [(207.3, 211.1, "Kèo chiên giòn này mình sẽ ăn kèm mắm me nha các nhà Như vậy là đã"),
            (211.1, 213.6, "xong công thức cá kèo chiên giòn thơm ngon đơn giản tại nhà"),
            (214.0, 218.0, "chúc các nhà thành công nha")]
    texts = [t for *_, t in prompt_engine.merge_sentences(segs, 220.0)]
    assert any("Như vậy là đã xong công thức" in t for t in texts)


def test_chunk_splits_at_capitalized_sentence_start():
    units = [(207.3, 207.6, "mình"), (207.6, 207.9, "chỉ"), (207.9, 208.2, "cả"), (208.2, 208.5, "nhà"),
             (208.5, 208.9, "Kèo"), (208.9, 209.2, "chiên"), (209.2, 209.6, "giòn"), (209.6, 210.0, "này")]
    out = transcribe.chunk_units(units, min_dur=0.8)
    assert [t for *_, t in out] == ["mình chỉ cả nhà", "Kèo chiên giòn này"]


def test_hook_stored_in_cache_key_independent_jobs(tmp_path, monkeypatch):
    """Kết quả phân tích cache theo video + có dò chữ hay không (đổi cài đặt phụ đề thì phân tích lại)."""
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "c"))
    import pipeline
    a = cache.key("kaggle_an", "h", True, pipeline.ANALYZE_V)
    b = cache.key("kaggle_an", "h", False, pipeline.ANALYZE_V)
    assert a != b
