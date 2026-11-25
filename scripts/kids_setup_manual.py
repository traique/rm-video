# rm-video: chuẩn bị model cho truyện bé (bot tạo, chạy MỘT lần, ~45-60 phút trên GPU). Không chứa key.
import os, shutil, subprocess, sys, time, glob
W = "/kaggle/working"
def run(cmd, env=None, check=True):
    print(">>", cmd, flush=True); t0 = time.time()
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
    print(r.stdout[-3000:], r.stderr[-3000:], f"({time.time() - t0:.0f}s)", flush=True)
    if check and r.returncode: raise RuntimeError(f"lệnh lỗi: {cmd}")
    return r
def used_gb(path=W):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs) / 2**30
done = {}
run("nvidia-smi -L; df -h / /kaggle/working /tmp | tail -3; python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'; "
    "ls /usr/local/cuda/lib64/stubs /usr/local/cuda/targets/x86_64-linux/lib/stubs 2>/dev/null | head; find / -name 'libcuda.so*' 2>/dev/null | head",
    check=False)
# 1. llama.cpp (CUDA, kiến trúc T4 = 75) -> /kaggle/working/llama/llama-server (+ thư viện .so cạnh nó)
# GGML_CUDA_NO_VMM=ON: không cần libcuda.so lúc link (Kaggle không có symlink libcuda.so -> cmake báo thiếu CUDA::cuda_driver,
# lỗi thật 18/11/2026); lúc chạy vẫn dùng driver libcuda.so.1 của phiên GPU.
try:
    run("git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp")
    run("cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DGGML_CUDA=ON -DGGML_CUDA_NO_VMM=ON -DCMAKE_CUDA_ARCHITECTURES=75 "
        "-DLLAMA_CURL=OFF -DGGML_NATIVE=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_BUILD_TYPE=Release "
        "-DCUDAToolkit_ROOT=/usr/local/cuda")
    run("cmake --build /tmp/llama.cpp/build --config Release -j4 --target llama-server")
    os.makedirs(f"{W}/llama", exist_ok=True)
    for p in glob.glob("/tmp/llama.cpp/build/bin/*"):
        shutil.copy(p, f"{W}/llama/")
    done["llama"] = os.listdir(f"{W}/llama")
    print("llama-server:", done["llama"], flush=True)
except Exception as e:
    print("!! llama.cpp lỗi:", e, flush=True)
# 2. model ảnh 4-bit (bitsandbytes NF4), lưu để phiên dựng nạp ~1 phút thay vì tải 20-30GB mỗi lần
run("pip install -q 'diffusers>=0.36' accelerate bitsandbytes sentencepiece protobuf 'transformers==4.57.6'", check=False)
import torch
from diffusers import BitsAndBytesConfig as DiffBnb
from transformers import BitsAndBytesConfig as TfBnb
q4 = DiffBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
# 2a. FLUX.1-schnell: repo gốc black-forest-labs đã khoá (gated) -> dùng bản sao công khai nguyên vẹn (unsloth, ...)
for repo in ("unsloth/FLUX.1-schnell", "Niansuh/FLUX.1-schnell", "cocktailpeanut/xulf-s", "ParkSol/flux-schnell"):
    try:
        from diffusers import FluxPipeline, FluxTransformer2DModel
        from transformers import T5EncoderModel
        print("FLUX.1-schnell từ", repo, flush=True)
        tr = FluxTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4, torch_dtype=torch.float16)
        t5 = T5EncoderModel.from_pretrained(repo, subfolder="text_encoder_2", quantization_config=TfBnb(load_in_8bit=True), torch_dtype=torch.float16)
        pipe = FluxPipeline.from_pretrained(repo, transformer=tr, text_encoder_2=t5, torch_dtype=torch.float16)
        pipe.save_pretrained(f"{W}/flux-schnell-nf4")
        del pipe, tr, t5
        done["flux"] = os.listdir(f"{W}/flux-schnell-nf4")
        print("đã lưu flux-schnell-nf4 (%.1f GB):" % used_gb(f"{W}/flux-schnell-nf4"), done["flux"], flush=True)
        break
    except Exception as e:
        print("!! FLUX", repo, "lỗi:", str(e)[:400], flush=True)
        shutil.rmtree(f"{W}/flux-schnell-nf4", ignore_errors=True)
    finally:
        shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
        torch.cuda.empty_cache()
