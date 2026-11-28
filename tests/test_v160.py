"""1.6.0: 📼 video tổng hợp truyện bé hằng tuần cho YouTube — giữ truyện đã đăng (liên kết cứng), ghép thẳng các file cùng
khuôn, mục lục theo tên truyện, ảnh bìa ghép, xếp lịch YouTube (vẫn «dành cho trẻ em»), dọn file khi YouTube đã nhận."""
import asyncio
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from aiohttp import web

import config
from rmvideo.app import kids_weekly as kw
from rmvideo.app import settings, youtube

T = 1795914000.0                    # Chủ nhật 29/11/2026 08:00 giờ Việt Nam (tuần ISO 2026-W48)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


@pytest.fixture
def wk(monkeypatch, tmp_path):
    """Kho + trạng thái riêng, YouTube kênh bé «đã kết nối» (hàng chờ riêng), thông báo admin ghi lại."""
    import state
    monkeypatch.setattr(kw, "STATE_FILE", str(tmp_path / "kids_weekly.json"))
    monkeypatch.setattr(kw, "LIB_DIR", str(tmp_path / "lib"))
    monkeypatch.setattr(youtube, "STATE_FILE", str(tmp_path / "youtube.json"))
    monkeypatch.setattr(youtube, "missing", lambda ch: "")
    monkeypatch.setattr(config, "KIDS_WEEKLY", "CN 08:00")
    monkeypatch.setattr(config, "KIDS_WEEKLY_MAX", "14")
    sent: list = []

    async def notify(msg, *a, **k):
        sent.append(msg)
    monkeypatch.setattr(state, "notify_admin", notify)

    async def not_verified():
        return False
    monkeypatch.setattr(kw, "_long_allowed", not_verified)                   # không gọi Google thật
    return sent


def _clip(path, color="red", size="108x192", dur=2.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30", "-f", "lavfi",
                    "-i", "sine=f=440:r=48000", "-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
                    "-level", "4.1", "-pix_fmt", "yuv420p", "-g", "60", "-c:a", "aac", "-b:a", "64k", "-ar", "48000",
                    "-movflags", "+faststart", str(path)], check=True)
    return str(path)


def _job(jid, words=("basket", "banana")):
    return SimpleNamespace(id=jid, hook=f"Truyện {jid}", brief={"words": [{"en": w, "vi": w} for w in words]}, post="")


# ---------------- lịch + cài đặt ----------------

def test_weekly_setting_and_slots():
    assert settings._weekly("cn 8:00") == "CN 08:00" and settings._weekly("T7 19:30") == "T7 19:30"
    assert settings._weekly("off") == "0" and settings._weekly("tắt") == "0"
    for bad in ("T8 10:00", "CN 25:00", "chủ nhật"):
        with pytest.raises(ValueError):
            settings._weekly(bad)
    assert settings._int_range("KIDS_WEEKLY_MAX", 3, 30)(" 10 ") == "10"
    with pytest.raises(ValueError):
        settings._int_range("KIDS_WEEKLY_MAX", 3, 30)("2")
    assert settings.BY_KEY["KIDS_WEEKLY"].default == "CN 08:00" and settings.BY_KEY["KIDS_WEEKLY_MAX"].default == "14"
    sp = kw.spec("CN 08:00")
    assert sp == (6, 8, 0) and kw.spec("0") is None and kw.spec("T2 07:05") == (0, 7, 5)
    sat = T - 22 * 3600                                                     # thứ Bảy 10:00
    assert kw.next_slot(sat, sp) == T and kw.due_slot(sat, sp) is None
    assert kw.due_slot(T - kw.BUILD_LEAD + 60, sp) == T                     # từ 02:00 Chủ nhật: lúc ghép
    assert kw.due_slot(T + kw.LATE - 60, sp) == T and kw.due_slot(T + kw.LATE + 60, sp) is None
    assert kw.next_slot(T + kw.LATE + 60, sp) == T + 7 * 86400
    assert kw.week_key(T) == "2026-W48" and kw._mmss(0) == "0:00" and kw._mmss(125.4) == "2:05"
    assert kw._mmss(3725) == "1:02:05"


