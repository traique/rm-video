"""Sinh trọn gói nội dung: kịch bản voiceover + tiêu đề + caption + hashtag để đăng luôn.
2 provider: Gemini -> OpenRouter (chuẩn OpenAI-compatible) -> mẫu dự phòng.
LUẬT CỨNG: giữ nguyên văn định lượng công thức, giá, địa chỉ, tên quán/thương hiệu."""
import asyncio
import contextvars
import difflib
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from typing import Literal

import aiohttp

import config
import ingredients
import metrics
from rmvideo.domain import dialect
from rmvideo.services import llm
import vocab

try:
    from google import genai
except Exception:
    genai = None

# Schema đầu ra cho Gemini (structured output): API trả JSON ĐÚNG khuôn, không còn phải cứu JSON hỏng bằng regex.
# loads_lenient vẫn giữ cho OpenRouter (model free không hỗ trợ schema).
try:
    from pydantic import BaseModel

    class SegOut(BaseModel):
        i: int
        text: str

    class TimedOut(BaseModel):
        segments: list[SegOut]
        title: str
        caption: str
        hashtags: str
        hook: str = ""

    class ShortOut(BaseModel):
        segments: list[SegOut]

    class ScriptOut(BaseModel):
        script: str
        title: str
        caption: str
        hashtags: str

    class PostOut(BaseModel):
        title: str
        caption: str
        hashtags: str
        hook: str = ""

    class SegOstOut(BaseModel):
        i: int
        text: str
        ost: Literal["new", "orig", "both"]

    class TimedOstOut(BaseModel):
        segments: list[SegOstOut]
        title: str
        caption: str
        hashtags: str
        hook: str = ""
    # mọi trường BẮT BUỘC: trường có giá trị mặc định thì Gemini hay bỏ qua (chạy thật 16/11: "text" của mọi bước rỗng)
    class IngOut(BaseModel):
        name: str
        qty: str
        unit: str
        seg: int
        quote: str
        first_img: int

    class StepOut(BaseModel):
        kind: str
        a: int                          # "from"/"to" là từ khoá Python: schema dùng a/b, prompt ghi rõ
        b: int
        text: str
        ingredients: list[str]
        independent: bool

    class TipOut(BaseModel):
        text: str
        seg: int

    class RecipeOut(BaseModel):
        is_recipe: bool
        dish: str
        servings: str
        ingredients: list[IngOut]
        steps: list[StepOut]
        tips: list[TipOut]
except Exception:                       # pydantic đi kèm google-genai; thiếu thì chỉ dùng JSON mode
    TimedOut = ShortOut = ScriptOut = TimedOstOut = PostOut = RecipeOut = None

_STYLE = {
    "mien_tay": """

GIỌNG VĂN MIỀN TÂY NAM BỘ (bắt buộc): viết như người miền Tây đang nói chuyện thân tình với bà con.
- Xưng "tui" hoặc "mình", gọi người xem "bà con", "cả nhà", "mấy bà", "mấy ông".
- Từ ngữ miền Nam: chén (không dùng bát), muỗng (không dùng thìa), heo (không dùng lợn), trái (không dùng quả), bắp (ngô), đậu phộng (lạc), ngò (rau mùi), thơm/khóm (dứa), kêu (gọi), xài (dùng), thiệt (thật), hổng (không), nhứt (nhất), mắc (đắt), bự (to), dzô dùng chữ "vô", "ra đây nè".
- Tiếng đệm cuối câu tự nhiên: nè, nha, nghen, hen, á, đó, luôn, vậy đó. Cảm thán: "trời ơi", "ngon dữ thần", "ngon hết sảy", "quá trời quá đất", "bá cháy".
- Vừa phải, không lố: mỗi câu tối đa 1 tiếng đệm, không câu nào cũng "nè". Viết đúng chính tả chuẩn (viết "vậy", "vô", không viết "dzậy", "zô") để giọng đọc phát âm đúng.
- Đổi từ địa phương KHÔNG đổi số liệu: "2 thìa" thành "2 muỗng" được, nhưng con số giữ nguyên.""",
    "mien_nam": """

GIỌNG VĂN MIỀN NAM (bắt buộc): viết như người Sài Gòn / Đồng Nai đang chỉ nhau nấu ăn, gần gũi mà gọn gàng.
- Xưng "mình", gọi người xem "mọi người", "cả nhà".
- Từ ngữ miền Nam: chén (không dùng bát), muỗng (không dùng thìa), heo (không dùng lợn), trái (không dùng quả), bắp (ngô), đậu phộng (lạc), ngò (rau mùi), thơm/khóm (dứa), trụng (không dùng trần / chần), xắt (thái), xài (dùng), thiệt (thật), mắc (đắt), bự (to), "vô" (vào).
- Tiếng đệm cuối câu tự nhiên: nha, nè, á, luôn, đó. Cảm thán: "ngon dữ luôn", "đậm đà khỏi chê", "bắt cơm lắm luôn", "thơm nức mũi".
- Vừa phải, không lố: mỗi câu tối đa 1 tiếng đệm. Không dùng giọng miền Tây đậm ("bà con", "nghen", "hen", "dữ thần", "hết sảy"). Viết đúng chính tả chuẩn để giọng đọc phát âm đúng.
- Đổi từ địa phương KHÔNG đổi số liệu: "2 thìa" thành "2 muỗng" được, nhưng con số giữ nguyên.""",
    "pho_thong": "",
}


_GLOSSARY: contextvars.ContextVar[tuple] = contextvars.ContextVar("glossary", default=())


def set_glossary(names) -> None:
    """Tên nguyên liệu ĐÚNG của món đang làm (AI đọc công thức đã sửa lỗi máy nghe): các lời nhắc viết lời của job này
    kèm danh sách, và kiểm "bịa nguyên liệu" chấp nhận tên trong danh sách (lời gốc "nấm mặn" -> lời mới "nước mắm")."""
    _GLOSSARY.set(tuple(dict.fromkeys(n for n in names if n)))


def _glossary_hint() -> str:
    names = _GLOSSARY.get()
    return ("" if not names else "\n\nTÊN NGUYÊN LIỆU ĐÚNG CỦA MÓN NÀY (lời gốc do máy nghe nên có thể viết sai, ví dụ "
            "\"nấm mặn\" là \"nước mắm\"): " + ", ".join(names) + ". Gặp chữ máy nghe sai thì viết đúng theo danh sách này.")


def _not_in_glossary(added: list) -> list:
    names = _GLOSSARY.get()
    if not names:
        return added
    ok = set().union(*(ingredients.find(n) for n in names))
    loose = [ingredients._loose(n).strip() for n in names]
    # "tỏi phi" là dạng của "tỏi" trong danh sách: không tính là bịa
    return [x for x in added if not ingredients.find(x) & ok
            and not any(n and f" {n} " in ingredients._loose(x) for n in loose)]


_KIND_VI = {"hook": "câu mở đầu", "action": "thao tác", "wait": "lúc chờ", "tip": "mẹo", "plate": "bày món",
            "react": "cảm thán", "recap": "tóm tắt", "close": "chào kết", "bank": "đã duyệt"}


def voice_block(examples: list | None) -> str:
    """Cặp mẫu «lời gốc -> lời kênh» (rmvideo/app/voice_book): AI học cách viết theo ví dụ, không chép."""
    if not examples:
        return ""
    lines = "\n".join(f"- [{_KIND_VI.get(x.get('kind', ''), 'mẫu')}] Gốc: «{x['orig']}» -> Viết: «{x['new']}»"
                       for x in examples)
    return ("\n\nGIỌNG KÊNH — học theo CÁCH VIẾT của các cặp mẫu dưới đây (độ dài câu, từ nối, nhịp nói, kết câu đổi "
            "qua lại, không lạm dụng \"nha\"). Mẫu là món KHÁC: KHÔNG chép câu mẫu, KHÔNG lấy nguyên liệu / con số / "
            "thao tác trong mẫu; chỉ viết về đúng video này.\n" + lines)


def style_block() -> str:
    """Giọng văn (SPEECH_STYLE) + từ điển của kênh (ASR_VOCAB) [+ tên nguyên liệu đúng của món, khi có]: mọi lời
    nhắc viết lời đều kèm khối này."""
    return _STYLE.get(config.SPEECH_STYLE, "") + vocab.prompt_hint() + _glossary_hint()


def _norm_words(t: str) -> list:
    return [w for w in re.sub(r"[^\w\s]", " ", (t or "").lower()).split() if w]


def copy_ratio(new: str, orig: str) -> float:
    """Tỉ lệ cụm 2 từ của lời mới trùng với lời gốc (0 = hoàn toàn mới, 1 = chép nguyên)."""
    a, b = ingredients.mask(new), ingredients.mask(orig)
    if len(a) < 4 or len(b) < 2:
        return 0.0
    bn = {tuple(a[i:i + 2]) for i in range(len(a) - 1) if "§" not in a[i:i + 2]}
    bo = {tuple(b[i:i + 2]) for i in range(len(b) - 1) if "§" not in b[i:i + 2]}
    if not bn:
        return 0.0
    return len(bn & bo) / max(1, len(bn))


_MODE_VI = {"cook": "video nấu ăn ASMR", "review": "video review sản phẩm/quán", "music": "video nhảy/âm nhạc giải trí",
            "photo": "video ảnh ghép theo nhạc (slideshow)"}

PROMPT = """Bạn là biên kịch kiêm người viết content video ngắn tiếng Việt.

LUẬT BẮT BUỘC, VI PHẠM LÀ HỎNG:
- Mọi ĐỊNH LƯỢNG công thức (gam, ml, muỗng, quả, phút, nhiệt độ...), GIÁ TIỀN, ĐỊA CHỈ, số điện thoại, tên quán/thương hiệu xuất hiện trong lời thoại gốc hoặc mô tả phải được giữ NGUYÊN VĂN trong kịch bản. Không làm tròn, không đổi đơn vị, không dịch tên riêng, không bịa thêm số liệu không có trong nguồn.
- Nếu nguồn không có thông tin nào thì tuyệt đối không tự chế ra.
- Các BƯỚC thực hiện (nấu ăn, hướng dẫn) phải giữ ĐÚNG THỨ TỰ và ĐỦ SỐ BƯỚC như lời thoại gốc: không gộp, không đảo, không bỏ bước nào (kể cả bước nhỏ như lật mặt, cho nước sốt, bắc ra). Mỗi bước trong lời gốc là một câu riêng trong kịch bản; hai thao tác khác nhau (ví dụ "cắt dải dài" rồi "thái miếng") không được gộp thành một. Nếu thiếu chỗ, rút ngắn câu hook và lời bình, không được rút bước.
- Số từ phải ĐỦ như yêu cầu bên dưới (thiếu từ thì giọng đọc hết sớm, video bị câm phần cuối): nếu các bước đã đủ mà còn dư chỗ, thêm lời bình, mẹo, cảm nhận giữa các bước cho đủ.

Nhiệm vụ: viết voiceover gồm 1 câu hook giật gân (~3 giây đọc) nối liền nội dung chính, tổng khoảng {words} từ ({dur_note}).

CÁCH VIẾT (quan trọng, để không bị nhận ra là chép lại):
- DIỄN ĐẠT LẠI bằng lời của riêng bạn: không chép nguyên câu nào của lời thoại gốc (trừ số liệu, tên riêng), đổi cấu trúc câu, dùng từ đồng nghĩa đời thường.
- Viết như đang NÓI CHUYỆN với người xem: câu ngắn 6-12 từ, dùng dấu phẩy và dấu chấm để ngắt hơi, thỉnh thoảng chen 1 câu cảm thán hoặc mẹo nhỏ ("nhìn kìa", "đấy", "mẹo là...") giữa các bước, kết bằng lời chốt tự nhiên về món (KHÔNG kêu gọi đăng ký / theo dõi / follow / like / share kênh).
- Vẫn tuân thủ tuyệt đối LUẬT BẮT BUỘC ở trên: đủ bước, đúng thứ tự, nguyên văn mọi định lượng.

Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích:
{{"script": "văn bản voiceover thuần để đọc thành tiếng, không emoji",
"title": "tiêu đề đăng bài <= 90 ký tự, hấp dẫn",
"caption": "caption 2-3 câu để đăng TikTok/Facebook, được dùng emoji, nhắc lại địa chỉ/định lượng quan trọng nếu có",
"hashtags": "5-8 hashtag tiếng Việt + tiếng Anh cách nhau bởi dấu cách"}}

Thể loại: {mode}
Tiêu đề gốc: {title}
Mô tả gốc: {desc}
Lời thoại gốc trong video (Whisper, có thể là ngôn ngữ khác, dựa vào đây là chính): {transcript}"""


# Tốc độ đọc đo thực tế: giọng CapCut ~4.0 từ/giây, edge-tts (+8%) ~2.8 từ/giây
WORDS_PER_SEC = {"capcut": 4.0, "edge": 2.8}


def _word_target(duration: int) -> tuple[str, str]:
    rate = WORDS_PER_SEC.get(config.TTS_PROVIDER, 2.8)
    if duration <= 0:
        n = int(18 * rate)
        return f"{n - 10}-{n}", "đọc xong trong khoảng 15-20 giây"
    n = max(15, int((duration - 1.5) * rate))   # trừ 1.5s cuối để không bị cụt; video 4-7s: khoảng không bị ngược
    return f"{max(5, n - 10)}-{n}", f"đọc vừa khít video {duration} giây"


def _build_prompt(title, desc, mode, transcript, duration):
    words, dur_note = _word_target(duration)
    return style_block() + "\n\n" + PROMPT.format(
        words=words, dur_note=dur_note, mode=_MODE_VI.get(mode, mode),
        title=title or "(không có)", desc=(desc or "(không có)")[:500],
        transcript=(transcript or "(không có)")[:1500],
    )


def _contents(prompt: str, images=None, label: str = "Ảnh đoạn"):
    if not images:
        return prompt
    from google.genai import types
    parts = [prompt]
    for k, img in enumerate(images):
        if img:
            parts += [f"{label} {k}:", types.Part.from_bytes(data=img, mime_type="image/jpeg")]
    return parts


def retry_delay(err: str, attempt: int) -> float | None:
    """Số giây nên chờ trước khi gọi lại Gemini; None = lỗi không đáng thử lại.
    503/UNAVAILABLE (quá tải): 3s, 6s. 429/RESOURCE_EXHAUSTED (hết hạn mức): theo retryDelay Google trả
    về nếu có (vd "retryDelay": "17s", "retry in 17.5s"). Phải chờ quá 45s (thường là hết hạn mức NGÀY)
    thì không chờ: rơi ngay sang provider/đường dự phòng thay vì ăn hết hạn giờ của job."""
    if "429" in err or "RESOURCE_EXHAUSTED" in err:
        m = re.search(r"retry(?:Delay|\s+in)\W*([0-9.]+)\s*s", err, re.IGNORECASE)
        wait = float(m.group(1)) + 1 if m else 10.0 * (attempt + 1)
        return wait if wait <= MAX_QUOTA_WAIT else None
    if "503" in err or "UNAVAILABLE" in err or "500" in err or "INTERNAL" in err:
        return 3.0 * (attempt + 1)
    return None


GEMINI_TRIES = 3
MAX_QUOTA_WAIT = 45.0


def is_quota_error(err: str) -> bool:
    return "429" in err or "RESOURCE_EXHAUSTED" in err


