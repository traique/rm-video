"""Đóng gói mã chạy bot thành zip vừa trình giải nén của Botkeep.

Giới hạn của Botkeep (Extract archive): zip ≤ 4 MB, ≤ 250 mục (tính cả thư mục), ≤ 20 MB sau giải nén, ≤ 4 MB mỗi file.
Zip «Download ZIP» của GitHub chứa cả test, tài liệu, cấu hình CI (bản 1.0.0: ~270 mục) nên bị từ chối. Bản này chỉ lấy
file git đang theo dõi, bỏ phần chỉ dùng khi phát triển (tests/, docs/, .github/, scripts/). Thư mục gốc giữ là
`rm-video-<nhánh>/` như zip của GitHub, nên các bước giải nén / chạy trên Botkeep giữ nguyên.
Dữ liệu trên máy (settings.json, jobs.db, outputs/, logo đã tải lên...) không nằm trong zip: giải nén đè lên bản cũ
không mất gì.

Dùng:  python scripts/botkeep_zip.py [thư_mục_ra]      (mặc định: thư mục hiện tại)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH = "ccr-0f4ff082-rshcy5"                 # nhánh chính: tên thư mục gốc giống zip GitHub tải từ nhánh này
SKIP = ("tests/", "docs/", ".github/", "scripts/")
MAX_ENTRIES, MAX_ZIP, MAX_TOTAL, MAX_FILE = 250, 4 << 20, 20 << 20, 4 << 20


def version() -> str:
    src = open(os.path.join(ROOT, "rmvideo", "version.py"), encoding="utf-8").read()
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    return m.group(1) if m else "0"


def runtime_files() -> list[str]:
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, check=True).stdout
    return sorted(f for f in out.decode().split("\0") if f and not f.startswith(SKIP))


def build(out_dir: str = ".") -> str:
    files = runtime_files()
    top = f"rm-video-{BRANCH}/"
    dirs = sorted({top} | {top + "/".join(f.split("/")[:k]) + "/" for f in files for k in range(1, f.count("/") + 1)})
    entries = len(dirs) + len(files)
    sizes = {f: os.path.getsize(os.path.join(ROOT, f)) for f in files}
    big = [f for f, s in sizes.items() if s > MAX_FILE]
    problems = ([f"{entries} mục > {MAX_ENTRIES}"] if entries > MAX_ENTRIES else []) + \
               ([f"giải nén {sum(sizes.values()) >> 20} MB > 20 MB"] if sum(sizes.values()) > MAX_TOTAL else []) + \
               [f"{f} lớn hơn 4 MB" for f in big]
    path = os.path.join(out_dir, f"rm-video-{version()}-botkeep.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in dirs:                                   # mục thư mục như zip GitHub (trình giải nén nào cũng tạo đúng)
            z.writestr(zipfile.ZipInfo(d), "")
        for f in files:
            z.write(os.path.join(ROOT, f), top + f)
    if os.path.getsize(path) > MAX_ZIP:
        problems.append(f"zip {os.path.getsize(path) >> 20} MB > 4 MB")
    if problems:
        os.remove(path)
        raise SystemExit("Không vừa giới hạn Botkeep: " + "; ".join(problems))
    print(f"{path}: {entries} mục ({len(files)} file), {sum(sizes.values()) / 1e6:.1f} MB giải nén, "
          f"zip {os.path.getsize(path) / 1e6:.2f} MB")
    return path


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else ".")
