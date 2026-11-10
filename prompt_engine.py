"""Sinh trọn gói nội dung: kịch bản voiceover + tiêu đề + caption + hashtag để đăng luôn.
2 provider: Gemini -> OpenRouter (chuẩn OpenAI-compatible) -> mẫu dự phòng.
LUẬT CỨNG: giữ nguyên văn định lượng công thức, giá, địa chỉ, tên quán/thương hiệu."""
import asyncio
import json
import logging
import re
import time

import aiohttp

import config
import ingredients
import metrics

try:
    from google import genai
except Exception:
    genai = None

_STYLE = {
    "mien_tay": """

GIỌNG VĂN MIỀN TÂY NAM BỘ (bắt buộc): viết như người miền Tây đang nói chuyện thân tình với bà con.
- Xưng "tui" hoặc "mình", gọi người xem "bà con", "cả nhà", "mấy bà", "mấy ông".
- Từ ngữ miền Nam: chén (không dùng bát), muỗng (không dùng thìa), heo (không dùng lợn), trái (không dùng quả), bắp (ngô), đậu phộng (lạc), ngò (rau mùi), thơm/khóm (dứa), kêu (gọi), xài (dùng), thiệt (thật), hổng (không), nhứt (nhất), mắc (đắt), bự (to), dzô dùng chữ "vô", "ra đây nè".
- Tiếng đệm cuối câu tự nhiên: nè, nha, nghen, hen, á, đó, luôn, vậy đó. Cảm thán: "trời ơi", "ngon dữ thần", "ngon hết sảy", "quá trời quá đất", "bá cháy".
- Vừa phải, không lố: mỗi câu tối đa 1 tiếng đệm, không câu nào cũng "nè". Viết đúng chính tả chuẩn (viết "vậy", "vô", không viết "dzậy", "zô") để giọng đọc phát âm đúng.
- Đổi từ địa phương KHÔNG đổi số liệu: "2 thìa" thành "2 muỗng" được, nhưng con số giữ nguyên.""",
    "mien_nam": """

GIỌNG VĂN MIỀN NAM (bắt buộc): xưng "mình"/"tui", gọi "mọi người", "cả nhà"; dùng từ miền Nam (chén, muỗng, heo, trái, bắp, xài, thiệt); tiếng đệm vừa phải: nha, nè, á, luôn. Viết đúng chính tả chuẩn. Đổi từ địa phương không đổi số liệu.""",
    "pho_thong": "",
}


def style_block() -> str:
    return _STYLE.get(config.SPEECH_STYLE, "")


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


_MODE_VI = {"cook": "video nấu ăn ASMR", "review": "video review sản phẩm/quán", "music": "video nhảy/âm nhạc giải trí"}

