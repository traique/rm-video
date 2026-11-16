"""Tăng tốc Kaggle: mã hoá bằng GPU khi có (h264_nvenc), tách giọng một lần cho mọi việc cùng video trong một phiên."""
import asyncio
import os
import subprocess

import kaggle_job
from rmvideo.media import renderer


def _reset(monkeypatch):
    monkeypatch.setattr(renderer, "_NVENC", {})
    monkeypatch.delenv("RM_NVENC", raising=False)


def test_no_gpu_keeps_x264(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert renderer.nvenc() is None
    args = renderer.encode_args(1920)
    assert args[args.index("-c:v") + 1] == "libx264" and "-crf" in args


def test_gpu_uses_nvenc_and_falls_back_to_old_preset_names(monkeypatch):
    _reset(monkeypatch)
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/dev/nvidia0" or real_exists(p))
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        ok = "p5" not in cmd                       # FFmpeg cũ: không biết preset p5 -> thử "slow"
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, b"", b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert renderer.nvenc() == ["-preset", "slow"] and len(seen) == 2
    assert renderer.nvenc() == ["-preset", "slow"] and len(seen) == 2          # chỉ thử một lần mỗi tiến trình
    args = renderer.encode_args(1920)
    i = args.index("-c:v")
    assert args[i + 1] == "h264_nvenc" and args[i + 2: i + 4] == ["-preset", "slow"] and "-cq" in args
    assert "-maxrate" in args and "-pix_fmt" in args and "libx264" not in args
    _reset(monkeypatch)
    monkeypatch.setenv("RM_NVENC", "0")
    assert renderer.nvenc() is None


def test_batch_separates_each_video_once(monkeypatch, tmp_path):
    """Bản đầy đủ + bản ngắn cùng video trong một phiên Kaggle: việc thứ hai dùng lại tiếng nền việc đầu đã tách."""
    import vocal_sep
    from rmvideo.app import assemble as asm
    monkeypatch.setattr(kaggle_job, "_SEP_DONE", {})
    calls = []

    async def remove_voice(inp, out, windows=None):
        calls.append(inp)
        open(out, "wb").write(b"RIFF")
        return True
    monkeypatch.setattr(vocal_sep, "remove_voice", remove_voice)
    got = []

    async def fake_assemble(inp, tmp, **kw):
        amb, gate = await kw["ambient"]()
        got.append(amb)
        return asm.Assembled(os.path.join(tmp, "o.mp4"), [], [], "", amb, [], [], {})
    monkeypatch.setattr(asm, "assemble", fake_assemble)
    spec = {"mode": "cook", "sep": {"ok": True}, "src": {"url": "https://x/v"}, "segments": [], "out_dur": 10}
    for k in range(2):
        d = tmp_path / f"j{k}"
        d.mkdir()
        asyncio.run(kaggle_job.produce(spec, str(d / "src.mp4"), str(d), None))
    assert len(calls) == 1 and got[0] == got[1] and got[0].endswith("ambient.wav")
