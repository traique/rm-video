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
import tempfile
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
    # FLUX.1-dev: giấy phép PHI THƯƠNG MẠI (kênh không bật kiếm tiền mới dùng); repo gốc gated -> bản sao công khai; 20 bước
    # guidance 3,5, chậm ~6 lần schnell trên T4 (thử 19/11)
    "dev": {"repo": "camenduru/FLUX.1-dev-diffusers", "steps": 20, "guidance": 3.5, "kind": "flux",
            "names": ("flux-dev", "dev", "flux1-dev")},
}
Log = Callable[[str], None]


def find_local(model: str, root: str = INPUT_ROOT) -> str | None:
    """Thư mục pipeline đã lưu (có model_index.json) mà tên đường dẫn chứa tên model."""
    if not os.path.isdir(root):
        return None
    names = MODELS.get(model, {}).get("names", (model,))
    # tên của model KHÁC loại («dev» với schnell) thì loại; trừ tên là phần của tên mình («flux» nằm trong «flux-dev»)
    others = {n for k, m in MODELS.items() if k != model for n in m["names"] if not any(n in x for x in names)}
    for p in sorted(glob.glob(os.path.join(root, "**", "model_index.json"), recursive=True)):
        base = os.path.basename(os.path.dirname(p)).lower()    # chỉ tên thư mục model: «dev» trong tên tài khoản không tính
        if any(n in base for n in names) and not any(n in base for n in others):
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


def is_blank(img: Any, band: float = 0.05) -> bool:
    """Ảnh đen/NaN/phẳng, hoặc có DẢI ĐEN ở mép trên/dưới (đo thật 18/11: 1/48 ảnh FLUX có dải đen ~8% phía trên)."""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.float32)
    if np.isnan(a).any() or a.std() < 3.0:
        return True
    rows = a.mean(axis=1)
    n = max(1, int(len(rows) * band))
    return bool((rows[:n] < 12).all() or (rows[-n:] < 12).all())


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


ESR_DIR = os.environ.get("ESR_DIR") or os.path.join(tempfile.gettempdir(), "rmv_esrgan")


def hd_path(p: str) -> str:
    """Bản HD (2 lần khung dựng) cạnh ảnh cảnh: scene_01.png -> scene_01_hd.png."""
    return os.path.splitext(p)[0] + "_hd.png"


def for_render(paths: Sequence[str]) -> list[str]:
    """Ảnh đưa vào dựng: bản HD Real-ESRGAN khi có (Ken Burns phóng / lia vẫn nét), không thì ảnh thường."""
    return [hd_path(p) if os.path.exists(hd_path(p)) else p for p in paths]


def _cover(img: Any, w: int, h: int) -> Any:
    """Phủ kín w x h rồi cắt giữa (như scale=…:force_original_aspect_ratio=increase,crop của ffmpeg)."""
    from PIL import Image
    k = max(w / img.width, h / img.height)
    big = img.resize((max(w, round(img.width * k)), max(h, round(img.height * k))), Image.Resampling.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


def upscale_esrgan(src: str, dst: str, w: int = OUT_W, h: int = OUT_H) -> bool:
    """1.2.0: Real-ESRGAN x4 trên GPU (rmvideo/kids/esrgan.py) -> dst (w x h) + bản HD 2w x 2h cho dựng. False khi
    không có GPU (caller phóng lanczos như cũ)."""
    import torch
    from PIL import Image

    from rmvideo.kids import esrgan
    if not torch.cuda.is_available():
        return False
    big = esrgan.upscale(Image.open(src).convert("RGB"), ESR_DIR, "cuda")
    _cover(big, 2 * w, 2 * h).save(hd_path(dst))
    _cover(big, w, h).save(dst)
    return True


def upscale(src: str, dst: str, w: int = OUT_W, h: int = OUT_H) -> str:
    """Phóng lên 1080x1920: phủ kín khung rồi cắt (768x1344 lệch tỉ lệ 9:16 ~1,6%, không kéo méo). 1.2.0: Real-ESRGAN
    trên GPU (kèm bản HD cho Ken Burns); không có GPU / lỗi thì lanczos + làm nét như cũ."""
    try:
        if upscale_esrgan(src, dst, w, h):
            return dst
    except Exception as e:                      # tải model hỏng, thiếu bộ nhớ GPU…: phóng lanczos, không hỏng job
        logging.warning("Real-ESRGAN lỗi (%s): phóng lanczos", str(e)[:150])
        for p in (dst, hd_path(dst)):
            if os.path.exists(p):
                os.remove(p)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                    f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},unsharp=5:5:0.4:3:3:0.0",
                    "-frames:v", "1", dst], check=True)
    return dst


