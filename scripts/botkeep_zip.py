"""Gói mã chạy bot cho Botkeep: zip gọn + nhánh `botkeep` trên GitHub (tự đồng bộ sau mỗi lần gộp vào nhánh chính).

Giới hạn của Botkeep (Extract archive): zip ≤ 4 MB, ≤ 250 mục (tính cả thư mục), ≤ 20 MB sau giải nén, ≤ 4 MB mỗi file.
Zip «Download ZIP» của GitHub có cả test, tài liệu, cấu hình CI (bản 1.0.0: ~270 mục) nên bị từ chối, và luôn bọc mọi
thứ trong một thư mục `rm-video-<nhánh>/`, trong khi bot chạy thẳng ở thư mục gốc (`/home/container`).

Ở đây chỉ lấy file git đang theo dõi, bỏ phần chỉ dùng khi phát triển (tests/, docs/, .github/, scripts/) và file model lưu trữ
(lora/: LoRA nhân vật truyện bé 164 MB qua Git LFS, chỉ để khôi phục dataset Kaggle):
- `rm-video-botkeep.zip`: zip PHẲNG (không có thư mục bọc ngoài): tải lên Botkeep, giải nén vào `/` là đè đúng chỗ.
- nhánh `botkeep`: mã chạy bot + zip phẳng ở trên + BOTKEEP.md. Mỗi lần đồng bộ là MỘT commit thay hẳn bản trước
  (zip đổi mỗi lần, giữ lịch sử thì repo phình ra), nội dung commit ghi rõ lấy từ commit nào của nhánh chính.
Dữ liệu trên máy (settings.json, jobs.db, outputs/, logo đã tải lên...) không nằm trong zip: giải nén đè không mất gì.

Dùng:
  python scripts/botkeep_zip.py [thư_mục_ra]      tạo rm-video-botkeep.zip (mặc định: thư mục hiện tại)
  python scripts/botkeep_zip.py --commit          tạo commit cho nhánh botkeep, in mã commit (CI đẩy lên GitHub)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = "ccr-0f4ff082-rshcy5"                   # nhánh chính
BRANCH = "botkeep"                             # nhánh mã chạy bot
ZIP_NAME = "rm-video-botkeep.zip"
SKIP = ("tests/", "docs/", ".github/", "scripts/", "lora/",
        "assets/DejaVuSans-Bold.ttf")     # font chỉ test dùng (696KB): bỏ cho gói nhẹ, Botkeep quét kịp (24/11)
MAX_ENTRIES, MAX_ZIP, MAX_TOTAL, MAX_FILE = 250, 4 << 20, 20 << 20, 4 << 20
# giờ cố định cho mọi mục trong zip: CI mỗi lần checkout là giờ file mới -> zip khác dù mã y nguyên -> đẩy bản «mới» giả
STAMP = (2026, 1, 1, 0, 0, 0)


def git(*args: str, env: dict | None = None, data: bytes | None = None) -> str:
    return subprocess.run(["git", "-C", ROOT, *args], input=data, capture_output=True, check=True,
                          env={**os.environ, **(env or {})}).stdout.decode().strip()


def version() -> str:
    src = open(os.path.join(ROOT, "rmvideo", "version.py"), encoding="utf-8").read()
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    return m.group(1) if m else "0"


def runtime_files() -> list[str]:
    out = git("ls-files", "-z")
    return sorted(f for f in out.split("\0") if f and not f.startswith(SKIP))


def _dirs(files: list[str]) -> list[str]:
    return sorted({"/".join(f.split("/")[:k]) + "/" for f in files for k in range(1, f.count("/") + 1)})


def check_limits(files: list[str], extra_entries: int = 0) -> list[str]:
    entries = len(_dirs(files)) + len(files) + extra_entries
    sizes = [os.path.getsize(os.path.join(ROOT, f)) for f in files]
    return ([f"{entries} mục > {MAX_ENTRIES}"] if entries > MAX_ENTRIES else []) + \
           ([f"giải nén {sum(sizes) >> 20} MB > 20 MB"] if sum(sizes) > MAX_TOTAL else []) + \
           [f"{f} lớn hơn 4 MB" for f, s in zip(files, sizes) if s > MAX_FILE]


def build(out_dir: str = ".") -> str:
    """Zip phẳng (giải nén vào thư mục gốc của bot). Vượt giới hạn Botkeep thì báo lỗi, không tạo file."""
    files = runtime_files()
    problems = check_limits(files)
    path = os.path.join(out_dir, ZIP_NAME)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in _dirs(files):                           # mục thư mục: trình giải nén nào cũng tạo đúng cây thư mục
            info = zipfile.ZipInfo(d, date_time=STAMP)
            info.external_attr = (0o40755 << 16) | 0x10
            z.writestr(info, "")
        for f in files:                                  # cùng nội dung -> cùng zip từng byte (giờ, quyền cố định)
            full = os.path.join(ROOT, f)
            info = zipfile.ZipInfo(f, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if os.access(full, os.X_OK) else 0o100644) << 16
            with open(full, "rb") as fh:
                z.writestr(info, fh.read(), compresslevel=9)
    if os.path.getsize(path) > MAX_ZIP:
        problems.append(f"zip {os.path.getsize(path) >> 20} MB > 4 MB")
    if problems:
        os.remove(path)
        raise SystemExit("Không vừa giới hạn Botkeep: " + "; ".join(problems))
    print(f"{path}: {len(_dirs(files)) + len(files)} mục ({len(files)} file), phiên bản {version()}, "
          f"zip {os.path.getsize(path) / 1e6:.2f} MB", file=sys.stderr)
    return path


def guide() -> str:
    # không ghi mã commit ở đây (ghi trong nội dung commit): mã chạy bot không đổi thì nhánh không đổi, khỏi cập nhật thừa
    return f"""# Mã chạy bot cho Botkeep (phiên bản {version()})

