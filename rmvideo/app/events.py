"""Bus sự kiện "job đã đổi" thay cho các vòng hỏi lại theo giờ.

Trước đây ba vòng lặp tự hỏi `job.step` theo nhịp: ghi timeline (1 giây), chép tiến độ phần con lên job cha
(2 giây), luồng SSE của web (1 giây/khách, dựng lại toàn bộ danh sách job mỗi lần). Giờ Job báo khi trường hiển thị
đổi (queue_manager.Job.__setattr__) -> `changed()` tăng số phiên bản và đánh thức người chờ; bước (step) đổi thì gọi
thêm các hàm theo dõi riêng của job đó (ghi timeline, chép lên job cha) ngay lúc đổi, không trễ, không sót bước.

An toàn đa luồng: `changed()` / `step_changed()` được gọi cả từ luồng phụ (vd tiến độ Kaggle trong asyncio.to_thread).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"version": 0}
_WAITERS: set[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = set()
_STEP_WATCH: dict[int, list[Callable[[str], None]]] = {}     # id(job) -> hàm nhận bước mới


def version() -> int:
    return int(_STATE["version"])


def changed() -> None:
    """Có job đổi (trạng thái, bước, kết quả...): tăng phiên bản, đánh thức mọi người đang chờ."""
    with _LOCK:
        _STATE["version"] += 1
        waiters = list(_WAITERS)
        _WAITERS.clear()
    for loop, fut in waiters:
        if loop.is_closed():
            continue
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_wake, fut)


def _wake(fut: asyncio.Future) -> None:
    if not fut.done():
        fut.set_result(None)


async def wait_change(since: int, timeout: float) -> int:
    """Chờ tới khi phiên bản khác `since` (hoặc hết `timeout` giây). Trả phiên bản hiện tại."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    with _LOCK:
        if _STATE["version"] != since:
            return int(_STATE["version"])
        item = (loop, fut)
        _WAITERS.add(item)
    try:
        await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        with _LOCK:
            _WAITERS.discard(item)
    return version()


def step_changed(job: Any, step: str) -> None:
    """Gọi khi job.step đổi: chạy các hàm theo dõi của job (lỗi của chúng không bao giờ lan ra pipeline)."""
    with _LOCK:
        fns = list(_STEP_WATCH.get(id(job), ()))
    for fn in fns:
        try:
            fn(step)
        except Exception:
            logging.exception("Theo dõi bước của job lỗi")


@contextlib.contextmanager
def watch_steps(job: Any, fn: Callable[[str], None]) -> Iterator[None]:
    """Trong khối này, mỗi lần job.step đổi thì gọi fn(bước mới) (ngay tại chỗ đổi, kể cả từ luồng phụ)."""
    key = id(job)
    with _LOCK:
        _STEP_WATCH.setdefault(key, []).append(fn)
    try:
        yield
    finally:
        with _LOCK:
            lst = _STEP_WATCH.get(key, [])
            if fn in lst:
                lst.remove(fn)
            if not lst:
                _STEP_WATCH.pop(key, None)
