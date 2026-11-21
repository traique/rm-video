"""1.3.3: giọng dự phòng trên Botkeep (edge-tts / CapCut) đọc đơn vị như VieNeu ("50g" -> "năm mươi gam"); "1/2 kg" -> "nửa ký"."""
import asyncio

import vi_text
from rmvideo.media import tts_engine


def test_fraction_with_unit():
    assert vi_text.normalize("Thêm 1/2 kg thịt, 1/2kg cá") == "Thêm nửa ký thịt, nửa ký cá"
    assert vi_text.normalize("3/4 lít nước") == "ba phần bốn lít nước"
    assert vi_text.normalize("1/2 chén") == "nửa chén" and vi_text.normalize("ngày 12/11").startswith("ngày mười hai tháng")


def test_botkeep_tts_reads_units_as_words(monkeypatch, tmp_path):
    seen = []

    async def fake_edge(text, path, voice, fallback=False):
        seen.append(text)
        return [(0.0, 0.5, "x")]
    monkeypatch.setattr(tts_engine.config, "TTS_PROVIDER", "edge")
    monkeypatch.setattr(tts_engine, "_edge_words", fake_edge)
    words, prov = asyncio.run(tts_engine.synth_clip_ex("Cho 50g đường và 200 ml nước", "vi-VN-HoaiMyNeural",
                                                       str(tmp_path / "a.mp3")))
    assert prov == "edge" and seen == ["Cho năm mươi gam đường và hai trăm mi li lít nước"]
