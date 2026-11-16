"""Phase 0: chụp NGUYÊN VĂN mọi prompt gửi AI + cách gọi (ảnh, schema, nhãn, thời gian chờ, provider dự phòng).

Gom các vòng "Gemini -> OpenRouter" về một bộ định tuyến (Phase 3) KHÔNG được đổi một byte prompt nào: lời AI viết ra
là sản phẩm của bot. Đổi prompt có chủ đích thì chạy UPDATE_SNAPSHOTS=1 và duyệt diff tests/snapshots/prompts.json.
"""
import asyncio
import json
import os

import pytest

import config
import cover
import highlights
import prompt_engine

SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", "prompts.json")
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"

SEGS = [(0.5, 2.5, "hôm nay mình làm món thịt kho tộ nha"), (2.8, 5.5, "cho 500g thịt ba chỉ vào nồi"),
        (6.0, 9.0, "thêm 2 muỗng nước mắm và một chút đường"), (9.5, 12.0, "kho lửa nhỏ khoảng 30 phút"),
        (12.5, 15.0, "bấm vào giỏ hàng góc trái để mua nồi nha")]


class FakeAI:
    """Thay _call_gemini/_call_openrouter: ghi mọi lời gọi, trả lời theo kịch bản `replies` (lần lượt)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def gemini(self, prompt, images=None, schema=None, label="Ảnh đoạn", timeout=60):
        self.calls.append({"via": "gemini", "prompt": prompt, "images": len(images or []),
                           "schema": getattr(schema, "__name__", None), "label": label, "timeout": timeout})
        return self._next()

    async def openrouter(self, prompt):
        self.calls.append({"via": "openrouter", "prompt": prompt})
        return self._next()

    def _next(self):
        r = self.replies.pop(0) if self.replies else "{}"
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def ai(monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(prompt_engine, "set_voice_rate", prompt_engine.set_voice_rate)
    prompt_engine.set_voice_rate(None)

    def make(replies):
        f = FakeAI(replies)
        monkeypatch.setattr(prompt_engine, "_call_gemini", f.gemini)
        monkeypatch.setattr(prompt_engine, "_call_openrouter", f.openrouter)
        return f
    return make


def _timed_reply(texts, **extra):
    d = {"segments": [{"i": k, "text": t} for k, t in enumerate(texts)], "title": "Thịt kho tộ",
         "caption": "Đậm đà 😋", "hashtags": "#thitkho #nauan", "hook": "THỊT KHO MỀM RỤC"}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


async def _images(segs):
    return [b"\xff\xd8img" for _ in segs]


def _cases(ai):
    out = {}

    f = ai([json.dumps({"script": "Thịt kho tộ ngon tuyệt.", "title": "Thịt kho", "caption": "c", "hashtags": "#a"})])
    asyncio.run(prompt_engine.generate("Thịt kho tộ", "Mô tả gốc", "cook", "lời gốc ở đây", 60))
    out["generate"] = f.calls

    f = ai([RuntimeError("503 UNAVAILABLE"), json.dumps({"script": "Bản OpenRouter.", "title": "t"})])
    asyncio.run(prompt_engine.generate("Thịt kho tộ", "", "review", "", 30))
    out["generate_openrouter_fallback"] = f.calls

    f = ai([json.dumps({"title": "Cảnh nấu", "caption": "c", "hashtags": "#a", "hook": "XEM LÀ ĐÓI"})])
    asyncio.run(prompt_engine.post_only("Cá kho", "Mô tả", "cook"))
    out["post_only"] = f.calls

    good = ["Mở màn món thịt kho tộ đậm đà nè", "Thả 500g thịt ba chỉ vô nồi liền",
            "Rưới 2 muỗng nước mắm với chút đường cho thấm", "Hạ lửa liu riu kho chừng 30 phút", "Vậy là xong rồi"]
    f = ai([_timed_reply(good)])
    asyncio.run(prompt_engine.generate_timed_any("Thịt kho tộ", "Mô tả", "cook", SEGS, 16.0, images_for=_images))
    out["timed"] = f.calls

    copied = [t for *_, t in SEGS]                         # chép nguyên lời gốc -> bắt viết lại (prompt "BẢN TRƯỚC BỊ LOẠI")
    f = ai([_timed_reply(copied), _timed_reply(good)])
    asyncio.run(prompt_engine.generate_timed_any("Thịt kho tộ", "", "cook", SEGS, 16.0, images_for=_images,
                                                 teaser=3.0))
    out["timed_rewrite_teaser"] = f.calls

    f = ai([RuntimeError("400 INVALID_ARGUMENT image"), _timed_reply(good)])
    asyncio.run(prompt_engine.generate_timed_any("Thịt kho tộ", "", "cook", SEGS, 16.0, images_for=_images))
    out["timed_image_rejected"] = f.calls

    f = ai([RuntimeError("429 RESOURCE_EXHAUSTED"), _timed_reply(good)])
    asyncio.run(prompt_engine.generate_timed_any("Thịt kho tộ", "", "cook", SEGS, 16.0, images_for=_images))
    out["timed_openrouter_fallback"] = f.calls

    f = ai([json.dumps({"segments": [{"i": 2, "text": "Thêm 2 muỗng nước mắm"}]})])
    asyncio.run(prompt_engine.shorten([(2, "thêm 2 muỗng nước mắm và một chút đường cho thấm đều", 6)]))
    out["shorten"] = f.calls

    f = ai([_timed_reply(["Cảnh một", "Cảnh hai"])])
    asyncio.run(prompt_engine.generate_scenes("Cảnh đẹp", "", "cook", [(0.0, 3.0, ""), (3.0, 7.0, "")],
                                              [b"a", b"b"], 7.0))
    out["scenes"] = f.calls

    long_segs = [(k * 5.0, k * 5.0 + 4.0, f"câu thứ {k} nói về bước nấu") for k in range(40)]
    f = ai([json.dumps({"clips": [{"start": 10, "end": 50, "score": 8, "title": "Chiên", "reason": "đẹp"}]})])
    asyncio.run(highlights.choose("Video dài", "", 200.0, long_segs, [50.0, 100.0], [], [b"i1", b"i2"], [5.0, 15.0]))
    out["highlights"] = f.calls

    f = ai([json.dumps({"best": 1, "reason": "đẹp", "finished": True, "opens_with_dish": False})])
    asyncio.run(cover.ai_judge([b"a", b"b", b"c"], "Thịt kho", "cook", opening=[b"o1", b"o2"], opening_secs=6.0))
    out["cover_judge"] = f.calls
    return out


def test_prompts_are_byte_identical(ai):
    got = json.loads(json.dumps(_cases(ai), ensure_ascii=False))
    if UPDATE or not os.path.exists(SNAP):
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        with open(SNAP, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        if not UPDATE:
            pytest.fail("snapshot prompt chưa có: đã tạo, chạy lại để so")
        return
    want = json.load(open(SNAP, encoding="utf-8"))
    assert sorted(got) == sorted(want)
    for name in want:
        assert got[name] == want[name], f"prompt/cách gọi AI đổi ở ca {name}"