Nhánh này **tự đồng bộ** từ nhánh chính `{MAIN}` sau mỗi lần gộp; nội dung commit ghi rõ lấy từ commit nào.
Chỉ có mã bot cần để chạy (bỏ test, tài liệu, cấu hình CI), nên vừa giới hạn giải nén của Botkeep
(≤ 250 mục, ≤ 20 MB). Không phải bản phát hành mới: số phiên bản chỉ đổi khi bot thật sự đổi.

## Cập nhật bot trên Botkeep
1. Mở file `{ZIP_NAME}` ở nhánh này → **Download** (tải file gốc).
2. Botkeep → Files → tải zip lên → **Extract archive** → thư mục đích để `/` → giải nén.
   Zip không có thư mục bọc ngoài nên file vào thẳng chỗ bot đang chạy, không phải chuyển file.
3. Khởi động lại bot. Lên phiên bản mới thì bot tự báo qua Telegram / Zoom.

Dữ liệu của bot (settings.json, jobs.db, outputs/, logo / nhạc đã tải lên) không nằm trong zip nên giải nén đè
không mất gì. Cách khác: «Code → Download ZIP» của nhánh này cũng giải nén được, nhưng GitHub bọc thêm thư mục
`rm-video-{BRANCH}/` nên phải chuyển file ra thư mục gốc.
"""


def make_commit() -> str:
    """Commit cho nhánh botkeep: cây = mã chạy bot + zip phẳng + BOTKEEP.md, không cha (thay hẳn bản trước).
    Cây giống hệt bản đang có trên nhánh thì trả mã commit cũ (không cần đẩy)."""
    src = git("rev-parse", "HEAD")
    files = runtime_files()
    problems = check_limits(files, extra_entries=2)       # + zip + BOTKEEP.md
    if problems:
        raise SystemExit("Không vừa giới hạn Botkeep: " + "; ".join(problems))
    stage = {}
    for ln in git("ls-files", "-s", "-z").split("\0"):    # "<mode> <blob> <stage>\t<path>" của chính HEAD
        if ln:
            meta, path = ln.split("\t", 1)
            mode, blob, _ = meta.split()
            stage[path] = (mode, blob)
    with tempfile.TemporaryDirectory() as tmp:
        zpath = build(tmp)
        zblob = git("hash-object", "-w", zpath)
        gblob = git("hash-object", "-w", "--stdin", data=guide().encode())
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        git("read-tree", "--empty", env=env)
        lines = [f"{stage[f][0]} {stage[f][1]}\t{f}" for f in files]
        lines += [f"100644 {zblob}\t{ZIP_NAME}", f"100644 {gblob}\tBOTKEEP.md"]
        git("update-index", "--index-info", env=env, data=("\n".join(lines) + "\n").encode())
        tree = git("write-tree", env=env)
    try:
        cur = git("rev-parse", f"refs/remotes/origin/{BRANCH}")
        if git("rev-parse", f"{cur}^{{tree}}") == tree:
            print(f"Nhánh {BRANCH} đã đúng bản này, không cần đẩy.", file=sys.stderr)
            return cur
    except subprocess.CalledProcessError:
        pass                                               # nhánh chưa có: tạo mới
    msg = f"Mã chạy bot cho Botkeep: {MAIN} @ {src[:7]} (phiên bản {version()})"
    bot = "github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"
    ident = ({"GIT_AUTHOR_NAME": bot[0], "GIT_COMMITTER_NAME": bot[0], "GIT_AUTHOR_EMAIL": bot[1],
              "GIT_COMMITTER_EMAIL": bot[1]} if os.environ.get("GITHUB_ACTIONS") == "true" else {})
    return git("commit-tree", tree, "-m", msg, env=ident)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--commit"]:
        print(make_commit())
    else:
        build(sys.argv[1] if len(sys.argv) > 1 else ".")
