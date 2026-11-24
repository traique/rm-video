"""1.4.4 (log 24/11, job 1 truyện bé): video Kaggle 80 MB tải đứt ở 7,7 MB -> tải tiếp bằng Range; Lyria đứt 1006 giữa
chừng -> giữ phần đã nhận, nối phiên mới."""
import asyncio
import http.server
import threading

import pytest

import kaggle_api

DATA = bytes(range(256)) * 4000            # ~1 MB


class _Srv(http.server.BaseHTTPRequestHandler):
    plan: list = []                         # mỗi request lấy một hành vi: "cut" (gửi nửa rồi đóng), "ok", "403"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        act = self.plan.pop(0) if self.plan else "ok"
        rng = self.headers.get("Range") or ""
        self.seen.append((self.path, rng))
        if act == "403":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        body = DATA[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if act == "cut":
            self.wfile.write(body[:len(body) // 3])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    _Srv.plan, _Srv.seen = [], []
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def test_download_resumes_after_cut(srv, tmp_path):
    """Job 1 ngày 24/11: «Connection broken: IncompleteRead(7767496 bytes read, 72194991 more expected)» -> mất trắng
    1 giờ Kaggle. Đứt thì nối lại, chỉ tải phần còn thiếu."""
    _Srv.plan = ["cut", "cut", "ok"]
    prog = []
    dst = str(tmp_path / "v.mp4")
    n = kaggle_api.download(srv + "/v", dst, on_progress=lambda a, b: prog.append((a, b)))
    assert n == len(DATA) and open(dst, "rb").read() == DATA
    assert [r for _, r in _Srv.seen][0] == "" and all(r.startswith("bytes=") for _, r in _Srv.seen[1:])
    assert prog and prog[0][1] == len(DATA)
    assert not (tmp_path / "v.mp4.part").exists()


def test_download_refreshes_expired_link(srv, tmp_path):
    _Srv.plan = ["cut", "403", "ok"]
    dst = str(tmp_path / "v.mp4")
    n = kaggle_api.download(srv + "/old", dst, refresh=lambda: srv + "/new")
    assert n == len(DATA) and open(dst, "rb").read() == DATA
    assert [p for p, _ in _Srv.seen] == ["/old", "/old", "/new"]


def test_download_gives_up_without_progress(srv, tmp_path, monkeypatch):
    _Srv.plan = ["403"] * 20
    with pytest.raises(kaggle_api.KaggleError, match="tải output Kaggle đứt"):
        kaggle_api.download(srv + "/v", str(tmp_path / "v.mp4"))
    assert len(_Srv.seen) == kaggle_api.DL_STALLS
    assert not list(tmp_path.iterdir())


def test_download_size_limit(srv, tmp_path):
    with pytest.raises(kaggle_api.KaggleError, match="quá lớn"):
        kaggle_api.download(srv + "/v", str(tmp_path / "v.mp4"), max_bytes=1000)


class _CutLyria:
    """Lyria giả: mỗi phiên trả `per` giây nhạc rồi ném lỗi «1006 abnormal closure» (như log 24/11)."""

    def __init__(self, per=2.0, chunk=0.5, empty_after=99):
        self.sessions = 0
        outer = self

        class _S:
            async def set_weighted_prompts(self, prompts):
                pass

            async def set_music_generation_config(self, config):
                pass

            async def play(self):
                pass

            async def stop(self):
                pass

            async def receive(self):
                from types import SimpleNamespace
                if outer.sessions > empty_after:
                    raise ConnectionError("1006 None. abnormal closure [internal]")
                n = int(48000 * chunk)
                for k in range(int(per / chunk)):
                    data = (k % 7 * 1000).to_bytes(2, "little", signed=True) * 2 * n
                    yield SimpleNamespace(server_content=SimpleNamespace(audio_chunks=[SimpleNamespace(data=data)]),
                                          filtered_prompt=None)
                raise ConnectionError("1006 None. abnormal closure [internal]")

        class _C:
            async def __aenter__(self):
                outer.sessions += 1
                return _S()

            async def __aexit__(self, *a):
                return False
        from types import SimpleNamespace
        self.aio = SimpleNamespace(live=SimpleNamespace(music=SimpleNamespace(connect=lambda model: _C())))


MOOD = {"prompt": "calm piano", "bpm": 90, "density": 0.4, "brightness": 0.5, "vi": "dịu"}


def test_lyria_keeps_partial_and_reconnects():
    from rmvideo.media import lyria
    logs = []
    fake = _CutLyria(per=3.0)                        # 3 + (3 - 1,5 hoà) + (3 - 1,5) = 6 giây ≥ 5
    pcm = asyncio.run(lyria.stream(MOOD, 5.0, fake, logs.append, timeout=60, stall=5))
    assert len(pcm) == int(5.0 * lyria.RATE * lyria.CH * 2)
    assert fake.sessions >= 3 and any("nối phiên mới" in m for m in logs)


def test_lyria_short_track_still_used():
    """Phiên thứ 2 trở đi không trả gì: vẫn dùng phần đã có nếu đủ MIN_PART (nhạc nền tự lặp khi dựng)."""
    from rmvideo.media import lyria
    fake = _CutLyria(per=3.0, empty_after=1)
    pcm = asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=60, stall=5))
    assert len(pcm) == int(3.0 * lyria.RATE * lyria.CH * 2)
    fake = _CutLyria(per=1.0, empty_after=1)
    with pytest.raises(ConnectionError, match="1006"):
        asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=60, stall=5))


