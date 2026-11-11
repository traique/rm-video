"""Bộ video mẫu (golden.py): đo video ra, so với chuẩn theo ngưỡng, báo cáo."""
import asyncio
import json
import os
import subprocess

import golden


def _ok(**kw):
    r = {"name": "a.mp4", "ok": True, "seconds": 100.0, "lufs": -14.0, "true_peak": -1.5, "black_ratio": 0.0,
         "sharp": -6.0, "color": 0.2, "duration": 60.0, "ingredient_errors": 0, "copy_ratio": 0.1, "max_speed": 1.1,
         "too_long": 0}
    r.update(kw)
    return r


def test_video_metrics_samples_few_frames(tmp_path, monkeypatch):
    import cover
    seen = {}
    real = cover._raw_frames

    def spy(args, pre_vf=""):
        seen["args"], seen["pre"] = args, pre_vf
        return real(args, pre_vf)
    monkeypatch.setattr(cover, "_raw_frames", spy)
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=10", "-c:v", "libx264",
                    "-preset", "ultrafast", str(v)], check=True)
    m = golden.video_metrics(str(v), n=6)
    assert "-vf" not in seen["args"] and seen["pre"].startswith("fps=")       # một -vf duy nhất: chỉ ~6 khung
    assert m["width"] == 180 and abs(m["duration"] - 10) < 0.2


def test_compare_flags_regressions():
    base = [_ok()]
    assert [x[4] for x in golden.compare([_ok()], base)] == ["ok"]
    rows = golden.compare([_ok(sharp=-6.5, ingredient_errors=2, lufs=-15.2, seconds=140.0, copy_ratio=0.3)], base)
    got = {(x[1], x[4]) for x in rows}
    assert ("sharp", "fail") in got and ("ingredient_errors", "fail") in got and ("lufs", "fail") in got
    assert ("seconds", "warn") in got and ("copy_ratio", "warn") in got
    assert ("sharp", "warn") not in got                         # đã là lỗi thì không báo thêm cảnh báo
    rows = golden.compare([{"name": "a.mp4", "ok": False, "error": "boom"}], base)
    assert rows[0][4] == "fail" and rows[0][3] == "LỖI"
    # chưa có chuẩn: chỉ ngưỡng tuyệt đối
    assert [x[4] for x in golden.compare([_ok()], None)] == ["ok"]
    assert ("true_peak", "fail") in {(x[1], x[4]) for x in golden.compare([_ok(true_peak=-0.2)], None)}


def test_script_metrics():
    from queue_manager import Job
    j = Job(url="", mode="cook", duration=0, voice="v")
    j.final_segments = [
        {"orig": "cho 2 muỗng nước mắm vào thịt heo", "text": "Thêm 2 muỗng nước mắm cho thịt heo nha.", "ost": "new",
         "voice": {"speed": 1.05}},
        {"orig": "rắc tiêu lên", "text": "Rắc chút hành lên.", "ost": "new", "voice": {"speed": 1.2}},
        {"orig": "xèo xèo", "text": "", "ost": "orig", "voice": None}]
    j.srt = "1\n00:00:00,000 --> 00:00:01,000\nThêm 2 muỗng\n\n2\n00:00:01,000 --> 00:00:02,000\nnước mắm cho thịt heo\n"
    j.error = "ℹ️ 3 đoạn giọng; 1 đoạn vẫn hơi dài."
    m = golden.script_metrics(j)
    assert m["spoken"] == 2 and m["fast_segments"] == 1 and m["max_speed"] == 1.2 and m["too_long"] == 1
    assert m["ingredient_errors"] == 2 and "tiêu" in m["ingredient_detail"]["thiếu"]   # mất tiêu, bịa hành
    assert m["sub_lines"] == 2 and m["sub_max_chars"] == len("nước mắm cho thịt heo")


def test_run_and_report(tmp_path, monkeypatch):
    import pipeline
    monkeypatch.setattr(golden, "GOLDEN", str(tmp_path))
    os.makedirs(tmp_path / "videos")
    src = tmp_path / "videos" / "mon.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=6", "-f", "lavfi",
                    "-i", "sine=f=300:d=6", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    (tmp_path / "manifest.json").write_text(json.dumps({"mon.mp4": {"mode": "review", "title": "Món"}}))

    async def fake_process(job, tmp, allow_review=True):
        assert job.mode == "review" and job.title_hint == "Món" and not allow_review
        job.step = "Render (FFmpeg)"
        out = os.path.join(tmp, "output.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", job.local, "-af", "loudnorm=I=-14:TP=-2", "-ar", "48000",
                        "-c:v", "libx264", "-preset", "ultrafast", out], check=True)
        return out
    monkeypatch.setattr(pipeline, "process", fake_process)
    res = asyncio.run(golden.run_all())
    d = json.load(open(res))["results"][0]
    assert d["ok"] and abs(d["lufs"] + 14) < 1.0 and d["width"] == 360 and abs(d["duration"] - 6) < 0.2
    assert os.path.exists(tmp_path / "out" / "mon_khung.jpg")
    text, fails = golden.report(res, str(tmp_path / "baseline.json"))
    assert fails == 0 and "mon.mp4" in text and (tmp_path / "report.md").exists()
