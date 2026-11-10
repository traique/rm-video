"""Thử với Gemini THẬT (tốn vài token). Chỉ chạy khi đặt LIVE_GEMINI=1 và GEMINI_API_KEY:
    LIVE_GEMINI=1 GEMINI_API_KEY=... pytest -q tests/test_live_gemini.py
CI không có key nên bỏ qua."""
import asyncio
import os
import subprocess

import pytest

import config

pytestmark = pytest.mark.skipif(not (os.environ.get("LIVE_GEMINI") == "1" and os.environ.get("GEMINI_API_KEY")),
                                reason="cần LIVE_GEMINI=1 và GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")


def test_live_structured_shorten():
    import prompt_engine
    out = asyncio.run(prompt_engine.shorten([(0, "Đầu tiên mình cho thịt ba chỉ vào chảo dầu nóng rồi đảo đều tay", 8)]))
    assert out.get(0) and len(out[0].split()) <= 12


def test_live_timed_rewrite_with_images(tmp_path):
    import prompt_engine
    import scenes
    v = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=orange:s=360x640:d=3", "-f", "lavfi",
                    "-i", "color=c=green:s=360x640:d=3", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", v], check=True)
    segs = [(0.2, 2.8, "đầu tiên cho thịt heo vào chảo chiên vàng"), (3.1, 5.8, "sau đó cho 2 muỗng nước mắm")]
    out = asyncio.run(prompt_engine.generate_timed("Thịt chiên", "", "cook", segs, 6.0,
                                                   images_for=lambda sg: scenes.keyframes(v, sg)))
    assert out and len(out["segments"]) == 2 and "2" in out["segments"][1]["text"]


def test_live_transcribe_word_timestamps(tmp_path):
    import transcribe
    audio = os.environ.get("LIVE_ASR_AUDIO")            # file giọng tiếng Việt bất kỳ (mp3)
    if not audio:
        pytest.skip("đặt LIVE_ASR_AUDIO=đường dẫn mp3 tiếng Việt để thử bóc lời")
    text, segs = asyncio.run(transcribe._gemini(audio))
    assert text and segs and all(b >= a for a, b, _ in segs)