PROMPT = """Bạn là biên kịch kiêm người viết content video ngắn tiếng Việt.

LUẬT BẮT BUỘC, VI PHẠM LÀ HỎNG:
- Mọi ĐỊNH LƯỢNG công thức (gam, ml, muỗng, quả, phút, nhiệt độ...), GIÁ TIỀN, ĐỊA CHỈ, số điện thoại, tên quán/thương hiệu xuất hiện trong lời thoại gốc hoặc mô tả phải được giữ NGUYÊN VĂN trong kịch bản. Không làm tròn, không đổi đơn vị, không dịch tên riêng, không bịa thêm số liệu không có trong nguồn.
- Nếu nguồn không có thông tin nào thì tuyệt đối không tự chế ra.
- Các BƯỚC thực hiện (nấu ăn, hướng dẫn) phải giữ ĐÚNG THỨ TỰ và ĐỦ SỐ BƯỚC như lời thoại gốc: không gộp, không đảo, không bỏ bước nào (kể cả bước nhỏ như lật mặt, cho nước sốt, bắc ra). Mỗi bước trong lời gốc là một câu riêng trong kịch bản; hai thao tác khác nhau (ví dụ "cắt dải dài" rồi "thái miếng") không được gộp thành một. Nếu thiếu chỗ, rút ngắn câu hook và lời bình, không được rút bước.
- Số từ phải ĐỦ như yêu cầu bên dưới (thiếu từ thì giọng đọc hết sớm, video bị câm phần cuối): nếu các bước đã đủ mà còn dư chỗ, thêm lời bình, mẹo, cảm nhận giữa các bước cho đủ.

Nhiệm vụ: viết voiceover gồm 1 câu hook giật gân (~3 giây đọc) nối liền nội dung chính, tổng khoảng {words} từ ({dur_note}).

CÁCH VIẾT (quan trọng, để không bị nhận ra là chép lại):
- DIỄN ĐẠT LẠI bằng lời của riêng bạn: không chép nguyên câu nào của lời thoại gốc (trừ số liệu, tên riêng), đổi cấu trúc câu, dùng từ đồng nghĩa đời thường.
- Viết như đang NÓI CHUYỆN với người xem: câu ngắn 6-12 từ, dùng dấu phẩy và dấu chấm để ngắt hơi, thỉnh thoảng chen 1 câu cảm thán hoặc mẹo nhỏ ("nhìn kìa", "đấy", "mẹo là...") giữa các bước, kết bằng lời rủ rê tự nhiên.
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
    n = int((duration - 1.5) * rate)   # trừ 1.5s cuối để không bị cụt
    return f"{max(15, n - 10)}-{n}", f"đọc vừa khít video {duration} giây"


def _build_prompt(title, desc, mode, transcript, duration):
    words, dur_note = _word_target(duration)
    return style_block() + "\n\n" + PROMPT.format(
        words=words, dur_note=dur_note, mode=_MODE_VI.get(mode, mode),
        title=title or "(không có)", desc=(desc or "(không có)")[:500],
        transcript=(transcript or "(không có)")[:1500],
    )


def _contents(prompt: str, images=None):
    if not images:
        return prompt
    from google.genai import types
    parts = [prompt]
    for k, img in enumerate(images):
        if img:
            parts += [f"Ảnh đoạn {k}:", types.Part.from_bytes(data=img, mime_type="image/jpeg")]
    return parts


def retry_delay(err: str, attempt: int) -> float | None:
    """Số giây nên chờ trước khi gọi lại Gemini; None = lỗi không đáng thử lại.
    503/UNAVAILABLE (quá tải): 3s, 6s. 429/RESOURCE_EXHAUSTED (hết hạn mức): theo retryDelay Google trả
    về nếu có (vd "retryDelay": "17s", "retry in 17.5s"), tối đa 60s."""
    if "429" in err or "RESOURCE_EXHAUSTED" in err:
        m = re.search(r"retry(?:Delay|\s+in)\W*([0-9.]+)\s*s", err, re.IGNORECASE)
        return min(60.0, float(m.group(1)) + 1) if m else min(60.0, 10.0 * (attempt + 1))
    if "503" in err or "UNAVAILABLE" in err or "500" in err or "INTERNAL" in err:
        return 3.0 * (attempt + 1)
    return None


GEMINI_TRIES = 3


async def _call_gemini(prompt: str, images=None) -> str:
    client = genai.Client(api_key=config.GEMINI_API_KEY)  # key đọc lúc gọi: đổi từ web là ăn ngay
    # prompt đòi JSON -> bật chế độ JSON của Gemini: API tự bảo đảm JSON hợp lệ (log thật: "Expecting ','
    # delimiter" làm mất lượt viết lại)
    cfg = {"response_mime_type": "application/json"} if "JSON" in prompt else None
    try:
        for attempt in range(GEMINI_TRIES):
            try:
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(model=config.GEMINI_MODEL, contents=_contents(prompt, images),
                                                      config=cfg), 60)
                break
            except Exception as e1:
                wait = retry_delay(str(e1), attempt)
                if wait is None or attempt == GEMINI_TRIES - 1:
                    raise
                logging.warning("Gemini lỗi tạm (%s), thử lại sau %.0fs", str(e1)[:80], wait)
                await asyncio.sleep(wait)
    except Exception as e:
        # Google ngừng model cũ và ghi tên model thay thế ngay trong lỗi 404
        m = re.search(r"use models/([\w.\-]+)", str(e))
        if "404" not in str(e) or not m:
            raise
        new_model = m.group(1)
        logging.warning("Model %s đã ngừng, tự chuyển sang %s và lưu vào Cài đặt",
                        config.GEMINI_MODEL, new_model)
        config.save_settings({"GEMINI_MODEL": new_model})
        resp = await client.aio.models.generate_content(model=new_model, contents=_contents(prompt, images), config=cfg)
    return resp.text or ""


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
    providers = []
    if genai and config.GEMINI_API_KEY:
        providers.append(("Gemini", _call_gemini))
    if config.OPENROUTER_API_KEY:
        providers.append(("OpenRouter", _call_openrouter))
    for k, (name, call) in enumerate(providers):
        try:
            pack = parse_pack(await call(prompt), title)
            metrics.record("llm", name.lower(), fallback=k > 0)
            return pack
        except Exception as e:
            metrics.record("llm", name.lower(), ok=False, fallback=k > 0, detail=str(e))
            logging.exception("Provider %s lỗi, thử provider kế tiếp", name)
    metrics.record("llm", "template", fallback=True)
    return _fallback(title, mode)


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
- Mỗi đoạn có SỐ TỪ mục tiêu (ghi kèm), viết sát con số đó: ít quá thì giọng hết sớm và lệch hình, nhiều quá thì tràn sang đoạn sau.
- Giọng nói chuyện tự nhiên: câu ngắn, có dấu câu ngắt hơi. Đoạn đầu mở bằng 1 câu hook ngắn gây tò mò.
- ĐỘNG TỪ ĐI TRƯỚC: mỗi đoạn (trừ đoạn hook đầu) phải MỞ ĐẦU bằng chính hành động đang diễn ra trên hình, vì câu được phát đúng lúc cảnh đó vừa hiện lên. Viết "Lật mặt nào, khi thịt đã vàng đều." chứ KHÔNG viết "Chờ thịt vàng đều rồi lật mặt." Viết "Áp chảo với lửa vừa." chứ KHÔNG viết "Dùng lửa vừa để áp chảo."
- BỎ LỜI QUẢNG CÁO CỦA NGƯỜI GỐC: video này được đăng lại bởi kênh khác, nên KHÔNG được giữ lời bán hàng/kêu gọi mua của người gốc (giỏ hàng, "góc trái màn hình", link, mã giảm giá, "giá đang rất tốt", "bấm vào mua", tên shop) và KHÔNG được tự nhận sản phẩm của người gốc ("bột do chính tay em làm", "độc quyền của em"). Đoạn đó viết thành lời nói về món ăn hoặc lời chốt tự nhiên ("Làm vầy là ăn được rồi, bà con thử liền nha."). Vẫn được gọi tên nguyên liệu như "bột rang muối", "bột chiên giòn" một cách chung chung.
- MỖI ĐOẠN LÀ MỘT CÂU TRỌN NGHĨA: lời gốc hay bị cắt giữa câu (vd đoạn gốc "rồi Gà thấm thì anh chị" + đoạn sau "dùng cái gói bột..."). Khi đó viết ý trọn vẹn của riêng phần đó ("Gà thấm gia vị rồi nè."), KHÔNG để câu treo kiểu "...thì bà cho tui", "Khi", "Mua". Không gộp sai nghĩa hai việc khác nhau vào một câu.
- Không emoji trong "text".

Thể loại: {mode}
Tiêu đề gốc: {title}
Mô tả gốc: {desc}

CÁC ĐOẠN (số thứ tự | thời điểm | tối đa số từ | lời gốc):
{segments}

Trả về DUY NHẤT JSON hợp lệ, không markdown:
{{"segments": [{{"i": 0, "text": "..."}}, ...],
"title": "tiêu đề đăng bài <= 90 ký tự",
"caption": "caption 2-3 câu để đăng, được dùng emoji",
"hashtags": "5-8 hashtag cách nhau bởi dấu cách"}}"""


