"""1.2.0: ảnh truyện bé phóng bằng Real-ESRGAN (GPU) kèm bản HD cho Ken Burns; không GPU / lỗi thì lanczos như cũ."""
import os

import pytest
from PIL import Image

from rmvideo.kids import images


def test_lanczos_fallback_without_gpu_and_render_paths(monkeypatch, tmp_path):
    src = tmp_path / "raw_01.png"
    Image.new("RGB", (768, 1344), (200, 120, 90)).save(src)

    def no_gpu(*a, **k):
        return False
    monkeypatch.setattr(images, "upscale_esrgan", no_gpu)
    dst = images.upscale(str(src), str(tmp_path / "scene_01.png"))
    assert Image.open(dst).size == (1080, 1920) and not os.path.exists(images.hd_path(dst))
    assert images.for_render([dst]) == [dst]

    def broken(*a, **k):
        open(images.hd_path(a[1]), "wb").write(b"half")
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(images, "upscale_esrgan", broken)
    dst = images.upscale(str(src), str(tmp_path / "scene_02.png"))
    assert Image.open(dst).size == (1080, 1920) and not os.path.exists(images.hd_path(dst))   # file dở bị xoá


def test_esrgan_writes_frame_and_hd(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    from rmvideo.kids import esrgan
    src = tmp_path / "raw_01.png"
    Image.new("RGB", (768, 1344), (200, 120, 90)).save(src)
    import config
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(config, "IMG_UPSCALE", "esrgan")              # 1.3.5: mặc định tắt
    seen = []

    def fake(img, models_dir, device="cuda"):
        seen.append((img.size, device))
        return img.resize((img.width * 4, img.height * 4))
    monkeypatch.setattr(esrgan, "upscale", fake)
    dst = images.upscale(str(src), str(tmp_path / "scene_01.png"))
    assert seen == [((768, 1344), "cuda")]
    assert Image.open(dst).size == (1080, 1920) and Image.open(images.hd_path(dst)).size == (2160, 3840)
    assert images.for_render([dst]) == [images.hd_path(dst)]


def test_esrgan_net_shape_and_denoise_mix(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    from rmvideo.kids import esrgan
    net = esrgan._net()
    assert len(net.body) == 67 and net(torch.zeros(1, 3, 8, 6)).shape == (1, 3, 32, 24)
    a = {k: torch.ones_like(v) for k, v in net.state_dict().items()}
    b = {k: torch.zeros_like(v) for k, v in net.state_dict().items()}
    files = {}
    for which, sd in (("x4v3", a), ("wdn", b)):
        p = tmp_path / which
        torch.save({"params": sd}, p)
        files[which] = str(p)
    monkeypatch.setattr(esrgan, "model_path", lambda d, which: files[which])
    monkeypatch.setattr(esrgan, "_LOADED", {})
    m, dev = esrgan.load(str(tmp_path), "cpu", denoise=0.2)
    assert dev == "cpu" and abs(float(m.body[0].weight.flatten()[0]) - 0.2) < 1e-6        # 0,2·mạnh + 0,8·nhẹ


def test_esrgan_off_by_default(monkeypatch, tmp_path):
    """1.3.5: Real-ESRGAN mặc định tắt (ảnh «cảm giác nhựa»): không gọi model, phóng lanczos."""
    pytest.importorskip("torch")
    import config
    assert config.IMG_UPSCALE == "lanczos"
    src = tmp_path / "raw_01.png"
    Image.new("RGB", (768, 1344), (200, 120, 90)).save(src)
    assert images.upscale_esrgan(str(src), str(tmp_path / "scene_01.png")) is False
