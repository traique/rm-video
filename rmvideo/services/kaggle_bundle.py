"""Bộ mã gửi lên notebook Kaggle, tính TỰ ĐỘNG từ import thật của điểm vào (kaggle_job.py).

Trước đây là danh sách tệp viết tay (kaggle_worker.BUNDLE): thêm một `import` vào mã lõi mà quên thêm tệp thì chỉ
hỏng TRÊN KAGGLE, lúc chạy, rồi job lặng lẽ lùi về Botkeep (chậm ~10 lần). Giờ đọc cây cú pháp (AST) của từng tệp,
đi theo mọi `import` nội bộ — kể cả import đặt trong thân hàm — tới khi khép kín.

Module trong OPTIONAL cố ý KHÔNG gửi (nặng, không cài trên Kaggle); nơi import chúng phải bắt ImportError
(tests/test_kaggle_bundle.py kiểm điều này).
"""
from __future__ import annotations

import ast
import base64
import functools
import io
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRY = "kaggle_job"
PACKAGE = "rmvideo"
OPTIONAL = {
    "offline": "gói offline (sherpa-onnx, model Piper/Spleeter) không cài trên Kaggle; core_video bắt ImportError",
}
DIRS = ("capcut_tts_api",)                          # gói đóng kèm: gửi cả thư mục (dữ liệu json + mã)
ASSETS = ("assets/BeVietnamPro-ExtraBold.ttf",)     # font chữ hook / tiêu đề


def _root_modules(root: str) -> set[str]:
    return {f[:-3] for f in os.listdir(root) if f.endswith(".py")}


def _package_file(root: str, dotted: str) -> str | None:
    """'rmvideo.app.settings' -> 'rmvideo/app/settings.py' (hoặc .../__init__.py nếu là gói)."""
    rel = dotted.replace(".", "/")
    for cand in (rel + ".py", rel + "/__init__.py"):
        if os.path.isfile(os.path.join(root, cand)):
            return cand
    return None


def _imported_names(path: str, module: str) -> set[str]:
    """Tên module (dạng chấm) mà tệp import, kể cả import trong thân hàm và import tương đối."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:                                 # from . import x / from ..a import b (trong gói rmvideo)
                parts = module.split(".")
                is_pkg = path.endswith("__init__.py")
                anchor = parts if is_pkg else parts[:-1]
                anchor = anchor[:len(anchor) - (n.level - 1)]
                base = ".".join(anchor + ([base] if base else []))
            out.add(base)
            out |= {f"{base}.{a.name}" for a in n.names if a.name != "*"}   # có thể là module con
    return out


@functools.lru_cache(maxsize=4)
def files(root: str = ROOT) -> tuple[str, ...]:
    """Đường dẫn tương đối (từ gốc repo) của mọi tệp .py cần gửi, đã sắp xếp."""
    roots = _root_modules(root)
    picked: set[str] = set()
    todo: list[tuple[str, str]] = [(ENTRY + ".py", ENTRY)]
    while todo:
        rel, mod = todo.pop()
        if rel in picked:
            continue
        picked.add(rel)
        for name in _imported_names(os.path.join(root, rel), mod):
            top = name.split(".")[0]
            if top in OPTIONAL or top in DIRS:
                continue
            if top in roots and "." not in name:
                todo.append((top + ".py", top))
            elif top == PACKAGE:
                parts = name.split(".")
                for k in range(1, len(parts) + 1):     # gói cha (rmvideo/__init__.py, rmvideo/app/__init__.py)
                    dotted = ".".join(parts[:k])
                    f = _package_file(root, dotted)
                    if f:
                        todo.append((f, dotted))
    return tuple(sorted(picked))


def members(root: str = ROOT) -> list[str]:
    """Mọi tệp trong zip: mã .py + thư mục đóng kèm + tài nguyên."""
    out = list(files(root)) + list(ASSETS)
    for d in DIRS:
        for base, _, names in os.walk(os.path.join(root, d)):
            if "__pycache__" in base:
                continue
            out += [os.path.relpath(os.path.join(base, f), root) for f in sorted(names)]
    return out


def build_b64(root: str = ROOT) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in members(root):
            z.write(os.path.join(root, rel), rel)
    return base64.b64encode(buf.getvalue()).decode()
