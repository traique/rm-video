"""(Botkeep) Viết truyện bé bằng Gemini TRƯỚC khi gửi việc sang Kaggle (19/11: Qwen 35B 4-bit viết chưa mạch lạc).

- Cùng vòng kiểm/viết lại của llm.generate_story: GeminiBackend có .chat() như llama-server, nên bộ kiểm, phản hồi, câu dạy
  từ (story.add_beats) dùng chung. Mỗi truyện 1 lượt gọi API, thêm 1 lượt mỗi lần viết lại (tối đa 1 + llm.RETRIES) + 1 lượt
  soát. Hai model theo vai: KIDS_GEMINI_MODEL viết nháp, KIDS_GEMINI_REVISE_MODEL viết lại / soát (xem GeminiBackend).
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
    out["propertyOrdering"] = ["title", "hook", "hook_text", "prop_vi", "prop_en", "scenes", "description", "hashtags"]
    return out


class GeminiBackend:
    """Bộ viết thay llama-server: cùng giao diện start()/chat()/stop() cho llm.generate_story (chạy trong thread).

    Định tuyến theo vai (20/11): bản nháp = `model` (Gemini 4 Hydrogen, mức suy nghĩ `thinking`, hạn mức free/ngày nhỏ);
    viết lại theo lỗi bộ kiểm và lượt tự soát = `revise_model` (3.5 flash-lite: hạn mức rộng, nhanh). Model của vai quá tải /
    hết lượt thì model kia làm thay; cả hai lỗi thì ném ra để flow lùi về Qwen trên Kaggle."""

    def __init__(self, model: str = "", log: Any = logging.info, revise_model: str = "", thinking: str = ""):
        self.model = model or config.GEMINI_MODEL
        self.revise_model = revise_model or ""
        self.thinking = thinking
        self.log = log
        self.calls = 0
        self.revise = True          # thêm một lượt tự soát (liền mạch, hấp dẫn) — rẻ với Gemini, bỏ qua với Qwen
        self.used: list[tuple[str, str]] = []       # (vai, model) từng lượt gọi — ghi chú job, test

    def start(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Chưa có GEMINI_API_KEY")
        self.log(f"Truyện bé: viết bằng Gemini {self.model}"
                 + (f", sửa / soát bằng {self.revise_model}" if self.revise_model else ""))

    def model_for(self, role: str) -> str:
        return self.revise_model if role in ("rewrite", "revise") and self.revise_model else self.model

    def chat(self, messages: list[dict[str, str]], schema: dict, max_tokens: int = 0, temperature: float = 0.8,
             seed: int | None = None, role: str = "draft") -> str:
        import prompt_engine
        prompt = "\n\n".join(m["content"] for m in messages)
        model = self.model_for(role)
        try:
            return self._ask(model, prompt, schema, role)
        except Exception as e:
            alt = self.revise_model if model == self.model else self.model
            if not alt or alt == model or not prompt_engine._overloaded(str(e)):
                raise
            self.log(f"Truyện bé: {model} lỗi ({str(e)[:60]}), lượt {role} dùng {alt}")
            return self._ask(alt, prompt, schema, role)

    def _ask(self, model: str, prompt: str, schema: dict, role: str) -> str:
        import prompt_engine
        self.calls += 1
        self.used.append((role, model))
        # model truyền thẳng: không đổi config.GEMINI_MODEL chung (kênh nấu ăn gọi Gemini song song trên cùng tiến trình)
        text = asyncio.run(prompt_engine._call_gemini(prompt, schema=gemini_schema(schema), label="Truyện bé",
                                                      timeout=TIMEOUT, thinking=self.thinking, model=model))
        return text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)

    def summary(self) -> str:
        """«gemini-4-hydrogen ×1, gemini-3.5-flash-lite ×2» — model nào đã gọi mấy lượt."""
        counts: dict[str, int] = {}
        for _, m in self.used:
            counts[m] = counts.get(m, 0) + 1
        return ", ".join(f"{m} ×{n}" for m, n in counts.items())

    def stop(self) -> None:
        pass
