"""Phase 1: schema cài đặt một nguồn sự thật (rmvideo/app/settings.py), nhập lại mật khẩu khi đổi khoá nhạy cảm (A5),
cài đặt ảnh hưởng tới cách dựng không đổi giữa chừng một job (C9)."""
import asyncio
import os

import pytest

import config
import kaggle_worker
from rmvideo.app import settings as schema


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
        monkeypatch.setitem(config._ENV, k, d)
    return tmp_path


def test_legacy_tables_are_derived_from_schema():
    assert list(config.EDITABLE) == [s.key for s in schema.SCHEMA]
    declared = [k for k, t in config.__annotations__.items() if k.isupper() and t in (str, "str")]
    assert set(declared) == set(config.EDITABLE), set(declared) ^ set(config.EDITABLE)   # khai báo kiểu khớp schema
    assert list(config.META) == list(schema.DISPLAY_ORDER) and set(config.META) == set(config.EDITABLE)
    assert config.SECRET_KEYS == {"YTDLP_PROXY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
                                  "YTDLP_COOKIES", "FB_PAGE_TOKEN", "KAGGLE_API_TOKEN"}
    ids = {g[0] for g in config.GROUPS}
    for s in schema.SCHEMA:
        assert s.group in ids and s.label, s.key
        for o in s.options:                              # lựa chọn hiển thị hợp lệ theo chính schema
            if s.choices:
                assert o[0] in s.choices, (s.key, o)


@pytest.mark.parametrize("key,bad,msg", [
    ("QUALITY", "ultra", "QUALITY chỉ nhận: fast, balanced, high"),
    ("TEASER", "2", "TEASER chỉ nhận: 0, 1"),
    ("KAGGLE_USE", "yes", "KAGGLE_USE chỉ nhận: 0, 1"),
    ("TTS_PROVIDER", "google", "TTS_PROVIDER chỉ nhận: vieneu, edge, capcut"),
    ("TTS_RATE", "fast", "TTS_RATE phải dạng +8% hoặc -5%"),
    ("ASR_ORDER", "groq,whisper", "ASR_ORDER là danh sách groq, gemini, capcut cách nhau dấu phẩy"),
    ("ALLOWED_USERS", "abc", "ALLOWED_USERS phải là các số chat_id cách nhau dấu phẩy"),
    ("AUTO_PER_DAY", "9", "AUTO_PER_DAY là số 1-4 (các khung giờ cách nhau ít nhất 3 tiếng)"),
    ("FB_PAGE_ID", "abc", "FB_PAGE_ID là dãy số id của Fanpage"),
    # siết thêm (B3): các khoá bật/tắt, link web, proxy, độ dài chữ tự do
    ("COVER", "true", "COVER chỉ nhận: 0, 1"),
    ("PUBLIC_URL", "bot.example.com", "PUBLIC_URL phải là link http(s)://tên-miền của web bot"),
    ("YTDLP_PROXY", "1.2.3.4:8080", "YTDLP_PROXY dạng http://user:pass@ip:port hoặc socks5://ip:port"),
    ("BRAND_NAME", "x" * 61, "BRAND_NAME tối đa 60 ký tự"),
])
def test_validation_messages(store, key, bad, msg):
    with pytest.raises(ValueError) as e:
        config.save_settings({key: bad})
    assert str(e.value) == msg
    assert not os.path.exists(config.SETTINGS_FILE)        # lỗi thì không ghi gì


def test_save_normalizes_and_reset(store):
    config.save_settings({"KAGGLE_USERNAME": "https://www.kaggle.com/QueTrai", "AUTO_PLATFORMS": "tiktok, facebook",
                          "BGM_LINKS": "https://youtu.be/a https://youtu.be/a", "PUBLIC_URL": "https://bot.example.com"})
    assert config.KAGGLE_USERNAME == "quetrai" and config.AUTO_PLATFORMS == "facebook,tiktok"
    assert config.BGM_LINKS == "https://youtu.be/a" and oct(os.stat(config.SETTINGS_FILE).st_mode)[-3:] == "600"
    config.save_settings({"KAGGLE_USERNAME": "-", "PUBLIC_URL": ""})      # "-" xoá, rỗng không đổi
    assert config.KAGGLE_USERNAME == "" and config.PUBLIC_URL == "https://bot.example.com"


