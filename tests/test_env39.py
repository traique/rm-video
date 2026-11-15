"""Bản 39 — file .env: bộ đọc chịu ghi chú cuối dòng và giá trị trong ngoặc; .env.example liệt kê đủ mọi biến, giá trị
mặc định trong mẫu khớp mã, và mọi dòng mẫu (kể cả khi bỏ # để bật) đọc ra giá trị dùng được."""
import os
import re

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_ONLY = ("BOT_TOKEN", "WEB_PASSWORD", "OPENROUTER_BASE_URL", "POT_SERVER", "PORT", "FFMPEG_THREADS", "CACHE_MAX_MB",
            "OUT_MAX_MB", "HEALTH_CHECK_HOURS", "ALLOWED_DOMAINS", "OUTPUT_HD", "RENDER_BUDGET", "LOUDNESS_2PASS",
            "SEP_THREADS", "SEP_BUDGET", "SEP_MASK", "FB_GRAPH_VERSION")


def _load(monkeypatch, tmp_path, text, keys):
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "_DIR", str(tmp_path))
    for k in keys:
        monkeypatch.setenv(k, "")             # trống = được ghi; monkeypatch tự gỡ sau test
    config._load_dotenv()
    return {k: os.environ.get(k) for k in keys}


def test_dotenv_inline_comments_quotes_and_priority(monkeypatch, tmp_path):
    text = ("﻿RMV_A=1\nRMV_B=500   # trần cache\nRMV_C=      # ghi chú\nRMV_D=\"mật # khẩu có cách\"\n"
            "RMV_E='x y'\nRMV_F=abc#123\nRMV_G=+8%\nRMV_H=https://www.facebook.com/a,https://www.tiktok.com/@b\n"
            "# RMV_I=bỏ qua\nRMV_J=\nRMV_K=giữ\n")
    keys = [f"RMV_{c}" for c in "ABCDEFGHIJ"]
    got = _load(monkeypatch, tmp_path, text, keys)
    assert got == {"RMV_A": "1", "RMV_B": "500", "RMV_C": "", "RMV_D": "mật # khẩu có cách", "RMV_E": "x y",
                   "RMV_F": "abc#123", "RMV_G": "+8%", "RMV_H": "https://www.facebook.com/a,https://www.tiktok.com/@b",
                   "RMV_I": "", "RMV_J": ""}
    monkeypatch.setenv("RMV_K", "từ panel")   # biến môi trường thật (panel Botkeep) thắng file .env
    config._load_dotenv()
    assert os.environ["RMV_K"] == "từ panel"


def test_env_example_complete_and_matches_defaults(monkeypatch, tmp_path):
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    lines = {}
    for ln in text.splitlines():
        m = re.match(r"^(?:# )?([A-Z][A-Z0-9_]+)=(.*)$", ln)
        if m:
            lines[m.group(1)] = m.group(2)
            assert not re.search(r"\s#", m.group(2)), f"ghi chú cuối dòng (bản cũ đọc sai): {ln}"
    missing = [k for k in (*config.EDITABLE, *ENV_ONLY) if k not in lines and k != "YTDLP_COOKIES"]
    assert missing == [], missing
    assert "YTDLP_COOKIES" not in lines and "YTDLP_COOKIES" in text     # nhiều dòng: chỉ hướng dẫn dán ở web
    wrong = {k: v for k, v in lines.items() if k in config.EDITABLE and v and v != config.EDITABLE[k]}
    assert wrong == {}, wrong
    # bỏ hết dấu # để bật mọi biến: phải đọc ra đúng giá trị, số vẫn là số
    enabled = "\n".join(re.sub(r"^# (?=[A-Z][A-Z0-9_]+=)", "", ln) for ln in text.splitlines())
    got = _load(monkeypatch, tmp_path, enabled, list(lines))
    for k in ("FFMPEG_THREADS", "CACHE_MAX_MB", "OUT_MAX_MB", "SEP_THREADS", "PORT"):
        assert got[k].isdigit(), (k, got[k])
    assert float(got["HEALTH_CHECK_HOURS"]) == 6 and float(got["RENDER_BUDGET"]) == 600
    assert got["VIENEU_VOICE_FEMALE"] == "Mỹ Duyên" and got["AUTO_PLATFORMS"] == "facebook,tiktok"
