"""(Kaggle GPU) Phóng nét ảnh truyện bé bằng Real-ESRGAN «realesr-general-x4v3» (1.2.0) thay cho phóng lanczos.

Ảnh FLUX 768x1344 -> x4 (3072x5376) -> thu về khung dựng. Model SRVGGNetCompact ~1,2 triệu tham số (file 4,9MB), giấy
phép BSD-3-Clause (xinntao/Real-ESRGAN; kiến trúc chép từ realesrgan/archs/srvgg_arch.py). Lỗi gì (không GPU, tải hỏng,
thiếu bộ nhớ) thì images.upscale quay về lanczos như cũ."""
from __future__ import annotations

import hashlib
import os
import urllib.request
from typing import Any

_REL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
# hai bản cùng kiến trúc: x4v3 khử nhiễu mạnh, wdn-x4v3 khử nhiễu nhẹ; trộn trọng số theo DENOISE (cách «-dn» của
# Real-ESRGAN). Đo 21/11 trên ảnh truyện bé: bản mạnh (1,0) làm lông nỉ của Thỏ / Gấu thành da nhăn bóng như nhựa, 0,2 giữ
# lớp lông mà viền, mắt, áo vẫn nét hơn phóng lanczos (độ nét Laplace 14 -> 96 ở bản mạnh).
MODELS = {"x4v3": ("realesr-general-x4v3.pth", "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292"),
          "wdn": ("realesr-general-wdn-x4v3.pth", "1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca")}
DENOISE = 0.2
SCALE = 4
_LOADED: dict[str, Any] = {}


def _net() -> Any:
    import torch
    from torch import nn

    class SRVGGNetCompact(nn.Module):
        """Real-ESRGAN (BSD-3-Clause, © Xintao Wang): conv + PReLU x num_conv, PixelShuffle, cộng ảnh phóng nearest."""

        def __init__(self, num_feat: int = 64, num_conv: int = 32, upscale: int = SCALE) -> None:
            super().__init__()
            self.upscale = upscale
            body: list = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            for _ in range(num_conv):
                body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
            self.body = nn.ModuleList(body)
            self.upsampler = nn.PixelShuffle(upscale)

        def forward(self, x: Any) -> Any:
            out = x
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + torch.nn.functional.interpolate(x, scale_factor=self.upscale, mode="nearest")
    return SRVGGNetCompact()


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def model_path(models_dir: str, which: str) -> str:
    name, sha = MODELS[which]
    os.makedirs(models_dir, exist_ok=True)
    p = os.path.join(models_dir, name)
    if os.path.exists(p) and _sha256(p) != sha:
        os.remove(p)
    if not os.path.exists(p):
        urllib.request.urlretrieve(_REL + name, p + ".part")
        if _sha256(p + ".part") != sha:
            os.remove(p + ".part")
            raise RuntimeError(f"model Real-ESRGAN {name} sai SHA-256")
        os.replace(p + ".part", p)
    return p


def load(models_dir: str, device: str = "cuda", denoise: float = DENOISE) -> Any:
    if "m" not in _LOADED:
        import torch

        def params(which: str) -> dict:
            st = torch.load(model_path(models_dir, which), map_location="cpu", weights_only=True)
            return st.get("params") or st.get("params_ema") or st
        a, b = params("x4v3"), params("wdn")
        net = _net()
        net.load_state_dict({k: denoise * a[k] + (1 - denoise) * b[k] for k in a})
        net = net.to(device).eval()
        if device == "cuda":
            net = net.half()
        _LOADED["m"] = (net, device)
    return _LOADED["m"]


def unload() -> None:
    _LOADED.clear()


def upscale(img: Any, models_dir: str, device: str = "cuda") -> Any:
    """Ảnh PIL RGB -> ảnh PIL RGB lớn gấp SCALE lần."""
    import numpy as np
    import torch
    from PIL import Image
    net, dev = load(models_dir, device)
    x = torch.from_numpy(np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0).permute(2, 0, 1)[None]
    x = x.to(dev, dtype=next(net.parameters()).dtype)
    with torch.inference_mode():
        y = net(x).clamp_(0, 1)
    out = (y[0].permute(1, 2, 0).float().cpu().numpy() * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(out)