def test_reauth_only_for_real_changes(store):
    assert config.reauth_keys({"QUALITY": "fast", "GEMINI_API_KEY": "AIza-x"}) == []
    assert config.reauth_keys({"PUBLIC_URL": "https://a.example.com", "YTDLP_PROXY": "", "KAGGLE_API_TOKEN": "KGAT_x"}) \
        == ["PUBLIC_URL", "KAGGLE_API_TOKEN"]
    config.save_settings({"PUBLIC_URL": "https://a.example.com", "ALLOWED_USERS": "123"})
    assert config.reauth_keys({"PUBLIC_URL": "https://a.example.com", "ALLOWED_USERS": "123"}) == []   # gửi lại y nguyên
    assert config.reauth_keys({"PUBLIC_URL": "-"}) == ["PUBLIC_URL"]                      # xoá cũng là đổi
    assert config.reauth_keys({"FB_PAGE_ID": "-"}) == []                                  # chưa đè: xoá không đổi gì
    assert config.reauth_keys({"PUBLIC_URL": "không phải link"}) == []                    # sai: báo lỗi ở bước lưu


def test_render_settings_wait_for_running_job(store):
    with config.hold():
        config.save_settings({"QUALITY": "high", "GEMINI_API_KEY": "AIzaSy-abcdefghijklmnop"})
        assert config.QUALITY == "balanced"                            # job đang chạy giữ nguyên cách dựng
        assert config.GEMINI_API_KEY.startswith("AIzaSy")             # key áp dụng ngay
        d = config.describe_settings()
        assert d["QUALITY"]["value"] == "high" and d["QUALITY"]["pending"] and "pending" not in d["GEMINI_API_KEY"]
    assert config.QUALITY == "high" and "pending" not in config.describe_settings()["QUALITY"]
    config.save_settings({"QUALITY": "fast"})                          # không có job: áp dụng ngay
    assert config.QUALITY == "fast"


def test_kaggle_snapshot_never_carries_secrets_or_accounts(store, monkeypatch):
    for k in config.EDITABLE:
        monkeypatch.setattr(config, k, f"bi-mat-{k}")
    snap = kaggle_worker.settings_snapshot()
    assert "QUALITY" in snap and "SUB_STYLE" in snap
    leaked = [k for k in snap if k in config.SECRET_KEYS or k.startswith(("KAGGLE_", "FB_", "AUTO_", "ALLOWED_", "ADMIN_"))]
    assert not leaked


def test_web_requires_password_for_sensitive_keys(store, monkeypatch, tmp_path):
    from test_security import _client

    async def go():
        async with _client(monkeypatch, tmp_path, "pw-123") as c:
            assert (await c.post("/api/login", json={"password": "pw-123"})).status == 200   # phiên cookie
            r = await c.post("/api/settings", json={"QUALITY": "fast"})
            assert r.status == 200 and config.QUALITY == "fast"                          # khoá thường: không hỏi
            r = await c.post("/api/settings", json={"PUBLIC_URL": "https://evil.example.com", "QUALITY": "high"})
            d = await r.json()
            assert r.status == 403 and d["need_password"] and d["keys"] == ["PUBLIC_URL"]
            assert config.PUBLIC_URL == "" and config.QUALITY == "fast"                  # không lưu gì cả
            r = await c.post("/api/settings", json={"PUBLIC_URL": "https://evil.example.com", "_password": "sai"})
            assert r.status == 403 and (await r.json())["error"] == "Sai mật khẩu" and config.PUBLIC_URL == ""
            r = await c.post("/api/settings", json={"PUBLIC_URL": "https://bot.example.com", "_password": "pw-123"})
            d = await r.json()
            assert r.status == 200 and config.PUBLIC_URL == "https://bot.example.com" and d["fields"]["PUBLIC_URL"]["reauth"]
            assert "_password" not in config.EDITABLE
        async with _client(monkeypatch, tmp_path, "pw-123") as c:                          # script: X-Auth là mật khẩu
            r = await c.post("/api/settings", headers={"X-Auth": "pw-123"}, json={"YTDLP_PROXY": "socks5://1.2.3.4:1080"})
            assert r.status == 200 and config.YTDLP_PROXY == "socks5://1.2.3.4:1080"
    asyncio.run(go())
