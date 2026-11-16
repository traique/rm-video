"""Định tuyến AI viết lời: thứ tự nhà cung cấp, đổi sang dự phòng khi lỗi, ghi số liệu — một chỗ.

Trước đây prompt_engine (generate, post_only, generate_timed, shorten), highlights.choose mỗi nơi tự dựng danh sách
"Gemini nếu có key -> OpenRouter nếu có key", tự vòng lặp thử, tự ghi metrics và log. Phần gọi từng nhà cung cấp
(thử lại khi quá tải, tự đổi model bị ngừng, dò model miễn phí) vẫn ở prompt_engine.call_gemini/call_openrouter;
phần bóc kết quả riêng từng việc do caller truyền vào `attempt`.

Prompt KHÔNG đi qua đây để biến đổi: tests/test_snapshot_prompts.py chụp nguyên văn mọi prompt.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")
Call = Callable[..., Awaitable[str]]
Recorder = Callable[..., None]


@dataclass(frozen=True)
class Provider:
    name: str          # tên hiện trong log ("Gemini", "OpenRouter"); số liệu dùng chữ thường
    call: Call


def providers(gemini: Call | None, openrouter: Call | None, *, gemini_ok: bool, openrouter_ok: bool,
              names: tuple[str, str] = ("Gemini", "OpenRouter")) -> list[Provider]:
    """Thứ tự cố định: Gemini (nếu có thư viện + key) rồi OpenRouter (nếu có key)."""
    out = []
    if gemini is not None and gemini_ok:
        out.append(Provider(names[0], gemini))
    if openrouter is not None and openrouter_ok:
        out.append(Provider(names[1], openrouter))
    return out


async def first_success(chain: Sequence[Provider], attempt: Callable[[Provider], Awaitable[T]], *,
                        record: Recorder | None = None, kind: str = "llm",
                        on_error: Callable[[Provider, BaseException], Any] | None = None) -> T | None:
    """Thử lần lượt từng nhà cung cấp: `attempt(p)` trả kết quả đã bóc hoặc ném lỗi (kể cả kết quả không dùng được).
    Thành công: ghi số liệu (fallback=True nếu không phải nhà cung cấp đầu) và trả kết quả. Hết danh sách: None
    (caller tự quyết mẫu dự phòng). Lỗi của một nhà cung cấp không bao giờ lan ra ngoài."""
    for k, p in enumerate(chain):
        try:
            out = await attempt(p)
        except Exception as e:
            if record:
                record(kind, p.name.lower(), ok=False, fallback=k > 0, detail=str(e))
            if on_error:
                on_error(p, e)
            else:
                logging.warning("AI %s lỗi (%s), thử nhà cung cấp kế", p.name, str(e)[:120])
            continue
        if record:
            record(kind, p.name.lower(), fallback=k > 0)
        return out
    return None
