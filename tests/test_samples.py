"""Code mẫu production trong samples/ (Phần 4 tài liệu đánh giá kiến trúc) phải tự chạy đúng như đã hứa."""
import asyncio
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples"))

from rmvideo.app.settings import SCHEMA, Settings            # noqa: E402
from rmvideo.media import proc                               # noqa: E402
from rmvideo.services.router import Provider, ServiceError, complete, loads_lenient   # noqa: E402


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


def test_settings_schema_validates_masks_and_saves_0600(tmp_path):
    s = Settings(SCHEMA, str(tmp_path / "settings.json"), env={"KAGGLE_ASR": "phowhisper"})
    assert s.get("KAGGLE_ASR") == "phowhisper"
    for bad in ({"KAGGLE_ASR": "medium"}, {"TTS_RATE": "fast"}, {"AUTO_PLATFORMS": "youtube"}):
        with pytest.raises(ValueError):
            s.save(bad)
    s.save({"KAGGLE_ASR": "large-v3", "GEMINI_API_KEY": "AIzaSy-abcdefghijklmnop",
            "AUTO_PLATFORMS": "tiktok, facebook, tiktok"})
    assert oct(os.stat(s.path).st_mode)[-3:] == "600" and s.get("AUTO_PLATFORMS") == "tiktok,facebook"
    d = s.describe()
    assert d["GEMINI_API_KEY"]["value"].startswith("AIza") and "•••" in d["GEMINI_API_KEY"]["value"]
    snap = s.snapshot(exclude_prefixes=("AUTO_",))
    assert "GEMINI_API_KEY" not in snap and "KAGGLE_API_TOKEN" not in snap and "AUTO_PLATFORMS" not in snap
    s.save({"KAGGLE_ASR": "-"})                        # "-" = bỏ giá trị đè, quay về env
    assert s.get("KAGGLE_ASR") == "phowhisper"
    assert Settings(SCHEMA, s.path, env={}).get("AUTO_PLATFORMS") == "tiktok,facebook"   # đọc lại từ đĩa


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
