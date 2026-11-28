"""Phạm vi dữ liệu theo job: thứ chỉ sống trong một job thì chết cùng job đó.

Trước đây `scenes._ANALYZED` (thư mục ảnh thu nhỏ đã trích của từng video) là dict toàn cục khoá theo ĐƯỜNG DẪN
file, giữ 4 mục gần nhất: hai job dùng cùng đường dẫn (file giữ lại để duyệt rồi làm lại) đọc nhầm ảnh của lần trước,
video dài > 4 phần thì ảnh của phần đầu bị đẩy ra trước khi kịp làm ảnh bìa (C9), và mở thêm worker là hỏng ngay.

`job_scope()` (worker bọc mỗi job) mở một kho riêng; `ScopedDict` trỏ vào kho của job hiện tại (contextvar: tự
truyền vào task con và luồng to_thread), ngoài job (test, Kaggle, lệnh console) dùng dict chung có trần như cũ.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, MutableMapping
from typing import Any

_SCOPE: contextvars.ContextVar[dict[str, dict] | None] = contextvars.ContextVar("job_scope", default=None)


@contextlib.contextmanager
def job_scope() -> Iterator[dict[str, dict]]:
    store: dict[str, dict] = {}
    token = _SCOPE.set(store)
    try:
        yield store
    finally:
        _SCOPE.reset(token)
        store.clear()


def in_job() -> bool:
    return _SCOPE.get() is not None


class ScopedDict(MutableMapping):
    """Dict riêng của job đang chạy; ngoài job là dict chung (trim() giữ tối đa n mục như hành vi cũ)."""

    def __init__(self, name: str):
        self.name = name
        self._shared: dict[Any, Any] = {}

    def _d(self) -> dict[Any, Any]:
        scope = _SCOPE.get()
        return self._shared if scope is None else scope.setdefault(self.name, {})

    def __getitem__(self, k: Any) -> Any:
        return self._d()[k]

    def __setitem__(self, k: Any, v: Any) -> None:
        self._d()[k] = v

    def __delitem__(self, k: Any) -> None:
        del self._d()[k]

    def __iter__(self) -> Iterator[Any]:
        return iter(list(self._d()))

    def __len__(self) -> int:
        return len(self._d())

    def trim(self, n: int) -> None:
        """Ngoài job: chỉ giữ n mục mới nhất. Trong job: giữ hết (kho bị bỏ khi job xong)."""
        if _SCOPE.get() is None:
            while len(self._shared) > n:
                self._shared.pop(next(iter(self._shared)))