# 2b. Z-Image-Turbo (Apache-2.0, không khoá): transformer NF4 + text encoder Qwen3 4-bit; chỉ khi còn chỗ (đầu ra tối đa 20GB)
try:
    if used_gb() > 13.0:
        raise RuntimeError("đầu ra đã %.1f GB, bỏ Z-Image để không vượt 20GB" % used_gb())
    from diffusers import ZImagePipeline, ZImageTransformer2DModel
    from transformers import AutoModel
    repo = "Tongyi-MAI/Z-Image-Turbo"
    tr = ZImageTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4, torch_dtype=torch.float16)
    te = AutoModel.from_pretrained(repo, subfolder="text_encoder", torch_dtype=torch.float16,
                                   quantization_config=TfBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16))
    pipe = ZImagePipeline.from_pretrained(repo, transformer=tr, text_encoder=te, torch_dtype=torch.float16)
    pipe.save_pretrained(f"{W}/zimage-turbo-nf4")
    done["zimage"] = os.listdir(f"{W}/zimage-turbo-nf4")
    print("đã lưu zimage-turbo-nf4 (%.1f GB):" % used_gb(f"{W}/zimage-turbo-nf4"), done["zimage"], flush=True)
except Exception as e:
    print("!! Z-Image lỗi:", str(e)[:400], flush=True)
    shutil.rmtree(f"{W}/zimage-turbo-nf4", ignore_errors=True)
finally:
    shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
# 3. LoRA 4 nhân vật (1.4.9): bản lưu trong repo GitHub công khai (Git LFS, 164 MB) -> đầu ra notebook này, phiên dựng tìm
#    theo tên file trong mọi thứ đã gắn (images.find_lora). Mất dataset rmvideo-kids-lora (lỡ xoá 24/11) vẫn có LoRA.
try:
    import hashlib, socket, urllib.request
    socket.setdefaulttimeout(120)        # kết nối đứng im không giữ phiên GPU tới hạn của Kaggle
    os.makedirs(f"{W}/lora", exist_ok=True)
    dst = f"{W}/lora/dcn_lora_v1.safetensors"
    for k in range(3):
        try:
            urllib.request.urlretrieve('https://media.githubusercontent.com/media/traique/rm-video/95be355cad019a3cc37f648a97e6ce13d1d2d615/lora/dcn_lora_v1.safetensors', dst + ".part")
            h = hashlib.sha256()
            with open(dst + ".part", "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != 'e5e1d8a5b924c2b2c566dd3f466c8e04ac6c77c8b024c928e96333b3f524a44b':
                raise RuntimeError("sai mã sha256")
            os.replace(dst + ".part", dst)
            done["lora"] = [os.path.basename(dst)]
            print("đã tải LoRA (%.0f MB)" % (os.path.getsize(dst) / 2**20), flush=True)
            break
        except Exception as e:
            print("!! LoRA lần", k + 1, "lỗi:", str(e)[:300], flush=True)
            time.sleep(10)
finally:
    if os.path.exists(f"{W}/lora/dcn_lora_v1.safetensors.part"):
        os.remove(f"{W}/lora/dcn_lora_v1.safetensors.part")
# (model chữ GGUF ~20GB vượt giới hạn đầu ra notebook: phiên dựng tải từ Hugging Face, ~3 phút)
print("KẾT QUẢ:", {k: len(v) for k, v in done.items()}, "| đầu ra %.1f GB" % used_gb(), flush=True)
if "llama" not in done or not ({"flux", "zimage"} & set(done)): raise SystemExit("thiếu: " + ", ".join(k for k in ("llama", "flux/zimage") if k not in done and not ({"flux", "zimage"} & set(done))))
