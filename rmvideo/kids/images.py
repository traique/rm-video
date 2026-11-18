"""(Kaggle) Sinh 8 ảnh 9:16 cho truyện bé bằng diffusers trên GPU T4.

- `schnell` (mặc định): FLUX.1-schnell, Apache-2.0, 4 bước; transformer 4-bit NF4 + T5 8-bit (bitsandbytes) để vừa T4
  (fp16 — T4 không có bf16). Bản đã lượng tử sẵn trong /kaggle/input (notebook rmvideo-kids-models, 14GB): ổ gắn chỉ
  ~30 MB/s nên nạp thẳng mất 7-8,5 phút (đo 18/11) -> prefetch() chép về đĩa cục bộ bằng 4 luồng NGAY khi bắt đầu việc,
  trong lúc model chữ tải/viết; không có bản lưu thì tải + lượng tử tại chỗ (~24GB, chậm hơn 5-8 phút).
- `zimage`: Z-Image-Turbo (Alibaba, 6B, Apache-2.0, 8 bước) — thử nghiệm, cần diffusers mới.
Sinh ở 832x1472 (~1,2 MP, bội số 16) rồi phóng lên 1080x1920 bằng lanczos (ffmpeg). Giải mã VAE ở fp32 để không ra ảnh
đen/NaN (lỗi hay gặp của FLUX fp16). Ảnh đen vẫn xảy ra thì sinh lại với seed khác một lần."""
from __future__ import annotations

import gc
import glob
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

INPUT_ROOT = "/kaggle/input"
PREFETCH_DIR = "/tmp/rm_kids_models"     # bản chép cục bộ của model đã lưu (đĩa Kaggle ngoài /kaggle/working còn rộng)
PREFETCH_THREADS = 4
_prefetching: dict[str, threading.Thread] = {}
# Đo thật 18/11/2026 (T4 14,5GB): T5 8-bit nằm cố định trên GPU (4,8GB) + transformer NF4 (6,5GB) + kích hoạt ở 832x1472 -> hết
# VRAM. Giờ: mã hoá cả 8 prompt trước, bỏ T5 khỏi GPU, rồi mới chạy transformer; sinh ở 768x1344 (~1 MP, bội số 16).
GEN_W, GEN_H = 768, 1344
OUT_W, OUT_H = 1080, 1920
MODELS: dict[str, dict[str, Any]] = {
    # repo gốc black-forest-labs/FLUX.1-schnell đã khoá (gated, cần token HF): dùng bản sao công khai nguyên vẹn
    "schnell": {"repo": "unsloth/FLUX.1-schnell", "steps": 4, "guidance": 0.0, "kind": "flux",
                "names": ("schnell", "flux"), "mirrors": ("Niansuh/FLUX.1-schnell", "cocktailpeanut/xulf-s")},
    "zimage": {"repo": "Tongyi-MAI/Z-Image-Turbo", "steps": 8, "guidance": 0.0, "kind": "zimage",
               "names": ("z-image", "zimage", "z_image")},
}
Log = Callable[[str], None]


def find_local(model: str, root: str = INPUT_ROOT) -> str | None:
    """Thư mục pipeline đã lưu (có model_index.json) mà tên đường dẫn chứa tên model."""
    if not os.path.isdir(root):
        return None
    names = MODELS.get(model, {}).get("names", (model,))
    for p in sorted(glob.glob(os.path.join(root, "**", "model_index.json"), recursive=True)):
        low = p.lower()
        if any(n in low for n in names):
            return os.path.dirname(p)
    return None


def prefetch(model: str, log: Log = logging.info, root: str = INPUT_ROOT, dest: str = PREFETCH_DIR) -> threading.Thread | None:
    """Chép thư mục model đã lưu từ ổ gắn về đĩa cục bộ trong luồng nền (4 file song song, file to trước). Gọi NGAY đầu
    việc; local_dir() chờ xong rồi trả bản cục bộ. Không đủ đĩa / lỗi thì bỏ, nạp thẳng từ ổ gắn như cũ."""
    src = find_local(model, root)
    if not src:
        return None
    dst = os.path.join(dest, os.path.basename(src))
    if os.path.exists(os.path.join(dst, ".done")):
        return None
    files = [os.path.relpath(os.path.join(r, f), src) for r, _, fs in os.walk(src) for f in fs]
    size = sum(os.path.getsize(os.path.join(src, f)) for f in files)
    try:
        os.makedirs(dest, exist_ok=True)
        free = shutil.disk_usage(dest).free
    except OSError:
        return None
    if free < size * 1.2 + 5 * 2 ** 30:
        log(f"Không chép model ảnh về đĩa cục bộ: còn {free / 2 ** 30:.0f}GB, cần {size * 1.2 / 2 ** 30:.0f}GB")
        return None
    files.sort(key=lambda f: -os.path.getsize(os.path.join(src, f)))

    def one(f: str) -> None:
        os.makedirs(os.path.dirname(os.path.join(dst, f)), exist_ok=True)
        shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))

    def work() -> None:
        t0 = time.time()
        try:
            with ThreadPoolExecutor(PREFETCH_THREADS) as ex:
                list(ex.map(one, files))
            open(os.path.join(dst, ".done"), "w").close()
            log(f"Đã chép model ảnh ({size / 2 ** 30:.1f}GB) về đĩa cục bộ sau {time.time() - t0:.0f}s")
        except Exception as e:
            log(f"Chép model ảnh về đĩa cục bộ lỗi ({str(e)[:120]}): nạp thẳng từ ổ gắn")
            shutil.rmtree(dst, ignore_errors=True)

    th = threading.Thread(target=work, name="kids-prefetch", daemon=True)
    th.start()
    _prefetching[model] = th
    return th