# ---------------- kho truyện ----------------

def test_add_links_clip_once_and_respects_switches(wk, monkeypatch, tmp_path):
    v = tmp_path / "out.mp4"
    v.write_bytes(b"video truyen be")
    W = T - 86400 - 12 * 3600                                               # bài Facebook hẹn tối thứ Bảy
    assert kw.add(_job(21), str(v), when=W) is True
    c = kw.load_state()["clips"][0]
    assert c["job"] == 21 and c["title"] == "Truyện 21" and c["words"] == ["basket", "banana"]
    assert os.path.samefile(c["path"], v)                                   # liên kết cứng: không tốn thêm chỗ
    assert c["publish"] == round(youtube.slot_for("kids", W) or W, 2)       # giờ Shorts lên YouTube
    assert kw.add(_job(21), str(v), when=W) is False                        # một lần mỗi truyện
    monkeypatch.setattr(config, "KIDS_WEEKLY", "0")
    assert kw.add(_job(22), str(v)) is False and kw.enabled().startswith("tắt")
    monkeypatch.setattr(config, "KIDS_WEEKLY", "CN 08:00")
    monkeypatch.setattr(youtube, "missing", lambda ch: "chưa kết nối")
    assert kw.add(_job(22), str(v)) is False and "kết nối YouTube" in kw.enabled()
    assert [x["job"] for x in kw.load_state()["clips"]] == [21]


def test_other_platforms_keeps_kids_story(wk, monkeypatch, tmp_path):
    import autopost
    from rmvideo.app import tiktok_kit
    monkeypatch.setattr(youtube, "after_facebook", lambda *a, **k: False)
    monkeypatch.setattr(tiktok_kit, "after_facebook", lambda *a, **k: False)
    v = tmp_path / "k.mp4"
    v.write_bytes(b"x")
    autopost.other_platforms("cook", _job(5), str(v), "t", "d", None)       # kênh bếp: không giữ
    autopost.other_platforms("kids", _job(6), str(v), "t", "d", None)
    assert [c["job"] for c in kw.load_state()["clips"]] == [6]


# ---------------- ghép ----------------

