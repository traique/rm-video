"""Ổ đĩa Botkeep 2GB: deno không cài mặc định (setup_youtube), lệnh clean, cảnh báo ổ gần đầy."""
import asyncio
import os
import stat

import core_video
import healthcheck
import potoken_setup
import storage


def test_requirements_without_deno():
    req = open(os.path.join(os.path.dirname(storage.__file__), "requirements.txt"), encoding="utf-8").read()
    assert "yt-dlp[default,curl-cffi]" in req and "deno" not in req.split("yt-dlp[")[1].split("]")[0]
    import ytdlp_update
    assert "deno" not in ytdlp_update.SPEC
    conf = open(os.path.join(os.path.dirname(storage.__file__), ".config", "pip", "pip.conf"), encoding="utf-8").read()
    assert "no-cache-dir = true" in conf


def _fake_deno(d):
    os.makedirs(os.path.join(d, "bin"), exist_ok=True)
    p = os.path.join(d, "bin", "deno")
    open(p, "w").write("#!/bin/sh\necho deno 2.9.8\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def test_setup_youtube_installs_into_tools_and_path(tmp_path, monkeypatch):
    monkeypatch.setattr(potoken_setup, "DENO_DIR", str(tmp_path / "deno"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert potoken_setup.js_runtime() == ""
    calls = []

    async def fake_pip(args, log, timeout):
        calls.append(args)
        _fake_deno(str(tmp_path / "deno"))
        return True
    monkeypatch.setattr(potoken_setup, "_pip", fake_pip)
    assert asyncio.run(potoken_setup.install_deno(lambda s: None))
    assert "--no-cache-dir" in calls[0] and "--target" in calls[0]
    assert potoken_setup.js_runtime().endswith("deno/bin/deno")
    potoken_setup.remove_deno(lambda s: None)
    assert not os.path.exists(tmp_path / "deno")

    async def bad_pip(args, log, timeout):
        return False
    monkeypatch.setattr(potoken_setup, "_pip", bad_pip)
    assert not asyncio.run(potoken_setup.install_deno(lambda s: None))


def test_youtube_without_js_runtime_is_explained_and_not_alarmed(monkeypatch):
    monkeypatch.setattr(potoken_setup, "js_runtime", lambda: "")
    e = core_video._friendly(Exception("ERROR: [youtube] x: n challenge solving failed: No supported JavaScript runtime"))
    assert "setup_youtube" in str(e)
    assert asyncio.run(healthcheck.check_youtube()) is None        # chưa cài deno: không báo động giả


def test_clean_removes_regenerable_only(tmp_path, monkeypatch):
    pip_cache, work, outputs = tmp_path / "pip", tmp_path / "work", tmp_path / "outputs"
    for d in (pip_cache, work, outputs):
        d.mkdir()
        (d / "f").write_bytes(b"x" * 1000)
    monkeypatch.setattr(storage, "PIP_CACHE", str(pip_cache))
    monkeypatch.setattr(storage, "WORK_DIR", str(work))
    pruned = []
    monkeypatch.setattr(storage.cache, "prune", lambda max_mb=None: pruned.append(max_mb))
    storage.clean(lambda s: None)
    assert not pip_cache.exists() and not work.exists() and pruned == [0]
    assert (outputs / "f").exists()                                # video kết quả không đụng tới


def test_low_disk_warning(monkeypatch):
    msgs = []
    monkeypatch.setattr(storage, "_free_mb", lambda p: 120)
    storage.warn_low_disk(msgs.append)
    assert msgs and "clean" in msgs[0] and "120MB" in msgs[0]
    msgs.clear()
    monkeypatch.setattr(storage, "_free_mb", lambda p: 5000)
    storage.warn_low_disk(msgs.append)
    assert not msgs


def test_ffmpeg_selftest_passes_and_reports_failure(monkeypatch):
    """Tự kiểm tra ffmpeg lúc khởi động: bản đang có chạy đủ bộ lọc; bộ lọc hỏng thì báo kèm lỗi."""
    ok, detail = core_video.selftest_sync()
    assert ok, detail
    monkeypatch.setattr(core_video, "AOUT", "aresample=48000:ochl=khong_co_kenh_nay")
    ok, detail = core_video.selftest_sync()
    assert not ok and "ochl" in detail
