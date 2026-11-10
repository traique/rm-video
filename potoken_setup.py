"""Cài / gỡ bộ sinh PO token (bgutil) NGAY TRONG BOT — không cần shell, git hay Node có sẵn.
Gọi từ console Botkeep: gõ  setup_potoken  (hoặc  sh setup_potoken.sh ).
Node lấy qua pip (nodejs-wheel-binaries) vào tools/, mã nguồn bgutil tải tarball từ GitHub.
Chiếm ~300MB đĩa; gỡ bằng  remove_potoken ."""
import asyncio
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(BASE, "tools")
NODE_DIR = os.path.join(TOOLS, "nodejs")
POT_DIR = os.path.join(BASE, "potoken")
PINNED_VERSION = "2.0.2"      # phải trùng plugin pip trong requirements.txt (server và plugin cùng phiên bản)


def _bgutil_version() -> str:
    try:
        from importlib.metadata import version
        return version("bgutil-ytdlp-pot-provider")
    except Exception:
        return PINNED_VERSION


def tarball_url(ver: str) -> str:
    """Mã nguồn server đúng TAG phiên bản (không lấy nhánh master đang thay đổi)."""
    return f"https://codeload.github.com/Brainicism/bgutil-ytdlp-pot-provider/tar.gz/refs/tags/{ver}"


def node_bin_dir() -> str:
    d = os.path.join(NODE_DIR, "nodejs_wheel", "bin")
    return d if os.path.exists(os.path.join(d, "node")) else ""


def ensure_path():
    """Đưa node cài trong tools/ vào PATH để plugin bgutil tìm thấy."""
    d = node_bin_dir()
    if d and d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _npm_cmd(node: str) -> list:
    sysnpm = shutil.which("npm")
    if sysnpm and not node_bin_dir():
        return [sysnpm]
    cli = os.path.join(NODE_DIR, "nodejs_wheel", "lib", "node_modules", "npm", "bin", "npm-cli.js")
    return [node, cli]


async def _run(cmd: list, cwd: str, log, timeout: int = 900) -> bool:
    env = dict(os.environ, npm_config_cache=os.path.join(TOOLS, ".npm-cache"), npm_config_update_notifier="false")
    p = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=env,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        log("❌ Quá thời gian: " + " ".join(cmd[-2:]))
        return False
    if p.returncode != 0:
        log("❌ Lỗi: " + out.decode(errors="ignore")[-600:])
        return False
    return True


async def _pip(args: list, log, timeout: int) -> bool:
    """pip install; máy chặn kiểu PEP 668 (externally-managed) thì thử lại với --break-system-packages."""
    quiet = []
    if await _run([sys.executable, "-m", "pip", "install", "-q"] + args, BASE, quiet.append, timeout):
        return True
    if any("externally-managed" in q or "break-system-packages" in q for q in quiet):
        return await _run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"] + args,
                          BASE, log, timeout)
    log(quiet[-1] if quiet else "❌ pip lỗi")
    return False


async def install(log=logging.info) -> bool:
    free = shutil.disk_usage(BASE).free // (1 << 20)
    if free < 700:
        log(f"❌ Ổ đĩa còn {free}MB, cần ≥700MB trống để cài (sau cài chiếm ~300MB). Xóa bớt video cũ rồi thử lại.")
        return False
    os.makedirs(TOOLS, exist_ok=True)
    # 1) plugin yt-dlp
    ver = _bgutil_version()
    log(f"1/5 Plugin bgutil cho yt-dlp (phiên bản {ver})...")
    if not await _pip([f"bgutil-ytdlp-pot-provider=={ver}"], log, 300):
        return False
    # 2) Node.js
    ensure_path()
    node = shutil.which("node")
    if not node:
        log("2/5 Máy chủ không có Node.js -> tải Node qua pip vào tools/ (~200MB)...")
        if not await _pip(["--target", NODE_DIR, "nodejs-wheel-binaries"], log, 600):
            return False
        ensure_path()
        node = os.path.join(node_bin_dir(), "node")
    else:
        log(f"2/5 Dùng Node có sẵn: {node}")
    npm = _npm_cmd(node)
    # 3) mã nguồn bgutil
    log("3/5 Tải mã nguồn bộ sinh token...")
    with tempfile.TemporaryDirectory(dir=TOOLS) as td:
        data = await asyncio.to_thread(lambda: urllib.request.urlopen(tarball_url(ver), timeout=120).read())
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(td, filter="data") if sys.version_info >= (3, 12) else tf.extractall(td)
        src = os.path.join(td, os.listdir(td)[0], "server")
        # 4) build
        log("4/5 Build (npm install + tsc), mất 1-3 phút...")
        # có package-lock.json thì dùng `npm ci`: cài ĐÚNG các phiên bản đã khoá, không kéo bản mới
        lock = os.path.exists(os.path.join(src, "package-lock.json"))
        if not await _run(npm + (["ci"] if lock else ["install"]) + ["--no-audit", "--no-fund"], src, log):
            return False
        if not await _run([node, os.path.join(src, "node_modules", "typescript", "bin", "tsc")], src, log):
            return False
        pkg = json.load(open(os.path.join(src, "package.json")))
        shutil.rmtree(POT_DIR, ignore_errors=True)
        os.makedirs(POT_DIR)
        shutil.copytree(os.path.join(src, "build"), os.path.join(POT_DIR, "build"))
        with open(os.path.join(POT_DIR, "package.json"), "w") as f:
            json.dump({"type": pkg.get("type", "module"), "dependencies": pkg["dependencies"]}, f)
        if lock:
            shutil.copy(os.path.join(src, "package-lock.json"), os.path.join(POT_DIR, "package-lock.json"))
    log("5/5 Cài thư viện chạy (bỏ phần dev cho gọn)...")
    if not await _run(npm + ["install", "--omit=dev", "--no-audit", "--no-fund"], POT_DIR, log):
        return False
    shutil.rmtree(os.path.join(TOOLS, ".npm-cache"), ignore_errors=True)
    ok = await _run([node, os.path.join(POT_DIR, "build", "generate_once.js")], POT_DIR, log, 120)
    size = sum(os.path.getsize(os.path.join(r, f)) for d in (POT_DIR, TOOLS) for r, _, fs in os.walk(d) for f in fs)
    log(("✅ Cài xong, sinh token thử OK" if ok else "⚠️ Cài xong nhưng sinh token thử lỗi") +
        f" ({size >> 20}MB). Bot dùng ngay, không cần restart. Thử:  yt_test <link YouTube>")
    return ok


def remove(log=logging.info):
    shutil.rmtree(POT_DIR, ignore_errors=True)
    shutil.rmtree(TOOLS, ignore_errors=True)
    log("Đã gỡ bộ PO token, giải phóng dung lượng.")