LORA_SCALE = 0.9


def find_lora(name: str, root: str = INPUT_ROOT) -> str | None:
    """File LoRA .safetensors trong /kaggle/input: `name` là tên file (có/không .safetensors) hoặc slug notebook / thư mục
    (lấy file có số bước lớn nhất trong đó); đường dẫn tuyệt đối tồn tại thì dùng luôn."""
    import glob
    import re
    if not name:
        return None
    if os.path.isfile(name):
        return name
    base = name[:-12] if name.endswith(".safetensors") else name
    hits = [p for p in glob.glob(os.path.join(root, "**", "*.safetensors"), recursive=True)
            if os.path.basename(p)[:-12] == base or base in p.split(os.sep)]
    if not hits:
        return None

    def step(p: str) -> int:
        m = re.search(r"_(\d{4,12})\.safetensors$", p)       # ai-toolkit: <tên>_000000300.safetensors
        return int(m.group(1)) if m else 10 ** 9           # file không có số bước = bản cuối
    return sorted(hits, key=lambda p: (step(p), p))[-1]


def apply_lora(pipe: Any, path: str, scale: float = LORA_SCALE, log: Log = logging.info) -> bool:
    try:
        pipe.load_lora_weights(path, adapter_name="dcn")
        pipe.set_adapters(["dcn"], adapter_weights=[scale])
        log(f"LoRA nhân vật: {os.path.basename(path)} (scale {scale})")
        return True
    except Exception as e:
        log(f"LoRA {os.path.basename(path)} không nạp được ({str(e)[:120]}), vẽ không LoRA")
        return False


