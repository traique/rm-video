"""Giờ GPU Kaggle còn lại (web + log) và giờ log theo UTC+7."""
import asyncio
import logging
import time

import kaggle_api


def test_quota_text_and_vn_time():
    q = {"gpu_left": 21.5 * 3600, "gpu_used": 8.5 * 3600, "refresh": "2026-11-22T00:00:00Z"}
    assert kaggle_api.quota_text(q) == "còn 21,5 / 30 giờ GPU tuần này, làm mới 07:00 22/11"
    assert kaggle_api.vn_time("2026-11-21T20:30:00Z") == "03:30 22/11"
    assert kaggle_api.vn_time("lạ") == "lạ"
    assert kaggle_api.quota_text({"gpu_left": -5, "gpu_used": 0, "refresh": ""}) == "còn 0,0 giờ GPU tuần này"


def test_quota_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(kaggle_api, "_QCACHE", {})
    monkeypatch.setattr(kaggle_api, "quota", lambda: calls.append(1) or {"gpu_left": 3600, "gpu_used": 0, "refresh": ""})
    kaggle_api.quota_cached()
    kaggle_api.quota_cached()
    assert len(calls) == 1
    kaggle_api.quota_cached(0)                                  # sau một phiên GPU: đọc mới
    assert len(calls) == 2


def test_log_quota_after_session(monkeypatch, caplog):
    import kaggle_worker
    monkeypatch.setattr(kaggle_api, "configured", lambda: True)
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 7200, "gpu_used": 3600, "refresh": "2026-11-22T00:00:00Z"})
    caplog.set_level("INFO")
    asyncio.run(kaggle_worker.log_quota())
    assert "Kaggle: còn 2,0 / 3 giờ GPU tuần này" in caplog.text
    monkeypatch.setattr(kaggle_api, "configured", lambda: False)    # chưa có token: không gọi mạng
    monkeypatch.setattr(kaggle_api, "quota", lambda: (_ for _ in ()).throw(AssertionError("không được gọi")))
    asyncio.run(kaggle_worker.log_quota())


def test_main_log_clock_is_utc_plus_7(monkeypatch):
    import main  # noqa: F401  (main đặt converter của logging lúc import)
    fmt = logging.Formatter("%(asctime)s", "%H:%M")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    rec.created = 1_800_000_000.0                                # 2027-01-15 08:00:00 UTC
    assert fmt.format(rec) == "15:00"
    assert time.gmtime(1_800_000_000.0).tm_hour == 8


def test_web_quota_endpoint(monkeypatch, tmp_path):
    from test_security import _client
    monkeypatch.setattr(kaggle_api, "configured", lambda: True)
    monkeypatch.setattr(kaggle_api, "_QCACHE", {})
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 36000, "gpu_used": 72000, "refresh": "2026-11-22T00:00:00Z"})

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.get("/api/kaggle/quota")).status == 401
            d = await (await c.get("/api/kaggle/quota", headers={"X-Auth": "pw-123"})).json()
            assert d["configured"] and d["left_h"] == 10.0 and "còn 10,0 / 30 giờ GPU" in d["text"]
    asyncio.run(go())
