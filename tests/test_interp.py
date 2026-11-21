"""1.2.0: nội suy khung RIFE khi video gốc bị kéo chậm (Kaggle GPU); không GPU / tắt / lỗi thì dựng như cũ."""
import os
import subprocess
import sys
import types

import numpy as np
import pytest

import config
from rmvideo.media import filtergraph, interp


def test_cut_detection():
    a = np.zeros((64, 64, 3), np.uint8)
    assert not interp.is_cut(a, a + 10)                       # chuyển động nhẹ / sáng hơn chút: vẫn nội suy
    assert interp.is_cut(a, a + 200)                          # chuyển cảnh: chép khung trước


def test_output_fps_unchanged_after_doubling():
    """Gấp đôi khung không đổi tốc độ khung đầu ra (out_fps chia đều): 30->60->30, 25->50->25, 24->48->24."""
    for src in (30.0, 25.0, 24.0, 29.97):
        assert filtergraph.out_fps(src * 2) == filtergraph.out_fps(src)


def test_smooth_only_when_slowed_enabled_and_gpu(monkeypatch, tmp_path):
    calls = []

    def fake_double(src, dst, models_dir, device="cuda", log=None, **k):
        calls.append((src, dst, device))
        open(dst, "wb").write(b"x")
        return {"frames": 2}
    monkeypatch.setattr(interp, "double_fps", fake_double)
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(config, "INTERP", "1", raising=False)
    assert interp.smooth_for_slowdown("in.mp4", str(tmp_path), 1.05, "m") == "in.mp4"      # chậm ít: bỏ qua
    assert interp.smooth_for_slowdown("in.mp4", str(tmp_path), 1.3, "m") == str(tmp_path / "smooth.mp4")
    monkeypatch.setattr(config, "INTERP", "0")
    assert interp.smooth_for_slowdown("in.mp4", str(tmp_path), 1.3, "m") == "in.mp4"
    monkeypatch.setattr(config, "INTERP", "1")
    fake_torch.cuda.is_available = lambda: False
    assert interp.smooth_for_slowdown("in.mp4", str(tmp_path), 1.3, "m") == "in.mp4"       # Botkeep / không GPU
    fake_torch.cuda.is_available = lambda: True

    def slow(*a, **k):
        raise TimeoutError("dự tính 900s > 300s")
    monkeypatch.setattr(interp, "double_fps", slow)
    assert interp.smooth_for_slowdown("in.mp4", str(tmp_path), 1.3, "m") == "in.mp4"
    assert len(calls) == 1


def test_double_fps_pipeline(tmp_path):
    """Đường ống thật (ffmpeg giải mã -> khung giữa -> ffmpeg mã hoá + chép tiếng), khung giữa thay bằng trộn đơn giản."""
    pytest.importorskip("torch")
    src = str(tmp_path / "c.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x160:r=30:d=1", "-f", "lavfi",
                    "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", src], check=True)
    orig_load, orig_mid = interp.load, interp.middle
    interp.load = lambda d, device="cuda": object()
    interp.middle = lambda net, a, b, device="cuda": ((a.astype(np.uint16) + b) // 2).astype(np.uint8)
    try:
        info = interp.double_fps(src, str(tmp_path / "d.mp4"), str(tmp_path), "cpu")
    finally:
        interp.load, interp.middle = orig_load, orig_mid
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,avg_frame_rate", "-of", "csv=p=0",
                          str(tmp_path / "d.mp4")], capture_output=True, text=True).stdout
    assert info["frames"] == 60 and "video,60/1" in out and "audio" in out and os.path.getsize(tmp_path / "d.mp4") > 0
