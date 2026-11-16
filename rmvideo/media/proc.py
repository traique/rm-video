"""Tiến trình con (ffmpeg / ffprobe / python con tách giọng...) có giới hạn, huỷ được THEO TỪNG JOB.

Trước đây mỗi module tự gọi subprocess theo một kiểu (4 bản "spawn + RUNNING + kill"), phần lớn lời gọi đồng bộ
trong luồng phụ KHÔNG đăng ký nên bấm Huỷ không dừng được; `core_video.kill_running()` lại giết MỌI tiến trình kể
cả ffprobe của request web đang chạy cùng lúc (C9), và chỉ giết con chứ không giết cháu (python con -> ffmpeg).

Ở đây:
- mọi tiến trình chạy trong phiên riêng (start_new_session) và bị giết CẢ NHÓM (killpg) khi huỷ / quá giờ;
- mỗi tiến trình ghi tên "chủ" = job đang chạy (contextvar, tự truyền vào task con và asyncio.to_thread);
  kill_owner(job_id) chỉ giết tiến trình của job đó, kèm cờ huỷ (threading.Event) cho vòng lặp đồng bộ như yt-dlp;
- `run` (async) đọc stderr có trần mặc định (ffmpeg in hàng MB log không làm phình RAM), lỗi là ProcError có mã thoát;
- `run_sync` dùng trong luồng phụ, cùng cách gọi với subprocess.run (CompletedProcess, check, input, text).

Không dùng `run_sync`/`popen` trong TIẾN TRÌNH CON tự gọi ffmpeg (vocal_sep / offline chạy như python con): phiên
riêng sẽ đưa ffmpeg cháu ra khỏi nhóm của con, killpg của cha không với tới nữa.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import itertools
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

STDERR_KEEP = 16_000                   # giữ ~16KB cuối stderr để báo lỗi
DEFAULT_TIMEOUT = 4 * 3600.0           # trần an toàn: dài hơn mọi ngân sách job (tối đa 3 giờ + chờ Kaggle)

_OWNER: contextvars.ContextVar[Any] = contextvars.ContextVar("proc_owner", default=None)
_LOCK = threading.Lock()
_IDS = itertools.count(1)
_LIVE: dict[int, tuple[Any, Callable[[], None]]] = {}      # mã đăng ký -> (chủ, hàm giết cả nhóm)
_CANCEL: dict[Any, threading.Event] = {}                    # chủ -> cờ huỷ


class ProcError(RuntimeError):
    """Tiến trình thoát mã khác 0. `stderr`: đuôi stderr (đã cắt theo trần)."""

    def __init__(self, cmd: Sequence[str], code: int | None, stderr: str):
        super().__init__(f"{os.path.basename(str(cmd[0]))} lỗi: " + stderr[-400:])
        self.cmd, self.code, self.stderr = list(cmd), code, stderr


@dataclass(frozen=True)
class ProcResult:
    stdout: bytes
    stderr: str
    seconds: float
    code: int = 0


# ---------------- chủ sở hữu (job) và huỷ ----------------

@contextlib.contextmanager
def owned_by(owner: Any) -> Iterator[threading.Event]:
    """Mọi tiến trình tạo ra trong khối này (kể cả trong task con / luồng to_thread) thuộc về `owner`."""
    ev = threading.Event()
    with _LOCK:
        _CANCEL[owner] = ev
    token = _OWNER.set(owner)
    try:
        yield ev
    finally:
        _OWNER.reset(token)
        with _LOCK:
            if _CANCEL.get(owner) is ev:
                del _CANCEL[owner]


def current_owner() -> Any:
    return _OWNER.get()


def cancelled() -> bool:
    """Job hiện tại đã bị huỷ chưa (cho vòng lặp đồng bộ dài, vd hook tiến độ của yt-dlp)."""
    with _LOCK:
        ev = _CANCEL.get(_OWNER.get())
    return bool(ev and ev.is_set())


def kill_owner(owner: Any) -> int:
    """Huỷ job: bật cờ huỷ và giết cả nhóm mọi tiến trình của job đó. Trả số tiến trình đã giết."""
    with _LOCK:
        ev = _CANCEL.get(owner)
        victims = [kill for o, kill in _LIVE.values() if o == owner]
    if ev:
        ev.set()
    for kill in victims:
        kill()
    return len(victims)


def kill_all() -> int:
    """Tắt bot: giết mọi tiến trình đang theo dõi."""
    with _LOCK:
        victims = [kill for _, kill in _LIVE.values()]
        events = list(_CANCEL.values())
    for ev in events:
        ev.set()
    for kill in victims:
        kill()
    return len(victims)


def running(owner: Any = None) -> int:
    """Số tiến trình đang chạy (của `owner`, hoặc tất cả khi None)."""
    with _LOCK:
        return sum(1 for o, _ in _LIVE.values() if owner is None or o == owner)


def _register(pid: int, fallback: Callable[[], None]) -> int:
    def kill() -> None:
        try:
            os.killpg(pid, signal.SIGKILL)        # start_new_session: pgid == pid -> giết cả cháu
        except ProcessLookupError:
            pass
        except OSError:
            with contextlib.suppress(Exception):
                fallback()
    rid = next(_IDS)
    with _LOCK:
        _LIVE[rid] = (_OWNER.get(), kill)
    return rid


def _unregister(rid: int) -> Callable[[], None] | None:
    with _LOCK:
        item = _LIVE.pop(rid, None)
    return item[1] if item else None


# ---------------- bất đồng bộ ----------------

async def _drain(stream: asyncio.StreamReader | None, keep: int | None) -> bytes:
    """Đọc hết một luồng; keep=None giữ tất cả, không thì chỉ giữ `keep` byte cuối."""
    if stream is None:
        return b""
    buf = bytearray()
    while chunk := await stream.read(1 << 16):
        buf += chunk
        if keep is not None and len(buf) > 2 * keep:
            del buf[: len(buf) - keep]
    return bytes(buf if keep is None else buf[-keep:])


async def run(cmd: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT, stdout: bool = False,
              stderr_keep: int | None = STDERR_KEEP, check: bool = True) -> ProcResult:
    """Chạy `cmd` (không qua shell). stdout=True: trả stdout nhị phân, không thì bỏ. stderr_keep=None: giữ toàn bộ
    stderr (cần khi phân tích log của ffmpeg, vd ebur128 / silencedetect). Quá giờ: TimeoutError, cả nhóm bị giết."""
    t0 = time.monotonic()
    p = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    rid = _register(p.pid, p.kill)

    async def _collect() -> tuple:
        out_t = asyncio.ensure_future(_drain(p.stdout, None)) if stdout else None
        try:
            err_b = await _drain(p.stderr, stderr_keep)
            out_b = await out_t if out_t else b""
        finally:
            if out_t and not out_t.done():
                out_t.cancel()
        return out_b, err_b, await p.wait()
    try:
        try:
            # wait_for (không dùng asyncio.timeout của 3.11): mã này cũng chạy trong notebook Kaggle
            out, err, code = await asyncio.wait_for(_collect(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{os.path.basename(str(cmd[0]))} quá {timeout:.0f}s") from None
    except BaseException:
        kill = _unregister(rid)
        if kill and p.returncode is None:
            kill()
        with contextlib.suppress(Exception):
            await asyncio.shield(p.wait())
        raise
    _unregister(rid)
    text = err.decode("utf-8", errors="replace")
    if check and code != 0:
        raise ProcError(cmd, code, text)
    return ProcResult(out, text, time.monotonic() - t0, code)


@contextlib.asynccontextmanager
async def spawn(cmd: Sequence[str], **kw: Any) -> Any:
    """Tiến trình async tự quản lý vào/ra (vd python con tách giọng): đăng ký theo job, ra khỏi khối mà còn chạy
    (lỗi / huỷ / quá giờ) thì giết cả nhóm."""
    kw.setdefault("stdin", asyncio.subprocess.DEVNULL)
    p = await asyncio.create_subprocess_exec(*cmd, start_new_session=True, **kw)
    rid = _register(p.pid, p.kill)
    try:
        yield p
    finally:
        kill = _unregister(rid)
        if kill and p.returncode is None:
            kill()
            with contextlib.suppress(Exception):
                await asyncio.shield(p.wait())


# ---------------- đồng bộ (trong luồng phụ) ----------------

def run_sync(args: Sequence[str], *, input: bytes | None = None, timeout: float | None = None, check: bool = False,
             text: bool = False, capture_output: bool = True) -> subprocess.CompletedProcess:
    """Như subprocess.run(capture_output=True) nhưng: phiên riêng + giết cả nhóm, đăng ký theo job (bấm Huỷ là dừng),
    luôn có trần thời gian. Quá giờ: subprocess.TimeoutExpired; check=True + mã khác 0: CalledProcessError."""
    del capture_output                                   # luôn bắt stdout/stderr (tham số giữ cho giống subprocess.run)
    p = subprocess.Popen(list(args), stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=text)
    rid = _register(p.pid, p.kill)
    try:
        try:
            out, err = p.communicate(input, timeout=timeout or DEFAULT_TIMEOUT)
        except subprocess.TimeoutExpired:
            kill = _unregister(rid)
            if kill:
                kill()
            out, err = p.communicate()
            raise subprocess.TimeoutExpired(p.args, timeout or DEFAULT_TIMEOUT, out, err) from None
    except BaseException:
        kill = _unregister(rid)
        if kill and p.poll() is None:
            kill()
            p.wait()
        raise
    _unregister(rid)
    if check and p.returncode:
        raise subprocess.CalledProcessError(p.returncode, p.args, out, err)
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


@contextlib.contextmanager
def popen(args: Sequence[str], **kw: Any) -> Iterator[subprocess.Popen]:
    """subprocess.Popen đăng ký theo job (đọc luồng ra từng phần); ra khỏi khối mà còn chạy thì giết cả nhóm."""
    p = subprocess.Popen(list(args), start_new_session=True, **kw)
    rid = _register(p.pid, p.kill)
    try:
        yield p
    finally:
        kill = _unregister(rid)
        if kill and p.poll() is None:
            kill()
            p.wait()
