"""Giai đoạn 2 — cache có trần dung lượng, tách giọng chỉ các khúc có lời, cửa sổ lời."""
import os
import time

import cache
import core_video
import vocal_sep


def test_cache_roundtrip_and_lru(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "DIR", str(tmp_path))
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * (600 << 10))
    keys = [cache.key("tts", "capcut", i) for i in range(3)]
    for i, k in enumerate(keys):
        cache.put("tts", k, str(src), ".mp3")
        os.utime(cache.get("tts", k, ".mp3"), (time.time() - 100 + i, time.time() - 100 + i))
    cache.get("tts", keys[0], ".mp3")                 # vừa dùng lại -> không bị xoá trước
    cache.prune(max_mb=1)
    assert cache.get("tts", keys[0], ".mp3") and not cache.get("tts", keys[1], ".mp3")
    cache.put_json("scenes", "k", {"cuts": [1.0]})
    assert cache.get_json("scenes", "k") == {"cuts": [1.0]}


def test_cache_key_depends_on_version(monkeypatch):
    k1 = cache.key("tts", "a")
    monkeypatch.setitem(cache.STEP_VERSION, "tts", 99)
    assert cache.key("tts", "a") != k1


def test_file_hash_changes_with_content(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"1" * 1000)
    b.write_bytes(b"1" * 999 + b"2")
    assert cache.file_hash(str(a)) != cache.file_hash(str(b))


def test_separation_skips_chunks_without_speech():
    gen = 253440                                       # ~5.75 giây mỗi khúc ở 44.1kHz
    n = gen * 4 - 10
    need = vocal_sep.chunks_needed(n, gen, [(1.0, 2.0), (17.5, 18.0)])
    assert need == [True, False, False, True]
    assert vocal_sep.chunks_needed(n, gen, None) == [True] * 4
    assert vocal_sep.chunks_needed(n, gen, []) == [False] * 4


def test_speech_windows_merge_and_clip():
    w = core_video.speech_windows([(0.1, 1.0), (1.2, 2.0), (5.0, 6.0), (9.8, 12.0)], 10.0)
    assert w == [(0.0, 2.3), (4.7, 6.3), (9.5, 10.0)]


def test_separation_edges_fade_next_to_skipped_chunks(monkeypatch):
    import numpy as np

    class FakeSess:
        def get_inputs(self):
            return [type("I", (), {"name": "x"})()]

        def run(self, _, feeds):
            x = feeds["x"]
            return [x]                                   # "giọng" = chính tín hiệu vào
    monkeypatch.setattr(vocal_sep, "_get_session", lambda: FakeSess())
    gen = vocal_sep.HOP * (vocal_sep.DIM_T - 1) - vocal_sep.N_FFT
    sr = vocal_sep.SR
    mix = (0.3 * np.ones((2, gen * 3), np.float32))
    voc = vocal_sep._separate(mix, windows=[(gen / sr + 1.0, gen / sr + 2.0)])     # chỉ khúc giữa có lời
    assert np.allclose(voc[:, :gen], 0) and np.allclose(voc[:, 2 * gen:], 0)
    assert abs(voc[0, gen]) < 0.01 and abs(voc[0, 2 * gen - 1]) < 0.01          # mép khúc giữa đã vuốt về 0
    assert np.abs(voc[0, gen + gen // 2]) > 0.1                                    # giữa khúc vẫn tách
