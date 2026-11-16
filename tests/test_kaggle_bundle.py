"""Bộ mã gửi lên Kaggle phải TỰ ĐỦ (rmvideo/services/kaggle_bundle.py tính tự động từ import thật).

Trước đây `kaggle_worker.BUNDLE` là danh sách tệp viết tay: thêm một `import` vào mã lõi mà quên thêm tệp thì chỉ hỏng
TRÊN KAGGLE, lúc chạy, rồi job lặng lẽ lùi về Botkeep (chậm ~10 lần). Ba lớp kiểm:
1. Tĩnh: mọi module nội bộ mà mã trong bundle import (kể cả import trong thân hàm) đều có trong bundle; module cố ý không
   gửi (kaggle_bundle.OPTIONAL) chỉ được import trong `try ... except ImportError`.
2. Không hụt so với danh sách viết tay cũ (đã chạy thật trên Kaggle tới bản 40).
3. Chạy thật: giải nén bundle vào thư mục trống, import từng module trong tiến trình Python cô lập (không thấy mã của
   repo, không có aiogram hay mã web/Telegram; edge-tts trên Kaggle tự kéo aiohttp nên được phép).
"""
import ast
import base64
import io
import os
import subprocess
import sys
import zipfile

import kaggle_worker
from rmvideo.services import kaggle_bundle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# queue_manager.py đã cố ý bỏ: metrics lấy đường dẫn jobs.db từ rmvideo/app/db.py, không kéo hàng đợi lên Kaggle
OLD_MANUAL = ["kaggle_job.py", "core_video.py", "config.py", "subs.py", "align.py", "cache.py", "metrics.py",
              "url_guard.py", "potoken_setup.py", "vocal_sep.py", "vi_text.py", "tts_capcut.py",
              "slideshow.py", "trendfx.py", "beat.py", "scenes.py", "textzone.py", "asr_local.py",
              "assets/BeVietnamPro-ExtraBold.ttf"]


def _bundle_dir(tmp_path):
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(kaggle_worker.bundle_b64())))
    d = tmp_path / "bundle"
    z.extractall(d)
    return d, z.namelist()


def _imports_with_guard(path):
    """[(tên module gốc, có nằm trong try bắt ImportError không)] cho mọi import trong tệp."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    guarded = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Try) and any(
                h.type is not None and "ImportError" in ast.unparse(h.type) for h in n.handlers):
            for m in n.body:
                guarded |= {id(x) for x in ast.walk(m)}
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name.split(".")[0], id(n) in guarded) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.module.split(".")[0], id(n) in guarded))
    return out


def test_bundle_is_import_closed(tmp_path):
    d, names = _bundle_dir(tmp_path)
    bundled = {n[:-3] for n in names if n.endswith(".py") and "/" not in n} | {n.split("/")[0] for n in names if "/" in n}
    internal = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")} | {"capcut_tts_api", "rmvideo"}
    problems = []
    for rel in [n for n in names if n.endswith(".py") and not n.startswith("capcut_tts_api/")]:
        for dep, guarded in _imports_with_guard(os.path.join(d, rel)):
            if dep not in internal or dep in bundled:
                continue
            if dep in kaggle_bundle.OPTIONAL and guarded:
                continue
            problems.append((rel, dep))
    assert not problems, f"mã trong bundle Kaggle import module không được gửi kèm: {sorted(problems)}"
    assert set(OLD_MANUAL) <= set(names), set(OLD_MANUAL) - set(names)
    assert kaggle_worker.BUNDLE == list(kaggle_bundle.files())


ISOLATED = r"""
import importlib, sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("aiogram", "state", "web_panel", "tg_bot", "jobs", "pipeline", "autopost"):
            raise ImportError("không có trên Kaggle: " + name)
sys.meta_path.insert(0, _Block())
sys.path.insert(0, sys.argv[1])
assert not any(p.rstrip("/") == sys.argv[2].rstrip("/") for p in sys.path), "lọt đường dẫn repo"
for m in sys.argv[3].split(","):
    mod = importlib.import_module(m)
    assert mod.__file__.startswith(sys.argv[1]), (m, mod.__file__)
print("ok")
"""


def test_bundle_imports_in_clean_interpreter(tmp_path):
    d, names = _bundle_dir(tmp_path)
    mods = sorted(n[:-3] for n in names if n.endswith(".py") and "/" not in n)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    r = subprocess.run([sys.executable, "-I", "-c", ISOLATED, str(d), ROOT, ",".join(mods)], cwd=str(d),
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]


PY311_ONLY = ("asyncio.timeout(", "StrEnum", "TaskGroup(", "tomllib", "typing.Self", "datetime.UTC", "except*")


def test_bundle_runs_on_python_310():
    """Notebook Kaggle chạy Python của Kaggle (có thể còn 3.10): mã gửi đi không được dùng cú pháp / API chỉ có từ 3.11
    (lỗi là MỌI việc Kaggle hỏng rồi lặng lẽ lùi về Botkeep)."""
    bad = []
    for rel in kaggle_bundle.files():
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        ast.parse(src, rel, feature_version=(3, 10))
        bad += [(rel, tok) for tok in PY311_ONLY if tok in src]
    assert not bad, bad