@needs_ffmpeg
def test_weekly_build_joins_same_format_clips(wk, monkeypatch, tmp_path):
    sent = wk
    for k, color in enumerate(("red", "blue", "green", "yellow")):
        kw.add(_job(40 + k, words=("sun", f"w{k}")), _clip(tmp_path / f"c{k}.mp4", color), publish=T - 86400 * (4 - k))
    kw.add(_job(50), _clip(tmp_path / "odd.mp4", size="120x192"), publish=T - 3600)    # khác khuôn: không nối thẳng được
    kw.add(_job(51), _clip(tmp_path / "late.mp4", "white"), publish=T + 3600)         # Shorts lên sau giờ tổng hợp: tuần sau
    assert asyncio.run(kw.tick(now=T - kw.BUILD_LEAD - 60)) == ""                     # chưa tới lúc ghép
    msg = asyncio.run(kw.tick(now=T - 3 * 3600))
    assert "đã ghép 4 truyện" in msg and "1 truyện khác khuôn để lại" in msg
    st = kw.load_state()
    m = st["made"][0]
    assert m["k"] == 1 and m["jobs"] == [40, 41, 42, 43] and m["n"] == 4 and st["weeks"]["2026-W48"]["status"] == "made"
    assert sorted(c["job"] for c in st["clips"]) == [50, 51]
    assert not any(os.path.exists(os.path.join(kw.LIB_DIR, f"{j}.mp4")) for j in (40, 41, 42, 43))   # đã nằm trong video ghép
    got = asyncio.run(kw.probe(m["path"]))
    assert got is not None and abs(got["dur"] - 8.0) < 0.5
    it = youtube.load_state()["items"][0]
    assert it["key"] == "kids:tuyentap-1" and it["long"] is True and it["when"] == T and it["thumb"] == m["thumb"]
    assert it["label"] == "video tổng hợp «Tuyển tập 1»" and "#Shorts" not in it["title"]
    assert it["title"].startswith("Tuyển tập 1: 4 truyện hoạt hình cho bé")
    d = it["desc"]
    assert "📖 Mục lục\n0:00 Truyện 40\n0:02 Truyện 41\n0:04 Truyện 42\n0:06 Truyện 43" in d
    assert "🔤 Từ tiếng Anh trong video: sun, w0, w1, w2, w3" in d and "#truyenchobe" in d
    monkeypatch.setattr(config, "KIDS_YT_MADE_FOR_KIDS", "1")                          # khai theo cài đặt (1.6.2)
    assert youtube.video_body("kids", it["title"], d, T, T - 3 * 3600)["status"]["selfDeclaredMadeForKids"] is True
    monkeypatch.setattr(config, "KIDS_YT_MADE_FOR_KIDS", "0")
    assert youtube.video_body("kids", it["title"], d, T, T - 3 * 3600)["status"]["selfDeclaredMadeForKids"] is False
    size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", m["thumb"]],
                          capture_output=True, text=True).stdout.strip()
    assert size == "1280,720"
    assert any("📼 Truyện bé: đã ghép 4 truyện" in s and "#40, #41, #42, #43" in s for s in sent)
    assert asyncio.run(kw.tick(now=T - 2 * 3600)) == ""                     # một lần mỗi tuần
    assert "1 truyện chờ ghép" not in kw.status_lines(T)[0] and "tuyển tập 1 (4 truyện, 0:08)" in kw.status_lines(T)[0]
    # YouTube đã nhận: xoá video ghép + ảnh bìa
    youtube._patch("kids:tuyentap-1", status="done")
    asyncio.run(kw.tick(now=T + 86400))
    assert not os.path.exists(m["path"]) and not os.path.exists(m["thumb"]) and kw.load_state()["made"][0]["gone"]


@needs_ffmpeg
def test_weekly_skips_thin_week_and_manual_build(wk, tmp_path):
    for k in range(2):
        kw.add(_job(60 + k), _clip(tmp_path / f"s{k}.mp4"), publish=T - 86400)
    msg = asyncio.run(kw.tick(now=T - 3600))
    assert "mới có 2 truyện" in msg and kw.load_state()["weeks"]["2026-W48"]["status"] == "skipped"
    assert asyncio.run(kw.tick(now=T - 1800)) == ""                         # không thử lại mỗi 2 phút
    assert len(kw.load_state()["clips"]) == 2                              # dồn sang tuần sau
    kw.add(_job(62), _clip(tmp_path / "s2.mp4", "blue"), publish=T - 600)
    out = asyncio.run(kw.build_now(now=T))                                  # lệnh kids_weekly_now: đăng ngay
    assert "đã ghép 3 truyện" in out and "ngay khi tải lên xong" in out
    it = youtube.load_state()["items"][0]
    assert it["when"] is None and kw.load_state()["clips"] == []


@needs_ffmpeg
def test_weekly_concat_check_failure_keeps_clips(wk, monkeypatch, tmp_path):
    sent = wk
    for k in range(3):
        kw.add(_job(70 + k), _clip(tmp_path / f"f{k}.mp4"), publish=T - 86400)
    real = kw.probe

    async def short_output(path):
        got = await real(path)
        return dict(got, dur=1.0) if got and "tuyentap" in path else got
    monkeypatch.setattr(kw, "probe", short_output)
    msg = asyncio.run(kw.tick(now=T - 3600))
    assert msg.startswith("ghép lỗi") and kw.load_state()["weeks"]["2026-W48"]["status"] == "failed"
    assert len(kw.load_state()["clips"]) == 3 and youtube.load_state()["items"] == []
    assert not os.path.exists(os.path.join(kw.LIB_DIR, "tuyentap-1.mp4")) and any("ghép lỗi" in s for s in sent)


