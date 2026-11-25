"""1.4.11 (25/11): không «dồn» phiên lên Kaggle — đóng hết phiên công việc còn xếp hàng / chạy trước khi gửi phiên GPU mới,
nghỉ gửi khi Kaggle lâu không cấp máy, phát hiện Kaggle không cấp GPU (tài khoản chưa xác minh số điện thoại)."""
import json
import os
import time

import pytest

import config
import kaggle_api
import kaggle_worker


def test_header_stops_when_no_gpu(tmp_path):
    """Tài khoản chưa xác minh số điện thoại: phiên chạy ngay nhưng bằng CPU (đo thật traique002 25/11). Notebook ghi kết
    quả lỗi rõ ràng cho mọi việc trong lô rồi thoát, không chạy hàng chục phút trên CPU."""
    text = kaggle_worker.kernel_text({"kind": "batch", "jobs": [{}, {}], "need_gpu": True})
    text = text.replace("/kaggle/working", str(tmp_path))
    with pytest.raises(SystemExit):
        exec(compile(text, "kernel.py", "exec"), {"__name__": "__main__"})     # máy test không có nvidia-smi
    for t in ("_0", "_1"):
        res = json.load(open(tmp_path / f"result{t}.json"))
        assert res["ok"] is False and kaggle_worker.NO_GPU_MARK in res["error"]
    cpu = kaggle_worker.kernel_text({"kind": "remake", "need_gpu": False})
    assert "if False and subprocess" in cpu                                     # việc CPU: không xét


def test_kids_setup_kernel_stops_without_gpu():
    assert "Kaggle không cấp GPU" in kaggle_worker.kids_models_kernel()


def test_push_closes_ghosts_first_and_respects_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(kaggle_worker, "_free_slots", lambda cur: calls.append(("free", cur)) or [])
    monkeypatch.setattr(kaggle_api, "push", lambda slug, *a, **k: calls.append(("push", slug)) or {})
    kaggle_worker._push("rmvideo-worker", "t", "x", True, None, None, lambda s: None)
    assert calls == [("free", "rmvideo-worker"), ("push", "rmvideo-worker")]
    calls.clear()
    kaggle_worker._push("rmvideo-music", "t", "x", False, None, None, lambda s: None)      # CPU: không đụng phiên khác
    assert calls == [("push", "rmvideo-music")]
    kaggle_worker._cool(3600, "Kaggle lâu không cấp máy GPU")
    calls.clear()
    with pytest.raises(kaggle_api.KaggleError, match="tạm nghỉ gửi việc GPU"):
        kaggle_worker._push("rmvideo-kids", "t", "x", True, None, None, lambda s: None)
    assert calls == []


def test_cooldown_blocks_new_jobs_not_collect(monkeypatch):
    from queue_manager import Job
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "")
    kaggle_worker._cool(3600, "Kaggle lâu không cấp máy GPU")
    assert "tạm nghỉ" in kaggle_worker.kids_ready()
    job = Job(url="https://www.youtube.com/watch?v=x", mode="cook", duration=0, voice="v")
    assert "tạm nghỉ" in kaggle_worker.usable(job)
    assert kaggle_worker.enabled() == ""                       # thu kết quả phiên đang giữ chỗ vẫn được


def test_queue_timeout_starts_cooldown(monkeypatch):
    monkeypatch.setattr(kaggle_worker, "_cancel_session", lambda *a, **k: None)

    def wait(*a, **k):
        raise kaggle_api.KaggleError("Kaggle chờ máy quá 35 phút")
    monkeypatch.setattr(kaggle_api, "wait", wait)
    with pytest.raises(kaggle_api.KaggleError):
        kaggle_worker._wait_session("rmvideo-worker", 100, lambda *a: None, queue_max=10)
    assert "lâu không cấp máy" in kaggle_worker.gpu_cool() and kaggle_worker._COOL["until"] > time.time() + 3000


