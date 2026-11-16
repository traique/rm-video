"""Code mẫu còn lại trong samples/ (mẫu cài đặt, tiến trình con đã thành rmvideo/app/settings.py, rmvideo/media/proc.py) phải tự chạy đúng như đã hứa."""
import asyncio
import importlib.util
import os
import sys

import pytest

_SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "rmvideo")


def _load(name, rel):
    """Nạp mẫu theo đường dẫn tệp (gói rmvideo/ thật đã có ở gốc repo, không được trùng tên)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SAMPLES, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_router = _load("sample_router", "services/router.py")
Provider, ServiceError, complete, loads_lenient = _router.Provider, _router.ServiceError, _router.complete, _router.loads_lenient


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
