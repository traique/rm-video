"""Phase 0: bộ mã gửi lên Kaggle phải TỰ ĐỦ.

`kaggle_worker.BUNDLE` hiện là danh sách tệp viết tay: thêm một `import` vào mã lõi mà quên thêm tệp thì chỉ hỏng
TRÊN KAGGLE, lúc chạy, rồi job lặng lẽ lùi về Botkeep (chậm ~10 lần). Hai lớp kiểm:
1. Tĩnh: mọi module nội bộ mà mã trong bundle import (kể cả import đặt trong thân hàm) phải có trong bundle, trừ các
   cạnh ĐÃ BIẾT không bao giờ chạy trên Kaggle (KNOWN_UNREACHABLE — Phase 1 sẽ cắt hết các cạnh này).
2. Chạy thật: giải nén bundle vào thư mục trống, import từng module trong tiến trình Python cô lập (không thấy mã của
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# cạnh import (module trong bundle -> module ngoài bundle) không chạy trên Kaggle; mỗi cạnh ghi rõ vì sao
KNOWN_UNREACHABLE = {
    ("config", "autopost"),          # config.save_settings kiểm link kênh: Kaggle không lưu cài đặt
    ("core_video", "kaggle_worker"),  # _vieneu_male: giọng VieNeu trên Kaggle không đi qua synth_clip_ex
    ("core_video", "offline"),       # giọng Piper: gói offline không có trên Kaggle (TTS_OFFLINE mặc định tắt)
}


def _bundle_dir(tmp_path):
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(kaggle_worker.bundle_b64())))
    d = tmp_path / "bundle"
    z.extractall(d)
    return d, z.namelist()


def _internal_imports(path, internal):
    out = set()
    for n in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(n, ast.Import):
            names = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names = [n.module.split(".")[0]]
        else:
            continue
        out |= {x for x in names if x in internal}
    return out


def test_bundle_is_import_closed(tmp_path):
    d, names = _bundle_dir(tmp_path)
    bundled = {n[:-3] for n in names if n.endswith(".py") and "/" not in n} | {n.split("/")[0] for n in names if "/" in n}
    internal = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")} | {"capcut_tts_api"}
    missing = set()
    for mod in sorted(m for m in bundled if os.path.exists(os.path.join(d, m + ".py"))):
        for dep in _internal_imports(os.path.join(d, mod + ".py"), internal):
            if dep not in bundled and (mod, dep) not in KNOWN_UNREACHABLE:
                missing.add((mod, dep))
    assert not missing, f"module trong bundle Kaggle import module không được gửi kèm: {sorted(missing)}"


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