_CTA_RE = re.compile(r"giỏ hàng|góc (trái|phải) màn hình|link (ở|dưới|trong)|mã giảm|bấm vào (mua|link|giỏ)|"
                     r"chính tay (em|mình|tui|tôi) làm|độc quyền|nhà tự làm|(nhà|em|mình|tui|tôi) tự (tay )?làm (ra )?(bột|gói)|"
                     r"(bà con|mọi người|anh chị|các bạn|cả nhà)( nhớ| hãy| cứ)? (mua|đặt|chốt)\b", re.IGNORECASE)


def _rate() -> float:
    return WORDS_PER_SEC.get(config.TTS_PROVIDER, 2.8)


def words_per_sec() -> float:
    """Tốc độ đọc đo thực tế của provider giọng đang dùng (từ/giây)."""
    return _rate()


async def generate_timed(title: str, desc: str, mode: str, segments: list, out_dur: float,
                         images_for=None, extra: str = "", avoid: list | None = None) -> dict:
    """segments: [(start, end, text)] của lời gốc. Trả về pack có "segments": [{i, start, slot, text}].
    Không bao giờ raise; nếu AI lỗi trả về None để caller dùng cách cũ."""
    # gộp đoạn bắt đầu quá sát đoạn trước (< 1.1s) vào đoạn trước: không đủ chỗ cho một câu
    merged = []
    for st, en, tx in segments:
        if merged and st - merged[-1][0] < 1.1:
            merged[-1] = (merged[-1][0], en, merged[-1][2] + " " + tx)
        else:
            merged.append((st, en, tx))
    if len(merged) >= 2 and out_dur - merged[-1][0] < 1.2:
        a, b = merged[-2], merged[-1]
        merged[-2:] = [(a[0], b[1], a[2] + " " + b[2])]
    segments = merged
    slots = []
    for k, (st, en, tx) in enumerate(segments):
        nxt = segments[k + 1][0] if k + 1 < len(segments) else out_dur
        slot = max(1.0, min(nxt, out_dur) - st)
        span = max(0.8, min(en, st + slot) - st)          # người gốc nói trong khoảng này
        mx = max(3, min(int(slot * _rate() * 0.92), int(len(tx.split()) * 1.15) + 1))
        slots.append((k, st, slot, mx, tx, span))
    desc_lines = "\n".join(f"{k} | {st:.1f}s | khoảng {mx} từ | {tx}" for k, st, slot, mx, tx, _ in slots)
    prompt = TIMED_PROMPT.format(mode=_MODE_VI.get(mode, mode), title=title or "(không có)",
                                 desc=(desc or "(không có)")[:400], segments=desc_lines) + style_block() + extra
    images = None
    if images_for and genai and config.GEMINI_API_KEY:
        try:
            images = await images_for([(st, st + slot, tx) for k, st, slot, mx, tx, span in slots])
            prompt += IMAGE_HINT
        except Exception:
            images = None
    providers = []
    if genai and config.GEMINI_API_KEY:
        async def _gem(pr, _imgs=images):
            try:
                return await _call_gemini(pr, _imgs)
            except Exception:
                if not _imgs:
                    raise
                logging.warning("Gemini không nhận ảnh, viết lại không kèm ảnh")
                return await _call_gemini(pr.replace(IMAGE_HINT, ""))
        providers.append(("Gemini", _gem))
    if config.OPENROUTER_API_KEY:
        providers.append(("OpenRouter", lambda pr: _call_openrouter(pr.replace(IMAGE_HINT, ""))))
    def _parse(raw):
        d = loads_lenient(raw)
        got = {}
        for x in d.get("segments", []):
            try:
                if (x.get("text") or "").strip():
                    got[int(x["i"])] = x["text"].strip()
            except Exception:
                continue
        return d, got

    def _bad(got):
        """Đoạn thiếu hoặc chép lời gốc (>=45% cụm 2 từ trùng)."""
        out = []
        for k, st, slot, mx, tx, span in slots:
            if k not in got or copy_ratio(got[k], tx) >= 0.45 \
                    or any(a and copy_ratio(got[k], a) >= 0.5 for a in (avoid or [])) \
                    or _CTA_RE.search(got[k]):                     # lặp câu lô trước / còn quảng cáo của người gốc
                out.append(k)
        return out

    orig_all = " ".join(s[4] for s in slots)
    for pk, (name, call) in enumerate(providers):
        try:
            d, got = _parse(await call(prompt))

            def _score(g):
                """(số đoạn chép/thiếu, [nguyên liệu + số liệu] thiếu, [nguyên liệu + số liệu] bịa/đổi)"""
                new_all = " ".join(g.get(k, "") for k, *_ in slots)
                miss, added = ingredients.check(orig_all, new_all)
                mn, an = ingredients.check_numbers(orig_all, new_all)
                return _bad(g), miss + mn, added + an

            bad, miss, added = _score(got)
            for attempt in range(2):                    # tối đa 2 lần bắt viết lại
                txt_of = {k: tx for k, st, slot, mx, tx, span in slots}
                severe = [k for k in bad if k not in got or copy_ratio(got[k], txt_of.get(k, "")) >= 0.6
                          or _CTA_RE.search(got.get(k, ""))]
                # viết lại khi: có đoạn chép nặng (≥60%, ví dụ thật trong log: 90%) / còn quảng cáo / thiếu đoạn,
                # hoặc ≥25% số đoạn chép nhẹ. Mỗi lượt AI giờ chỉ vài giây nên siết chặt được.
                too_copy = bool(severe) or (bad and len(bad) >= max(1, len(slots) // 4))
                if not (too_copy or miss or added):
                    break
                logging.warning("AI %s lần %d: %d đoạn chép/thiếu; nguyên liệu thiếu %s; bịa/đổi %s -> viết lại",
                                name, attempt + 1, len(bad), miss, added)
                why = []
                ads = [k for k in bad if _CTA_RE.search(got.get(k, ""))]
                copies = [k for k in bad if k not in ads]
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
                               " (gốc là trứng gà thì KHÔNG được viết trứng vịt; gốc 2 muỗng thì KHÔNG được viết 3 muỗng)")
                fix = prompt + "\n\nBẢN TRƯỚC BỊ LOẠI vì " + "; ".join(why) + \
                    ". Viết lại TOÀN BỘ: dùng từ ngữ khác lời gốc, nhưng giữ ĐỦ và ĐÚNG mọi nguyên liệu, con số."
                try:
                    d2, got2 = _parse(await call(fix))
                    b2, m2, a2 = _score(got2)
                    old_err = len(bad) + 3 * (len(miss) + len(added))
                    new_err = len(b2) + 3 * (len(m2) + len(a2))
                    if new_err < old_err:
                        d, got, bad, miss, added = d2, got2, b2, m2, a2
                except Exception:
                    logging.exception("AI viết lại lỗi, dùng bản tốt nhất hiện có")
                    break
            if miss or added:
                logging.warning("Sau khi viết lại vẫn lệch nguyên liệu/số liệu: thiếu %s, bịa %s", miss, added)
            d["_issues"] = (miss, added)
            out = []
            for k, st, slot, mx, tx, span in slots:
                text = got.get(k) or tx      # chỉ còn thiếu sau khi đã yêu cầu lại mới dùng lời gốc
                # KHÔNG cắt theo số từ (cắt ngang câu -> "vàng đều hai"); đoạn dài để plan_voice
                # xử lý (mượn khoảng lặng/nói nhanh) và nếu vẫn dài thì AI rút gọn NGUYÊN CÂU.
                out.append({"i": k, "start": st, "slot": slot, "span": span, "text": text, "orig": tx,
                            "copy": round(copy_ratio(text, tx), 2)})
            for o, (k, st, slot, mx, tx, span) in zip(out, slots):
                logging.info("Đoạn %d %.1fs | gốc: %s | mới: %s | trùng %.0f%%", k, st, tx, o["text"], o["copy"] * 100)
            script = " ".join(x["text"] for x in out)
            fresh = 1 - sum(x["copy"] for x in out) / max(1, len(out))
            metrics.record("llm", name.lower(), fallback=pk > 0)
            return {"segments": out, "script": script, "fresh": round(fresh, 2), "issues": d.get("_issues", ([], [])),
                    "title": (d.get("title") or title or script[:80]).strip(),
                    "caption": (d.get("caption") or script).strip(),
                    "hashtags": (d.get("hashtags") or "#viral #xuhuong #fyp").strip()}
        except Exception as e:
            metrics.record("llm", name.lower(), ok=False, fallback=pk > 0, detail=str(e))
            logging.exception("generate_timed: provider %s lỗi", name)
    return None


SHORTEN_PROMPT = """Rút gọn các câu thoại video dưới đây cho vừa thời gian, mỗi câu tối đa số từ ghi kèm.
Viết lại thành câu NGẮN HOÀN CHỈNH (đủ nghĩa, không bỏ lửng giữa chừng). Giữ NGUYÊN VĂN số liệu, định lượng,
tên riêng và TÊN MỌI NGUYÊN LIỆU (không bỏ, không đổi); giữ động từ hành động ở đầu câu; bỏ từ đệm, lời bình.
{items}
Trả về DUY NHẤT JSON: {{"segments": [{{"i": số, "text": "..."}}]}}"""


async def shorten(items: list) -> dict:
    """items: [(i, text, max_words)] -> {i: text_ngắn}. Lỗi thì trả {}."""
    prompt = SHORTEN_PROMPT.format(items="\n".join(f"{i} | tối đa {mx} từ | {t}" for i, t, mx in items)) + style_block()
    providers = []
    if genai and config.GEMINI_API_KEY:
        providers.append(_call_gemini)
    if config.OPENROUTER_API_KEY:
        providers.append(_call_openrouter)
    for call in providers:
        try:
            raw = await call(prompt)
            d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            return {int(x["i"]): x["text"].strip() for x in d.get("segments", []) if x.get("text")}
        except Exception:
            logging.exception("shorten lỗi")
    return {}


SCENE_PROMPT = """Bạn là biên kịch video ngắn tiếng Việt. Video không có lời thoại; dưới đây là ảnh đại diện của từng CẢNH theo thứ tự, kèm thời điểm và số từ tối đa. Viết lời thuyết minh cho TỪNG CẢNH, nói đúng điều đang thấy trong ảnh của cảnh đó (không đoán thứ không thấy, không bịa số liệu). Cảnh đầu mở bằng 1 câu hook gây tò mò. Giọng nói chuyện tự nhiên, câu ngắn, có dấu câu. Không emoji trong "text".
Thể loại: {mode}
Tiêu đề gốc: {title}
Mô tả gốc: {desc}
{scenes}
Trả về DUY NHẤT JSON: {{"segments": [{{"i": 0, "text": "..."}}, ...], "title": "...", "caption": "... (được dùng emoji)", "hashtags": "..."}}"""


async def generate_scenes(title: str, desc: str, mode: str, segs: list, images: list, out_dur: float):
    """Video không lời: AI (Gemini, nhìn ảnh) viết lời cho từng cảnh. Trả về pack như generate_timed hoặc None."""
    if not (genai and config.GEMINI_API_KEY) or not segs:
        return None
    from google.genai import types
    lines = []
    for k, (st, en, _) in enumerate(segs):
        lines.append(f"Cảnh {k} | {st:.1f}s-{en:.1f}s | tối đa {max(3, int((en - st) * _rate() * 0.85))} từ")
    prompt = SCENE_PROMPT.format(mode=_MODE_VI.get(mode, mode), title=title or "(không có)",
                                 desc=(desc or "(không có)")[:400], scenes="\n".join(lines)) + style_block()
    parts = [prompt]
    for k, img in enumerate(images):
        if img:
            parts += [f"Ảnh cảnh {k}:", types.Part.from_bytes(data=img, mime_type="image/jpeg")]
    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(model=config.GEMINI_MODEL, contents=parts), 90)
        raw = resp.text or ""
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        got = {int(x["i"]): (x.get("text") or "").strip() for x in d.get("segments", [])}
        out = []
        for k, (st, en, _) in enumerate(segs):
            if got.get(k):
                out.append({"i": k, "start": st + 0.1, "slot": en - st - 0.1, "span": (en - st) * 0.9,
                            "text": got[k]})
        if not out:
            return None
        script = " ".join(x["text"] for x in out)
        return {"segments": out, "script": script, "title": (d.get("title") or title or script[:80]).strip(),
                "caption": (d.get("caption") or script).strip(),
                "hashtags": (d.get("hashtags") or "#viral #fyp").strip()}
    except Exception:
        logging.exception("generate_scenes lỗi (model có thể không nhận ảnh)")
        return None