def local_dir(model: str, root: str = INPUT_ROOT, dest: str = PREFETCH_DIR) -> str | None:
    """Thư mục để nạp: bản chép cục bộ (chờ prefetch xong nếu đang chép) hoặc thẳng từ ổ gắn, hoặc None."""
    th = _prefetching.pop(model, None)
    if th is not None:
        th.join()
    src = find_local(model, root)
    if src:
        dst = os.path.join(dest, os.path.basename(src))
        if os.path.exists(os.path.join(dst, ".done")):
            return dst
    return src


def load_flux(repo: str, local: str | None, log: Log) -> Any:
    import torch
    from diffusers import FluxPipeline
    if local:
        log(f"Nạp FLUX.1-schnell 4-bit đã lưu: {local}")
        pipe = FluxPipeline.from_pretrained(local, torch_dtype=torch.float16)
    else:
        from diffusers import BitsAndBytesConfig as DiffBnb, FluxTransformer2DModel
        from transformers import BitsAndBytesConfig as TfBnb, T5EncoderModel
        log(f"Tải {repo} và lượng tử 4-bit tại chỗ (lần đầu, chậm; chạy kids_setup để lưu lại)")
        q4 = DiffBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
        tr = FluxTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4,
                                                    torch_dtype=torch.float16)
        t5 = T5EncoderModel.from_pretrained(repo, subfolder="text_encoder_2", quantization_config=TfBnb(load_in_8bit=True),
                                            torch_dtype=torch.float16)
        pipe = FluxPipeline.from_pretrained(repo, transformer=tr, text_encoder_2=t5, torch_dtype=torch.float16)
    _place(pipe, log)
    pipe.vae.to(torch.float32)                      # giải mã fp32: tránh ảnh đen do tràn fp16
    return pipe


def _place(pipe: Any, log: Log) -> None:
    """Đặt pipeline lên GPU: tuần tự từng model (cpu offload, ~12GB lúc cao nhất vừa T4); không được thì cả pipeline lên cuda."""
    try:
        pipe.enable_model_cpu_offload()
    except Exception as e:
        log(f"enable_model_cpu_offload lỗi ({str(e)[:120]}), đưa cả pipeline lên GPU")
        pipe.to("cuda")


def load_zimage(repo: str, local: str | None, log: Log) -> Any:
    """Z-Image-Turbo: 6B + text encoder Qwen3 4B, bf16 gốc không vừa T4 -> 4-bit (bản đã lưu, hoặc lượng tử tại chỗ)."""
    import torch
    from diffusers import ZImagePipeline
    if local:
        log(f"Nạp Z-Image-Turbo 4-bit đã lưu: {local}")
        pipe = ZImagePipeline.from_pretrained(local, torch_dtype=torch.float16)
    else:
        from diffusers import BitsAndBytesConfig as DiffBnb, ZImageTransformer2DModel
        from transformers import AutoModel, BitsAndBytesConfig as TfBnb
        log(f"Tải {repo} và lượng tử 4-bit tại chỗ (lần đầu, chậm; chạy kids_setup để lưu lại)")
        q4 = DiffBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
        tr = ZImageTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4,
                                                      torch_dtype=torch.float16)
        te = AutoModel.from_pretrained(repo, subfolder="text_encoder", torch_dtype=torch.float16,
                                       quantization_config=TfBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                                 bnb_4bit_compute_dtype=torch.float16))
        pipe = ZImagePipeline.from_pretrained(repo, transformer=tr, text_encoder=te, torch_dtype=torch.float16)
    _place(pipe, log)
    pipe.vae.to(torch.float32)
    return pipe


