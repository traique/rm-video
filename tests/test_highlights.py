"""Chế độ ✨ Đoạn hay: chuẩn hoá kết quả AI, dời ranh giới, và luồng cắt + remake (có dự phòng cắt đều)."""
import asyncio
import os
import subprocess

import config
import highlights
import pipeline


def test_transcript_lines_merge():
    segs = [(0.0, 2.0, "a"), (2.1, 4.0, "b"), (4.2, 9.5, "c"), (12.0, 13.0, "d")]
    assert highlights.transcript_lines(segs) == [(0.0, 4.0, "a b"), (4.2, 9.5, "c"), (12.0, 13.0, "d")]


def test_snap_boundary_prefers_cut_in_silence():
    cuts, sil = [10.5, 12.2], [(11.8, 12.6), (8.0, 8.4)]
    assert highlights.snap_boundary(11.0, cuts, sil) == 12.2          # chuyển cảnh nằm trong khoảng lặng
    assert highlights.snap_boundary(11.0, cuts, sil, limit=12.0) == 8.2      # ≤ 12: khoảng lặng hơn chuyển cảnh trơn
    assert highlights.snap_boundary(30.0, cuts, sil) == 30.0          # không có gì gần: giữ nguyên


def test_normalize_filters_and_orders():
    clips = [
        {"start": 10, "end": 70, "score": 7, "title": "A", "reason": "r"},
        {"start": 60, "end": 120, "score": 9, "title": "B", "reason": "r"},     # chồng lên A, điểm cao hơn
        {"start": 200, "end": 210, "score": 10, "title": "ngắn", "reason": ""},  # quá ngắn
        {"start": 300, "end": 500, "score": 10, "title": "dài", "reason": ""},   # quá dài
        {"start": "x", "end": 5},                                                # sai kiểu
        {"start": 130, "end": 240, "score": 6, "title": "C", "reason": ""},     # hơi dài: thu về giới hạn
        {"start": 400, "end": 450, "score": 5, "title": "D", "reason": ""},
    ]
    out = highlights.normalize(clips, 600.0, [], [], n=3)
    assert [c["title"] for c in out] == ["B", "C", "D"]
    assert out[1]["end"] - out[1]["start"] <= config.MAX_DURATION - 1.5
    assert all(30 * 0.7 <= c["end"] - c["start"] <= 90 for c in out)


def test_choose_needs_speech_and_parses(monkeypatch):
    import prompt_engine
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())
    seen = {}

    async def fake(prompt, images=None, schema=None, label="", timeout=60):
        seen["prompt"], seen["images"] = prompt, images
        return '{"clips": [{"start": 20, "end": 75, "score": 8, "title": "Cắt thịt ra", "reason": "thịt mềm"}]}'
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    segs = [(float(t), t + 4.0, f"câu {t}") for t in range(0, 200, 5)]
    out = asyncio.run(highlights.choose("Thịt kho", "", 200.0, segs, [21.0], [(74.5, 75.6)], [b"img"], [5.0]))
    assert out and out[0]["start"] == 21.0 and out[0]["end"] == 75.05
    assert "[0.0-" in seen["prompt"] and "Ảnh 0 = giây 5" in seen["prompt"] and seen["images"] == [b"img"]
    # video gần như không lời: không gọi AI
    seen.clear()
    assert asyncio.run(highlights.choose("x", "", 600.0, segs[:2], [], [])) == [] and not seen


def _src(tmp_path, d=40):
    p = str(tmp_path / "long.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=d={d}:s=320x240:r=25", "-f", "lavfi",
                    "-i", f"sine=f=300:d={d}", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac",
                    "-shortest", p], check=True)
    return p


def _fakes(monkeypatch, tmp_path, clips):
    calls = {"process": [], "deliver": []}

    async def fake_tr(audio, *a, **k):
        return "x", [(float(t), t + 3.0, "lời") for t in range(0, 40, 4)]

    async def fake_choose(*a, **k):
        return clips

    async def fake_process(sub, part_dir, allow_review=True):
        calls["process"].append(sub.title_hint)
        out = os.path.join(part_dir, "output.mp4")
        open(out, "wb").write(b"v")
        sub.post = "📌 Tiêu đề\ncaption"
        return out

    async def fake_deliver(job, out, label, caption):
        calls["deliver"].append((label, caption))
        job.parts.append({"label": label, "path": out, "token": "t"})
    monkeypatch.setattr(pipeline, "transcribe_segments", fake_tr)
    monkeypatch.setattr(highlights, "choose", fake_choose)
    monkeypatch.setattr(pipeline, "process", fake_process)
    monkeypatch.setattr(pipeline, "deliver", fake_deliver)
    monkeypatch.setattr(config, "SPLIT_MAX", 600)
    return calls


def test_process_highlights_flow(monkeypatch, tmp_path):
    from queue_manager import Job
    clips = [{"start": 20.0, "end": 38.0, "score": 9, "title": "Món chín", "reason": "đẹp"},
             {"start": 2.3, "end": 15.8, "score": 6, "title": "Sơ chế", "reason": ""}]   # lệch khung khoá 0.3s
    calls = _fakes(monkeypatch, tmp_path, clips)
    job = Job(url="", mode="cook", duration=0, voice="v", long_mode="highlight", local=_src(tmp_path), source="web")
    asyncio.run(pipeline.process_highlights(job, str(tmp_path)))
    assert [lb for lb, _ in calls["deliver"]] == ["Đoạn hay 1/2", "Đoạn hay 2/2"]
    assert calls["deliver"][0][1].startswith("⭐ 9/10 · Món chín")
    assert calls["process"][0].startswith("Món chín")
    assert job.pieces[0]["a"] == 20.0 and "đoạn hay" in job.script
    assert (job.pieces[1]["a"], job.pieces[1]["b"]) == (2.0, 16.0)      # cả hai đầu bám khung khoá (g=25: mỗi 1s)
    # chạy lại (bot khởi động lại): giữ đúng các đoạn đã chọn, bỏ qua đoạn đã giao
    calls["deliver"].clear()
    asyncio.run(pipeline.process_highlights(job, str(tmp_path)))
    assert calls["deliver"] == []


def test_process_highlights_falls_back_to_even_split(monkeypatch, tmp_path):
    from queue_manager import Job
    calls = _fakes(monkeypatch, tmp_path, [])
    job = Job(url="", mode="cook", duration=0, voice="v", long_mode="highlight", local=_src(tmp_path, 130),
              source="web")
    asyncio.run(pipeline.process_highlights(job, str(tmp_path)))
    labels = [lb for lb, _ in calls["deliver"]]
    assert labels and all(lb.startswith("Phần ") for lb in labels)
    assert "Không chọn được đoạn hay" in job.error
