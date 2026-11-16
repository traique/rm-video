"""Code mẫu production trong samples/ (mẫu cài đặt đã thành rmvideo/app/settings.py, xem test_settings_schema.py) (Phần 4 tài liệu đánh giá kiến trúc) phải tự chạy đúng như đã hứa."""
import asyncio
import importlib.util
import os
import subprocess
import sys
import time

import pytest

_SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "rmvideo")


def _load(name, rel):
    """Nạp mẫu theo đường dẫn tệp (gói rmvideo/ thật đã có ở gốc repo, không được trùng tên)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SAMPLES, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


proc = _load("sample_proc", "media/proc.py")
_router = _load("sample_router", "services/router.py")
Provider, ServiceError, complete, loads_lenient = _router.Provider, _router.ServiceError, _router.complete, _router.loads_lenient


def test_proc_runs_reports_errors_and_kills_whole_group():
    async def go():
        r = await proc.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=0.2", "-f", "null", "-"], timeout=20)
        assert r.seconds < 10 and r.stderr == ""
        with pytest.raises(proc.ProcError) as e:
            await proc.run(["ffmpeg", "-i", "/khong/co.mp4", "-f", "null", "-"], timeout=20)
        assert e.value.code != 0 and "co.mp4" in e.value.stderr
        t = time.time()
        with pytest.raises(asyncio.TimeoutError):      # sh sinh ra sleep (cháu): quá giờ phải giết cả cháu
            await proc.run(["sh", "-c", "sleep 30 & wait"], timeout=0.5)
        assert time.time() - t < 5 and not proc.RUNNING
    asyncio.run(go())
    ps = subprocess.run(["ps", "-eo", "stat,args"], capture_output=True, text=True).stdout.splitlines()
    alive = [l for l in ps if l.split(None, 1)[-1].startswith("sleep 30") and not l.strip().startswith("Z")]
    assert not alive                                   # chỉ còn xác (Z) chờ init dọn, không còn sleep chạy thật


def test_router_retries_then_falls_back():
    calls, rec = [], []

    async def flaky(prompt, images, schema):
        calls.append("gemini")
        if len(calls) < 3:
            raise ServiceError("503", retryable=True, wait=0.01)
        return '```json\n{"segments": [{"i": 0, "text": "ok"},]}\n```'

    async def never(prompt, images, schema):
        raise ServiceError("401")

    async def go():
        name, d = await complete([Provider("gemini", flaky), Provider("openrouter", never)], "p",
                                 record=lambda n, **k: rec.append((n, k["ok"])))
        assert name == "gemini" and d["segments"][0]["text"] == "ok" and rec[-1] == ("gemini", True) and len(calls) == 3
        with pytest.raises(ServiceError, match="mọi dịch vụ"):
            await complete([Provider("a", never), Provider("b", never)], "p")
    asyncio.run(go())
    assert loads_lenient('text {"a": [1, 2,]} tail') == {"a": [1, 2]}