def _flux_decode(pipe: Any, latents: Any, w: int, h: int) -> Any:
    """Giải mã latent bằng VAE fp32 (pipe.vae đã chuyển fp32). Latent đặt lên thiết bị CHẠY (cuda): với cpu offload,
    pipe.vae.device là cpu lúc chưa gọi, hook đưa VAE lên GPU khi decode nhưng không đưa đầu vào theo (lỗi thật 18/11)."""
    import torch
    dev = pipe._execution_device
    lat = pipe._unpack_latents(latents, h, w, pipe.vae_scale_factor)
    lat = (lat / pipe.vae.config.scaling_factor) + pipe.vae.config.shift_factor
    with torch.no_grad():
        img = pipe.vae.decode(lat.to(dev, torch.float32), return_dict=False)[0]
    return pipe.image_processor.postprocess(img, output_type="pil")[0]


def is_blank(img: Any) -> bool:
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return bool(np.isnan(a).any() or a.std() < 3.0)


def flux_encode_all(pipe: Any, prompt_list: Sequence[str], log: Log) -> list[tuple[Any, Any]]:
    """Mã hoá mọi prompt bằng CLIP + T5 trước (giữ ở CPU), rồi BỎ T5 khỏi pipeline để trả VRAM cho transformer."""
    import gc

    import torch
    out = []
    with torch.no_grad():
        for p in prompt_list:
            pe, ppe, _ = pipe.encode_prompt(prompt=p, prompt_2=None, max_sequence_length=256)
            out.append((pe.to("cpu"), ppe.to("cpu")))
    pipe.text_encoder_2 = None                      # T5 8-bit không dời được: xoá hẳn, torch trả VRAM
    gc.collect()
    torch.cuda.empty_cache()
    log(f"Đã mã hoá {len(out)} prompt, bỏ T5 khỏi GPU (còn trống {torch.cuda.mem_get_info()[0] >> 20}MB)")
    return out


def generate_one(pipe: Any, kind: str, prompt: str, seed: int, steps: int, guidance: float, w: int = GEN_W,
                 h: int = GEN_H, embeds: tuple[Any, Any] | None = None) -> Any:
    import torch
    g = torch.Generator("cpu").manual_seed(int(seed))
    if kind == "flux":
        dev = pipe._execution_device
        if embeds is not None:
            lat = pipe(prompt_embeds=embeds[0].to(dev, torch.float16), pooled_prompt_embeds=embeds[1].to(dev, torch.float16),
                       width=w, height=h, num_inference_steps=steps, guidance_scale=guidance, generator=g,
                       output_type="latent").images
        else:
            lat = pipe(prompt=prompt, width=w, height=h, num_inference_steps=steps, guidance_scale=guidance, generator=g,
                       max_sequence_length=256, output_type="latent").images
        return _flux_decode(pipe, lat, w, h)
    return pipe(prompt=prompt, width=w, height=h, num_inference_steps=steps, guidance_scale=guidance, generator=g).images[0]


def upscale(src: str, dst: str, w: int = OUT_W, h: int = OUT_H) -> str:
    """Phóng lên 1080x1920: phủ kín khung rồi cắt (768x1344 lệch tỉ lệ 9:16 ~1,6%, không kéo méo)."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                    f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},unsharp=5:5:0.4:3:3:0.0",
                    "-frames:v", "1", dst], check=True)
    return dst


def generate(prompt_list: Sequence[str], seeds: Sequence[int], out_dir: str, model: str = "schnell",
             log: Log = logging.info, pipe: Any = None) -> list[str]:
    """Sinh từng prompt -> scene_01.png ... (1080x1920). pipe: pipeline đã nạp (test); None thì tự nạp và giải phóng."""
    os.makedirs(out_dir, exist_ok=True)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    m = MODELS.get(model) or MODELS["schnell"]
    own = pipe is None
    if own:
        t0 = time.time()
        local = local_dir(model)
        pipe = load_flux(m["repo"], local, log) if m["kind"] == "flux" else load_zimage(m["repo"], local, log)
        log(f"Model ảnh {model} sẵn sàng sau {time.time() - t0:.0f}s")
    embeds: list = []
    if m["kind"] == "flux" and getattr(pipe, "text_encoder_2", None) is not None:
        embeds = flux_encode_all(pipe, prompt_list, log)
    out = []
    try:
        for i, (prompt, seed) in enumerate(zip(prompt_list, seeds), 1):
            t0 = time.time()
            emb = embeds[i - 1] if embeds else None
            img = generate_one(pipe, m["kind"], prompt, seed, int(m["steps"]), float(m["guidance"]), embeds=emb)
            if is_blank(img):
                log(f"Ảnh cảnh {i} đen/hỏng, sinh lại với seed khác")
                img = generate_one(pipe, m["kind"], prompt, seed + 7919, int(m["steps"]), float(m["guidance"]), embeds=emb)
            raw = os.path.join(out_dir, f"raw_{i:02d}.png")
            img.save(raw)
            out.append(upscale(raw, os.path.join(out_dir, f"scene_{i:02d}.png")))
            log(f"Ảnh cảnh {i}/{len(prompt_list)}: {time.time() - t0:.0f}s")
    finally:
        if own:
            del pipe
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
    return out