def test_lyria_xfade_joins_smoothly():
    from rmvideo.media import lyria
    a = (1000).to_bytes(2, "little", signed=True) * 2 * 48000          # 1 giây, mức 1000
    b = (-1000).to_bytes(2, "little", signed=True) * 2 * 48000
    out = lyria._xfade(a, b, 0.5)
    assert len(out) == len(a) + len(b) - int(0.5 * 48000) * 2 * 2
    mid = len(a) - int(0.5 * 48000) * 2 * 2 + int(0.25 * 48000) * 4     # giữa đoạn hoà: gần 0
    assert abs(int.from_bytes(out[mid:mid + 2], "little", signed=True)) < 50


def test_lyria_slow_stream_gives_up_fast(monkeypatch, tmp_path):
    """Log 24/11 job 2: phiên đầu 4 giây nhạc trong 51 giây (mạng Botkeep không theo kịp luồng 48 kHz). Không nối thêm
    3 phiên x 2 lần (~7 phút mỗi job): bỏ ngay, nghỉ Lyria SLOW_COOLDOWN; truyện bé thôi thử lại."""
    from rmvideo.kids import bgm
    from rmvideo.media import lyria
    monkeypatch.setattr(lyria, "_SLOW", {"until": 0.0, "why": ""})
    monkeypatch.setattr(lyria, "SLOW_MIN", 0.05)
    fake = _CutLyria(per=0.02, chunk=0.01)
    real_session = lyria._session

    async def slow_session(*a, **k):
        await asyncio.sleep(0.2)
        return await real_session(*a, **k)
    monkeypatch.setattr(lyria, "_session", slow_session)
    with pytest.raises(lyria.Slow, match="mạng không theo kịp"):
        asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=60, stall=5))
    assert fake.sessions == 1 and lyria.slow_left() > 3600
    with pytest.raises(lyria.Slow, match="tạm nghỉ"):
        asyncio.run(lyria.stream(MOOD, 6.0, fake, lambda m: None, timeout=60, stall=5))
    assert fake.sessions == 1                                       # đang nghỉ: không mở phiên

    from rmvideo.media import lyria_kaggle
    asked = []
    monkeypatch.setattr(lyria_kaggle, "start_later", lambda **k: asked.append(k) or "gửi notebook nhạc lên Kaggle")
    logs = []
    brief = {"plot": ""}
    out = asyncio.run(bgm.pick(brief, {"plots": []}, lib_dir=str(tmp_path / "lib"), client=fake, log=logs.append))
    assert out is None and fake.sessions == 1
    assert "bỏ qua Lyria" in logs[0] and "notebook nhạc" in logs[1] and len(asked) == 1     # nhờ Kaggle tạo kho


@pytest.fixture
def music_libs(tmp_path, monkeypatch):
    """Kho bé + bếp tạm, bài ngắn (3 giây), mỗi màu 1 bài."""
    import bgm
    from rmvideo.kids import bgm as kids_bgm
    from rmvideo.media import lyria_kaggle
    monkeypatch.setattr(kids_bgm, "LIB_DIR", str(tmp_path / "kids"))
    monkeypatch.setattr(kids_bgm, "TRACK_SECS", 3)
    monkeypatch.setattr(kids_bgm, "TRACKS_PER_MOOD", 1)
    monkeypatch.setattr(bgm, "LYRIA_DIR", str(tmp_path / "cook"))
    monkeypatch.setattr(bgm, "LYRIA_SECS", 3)
    monkeypatch.setattr(bgm, "LYRIA_PER_MOOD", 1)
    monkeypatch.setattr(lyria_kaggle, "STATE_FILE", str(tmp_path / "state.json"))
    return tmp_path


