"""Mốc chữ phụ đề cho giọng không có mốc từng từ (CapCut/Piper): rải trên khoảng CÓ TIẾNG của giọng."""
import asyncio
import subprocess

import numpy as np

import align
import config
import core_video

SR = 16000


def _speechish(parts):
    """parts: [(giây có tiếng, giây lặng)] -> tín hiệu: tiếng = sóng 220Hz biên độ 0.3, lặng = nhiễu rất nhỏ."""
    rng = np.random.default_rng(0)
    out = []
    for on, off in parts:
        t = np.arange(int(on * SR)) / SR
        out.append(0.3 * np.sin(2 * np.pi * 220 * t))
        out.append(0.0005 * rng.standard_normal(int(off * SR)))
    return np.concatenate(out).astype(np.float32)


def test_voiced_intervals_find_pauses():
    x = _speechish([(1.0, 0.3), (0.8, 0.05), (0.6, 0.4)])
    ivs = align.voiced_intervals(x, SR)
    # khe 50ms giữa hai âm tiết được lấp; hai khoảng lặng 0.3s, 0.4s là chỗ ngắt
    assert len(ivs) == 2
    assert abs(ivs[0][0] - 0.0) < 0.02 and abs(ivs[0][1] - 1.0) < 0.02
    assert abs(ivs[1][0] - 1.3) < 0.02 and abs(ivs[1][1] - 2.75) < 0.03


def test_spread_voiced_puts_each_phrase_in_its_chunk():
    ivs = [(0.1, 1.0), (1.4, 2.6)]                 # hai khúc giọng cách nhau 0.4s
    words = "Lật mặt nào, khi thịt đã vàng đều.".split()
    out = align.spread_voiced(words, ivs, 0.0, 2.7)
    assert [w for *_, w in out] == words
    first = [o for o in out if o[2] in ("Lật", "mặt", "nào,")]
    rest = out[3:]
    assert first[0][0] >= 0.1 and first[-1][1] <= 1.0 + 1e-6      # cụm 1 trong khúc 1
    assert rest[0][0] >= 1.4 - 1e-6                                 # cụm 2 bắt đầu đúng lúc khúc 2 vang lên
    assert all(b[0] >= a[0] for a, b in zip(out, out[1:]))
    # âm tiết cuối cụm được dành nhiều thời gian hơn âm tiết giữa cụm cùng độ dài chữ
    dur = {w: b - a for a, b, w in out}
    assert dur["nào,"] > dur["mặt"]


def test_spread_voiced_mismatch_and_silence_fallback():
    words = "một hai ba bốn".split()
    out = align.spread_voiced(words, [(0.0, 0.5), (0.8, 1.2), (1.6, 2.0)], 0.0, 2.0)   # 1 cụm, 3 khúc
    assert len(out) == 4 and out[0][0] == 0.0 and out[-1][1] <= 2.0
    assert not any(0.5 < a < 0.8 for a, _, _ in out)               # không có từ nào bắt đầu trong khoảng lặng
    assert align.spread_voiced(words, [], 1.0, 3.0) == align.spread(words, 1.0, 3.0)


def test_words_for_track_uses_voiced_track(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUB_TIMING", "spread")
    x = _speechish([(0.5, 0.0), (0.9, 0.4), (0.8, 1.0)])          # 0.5s lặng đầu, rồi 2 khúc giọng
    x[: int(0.5 * SR)] = 0.0
    wav = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", str(wav)],
                   input=x.tobytes(), check=True)
    clips = [{"path": "c0.mp3", "words": None}]
    plan = [{"start": 0.5, "factor": 1.0, "end": 2.8}]
    words = asyncio.run(core_video.words_for_track(clips, plan, str(wav), ["Cho hành vào, phi thơm lên."]))
    assert [w[2] for w in words] == "Cho hành vào, phi thơm lên.".split()
    assert abs(words[0][0] - 0.5) < 0.03                          # chữ đầu đúng lúc giọng vang lên, không phải 0.5s sớm
    assert abs(words[3][0] - 1.8) < 0.03                          # cụm 2 bắt đầu sau chỗ ngắt hơi
