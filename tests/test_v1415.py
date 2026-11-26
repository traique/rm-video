"""1.4.15 (log 26/11, truyện #10): video Kaggle 80 MB tải 48 phút, đứt 53 lần (trần cũ 60 lần suýt hỏng job) -> bỏ trần
khi vẫn có tiến triển, tải song song nhiều đoạn; token Fanpage truyện bé thiếu quyền («(#200) Subject does not have
permission to post videos on this target») -> «Thử token» bắt được, YouTube / TikTok vẫn theo lịch khi Facebook dừng."""
import asyncio
import http.server
import threading
from datetime import datetime

import pytest
from aiohttp import web

import autopost
import config
import kaggle_api
import kids_auto
from rmvideo.app import job_factory

DATA = bytes(range(256)) * 20000          # ~5 MB
CUT = 70_000                              # > khúc đọc 64 KB: mỗi kết nối đứt vẫn được một khúc


class _Srv(http.server.BaseHTTPRequestHandler):
    """Máy chủ Google Storage giả: hiểu Range «bytes=a-b», cắt kết nối sau `cut` byte (mạng Botkeep)."""
    cut = 0                                 # >0: mỗi kết nối chỉ gửi chừng này byte rồi đóng
    ranges = True                           # False: bỏ qua Range, luôn trả nguyên file (200)
    expired: set = set()                    # đường dẫn trả 403 (link hết hạn)
    seen: list = []
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range") or ""
        with self.lock:
            self.seen.append((self.path, rng))
        if self.path in self.expired:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if rng and self.ranges:
            a, b = rng.split("=")[1].split("-")
            a, b = int(a), (int(b) if b else len(DATA) - 1)
            body = DATA[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{a + len(body) - 1}/{len(DATA)}")
        else:
            body = DATA
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.cut and len(body) > self.cut:
            self.wfile.write(body[:self.cut])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    monkeypatch.setattr(kaggle_api, "DL_PARALLEL_MIN", 1000)
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    _Srv.cut, _Srv.ranges, _Srv.expired, _Srv.seen = 0, True, set(), []
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def test_parallel_download_reassembles_parts_with_cuts(srv, tmp_path):
    """4 đoạn song song, mỗi kết nối chỉ gửi ~70 KB rồi đứt (như log 26/11): file ráp lại đúng từng byte."""
    _Srv.cut = CUT
    prog = []
    dst = str(tmp_path / "v.mp4")
    n = kaggle_api.download(srv + "/v", dst, on_progress=lambda a, b: prog.append((a, b)), parts=4)
    assert n == len(DATA) and open(dst, "rb").read() == DATA
    starts = {int(r.split("=")[1].split("-")[0]) for _, r in _Srv.seen if r and r != "bytes=0-0"}
    size = -(-len(DATA) // 4)
    assert {0, size, 2 * size, 3 * size} <= starts                   # 4 đoạn tải riêng
    assert len(_Srv.seen) > 30                                       # nhiều lần đứt, vẫn xong
    assert prog and prog[0][1] == len(DATA)
    assert not (tmp_path / "v.mp4.part").exists()


def test_parallel_falls_back_to_one_stream_without_range(srv, tmp_path):
    _Srv.ranges = False
    dst = str(tmp_path / "v.mp4")
    assert kaggle_api.download(srv + "/v", dst, parts=4) == len(DATA) and open(dst, "rb").read() == DATA


def test_parallel_small_file_uses_one_stream(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(kaggle_api, "DL_PARALLEL_MIN", 10 << 20)
    dst = str(tmp_path / "v.mp4")
    assert kaggle_api.download(srv + "/v", dst, parts=4) == len(DATA)
    assert [r for _, r in _Srv.seen] == ["bytes=0-0", ""]            # thăm dò rồi tải một luồng


def test_parallel_renews_expired_link_once(srv, tmp_path):
    _Srv.expired = {"/old"}
    asked = []

    def refresh():
        asked.append(1)
        return srv + "/new"
    dst = str(tmp_path / "v.mp4")
    assert kaggle_api.download(srv + "/old", dst, refresh=refresh, parts=4) == len(DATA)
    assert open(dst, "rb").read() == DATA and len(asked) == 1


def test_parallel_gives_up_without_progress(srv, tmp_path):
    with pytest.raises(kaggle_api.KaggleError, match="quá lớn"):
        kaggle_api.download(srv + "/v", str(tmp_path / "v.mp4"), max_bytes=1000, parts=4)
    _Srv.expired = {"/v"}
    with pytest.raises(kaggle_api.KaggleError):
        kaggle_api.download(srv + "/v", str(tmp_path / "v.mp4"), parts=4)     # thăm dò hỏng -> một luồng -> bỏ cuộc
    assert not list(tmp_path.iterdir())


def test_one_stream_has_no_try_cap_while_progressing(srv, tmp_path):
    """Trần cũ 60 lần: job 10 cần 54 lần. Giờ còn tải thêm được là tải tiếp (5 MB, mỗi lần được 64 KB: ~80 lần)."""
    _Srv.cut = CUT
    dst = str(tmp_path / "v.mp4")
    assert kaggle_api.download(srv + "/v", dst) == len(DATA) and open(dst, "rb").read() == DATA
    assert len(_Srv.seen) > 60


def test_parallel_time_limit(srv, tmp_path, monkeypatch):
    _Srv.cut = CUT
    monkeypatch.setattr(kaggle_api, "DL_DEADLINE", -1.0)
    with pytest.raises(kaggle_api.KaggleError, match="đứt"):
        kaggle_api.download(srv + "/v", str(tmp_path / "v.mp4"), parts=4)


# ---------------- Facebook: «Thử token» ----------------

def _graph(me_id: str, debug: dict | None):
    app = web.Application()

    async def page(req):
        if req.match_info["pid"] == "me":
            return web.json_response({"id": me_id, "name": "Chủ tài khoản" if me_id != "222" else "Kể Bé Nghe"})
        if req.match_info["pid"] == "debug_token":
            if debug is None:
                return web.json_response({"error": {"code": 100, "message": "not allowed"}}, status=400)
            return web.json_response({"data": debug})
        return web.json_response({"id": req.match_info["pid"], "name": "Kể Bé Nghe"})
    app.router.add_get("/v27.0/{pid}", page)
    return app


def _check(monkeypatch, me_id, debug):
    async def go():
        runner = web.AppRunner(_graph(me_id, debug))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        monkeypatch.setattr(autopost, "GRAPH", f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
        try:
            return await autopost.check_token(page="222", token="tok")
        finally:
            await runner.cleanup()
    return asyncio.run(go())


def test_check_token_catches_user_token_and_missing_scopes(monkeypatch):
    with pytest.raises(autopost.FBError, match="KHÔNG phải token của Page này"):
        _check(monkeypatch, "999", None)                                  # token người dùng: /me là người, không phải Page
    with pytest.raises(autopost.FBError, match="thiếu quyền pages_manage_posts"):
        _check(monkeypatch, "222", {"type": "PAGE", "profile_id": "222",
                                    "scopes": ["pages_show_list", "pages_read_engagement"]})
    with pytest.raises(autopost.FBError, match="cho Page này"):
        _check(monkeypatch, "222", {"scopes": list(autopost.POST_SCOPES),
                                    "granular_scopes": [{"scope": "pages_manage_posts", "target_ids": ["111"]}]})
    ok = {"scopes": list(autopost.POST_SCOPES), "granular_scopes": [{"scope": "pages_manage_posts", "target_ids": ["222"]}]}
    assert _check(monkeypatch, "222", ok) == "Kể Bé Nghe"
    assert _check(monkeypatch, "222", None) == "Kể Bé Nghe"              # Facebook không cho tự kiểm quyền: không chặn


# ---------------- Facebook dừng: YouTube / TikTok vẫn theo lịch ----------------

def _ts(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=autopost.VN).timestamp()


@pytest.fixture
def kids(monkeypatch, tmp_path):
    from state import queue
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "kids.json"))
    monkeypatch.setattr(kids_auto, "_POSTED", {})
    for k, v in (("KIDS_AUTO", "1"), ("KIDS_FB_PAGE_ID", "222"), ("KIDS_FB_PAGE_TOKEN", "tok-kids"),
                 ("KAGGLE_API_TOKEN", "KGAT_x"), ("MAX_QUEUE", 1000)):
        monkeypatch.setattr(config, k, v)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0" * 2000)
    job = job_factory.kids(brief={}, source="auto")
    job.status, job.out_dur = "done", 85.9
    job.post = "📌 Gấu Nâu và món quà\n\n▶ Facebook:\nMô tả bé\n\n▶ YouTube Shorts:\ny"
    job.parts = [{"label": "Video", "path": str(vid), "token": "t"}]
    monkeypatch.setitem(queue.jobs, job.id, job)
    monkeypatch.setattr(queue, "pending", 0)
    others = []
    from rmvideo.app import tiktok_kit, youtube
    monkeypatch.setattr(youtube, "after_facebook", lambda ch, j, path, t, d, when: others.append(("yt", ch, j.id, when)) or True)
    monkeypatch.setattr(tiktok_kit, "after_facebook", lambda ch, j, path, d, when: others.append(("tk", ch, j.id, when)) or False)
    sent = []

    async def notify(msg, *a, **k):
        sent.append(msg)
    import state
    monkeypatch.setattr(state, "notify_admin", notify)
    return job, others, sent


def test_kids_facebook_permission_error_still_queues_youtube(kids, monkeypatch):
    job, others, sent = kids

    async def no_perm(*a, **k):
        raise autopost.FBError("Facebook lỗi 200: (#200) Subject does not have permission to post videos on this target", 200)
    monkeypatch.setattr(autopost, "publish", no_perm)
    st = kids_auto.load_state()
    st["pending"] = {"job": job.id, "slot_ts": _ts(26, "19:00"), "slot": "19:00"}
    kids_auto.save_state(st)
    with pytest.raises(autopost.FBError):
        asyncio.run(kids_auto.tick(_ts(26, "11:57")))
    st = kids_auto.load_state()
    assert st["paused"] and st["pending"]["job"] == job.id               # Facebook dừng, truyện vẫn giữ để đăng lại
    assert ("yt", "kids", job.id, _ts(26, "19:00")) in others
    assert "YouTube vẫn theo lịch (19:00 26/11)" in sent[-1] and "Thử token" in sent[-1]


def test_kids_paused_before_update_queues_youtube_on_next_tick(kids):
    """Bản cũ đã dừng Fanpage mà chưa xếp YouTube (truyện #10): cài bản mới, lượt kế xếp YouTube đúng khung."""
    job, others, _sent = kids
    st = kids_auto.load_state()
    st["paused"] = "Facebook lỗi 200"
    st["pending"] = {"job": job.id, "slot_ts": _ts(26, "19:00"), "slot": "19:00", "tries": 1}
    kids_auto.save_state(st)
    assert asyncio.run(kids_auto.tick(_ts(26, "15:00"))).startswith("đang dừng")
    assert others == [("yt", "kids", job.id, _ts(26, "19:00")), ("tk", "kids", job.id, _ts(26, "19:00"))]
    others.clear()
    asyncio.run(kids_auto.tick(_ts(26, "20:00")))                       # khung đã qua: đăng ngay (when None)
    assert ("yt", "kids", job.id, None) in others


def test_kids_paused_unfinished_job_queues_nothing(kids):
    job, others, _sent = kids
    job.status = "processing"
    st = kids_auto.load_state()
    st["paused"] = "x"
    st["pending"] = {"job": job.id, "slot_ts": _ts(26, "19:00"), "slot": "19:00"}
    kids_auto.save_state(st)
    asyncio.run(kids_auto.tick(_ts(26, "15:00")))
    assert others == []


def test_other_platforms_note():
    from types import SimpleNamespace
    calls = []
    from rmvideo.app import tiktok_kit, youtube
    old = youtube.after_facebook, tiktok_kit.after_facebook
    try:
        youtube.after_facebook = lambda *a: calls.append("yt") or False
        tiktok_kit.after_facebook = lambda *a: calls.append("tk") or False
        assert autopost.other_platforms("cook", SimpleNamespace(id=1), "v", "t", "d", None) == ""   # đã xếp rồi: không báo lại
    finally:
        youtube.after_facebook, tiktok_kit.after_facebook = old
    assert calls == ["yt", "tk"]


def test_all_providers_timing_out_counts_as_temporary(monkeypatch):
    """Góp ý Codex PR #27: asyncio.wait_for hết giờ ném TimeoutError có str() rỗng -> trước đây không coi là lỗi tạm,
    video một mạch vẫn dựng kịch bản mẫu thay vì dừng job để gửi lại."""
    import prompt_engine
    from rmvideo.services import llm as llm_mod

    async def slow(prompt):
        raise TimeoutError()
    monkeypatch.setattr(prompt_engine, "_chain", lambda call: [llm_mod.Provider("Gemini", slow)])
    logged = []
    monkeypatch.setattr(prompt_engine.logging, "error", lambda *a, **k: logged.append(a))
    pack = asyncio.run(prompt_engine.generate("Kho quẹt", "", "cook", "lời gốc", 60))
    assert pack["fallback"] and pack["temporary"] and pack["why"] == "TimeoutError"
    assert logged == []                                             # quá giờ: một dòng cảnh báo, không traceback
    assert prompt_engine.err_text(RuntimeError("503 x")) == "503 x"