def test_unexpected_error_marks_week_once(wk, monkeypatch):
    sent = wk

    async def boom(*a, **k):
        raise RuntimeError("đĩa đầy")
    monkeypatch.setattr(kw, "_build", boom)
    assert "đĩa đầy" in asyncio.run(kw.tick(now=T - 3600))
    assert kw.load_state()["weeks"]["2026-W48"]["status"] == "failed" and any("đĩa đầy" in s for s in sent)
    assert asyncio.run(kw.tick(now=T - 1800)) == ""


def test_pick_groups_by_format_oldest_first():
    def info(sig):
        return {"dur": 80.0, "sig": (sig,)}
    clips = [({"job": j, "publish": p}, info(s)) for j, p, s in ((1, 10, "a"), (2, 20, "b"), (3, 30, "a"), (4, 40, "b"),
                                                               (5, 50, "b"))]
    got, odd = kw.pick(clips, 2)
    assert [c["job"] for c, _ in got] == [2, 4] and odd == 2                 # nhóm đông nhất, cũ trước
    got, _ = kw.pick(clips[:4], 5)
    assert [c["job"] for c, _ in got] == [2, 4]                             # hoà: nhóm có truyện mới nhất
    assert kw.pick([], 5) == ([], 0)


# ---------------- lần đầu: truyện đã đăng gần đây ----------------

def test_backfill_recent_posts_once(wk, monkeypatch, tmp_path):
    import kids_auto
    from state import queue
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "kids_auto.json"))
    v = tmp_path / "old.mp4"
    v.write_bytes(b"x")
    job = SimpleNamespace(id=31, status="done", parts=[{"label": "Video", "path": str(v)}], output="", hook="Mèo Cam",
                          brief={}, post="")
    monkeypatch.setitem(queue.jobs, 31, job)
    monkeypatch.setitem(queue.jobs, 32, SimpleNamespace(id=32, status="error", parts=[], output=""))
    st = kids_auto.load_state()
    st["posts"] = [{"job": 31, "time": T - 2 * 86400}, {"job": 32, "time": T - 86400}, {"job": 30, "time": T - 30 * 86400}]
    kids_auto.save_state(st)
    note = asyncio.run(kw.tick(now=T - 20 * 3600))
    assert "đưa 1 truyện" in note and [c["job"] for c in kw.load_state()["clips"]] == [31]
    assert kw.load_state()["clips"][0]["publish"] <= T - 2 * 86400 + 86400 and kw.load_state()["backfilled"]
    assert asyncio.run(kw.tick(now=T - 19 * 3600)) == ""


def test_cleanup_drops_stale_clips_and_orphan_compilations(wk, tmp_path):
    os.makedirs(kw.LIB_DIR, exist_ok=True)
    old, gone = os.path.join(kw.LIB_DIR, "1.mp4"), os.path.join(kw.LIB_DIR, "tuyentap-1.mp4")
    for p in (old, gone):
        open(p, "wb").write(b"x")
    st = kw.load_state()
    st["clips"] = [{"job": 1, "path": old, "added": T - kw.CLIP_MAX_AGE - 10, "publish": T},
                   {"job": 2, "path": os.path.join(kw.LIB_DIR, "missing.mp4"), "added": T, "publish": T}]
    st["made"] = [{"k": 1, "key": "kids:tuyentap-1", "path": gone, "thumb": "", "at": T - kw.MADE_KEEP - 10}]
    kw.cleanup(st, T)
    assert st["clips"] == [] and not os.path.exists(old) and not os.path.exists(gone) and st["made"][0]["gone"]


# ---------------- YouTube: video dài, nhãn, ảnh bìa riêng ----------------

