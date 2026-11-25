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
