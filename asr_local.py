"""Bóc lời NGAY TRÊN KAGGLE (GPU) bằng Whisper large-v3 (faster-whisper, giấy phép MIT), trên GIỌNG ĐÃ TÁCH khỏi nhạc.

Vì sao: Groq Whisper (turbo) nghe thẳng âm thanh có nhạc hay bịa câu chào kết rồi bỏ qua cả quãng lời thật (video cá
kèo 12/11/2026: sót 0-29s và 52-82s). Trên Kaggle giọng gốc đã được tách (Kim_Vocal_2) nên Whisper nghe giọng sạch;
VAD bỏ quãng không người nói (chỗ Whisper hay bịa); không nối ngữ cảnh câu trước (chống lặp vòng).
Trả về dict giống verbose_json của Groq ({"segments", "words", "language", "duration", "text"}) để Botkeep lọc tiếp
bằng đúng các bộ lọc của transcribe.py."""
import os

MODEL = os.environ.get("ASR_LOCAL_MODEL", "large-v3")
_MODEL: dict = {}


def preload_cuda() -> list:
    """ctranslate2 tìm libcublas.so.12 / libcudnn*.so.9 theo tên; PyTorch chỉ nạp chúng khi dùng tới nên `import torch`
    không đủ (đo thật 13/11/2026: "Library libcublas.so.12 is not found"). Nạp sẵn vào tiến trình: onnxruntime-gpu
    preload_dlls (cách tách giọng đã chạy được trên GPU), rồi dò thẳng các gói nvidia-*-cu12 trong site-packages."""
    import ctypes
    import glob
    import site
    loaded = []
    try:
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
            loaded.append("onnxruntime.preload_dlls")
    except Exception:
        pass
    bases = set(site.getsitepackages() + [site.getusersitepackages()] + glob.glob("/usr/local/lib/python3*/dist-packages"))
    for b in sorted(bases):
        for pat in ("nvidia/cuda_runtime/lib/libcudart.so.12*", "nvidia/cublas/lib/libcublasLt.so.12*",
                    "nvidia/cublas/lib/libcublas.so.12*", "nvidia/cudnn/lib/libcudnn*.so.9*"):
            for p in sorted(glob.glob(os.path.join(b, pat))):
                try:
                    ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                    loaded.append(os.path.basename(p))
                except OSError:
                    pass
    return loaded


def _device() -> tuple:
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            preload_cuda()
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def model(device: str = ""):
    if "m" not in _MODEL or (device and _MODEL.get("dev") != device):
        from faster_whisper import WhisperModel
        dev, ct = (device, "int8") if device == "cpu" else _device()
        _MODEL["m"] = WhisperModel(MODEL, device=dev, compute_type=ct, cpu_threads=os.cpu_count() or 4)
        _MODEL["dev"] = dev
    return _MODEL["m"]


def _load(path: str):
    """Âm thanh mono 16kHz float32 qua ffmpeg. KHÔNG để faster-whisper tự giải mã: nó gọi PyAV với tham số mà bản
    PyAV trên Kaggle không có (av.open(metadata_errors=...) -> TypeError, đo thật 13/11/2026)."""
    import subprocess

    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _run(audio, language: str, prompt: str, device: str = "") -> tuple:
    m = model(device)
    segs, info = m.transcribe(
        audio, language=language or None, initial_prompt=prompt or None, beam_size=5, word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 600, "speech_pad_ms": 300},
        condition_on_previous_text=False, no_speech_threshold=0.6, compression_ratio_threshold=2.4,
        temperature=[0.0, 0.2, 0.4, 0.6])
    return list(segs), info                       # segs là generator: lỗi GPU lộ ra ở đây chứ không phải lúc gọi


def transcribe(path: str, language: str = "", prompt: str = "") -> dict:
    audio = _load(path)
    try:
        segs, info = _run(audio, language, prompt)
    except RuntimeError as e:
        if _MODEL.get("dev") != "cuda":
            raise
        print(f"Whisper GPU lỗi ({str(e)[:150]}), chạy CPU", flush=True)       # chậm hơn nhiều nhưng vẫn ra lời
        segs, info = _run(audio, language, prompt, device="cpu")
    out, words = [], []
    for s in segs:
        ws = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
              for w in (s.words or []) if (w.word or "").strip()]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                    "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob,
                    "compression_ratio": s.compression_ratio})
        words += ws
    return {"segments": out, "words": words, "language": info.language, "duration": info.duration,
            "text": " ".join(x["text"] for x in out).strip(), "device": _MODEL.get("dev", "")}
