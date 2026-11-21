"""(Kaggle GPU) Nội suy khung hình RIFE cho video gốc bị kéo chậm (1.2.0).

Video gốc ngắn hơn thời lượng cần thì remake kéo chậm tới 1,4 lần (setpts): 30 khung/giây gốc chỉ còn ~21 khung thật mỗi
giây, ffmpeg nhân đôi khung để đủ 30 -> chuyển động giật. Trước khi dựng, nhân đôi số khung bằng RIFE 4.25 (model 22MB,
MIT; mã rmvideo/media/rife.py từ FreeVideo): mỗi cặp khung liền nhau thêm một khung giữa (t = 0,5). Cùng thời lượng, cùng
tiếng (chép nguyên), nên mọi mốc thời gian của kế hoạch dựng giữ nguyên; setpts + fps phía sau tự lấy khung thật.

Hai khung khác hẳn nhau (chuyển cảnh) không nội suy (khung giữa sẽ nhoè hai cảnh), chép khung trước. Đo tốc độ trên
BENCH cặp đầu: dự tính quá BUDGET giây thì bỏ, dựng như cũ. Không có GPU / lỗi gì cũng dựng như cũ."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
import urllib.request
from typing import Any

import numpy as np

MIN_STRETCH = 1.1              # kéo chậm ít hơn chừng này thì khung lặp không thấy rõ: khỏi tốn GPU
BUDGET = 300.0                 # giây tối đa cho cả lượt nội suy (đo trên BENCH cặp đầu rồi dự tính)
BENCH = 24
CUT_DIFF = 0.12                # độ lệch trung bình (0-1, ảnh thu nhỏ) giữa hai khung: hơn mức này là chuyển cảnh
FULL_SCALE_SIDE = 1920         # cạnh dài hơn: tính dòng chảy ở nửa độ phân giải (FreeVideo: nét như nhau, ít bộ nhớ)
WEIGHTS = "rife_v4.25.safetensors"
WEIGHTS_URL = ("https://huggingface.co/Comfy-Org/Frame-Interpolation/resolve/aa7009be4d1471ea0bac91d6fe4d7e4b637cbbfc/"
               "frame_interpolation/rife_v4.25.safetensors")
WEIGHTS_SHA256 = "1505884b9bdae956795430d2a70f7e2317b2abd8f130f8cfdb35a5759f909481"
_NET: dict[str, Any] = {}


def weights_path(models_dir: str) -> str:
    os.makedirs(models_dir, exist_ok=True)
    p = os.path.join(models_dir, WEIGHTS)

    def sha(f: str) -> str:
        with open(f, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    if os.path.exists(p) and sha(p) != WEIGHTS_SHA256:
        os.remove(p)
    if not os.path.exists(p):
        urllib.request.urlretrieve(WEIGHTS_URL, p + ".part")
        if sha(p + ".part") != WEIGHTS_SHA256:
            os.remove(p + ".part")
            raise RuntimeError("model RIFE sai SHA-256")
        os.replace(p + ".part", p)
    return p


def load(models_dir: str, device: str = "cuda") -> Any:
    if "m" not in _NET:
        from safetensors.torch import load_file

        from rmvideo.media.rife import IFNet
        net = IFNet()
        net.load_state_dict(load_file(weights_path(models_dir)), strict=True)
        _NET["m"] = net.eval().to(device)
    return _NET["m"]


def is_cut(a: np.ndarray, b: np.ndarray) -> bool:
    """Hai khung (H, W, 3 uint8) thuộc hai cảnh khác nhau? So trên ảnh thu nhỏ 8 lần."""
    sa, sb = a[::8, ::8].astype(np.float32), b[::8, ::8].astype(np.float32)
    return float(np.mean(np.abs(sa - sb))) / 255.0 > CUT_DIFF


def middle(net: Any, a: np.ndarray, b: np.ndarray, device: str = "cuda") -> np.ndarray:
    """Khung giữa hai khung RGB uint8 (H, W, 3)."""
    import torch

    from rmvideo.media.rife import interpolate

    def t(x: np.ndarray) -> Any:
        return torch.from_numpy(np.require(x, requirements=["C", "W"])).to(device).permute(2, 0, 1)[None].float().div_(255.0)
    scale = 1.0 if max(a.shape[:2]) <= FULL_SCALE_SIDE else 0.5
    with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=device == "cuda"):
        out = interpolate(net, t(a), t(b), 0.5, scale)
        return out[0].float().permute(1, 2, 0).mul_(255.0).add_(0.5).clamp_(0, 255).to(torch.uint8).cpu().numpy()


def probe(path: str) -> tuple[int, int, float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate", "-of", "json", path], capture_output=True, check=True)
    st = json.loads(r.stdout)["streams"][0]
    num, den = (st.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return int(st["width"]), int(st["height"]), fps if 5 <= fps <= 120 else 30.0


def double_fps(src: str, dst: str, models_dir: str, device: str = "cuda", budget: float = BUDGET,
               log: Any = logging.info) -> dict:
    """src -> dst với gấp đôi số khung (RIFE), tiếng chép nguyên. Trả {"frames", "cuts", "secs"}; quá giờ / lỗi thì ném
    lỗi (caller dựng bằng src)."""
    w, h, fps = probe(src)
    net = load(models_dir, device)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-vf", f"fps={fps:.6f}", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", f"{2 * fps:.6f}", "-i", "-", "-i", src, "-map", "0:v", "-map", "1:a?", "-c:v",
                            "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-shortest", dst], stdin=subprocess.PIPE)
    size = w * h * 3
    t0 = time.time()
    n = cuts = 0
    assert dec.stdout is not None and enc.stdin is not None
    try:
        prev = None
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            cur = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            if prev is not None:
                if is_cut(prev, cur):
                    mid, cuts = prev, cuts + 1
                else:
                    mid = middle(net, prev, cur, device)
                enc.stdin.write(prev.tobytes())
                enc.stdin.write(mid.tobytes())
                n += 1
                if n == BENCH:
                    total = int(fps * float(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                        capture_output=True, text=True).stdout.strip() or 0))
                    est = (time.time() - t0) / BENCH * max(total, BENCH)
                    if est > budget:
                        raise TimeoutError(f"dự tính {est:.0f}s > {budget:.0f}s")
                    log(f"Nội suy khung: {total} khung, dự tính {est:.0f}s")
            prev = cur
        if prev is not None:
            enc.stdin.write(prev.tobytes())
            enc.stdin.write(prev.tobytes())
        enc.stdin.close()
        if enc.wait(timeout=600) != 0:
            raise RuntimeError("ffmpeg ghi video nội suy lỗi")
    finally:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
        dec.wait()
    if n == 0:
        raise RuntimeError("không đọc được khung hình")
    return {"frames": 2 * (n + 1), "cuts": cuts, "secs": round(time.time() - t0, 1)}


def smooth_for_slowdown(src: str, tmp: str, stretch: float, models_dir: str, log: Any = logging.info) -> str:
    """Video đưa vào dựng: bản gấp đôi khung khi bị kéo chậm ≥ MIN_STRETCH và có GPU; không thì (hoặc lỗi) src."""
    import config
    if stretch < MIN_STRETCH or str(getattr(config, "INTERP", "1")) != "1":
        return src
    try:
        import torch
        if not torch.cuda.is_available():         # job giọng edge / CapCut chạy Kaggle bằng CPU (góp ý Codex PR #20)
            log(f"Nội suy khung bỏ qua (chậm {stretch:.2f}x): phiên không có GPU")
            return src
        out = os.path.join(tmp, "smooth.mp4")
        info = double_fps(src, out, models_dir, "cuda", log=log)
        log(f"Nội suy khung RIFE (chậm {stretch:.2f}x): {info}")
        return out
    except Exception as e:
        log(f"Nội suy khung bỏ qua ({str(e)[:150]}): dựng như cũ")
        return src