def _thumb_google(calls, thumb_status):
    from test_youtube import _fake_google
    app = _fake_google(calls, {})

    async def thumb(req):
        calls.append(("thumb", req.query["videoId"], req.headers["Content-Type"], len(await req.read())))
        if thumb_status != 200:
            return web.json_response({"error": {"code": 403, "message": "The authenticated user doesn't have permissions "
                                                "to upload and set custom video thumbnails.",
                                                "errors": [{"reason": "forbidden"}]}}, status=thumb_status)
        return web.json_response({"items": [{"default": {"url": "x"}}]})
    app.router.add_post("/up/thumb", thumb)
    return app


async def _run_with(monkeypatch, app, coro_fn):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    for name, path in (("TOKEN_URL", "/token"), ("API", "/yt"), ("UPLOAD", "/up/videos"), ("THUMB_URL", "/up/thumb")):
        monkeypatch.setattr(youtube, name, base + path)
    try:
        return await coro_fn()
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("thumb_status", [200, 403])
def test_long_item_uploads_with_label_link_and_thumbnail(wk, monkeypatch, tmp_path, thumb_status):
    sent = wk
    monkeypatch.setattr(youtube, "_ACCESS", {})
    monkeypatch.setattr(youtube, "refresh_token", lambda ch: "RT-kids")
    monkeypatch.setattr(youtube, "client", lambda: ("id", "sec"))
    v, j = tmp_path / "tt.mp4", tmp_path / "tt.jpg"
    v.write_bytes(b"v" * 2000)
    j.write_bytes(b"\xff\xd8jpg")
    now = T - 3 * 3600
    assert youtube.enqueue("kids", 0, str(v), "Tuyển tập 1: 4 truyện", "mô tả", T, now=now, key="kids:tuyentap-1",
                           label="video tổng hợp «Tuyển tập 1»", thumb=str(j), long=True)
    assert not youtube.enqueue("kids", 0, str(v), "x", "y", T, key="kids:tuyentap-1")      # một lần mỗi khoá
    calls: list = []
    msg = asyncio.run(_run_with(monkeypatch, _thumb_google(calls, thumb_status), lambda: youtube.tick(now=now)))
    it = youtube.load_state()["items"][0]
    assert it["status"] == "done" and it["yt_id"] == "VID1" and "kids" not in youtube.load_state()["paused"]
    assert ("thumb", "VID1", "image/jpeg", 5) in calls                         # ảnh bìa gửi sau khi video lên
    note = sent[-1]
    assert note.startswith("▶️ Đã hẹn đăng video tổng hợp «Tuyển tập 1» lên YouTube (kênh truyện bé)")
    assert "https://youtu.be/VID1" in note and "shorts" not in note
    assert ("Chưa đặt được ảnh bìa riêng" in note) == (thumb_status != 200) and "(cần kiểm tra)" not in msg
    assert "video tổng hợp «Tuyển tập 1»" in youtube._name(it)


# ---------------- trạng thái + vòng truyện bé ----------------

def test_status_and_kids_tick_survive_weekly_errors(wk, monkeypatch):
    import kids_auto
    assert kw.status_lines(T - 22 * 3600)[0] == "📼 Video tổng hợp tuần: 0 truyện chờ ghép, lần tới 08:00 29/11"
    assert any(x.startswith("📼 Video tổng hợp tuần") for x in kids_auto.status_text(T).splitlines())
    monkeypatch.setattr(config, "KIDS_WEEKLY", "0")
    assert kw.status_lines(T) == ["📼 Video tổng hợp tuần: tắt (KIDS_WEEKLY = 0)"]

    async def boom(now=None):
        raise RuntimeError("hỏng")
    monkeypatch.setattr(kw, "tick", boom)
    out = asyncio.run(kids_auto.tick(now=T))                                 # vòng truyện bé vẫn chạy
    assert isinstance(out, str)

    async def note(now=None):
        return "đưa 2 truyện đã đăng gần đây vào kho video tổng hợp tuần"
    monkeypatch.setattr(kw, "tick", note)
    assert "tổng hợp tuần: đưa 2 truyện" in asyncio.run(kids_auto.tick(now=T))


