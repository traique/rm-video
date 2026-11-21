"""1.2.0: bỏ khoảng lặng ĐẦU mẩu giọng TTS (giữ 0,05s) — lời vào đúng nhịp hình, độ dài không tính phần im lặng."""
import asyncio
import subprocess

import numpy as np

from rmvideo.media import voiceplan


def _tone(path, lead, dur, tail):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=220:d={dur}", "-af",
                    f"adelay={int(lead * 1000)},apad=pad_dur={tail}", "-ar", "24000", "-ac", "1", path], check=True)


def _onset(path, sr=16000):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, np.int16).astype(np.float32))
    return int(np.argmax(x > 1000)) / sr


def test_speech_span_and_compose_trim_leading_silence(tmp_path):
    p = str(tmp_path / "v.wav")
    _tone(p, 0.3, 1.0, 0.5)
    lead, dur = asyncio.run(voiceplan.speech_span(p))
    assert abs(lead - (0.3 - voiceplan.LEAD_KEEP)) < 0.03
    assert abs(dur - (voiceplan.LEAD_KEEP + 1.0 + voiceplan.SPEECH_TAIL)) < 0.05    # không tính 0,25s im lặng đầu
    out = str(tmp_path / "track.wav")
    plan = [{"start": 1.0, "factor": 1.0, "end": 1.0 + dur}]
    asyncio.run(voiceplan.compose_voice([{"path": p, "lead": lead}], plan, out, 4.0))
    assert abs(_onset(out) - (1.0 + voiceplan.LEAD_KEEP)) < 0.03                    # lời vào ngay sau mốc đặt
    asyncio.run(voiceplan.compose_voice([{"path": p}], plan, out, 4.0))
    assert abs(_onset(out) - 1.3) < 0.03                                              # không có lead: như cũ


def test_lead_capped_and_edge_words_shifted(tmp_path):
    p = str(tmp_path / "v.wav")
    _tone(p, 1.2, 0.5, 0.2)
    lead, _d = asyncio.run(voiceplan.speech_span(p))
    assert lead == voiceplan.LEAD_MAX                                                 # mở đầu im quá lâu: cắt tối đa 0,5s
    lead, dur = asyncio.run(voiceplan.speech_span(p, [(1.2, 1.5, "a"), (1.5, 1.7, "b")]))
    assert abs(lead - voiceplan.LEAD_MAX) < 1e-6
    words = asyncio.run(voiceplan.words_for_track([{"path": p, "lead": 0.15, "words": [(0.2, 0.5, "Xin"), (0.5, 0.9, "chào")]}],
                                                  [{"start": 2.0, "factor": 1.0, "end": 3.0}], p, ["Xin chào"]))
    assert abs(words[0][0] - 2.05) < 1e-6 and words[0][2] == "Xin"
