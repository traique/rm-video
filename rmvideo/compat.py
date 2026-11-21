"""Mặt tiền tương thích cho module cũ ở gốc repo (pipeline.py, web_panel.py) sau khi mã thật chuyển vào gói rmvideo/.

`install(module_name, routes)` biến module cũ thành mặt tiền: ĐỌC một tên cũ trả về đối tượng ở module mới; GHI một tên
(kể cả monkeypatch trong test) chuyển tới đúng module đang dùng tên đó, nên bản vá có tác dụng thật thay vì âm thầm chỉ
sửa một bản sao; xoá (monkeypatch hoàn tác) trả lại giá trị gốc.
"""
from __future__ import annotations

import sys
import types
from typing import Any

Routes = dict[str, tuple[types.ModuleType, str]]


def install(module_name: str, routes: Routes) -> None:
    original: dict[str, Any] = {}

    class _Facade(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            route = routes.get(name)
            if route is None:
                raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
            return getattr(*route)

        def __setattr__(self, name: str, value: Any) -> None:
            route = routes.get(name)
            if route is None:
                super().__setattr__(name, value)
                return
            original.setdefault(name, getattr(*route))
            setattr(route[0], route[1], value)            # vá đúng nơi tên được dùng

        def __delattr__(self, name: str) -> None:
            route = routes.get(name)
            if route is None:
                super().__delattr__(name)
            elif name in original:                        # hoàn tác bản vá: trả giá trị gốc cho module thật
                setattr(route[0], route[1], original.pop(name))

        def __dir__(self) -> list[str]:
            return sorted(set(super().__dir__()) | set(routes))

    sys.modules[module_name].__class__ = _Facade


def routes_of(*modules: types.ModuleType, skip: tuple[str, ...] = ()) -> Routes:
    """Mọi tên mức module (hàm, hằng, biến riêng) của các module -> chính module đó (module trước thắng)."""
    out: Routes = {}
    for mod in modules:
        for name, value in vars(mod).items():
            if name.startswith("__") or name in skip or isinstance(value, types.ModuleType):
                continue
            out.setdefault(name, (mod, name))
    return out