def test_no_gpu_result_starts_long_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": [{"fileName": "result.json", "url": "r"}]})

    def dl(url, dst, *a, **k):
        json.dump({"ok": False, "error": kaggle_worker.NO_GPU_MARK + " cho phiên này"}, open(dst, "w"))
        return 1
    monkeypatch.setattr(kaggle_api, "download", dl)
    out = kaggle_worker._fetch("rmvideo-worker", [{"token": "t"}], False, str(tmp_path), lambda s: None, True)
    assert isinstance(out[0], kaggle_api.KaggleError) and "xác minh số điện thoại" in kaggle_worker.gpu_cool()
    assert kaggle_worker._COOL["until"] > time.time() + 5 * 3600
    assert os.path.exists(tmp_path / "kaggle_result.json")


# ---------------- 25/11 chiều: link TikTok dài hơn chế độ đã chọn ----------------

def test_tiktok_too_long_reports_limit_not_download_failure(monkeypatch, tmp_path):
    """Job 5 (25/11): TikTok 365s ở chế độ «remake nguyên ≤5 phút». Trước báo «Không tải được video TikTok … tải về máy
    rồi upload» (sai — tải được, chỉ dài quá) và còn gọi tikwm vô ích. Giờ báo thẳng giới hạn + nút cần bấm."""
    from rmvideo.media import fetch
    monkeypatch.setattr(fetch, "resolve_url", lambda u: u)
    calls = []

    def long_video(url, tmpdir, max_duration):
        raise fetch.TooBig(f"Video dài 365s, vượt giới hạn {max_duration}s. " + fetch._long_hint(365, max_duration))
    monkeypatch.setattr(fetch, "_ytdlp_video_sync", long_video)
    monkeypatch.setattr(fetch, "_tikwm_sync", lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError) as ei:
        fetch._download_sync("https://vt.tiktok.com/ZSx/", str(tmp_path), config.LONG_MAX)
    msg = str(ei.value)
    assert msg.startswith("Video dài 365s") and "Cắt thành phần" in msg and "Không tải được" not in msg
    assert calls == []


def test_tiktok_blocked_still_falls_back_to_tikwm(monkeypatch, tmp_path):
    from rmvideo.media import fetch
    monkeypatch.setattr(fetch, "resolve_url", lambda u: u)

    def blocked(url, tmpdir, max_duration):
        raise RuntimeError("TikTok chặn tải từ máy chủ.")
    monkeypatch.setattr(fetch, "_ytdlp_video_sync", blocked)
    monkeypatch.setattr(fetch, "_tikwm_sync", lambda url, d, m: ("p", "t", "t", {}))
    assert fetch._download_sync("https://vt.tiktok.com/ZSx/", str(tmp_path))[0] == "p"

    def tikwm_long(url, d, m):
        raise fetch.TooBig("Video dài 400s, vượt giới hạn 90s.")
    monkeypatch.setattr(fetch, "_tikwm_sync", tikwm_long)
    with pytest.raises(fetch.TooBig, match="^Video dài 400s"):
        fetch._download_sync("https://vt.tiktok.com/ZSx/", str(tmp_path))


# ---------------- job 6 (25/11): tải 14MB mất 10 phút không thấy gì, ffmpeg dựng bị hệ thống dừng (hết RAM) ----------------

def _media(tmp_path):
    import subprocess
    src, voice = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav")
    for args in (["-f", "lavfi", "-i", "testsrc2=s=360x640:d=6:r=25", "-f", "lavfi", "-i", "sine=f=300:d=6",
                  "-shortest", src], ["-f", "lavfi", "-i", "sine=f=500:d=4", voice]):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=120)
    return src, voice


@pytest.fixture
def render_env(tmp_path, monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    return tmp_path, *_media(tmp_path)


def _oom_on_final(monkeypatch, out, times):
    from rmvideo.media import ff, proc
    real, seen = ff._run_ffmpeg, []

    async def fake(*args, tool="ffmpeg"):
        if args and args[-1] == out:
            seen.append(ff.ffmpeg_threads())
            if len(seen) <= times:
                raise proc.ProcError(["ffmpeg"], -9, "frame= 1135 fps=6.5")
        return await real(*args, tool=tool)
    monkeypatch.setattr(ff, "_run_ffmpeg", fake)
    return seen


def test_render_retries_once_in_low_memory_mode_after_oom_kill(render_env, monkeypatch):
    import asyncio
    from rmvideo.media import ff, renderer
    tmp, src, voice = render_env
    monkeypatch.setattr(config, "FFMPEG_THREADS", 2)
    out = str(tmp / "o.mp4")
    seen = _oom_on_final(monkeypatch, out, 1)
    notes: list = []
    asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, None, allow_hd=False, tmp=str(tmp),
                                notes=notes))
    assert seen == [2, 1] and os.path.getsize(out) > 0          # lần 2: 1 luồng
    assert any("tiết kiệm RAM" in n for n in notes) and ff.ffmpeg_threads() == 2      # hết khối: trả lại như cũ