BATCH = 28
BATCH_PARALLEL = 2


async def generate_timed_any(title: str, desc: str, mode: str, segments: list, out_dur: float,
                             images_for=None):
    """Video dài: chia thành lô ~28 đoạn, mỗi lô một lần gọi AI (ngắn gọn, ít lỗi hơn một prompt khổng lồ),
    lô sau được báo là phần tiếp theo + câu cuối của lô trước để nối mạch, không mở hook lại."""
    if len(segments) <= BATCH + 6:
        return await generate_timed(title, desc, mode, segments, out_dur, images_for)
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
        return await generate_timed(title, desc, mode, part, end, images_for, extra)

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
        if k in heads and out and same_sentence(s["text"], out[-1]["text"]) >= 0.85:
            logging.warning("Bỏ đoạn lặp lại câu trước: %s", s["text"][:60])
            out[-1]["slot"] = round(s["start"] + s["slot"] - out[-1]["start"], 2)   # đoạn trước nói dài ra
            continue
        out.append(s)
    for k, s in enumerate(out):
        s["i"] = k
    merged = out
    script = " ".join(s["text"] for s in merged)
    fresh = 1 - sum(s.get("copy", 0) for s in merged) / max(1, len(merged))
    return {"segments": merged, "script": script, "fresh": round(fresh, 2),
            "title": first["title"], "caption": first["caption"], "hashtags": first["hashtags"]}