def _schema_rejected(err: str) -> bool:
    return "400" in err or "INVALID_ARGUMENT" in err or "schema" in err.lower()


def _with_thinking(cfg, model: str, level: str):
    """Mức suy nghĩ low/medium/high/xhigh cho Gemini 4 (mặc định của Google là high: chậm, tốn token); model khác giữ nguyên."""
    if not level or not model.startswith("gemini-4"):
        return cfg
    c = dict(cfg or {})
    c["thinking_config"] = {"thinking_level": level}
    return c


async def _call_gemini(prompt: str, images=None, schema=None, label: str = "Ảnh đoạn", timeout: float = 60,
                       thinking: str = "") -> str:
    """Gọi Gemini: thử lại khi quá tải/hết hạn mức, tự đổi model khi model cũ bị ngừng (404).
    schema (lớp pydantic): bật structured output; model không nhận schema thì lùi về JSON mode thường.
    thinking: mức suy nghĩ cho Gemini 4 (truyện bé), xem _with_thinking."""
    client = genai.Client(api_key=config.GEMINI_API_KEY)  # key đọc lúc gọi: đổi từ web là ăn ngay
    contents = _contents(prompt, images, label)
    json_only = {"response_mime_type": "application/json"} if "JSON" in prompt else None
    cfg = {"response_mime_type": "application/json", "response_schema": schema} if schema else json_only

    async def _gen(model: str, cfg):
        cfg = _with_thinking(cfg, model, thinking)
        for attempt in range(GEMINI_TRIES):
            try:
                return await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=contents, config=cfg), timeout)
            except Exception as e1:
                wait = retry_delay(str(e1), attempt)
                if wait is None or attempt == GEMINI_TRIES - 1:
                    raise
                logging.warning("Gemini lỗi tạm (%s), thử lại sau %.0fs", str(e1)[:80], wait)
                await asyncio.sleep(wait)

    async def _one(model: str):
        nonlocal cfg
        try:
            try:
                return await _gen(model, cfg)
            except Exception as e:
                if not (schema and _schema_rejected(str(e))):
                    raise
                logging.warning("Gemini không nhận schema (%s), dùng JSON mode thường", str(e)[:80])
                cfg = json_only or {"response_mime_type": "application/json"}
                return await _gen(model, cfg)
        except Exception as e:
            # Google ngừng model cũ và ghi tên model thay thế ngay trong lỗi 404
            m = re.search(r"use models/([\w.\-]+)", str(e))
            if "404" not in str(e) or not m or model != config.GEMINI_MODEL:
                raise
            new_model = m.group(1)
            logging.warning("Model %s đã ngừng, tự chuyển sang %s và lưu vào Cài đặt", model, new_model)
            config.save_settings({"GEMINI_MODEL": new_model})
            return await _gen(new_model, cfg)

    try:
        resp = await _one(config.GEMINI_MODEL)
    except Exception as e:
        # model chính quá tải (503 "high demand", đã thử lại) / hết lượt: thử model dự phòng (hạn mức, tải riêng) thay
        # vì bỏ cả bước (bản 43: cả job viết lời mù vì 3.5-flash-lite quá tải suốt 5 phút)
        backups = [m for m in fallback_models() if m != config.GEMINI_MODEL]
        if not backups or not _overloaded(str(e)):
            raise
        last: Exception = e
        for model in backups:
            logging.warning("Gemini %s lỗi (%s), thử model dự phòng %s", config.GEMINI_MODEL, str(last)[:60], model)
            try:
                resp = await _one(model)
                break
            except Exception as e2:
                last = e2
                if not (_overloaded(str(e2)) or "404" in str(e2) or "NOT_FOUND" in str(e2)):
                    raise
        else:
            raise last
    return resp.text or ""


def fallback_models() -> list[str]:
    return [m for m in re.split(r"[,\s]+", getattr(config, "GEMINI_FALLBACK_MODELS", "") or "") if m]


def _overloaded(err: str) -> bool:
    """Lỗi của riêng model đang gọi (quá tải / hết lượt / quá giờ) — model khác có thể vẫn chạy được."""
    return (not err or retry_delay(err, 0) is not None or is_quota_error(err) or "high demand" in err
            or "DEADLINE" in err or "Timeout" in err)


def _image_rejected(err: str) -> bool:
    """Lỗi do ẢNH (model không nhận ảnh, ảnh hỏng / quá nặng) — khác lỗi quá tải: quá tải mà bỏ ảnh thì AI viết lời
    không nhìn hình, câu chữ lệch cảnh (bản 43)."""
    e = err.lower()
    return not _overloaded(err) and any(x in e for x in ("image", "inline", "mime", "too large", "413", "payload",
                                                        "invalid_argument", "400"))


_free_cache = {"at": 0.0, "models": []}


def _param_size(model_id: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)b\b", model_id.lower())
    return float(m.group(1)) if m else 0.0


async def free_models() -> list[str]:
    """Model free trên OpenRouter, lớn trước; cache 1 giờ. Bỏ model không phải text (nhạc, ảnh)."""
    if time.time() - _free_cache["at"] < 3600 and _free_cache["models"]:
        return _free_cache["models"]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{config.OPENROUTER_BASE_URL.rstrip('/')}/models",
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = (await r.json()).get("data", [])
    except Exception:
        return _free_cache["models"]
    ids = []
    for m in data:
        pr = m.get("pricing") or {}
        mod = m.get("architecture") or {}
        if pr.get("prompt") == "0" and pr.get("completion") == "0" \
                and "text" in (mod.get("output_modalities") or ["text"]) \
                and not any(x in m["id"] for x in ("lyria", "alpha", "image", "audio")):
            ids.append(m["id"])
    ids.sort(key=lambda i: -_param_size(i))
    if ids:
        _free_cache.update(at=time.time(), models=ids)
    return ids


