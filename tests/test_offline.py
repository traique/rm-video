"""Gói tuỳ chọn offline: tách giọng nhanh Spleeter, giọng Piper, dò chữ OCR.
Phần cần sherpa-onnx + model thật chỉ chạy khi đặt OFFLINE_MODELS (thư mục model đã tải) — CI bỏ qua."""
import asyncio
import os
import subprocess

import numpy as np
import pytest

import config
import core_video
import metrics
import offline
import ocr_text

REAL = bool(os.environ.get("OFFLINE_MODELS")) and offline.sherpa_ok()


def test_accompaniment_only_touches_speech_chunks():
    sr, step = offline.SR, int(offline.CHUNK_S * offline.SR)
    mix = np.ones((2, step * 3), np.float32)
    calls = []

    def fake_sep(x):
        calls.append(x.shape[1])
        return np.zeros_like(x)                       # "tiếng nền" sau tách = 0 để dễ thấy
    out = offline.accompaniment(mix, [(step / sr + 1.0, step / sr + 2.0)], fake_sep)
    assert calls == [step + 2 * int(offline.CONTEXT_S * sr)]   # chỉ khúc giữa có lời, kèm 0.5s ngữ cảnh hai bên
    assert np.all(out[:, :step - offline.XFADE] == 1) and np.all(out[:, 2 * step + offline.XFADE:] == 1)
    assert np.all(out[:, step + offline.XFADE:2 * step - offline.XFADE] == 0)
    edge = out[0, step:step + offline.XFADE]
    assert edge[0] == pytest.approx(1.0) and np.all(np.diff(edge) <= 0)      # vuốt mượt, không nhảy bậc
    full = offline.accompaniment(mix, None, fake_sep)                         # không có mốc lời: tách cả bài
    assert np.all(full == 0)


def test_ocr_band_shape_filter():
    h, w = 640, 352
    runs = np.zeros((10, h), np.int32)
    runs[:, 500:520] = 200                           # dòng chữ: rộng 200, cao 20, có ở mọi khung
    runs[:6, 100:160] = 60                           # ô vuông 60x60 (kiểu bàn cờ): không phải dòng chữ
    runs[:3, 300:310] = 200                          # chữ chỉ hiện 3/10 khung (tiêu đề thoáng qua)
    assert ocr_text.bands_from_runs(runs, w) == [(round(500 / h, 4), round(520 / h, 4))]
    m = np.array([[1, 1, 0, 1, 1, 1], [0, 0, 0, 0, 0, 0]], bool)
    assert list(ocr_text.longest_runs(m)) == [3, 0]


def test_tts_falls_back_to_offline_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TTS_PROVIDER", "edge")
    monkeypatch.setattr(config, "TTS_OFFLINE", "1")
    monkeypatch.setattr(metrics, "record", lambda *a, **k: None)

    async def edge_down(*a, **k):
        raise RuntimeError("NoAudioReceived")
    spoken = []

    async def fake_speak(text, path):
        spoken.append(text)
        open(path, "wb").write(b"x")
    monkeypatch.setattr(core_video, "_edge_words", edge_down)
    monkeypatch.setattr(offline, "speak", fake_speak)
    assert asyncio.run(core_video.synth_clip("Xin chào", config.VOICE_FEMALE, str(tmp_path / "a.mp3"))) is None
    assert spoken == ["Xin chào"]
    monkeypatch.setattr(config, "TTS_OFFLINE", "0")
    with pytest.raises(RuntimeError):
        asyncio.run(core_video.synth_clip("Xin chào", config.VOICE_FEMALE, str(tmp_path / "b.mp3")))


@pytest.mark.skipif(not REAL, reason="cần OFFLINE_MODELS + sherpa-onnx")
def test_real_spleeter_and_piper(tmp_path):
    speech = str(tmp_path / "s.mp3")
    asyncio.run(offline.speak("Đầu tiên mình cho thịt ba chỉ vào chảo dầu nóng rồi đảo đều tay.", speech))
    d = asyncio.run(core_video.probe_duration(speech))
    assert 2.0 < d < 10.0
    mix = str(tmp_path / "mix.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", speech, "-f", "lavfi", "-i", "anoisesrc=color=pink:a=0.05:d=25",
                    "-filter_complex", "[0:a]adelay=12000|12000[s];[s][1:a]amix=inputs=2:duration=longest", "-ac", "2",
                    "-ar", "44100", mix], check=True)
    out = str(tmp_path / "amb.wav")
    assert asyncio.run(offline.remove_voice_fast(mix, out, windows=[(11.7, 12.3 + d)]))
    assert abs(asyncio.run(core_video.probe_duration(out)) - asyncio.run(core_video.probe_duration(mix))) < 0.1