# Dấu hiệu lời BÁN HÀNG của người gốc trên lời gốc do máy nghe chép (có cả lỗi chính tả máy nghe:
# "chào hàng" = giỏ hàng, "răng muối" = rang muối). "mua" trần KHÔNG tính (vì "gà mua về").
_PROMO_RE = re.compile(
    r"giỏ hàng|giỏ hàn|chào hàng|góc (trái|phải)|màn hình|độc quyền|chính tay (em|mình|tui|tôi)|"
    r"bấm vào|nhấn vào|ủng hộ (em|mình|shop|kênh)|giá (đang|rất|chỉ|cực|siêu|ưu)|link|mã giảm|khuyến mãi|"
    r"(có|đặt|chốt|nhớ) mua|mua (bột|gói|hũ|sản phẩm|hàng|ngay|ở|tại|giúp|ủng)|sản phẩm (của|bên) (em|mình|shop)|"
    r"inbox|nhắn tin|freeship|đặt hàng|shop (em|mình)", re.IGNORECASE)


def find_promo_tail(segs: list, dur: float) -> float | None:
    """Mốc bắt đầu đoạn QUẢNG CÁO Ở CUỐI video của người gốc (giới thiệu sản phẩm, giỏ hàng, kêu gọi mua),
    để CẮT BỎ cả hình lẫn tiếng (viết lại lời thì hình người gốc cầm sản phẩm vẫn còn).
    Điều kiện chặt để không cắt nhầm công thức: đoạn bắt đầu bằng 1 câu có dấu hiệu bán hàng, nằm ở nửa sau
    video, kéo tới hết video, và ≥40% số câu trong đoạn có dấu hiệu. Trả None nếu không có."""
    if not segs or dur <= 0:
        return None
    hits = [bool(_PROMO_RE.search(t or "")) for _, _, t in segs]
    for i, (st, _, _) in enumerate(segs):
        if not hits[i] or st < 0.5 * dur or dur - st < 3:
            continue
        tail = hits[i:]
        if sum(tail) / len(tail) >= 0.4:
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


def platform_post(title: str, caption: str, hashtags: str) -> str:
    """Nội dung đăng sẵn cho 3 nền tảng từ 1 bộ tiêu đề/caption/hashtag."""
    tags = hashtags.split()
    tiktok = (caption.strip() + " " + " ".join(tags[:6])).strip()
    if len(tiktok) > 150:                      # TikTok hiển thị tốt dưới ~150 ký tự
        tiktok = (caption.strip()[:110].rsplit(" ", 1)[0] + "… " + " ".join(tags[:4])).strip()
    facebook = f"{caption.strip()}\n\n{' '.join(tags[:3])}"
    youtube = f"{title.strip()} #Shorts\n\n{caption.strip()}\n\n{' '.join(tags[:8])}"
    return (f"📌 {title.strip()}\n\n▶ TikTok:\n{tiktok}\n\n▶ Facebook:\n{facebook}\n\n▶ YouTube Shorts:\n{youtube}")