def test_render_gives_clear_error_when_retry_also_oom(render_env, monkeypatch):
    import asyncio
    from rmvideo.media import renderer
    tmp, src, voice = render_env
    out = str(tmp / "o.mp4")
    seen = _oom_on_final(monkeypatch, out, 2)
    with pytest.raises(RuntimeError, match="hết RAM.*RAM trống"):
        asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, None, allow_hd=False, tmp=str(tmp)))
    assert len(seen) == 2


def test_cancelled_job_is_not_rerendered(render_env, monkeypatch):
    """Huỷ job cũng giết ffmpeg bằng SIGKILL (-9) — không được hiểu là hết RAM rồi dựng lại."""
    import asyncio
    from rmvideo.media import proc, renderer
    tmp, src, voice = render_env
    out = str(tmp / "o.mp4")
    seen = _oom_on_final(monkeypatch, out, 1)

    async def go():
        with proc.owned_by("job-x") as ev:
            ev.set()
            await renderer.render(src, voice, out, "cook", 0, None, None, None, None, allow_hd=False, tmp=str(tmp))
    with pytest.raises(proc.ProcError):
        asyncio.run(go())
    assert len(seen) == 1


def test_free_memory_drops_onnx_sessions(monkeypatch):
    import ocr_text
    import vocal_sep
    from rmvideo.media import ff, talking
    monkeypatch.setattr(ff, "on_kaggle", lambda: False)
    monkeypatch.setattr(ocr_text, "_session", object())
    monkeypatch.setattr(vocal_sep, "_session", object())
    monkeypatch.setitem(talking._SESSION, "s", object())
    ff.free_memory()
    assert ocr_text._session is None and vocal_sep._session is None and not talking._SESSION
    assert "RAM trống" in ff.mem_report(top=3) and "bot giữ" in ff.mem_report()


def test_download_progress_shown_every_30s(monkeypatch):
    from rmvideo.media import fetch
    now = [100.0]
    monkeypatch.setattr(fetch.time, "monotonic", lambda: now[0])
    got: list = []
    hook = fetch._progress_hook(got.append)
    for t, b in ((110, 1 << 20), (131, 6 << 20), (140, 7 << 20), (162, 10 << 20)):
        now[0] = t
        hook({"status": "downloading", "downloaded_bytes": b, "total_bytes": 14 << 20, "speed": 23 * 1024})
    hook({"status": "finished"})
    assert got == ["42% của 14MB · 23 KB/s", "71% của 14MB · 23 KB/s"]


def test_download_progress_reaches_ytdlp_thread(monkeypatch, tmp_path):
    """dispatch đặt ON_PROGRESS rồi gọi fetch.download (asyncio.to_thread): hook của yt-dlp phải thấy được."""
    import asyncio
    from rmvideo.media import fetch
    monkeypatch.setattr(fetch, "resolve_url", lambda u: u)
    monkeypatch.setattr(fetch, "_has_video", lambda p: True)
    monkeypatch.setattr(fetch, "PROGRESS_EVERY", 0.0)

    class YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            for h in self.opts.get("progress_hooks", []):
                h({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
            open(os.path.join(str(tmp_path), "input.mp4"), "wb").write(b"x" * 10)
            return {"title": "t", "duration": 5}
    monkeypatch.setattr(fetch.yt_dlp, "YoutubeDL", YDL)
    monkeypatch.setattr(fetch, "_progress_hook", lambda cb, every=0.0: (lambda d: cb("50%")))
    steps: list = []

    async def go():
        tok = fetch.ON_PROGRESS.set(steps.append)
        try:
            return await fetch.download("https://www.tiktok.com/@a/video/1", str(tmp_path))
        finally:
            fetch.ON_PROGRESS.reset(tok)
    assert asyncio.run(go())[0].endswith("input.mp4") and steps == ["50%"]