async def _openrouter_once(model: str, prompt: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{config.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            timeout=aiohttp.ClientTimeout(total=45),   # ngắn: tầng dự phòng không được treo
        ) as r:
            if r.status != 200:
                raise RuntimeError(f"{model}: {r.status} {(await r.text())[:120]}")
            d = await r.json()
            return d["choices"][0]["message"]["content"]


async def _call_openrouter(prompt: str) -> str:
    chosen = (config.OPENROUTER_MODEL or "auto").strip()
    candidates = [] if chosen in ("", "auto") else [chosen]
    candidates += [m for m in await free_models() if m not in candidates][:4]
    last = None
    for model in candidates:
        try:
            out = await _openrouter_once(model, prompt)
            logging.info("OpenRouter dùng model %s", model)
            return out
        except Exception as e:
            last = e
            logging.warning("OpenRouter %s lỗi, thử model kế: %s", model, str(e)[:120])
    raise RuntimeError(f"OpenRouter: không model nào trả lời ({last})")


async def call_gemini(prompt: str, *args, **kwargs) -> str:
    """API công khai cho module khác (ảnh bìa, đoạn hay, kiểm tra định kỳ) — trước đây gọi thẳng _call_gemini riêng
    tư. Tham số như _call_gemini(prompt, images=None, schema=None, label=..., timeout=...), chuyển nguyên trạng."""
    return await _call_gemini(prompt, *args, **kwargs)


async def call_openrouter(prompt: str) -> str:
    return await _call_openrouter(prompt)


def _chain(gemini_call) -> list:
    """Gemini (nếu có thư viện + key) rồi OpenRouter (nếu có key). Hàm gọi tra lúc chạy (test thay được)."""
    return llm.providers(gemini_call, lambda pr: _call_openrouter(pr), gemini_ok=bool(genai and config.GEMINI_API_KEY),
                         openrouter_ok=bool(config.OPENROUTER_API_KEY))


def parse_pack(text: str, fallback_title: str) -> dict:
    """Bóc JSON kể cả khi model bọc trong ```json ... ``` hoặc kèm chữ thừa."""
    t = text.strip()
    if "{" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    try:
        d = json.loads(t)
    except Exception:
        d = {"script": text.strip()}
    script = (d.get("script") or "").strip() or f"Xem ngay video này nhé. {fallback_title}"
    return {
        "script": script,
        "title": (d.get("title") or fallback_title or script[:80]).strip(),
        "caption": (d.get("caption") or script).strip(),
        "hashtags": (d.get("hashtags") or "#viral #xuhuong #fyp").strip(),
    }


def _fallback(title: str, mode: str) -> dict:
    tags = "#nauan #asmr #monngon #food #fyp" if mode == "cook" else "#review #xuhuong #fyp"
    return {
        "script": f"Bạn sẽ không tin nổi video này đâu. {title or 'Xem ngay'}. Xem đến cuối để không bỏ lỡ bí quyết nhé.",
        "title": title or "Video hot hôm nay",
        "caption": f"{title or 'Xem ngay kẻo lỡ'} 🔥 Lưu lại để xem sau nhé!",
        "hashtags": tags,
        "fallback": True,
    }


async def generate(title: str, desc: str, mode: str, transcript: str = "", duration: int = 0) -> dict:
    """Trả về {script, title, caption, hashtags}. Không bao giờ raise."""
    prompt = _build_prompt(title, desc, mode, transcript, duration)

    async def attempt(p):
        pack = parse_pack(await p.call(prompt), title)
        pack["script"] = dialect.south(strip_sub_cta(pack.get("script") or ""))   # không kêu gọi đăng ký kênh
        return pack
    pack = await llm.first_success(
        _chain(lambda pr: _call_gemini(pr, schema=ScriptOut)), attempt, record=metrics.record,
        on_error=lambda p, e: logging.error("Provider %s lỗi, thử provider kế tiếp", p.name, exc_info=e))
    if pack is not None:
        return pack
    metrics.record("llm", "template", fallback=True)
    return _fallback(title, mode)


POST_PROMPT = """Viết nội dung đăng cho một {mode} KHÔNG CÓ LỜI THOẠI (chỉ có hình và nhạc nền, không ai nói gì). Chỉ dựa vào tiêu đề và mô tả gốc dưới đây; KHÔNG bịa thêm nguyên liệu, số liệu, địa điểm, giá tiền hay chi tiết nào không có trong đó. Viết lại bằng lời của mình, không chép nguyên câu. Không kêu gọi đăng ký / theo dõi / like / share kênh, không lời bán hàng của người gốc (giỏ hàng, link, mã giảm giá).

Tiêu đề gốc: {title}
Mô tả gốc: {desc}

Trả về DUY NHẤT JSON hợp lệ, không markdown:
{{"title": "tiêu đề đăng bài <= 90 ký tự",
"caption": "caption 1-2 câu, được dùng emoji",
"hashtags": "5-8 hashtag cách nhau bởi dấu cách",
"hook": "3-7 chữ in lên màn hình 3 giây đầu: tên món / thành phẩm hấp dẫn nhất, viết chữ thường có dấu đầy đủ"}}"""


async def post_only(title: str, desc: str, mode: str) -> dict:
    """Video KHÔNG LỜI (chỉ chèn nhạc nền): chỉ viết tiêu đề / caption / hashtag / hook bài đăng từ tiêu đề + mô tả
    gốc, KHÔNG viết lời đọc (dễ nói sai sự thật của video gốc). Trả {title, caption, hashtags, hook[, fallback]}.
    Không có tiêu đề lẫn mô tả thì dùng mẫu (AI không có gì để dựa vào). Không bao giờ raise."""
    title, desc = (title or "").strip(), (desc or "").strip()
    if title or desc:
        prompt = POST_PROMPT.format(mode=_MODE_VI.get(mode, mode), title=title or "(không có)",
                                    desc=desc[:1500] or "(không có)")

        async def attempt(p):
            t = (await p.call(prompt)).strip()
            d = json.loads(t[t.index("{"): t.rindex("}") + 1])
            return {"title": strip_sub_cta(str(d.get("title") or "")).strip() or title or "Video hot hôm nay",
                    "caption": strip_sub_cta(str(d.get("caption") or "")).strip() or title,
                    "hashtags": str(d.get("hashtags") or "").strip() or "#viral #xuhuong #fyp",
                    "hook": clean_hook(str(d.get("hook") or ""), f"{title} {desc}")}
        pack = await llm.first_success(
            _chain(lambda pr: _call_gemini(pr, schema=PostOut)), attempt, record=metrics.record,
            on_error=lambda p, e: logging.warning("Viết bài đăng bằng %s lỗi (%s), thử provider kế", p.name, str(e)[:120]))
        if pack is not None:
            return pack
    metrics.record("llm", "template", fallback=True)
    fb = _fallback(title, mode)
    return {"title": fb["title"], "caption": fb["caption"], "hashtags": fb["hashtags"], "hook": "", "fallback": True}


OST_HINT = """

TIẾNG GỐC (trường "ost" của mỗi đoạn): mặc định "new" (đọc lời mới). Chỉ đặt "orig" (GIỮ NGUYÊN tiếng gốc, không đọc lời mới, "text" để trống) khi tiếng gốc của đoạn đó là điểm nhấn không cần lời: tiếng xèo, tiếng giòn rụm khi cắn, một câu cảm thán ngắn tự nhiên; VÀ lời gốc đoạn đó KHÔNG có bước làm, nguyên liệu hay con số nào. Đặt "both" khi muốn đọc lời mới mà vẫn lót tiếng gốc nhỏ phía dưới. Tối đa 1/5 số đoạn là "orig" hoặc "both"; không chắc thì để "new"."""

IMAGE_HINT = """

ẢNH: kèm theo là ảnh chụp giữa từng đoạn ("Ảnh đoạn k"). Dùng ảnh để lời mới KHỚP HÌNH: nói về đúng điều người xem đang thấy trong ảnh của đoạn đó. Nếu lời gốc của đoạn nói một bước KHÔNG thấy trong ảnh (người làm video đọc lệch hình), hãy chuyển bước đó sang đoạn có ảnh thể hiện nó, nhưng vẫn giữ ĐỦ mọi bước và NGUYÊN VĂN mọi số liệu trong toàn bài, và thứ tự bước trong toàn bài vẫn hợp lý. Ưu tiên khớp hình hơn khớp lời gốc."""


TIMED_PROMPT = """Bạn là biên kịch video ngắn tiếng Việt. Video dưới đây có lời thoại gốc chia thành các ĐOẠN theo mốc thời gian, mỗi đoạn ứng với hình ảnh đang diễn ra lúc đó. Hãy viết lại LỜI MỚI cho TỪNG ĐOẠN, giữ đúng vị trí: lời mới của đoạn nào phải nói về đúng việc đoạn đó đang làm trên hình.

LUẬT BẮT BUỘC:
- Giữ NGUYÊN VĂN chỉ những thứ sau: CON SỐ và đơn vị (gam, ml, muỗng, phút, giây, độ...), giá tiền món ăn/quán (video review), địa chỉ, tên riêng. Không bịa số liệu, không đổi số cụ thể thành "vài"/"chút" (gốc "5 giây" thì viết "5 giây").
- NGUYÊN LIỆU: nhắc ĐỦ và ĐÚNG từng nguyên liệu có trong lời gốc, ở đúng đoạn của nó. Không được bỏ, không được gộp thành "rau củ", không được đổi sang thứ khác (trứng gà KHÔNG thành trứng vịt, thịt heo KHÔNG thành thịt bò). Chỉ được dùng tên gọi miền Nam của CÙNG một thứ: mùi = ngò, dứa = thơm/khóm, mộc nhĩ = nấm mèo, lạc = đậu phộng, giò lụa = chả lụa, trứng gà = hột gà.
- MỌI CHỮ CÒN LẠI PHẢI VIẾT LẠI bằng từ ngữ khác: không được giữ nguyên câu, không được chép cụm 3 từ liền nhau của lời gốc. Đây là video làm lại, chép lời gốc là thất bại.
  Ví dụ — gốc: "trước tiên cắt thành dải dài sau đó cắt thành miếng vừa ăn" → mới: "Đầu tiên mình xẻ thịt thành từng dải, rồi thái nhỏ cỡ vừa miệng."
- Lời gốc là bản nhận dạng giọng nói tự động nên có thể nghe nhầm, sai chính tả (ví dụ "nạt vai" là "nạc vai", "1 miếng... 2 miếng" có thể là cách đếm nhịp). Hãy hiểu đúng ý rồi viết chính tả chuẩn; số liệu nào vô lý do nghe nhầm thì bỏ, không bịa số khác.
- Lời mới của MỖI ĐOẠN chỉ nói đúng những thao tác có trong lời gốc của CHÍNH ĐOẠN ĐÓ, theo đúng thứ tự trong đoạn. TUYỆT ĐỐI không kéo thao tác của đoạn sau lên đoạn trước, không dời thao tác của đoạn trước xuống đoạn sau (vì hình ảnh thao tác đó chỉ xuất hiện đúng lúc đoạn đó). Nếu lời gốc của đoạn dừng giữa chừng (ví dụ kết thúc bằng "cho dứa"), lời mới cũng dừng ở đúng ý đó.
- KHÔNG KỂ LẠI BƯỚC ĐÃ LÀM: lời gốc kiểu «sau khi luộc khoảng 4 phút, lúc này mực bung ra» là NHÌN LẠI kết quả của bước trước (mực đã nằm trong nồi từ đoạn trước) — viết về kết quả đang thấy («Luộc chừng 4 phút là mực bung đều rồi nè»), KHÔNG viết như đang làm lại («giờ mình thả mực vô nồi»). Mỗi lần cho nguyên liệu vào chỉ kể ở đúng đoạn có thao tác đó.
- Mỗi đoạn có KHOẢNG SỐ TỪ ghi kèm (vd "10-13 từ"; con số cũng tính theo chữ đọc ra: "100g" là 3 từ "một trăm gam", "60%" là 4 từ). Viết TRONG khoảng đó: ít hơn thì giọng hết sớm, video bị lặng dài; nhiều hơn thì tràn sang đoạn sau và phải cắt bớt. Khe dài thì diễn đạt đầy đủ hơn (tả độ vàng, tiếng xèo đang thấy trên hình), KHÔNG thêm thao tác, nguyên liệu hay số liệu mới.
- Giọng NÓI CHUYỆN như đang đứng bếp chỉ cho người xem, KHÔNG viết kiểu tít báo / ghi chú / mệnh lệnh cụt: câu trọn ý, có "mình" / "mọi người", có từ nối tự nhiên (giờ, rồi, xong, tới đây) và dấu phẩy ngắt hơi. Sai: "Bắt nồi nước sôi sùng sục rồi thả vô luôn." / "Tránh đảo nhiều làm rách da mất ngon." Đúng: "Giờ mình bắc nồi nước lên, đợi sôi sùng sục rồi thả mực vô." / "Mọi người đừng đảo nhiều nha, đảo hoài là rách da, mất ngon liền." Từ đệm cuối câu (nha, nè, luôn, ghê) thì đổi qua lại, không hai câu liền nhau cùng kết bằng "nha" (chỉ bớt từ đệm; mỗi đoạn vẫn viết ĐỦ số từ yêu cầu, tả thêm điều đang thấy trên hình).
- HÀNH ĐỘNG ĐI TRƯỚC: mỗi đoạn (trừ đoạn hook đầu) nói NGAY hành động đang diễn ra trên hình ở đầu câu (được mở bằng 1-3 chữ dẫn như "Giờ mình", "Rồi", "Tới đây mình"), vì câu được phát đúng lúc cảnh đó vừa hiện lên. Viết "Giờ mình lật mặt nè, thịt vàng đều rồi." chứ KHÔNG viết "Chờ thịt vàng đều rồi lật mặt." Viết "Rồi mình áp chảo với lửa vừa thôi." chứ KHÔNG viết "Dùng lửa vừa để áp chảo."
- BỎ LỜI QUẢNG CÁO CỦA NGƯỜI GỐC: video này được đăng lại bởi kênh khác, nên KHÔNG được giữ lời bán hàng/kêu gọi mua của người gốc (giỏ hàng, "góc trái màn hình", link, mã giảm giá, "giá đang rất tốt", "bấm vào mua", tên shop) và KHÔNG được tự nhận sản phẩm của người gốc ("bột do chính tay em làm", "độc quyền của em"). Đoạn đó viết thành lời nói về món ăn hoặc lời chốt tự nhiên ("Làm vầy là ăn được rồi, bà con thử liền nha."). Vẫn được gọi tên nguyên liệu như "bột rang muối", "bột chiên giòn" một cách chung chung.
- ĐOẠN ĐIỂM DANH / TÓM TẮT: đoạn có lời gốc bắt đầu bằng «Nguyên liệu gồm:» hoặc «Tóm lại:» là máy soạn cho khúc chiếu nguyên liệu / tóm tắt các bước (trên hình có thẻ chữ tương ứng). Đọc lại ĐỦ các ý trong đó, đúng thứ tự, bằng giọng nói chuyện (vd «Chuẩn bị mực, nửa muỗng bột ngọt...», «Tóm lại là để nguyên mực đông, đun sôi nước...»); KHÔNG đổi thành lời khen món hay câu chung chung. Đoạn điểm danh vào THẲNG món đầu tiên (thẻ chữ từng món hiện ngay khi khúc bắt đầu), không mở bằng «Rồi mình...».
- MỖI ĐOẠN LÀ MỘT CÂU TRỌN NGHĨA: lời gốc hay bị cắt giữa câu (vd đoạn gốc "rồi Gà thấm thì anh chị" + đoạn sau "dùng cái gói bột..."). Khi đó viết ý trọn vẹn của riêng phần đó ("Gà thấm gia vị rồi nè."), KHÔNG để câu treo kiểu "...thì bà cho tui", "Khi", "Mua". Không gộp sai nghĩa hai việc khác nhau vào một câu.
- KHÔNG KÊU GỌI ĐĂNG KÝ / THEO DÕI KÊNH ở BẤT KỲ đoạn nào, kể cả đoạn cuối: không viết "đăng ký kênh", "theo dõi", "follow", "like share", "bấm chuông", "ủng hộ kênh", "hẹn gặp lại ở video sau". Lời gốc có câu đó thì bỏ ý đó: viết lời chốt về món ("Vậy là có dĩa cá kèo giòn rụm rồi nè.") hoặc để "text" rỗng.
- Không emoji trong "text".

Thể loại: {mode}
Tiêu đề gốc: {title}
Mô tả gốc: {desc}

CÁC ĐOẠN (số thứ tự | thời điểm | số từ | lời gốc):
{segments}

Trả về DUY NHẤT JSON hợp lệ, không markdown:
{{"segments": [{{"i": 0, "text": "..."}}, ...],
"title": "tiêu đề đăng bài <= 90 ký tự",
"caption": "caption 2-3 câu để đăng, được dùng emoji",
"hashtags": "5-8 hashtag cách nhau bởi dấu cách",
"hook": "3-7 chữ in lên màn hình 3 giây đầu, viết chữ thường có dấu đầy đủ"}}"""

HOOK_RULE = """

CÂU HOOK — ĐOẠN 0 (3 giây đầu quyết định người xem có lướt qua không):
- Đoạn 0 là câu HOOK: nói NGAY điều hấp dẫn nhất của video — món thành phẩm ngon/lạ, mẹo bất ngờ, con số ấn tượng, hoặc một câu hỏi gây tò mò. Ví dụ: "Cá kèo chiên kiểu này giòn rụm mà hổng văng dầu nè!", "Bí quyết để cá kèo không nhảy khỏi chảo là đây!", "Chỉ 3 bước là có dĩa mì xào bò mềm ngon như ngoài tiệm!".
- KHÔNG chào hỏi ("xin chào", "chào cả nhà", "hôm nay mình làm..."), KHÔNG giới thiệu kênh, KHÔNG nói "video này".
- Lời gốc đoạn 0 có thao tác, nguyên liệu, số liệu nào thì câu hook vẫn nhắc đủ và đúng. Đoạn 0 ghi "(chưa có lời gốc)" là phần mở đầu trước khi người gốc nói: chỉ dựa vào tiêu đề và hình, không thêm nguyên liệu hay số liệu mà lời gốc không có.
- "hook": 3-7 chữ in lên màn hình cùng lúc (vd "cá kèo giòn không văng dầu"): viết chữ thường có dấu đầy đủ (máy tự in hoa), đúng chính tả tên món như trong lời, không dấu chấm, không emoji."""

# Kêu gọi đăng ký / theo dõi kênh (người dùng không muốn có, kể cả cuối video — 13/11/2026). "theo dõi" chỉ tính khi đi
# với kênh/trang/mình/để...: "theo dõi nhiệt độ", "theo dõi lửa" là thao tác nấu.
_SUB_RE = re.compile(r"đăng\s*k[ýí]|subscri|\bsub\s+kênh|theo\s*dõi\s*(kênh|trang|page|fanpage|mình|tui|tôi|em|"
                     r"để\s*(xem|nhận|không|cập|biết|ủng|đón)|nha|nhé|nghen|ngay|thêm)|\bfollow|bấm\s*(chuông|like|thích|tim)|nhấn\s*(chuông|like|theo dõi)|"
                     r"\blike\s*(và|,|&)?\s*share|ủng\s*hộ\s*(kênh|mình|tui|em|page|video)|"
                     r"hẹn\s*gặp\s*(lại\s*)?(các bạn|bà con|mọi người|cả nhà)?\s*(ở|trong)\s*(video|clip)|"
                     r"xem\s*thêm\s*(nhiều\s*)?(video|clip)", re.IGNORECASE)
_GREET_RE = re.compile(r"^\W*(xin\s+chào|chào\s+(mừng|cả\s+nhà|mọi\s+người|các\s+bạn|bà\s+con)|hello\b|"
                       r"hôm\s+nay\s+(mình|tui|tôi|em|chúng\s+ta)\b)", re.IGNORECASE)


def has_sub_cta(text: str) -> bool:
    return bool(_SUB_RE.search(text or ""))


def strip_sub_cta(text: str) -> str:
    """Bỏ các vế kêu gọi đăng ký / theo dõi kênh khỏi lời đọc (lưới cuối, sau khi đã cấm AI viết). Tách theo vế (dấu
    câu và dấu phẩy): "Xong rồi, cá giòn rụm, nhớ đăng ký kênh nha" -> "Xong rồi, cá giòn rụm."."""
    if not has_sub_cta(text):
        return text
    out = []
    for p in re.split(r"(?<=[.!?…,;])\s+", text.strip()):
        if not has_sub_cta(p):
            out.append(p)
            continue
        # vế không có dấu câu (Whisper hay bỏ dấu): tách tiếp ở từ nối, chỉ bỏ mẩu có lời kêu gọi, giữ bước làm —
        # "các bạn nhớ đăng ký kênh ủng hộ mình nha rồi cho 2 muỗng đường vào" -> "cho 2 muỗng đường vào"
        bits = [b for b in _CTA_JOIN.split(p) if b.strip()]
        kept = " ".join(b for b in bits if not has_sub_cta(b)).strip()
        kept = re.sub(r"^(rồi|và|sau đó|với lại|còn|tiếp theo|bây giờ)\s+", "", kept, flags=re.IGNORECASE)
        if len(kept.split()) >= 3:
            out.append(kept)
    out = " ".join(out).strip()
    return (out[:-1] + ".") if out.endswith((",", ";")) else out


_CTA_JOIN = re.compile(r"(?<=\bnha)\s+|(?<=\bnhé)\s+|(?<=\bnghen)\s+|(?<=\bnhen)\s+|"
                       r"\s+(?=(?:rồi|và|sau đó|với lại|còn|tiếp theo|bây giờ)\b)", re.IGNORECASE)


def is_sub_cta_line(text: str) -> bool:
    """Đoạn lời GỐC chỉ là lời kêu gọi đăng ký (bỏ ý đó thì còn < 3 chữ): bỏ trước khi đưa AI viết lại."""
    return has_sub_cta(text) and len(strip_sub_cta(text).split()) < 3


_MODS = {"\u0302", "\u0306", "\u031b"}     # dấu mũ (â ê ô), trăng (ă), móc (ơ ư): AI hay rơi khi viết IN HOA


def _skeleton(word: str) -> str:
    """Chữ bỏ dấu mũ / trăng / móc và đ->d nhưng GIỮ dấu thanh: "gừng" và "gùng" cùng khung, "vàng" và "văng" thì không."""
    d = unicodedata.normalize("NFD", word.lower().replace("đ", "d"))
    return unicodedata.normalize("NFC", "".join(c for c in d if c not in _MODS))


def repair_marks(text: str, ref: str) -> str:
    """Sửa chữ AI viết rơi dấu mũ / trăng / móc ("MỰC HẤP GÙNG" -> "gừng", bản 43): chữ KHÔNG có trong tiêu đề / lời
    gốc / lời mới mà có đúng một chữ ở đó cùng khung (cùng dấu thanh) thì thay bằng chữ đó (nhiều chữ: lấy chữ hay gặp
    nhất). Chữ có thật trong `ref` giữ nguyên."""
    vocab: dict = {}
    for w in re.findall(r"\w+", (ref or "").lower()):
        vocab[w] = vocab.get(w, 0) + 1
    if not vocab:
        return text
    by_sk: dict = {}
    for w, n in vocab.items():
        by_sk.setdefault(_skeleton(w), []).append((n, w))

    def fix(m):
        w = m.group(0)
        lw = w.lower()
        if lw in vocab or not any(c.isalpha() for c in lw):
            return w
        cand = by_sk.get(_skeleton(lw))
        if not cand:
            return w
        best = max(cand)[1]
        return best.upper() if w.isupper() else best
    return re.sub(r"\w+", fix, text)


def clean_hook(text: str, ref: str = "") -> str:
    """Chữ hook in màn hình: một dòng ngắn (≤ 40 ký tự, cắt ở ranh giới từ), bỏ dấu cuối, không emoji/kêu gọi.
    AI viết chữ thường (viết IN HOA hay rơi dấu: "GÙNG" thay vì "GỪNG"); máy sửa dấu theo `ref` (tiêu đề + lời) rồi
    tự in hoa."""
    t = re.sub(r"[^\w\s%,!?'-]", " ", text or "")
    t = " ".join(t.split()).strip(" .,!?-")
    if not t or has_sub_cta(t):
        return ""
    if len(t) > 40:
        t = t[:41].rsplit(" ", 1)[0]
    return repair_marks(t.lower(), ref).upper()


_CTA_RE = re.compile(r"giỏ hàng|góc (trái|phải) màn hình|link (ở|dưới|trong)|mã giảm|bấm vào (mua|link|giỏ)|"
                     r"chính tay (em|mình|tui|tôi) làm|độc quyền (của )?(em|mình|tui|tôi|shop|nhà)|nhà tự làm|"
                     r"(nhà|em|mình|tui|tôi) tự (tay )?làm (ra )?(bột|gói)|"
                     r"(bà con|mọi người|anh chị|các bạn|cả nhà)( nhớ| hãy| cứ)? (mua|đặt|chốt) "
                     r"(ngay|liền|ủng hộ|giúp|ở shop|tại shop|sản phẩm|gói|hũ|hàng|về (dùng|xài))", re.IGNORECASE)


_VOICE_RATE = contextvars.ContextVar("voice_rate", default=None)


def set_voice_rate(rate: float | None):
    """Tốc độ đọc của giọng job đang làm (chữ/giây). VieNeu Mỹ Duyên chỉ ~3.1 chữ/giây (Adam 4.15): AI phải viết lời
    ngắn hơn cho vừa khung hình. Biến theo ngữ cảnh asyncio: mỗi job một giá trị."""
    _VOICE_RATE.set(rate)


def _rate() -> float:
    return _VOICE_RATE.get() or WORDS_PER_SEC.get(config.TTS_PROVIDER, 2.8)


def words_per_sec() -> float:
    """Tốc độ đọc đo thực tế của provider giọng đang dùng (từ/giây)."""
    return _rate()


def spoken_words(text: str) -> int:
    """Số chữ thật sự đọc ra: "100g" là 3 chữ "một trăm gam" (đếm thô thì ước lượng độ dài giọng bị hụt)."""
    import vi_text
    return len(vi_text.normalize(text or "").split())


SENT_MAX = 6.0          # một câu đọc liền dài tối đa (giây): dài hơn thì lời mới lệch khỏi hình đang chiếu
FRAG_WORDS = 4          # mẩu cụt chưa hết câu tối đa chừng này chữ thì được dời tới đọc liền với câu sau
MIN_SLOT = 3.0          # khe ngắn hơn thế này (người gốc nói liền) thì gộp với câu sau cho AI đủ chỗ viết câu tự nhiên
SENT_CUT_MAX = 9.0      # câu bị cắt ngang (chưa có dấu câu) được gộp dài tới chừng này: câu dài còn hơn câu cụt
_CLAUSE_END = (".", "!", "?", "…", ",", ";", ":")
_SENT_END = (".", "!", "?", "…")
# hư từ hầu như không đứng cuối câu: đoạn kết bằng các chữ này là câu bị cắt ngang ("Như vậy là đã" | "xong công
# thức"). Cần khi bóc lời KHÔNG có dấu câu (Whisper trên Kaggle hay bỏ dấu ở các khung sau — đo thật 13/11/2026).
_OPEN_END = {"là", "đã", "sẽ", "đang", "của", "và", "với", "thì", "mà", "để", "những", "các", "một", "khi", "nếu", "vì",
             "nên", "cũng", "rất", "bị", "cái", "hay", "hoặc", "nhưng", "tại", "lúc", "từ", "về", "như", "càng", "chưa"}


def _open_end(t: str) -> bool:
    w = re.findall(r"\w+", (t or "").lower())
    return bool(w) and w[-1] in _OPEN_END and not (t or "").rstrip().endswith(_SENT_END)


_END_PART_RE = re.compile(r"[,\s]+(nha|nè|nhé)\s*([.!?…]*)\s*$", re.IGNORECASE)


def vary_particles(got: dict, order: list) -> None:
    """Hai đoạn đọc liền nhau cùng kết bằng "nha" / "nè": bỏ từ đệm ở đoạn sau ("...4 phút nha." -> "...4 phút.").
    Bản 47: 8/26 câu kết bằng "nha" (11 lần "nha" trong 3 phút) nghe đều đều như máy dù từng câu vẫn tự nhiên."""
    prev = ""
    for k in order:
        t = (got.get(k) or "").strip()
        if not t:
            continue
        m = _END_PART_RE.search(t)
        part = m.group(1).lower() if m else ""
        if part and part == prev and len(t[:m.start()].split()) >= 3:
            got[k] = t[:m.start()].rstrip(" ,") + (m.group(2) or ".")
            part = ""
        prev = part


SIDE_MARKS = ("Nguyên liệu gồm:", "Tóm lại:")   # lời "gốc" của khúc điểm danh / tóm tắt (style_b): luôn đứng riêng


def _side(t: str) -> bool:
    return (t or "").lstrip().startswith(SIDE_MARKS)


def side_spoken(orig: str) -> str:
    """Lời đọc dự phòng của khúc điểm danh / tóm tắt (máy soạn, không phải lời người gốc nên đọc thẳng được)."""
    body = dialect.south(orig.split(":", 1)[1].strip())
    return ("Tóm lại là " if orig.lstrip().startswith("Tóm lại") else "Chuẩn bị ") + body


# mở đầu khúc điểm danh: thẻ chữ từng món hiện ngay khi khúc bắt đầu, "Rồi mình chuẩn bị" mất ~1 giây -> lời trễ thẻ
_MONTAGE_LEAD_RE = re.compile(
    r"^\s*(?:(?:rồi|giờ|bây giờ|đầu tiên|trước tiên|trước hết|xong|nào|ok|okay)\s*,?\s+)?"
    r"(?:(?:thì\s+)?(?:mình|mọi người|cả nhà|anh chị|tụi mình|chúng ta)\s+)?(?=chuẩn bị\b)",
    re.IGNORECASE)


def montage_lead(text: str) -> str:
    """"Rồi mình chuẩn bị mực, ..." -> "Chuẩn bị mực, ..." (bản 49, job 7: lời điểm danh trễ thẻ chữ ~1 giây)."""
    t = _MONTAGE_LEAD_RE.sub("", text or "", count=1)
    return t[:1].upper() + t[1:] if t != (text or "") and t else (text or "")


# ---------- kể lại bước đã làm (bản 49, job 7 phút 1:51) ----------
# Lời gốc "sau khi mình luộc khoảng tầm 4 phút, lúc này cái mực bung ra" là NHÌN LẠI bước trước; AI viết "giờ mình thả
# mực vô nồi nước đang sôi, đun chừng 4 phút..." trong khi hình là mực đã bung trong nồi -> người xem tưởng video quay
# ngược. Bắt: đoạn có "cho nguyên liệu X vô CHỖ C" mà lời gốc của chính đoạn không cho X vào, và X đã được cho vô ĐÚNG
# chỗ C ở một đoạn trước. Phải cùng chỗ: món nhiều công đoạn cho cùng một thứ vào chỗ mới là bước THẬT (ướp mực rồi
# thả mực vô chảo chiên; luộc trong nồi rồi cho vô chảo xào) — soát độc lập PR #8.
_ADD_VERBS = {"thả", "bỏ", "đổ", "trút", "nêm", "rắc", "rưới", "thêm", "cho"}
_ADD_FILL = {"hết", "cả", "chút", "ít", "xíu", "nguyên", "con", "cái", "mấy", "phần", "nốt", "tiếp", "thẳng", "luôn",
             "thêm", "đám", "số", "vô", "vào", "lên"}
# chỗ cho vào (chữ ngay sau "vô / vào"), gộp từ cùng nghĩa
_PLACES = {"nồi": "nồi", "xoong": "nồi", "chảo": "chảo", "tô": "tô", "chén": "chén", "bát": "chén", "dĩa": "dĩa",
           "đĩa": "dĩa", "thau": "thau", "khay": "khay", "hộp": "hộp", "lò": "lò", "xửng": "xửng", "nước": "nước",
           "dầu": "dầu", "hũ": "hũ", "ly": "ly", "cốc": "ly"}
_NUMWORD_RE = re.compile(r"\d|(?<![\w])(?:một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|nửa|rưỡi)(?![\w])", re.IGNORECASE)


def _qty_word(w: str) -> bool:
    return bool(re.fullmatch(r"[\d.,/-]+", w)) or w in ingredients._WORDNUM or w in ingredients._UNITS


def add_steps(text: str) -> set:
    """Các lần CHO VÀO trong câu: {(nhóm nguyên liệu, chỗ)}. Động từ cho vào + (chữ đệm / định lượng) + tên nguyên liệu
    ngay sau + "vô / vào" + chỗ. "thả mực vô nồi nước sôi" -> {(mực, "nồi")}; không nói chỗ -> chỗ "". Không tính:
    "luộc cho mực chín", "thêm nữa là mực...", "trút mực ra dĩa" (lấy ra), "ướp mực" (không cho vào đâu)."""
    toks = _norm_words(text)
    out: set = set()
    for i, w in enumerate(toks):
        if w not in _ADD_VERBS:
            continue
        for p in range(i + 1, min(len(toks), i + 6)):
            head = toks[p:p + 3]
            got = ingredients.find(" ".join(head)) - ingredients.find(" ".join(head[1:]))   # tên bắt đầu ĐÚNG ở p
            if got:
                n = next((L for L in (1, 2, 3) if got <= ingredients.find(" ".join(toks[p:p + L]))), len(head))
                after = toks[p + n:p + n + 4]
                into = next((j for j, x in enumerate(after) if x in ("vô", "vào")), None)
                if into is None and (w == "cho" or "ra" in after[:2]):   # "luộc cho mực chín" / "trút mực ra dĩa"
                    break
                place = _PLACES.get(after[into + 1], "") if into is not None and into + 1 < len(after) else ""
                out |= {(g, place) for g in got}
                break
            if toks[p] not in _ADD_FILL and not _qty_word(toks[p]):
                break
    return out


def _steps_vi(steps: set) -> str:
    """{(nhóm, chỗ)} -> "mực vô nồi, hạt nêm" (cho lời nhắc AI / log)."""
    return ", ".join(sorted(ingredients.GROUPS[g][0] + (f" vô {c}" if c else "") for g, c in steps))


def add_actions(text: str) -> set:
    """Nhóm nguyên liệu được cho vào trong câu (add_steps, bỏ chỗ)."""
    return {g for g, _ in add_steps(text)}


def replayed_steps(items: list) -> dict:
    """items: [(k, lời gốc, lời mới)] theo thứ tự video (bỏ câu hook / khúc điểm danh, tóm tắt). Trả {k: {(nhóm, chỗ)}}:
    đoạn cho nguyên liệu vô một chỗ mà ở đoạn trước nó đã được cho vô đúng chỗ đó, trong khi lời gốc của đoạn không
    cho nguyên liệu đó vào."""
    done: set = set()
    out: dict = {}
    for k, orig, new in items:
        mine = add_steps(new)
        again = {x for x in mine if x[1] and x in done}
        if again and not _orig_adds(orig or "", {g for g, _ in again}):
            out[k] = again
        done |= mine | add_steps(orig)
    return out


def _orig_adds(orig: str, groups: set) -> bool:
    """Lời gốc của đoạn có cho (một trong) các nguyên liệu `groups` vào -> không coi là kể lại. Tính khi: có động từ cho
    vào kèm tên nguyên liệu đó trong vài chữ sau ("cho là hành tây, đầu hành" cũng tính), hoặc có động từ cho vào mà
    KHÔNG nói cho gì ("bỏ trực tiếp vô luôn": rộng tay, không đoán được). Gốc cho vào thứ KHÁC có nêu tên ("cho hành
    tây vô") thì "thả mực vô" vẫn là kể lại (Codex, PR #8)."""
    toks = _norm_words(orig)
    for i, w in enumerate(toks):
        if w not in _ADD_VERBS or (w == "thêm" and toks[i + 1:i + 2] == ["nữa"]):    # "thêm nữa là..." = ngoài ra
            continue
        named = ingredients.find(" ".join(toks[i + 1:i + 9]))
        if named & groups:
            return True
        if w == "cho" and not {"vô", "vào"} & set(toks[i + 1:i + 5]):   # "cho mình", "cho mực chín": không phải cho vào
            continue
        if not named:
            return True
    return False


def drop_replayed(text: str, steps: set) -> str:
    """Bỏ vế câu kể lại việc cho nguyên liệu vào (steps: {(nhóm, chỗ)} của replayed_steps; vế có con số thì giữ). Còn
    dưới 4 chữ thì giữ nguyên câu."""
    parts = re.split(r"(?<=[,.!?;…])\s+", (text or "").strip())
    keep = [p for p in parts if not (add_steps(p) & steps and not _NUMWORD_RE.search(p))]
    if len(keep) == len(parts):
        return text
    out = " ".join(keep).strip()
    if len(out.split()) < 4:
        return text
    out = out[:1].upper() + out[1:]
    return out[:-1] + "." if out.endswith((",", ";")) else out if out.endswith((".", "!", "?", "…")) else out + "."


# ---------- nói «dĩa» khi món chưa lên dĩa (bản 50, job 7 phút 2:28) ----------
_PLATE_ACT_RE = re.compile(r"(?<![\w])(?:đổ ra|trút ra|bày ra|xúc ra|ra dĩa|ra đĩa|ra tô|ra chén|ra mâm|lên dĩa|lên đĩa|"
                           r"bày lên)(?![\w])", re.IGNORECASE)
_PLATE_NOUN_RE = re.compile(r"(?<![\w])(dĩa|đĩa)\s+(\w+(?:\s+\w+)?)", re.IGNORECASE)


def early_plate(items: list) -> dict:
    """items: [(k, lời gốc, lời mới)] theo thứ tự (bỏ câu hook / khúc điểm danh, tóm tắt). Trả {k: "Dĩa mực"}: lời mới
    gọi món là «dĩa + món» ở đoạn mà lời gốc CHƯA bày món ra dĩa, trong khi một đoạn SAU mới đổ / bày ra (job 7, 2:28:
    «Dĩa mực nóng hổi» khi hình là mực còn trong vợt; gốc «đổ ra» ở 2:43). Lời gốc không bao giờ nói bày ra: không bắt."""
    at = next((i for i, (_, orig, _new) in enumerate(items) if _PLATE_ACT_RE.search(orig or "")), None)
    out: dict = {}
    for k, orig, new in items[:at] if at is not None else []:
        if re.search(r"(?<![\w])(?:dĩa|đĩa)(?![\w])", orig or "", re.IGNORECASE):   # gốc cũng nói dĩa (dĩa rau...)
            continue
        for m in _PLATE_NOUN_RE.finditer(new or ""):
            what = m.group(2).lower()
            if ingredients.find(what) or what.split()[0] in ("món", "này", "thành"):
                out[k] = m.group(0)
                break
    return out


def unplate(text: str) -> str:
    """Bản cuối vẫn nói «dĩa» quá sớm: «Dĩa mực nóng hổi» -> «Mẻ mực nóng hổi» (giữ chữ hoa đầu câu)."""
    return _PLATE_NOUN_RE.sub(lambda m: ("Mẻ" if m.group(1)[0].isupper() else "mẻ") + " " + m.group(2), text or "",
                              count=1)


# ---------- lời lặp ý (bản 1.0.1, job 1 video mực) ----------
# Quãng luộc dài không có việc mới, AI độn lời: «luộc chừng 4 phút» 3 lần, «không rách da» 3 lần, «ngon ghê chưa» 2
# lần. Người xem TikTok nghe lặp là lướt. Bắt: một thời lượng (4 phút, ba phút) hay một cụm ≥ 3 chữ có nghĩa đã nói
# ở đoạn trước. Tên món / nguyên liệu / cách nấu («mực hấp gừng», «nồi nước sôi») nhắc lại là chuyện thường: không bắt.
_TIME_Q_RE = re.compile(r"(?<![\w])(\d+(?:[,.]\d+)?(?:\s*[-–]\s*\d+)?|nửa|một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười"
                        r"|mươi)\s+(phút|giây|tiếng)(?![\w])", re.IGNORECASE)
_REPEAT_STOP = set("mình là thì này nha nè cho vô vào rồi cái con mấy cả nhà đó á luôn cũng vậy nữa đã đang sẽ có được "
                   "với và hay mà ra lên xuống đi các những kia đây ơi nhé nhá hen nghen giờ lúc khi để một hai ba "
                   "chừng khoảng tầm cứ chỉ thêm như là coi xem nhìn thấy".split())
_COOK_WORDS = set("hấp luộc chiên xào nấu kho nướng rang trụng chần ướp nêm phi rim om hầm nồi chảo vợt dĩa đĩa tô chén "
                  "nước sôi lửa món mẻ con".split())


def _idea_grams(text: str) -> set:
    toks = re.findall(r"\w+", (text or "").lower())
    out = set()
    for i in range(len(toks) - 2):
        g = toks[i:i + 3]
        content = [t for t in g if t not in _REPEAT_STOP]
        if len(content) < 2:
            continue
        # cụm chỉ gồm tên món / nguyên liệu / cách nấu (mực hấp gừng, nồi nước sôi): nhắc lại là thường
        if all(t in _COOK_WORDS or ingredients.find(t) for t in content):
            continue
        out.add(" ".join(g))
    return out


def repeated_ideas(items: list) -> dict:
    """items: [(k, lời mới)] theo thứ tự (đã bỏ khúc điểm danh / tóm tắt — đọc lại là việc của chúng). Trả {k: cụm lặp}
    cho đoạn nhắc lại một thời lượng hay một cụm có nghĩa đã nói ở đoạn trước."""
    seen_q: set = set()
    seen_g: set = set()
    out: dict = {}
    for k, text in items:
        qs = {re.sub(r"\s+", " ", m.group(0).lower()) for m in _TIME_Q_RE.finditer(text or "")}
        grams = _idea_grams(text)
        hit = sorted(qs & seen_q) or sorted(grams & seen_g)
        if hit:
            out[k] = hit[0]
        seen_q |= qs
        seen_g |= grams
    return out


_DUP_ITEM_RE = re.compile(r"(?<![\w])([^,.;!?]*?\w\s+\w[^,.;!?]*?)(?:,\s*\1)+(?=[,.;!?]|$)", re.IGNORECASE)


def dedupe_items(text: str) -> str:
    """Bỏ mục lặp liền nhau trong câu liệt kê: "1/2 muỗng hạt nêm, 1/2 muỗng hạt nêm" -> một lần (bản 48, job 6: lời gốc
    Whisper nghe lặp, AI chép theo, giọng đọc hai lần)."""
    return _DUP_ITEM_RE.sub(lambda m: m.group(1), text or "")


_SIDE_STOP = {"và", "cho", "rồi", "là", "với", "các", "một", "lại", "gồm", "tóm", "nguyên", "liệu", "thêm", "vào", "vô"}


def side_lost(new: str, orig: str) -> bool:
    """Đoạn điểm danh / tóm tắt mà lời mới mất ý: dưới 40% chữ chính của lời gốc, HOẶC bỏ trọn một ý (không còn chữ
    riêng nào của ý đó). Bản 48, job 6: «Tóm lại: dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa» ->
    AI viết «Nấu kiểu này đơn giản lắm, ai làm cũng ngon». Job 7: «Dùng mực đông lạnh, trụng hành tây rồi vớt mực» —
    còn 58% chữ nhưng mất hẳn bước «đun sôi nước»."""
    if not _side(orig):
        return False
    body = dialect.south(orig.split(":", 1)[1])
    keys = {w for w in _norm_words(body) if len(w) > 1} - _SIDE_STOP
    if len(keys) < 3:
        return False
    have = set(_norm_words(dialect.south(new or "")))
    if len(keys & have) / len(keys) < 0.4:
        return True
    items = [x for x in ({w for w in _norm_words(p) if len(w) > 1} - _SIDE_STOP for p in re.split(r"[,;.]", body)) if x]
    if len(items) < 2:
        return False
    seen = Counter(w for x in items for w in x)
    return any(own and not own & have for own in ({w for w in x if seen[w] == 1} for x in items))


def merge_sentences(segments: list, out_dur: float) -> list:
    """Gộp các mẩu lời bị cắt ngang thành câu trọn TRƯỚC khi AI viết lại. Máy nghe cắt theo chỗ ngắt hơi nên ra
    những mẩu cụt ("Sau khi mình" | "luộc ... vớt ra" | "cho mình." | "Rồi"); AI viết từng mẩu thành một câu riêng
    và mỗi mẩu được đọc riêng với ngữ điệu kết câu -> nghe ngắt câu vô lý (Botkeep 11/2026). Gộp khi: đoạn bắt đầu
    quá sát đoạn trước (< 1.1s), hoặc câu trước chưa xong (không kết bằng dấu câu) / một trong hai chỉ 1-2 chữ, miễn
    là cả câu gộp không dài quá SENT_MAX giây.
    Mẩu 1-2 chữ chỉ gộp NGƯỢC về câu trước khi nó KẾT câu ("...70g." + "cái thìa."); mẩu không dấu đứng sau một câu
    đã xong là ĐẦU câu mới ("sau" | 6s nghỉ | "khi bò nó tái..."): mẩu cụt như vậy (≤ FRAG_WORDS chữ) được dời tới sát
    câu sau và đọc liền — trước đây đọc "Sau." một mình rồi im 6 giây (Botkeep 12/11/2026). Mốc thời gian của Whisper
    hay kéo dài chữ cuối tới tận câu sau nên không dựa vào độ dài mẩu."""
    # nguồn bóc lời không có dấu câu (cả bài không đoạn nào kết bằng dấu) thì không suy "câu chưa xong" được
    punct = sum((t or "").rstrip().endswith(_CLAUSE_END) for _, _, t in segments) >= max(1, 0.2 * len(segments))

    def n(t):
        return len(t.split())

    def tiny(a, b, t):       # mẩu vụn: "Rồi", "sau", "cái thìa." (không có dấu câu thì 2 chữ phải là nói vội)
        return n(t) <= 1 or (n(t) <= 2 and (punct or b - a < 1.5))
    out: list = []
    for st, en, tx in segments:
        tx = (tx or "").strip()
        if out and not _side(tx) and not _side(out[-1][2]):    # khúc điểm danh / tóm tắt: không gộp với câu nào
            pst, pen, ptx = out[-1]
            cut = (punct and not ptx.rstrip().endswith(_CLAUSE_END)) or _open_end(ptx)   # câu trước chưa xong
            opens = punct and not tx.rstrip().endswith(_CLAUSE_END) and ptx.rstrip().endswith(_SENT_END)
            small = tiny(pst, pen, ptx) or (tiny(st, en, tx) and not opens)
            if st - pst < 1.1 or ((cut or small) and en - pst <= SENT_MAX):
                out[-1] = (pst, en, f"{ptx} {tx}".strip())
                continue
            if (cut or tiny(pst, pen, ptx)) and n(ptx) <= FRAG_WORDS and not ptx.rstrip().endswith(_SENT_END):
                ns = max(pst, st - (n(ptx) / 4.0 + 0.25))   # mẩu cụt nói ~4 chữ/giây, sát trước câu sau
                if en - ns <= SENT_MAX + 1.5:
                    out[-1] = (round(ns, 2), en, f"{ptx} {tx}")
                    continue
            if cut and en - pst <= SENT_CUT_MAX:
                # câu dài bị cắt ngang ("Bởi vì lúc chiên cá kèo thì Các cá" | "kèo sẽ giòn lâu..." — cắt giữa tên
                # "cá kèo", AI viết thành "Lúc này cá sẽ" | "giòn rụm...", Botkeep 12/11/2026): thà một câu dài
                out[-1] = (pst, en, f"{ptx} {tx}")
                continue
            if tiny(st, en, tx) and not opens and st - pen <= 3.0:
                # mẩu kết câu đứng lẻ ("xuống." sau "...để cho nó ráo dầu", từng còn lại một mình): về câu trước
                out[-1] = (pst, en, f"{ptx} {tx}")
                continue
        out.append((st, en, tx))
    # khe quá ngắn: người gốc nói nhanh, máy cắt mỗi 1.5-2.5s -> giọng mới (Mỹ Duyên ~3.1 chữ/giây) chỉ được 4-6 chữ,
    # AI viết câu cụt hoặc viết quá rồi bị nén (Botkeep 12/11/2026: 19/46 đoạn vượt số từ, 16 đoạn bị rút gọn thành
    # "Thêm vào.", "Phi thơm lên."). Gộp với đoạn sau khi người gốc nói liền (nghỉ < 0.8s), câu gộp ≤ SENT_MAX.
    out2: list = []
    for st, en, tx in out:
        if out2 and not _side(tx) and not _side(out2[-1][2]):
            pst, pen, ptx = out2[-1]
            if st - pst < MIN_SLOT and st - pen < 0.8 and en - pst <= SENT_MAX:
                out2[-1] = (pst, en, f"{ptx} {tx}")
                continue
        out2.append((st, en, tx))
    out = out2
    if len(out) >= 2 and out_dur - out[-1][0] < 1.2 and not _side(out[-1][2]) and not _side(out[-2][2]):
        a, b = out[-2], out[-1]
        out[-2:] = [(a[0], b[1], a[2] + " " + b[2])]
    return out


TEASER_SPILL = 0.4      # câu hook của đoạn mở đầu được lấn sang video chính tối đa chừng này giây
HOOK_TIP = """
- ĐIỂM ĂN TIỀN của video (mẹo chính người gốc nói, đã kiểm trong lời gốc): «{tip}». Câu hook (đoạn 0) và chữ "hook" xoáy vào điều BẤT NGỜ hoặc KẾT QUẢ mà mẹo này mang lại, nói như một lời hứa ngắn cho người đang lướt; KHÔNG khen chung chung kiểu "giữ trọn vị ngọt", "thơm ngon khó cưỡng". "title" cũng nêu tên món kèm đúng điểm này (người xem YouTube / Facebook bấm vì lời hứa trong tiêu đề). Nguyên liệu, con số trong câu hook và tiêu đề chỉ lấy từ mẹo này hoặc lời gốc."""

TEASER_HINT = """
- ĐOẠN MỞ ĐẦU: đoạn 0 chiếu trước cảnh MÓN ĐÃ HOÀN THÀNH trong {dur:.0f} giây đầu (xem ảnh đoạn 0), rồi video mới bắt đầu làm từ đầu. Câu hook nói về đúng món đang thấy (giòn, thơm, mềm, màu, độ hấp dẫn) hoặc gợi tò mò cách làm, đọc vừa {dur:.0f} giây; KHÔNG kể bước làm, không nhắc nguyên liệu hay con số mà lời gốc không có."""
INTRO_MIN = 2.0         # lời gốc bắt đầu muộn hơn thế này thì thêm câu hook riêng ở giây 0 (sớm hơn: đoạn 0 là hook)
INTRO_SPEAK = 5.5       # câu mở đầu đọc tối đa chừng này giây (một câu hook, phần còn lại của quãng để im)
DEAD_AIR = 3.5          # đọc xong mà khe còn lặng quá chừng này giây (ước theo số chữ) -> bắt viết thêm (bản 50)


async def generate_timed(title: str, desc: str, mode: str, segments: list, out_dur: float,
                         images_for=None, extra: str = "", avoid: list | None = None, intro: bool = True,
                         teaser: float = 0.0, examples: list | None = None, hook_tip: str = "") -> dict:
    """segments: [(start, end, text)] của lời gốc. Trả về pack có "segments": [{i, start, slot, text}].
    hook_tip: mẹo chính của video (đã đối chiếu lời gốc) để câu hook xoáy vào (bản 50).
    Không bao giờ raise; nếu AI lỗi trả về None để caller dùng cách cũ.
    intro: lời gốc bắt đầu muộn (≥ INTRO_MIN) thì thêm đoạn 0s "(chưa có lời gốc)" để AI viết câu mở đầu — video cá
    kèo 12/11/2026 bóc lời sót 29s đầu, giọng mới tới giây 29 mới cất lên (người xem lướt qua trong 3 giây đầu).
    teaser: video có đoạn mở đầu dài chừng ấy giây (cảnh món đã xong chiếu trước, mốc lời gốc đã lùi theo): đoạn 0 là
    câu hook đọc vừa trong đoạn đó, nói về đúng món đang thấy."""
    segments = merge_sentences([(a, b, strip_sub_cta(t)) for a, b, t in segments if not is_sub_cta_line(t)], out_dur)
    if not segments:
        return None
    if intro and segments[0][0] >= INTRO_MIN:
        cap = teaser + TEASER_SPILL if teaser else INTRO_SPEAK
        segments = [(0.0, min(segments[0][0], cap), "")] + segments
    slots = []
    for k, (st, en, tx) in enumerate(segments):
        nxt = segments[k + 1][0] if k + 1 < len(segments) else out_dur
        slot = max(1.0, min(nxt, out_dur) - st)
        span = max(0.8, min(en, st + slot) - st)          # người gốc nói trong khoảng này
        # số chữ ĐỌC RA ("100g" = 3 chữ) như prompt yêu cầu; đếm theo chữ viết thì câu nhiều số bị giao ít chữ hơn số
        # chữ phải giữ nguyên văn (rà soát 13/11/2026)
        mx = max(3, min(int(slot * _rate() * 0.92), int(spoken_words(tx) * 1.15) + 1))
        if not tx:                                         # câu mở đầu: một câu hook, không theo số chữ lời gốc
            mx = max(5, min(int(min(slot, en - st, INTRO_SPEAK) * _rate() * 0.92), 16))   # có đoạn mở đầu: vừa 3s
        slots.append((k, st, slot, mx, tx, span))
    # khoảng "lo-mx": bản 25 ghi "tối đa mx" thì AI chỉ viết ~67% (giọng phủ ~53% khe, video lặng nhiều — Botkeep
    # 12/11/2026); bản 24 ghi "khoảng mx" thì viết đủ nhưng 19/46 đoạn vượt. Khoảng giữ AI sát số từ cả hai phía.
    desc_lines = "\n".join(f"{k} | {st:.1f}s | {max(2, round(mx * 0.8))}-{mx} từ | {tx or '(chưa có lời gốc)'}"
                           for k, st, slot, mx, tx, _ in slots)
    prompt = TIMED_PROMPT.format(mode=_MODE_VI.get(mode, mode), title=title or "(không có)",
                                 desc=(desc or "(không có)")[:400], segments=desc_lines) + style_block() + extra
    prompt += voice_block(examples)
    # câu mẫu của sổ giọng cũng không được chép (như câu đã viết ở lô trước)
    avoid = list(avoid or []) + [x["new"] for x in (examples or [])]
    if intro:
        prompt += HOOK_RULE
        if hook_tip:
            prompt += HOOK_TIP.format(tip=hook_tip.strip().rstrip("."))
        if teaser and not slots[0][4]:
            prompt += TEASER_HINT.format(dur=teaser)
    ost_auto = config.OST_AUTO == "1"
    if ost_auto:
        prompt += OST_HINT
    images = None
    if images_for and genai and config.GEMINI_API_KEY:
        try:
            images = await images_for([(st, st + slot, tx) for k, st, slot, mx, tx, span in slots])
            prompt += IMAGE_HINT
        except Exception:
            images = None
    async def _gem(pr, _imgs=images):
        schema = TimedOstOut if ost_auto else TimedOut
        try:
            return await _call_gemini(pr, _imgs, schema=schema)
        except Exception as e:
            # chỉ bỏ ảnh khi lỗi DO ẢNH; quá tải / hết lượt thì bỏ ảnh cũng không giúp mà lời mất khớp hình
            if not _imgs or not _image_rejected(str(e)):
                raise
            logging.warning("Gemini không nhận ảnh (%s), viết lại không kèm ảnh", str(e)[:80])
            return await _call_gemini(pr.replace(IMAGE_HINT, ""), schema=schema)
    chain = llm.providers(_gem, lambda pr: _call_openrouter(pr.replace(IMAGE_HINT, "")),
                          gemini_ok=bool(genai and config.GEMINI_API_KEY), openrouter_ok=bool(config.OPENROUTER_API_KEY))
    def _parse(raw):
        d = loads_lenient(raw)
        got, osts = {}, {}
        for x in d.get("segments", []):
            try:
                k = int(x["i"])
                if (x.get("text") or "").strip():
                    got[k] = dedupe_items(dialect.south(x["text"].strip()))   # từ miền Bắc -> Nam, TRƯỚC khi kiểm
                if ost_auto and x.get("ost") in ("orig", "both"):
                    osts[k] = x["ost"]
            except Exception:
                continue
        for k in sorted(osts)[max(1, len(slots) // 5):]:   # tối đa 1/5 số đoạn giữ tiếng gốc
            osts.pop(k)
        d["_ost"] = osts
        return d, got

    def _replays(got, osts):
        """Đoạn kể lại việc cho nguyên liệu vào đã làm ở đoạn trước (replayed_steps)."""
        return replayed_steps([(k, tx, "" if osts.get(k) == "orig" else got.get(k, ""))
                               for k, st, slot, mx, tx, span in slots if tx and not _side(tx)])

    def _repeats(got, osts):
        """Đoạn nhắc lại ý / thời lượng đã nói ở đoạn trước (repeated_ideas). Đoạn 0 (câu hook) được nhắc lại về sau."""
        return repeated_ideas([(k, got.get(k, "")) for k, st, slot, mx, tx, span in slots
                               if k and osts.get(k) != "orig" and k in got and not _side(tx)])

    def _plates(got, osts):
        """Đoạn nói «dĩa + món» khi món chưa được bày ra dĩa (early_plate). Đoạn 0 (câu hook, hay chiếu món xong) không
        tính."""
        return early_plate([(k, tx, "" if osts.get(k) == "orig" else got.get(k, ""))
                            for k, st, slot, mx, tx, span in slots if k and tx and not _side(tx)])

    def _bad(got, osts):
        """Đoạn thiếu hoặc chép lời gốc (>=45% cụm 2 từ trùng). Đoạn giữ nguyên tiếng gốc không cần lời mới."""
        out = []
        again = _replays(got, osts)
        plates = _plates(got, osts)
        for k, st, slot, mx, tx, span in slots:
            if osts.get(k) == "orig":
                continue
            if k not in got or copy_ratio(got[k], tx) >= 0.45 \
                    or any(a and copy_ratio(got[k], a) >= 0.5 for a in (avoid or [])) \
                    or _CTA_RE.search(got[k]) or has_sub_cta(got[k]) \
                    or _hook_bad(k, got[k]) or side_lost(got[k], tx) or k in again or k in plates:   # lặp câu / quảng cáo
                out.append(k)
        return out

    def _hook_bad(k, text):
        return intro and k == 0 and bool(_GREET_RE.search(text or ""))

    def _sparse(got, osts):
        """Cả bài viết thưa: tổng số chữ < 70% tổng mức tối thiểu của các khe ≥ 5 chữ. Từng đoạn chỉ hơi ngắn nên lọt
        luật từng đoạn, cộng lại video lặng gần nửa thời lượng (bản 48, job 5: 322 chữ, giọng phủ 54% video, 7 quãng lặng
        5-10 giây; job 4 cùng video: 477 chữ, 80%)."""
        want = [(k, max(2, round(mx * 0.8))) for k, st, slot, mx, tx, span in slots if osts.get(k) != "orig" and mx >= 5]
        need = sum(w for _, w in want)
        return need >= 40 and sum(spoken_words(got.get(k, "")) for k, _ in want) < 0.7 * need

    def _dead(got, osts):
        """Đoạn đọc xong còn lặng > DEAD_AIR giây tới đoạn sau (ước theo số chữ / tốc độ giọng) mà chưa viết tới mức tối
        thiểu của khe. Job 7: 3 quãng lặng 4-5 giây (1:35, 2:10, 2:24) — người xem lướt đi khi video im. Không tính
        đoạn cuối (câu chốt kênh nối vào), khúc điểm danh / tóm tắt, và đoạn ngay trước đoạn giữ tiếng gốc."""
        out = []
        for n, (k, st, slot, mx, tx, span) in enumerate(slots):
            if n + 1 >= len(slots) or osts.get(k) == "orig" or osts.get(slots[n + 1][0]) == "orig" or k not in got \
                    or _side(tx):
                continue
            words = spoken_words(got[k])
            if words < max(2, round(mx * 0.8)) and slot - words / _rate() > DEAD_AIR:
                out.append(k)
        return out

    def _thin(got, osts):
        """Đoạn viết quá ít chữ so với khe (dưới 60% mức tối thiểu): giọng hết sớm, video lặng 5-7 giây giữa các câu
        (bản 43, video mực: AI viết ~nửa số chữ, nghe như đọc tít rời rạc). Khe ngắn (< 8 chữ) không tính. Cả bài thưa
        (_sparse): thêm các đoạn dưới 80% mức tối thiểu, đoạn thiếu nhiều nhất trước."""
        out = [k for k, st, slot, mx, tx, span in slots
               if osts.get(k) != "orig" and k in got and mx >= 8 and spoken_words(got[k]) < 0.6 * round(mx * 0.8)]
        if _sparse(got, osts):
            low = [(spoken_words(got[k]) / max(2, round(mx * 0.8)), k) for k, st, slot, mx, tx, span in slots
                   if osts.get(k) != "orig" and k in got and k not in out and mx >= 5
                   and spoken_words(got[k]) < 0.8 * max(2, round(mx * 0.8))]
            out += [k for _, k in sorted(low)]
        return out + [k for k in _dead(got, osts) if k not in out]

    orig_all = " ".join(s[4] for s in slots)
    async def attempt(p):
        name, call = p.name, p.call
        d, got = _parse(await call(prompt))

        def _score(g, osts):
            """(số đoạn chép/thiếu, [nguyên liệu + số liệu] thiếu, [nguyên liệu + số liệu] bịa/đổi).
            Đoạn giữ nguyên tiếng gốc: người xem vẫn nghe lời gốc nên tính như đã nhắc đủ."""
            new_all = " ".join(tx if osts.get(k) == "orig" else g.get(k, "") for k, st, slot, mx, tx, span in slots)
            miss, added = ingredients.check_rewrite(orig_all, new_all)
            mn, an = ingredients.check_numbers(orig_all, new_all)
            return _bad(g, osts), miss + mn, _not_in_glossary(added) + an

        bad, miss, added = _score(got, d["_ost"])
        thin = _thin(got, d["_ost"])
        for attempt in range(2):                    # tối đa 2 lần bắt viết lại
            txt_of = {k: tx for k, st, slot, mx, tx, span in slots}
            again = _replays(got, d["_ost"])
            plates = _plates(got, d["_ost"])
            severe = [k for k in bad if k not in got or copy_ratio(got[k], txt_of.get(k, "")) >= 0.6
                      or _CTA_RE.search(got.get(k, "")) or has_sub_cta(got.get(k, ""))
                      or _hook_bad(k, got.get(k, "")) or side_lost(got.get(k, ""), txt_of.get(k, "")) or k in again
                      or k in plates]
            # viết lại khi: có đoạn chép nặng (≥60%, ví dụ thật trong log: 90%) / còn quảng cáo / thiếu đoạn,
            # hoặc ≥25% số đoạn chép nhẹ. Mỗi lượt AI giờ chỉ vài giây nên siết chặt được.
            too_copy = bool(severe) or (bad and len(bad) >= max(1, len(slots) // 4))
            too_thin = len(thin) >= max(2, len(slots) // 4) or _sparse(got, d["_ost"]) or len(_dead(got, d["_ost"])) >= 2
            reps = _repeats(got, d["_ost"])
            too_rep = len(reps) >= 2                  # một lần nhắc lại thì cho qua; từ hai lần là độn lời
            if not (too_copy or miss or added or too_thin or too_rep):
                break
            logging.warning("AI %s lần %d: %d đoạn chép/thiếu; %d đoạn quá ngắn; nguyên liệu thiếu %s; bịa/đổi %s -> "
                            "viết lại", name, attempt + 1, len(bad), len(thin), miss, added)
            why = []
            ads = [k for k in bad if _CTA_RE.search(got.get(k, ""))]
            subs = [k for k in bad if k not in ads and has_sub_cta(got.get(k, ""))]
            hooks = [k for k in bad if _hook_bad(k, got.get(k, ""))]
            sides = [k for k in bad if side_lost(got.get(k, ""), txt_of.get(k, ""))]
            if sides:
                why.append("đoạn điểm danh / tóm tắt phải đọc ĐỦ các ý của lời gốc theo thứ tự, không đổi thành lời khen "
                           "chung chung: " + ", ".join(f"{k} (gốc «{txt_of[k]}», bản trước «{got.get(k, '')}»)"
                                                       for k in sides[:4]))
            if again:
                why.append("các đoạn sau KỂ LẠI bước cho nguyên liệu vào ĐÃ LÀM ở đoạn trước, như thể đang làm lại (hình lúc"
                           " đó là kết quả của bước ấy, người xem tưởng video quay ngược); viết về điều đang thấy theo lời"
                           " gốc của chính đoạn đó, không cho lại nguyên liệu vào: " +
                           ", ".join(f"{k} (gốc «{txt_of[k]}», bản trước «{got.get(k, '')}», đã cho vào trước đó: "
                                     f"{_steps_vi(again[k])})" for k in sorted(again)[:4]))
            if plates:
                why.append("các đoạn sau gọi món là «dĩa ...» nhưng lúc đó món CHƯA được bày ra dĩa (lời gốc tới đoạn sau mới"
                           " đổ / bày ra), hình đang là món trong nồi / chảo / vợt: gọi đúng thứ đang thấy: " +
                           ", ".join(f"{k} («{got.get(k, '')}»)" for k in sorted(plates)[:4]))
            copies = [k for k in bad if k not in ads and k not in subs and k not in hooks and k not in sides
                      and k not in again and k not in plates]
            if subs:
                why.append("các đoạn sau còn lời kêu gọi đăng ký/theo dõi kênh, BỎ ý đó (viết lời chốt về món "
                           "hoặc để trống): " + ", ".join(f"{k} («{got[k]}»)" for k in subs[:8]))
            if hooks:
                why.append(f"đoạn 0 phải là CÂU HOOK gây tò mò, không chào hỏi («{got.get(0, '')}»)")
            if too_copy and copies:
                why.append("các đoạn sau thiếu hoặc chép gần nguyên lời gốc: " +
                           ", ".join(f"{k} («{got.get(k, '(thiếu)')}»)" for k in copies[:8]))
            if ads:
                why.append("các đoạn sau còn lời bán hàng/kêu gọi mua hoặc tự nhận sản phẩm của người gốc, "
                           "viết lại thành lời về món ăn: " + ", ".join(f"{k} («{got[k]}»)" for k in ads[:8]))
            if miss:
                why.append("BỎ MẤT nguyên liệu/số liệu: " + ", ".join(miss) +
                           " (phải nhắc lại đủ, đúng đoạn có nó trong lời gốc)")
            if added:
                why.append("BỊA hoặc ĐỔI nguyên liệu/số liệu không có trong lời gốc: " + ", ".join(added) +
                           " (gốc là trứng gà thì KHÔNG được viết trứng vịt; gốc 2 muỗng thì KHÔNG được viết 3 muỗng; gốc"
                           " 3-4 giọt thì KHÔNG được viết 3-4 muỗng;"
                           " gốc nghe không rõ số thì bỏ số, KHÔNG tự điền)")
            if too_rep:
                why.append("các đoạn sau NHẮC LẠI ý / thời lượng đã nói ở đoạn trước (người xem nghe lặp là lướt đi): "
                           "viết điều MỚI đang thấy trên hình của chính đoạn đó (màu, tiếng, độ chín, thao tác tay), "
                           "không lặp lại cụm hay thời lượng đã nói: " +
                           ", ".join(f"{k} («{got.get(k, '')}», lặp «{reps[k]}»)" for k in sorted(reps)[:6]))
            if too_thin:
                need = {k: (max(2, round(mx * 0.8)), mx) for k, st, slot, mx, tx, span in slots}
                why.append("các đoạn sau QUÁ NGẮN, giọng hết sớm làm video lặng dài; viết lại thành câu nói chuyện đủ "
                           "ý trong đúng khoảng số từ (tả thêm điều đang thấy trên hình, không thêm thao tác hay số "
                           "liệu mới): " + ", ".join(f"{k} («{got.get(k, '')}», cần {need[k][0]}-{need[k][1]} từ)"
                                                      for k in thin[:10]))
            fix = prompt + "\n\nBẢN TRƯỚC BỊ LOẠI vì " + "; ".join(why) + \
                ". Viết lại TOÀN BỘ: dùng từ ngữ khác lời gốc, nhưng giữ ĐỦ và ĐÚNG mọi nguyên liệu, con số."
            try:
                d2, got2 = _parse(await call(fix))
                b2, m2, a2 = _score(got2, d2["_ost"])
                t2 = _thin(got2, d2["_ost"])
                old_err = len(bad) + 3 * (len(miss) + len(added)) + 0.5 * len(thin) + 3 * _sparse(got, d["_ost"]) \
                    + 0.5 * len(reps)
                new_err = len(b2) + 3 * (len(m2) + len(a2)) + 0.5 * len(t2) + 3 * _sparse(got2, d2["_ost"]) \
                    + 0.5 * len(_repeats(got2, d2["_ost"]))
                if new_err < old_err:
                    d, got, bad, miss, added, thin = d2, got2, b2, m2, a2, t2
            except Exception as e:       # 503/quá giờ của Gemini: chuyện thường, không in cả traceback
                logging.warning("AI viết lại lỗi (%s: %s), dùng bản tốt nhất hiện có", type(e).__name__,
                                str(e)[:120])
                break
        if miss or added:
            logging.warning("Sau khi viết lại vẫn lệch nguyên liệu/số liệu: thiếu %s, bịa %s", miss, added)
        # "số đơn vị" (không chỉ con số): "4 muỗng" tự đổi từ "4 giọt" thì bỏ cụm đó, giữ "luộc 4 phút" (bản 47)
        bad_nums = set(ingredients.check_numbers(orig_all, " ".join(got.values()))[1])
        if bad_nums:
            # vẫn còn số AI tự điền (bản 43: gốc "nửa muỗng" nghe thành "nổ muỗng", AI viết "1 muỗng" x3): bỏ con số
            # khỏi câu, đọc sai định lượng tệ hơn không đọc số
            for k in list(got):
                fixed = ingredients.drop_numbers(got[k], bad_nums)
                if fixed != got[k]:
                    logging.warning("Đoạn %d: bỏ số AI tự điền (%s): %s -> %s", k, ", ".join(sorted(bad_nums)), got[k],
                                    fixed)
                    got[k] = fixed
        for k, steps in _replays(got, d["_ost"]).items():     # viết lại vẫn kể lại bước cũ: bỏ vế câu đó
            fixed = drop_replayed(got[k], steps)
            if fixed != got[k]:
                logging.warning("Đoạn %d kể lại bước đã làm (%s): %s -> %s", k, _steps_vi(steps), got[k], fixed)
                got[k] = fixed
        for k, what in _plates(got, d["_ost"]).items():        # viết lại vẫn nói «dĩa» quá sớm: "Dĩa mực" -> "Mẻ mực"
            logging.warning("Đoạn %d nói «%s» khi món chưa lên dĩa: %s", k, what, got[k])
            got[k] = unplate(got[k])
        for k, st, slot, mx, tx, span in slots:               # điểm danh: vào thẳng món đầu, khớp thẻ chữ
            if (tx or "").lstrip().startswith(SIDE_MARKS[0]) and got.get(k):
                got[k] = montage_lead(got[k])
        vary_particles(got, [k for k, *_ in slots if d["_ost"].get(k) != "orig"])
        d["_issues"] = (miss, added)
        out = []
        for k, st, slot, mx, tx, span in slots:
            ost = d["_ost"].get(k, "new") if tx else "new"     # câu mở đầu: luôn là lời mới
            # chỉ còn thiếu sau khi đã yêu cầu lại mới dùng lời gốc (trừ đoạn giữ nguyên tiếng gốc: không đọc)
            text = got.get(k, "") if ost == "orig" else strip_sub_cta(got.get(k) or tx)
            if ost != "orig" and side_lost(text, tx):    # viết lại vẫn mất ý: đọc đúng lời máy soạn cho thẻ chữ
                text = side_spoken(tx)
            # KHÔNG cắt theo số từ (cắt ngang câu -> "vàng đều hai"); đoạn dài để plan_voice
            # xử lý (mượn khoảng lặng/nói nhanh) và nếu vẫn dài thì AI rút gọn NGUYÊN CÂU.
            out.append({"i": k, "start": st, "slot": slot, "span": span, "text": text, "orig": tx, "ost": ost,
                        "copy": 0.0 if ost == "orig" else round(copy_ratio(text, tx), 2)})
        for o, (k, st, slot, mx, tx, span) in zip(out, slots):
            logging.info("Đoạn %d %.1fs | gốc: %s | mới: %s | trùng %.0f%%", k, st, tx, o["text"], o["copy"] * 100)
        script = " ".join(x["text"] for x in out if x["text"])
        fresh = 1 - sum(x["copy"] for x in out) / max(1, len(out))
        return {"segments": out, "script": script, "fresh": round(fresh, 2), "issues": d.get("_issues", ([], [])),
                "hook": clean_hook(dialect.south(d.get("hook") or ""),
                                   " ".join([title, script, *(x["orig"] for x in out), *_GLOSSARY.get()]))
                if intro else "",
                "title": dialect.south(d.get("title") or title or script[:80]).strip(),
                "caption": dialect.south(d.get("caption") or script).strip(),
                "hashtags": (d.get("hashtags") or "#viral #xuhuong #fyp").strip()}
    return await llm.first_success(chain, attempt, record=metrics.record,
                                   on_error=lambda p, e: logging.error("generate_timed: provider %s lỗi", p.name, exc_info=e))


PROOF_PROMPT = """Bạn là biên tập viên soát lời đọc cho video nấu ăn tiếng Việt (giọng miền Nam). Dưới đây là các câu sẽ được đọc lên, mỗi dòng «số | câu».
Chỉ tìm câu có CHỮ DÙNG SAI NGHĨA hoặc chữ vô nghĩa, lạc ngữ cảnh (vd «Hoàn thành dĩa mực thơm lừng rảnh rỗi nè»: «rảnh rỗi» lạc nghĩa), sai chính tả, chữ bị lặp, câu cụt bỏ lửng.
Sửa ÍT NHẤT có thể: chỉ thay hoặc bỏ đúng chữ sai; giữ nguyên ý, giọng nói chuyện, mọi con số, đơn vị và tên nguyên liệu; không thêm ý mới, KHÔNG viết lại câu đang đúng (khen ngon, từ đệm nha/nè/luôn là bình thường).
{items}
Trả về DUY NHẤT JSON: {{"fixes": [{{"i": số, "text": "câu đã sửa"}}]}}; không có câu nào sai thì "fixes": []."""


PROOF_MAX = 75.0        # giây: lượt soát lời tối đa chừng này (kể cả thử lại)


def _ingredient_seq(text: str) -> list:
    """Các nhóm nguyên liệu theo đúng thứ tự xuất hiện trong câu (tên dài ưu tiên: "hành tây" không tính thêm "hành")."""
    toks = _norm_words(text)
    out: list = []
    p = 0
    while p < len(toks):
        head = toks[p:p + 3]
        got = ingredients.find(" ".join(head)) - ingredients.find(" ".join(head[1:]))
        if got:
            n = next((L for L in (1, 2, 3) if got <= ingredients.find(" ".join(toks[p:p + L]))), len(head))
            out.append(sorted(got))
            p += n
        else:
            p += 1
    return out


def _minor_edit(old: str, new: str) -> bool:
    """Bản soát chỉ được sửa nhỏ: thay / bỏ / thêm tối đa 30% số chữ (ít nhất 2 chữ)."""
    a, b = _norm_words(old), _norm_words(new)
    ops = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")
    return 0 < changed <= max(2, 0.3 * len(a))


async def proofread(segments: list) -> list:
    """Soát lời lần cuối (bản 50): một lượt AI đọc lại cả kịch bản, chỉ sửa chữ dùng sai nghĩa / vô nghĩa (job 7, 3:01:
    «Hoàn thành dĩa mực hấp gừng thơm lừng rảnh rỗi nè»). Mỗi chỗ sửa phải nhỏ (_minor_edit), giữ đúng nguyên liệu và
    con số, không chép lời gốc; sai thì bỏ chỗ sửa đó. Sửa tại chỗ trong `segments`, trả [(i, cũ, mới)]. Không có key
    Gemini / lỗi mạng: không sửa gì."""
    items = {n: s for n, s in enumerate(segments) if s.get("ost") != "orig" and (s.get("text") or "").strip()
             and not _side(s.get("orig") or "")}
    if not items or not (genai and config.GEMINI_API_KEY):
        return []
    lines = "\n".join(f"{n} | {s['text']}" for n, s in items.items())
    try:                                  # lượt phụ: Gemini quá tải (tự thử lại nhiều lần) thì bỏ, không giữ job lại
        raw = await asyncio.wait_for(_call_gemini(PROOF_PROMPT.format(items=lines), timeout=45), PROOF_MAX)
        fixes = loads_lenient(raw).get("fixes") or []
    except Exception as e:
        logging.warning("Soát lời lỗi (%s: %s), giữ nguyên", type(e).__name__, str(e)[:100])
        return []
    done = []
    for f in fixes if isinstance(fixes, list) else []:
        try:
            n, new = int(f["i"]), dialect.south(str(f["text"]).strip())
        except Exception:
            continue
        seg = items.get(n)
        if seg is None or not new:
            continue
        old = seg["text"]
        # số và nguyên liệu phải giữ ĐÚNG THỨ TỰ (Codex PR #9: tráo «2 muỗng nước mắm, 1 muỗng đường» thành «1 muỗng
        # nước mắm, 2 muỗng đường» thì tập số / tập nguyên liệu vẫn y nguyên mà công thức đã đổi)
        if new == old or not _minor_edit(old, new) or _ingredient_seq(new) != _ingredient_seq(old) \
                or ingredients.numbers(new) != ingredients.numbers(old) \
                or copy_ratio(new, seg.get("orig") or "") >= 0.45:
            logging.info("Soát lời: bỏ chỗ sửa đoạn %d («%s» -> «%s»)", n, old, new)
            continue
        seg["text"] = new
        done.append((n, old, new))
        logging.warning("Soát lời: đoạn %d «%s» -> «%s»", n, old, new)
    return done


SHORTEN_PROMPT = """Rút gọn các câu thoại video dưới đây cho vừa thời gian, mỗi câu tối đa số từ ghi kèm.
Viết lại thành câu NGẮN HOÀN CHỈNH (đủ nghĩa, không bỏ lửng giữa chừng). Giữ NGUYÊN VĂN số liệu, định lượng,
tên riêng và TÊN MỌI NGUYÊN LIỆU (không bỏ, không đổi); giữ động từ hành động ở đầu câu; bỏ từ đệm, lời bình.
{items}
Trả về DUY NHẤT JSON: {{"segments": [{{"i": số, "text": "..."}}]}}"""


async def shorten(items: list) -> dict:
    """items: [(i, text, max_words)] -> {i: text_ngắn}. Lỗi thì trả {}."""
    prompt = SHORTEN_PROMPT.format(items="\n".join(f"{i} | tối đa {mx} từ | {t}" for i, t, mx in items)) + style_block()

    async def attempt(p):
        d = loads_lenient(await p.call(prompt))
        return {int(x["i"]): dialect.south(strip_sub_cta(x["text"].strip())) for x in d.get("segments", [])
                if x.get("text")}
    out = await llm.first_success(_chain(lambda pr: _call_gemini(pr, schema=ShortOut)), attempt,
                                  on_error=lambda p, e: logging.error("shorten lỗi", exc_info=e))
    return out if out is not None else {}


SCENE_PROMPT = """Bạn là biên kịch video ngắn tiếng Việt. Video không có lời thoại; dưới đây là ảnh đại diện của từng CẢNH theo thứ tự, kèm thời điểm và số từ tối đa. Viết lời thuyết minh cho TỪNG CẢNH, nói đúng điều đang thấy trong ảnh của cảnh đó (không đoán thứ không thấy, không bịa số liệu). Cảnh đầu là CÂU HOOK: nói ngay điều hấp dẫn nhất (món thành phẩm, mẹo, câu hỏi gây tò mò), không chào hỏi, không giới thiệu kênh. KHÔNG kêu gọi đăng ký / theo dõi / like / share kênh ở bất kỳ cảnh nào. Giọng nói chuyện tự nhiên, câu ngắn, có dấu câu. Không emoji trong "text".
Thể loại: {mode}
Tiêu đề gốc: {title}
Mô tả gốc: {desc}
{scenes}
Trả về DUY NHẤT JSON: {{"segments": [{{"i": 0, "text": "..."}}, ...], "title": "...", "caption": "... (được dùng emoji)", "hashtags": "...", "hook": "3-7 chữ hiện trên màn hình 3 giây đầu, viết chữ thường có dấu đầy đủ (máy tự in hoa)"}}"""


async def generate_scenes(title: str, desc: str, mode: str, segs: list, images: list, out_dur: float,
                          examples: list | None = None):
    """Video không lời: AI (Gemini, nhìn ảnh) viết lời cho từng cảnh. Trả về pack như generate_timed hoặc None."""
    if not (genai and config.GEMINI_API_KEY) or not segs:
        return None
    lines = []
    for k, (st, en, _) in enumerate(segs):
        lines.append(f"Cảnh {k} | {st:.1f}s-{en:.1f}s | tối đa {max(3, int((en - st) * _rate() * 0.85))} từ")
    prompt = SCENE_PROMPT.format(mode=_MODE_VI.get(mode, mode), title=title or "(không có)",
                                 desc=(desc or "(không có)")[:400], scenes="\n".join(lines)) + style_block() \
        + voice_block(examples)
    try:
        raw = await _call_gemini(prompt, images, schema=TimedOut, label="Ảnh cảnh", timeout=90)
        d = loads_lenient(raw)
        got = {int(x["i"]): dialect.south(strip_sub_cta((x.get("text") or "").strip())) for x in d.get("segments", [])}
        out = []
        for k, (st, en, _) in enumerate(segs):
            if got.get(k):
                out.append({"i": k, "start": st + 0.1, "slot": en - st - 0.1, "span": (en - st) * 0.9,
                            "text": got[k]})
        if not out:
            return None
        script = " ".join(x["text"] for x in out)
        metrics.record("llm", "gemini")
        return {"segments": out, "script": script,
                "title": dialect.south(d.get("title") or title or script[:80]).strip(),
                "caption": dialect.south(d.get("caption") or script).strip(),
                "hook": clean_hook(dialect.south(d.get("hook") or ""), f"{title} {desc} {script}"),
                "hashtags": (d.get("hashtags") or "#viral #fyp").strip()}
    except Exception as e:
        metrics.record("llm", "gemini", ok=False, detail=str(e))
        logging.exception("generate_scenes lỗi (model có thể không nhận ảnh)")
        return None


BATCH = 28
BATCH_PARALLEL = 2


async def generate_timed_any(title: str, desc: str, mode: str, segments: list, out_dur: float,
                             images_for=None, teaser: float = 0.0, examples: list | None = None,
                             hook_tip: str = ""):
    """Video dài: chia thành lô ~28 đoạn, mỗi lô một lần gọi AI (ngắn gọn, ít lỗi hơn một prompt khổng lồ),
    lô sau được báo là phần tiếp theo + câu cuối của lô trước để nối mạch, không mở hook lại.
    teaser: giây của đoạn mở đầu (cảnh món đã xong chiếu trước), chỉ lô đầu có câu hook cho đoạn đó."""
    segments = merge_sentences([(a, b, strip_sub_cta(t)) for a, b, t in segments if not is_sub_cta_line(t)],
                               out_dur)                                        # gộp TRƯỚC khi chia lô
    tkw = {"teaser": teaser} if teaser else {}           # chỉ truyền khi có (giữ chữ ký cũ cho các bản giả trong test)
    if hook_tip:                                           # mẹo chính: chỉ lô đầu (lô có câu hook)
        tkw["hook_tip"] = hook_tip
    ekw = {"examples": examples} if examples else {}       # sổ giọng: mọi lô cùng một bộ mẫu
    if len(segments) <= BATCH + 6:
        return await generate_timed(title, desc, mode, segments, out_dur, images_for, **tkw, **ekw)
    starts = list(range(0, len(segments), BATCH))

    async def _one(b):
        part = segments[b:b + BATCH]
        end = segments[b + BATCH][0] if b + BATCH < len(segments) else out_dur
        prev_orig = segments[b - 1][2] if b else ""
        extra = "" if b == 0 else (
            f"\n\nĐÂY LÀ PHẦN TIẾP THEO của cùng một video (từ {part[0][0]:.0f}s). KHÔNG mở bằng hook, "
            f"không chào lại. Lời GỐC ngay trước phần này là «{prev_orig}» — chỉ để hiểu mạch, KHÔNG viết lại "
            "câu đó; đoạn 0 của phần này phải nói đúng lời gốc của chính đoạn 0. Đoạn cuối của phần này "
            "chưa phải kết video" + (".\n" if b + BATCH < len(segments) else ", trừ khi đây là phần cuối.\n"))
        return await generate_timed(title, desc, mode, part, end, images_for, extra, intro=b == 0,
                                    **(tkw if b == 0 else {}), **ekw)

    # các lô chạy song song TỐI ĐA 2 (ngữ cảnh lấy từ lời gốc, không phải chờ lô trước viết xong); nhiều hơn
    # dễ chạm hạn mức Gemini miễn phí. Lô nào lỗi thì gọi lại riêng lô đó một lần, không bỏ cả kịch bản.
    sem = asyncio.Semaphore(BATCH_PARALLEL)

    async def _guarded(b):
        async with sem:
            return await _one(b)
    results = list(await asyncio.gather(*(_guarded(b) for b in starts)))
    for k, b in enumerate(starts):
        if results[k] is None:
            logging.warning("Lô %d (từ đoạn %d) lỗi, gọi lại riêng lô này", k, b)
            results[k] = await _one(b)
    if any(r is None for r in results):
        return None
    merged, first, heads = [], results[0], set()
    for r in results:
        heads.add(len(merged))                       # chỉ số đoạn đầu mỗi lô
        for s in r["segments"]:
            s = dict(s)
            s["i"] = len(merged)
            merged.append(s)
    # chặn lặp câu ĐÚNG Ở RANH GIỚI hai lô (lỗi thấy trong log thật): đoạn đầu lô sau trùng đoạn cuối lô trước
    out = []
    for k, s in enumerate(merged):
        if k in heads and out and s["text"] and out[-1]["text"] and same_sentence(s["text"], out[-1]["text"]) >= 0.85:
            logging.warning("Bỏ đoạn lặp lại câu trước: %s", s["text"][:60])
            out[-1]["slot"] = round(s["start"] + s["slot"] - out[-1]["start"], 2)   # đoạn trước nói dài ra
            continue
        out.append(s)
    for k, s in enumerate(out):
        s["i"] = k
    merged = out
    script = " ".join(s["text"] for s in merged if s["text"])
    fresh = 1 - sum(s.get("copy", 0) for s in merged) / max(1, len(merged))
    return {"segments": merged, "script": script, "fresh": round(fresh, 2), "hook": first.get("hook", ""),
            "title": first["title"], "caption": first["caption"], "hashtags": first["hashtags"]}


# Dấu hiệu lời BÁN HÀNG của người gốc trên lời gốc do máy nghe chép (có cả lỗi chính tả máy nghe:
# "chào hàng" = giỏ hàng, "răng muối" = rang muối). "mua" trần KHÔNG tính (vì "gà mua về").
# Dấu hiệu MẠNH (gần như chỉ có trong lời bán hàng) và YẾU ("mua ở chợ", "giá rất rẻ", "ủng hộ mình" cũng gặp trong
# lời nấu ăn thường): chỉ cắt khi đoạn cuối có ít nhất một dấu hiệu mạnh. Rà soát 13/11/2026: "cá này mình mua ở chợ về
# đó" + "chiên vàng là xong rồi" bị cắt mất bước cuối.
_PROMO_STRONG = re.compile(
    r"giỏ hàng|giỏ hàn|chào hàng|góc (trái|phải)|độc quyền|chính tay (em|mình|tui|tôi)|bấm vào|nhấn vào|link|mã giảm|"
    r"khuyến mãi|(đặt|chốt) mua|mua (bột|gói|hũ|sản phẩm|hàng|ngay|giúp|ủng)|sản phẩm (của|bên) (em|mình|shop)|"
    r"inbox|nhắn tin|freeship|đặt hàng|shop (em|mình)|khóa học|khoá học|đăng ký học|học viên|lớp (dạy|học) nấu|"
    r"nhận (dạy|đặt tiệc|nấu tiệc)|đặt tiệc|tiệc (trọn gói|cưới)|hotline|zalo|số điện thoại", re.IGNORECASE)
_PROMO_WEAK = re.compile(r"màn hình|ủng hộ (em|mình|shop|kênh)|giá (đang|rất|chỉ|cực|siêu|ưu)|(có|nhớ) mua|mua (ở|tại)",
                         re.IGNORECASE)
_PROMO_RE = re.compile(_PROMO_STRONG.pattern + "|" + _PROMO_WEAK.pattern, re.IGNORECASE)


RECIPE_PROMPT = """Bạn là đầu bếp kiêm biên tập viên công thức. Dưới đây là lời thoại gốc của một video nấu ăn (máy nghe chép, có thể sai chính tả), chia thành các ĐOẠN có số thứ tự{img_note}. Hãy đọc kỹ và trả về:

1. "is_recipe": video có hướng dẫn nấu một món không (false nếu là quảng cáo khóa học, tiệc, quán, vlog...).
2. "dish": tên món ngắn gọn.
3. "servings": khẩu phần CHỈ KHI lời gốc / mô tả nói rõ (vd "4 người ăn"), không thì để "".
4. "ingredients": MỌI nguyên liệu và gia vị được nhắc, theo thứ tự xuất hiện. Mỗi nguyên liệu:
   - "name": tên THẬT của nguyên liệu, tiếng Việt chuẩn. Máy nghe hay sai: phải sửa theo ý nghĩa món, ví dụ "nạt vai" -> "nạc vai", "nấm mặn" -> "nước mắm", "hột ngọt" -> "bột ngọt", "củ hành sắt" -> "củ hành xắt". Không ghi nguyên chữ máy nghe sai.
   - "qty", "unit": định lượng ĐÚNG như người trong video nói (vd "500" + "gam", "2" + "muỗng canh", "1/2" + "muỗng cà phê"). Không nói số thì để "" (vừa đủ). TUYỆT ĐỐI không đoán, không tự quy đổi, không làm tròn.
   - "seg": số thứ tự ĐOẠN nơi nghe thấy định lượng đó (-1 nếu lấy từ mô tả); "quote": trích nguyên văn mấy chữ trong đoạn đó.
   - Cùng một nguyên liệu dùng ở hai bước với hai lượng khác nhau thì ghi HAI dòng.
   - "first_img": số thứ tự ẢNH đầu tiên thấy rõ nguyên liệu đó (-1 nếu không có ảnh / không thấy).
5. "steps": chia TOÀN BỘ các đoạn thành các bước theo đúng thứ tự, mỗi đoạn thuộc đúng một bước; "a", "b" là số đoạn đầu và cuối của bước. Chia NHỎ: mỗi bước một thao tác chính (không gộp cả quá trình chiên vài phút thành một bước); các đoạn chỉ giải thích, khen, tán gẫu, không có thao tác thì tách riêng thành bước "other" / "reaction" / "wait".
   - "kind": intro (chào hỏi, giới thiệu) | ingredients (bày / giới thiệu nguyên liệu) | prep (sơ chế, ướp) | cook (nấu trên lửa) | wait (chờ chín, tán gẫu trong lúc chờ) | plate (bày ra đĩa) | taste (nếm, ăn thử) | reaction (khen, cảm thán, không có thao tác) | promo (quảng cáo quán, khóa học, sản phẩm, kêu gọi) | other.
   - "text": câu hướng dẫn ngắn (≤ 25 chữ) cho phần CÁCH LÀM của bài đăng, viết lại bằng lời của bạn{style}, chỉ dùng nguyên liệu và con số có trong lời gốc; để "" cho intro / reaction / promo / other.
   - "ingredients": tên nguyên liệu dùng ở bước đó.
   - "independent": true nếu đây là bước SƠ CHẾ không phụ thuộc bước sơ chế liền trước (vd băm tỏi và thái hành).
6. "tips": tối đa 3 mẹo hay. Ưu tiên mẹo chính người trong video nói (ghi "seg" của đoạn đó). Được thêm TỐI ĐA 1 mẹo kỹ thuật của bạn ("seg": -1) gắn với một bước có trong video: không có con số, không nói về sức khỏe / chữa bệnh / giảm cân, không thêm nguyên liệu mới.

Không kêu gọi đăng ký / theo dõi kênh. Không lời quảng cáo của quán, khóa học.

Tiêu đề gốc: {title}
Mô tả gốc: {desc}

CÁC ĐOẠN (số thứ tự | lời gốc):
{segments}

Trả về DUY NHẤT JSON hợp lệ, không markdown:
{{"is_recipe": true, "dish": "...", "servings": "", "ingredients": [{{"name": "...", "qty": "...", "unit": "...", "seg": 0, "quote": "...", "first_img": -1}}], "steps": [{{"kind": "prep", "a": 0, "b": 2, "text": "...", "ingredients": ["..."], "independent": false}}], "tips": [{{"text": "...", "seg": 3}}]}}"""

RECIPE_SEG_MAX = 160          # đoạn lời gốc tối đa gửi AI (video 5 phút ~ 80-120 đoạn)


async def recipe_facts(title: str, desc: str, texts: list[str], images: list | None = None,
                       times: list | None = None) -> dict | None:
    """AI đọc lời gốc (và ảnh theo mốc giây) -> nguyên liệu + định lượng kèm chỗ nghe thấy, các bước có nhãn, mẹo.
    CHỈ là đề xuất: rmvideo/domain/recipe.verify kiểm từng con số với lời gốc. Lỗi / không có AI: None."""
    texts = [str(t or "").strip() for t in texts][:RECIPE_SEG_MAX]
    if not texts:
        return None
    img_note = ""
    if images and times:
        img_note = ("; kèm " + str(len(images)) + " ảnh chụp theo thời gian ("
                    + ", ".join(f"Ảnh {k}: giây {t:.0f}" for k, t in enumerate(times)) + ")")
    style = {"mien_nam": " (giọng miền Nam, gọn)", "mien_tay": " (giọng miền Tây, gọn)"}.get(config.SPEECH_STYLE, "")
    prompt = RECIPE_PROMPT.format(img_note=img_note, style=style, title=title or "(không có)",
                                  desc=(desc or "(không có)")[:800],
                                  segments="\n".join(f"{k} | {t}" for k, t in enumerate(texts)))

    def norm(d: dict) -> dict:
        for st in d.get("steps") or []:                  # schema dùng a/b; JSON mode tự do có thể ghi from/to
            st.setdefault("from", st.get("a", 0))
            st.setdefault("to", st.get("b", st.get("from", 0)))
        if not isinstance(d.get("ingredients"), list) or not isinstance(d.get("steps"), list):
            raise ValueError("thiếu ingredients/steps")
        return d

    async def attempt(p):
        return norm(loads_lenient(await p.call(prompt)))
    return await llm.first_success(
        _chain(lambda pr: _call_gemini(pr, images=images or None, schema=RecipeOut, label="Ảnh", timeout=90)),
        attempt, record=metrics.record,
        on_error=lambda p, e: logging.warning("AI đọc công thức bằng %s lỗi (%s)", p.name, str(e)[:120]))


def find_promo_tail(segs: list, dur: float) -> float | None:
    """Mốc bắt đầu đoạn QUẢNG CÁO Ở CUỐI video của người gốc (giới thiệu sản phẩm, giỏ hàng, kêu gọi mua),
    để CẮT BỎ cả hình lẫn tiếng (viết lại lời thì hình người gốc cầm sản phẩm vẫn còn).
    Điều kiện chặt để không cắt nhầm công thức: đoạn bắt đầu bằng 1 câu có dấu hiệu bán hàng, nằm ở nửa sau
    video, kéo tới hết video, và ≥40% số câu trong đoạn có dấu hiệu. Trả None nếu không có."""
    if not segs or dur <= 0:
        return None
    hits = [bool(_PROMO_RE.search(t or "")) for _, _, t in segs]
    strong = [bool(_PROMO_STRONG.search(t or "")) for _, _, t in segs]
    for i, (st, _, _) in enumerate(segs):
        if not hits[i] or st < 0.5 * dur or dur - st < 3:
            continue
        tail = hits[i:]
        if sum(tail) / len(tail) >= 0.4 and any(strong[i:]):    # dấu hiệu yếu không đủ để cắt công thức
            return round(max(0.0, st - 0.05), 2)
    return None


def loads_lenient(raw: str) -> dict:
    """Đọc JSON từ AI, chịu lỗi: rào ```json, dấu phẩy thừa, ngoặc kép cong; hỏng hẳn thì vẫn moi từng
    {"i":..,"text":".."} và title/caption/hashtags bằng regex thay vì vứt cả lượt."""
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    body = t[t.index("{"): t.rindex("}") + 1] if "{" in t and "}" in t else t
    for cand in (body, re.sub(r",\s*([}\]])", r"\1", body.replace("“", '"').replace("”", '"'))):
        try:
            return json.loads(cand)
        except Exception:
            pass
    # đọc text tới dấu " ngay trước } (chịu được ngoặc kép lồng chưa thoát: "Chặt "nhỏ" ra")
    segs = [{"i": int(i), "text": s.replace('\\"', '"').replace('"', "").strip()}
            for i, s in re.findall(r'"i"\s*:\s*(\d+)\s*,\s*"text"\s*:\s*"(.*?)"\s*\}', body, flags=re.S)]
    if not segs:
        raise ValueError("AI trả về không đọc được: " + t[:120])
    d = {"segments": segs}
    for k in ("title", "caption", "hashtags"):
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % k, body)
        if m:
            d[k] = m.group(1)
    logging.warning("JSON của AI bị hỏng, đã cứu được %d đoạn", len(segs))
    return d


def same_sentence(a: str, b: str) -> float:
    """Độ giống hai câu, đối xứng, theo thứ tự từ (0..1). Đo trên log thật: câu lặp >= 0.96,
    câu khác ý (kể cả cùng khuôn "Giờ mình ... nè", "Hợp làm món gà/cá tôm") <= 0.67 -> ngưỡng 0.85."""
    import difflib
    w = lambda s: re.findall(r"\w+", (s or "").lower())
    return difflib.SequenceMatcher(None, w(a), w(b)).ratio()


def platform_post(title: str, caption: str, hashtags: str, credit: str = "", recipe: str = "",
                  recipe_line: str = "") -> str:
    """Nội dung đăng sẵn cho 3 nền tảng từ 1 bộ tiêu đề/caption/hashtag. credit: dòng ghi nguồn + nhãn giọng AI
    (YouTube, TikTok bắt khai báo nội dung AI trông như thật; ghi nguồn giúp xử lý khiếu nại)."""
    tags = ["#" + t.lstrip("#") for t in hashtags.replace(",", " ").split() if t.strip("#")]   # AI đôi khi quên dấu #
    tiktok = (caption.strip() + " " + " ".join(tags[:6])).strip()
    if len(tiktok) > 150:                      # TikTok hiển thị tốt dưới ~150 ký tự
        cap = caption.strip()
        if len(cap) > 110:                     # caption ngắn thì giữ nguyên, chỉ bớt hashtag (trước đây mất chữ cuối)
            cap = cap[:110].rsplit(" ", 1)[0] + "…"
        tiktok = (cap + " " + " ".join(tags[:4])).strip()
    facebook = f"{caption.strip()}\n\n{' '.join(tags[:3])}"
    youtube = f"{title.strip()} #Shorts\n\n{caption.strip()}\n\n{' '.join(tags[:8])}"
    if recipe:                                 # công thức đã đối chiếu (POST_RECIPE / TIPS): nằm giữa caption và hashtag
        facebook = f"{caption.strip()}\n\n{recipe.strip()}\n\n{' '.join(tags[:3])}"
        youtube = f"{title.strip()} #Shorts\n\n{caption.strip()}\n\n{recipe.strip()}\n\n{' '.join(tags[:8])}"
    if recipe_line:
        tiktok = f"{tiktok}\n{recipe_line.strip()}"
    if credit:
        tiktok, facebook, youtube = f"{tiktok}\n{credit}", f"{facebook}\n\n{credit}", f"{youtube}\n\n{credit}"
    return (f"📌 {title.strip()}\n\n▶ TikTok:\n{tiktok}\n\n▶ Facebook:\n{facebook}\n\n▶ YouTube Shorts:\n{youtube}")
