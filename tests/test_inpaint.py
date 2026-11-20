"""LaMa xoá chữ in cứng (rmvideo/media/inpaint.py): vùng cắt/mặt nạ, luồng khung ffmpeg -> model -> ffmpeg (model giả,
không cần torch), mặt nạ theo chữ thật + giữ khung trước/sau + trộn nền đứng yên, tìm model trong nguồn gắn vào, dải
còn phải che (remake.uncovered)."""
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
    info = inpaint.clean_video(src, [(0.70, 0.76)], out, log=logs.append, model=object(), batch=4, nvenc=False,
                               mask="band")                         # vạch trắng đặc không phải chữ: mặt nạ cả dải như cũ
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
        inpaint.clean_video(src, [], out, model=object(), nvenc=False, mask="band")
    # khung không có chữ: giữ nguyên, không vẽ lại (phụ đề tạm tắt, hay dải dò nhầm)
    plain = str(tmp_path / "plain.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x2060a0:s=160x288:r=10:d=1", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", plain], check=True)
    calls = []
    monkeypatch.setattr(inpaint, "apply", lambda m, c, k: calls.append(c.shape) or _fake_apply(m, c, k))
    info2 = inpaint.clean_video(plain, [(0.70, 0.76)], str(tmp_path / "plain_clean.mp4"), model=object(), nvenc=False,
                                mask="band")
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
    info = inpaint.clean_video(rot, [(0.70, 0.76)], out, model=object(), batch=4, nvenc=False, mask="band")
    assert inpaint._probe(out)[:2] == (160, 288) and info["painted"] == 20
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "gray", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(288, 160)
    assert g[int(0.73 * 288), 80] < 140 and g[int(0.73 * 288), 2] > 200


def test_text_boxes_keep_strokes_drop_edges():
    t = np.zeros((60, 300), bool)
    t[20:40, 50:150:6] = t[20:40, 51:150:6] = True               # dòng chữ: nét dọc dày 2px cách nhau 6px
    t[:, 250] = True                                             # mép vật sáng trên nền tối: mỗi hàng 1 nét
    t[5:7, 200:204] = True                                       # chấm nhỏ thấp hơn 1% chiều cao khung
    assert inpaint.text_boxes(t, 600) == [(20, 40, 50, 148)]
    assert inpaint.text_boxes(np.zeros((60, 300), bool), 600) == []


def test_held_hysteresis_static_only():
    B, C = (10, 30, 40, 200), (5, 25, 300, 340)
    dets = {i: [B] for i in range(10, 21)}
    dets[3] = [C]                                                # thoáng một khung: không giữ
    assert inpaint.held(dets, 3) == []
    on = [i for i in range(30) if inpaint.held(dets, i)]
    assert on == list(range(10 - inpaint.HOLD, 21 + inpaint.HOLD))   # giữ HOLD khung trước khi chữ hiện và sau khi tắt
    assert {b for _, b in inpaint.held(dets, 15)} == {B} and inpaint.held(dets, 15, hold=0) == [(15, B)]
    moving = {i: [(10, 30, 20 * i, 20 * i + 15)] for i in range(10)}    # mép tay chạy ngang: hộp không trùng khung kề
    assert not any(inpaint.held(moving, i) for i in range(10))


def test_grow_matches_naive():
    m = np.random.default_rng(1).random((40, 70)) > 0.97
    naive = np.zeros_like(m)
    for y, x in zip(*np.nonzero(m)):
        naive[max(0, y - 3):y + 4, max(0, x - 3):x + 4] = True
    assert (inpaint._grow(m, 3) == naive).all() and not inpaint._grow(np.zeros((5, 5), bool), 2).any()


def test_steady_blends_only_static_background():
    crop = np.full((40, 121, 3), 100, np.uint8)
    m = np.zeros((40, 121), bool)
    m[15:25, 20:100] = True
    prev_out = crop.copy()
    prev_out[m] = 200                                            # khung trước LaMa vẽ 200, khung này vẽ 100
    out = crop.copy()
    res = inpaint._steady(out, crop, m, (crop, prev_out, m), 1000)
    assert res[20, 60, 0] == round(100 + inpaint.BLEND * 100) and (res[~m] == 100).all()      # nền đứng yên: trộn
    moved = crop.copy()
    moved[:5], moved[35:] = 250, 0                               # nền quanh đổi hẳn (tay đi qua): giữ LaMa khung này
    assert (inpaint._steady(out, moved, m, (crop, prev_out, m), 1000) == out).all()
    assert inpaint._steady(out, crop, m, None, 1000) is out


def test_band_mask_follows_text_past_band_edge():
    """Phụ đề 2 dòng tràn ra ngoài dải textzone: cụm chữ nằm ≥ 1/3 trong dải được xoá trọn cả phần trong ngữ cảnh; dòng
    chữ khác nằm hẳn trong ngữ cảnh thì giữ nguyên."""
    W, H = 320, 576
    r = inpaint.regions([(0.70, 0.76)], W, H)[0]
    frames = np.full((6, H, W, 3), 120, np.uint8)

    def line(y):                                                 # "dòng chữ": nét trắng 2px trên nền viền đen
        frames[:, y:y + 14, 60:260] = 0
        for x in range(62, 258, 6):
            frames[:, y + 2:y + 12, x:x + 2] = 255
    line(r["my0"] - 7)                                           # nửa trên dòng nằm ngoài dải
    line(r["cy0"] + 18)                                          # dòng khác hẳn, chỉ trong phần ngữ cảnh
    b = inpaint._Band(r, W, H, "text")
    b.detect(frames, 0)
    m = b.mask(2)
    a = r["my0"] - r["cy0"]
    assert m is not None and m[a - 6, 100] and not b.rect[a - 6, 100]       # lấn ra ngoài dải theo chữ
    assert not m[:30].any() and not m[a + 20:].any()                        # dòng ngữ cảnh, phần dải không chữ: để nguyên


def _green_apply(model, crops, mask):
    """Thay LaMa: tô xanh lá chỗ mặt nạ ((h, w) hay (B, h, w)) cho dễ thấy chỗ nào bị vẽ lại."""
    out = crops.copy()
    out[np.broadcast_to(mask > 0, crops.shape[:3])] = (0, 255, 0)
    return out


def test_clean_video_text_mask_keeps_rest_of_band(tmp_path, monkeypatch):
    """Mặt nạ mới: chỉ chỗ có chữ thật bị vẽ lại (vật cam trong dải giữ nguyên), giữ HOLD khung trước/sau chữ; mask=band
    như cũ tô cả dải."""
    font = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "DejaVuSans-Bold.ttf")
    src = str(tmp_path / "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x806040:s=320x576:r=10:d=2", "-vf",
                    "drawbox=x=14:y=400:w=40:h=40:color=0xff8000:t=fill,"
                    f"drawtext=fontfile={font}:text='BEP NGON':fontsize=30:fontcolor=white:borderw=3:bordercolor=black:"
                    "x=(w-text_w)/2:y=h*0.71:enable='between(t,0.8,1.6)'", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", src], check=True)
    monkeypatch.setattr(inpaint, "apply", _green_apply)

    def green(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        f = np.frombuffer(raw, np.uint8).reshape(-1, 576, 320, 3).astype(int)
        return (f[..., 1] > 200) & (f[..., 0] < 80) & (f[..., 2] < 80)

    out = str(tmp_path / "text.mp4")
    info = inpaint.clean_video(src, [(0.70, 0.76)], out, model=object(), batch=4, nvenc=False)
    g = green(out)
    on = [i for i in range(len(g)) if g[i].sum() > 200]
    assert info["mask"] == "text" and on == list(range(8 - inpaint.HOLD, 17 + inpaint.HOLD)) == list(range(5, 20))
    assert info["painted"] == 15 and not g[12, 400:440, 14:54].any()      # vật cam trong dải: giữ nguyên
    assert g[12, 400:445, 110:210].mean() > 0.5                           # chữ: vẽ lại
    band = str(tmp_path / "band.mp4")
    info = inpaint.clean_video(src, [(0.70, 0.76)], band, model=object(), batch=4, nvenc=False, mask="band")
    gb = green(band)
    assert info["painted"] == 9 and gb[12, 405:435, 20:45].all()           # cũ: cả dải, chỉ khung thấy chữ


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
