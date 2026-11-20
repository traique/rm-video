"""Khôi phục dataset Kaggle riêng tư `rmvideo-kids-lora` (LoRA 4 nhân vật truyện bé) từ bản lưu trong repo.

File LoRA nằm ở lora/ qua Git LFS (164 MB, quá giới hạn 100 MB/file của GitHub). Clone repo cần `git lfs pull` để có
file thật (không có LFS thì chỉ là file con trỏ vài trăm byte). Dùng khi đổi tài khoản Kaggle hay lỡ xoá dataset:

  git lfs pull --include "lora/*"
  KAGGLE_USERNAME=... KAGGLE_API_TOKEN=... python scripts/push_kids_lora.py

Dataset tạo xong (READY) thì phiên dựng tự gắn và nạp LoRA (KIDS_LORA=dcn_lora_v1, kaggle_worker.lora_source)."""
from __future__ import annotations

import glob
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import kaggle_api  # noqa: E402
import kaggle_worker  # noqa: E402

LORA_DIR = os.path.join(ROOT, "lora")
SHA256 = {"dcn_lora_v1.safetensors": "e5e1d8a5b924c2b2c566dd3f466c8e04ac6c77c8b024c928e96333b3f524a44b"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = sorted(glob.glob(os.path.join(LORA_DIR, "*.safetensors")))
    if not files:
        raise SystemExit(f"không thấy file .safetensors trong {LORA_DIR}")
    for p in files:
        want = SHA256.get(os.path.basename(p))
        if os.path.getsize(p) < 1 << 20:
            raise SystemExit(f"{p} chỉ là con trỏ Git LFS: chạy  git lfs pull --include \"lora/*\"  trước")
        if want and sha256(p) != want:
            raise SystemExit(f"{p} sai mã sha256 (file hỏng?)")
    print(f"Đẩy {len(files)} file lên dataset {kaggle_worker.SLUG_KIDS_LORA}...", flush=True)
    kaggle_api.dataset_push(kaggle_worker.SLUG_KIDS_LORA, "rmvideo kids lora", files, notes="LoRA 4 nhân vật (từ repo)")
    print("Trạng thái:", kaggle_api.dataset_wait(kaggle_worker.SLUG_KIDS_LORA, timeout=1800), flush=True)


if __name__ == "__main__":
    main()