def test_music_missing_interleaves_moods(music_libs, monkeypatch):
    from rmvideo.kids import bgm as kids_bgm
    from rmvideo.media import lyria, lyria_kaggle
    monkeypatch.setattr(kids_bgm, "TRACKS_PER_MOOD", 2)
    os_dir = music_libs / "kids"
    os_dir.mkdir()
    (os_dir / "tender_1.m4a").write_bytes(b"x")
    lyria.add_track(str(os_dir), "tender_1.m4a", "tender", 3, "p")
    todo = lyria_kaggle.missing()
    kids = [t["file"] for t in todo if t["lib"] == "kids"]
    assert kids == ["playful_1.m4a", "curious_1.m4a", "tender_2.m4a", "playful_2.m4a", "curious_2.m4a"]
    assert todo[0]["lib"] == "kids" and {t["lib"] for t in todo} == {"kids", "cook"}
    assert all(t["remote"] == f"{t['lib']}__{t['file']}" for t in todo)


def test_music_kaggle_end_to_end(music_libs, monkeypatch):
    """Botkeep gửi notebook -> (giả) Kaggle chạy đúng mã notebook với Lyria giả -> tải m4a về kho, ghi library, đẩy
    dataset; key không nằm dạng chữ thường trong mã notebook."""
    import sys

    import config
    import kaggle_api
    from rmvideo.media import lyria, lyria_kaggle
    out = music_libs / "kaggle_out"
    out.mkdir()
    monkeypatch.setenv("RMV_MUSIC_OUT", str(out))
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test-key-123")
    monkeypatch.setattr(kaggle_api, "configured", lambda: True)
    monkeypatch.setitem(sys.modules, "config", sys.modules["config"])
    monkeypatch.delitem(sys.modules, "lyria", raising=False)
    sent = {}

    def push(slug, title, text, gpu=True, **k):
        sent.update(slug=slug, text=text, gpu=gpu)
        return {}

    def wait(slug, timeout, **k):
        g = {"__name__": "rmv_test"}
        exec(compile(sent["text"], "kernel.py", "exec"), g)
        g["asyncio"].run(g["main"](_CutLyria(per=10.0)))
        g["save"]()
        return "COMPLETE"

    def outputs(slug):
        return {"files": [{"fileName": p.name, "url": str(p)} for p in out.iterdir()]}

    def download(url, dst, *a, **k):
        import shutil
        shutil.copy(url, dst)
        return 1
    pushed = []
    monkeypatch.setattr(kaggle_api, "push", push)
    monkeypatch.setattr(kaggle_api, "wait", wait)
    monkeypatch.setattr(kaggle_api, "outputs", outputs)
    monkeypatch.setattr(kaggle_api, "download", download)
    monkeypatch.setattr(lyria, "push_dataset_sync", lambda lib, log=None: pushed.append(lib.dataset) or {})
    logs = []
    msg = lyria_kaggle.run_sync(logs.append)
    assert sent["slug"] == "rmvideo-music" and sent["gpu"] is False
    assert "AIza-test-key-123" not in sent["text"]
    assert "thêm 6/6 bài" in msg and "kho đã đủ" in msg, msg
    kids = lyria.load_library(str(music_libs / "kids"))
    assert sorted(t["file"] for t in kids["tracks"]) == ["curious_1.m4a", "playful_1.m4a", "tender_1.m4a"]
    assert all(t.get("via") == "kaggle" and 2.5 <= t["secs"] <= 3.0 for t in kids["tracks"])
    assert sorted(pushed) == ["rmvideo-cook-bgm", "rmvideo-kids-bgm"]
    assert lyria_kaggle.missing() == []


def test_music_kaggle_start_later_rules(music_libs, monkeypatch):
    import config
    import kaggle_api
    from rmvideo.media import lyria_kaggle
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(kaggle_api, "configured", lambda: True)
    ran = []

    def fake_run(log):
        ran.append(1)
        return "xong"
    monkeypatch.setattr(lyria_kaggle, "run_sync", fake_run)

    async def go():
        m1 = lyria_kaggle.start_later(why="chậm")
        await lyria_kaggle._RUN["task"]
        m2 = lyria_kaggle.start_later()                 # vừa chạy: tự động thì chờ RETRY_AFTER
        m3 = lyria_kaggle.start_later(force=True)       # lệnh console: chạy luôn
        await lyria_kaggle._RUN["task"]
        return m1, m2, m3
    m1, m2, m3 = asyncio.run(go())
    assert "Kaggle" in m1 and m2 == "" and m3 and len(ran) == 2
    assert lyria_kaggle.load_state()["status"] == "done"
