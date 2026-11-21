"""1.2.0: bỏ nhạc gốc khỏi tiếng nền (Bandit, Kaggle GPU), giữ tiếng động; lỗi thì giữ tiếng nền cũ."""
import asyncio
import os

import numpy as np
import pytest

import config
import vocal_sep


def test_sfx_only_on_kaggle_gpu_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "SFX_SEP", "1")
    monkeypatch.delenv("SEP_DEVICE", raising=False)
    assert not vocal_sep.sfx_wanted()                               # Botkeep (không GPU): không chạy
    monkeypatch.setenv("SEP_DEVICE", "cuda")
    assert vocal_sep.sfx_wanted()
    monkeypatch.setattr(config, "SFX_SEP", "0")                     # kênh bật kiếm tiền: tắt
    assert not vocal_sep.sfx_wanted()


def test_strip_music_keeps_old_ambient_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SFX_SEP", "1")
    monkeypatch.setenv("SEP_DEVICE", "cuda")
    amb = tmp_path / "ambient.wav"
    amb.write_bytes(b"OLD")
    calls = []

    async def child_ok(args, timeout):
        calls.append((args, timeout))
        open(args[1], "wb").write(b"FX")
        return 0, ["Bandit: đã bỏ nhạc gốc, giữ tiếng động ({'music': -28.1})"]
    monkeypatch.setattr(vocal_sep, "_child", child_ok)
    assert asyncio.run(vocal_sep.strip_music(str(amb))) is True
    assert amb.read_bytes() == b"FX" and calls[0][0][0] == "--sfx" and calls[0][1] == vocal_sep.SFX_TIMEOUT

    async def child_bad(args, timeout):
        open(args[1], "wb").write(b"HALF")                          # file dở rồi chết
        return 1, ["CUDA out of memory"]
    monkeypatch.setattr(vocal_sep, "_child", child_bad)
    assert asyncio.run(vocal_sep.strip_music(str(amb))) is False and amb.read_bytes() == b"FX"

    async def child_slow(args, timeout):
        raise asyncio.TimeoutError
    monkeypatch.setattr(vocal_sep, "_child", child_slow)
    assert asyncio.run(vocal_sep.strip_music(str(amb))) is False and amb.read_bytes() == b"FX"
    assert not os.path.exists(str(amb) + ".bak.wav")

    async def child_nomusic(args, timeout):
        return 0, ["Bandit: gốc gần như không có nhạc (...), giữ tiếng nền"]
    monkeypatch.setattr(vocal_sep, "_child", child_nomusic)
    assert asyncio.run(vocal_sep.strip_music(str(amb))) is False and amb.read_bytes() == b"FX"


def test_remove_voice_then_strip_music(monkeypatch, tmp_path):
    """Tách giọng xong (Kim) thì mới bỏ nhạc; tách giọng hỏng thì không gọi Bandit."""
    monkeypatch.setattr(vocal_sep, "free_mb", lambda: 99999)
    monkeypatch.setattr(vocal_sep, "roformer_wanted", lambda: False)
    monkeypatch.setattr(vocal_sep, "_model_path", lambda: "m")
    amb = str(tmp_path / "a.wav")
    order = []

    async def child(args, timeout):
        order.append("voice")
        open(amb, "wb").write(b"A")
        return 0, ["xong"]

    async def strip(p):
        order.append("music")
        return True
    monkeypatch.setattr(vocal_sep, "_child", child)
    monkeypatch.setattr(vocal_sep, "strip_music", strip)
    assert asyncio.run(vocal_sep.remove_voice("src.mp4", amb)) and order == ["voice", "music"]


def test_overlap_add_rebuilds_input_and_quiet_lift():
    """Khúc 6s chồng 3s (Hann) cộng lại đúng độ dài và biên độ; bài nhỏ được nâng cho model rồi hạ lại."""
    torch = pytest.importorskip("torch")
    from rmvideo.media import stems
    seen = []

    def model(batch):
        x = batch["audio"]["mixture"]
        seen.append(float(x.abs().max()))
        return None, {"audio": {"speech": x * 0.2, "music": x * 0.3, "effects": x * 0.5}}
    rng = np.random.default_rng(1)
    audio = (rng.standard_normal((2, stems.SR * 14 + 123)) * 0.01).astype(np.float32)      # nhỏ: được nâng
    parts = stems.separate(audio, model, "cpu")
    assert all(v.shape == audio.shape for v in parts.values())
    assert np.allclose(parts["effects"], audio * 0.5, atol=1e-5) and np.allclose(sum(parts.values()), audio, atol=1e-5)
    assert max(seen) > float(np.abs(audio).max()) * 2 and torch.is_tensor(torch.zeros(1))
    assert stems.lift(np.full((1, 10), 0.5, np.float32)) == 1.0
