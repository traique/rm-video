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
