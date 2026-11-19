"""(Botkeep) Viết truyện bé bằng Gemini TRƯỚC khi gửi việc sang Kaggle (19/11: Qwen 35B 4-bit viết chưa mạch lạc).

- Cùng vòng kiểm/viết lại của llm.generate_story: GeminiBackend có .chat() như llama-server, nên bộ kiểm, phản hồi, câu dạy
  từ (story.add_beats) dùng chung. Mỗi truyện 1 lượt gọi API, thêm 1 lượt mỗi lần viết lại (tối đa 1 + llm.RETRIES).
- Gọi qua prompt_engine._call_gemini: tự thử lại khi quá tải / hết hạn mức, đổi model dự phòng (GEMINI_FALLBACK_MODELS), đổi
  model khi Google ngừng model cũ. Key Gemini KHÔNG bao giờ sang Kaggle: truyện viết xong nằm trong spec["story"].
- Schema cho Gemini: bỏ minLength/maxLength/additionalProperties (Gemini không nhận), giữ enum/minItems/maxItems/required.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any

import config

TIMEOUT = 120.0       # giây cho một lượt (truyện ngắn, flash-lite ~5-15 giây)
_DROP = {"minLength", "maxLength", "additionalProperties"}


def gemini_schema(schema: dict) -> dict:
    """Schema JSON của prompts.schema() -> dạng Gemini nhận (bỏ các khoá không hỗ trợ, đệ quy)."""
    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items() if k not in _DROP}
        if isinstance(node, list):
            return [clean(x) for x in node]
        return node
    out = clean(copy.deepcopy(schema))
    out["propertyOrdering"] = ["title", "hook", "hook_text", "scenes", "description", "hashtags"]
    return out


class GeminiBackend:
    """Bộ viết thay llama-server: cùng giao diện start()/chat()/stop() cho llm.generate_story (chạy trong thread)."""

    def __init__(self, model: str = "", log: Any = logging.info):
        self.model = model or config.GEMINI_MODEL
        self.log = log
        self.calls = 0
        self.revise = True          # thêm một lượt tự soát (liền mạch, hấp dẫn) — rẻ với Gemini, bỏ qua với Qwen

    def start(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Chưa có GEMINI_API_KEY")
        self.log(f"Truyện bé: viết bằng Gemini {self.model}")

    def chat(self, messages: list[dict[str, str]], schema: dict, max_tokens: int = 0, temperature: float = 0.8,
             seed: int | None = None) -> str:
        import prompt_engine
        prompt = "\n\n".join(m["content"] for m in messages)
        self.calls += 1
        old = config.GEMINI_MODEL
        try:
            if self.model != old:
                config.GEMINI_MODEL = self.model              # _call_gemini đọc config.GEMINI_MODEL
            text = asyncio.run(prompt_engine._call_gemini(prompt, schema=gemini_schema(schema), label="Truyện bé",
                                                          timeout=TIMEOUT))
        finally:
            if config.GEMINI_MODEL == self.model and self.model != old:
                config.GEMINI_MODEL = old
        return text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)

    def stop(self) -> None:
        pass