def test_state_file_roundtrip_and_bad_file(wk):
    st = kw.load_state()
    assert st == {"clips": [], "made": [], "weeks": {}, "backfilled": False}
    open(kw.STATE_FILE, "w").write("[1, 2]")
    assert kw.load_state()["clips"] == [] and os.path.exists(kw.STATE_FILE + ".bad")
    st["weeks"] = {f"2026-W{k:02d}": {} for k in range(1, 70)}
    kw.save_state(st)
    assert len(json.load(open(kw.STATE_FILE))["weeks"]) == kw.KEEP_MADE


# ---------------- kênh chưa xác minh: tối đa 15 phút ----------------

@needs_ffmpeg
@pytest.mark.parametrize("verified", [False, True])
def test_unverified_channel_capped_at_15_minutes(wk, monkeypatch, tmp_path, verified):
    sent = wk
    monkeypatch.setattr(kw, "SHORT_MAX", 5.0)                               # 4 truyện x 2s = 8s > «15 phút» giả

    async def allowed():
        return verified
    monkeypatch.setattr(kw, "_long_allowed", allowed)
    for k in range(4):
        kw.add(_job(80 + k), _clip(tmp_path / f"v{k}.mp4"), publish=T - 86400 * (4 - k))
    msg = asyncio.run(kw.tick(now=T - 3600))
    st = kw.load_state()
    if verified:
        assert "đã ghép 4 truyện" in msg and st["clips"] == []
    else:
        assert "đã ghép 3 truyện" in msg and "1 truyện dời sang tuần sau vì kênh chưa xác minh" in msg
        assert [c["job"] for c in st["clips"]] == [83] and any("youtube.com/verify" in x for x in sent)


def test_compilation_number_survives_trimmed_history(wk, monkeypatch):
    st = kw.load_state()
    st["made"] = [{"k": k, "key": f"kids:tuyentap-{k}", "path": "", "gone": True, "at": T} for k in range(1, 75)]
    kw.save_state(st)
    st = kw.load_state()
    assert len(st["made"]) == kw.KEEP_MADE and max(m["k"] for m in st["made"]) == 74
    seen = {}

    async def fake_build(now, when, wkey, manual=False):
        seen["k"] = max((int(m.get("k") or 0) for m in kw.load_state()["made"]), default=0) + 1
        return "ok"
    monkeypatch.setattr(kw, "_build", fake_build)
    asyncio.run(kw.tick(now=T - 3600))
    assert seen["k"] == 75                                                   # không lặp «Tuyển tập 61»


def test_long_uploads_status(monkeypatch, wk):
    monkeypatch.setattr(youtube, "_ACCESS", {})
    monkeypatch.setattr(youtube, "refresh_token", lambda ch: "RT-kids")
    monkeypatch.setattr(youtube, "client", lambda: ("id", "sec"))
    app = web.Application()

    async def token(req):
        return web.json_response({"access_token": "AT", "expires_in": 3599})

    async def channels(req):
        assert req.query["part"] == "status" and req.query["mine"] == "true"
        return web.json_response({"items": [{"status": {"longUploadsStatus": "allowed"}}]})
    app.router.add_post("/token", token)
    app.router.add_get("/yt/channels", channels)

    async def ask():
        async with youtube._session() as s:
            return await youtube.long_uploads("kids", s)
    assert asyncio.run(_run_with(monkeypatch, app, ask)) == "allowed"


def test_probe_without_data_hash(monkeypatch):
    seen = []

    async def ffprobe(*args, tool="ffmpeg"):
        seen.append(args)
        if "-show_data_hash" in args:
            raise RuntimeError("Unrecognized option 'show_data_hash'")
        return json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}],
                           "format": {"duration": "81.5"}})
    monkeypatch.setattr(kw.ff, "_run_ffmpeg", ffprobe)
    got = asyncio.run(kw.probe("x.mp4"))
    assert got["dur"] == 81.5 and got["sig"][0] == "h264" and len(seen) == 2
