"""Một chỗ duy nhất cho: thứ tự dịch vụ → thử lại khi lỗi tạm → đổi dịch vụ → bóc JSON → ghi số liệu.
Hiện logic này nằm rải ở 7 hàm (generate, generate_timed, post_only, shorten, cover.ai_judge, highlights,
transcribe._recheck_gemini), mỗi nơi một kiểu."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable


class ServiceError(Exception):
    def __init__(self, msg: str, *, retryable: bool = False, wait: float = 0.0):
        super().__init__(msg)
        self.retryable, self.wait = retryable, wait


@dataclass
class Provider:
    name: str
    call: Callable[..., Awaitable[str]]        # async (prompt, images, schema) -> text
    tries: int = 3
    max_wait: float = 45.0                     # chờ quá ngưỡng (hết hạn mức ngày) -> sang dịch vụ kế


def loads_lenient(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    if "{" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", t))      # dấu phẩy thừa cuối


async def complete(providers: list[Provider], prompt: str, *, images=None, schema=None, record=None) -> tuple[str, dict]:
    """Trả (tên dịch vụ đã trả lời, JSON). Ném ServiceError khi mọi dịch vụ đều lỗi."""
    last: Exception | None = None
    for k, p in enumerate(providers):
        for attempt in range(p.tries):
            try:
                raw = await p.call(prompt, images, schema)
                data = loads_lenient(raw)
                if record:
                    record(p.name, ok=True, fallback=k > 0)
                return p.name, data
            except ServiceError as e:
                last = e
                if record:
                    record(p.name, ok=False, fallback=k > 0, detail=str(e))
                if not e.retryable or attempt == p.tries - 1 or e.wait > p.max_wait:
                    break                                   # sang dịch vụ kế
                await asyncio.sleep(e.wait or 3.0 * (attempt + 1))
            except (json.JSONDecodeError, ValueError) as e:  # trả lời không phải JSON: thử lại một lần rồi đổi
                last = e
                if attempt >= 1:
                    break
        logging.warning("Dịch vụ %s không trả lời được (%s), thử dịch vụ kế", p.name, str(last)[:120])
    raise ServiceError(f"mọi dịch vụ đều lỗi: {last}")
