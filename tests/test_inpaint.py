"""LaMa xoá chữ in cứng (rmvideo/media/inpaint.py): vùng cắt/mặt nạ, luồng khung ffmpeg -> model -> ffmpeg (model giả,
không cần torch), tìm model trong nguồn gắn vào, dải còn phải che (remake.uncovered)."""
import os
import subprocess

import numpy as np
import pytest

from rmvideo.media import inpaint


def test_regions_merge_and_align():
    W, H = 720, 1280
    r = inpaint.regions([(0.70, 0.76)], W, H)
    assert len(r) == 1
    g = r[0]
    assert g["my0"] == int((0.70 - inpaint.MARGIN) * H) and g["my1"] == int((0.76 + inpaint.MARGIN) * H) + 1
    assert g["cy0"] <= g["my0"] - inpaint.CONTEXT and g["cy1"] >= g["my1"] and (g["cy1"] - g["cy0"]) % 8 == 0
    assert g["x0"] == int(inpaint.SIDE[0] * W) and g["x1"] == int(inpaint.SIDE[1] * W)
    two = inpaint.regions([(0.10, 0.14), (0.15, 0.18), (0.80, 0.85)], W, H)        # hai dải sát nhau gộp một
    assert len(two) == 2 and two[0]["my0"] < two[0]["my1"] and two[1]["my0"] > two[0]["my1"]
    edge = inpaint.regions([(0.97, 1.0)], W, H)                                     # sát mép: không vượt khung
    assert edge[0]["cy1"] == H and edge[0]["cy0"] >= 0 and (edge[0]["cy1"] - edge[0]["cy0"]) % 8 == 0
    assert inpaint.regions([], W, H) == []


def _video(path: str, w=160, h=288, secs=2, fps=10):
    """Nền xanh có dải chữ trắng ngang ở 70-76% chiều cao, có tiếng."""
    y0, y1 = int(0.70 * h), int(0.76 * h)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x2060a0:s={w}x{h}:r={fps}:d={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", "-vf",
                    f"drawbox=x=0:y={y0}:w={w}:h={y1 - y0}:color=white:t=fill", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path], check=True)
    return path


def _fake_apply(model, crops, mask):
    """Thay LaMa: tô vùng mặt nạ bằng màu trung bình của các hàng ngữ cảnh (không có chữ)."""
    out = crops.copy()
    rows = np.where(mask.max(axis=1) > 0)[0]
    ctx = np.concatenate([crops[:, :rows[0]], crops[:, rows[-1] + 1:]], axis=1)
    fill = ctx.reshape(crops.shape[0], -1, 3).mean(axis=1).round().astype(np.uint8)
    for b in range(crops.shape[0]):
        out[b][mask > 0] = fill[b]
    return out


def test_clean_video_removes_band(tmp_path, monkeypatch):
    src = _video(str(tmp_path / "src.mp4"))
    out = str(tmp_path / "clean.mp4")
    monkeypatch.setattr(inpaint, "apply", _fake_apply)
    logs = []
    info = inpaint.clean_video(src, [(0.70, 0.76)], out, log=logs.append, model=object(), batch=4, nvenc=False)
    assert info["frames"] == 20 and len(info["regions"]) == 1 and os.path.getsize(out) > 10_000
    W, H, fps, has_audio = inpaint._probe(out)
    assert (W, H, has_audio) == (160, 288, True) and abs(fps - 10) < 0.01
    # dải chữ không còn trắng: lấy một khung giữa, đo độ sáng ở giữa dải
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "gray", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(288, 160)
    assert g[int(0.73 * 288), 80] < 140 and g[int(0.73 * 288), 2] > 200    # giữa: nền đã vẽ lại; mép (3%): để nguyên
    assert any("LaMa xong" in x for x in logs) and info["painted"] == 20
    with pytest.raises(ValueError):
        inpaint.clean_video(src, [], out, model=object(), nvenc=False)
    # khung không có chữ: giữ nguyên, không vẽ lại (phụ đề tạm tắt, hay dải dò nhầm)
    plain = str(tmp_path / "plain.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x2060a0:s=160x288:r=10:d=1", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", plain], check=True)
    calls = []
    monkeypatch.setattr(inpaint, "apply", lambda m, c, k: calls.append(c.shape) or _fake_apply(m, c, k))
    info2 = inpaint.clean_video(plain, [(0.70, 0.76)], str(tmp_path / "plain_clean.mp4"), model=object(), nvenc=False)
    assert info2["frames"] == 10 and info2["painted"] == 0 and calls == []


def test_clean_video_rotated_phone_video(tmp_path, monkeypatch):
    """Video quay dọc có cờ xoay 90° (khung lưu 288x160): FFmpeg tự xoay khi giải mã -> _probe phải báo khung hiển thị 160x288,
    video sạch ra đúng dọc, dải chữ ở đúng chỗ (nhận xét Codex PR #13)."""
    src = _video(str(tmp_path / "src.mp4"))                                       # 160x288, chữ trắng ở 70-76% chiều cao
    land = str(tmp_path / "land.mp4")                                             # lưu nằm ngang + cờ xoay -> hiển thị dọc
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", "transpose=1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", land], check=True)
    rot = str(tmp_path / "rot.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-display_rotation", "90", "-i", land, "-c", "copy", rot], check=True)
    assert inpaint._probe(rot)[:2] == (160, 288)
    monkeypatch.setattr(inpaint, "apply", _fake_apply)
    out = str(tmp_path / "clean.mp4")
    info = inpaint.clean_video(rot, [(0.70, 0.76)], out, model=object(), batch=4, nvenc=False)
    assert inpaint._probe(out)[:2] == (160, 288) and info["painted"] == 20
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "gray", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(288, 160)
    assert g[int(0.73 * 288), 80] < 140 and g[int(0.73 * 288), 2] > 200


def test_model_path_prefers_mounted(tmp_path, monkeypatch):
    root = tmp_path / "input" / "datasets" / "u" / "lama"
    root.mkdir(parents=True)
    (root / "big-lama.pt").write_bytes(b"x" * 64)
    monkeypatch.setattr(inpaint, "MODEL_MIN_BYTES", 32)
    assert inpaint.model_path(str(tmp_path / "input")) == str(root / "big-lama.pt")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "big-lama.pt").write_bytes(b"y" * 64)
    assert inpaint.model_path(str(tmp_path / "nope"), str(cache)) == str(cache / "big-lama.pt")


def test_uncovered_bands():
    from rmvideo.app.flow.remake import uncovered
    assert uncovered([(0.7, 0.76), (0.1, 0.15)], [(0.69, 0.77)]) == [(0.1, 0.15)]
    assert uncovered([(0.7, 0.76)], []) == [(0.7, 0.76)]
    assert uncovered([(0.7, 0.76)], [(0.74, 0.9)]) == [(0.7, 0.76)]            # chồng ít -> vẫn che