@pytest.mark.skipif(not (os.environ.get("OFFLINE_MODELS") and ocr_text.available()), reason="cần model OCR")
def test_real_ocr_detects_burned_subtitle(tmp_path):
    v = str(tmp_path / "v.mp4")
    font = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "BeVietnamPro-ExtraBold.ttf")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:d=8:r=10", "-vf",
                    f"drawtext=fontfile={font}:text='Món ngon mỗi ngày':fontsize=56:fontcolor=yellow:x=(w-tw)/2:y=h*0.8",
                    "-c:v", "libx264", "-preset", "ultrafast", v], check=True)
    bands = ocr_text.detect_sync(v)
    assert len(bands) == 1 and 0.75 < bands[0][0] < 0.85


def test_long_speech_uses_fast_separation(monkeypatch, tmp_path):
    """Người gốc nói > 45s: trước đây phải tắt tiếng nền; có gói offline thì tách bằng Spleeter."""
    import cache
    import pipeline
    import vocal_sep
    from queue_manager import Job
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "cache"))
    for k, v in (("TTS_PROVIDER", "capcut"), ("BRAND_INTRO", "0"), ("QUALITY", "fast"), ("VOICE_SEPARATION", "1"),
                 ("CUT_PROMO", "0")):
        monkeypatch.setattr(config, k, v)
    segs = [(float(t), t + 4.5, f"đoạn {t}") for t in range(0, 55, 5)]          # ~50s có lời

    async def fake_tr(audio, *a, **k):
        return "x", segs

    async def fake_timed(title, desc, mode, sg, out_dur, images_for=None):
        out = [{"i": k, "start": a, "slot": 4.5, "span": 4.0, "text": "Lời mới nè", "orig": t} for k, (a, b, t) in enumerate(sg)]
        return {"segments": out, "script": "x", "title": "t", "caption": "c", "hashtags": "#a"}

    async def fake_synth(text, voice, path):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=500:d=1", "-ar", "24000", path], check=True)
        return None, "capcut"
    used = []

    async def kim(*a, **k):
        used.append("kim")
        return False

    async def fast(src, out, windows=None, timeout=600):
        used.append(("fast", len(windows or [])))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "2", "-ar", "44100", out], check=True)
        return True
    monkeypatch.setattr(pipeline, "transcribe_segments", fake_tr)
    monkeypatch.setattr(pipeline.prompt_engine, "generate_timed_any", fake_timed)
    monkeypatch.setattr(core_video, "synth_clip_ex", fake_synth)
    monkeypatch.setattr(vocal_sep, "remove_voice", kim)
    monkeypatch.setattr(offline, "remove_voice_fast", fast)
    monkeypatch.setattr(offline, "spleeter_available", lambda: True)
    src = str(tmp_path / "in.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=58:s=180x320:r=10", "-f", "lavfi",
                    "-i", "sine=f=800:d=58", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src],
                   check=True)
    job = Job(url="", mode="cook", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src)
    (tmp_path / "w").mkdir()
    asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert used and used[0][0] == "fast" and "kim" not in used
    assert "Đã tách bỏ giọng gốc" in job.error


def test_tts_cache_skips_fallback_voice(monkeypatch, tmp_path):
    """Review đợt 2: giọng Piper/edge dự phòng không được cache dưới khoá giọng CapCut."""
    import cache
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "c"))
    monkeypatch.setattr(config, "TTS_PROVIDER", "capcut")
    calls = []

    async def fake_ex(text, voice, path):
        calls.append(text)
        open(path, "wb").write(b"PIPER")
        return None, "piper"
    monkeypatch.setattr(core_video, "synth_clip_ex", fake_ex)
    for k in range(2):
        asyncio.run(core_video.synth_clip_cached("Xin chào", config.CAPCUT_VOICE_FEMALE, str(tmp_path / f"{k}.mp3")))
    assert len(calls) == 2


def test_spleeter_pads_short_and_odd_chunks(monkeypatch):
    """Review đợt 2: Spleeter cắt đầu ra xuống bội số 1024 và lỗi với khúc < 4096 mẫu."""
    class FakeSp:
        def process(self, sample_rate, samples):
            n = samples.shape[1]
            if n < 4096:
                raise RuntimeError("vector::_M_default_append")
            data = samples[:, : n // 1024 * 1024] * 0
            return type("O", (), {"sample_rate": sample_rate, "stems": [None, type("S", (), {"data": data})()]})()
    import sys
    import types
    fake_mod = types.SimpleNamespace(
        OfflineSourceSeparationConfig=lambda **k: None, OfflineSourceSeparationModelConfig=lambda **k: None,
        OfflineSourceSeparationSpleeterModelConfig=lambda **k: None, OfflineSourceSeparation=lambda cfg: FakeSp())
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_mod)
    sep = offline._spleeter()
    for n in (100, 4095, 441000, 441017):
        out = sep(np.ones((2, n), np.float32))
        assert out.shape == (2, n) and not out.any()          # đủ độ dài, không còn đuôi giọng gốc
