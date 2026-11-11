"""Cờ tiếng gốc từng đoạn (lời mới / giữ tiếng gốc / cả hai), timeline + SRT, SSE."""
import asyncio
import json
import os
import re
import subprocess

import pytest

import config
import core_video
import pipeline
import review
import state
import prompt_engine
from queue_manager import Job


def _tone_rms(path, freq, start, dur):
    """Độ lớn (dB) quanh tần số `freq` trong [start, start+dur] của file."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", path, "-af",
                          f"bandpass=f={freq}:width_type=q:w=8,astats=metadata=0:reset=0", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    vals = re.findall(r"RMS level dB:\s*(-?[0-9.]+|-inf)", err)
    return float(vals[-1]) if vals and vals[-1] != "-inf" else -200.0


@pytest.mark.parametrize("mode", ["review", "cook"])
def test_original_audio_only_in_marked_window(tmp_path, mode):
    src, voice, out = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav"), str(tmp_path / "o.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=6:s=320x568:r=25", "-f", "lavfi",
                    "-i", "sine=f=1000:d=6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=6", "-ar", "44100", voice], check=True)
    segs = [{"start": 0.0, "ost": "new"}, {"start": 2.0, "ost": "orig"}, {"start": 4.0, "ost": "new"}]
    wins = core_video.ost_windows(segs, 6.0)
    assert wins == [(2.0, 4.0, 1.0)]
    gate = [(0.0, 6.0)] if mode == "cook" else None               # cook: người gốc "nói" suốt -> tắt tiếng nền
    asyncio.run(core_video.render(src, voice, out, mode, 6, speech_gate=gate, tmp=str(tmp_path), ost=wins))
    inside, outside = _tone_rms(out, 1000, 2.5, 1.0), _tone_rms(out, 1000, 0.5, 1.0)
    assert inside - outside > 25, (inside, outside)               # tiếng gốc chỉ vang trong cửa sổ đánh dấu


def test_ost_windows_levels():
    segs = [{"start": 0.0, "ost": "both"}, {"start": 3.0}, {"start": 5.0, "ost": "orig"}]
    assert core_video.ost_windows(segs, 8.0) == [(0.0, 3.0, 0.35), (5.0, 8.0, 1.0)]


def test_parse_review_reply_ost_commands():
    e = review.parse_review_reply("1: gốc\n2: Lời mới cho đoạn hai\n3: lót gốc\n4: mới\nkhông phải lệnh")
    assert e == {1: {"ost": "orig"}, 2: "Lời mới cho đoạn hai", 3: {"ost": "both"}, 4: {"ost": "new"}}


def test_approve_keeps_ost_and_allows_empty_text_for_orig():
    src = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="", status="review",
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "text": "Một", "ost": "new"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "text": "Hai", "ost": "new"}])
    state.queue.jobs[src.id] = src
    new = review.approve_job(src, {1: {"ost": "orig", "text": ""}, 0: {"ost": "khong-hop-le"}})
    assert [s["ost"] for s in new.approved] == ["new", "orig"]
    assert new.script == "Một"


def test_ai_may_propose_orig_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "OST_AUTO", "1")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())
    seen = {}

    async def fake(prompt, images=None, schema=None, **kw):
        seen["schema"], seen["hint"] = schema, "TIẾNG GỐC" in prompt
        return json.dumps({"segments": [{"i": 0, "text": "Thả thịt vô chảo nè.", "ost": "new"},
                                        {"i": 1, "text": "", "ost": "orig"},
                                        {"i": 2, "text": "Rắc tiêu lên cho thơm.", "ost": "orig"}],
                           "title": "t", "caption": "c", "hashtags": "#a"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    segs = [(0.0, 2.0, "cho thịt vào chảo"), (3.0, 5.0, "ui da nóng quá"), (6.0, 8.0, "rắc tiêu")]
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 9.0))
    assert seen["schema"] is prompt_engine.TimedOstOut and seen["hint"]
    assert [s["ost"] for s in out["segments"]] == ["new", "orig", "new"]     # tối đa 1/5 (ít nhất 1) đoạn
    assert out["segments"][1]["text"] == "" and out["script"] == "Thả thịt vô chảo nè. Rắc tiêu lên cho thơm."


def test_final_timeline_shape():
    segs = [{"start": 0.0, "text": "a", "orig": "x", "ost": "new"}, {"start": 2.0, "text": "", "ost": "orig"}]
    plan = [{"start": 0.1, "end": 1.5, "factor": 1.0}, {"start": 2.0, "end": 2.0, "factor": 0.92}]
    tl = pipeline.final_timeline(segs, plan, 4.0)
    assert tl[0]["voice"] == {"start": 0.1, "end": 1.5, "speed": 1.0} and tl[1]["voice"] is None
    assert tl[1]["end"] == 4.0


def test_srt_format():
    import subs
    srt = subs.to_srt([(0.0, 0.4, "Món", 0), (0.4, 0.9, "ngon", 0), (61.25, 62.0, "lắm.", 1)])
    assert srt.startswith("1\n00:00:00,000 --> ") and "00:01:01,250" in srt


def test_web_timeline_srt_and_events(monkeypatch, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    import web_panel
    from queue_manager import JobQueue
    monkeypatch.setattr(config, "WEB_PASSWORD", "pw")
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "k"))
    q = JobQueue(db_path=str(tmp_path / "q.db"))
    j = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="", status="done",
            final_segments=[{"i": 0, "start": 0.0, "end": 2.0, "ost": "new", "text": "a", "orig": "b", "voice": None}],
            srt="1\n00:00:00,000 --> 00:00:01,000\na\n")
    q.jobs[j.id] = j

    async def go():
        async with TestClient(TestServer(web_panel.build_app(q, {}))) as c:
            assert (await c.get(f"/api/jobs/{j.id}/timeline.json")).status == 401
            h = {"X-Auth": "pw"}
            r = await c.get(f"/api/jobs/{j.id}/timeline.json", headers=h)
            assert r.status == 200 and (await r.json())["segments"][0]["text"] == "a"
            assert (await (await c.get(f"/api/jobs/{j.id}/subs.srt", headers=h)).text()).startswith("1\n")
            r = await c.get("/api/events", headers=h)
            assert r.headers["Content-Type"].startswith("text/event-stream")
            line = await asyncio.wait_for(r.content.readline(), 5)
            assert line.startswith(b"data: ") and json.loads(line[6:])["jobs"][0]["timeline"]
            r.close()
    asyncio.run(go())


def test_pipeline_skips_tts_for_orig(tmp_path, monkeypatch):
    """Bản duyệt có đoạn giữ tiếng gốc: không đọc đoạn đó, timeline + SRT được lưu."""
    import cache
    monkeypatch.setattr(cache, "DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "TTS_PROVIDER", "capcut")
    monkeypatch.setattr(config, "BRAND_INTRO", "0")
    monkeypatch.setattr(config, "QUALITY", "fast")
    calls = []

    async def fake_synth(text, voice, path):
        calls.append(text)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=500:d=1", "-ar", "24000", path],
                       check=True)
        return None, "capcut"

    async def fake_tr(audio):
        return "", []
    monkeypatch.setattr(core_video, "synth_clip_ex", fake_synth)
    monkeypatch.setattr(pipeline, "transcribe_segments", fake_tr)
    src = str(tmp_path / "in.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=6:s=320x568:r=25", "-f", "lavfi",
                    "-i", "sine=f=800:d=6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src],
                   check=True)
    job = Job(url="", mode="review", duration=0, voice=config.CAPCUT_VOICE_FEMALE, local=src,
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "span": 2.0, "text": "Mở đầu nè", "ost": "new"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "span": 2.0, "text": "", "ost": "orig"},
                        {"i": 2, "start": 4.0, "slot": 2.0, "span": 2.0, "text": "Kết thúc nha", "ost": "both"}])
    (tmp_path / "w").mkdir()
    out = asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert os.path.exists(out) and calls == ["Mở đầu nè", "Kết thúc nha"]
    assert [s["ost"] for s in job.final_segments] == ["new", "orig", "both"] and job.final_segments[1]["voice"] is None
    assert "Mở" in job.srt and "Kết" in job.srt


def test_voice_never_enters_orig_window():
    """Review đợt 2: đoạn sau không được mượn lùi vào cửa sổ giữ tiếng gốc, đoạn trước không được lấn sang."""
    segs = [{"start": 0.0, "slot": 3.0, "span": 3.0}, {"start": 3.0, "slot": 3.0, "ost": "orig"},
            {"start": 6.0, "slot": 3.0, "span": 3.0}]
    plan, too_long = core_video.plan_voice([3.6, 0.0, 3.4], segs, 9.5)
    assert plan[0]["end"] <= 3.0 + 1e-6 or 0 in too_long
    assert plan[2]["start"] >= 6.0 - 1e-6
    expr = core_video._ost_level_expr([(3.0, 6.0, 1.0)])
    assert "(t-3.00)" in expr and "(6.00-t)" in expr        # vuốt nằm trong cửa sổ


def test_switching_ai_orig_back_needs_text():
    src = Job(url="https://youtu.be/x", mode="cook", duration=0, voice="", status="review",
              approved=[{"i": 0, "start": 0.0, "slot": 2.0, "text": "Một", "ost": "new"},
                        {"i": 1, "start": 2.0, "slot": 2.0, "text": "", "orig": "ui da", "ost": "orig"}])
    state.queue.jobs[src.id] = src
    with pytest.raises(ValueError, match="Đoạn 1"):
        review.approve_job(src, {1: {"ost": "new"}})
    assert src.status == "review"
    new = review.approve_job(src, {1: {"ost": "new", "text": "Nóng quá trời nè"}})
    assert [s["ost"] for s in new.approved] == ["new", "new"]


def test_review_reply_nfd():
    import unicodedata
    assert review.parse_review_reply(unicodedata.normalize("NFD", "2: gốc")) == {2: {"ost": "orig"}}