def generate(prompt_list: Sequence[str], seeds: Sequence[int], out_dir: str, model: str = "schnell",
             log: Log = logging.info, pipe: Any = None, expected: Sequence[int] | None = None,
             checker: Any = None, stats: dict | None = None, colors: Sequence[Sequence[str]] | None = None,
             lora: str = "", contact: Sequence[bool] | None = None, props: Sequence[str] | None = None) -> list[str]:
    """Sinh từng prompt -> scene_01.png ... (1080x1920). pipe: pipeline đã nạp (test); None thì tự nạp và giải phóng.
    expected: số bạn có mặt mỗi cảnh -> kiểm đầu nhân vật bằng OWLv2 (rmvideo.kids.imgcheck), thừa đầu thì vẽ lại với seed
    khác (tối đa imgcheck.RETRIES lần, giữ ảnh ít đầu thừa nhất). checker(img, n) -> (ok, số đầu, khung) để test.
    contact: cảnh nào hai bạn phải chạm nhau (ôm / dắt tay) -> checker(..., contact=True); props: đồ vật chính phải có trong
    cảnh («toy box», rỗng = không soát) -> checker(..., prop=...). stats: ghi {"checked", "redo"}."""
    os.makedirs(out_dir, exist_ok=True)
    st = stats if stats is not None else {}
    st.setdefault("checked", 0)
    st.setdefault("redo", 0)
    if expected is not None and checker is None:
        try:
            from rmvideo.kids import imgcheck
            t0 = time.time()
            loaded = imgcheck.load()
            checker = lambda img, n, c=None, _l=loaded, **kw: imgcheck.check(img, n, _l, colors=c, **kw)      # noqa: E731
            log(f"Kiểm ảnh: OWLv2 sẵn sàng sau {time.time() - t0:.0f}s")
        except Exception as e:
            log(f"Kiểm ảnh: không nạp được OWLv2 ({str(e)[:100]}), bỏ qua bước kiểm")
            checker = None
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    m = MODELS.get(model) or MODELS["schnell"]
    own = pipe is None
    if own:
        t0 = time.time()
        local = local_dir(model)
        pipe = load_flux(m["repo"], local, log) if m["kind"] == "flux" else load_zimage(m["repo"], local, log)
        log(f"Model ảnh {model} sẵn sàng sau {time.time() - t0:.0f}s")
        if lora:
            path = find_lora(lora)
            if path:
                st["lora"] = apply_lora(pipe, path, log=log)
            else:
                log(f"Không thấy LoRA «{lora}» trong {INPUT_ROOT}, vẽ không LoRA")
                st["lora"] = False
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
                log(f"Ảnh cảnh {i} đen/hỏng/có dải đen, sinh lại với seed khác")
                img = generate_one(pipe, m["kind"], prompt, seed + 7919, int(m["steps"]), float(m["guidance"]), embeds=emb)
            if checker is not None and expected is not None:
                from rmvideo.kids import imgcheck
                want = int(expected[i - 1]) if i - 1 < len(expected) else 1
                want_c = list(colors[i - 1]) if colors is not None and i - 1 < len(colors) else None
                kw: dict = {}
                if contact is not None and i - 1 < len(contact) and contact[i - 1]:
                    kw["contact"] = True
                if props is not None and i - 1 < len(props) and props[i - 1]:
                    kw["prop"] = str(props[i - 1])
                best, best_rank = img, None     # giữ ảnh ít lỗi nhất: (số lý do hỏng, lệch số đầu), không chỉ ít đầu
                for r in range(imgcheck.RETRIES + 1):
                    if kw:
                        ok, n, heads = checker(img, want, want_c, **kw)
                    else:
                        ok, n, heads = checker(img, want, want_c) if want_c is not None else checker(img, want)
                    st["checked"] += 1
                    if ok:
                        best = img
                        break
                    shirts = [h[0].split(":")[1] for h in heads if h[0].startswith("shirt:")]
                    whys = [h[0][4:] for h in heads if h[0].startswith("why:")]
                    head_boxes = [h for h in heads if not h[0].startswith(("shirt:", "why:"))]
                    why = "; ".join(whys) if whys else (
                          f"thấy {n} đầu nhân vật, cảnh chỉ có {want} bạn (ảnh hai đầu / nhân vật nhân đôi)" if n > max(1, want)
                           else f"áo thấy {shirts}, các bạn mặc {want_c} (áo đổi màu)" if (want_c and shirts
                                                                                       and not imgcheck.shirts_ok(
                                                                                           [(x, 0.0, [0, 0, 1, 1]) for x in shirts],
                                                                                           [c.split(":")[-1] for c in want_c]))
                           else f"hai bạn to nhỏ lệch nhau (đầu {[(b[2] - b[0]) for _q, _s, b in head_boxes]} px)")
                    log(f"Ảnh cảnh {i}: {why}" + (", vẽ lại với seed khác" if r < imgcheck.RETRIES
                                                else ", hết lượt: giữ ảnh ít lỗi nhất"))
                    try:
                        img.save(os.path.join(out_dir, f"raw_{i:02d}_bad{r + 1}.png"))     # giữ lại để soát bộ kiểm
                    except Exception:
                        pass
                    rank = (len(whys), abs(int(n) - want))
                    if best_rank is None or rank < best_rank:
                        best, best_rank = img, rank
                    if r == imgcheck.RETRIES:
                        break
                    st["redo"] += 1
                    img = generate_one(pipe, m["kind"], prompt, seed + 7919 * (r + 2), int(m["steps"]), float(m["guidance"]),
                                       embeds=emb)
                img = best
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
