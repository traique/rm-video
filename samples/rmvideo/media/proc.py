"""Chạy tiến trình con (ffmpeg/ffprobe/yt-dlp/tách giọng) đúng chuẩn production:
- có timeout; quá giờ hoặc bị huỷ (CancelledError) thì GIẾT CẢ NHÓM tiến trình (ffmpeg do con gọi cũng chết);
- đọc stderr có giới hạn (không nuốt RAM khi ffmpeg in hàng MB log);
- lỗi trả về ProcError có mã thoát + đuôi stderr, không ném chuỗi tự do;
- registry tiến trình đang chạy để huỷ job thì dọn hết."""
from __future__ import annotations
import asyncio
import os
import signal
from dataclasses import dataclass

RUNNING: set[asyncio.subprocess.Process] = set()
STDERR_KEEP = 16_000          # giữ ~16KB cuối của stderr để báo lỗi


class ProcError(RuntimeError):
    def __init__(self, cmd: list[str], code: int | None, stderr: str):
        super().__init__(f"{os.path.basename(cmd[0])} lỗi (mã {code}): {stderr[-400:]}")
        self.cmd, self.code, self.stderr = cmd, code, stderr


@dataclass(frozen=True)
class ProcResult:
    stdout: bytes
    stderr: str
    seconds: float


async def _drain(stream: asyncio.StreamReader, keep: int) -> bytes:
    """Đọc hết một luồng, chỉ giữ `keep` byte cuối (stderr của ffmpeg có thể rất dài)."""
    buf = bytearray()
    while chunk := await stream.read(1 << 16):
        buf += chunk
        if len(buf) > 2 * keep:
            del buf[: len(buf) - keep]
    return bytes(buf[-keep:])


def _kill_group(proc: asyncio.subprocess.Process):
    try:
        os.killpg(proc.pid, signal.SIGKILL)       # start_new_session=True: pid == pgid, giết cả cháu
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run(cmd: list[str], *, timeout: float, stdout_bytes: bool = False, nice: int = 0) -> ProcResult:
    """Chạy `cmd` (không shell). stdout_bytes=True: trả stdout nhị phân (vd rawvideo), nếu không thì bỏ stdout."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if stdout_bytes else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
        preexec_fn=(lambda: os.nice(nice)) if nice else None)
    RUNNING.add(proc)
    try:
        async with asyncio.timeout(timeout):
            out_task = asyncio.create_task(proc.stdout.read()) if stdout_bytes else None
            err = await _drain(proc.stderr, STDERR_KEEP)
            out = await out_task if out_task else b""
            code = await proc.wait()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _kill_group(proc)
        await proc.wait()
        raise
    finally:
        RUNNING.discard(proc)
    text = err.decode("utf-8", errors="replace")
    if code != 0:
        raise ProcError(cmd, code, text)
    return ProcResult(out, text, loop.time() - t0)


def kill_all():
    """Huỷ job / tắt bot: giết mọi tiến trình con đang chạy (cả nhóm của chúng)."""
    for p in list(RUNNING):
        _kill_group(p)
