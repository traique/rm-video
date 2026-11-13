"""Bóc lời thoại gốc KÈM mốc thời gian từng câu, để kịch bản mới được đặt đúng lúc hình đang diễn ra.
Nguồn theo ASR_ORDER (mặc định groq,gemini,capcut): Groq Whisper (verbose_json, cần key), Gemini
gemini-3.5-transcribe (mốc từng từ, có dấu câu, tự viết số "300g"; dùng key Gemini sẵn có), STT CapCut
(không cần key, API không chính thức).
Trả về (text, segments) với segments = [(start_s, end_s, text)]. Lỗi gì cũng trả ("", [])."""
import asyncio
import hashlib
import logging
import os
import re
import unicodedata

import aiohttp

import cache as cache_file
import config
import metrics
import vocab

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def _groq_post(audio_path: str, prompt: str, model: str = "", language: str = ""):
    """Một lượt gọi Groq. Trả (mã HTTP, json hoặc chuỗi lỗi)."""
    with open(audio_path, "rb") as f:
        form = aiohttp.FormData()
        form.add_field("model", model or config.GROQ_WHISPER_MODEL)
        form.add_field("response_format", "verbose_json")
        form.add_field("timestamp_granularities[]", "word")
        form.add_field("timestamp_granularities[]", "segment")     # cần no_speech_prob để lọc chữ "bịa"
        if language:                                 # khoá ngôn ngữ: Whisper đoán sai ngôn ngữ ở quãng có nhạc
            form.add_field("language", language)
        if prompt:                                   # từ điển: Whisper viết theo chính tả trong prompt
            form.add_field("prompt", prompt)
        form.add_field("file", f, filename="audio.mp3", content_type="audio/mpeg")
        async with aiohttp.ClientSession() as s:
            async with s.post(GROQ_URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                              data=form, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    return r.status, (await r.text())[:200]
                return 200, await r.json()


async def _groq(audio_path: str, prompt: str = ""):
    status, d = await _groq_post(audio_path, prompt)
    if status != 200 and prompt and (status >= 500 or status == 400):
        # Groq từng trả 500 vì tham số prompt: thử lại một lần không kèm từ điển
        logging.warning("Groq Whisper %s khi kèm từ điển, thử lại không kèm: %s", status, d)
        status, d = await _groq_post(audio_path, "")
    if status != 200:
        logging.warning("Groq Whisper %s: %s", status, d)
        metrics.record("asr", "groq", ok=False, detail=f"{status} {d}")
        return "", []
    metrics.record("asr", "groq")
    units, pieces, nbad = _parse(d)
    dur = float(d.get("duration") or 0) or max([float(x.get("end") or 0) for x in d.get("segments") or []] + [0.0])
    added = 0
    if units:                                 # video có lời: nghe lại các quãng dài không có chữ nào
        units, pieces, added = await _fill_holes(audio_path, units, pieces, dur, lang_code(d.get("language")))
    text = (" ".join(t for _, t in sorted(pieces)) if nbad or added else (d.get("text") or "")).strip()
    return text, chunk_units(units)


LONG_SEG = 9.0          # câu Whisper dài hơn thế này thì tách ở quãng nghỉ lớn nhất bên trong


def _split_long(a: float, b: float, text: str, words: list) -> list:
    """Câu Whisper quá dài -> tách đôi ở quãng nghỉ lớn nhất (khoảng giữa 20-80% câu) theo mốc từng chữ."""
    inner = [w for w in words if a - 0.05 <= (w[0] + w[1]) / 2 <= b + 0.05]
    if b - a <= LONG_SEG or len(inner) < 6:
        return [(a, b, text)]
    lo, hi = a + 0.2 * (b - a), a + 0.8 * (b - a)
    cand = [(inner[k + 1][0] - inner[k][1], k) for k in range(len(inner) - 1) if lo <= inner[k][1] <= hi]
    if not cand:
        return [(a, b, text)]
    k = max(cand)[1]
    left, right = inner[:k + 1], inner[k + 1:]
    return (_split_long(a, left[-1][1], " ".join(w[2] for w in left), words)
            + _split_long(right[0][0], b, " ".join(w[2] for w in right), words))


def from_whisper(d: dict) -> tuple:
    """Kết quả Whisper chạy TRÊN KAGGLE (asr_local; cùng dạng verbose_json của Groq) -> (text, đoạn) như
    transcribe_segments: lọc câu bịa / chào kết / lặp vòng, chia đoạn, sửa chính tả theo từ điển. Không nghe lại quãng
    trống: Kaggle đã nghe GIỌNG ĐÃ TÁCH khỏi nhạc, quãng trống là quãng không ai nói.
    Chia theo CÂU của Whisper (không ghép từng chữ theo quãng nghỉ như Groq): Whisper trên Kaggle hay bỏ dấu câu ở các
    khung sau, ghép từng chữ thì bị cắt cứng 4.6s giữa câu ("...mình đã chỉ" | "cho cả nhà"): video cá kèo 8 chỗ cắt
    giữa câu, theo câu của Whisper còn 2 (đo thật 13/11/2026)."""
    d = d or {}
    segs = d.get("segments") or []
    loops = _loops(segs)
    words = [(float(w["start"]), float(w["end"]), (w.get("word") or "").strip()) for w in d.get("words") or []
             if (w.get("word") or "").strip()]
    bad = [x for k, x in enumerate(segs) if _hallucinated(x) or k in loops]
    if bad:
        logging.info("Bỏ %d đoạn Whisper nghe nhầm/lặp lại: %s", len(bad),
                     " | ".join((x.get("text") or "").strip()[:40] for x in bad))
    units = []
    for k, x in enumerate(segs):
        t = (x.get("text") or "").strip()
        if t and not _hallucinated(x) and k not in loops:
            units += _split_long(float(x["start"]), float(x["end"]), t, words)
    text = " ".join(t for _, _, t in units).strip()
    return vocab.fix_segments(text, chunk_units(units))


_LANG = {"vietnamese": "vi", "english": "en", "chinese": "zh", "thai": "th", "korean": "ko", "japanese": "ja",
         "indonesian": "id", "malay": "ms", "khmer": "km", "lao": "lo", "spanish": "es", "french": "fr"}


def lang_code(v) -> str:
    """Ngôn ngữ Groq trả về ("vietnamese" / "vi") -> mã ISO-639-1 để khoá lượt nghe lại; không rõ thì rỗng."""
    v = str(v or "").strip().lower()
    return v if re.fullmatch(r"[a-z]{2}", v) else _LANG.get(v, "")


def _norm(t: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFC", t or "").lower()))


def _loops(segs: list) -> set:
    """Chỉ số các đoạn Whisper LẶP LẠI câu vừa nói (lặp vòng qua nhiều đoạn): lỗi thật 12/11/2026 "Các cá kèo sẽ giòn
    lâu từ trong ra ngoài." ở 84.6s, 93.9s, 98.3s (2 lần trong một đoạn). So với 1-2 đoạn giữ lại ngay trước (và cả
    hai ghép lại, vì Whisper hay cắt câu lặp ở chỗ khác) trong vòng 30s: gần như y hệt, hoặc là câu đó lặp hai lần.
    Câu NÓI TIẾP câu trước ("Cho cá vào chảo" -> "Cho cá vào chảo rồi chiên vàng") không tính."""
    import difflib

    def sim(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio()
    out, kept = set(), []
    for k, x in enumerate(segs):
        t = _norm(x.get("text"))
        st = float(x.get("start") or 0)
        prev = [p for j, p in kept[-2:] if st - float(segs[j].get("start") or 0) <= 30]
        cands = [c for c in prev + ([prev[0] + " " + prev[1]] if len(prev) == 2 else []) if len(c.split()) >= 4]
        # câu sau NỐI DÀI câu trước ("Cho một muỗng đường" -> "Cho một muỗng đường nữa") là bước tiếp theo, không phải lặp
        if len(t.split()) >= 4 and any((sim(t, c) >= 0.85 and not t.startswith(c + " ")) or sim(t, c + " " + c) >= 0.9
                                       for c in cands):
            out.add(k)
            continue
        kept.append((k, t))
    return out


def _parse(d: dict, offset: float = 0.0, strict: bool = False) -> tuple:
    """Kết quả Groq -> (từ [(đầu, cuối, chữ)], câu [(đầu, chữ)], số đoạn bỏ). Bỏ đoạn Whisper nghe nhầm và đoạn lặp
    vòng; strict: lượt nghe lại quãng trống, chặt hơn (_weak)."""
    segs = d.get("segments") or []
    loops = _loops(segs)

    def junk(x, k=-1):
        return _hallucinated(x) or (strict and _weak(x)) or k in loops
    bad = [(float(x["start"]), float(x["end"])) for k, x in enumerate(segs) if junk(x, k)]
    if bad:
        logging.info("Bỏ %d đoạn Whisper nghe nhầm (nhạc/im lặng/lặp lại): %s", len(bad),
                     " | ".join((x.get("text") or "").strip()[:40] for k, x in enumerate(segs) if junk(x, k)))

    def ok(a, b):
        m = (a + b) / 2
        return not any(s - 0.05 <= m <= e + 0.05 for s, e in bad)
    units = [(offset + float(x["start"]), offset + float(x["end"]), (x.get("word") or "").strip())
             for x in d.get("words", []) if (x.get("word") or "").strip() and ok(float(x["start"]), float(x["end"]))]
    if not units and not d.get("words"):   # không có mốc từ -> dùng segment của Whisper
        units = [(offset + float(x["start"]), offset + float(x["end"]), (x.get("text") or "").strip())
                 for k, x in enumerate(segs) if (x.get("text") or "").strip() and not junk(x, k)]
    pieces = [(offset + float(x["start"]), (x.get("text") or "").strip())
              for k, x in enumerate(segs) if (x.get("text") or "").strip() and not junk(x, k)]
    if not segs and units:                 # không có segment: ghép chữ thành câu
        pieces = [(units[0][0], " ".join(u[2] for u in units))]
    return units, pieces, len(bad)


HOLE_MIN = 8.0        # quãng ≥ 8s không có chữ nào (giữa video có lời) thì nghe lại riêng quãng đó
HOLE_N = 4            # tối đa số quãng nghe lại mỗi video
HOLE_TOTAL = 180.0    # và tổng số giây nghe lại (hạn mức Groq tính theo giây audio)
HOLE_MODEL = "whisper-large-v3"   # bản ĐẦY ĐỦ (lượt đầu mặc định bản turbo): khác model thì khác kiểu nghe sót
HOLE_ENOUGH = 0.4     # nghe lại mà < 0.4 chữ/giây của quãng thì nhờ Gemini nghe thêm
HOLE_GEMINI_TIMEOUT = 45.0


def find_holes(units: list, dur: float, min_len: float = HOLE_MIN) -> list:
    """Các quãng [a, b] dài ≥ min_len không có chữ nào (kể cả đầu và cuối video)."""
    holes, prev = [], 0.0
    for a, b, _ in sorted(units):
        if a - prev >= min_len:
            holes.append((round(prev, 2), round(a, 2)))
        prev = max(prev, b)
    if dur - prev >= min_len:
        holes.append((round(prev, 2), round(dur, 2)))
    return holes


async def _cut_audio(src: str, dst: str, start: float, length: float):
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src, "-vn", "-ac", "1",
        "-ar", "16000", "-b:a", "48k", dst, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(err.decode(errors="replace")[-150:])


async def _recheck_groq(clip: str, lang: str) -> tuple:
    """(mã HTTP, kết quả) của lượt nghe lại bằng Groq: model đầy đủ, khoá ngôn ngữ, không kèm prompt (prompt có tiêu đề
    là thứ Whisper hay chép lại). Model đầy đủ không dùng được (tài khoản không có) thì dùng model của lượt đầu."""
    status, d = await _groq_post(clip, "", model=HOLE_MODEL, language=lang)
    if status in (400, 403, 404) and HOLE_MODEL != config.GROQ_WHISPER_MODEL:
        status, d = await _groq_post(clip, "", language=lang)
    return status, d


async def _recheck_gemini(clip: str, offset: float, a: float, b: float) -> tuple:
    """Gemini nghe quãng Whisper bỏ sót (Whisper nghe hỏng cả 2 lượt ở quãng có nhạc: 0-29s video cá kèo 12/11/2026,
    người gốc nói liên tục). Trả (từ, câu) đã đổi mốc về trục cả video."""
    if not (config.GEMINI_API_KEY and "gemini" in asr_order()):
        return [], []
    try:
        # trần 45s: Gemini quá tải hay treo (log 12/11: 503 liên tục), 4 quãng x 2 lần x 120s là chờ cả chục phút
        text, words = await asyncio.wait_for(_gemini_words(clip), HOLE_GEMINI_TIMEOUT)
    except Exception as e:
        logging.warning("Gemini nghe lại quãng %.0f-%.0fs lỗi: %s", a, b, (str(e) or type(e).__name__)[:120])
        return [], []
    u = [(offset + x, offset + y, w) for x, y, w in words
         if a - 0.05 <= offset + (x + y) / 2 <= b + 0.05 and not _hallucinated({"text": w})]
    if _hallucinated({"text": text}) or not u:
        return [], []
    return u, [(u[0][0], " ".join(w for _, _, w in u))]


async def _fill_holes(audio_path: str, units: list, pieces: list, dur: float, lang: str = "") -> tuple:
    """Whisper hay bịa một câu ở quãng nhạc rồi NHẢY QUA cả khung 30s phía sau, bỏ sót lời thật (lỗi thật 12/11/2026:
    giây 143-170 người gốc rắc tiêu, ngò rí, chốt món mà bản bóc lời trống trơn -> video không có lời mới ở đó và
    giọng gốc lọt ra). Nghe lại riêng từng quãng trống dài: Groq model đầy đủ, khoá ngôn ngữ, chặt hơn lượt đầu; ra quá
    ít chữ (video cá kèo: 30s người gốc nói liên tục mà Whisper chỉ ra "Cá vào gãy là") thì nhờ Gemini nghe, lấy bản
    nhiều lời thật hơn. Trả (units, pieces, số chữ thêm)."""
    holes = find_holes(units, dur)
    picked, total = [], 0.0
    for a, b in sorted(holes, key=lambda h: h[0] - h[1]):            # quãng dài nhất trước
        if len(picked) < HOLE_N and total + (b - a) <= HOLE_TOTAL:
            picked.append((a, b))
            total += b - a
    units, pieces, added = list(units), list(pieces), 0
    for a, b in sorted(picked):
        s = max(0.0, a - 0.3)
        clip = f"{audio_path}.hole{int(a)}.mp3"
        u, p, src, stop = [], [], "Groq", False
        try:
            await _cut_audio(audio_path, clip, s, b + 0.3 - s)
            status, d = await _recheck_groq(clip, lang)
            if status == 200:
                u, p, _ = _parse(d, offset=s, strict=True)
                u = [x for x in u if a - 0.05 <= (x[0] + x[1]) / 2 <= b + 0.05]
                p = [x for x in p if a - 0.5 <= x[0] <= b]
            else:
                logging.warning("Nghe lại quãng %.0f-%.0fs: Groq %s", a, b, status)
                stop = status == 429                 # hết hạn mức Groq: quãng sau cũng sẽ lỗi
            if len(u) < HOLE_ENOUGH * (b - a):
                gu, gp = await _recheck_gemini(clip, s, a, b)
                if len(gu) > len(u):
                    u, p, src = gu, gp, "Gemini"
        except Exception as e:
            logging.warning("Nghe lại quãng %.0f-%.0fs lỗi: %s", a, b, str(e)[:120])
        finally:
            if os.path.exists(clip):
                os.remove(clip)
        logging.info("Nghe lại quãng %.0f-%.0fs không có chữ: %s", a, b,
                     f"{src} thêm {len(u)} từ «{' '.join(t for _, t in p)[:80]}»" if u else "không có lời")
        if u:
            units += u
            pieces += p
            added += len(u)
        if stop:
            break
    return sorted(units), sorted(pieces), added


# Câu Whisper hay "bịa" ở chỗ chỉ có nhạc/im lặng (học từ phụ đề YouTube), và nhãn từ điển cũ bị chép lại
_FAKE = re.compile(r"ghiền mì gõ|vietsub|subtitles? by|amara\.org", re.I)
_FAKE_WHOLE = re.compile(r"^\W*(?:từ\s+kh(?:óa|oá|oa)|keywords?)\W*$", re.I)   # "khóa" / "khoá"
# Câu chào kết kiểu YouTube mà Whisper bịa ra ở quãng nhạc — lỗi thật 12/11/2026: "Đừng quên đăng ký kênh để nhận thêm
# những video mới nhé." ở giây 140 rồi Whisper bỏ qua luôn ~27s người gốc nói thật phía sau
_OUTRO = re.compile(r"(?:đừng quên|nhớ|hãy)\s+(?:bấm\s+)?(?:like|đăng\s*ký|subscribe)|đăng\s*ký\s+kênh\s+để|"
                    r"nhận\s+thêm\s+(?:những\s+)?video|hẹn\s+gặp\s+lại\s+(?:các\s+bạn\s+)?trong\s+(?:những\s+)?video|"
                    r"cảm\s+ơn\s+(?:các\s+bạn\s+)?đã\s+(?:xem|theo\s+dõi)\s+video", re.I)


def _repeats(text: str) -> bool:
    """Whisper lặp vòng: một cụm 2-4 chữ lặp liền ≥ 3 lần ("ĐỪC BÀ NGON ĐỪC BÀ NGON ĐỪC BÀ NGON")."""
    w = re.findall(r"\w+", text.lower())
    for n in (2, 3, 4):
        for i in range(len(w) - 3 * n + 1):
            if w[i:i + n] == w[i + n:i + 2 * n] == w[i + 2 * n:i + 3 * n]:
                return True
    return False


def _shouting(text: str) -> bool:
    """Cả câu viết HOA: lời nói thật không ra chữ hoa; đây là Whisper chép lại tiêu đề HOA trong prompt."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 12 and sum(c.isupper() for c in letters) >= 0.7 * len(letters)


def _hallucinated(seg: dict) -> bool:
    """Đoạn Whisper nghe nhầm: quy tắc im lặng của chính Whisper (no_speech_prob > 0.6 và avg_logprob < -1),
    câu quảng cáo/nhãn/chào kết hay bị bịa, câu lặp vòng hoặc chữ HOA cả câu."""
    text = unicodedata.normalize("NFC", (seg.get("text") or "").strip())
    try:
        silent = float(seg.get("no_speech_prob") or 0) > 0.6 and float(seg.get("avg_logprob") or 0) < -1.0
    except (TypeError, ValueError):
        silent = False
    return silent or bool(_FAKE.search(text) or _FAKE_WHOLE.match(text) or _outro_only(text)
                          or _repeats(text) or _shouting(text))


def _outro_only(text: str) -> bool:
    """Câu chào kết BỊA: lời kêu gọi đăng ký chiếm gần hết câu. Câu thật chỉ KẾT bằng lời kêu gọi ("...cho thêm 2 muỗng
    đường nữa, các bạn nhớ đăng ký kênh nha") thì giữ: prompt_engine bỏ riêng vế kêu gọi, bước làm vẫn còn."""
    if not _OUTRO.search(text):
        return False
    rest = [p for p in re.split(r"(?<=[.!?…,;])\s+|\s+(?=(?:rồi|và|sau đó|còn)\b)", text) if not _OUTRO.search(p)]
    return len(" ".join(rest).split()) < 4


def _weak(seg: dict) -> bool:
    """Lượt NGHE LẠI một quãng trống: quãng đó thường chỉ có nhạc nên chặt hơn lượt đầu."""
    try:
        return (float(seg.get("no_speech_prob") or 0) > 0.5 or float(seg.get("avg_logprob") or 0) < -0.9
                or float(seg.get("compression_ratio") or 0) > 2.4)
    except (TypeError, ValueError):
        return True


def drop_fake_segments(segs: list) -> list:
    """Lọc lại kết quả đã lưu cache trước bản sửa (vd. đoạn "Từ khóa" 0.4s ở đầu video)."""
    return [s for s in segs if not _hallucinated({"text": s[2]})]


def parse_offset(v) -> float:
    """'1.600s' / '1s' / 1.6 -> 1.6"""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).strip().rstrip("s") or 0)


def gemini_units(resp) -> list:
    """Kết quả gemini-3.5-transcribe -> [(start, end, word)] (bỏ từ rỗng, giữ dấu câu dính kèm từ)."""
    units = []
    for cand in getattr(resp, "candidates", None) or []:
        for part in getattr(getattr(cand, "content", None), "parts", None) or []:
            tr = getattr(part, "audio_transcription", None)
            for w in getattr(tr, "words", None) or []:
                word = (w.word or "").strip()
                if word:
                    units.append((parse_offset(w.start_offset), parse_offset(w.end_offset), word))
    return sorted(units, key=lambda u: u[0])


async def _gemini(audio_path: str):
    text, units = await _gemini_words(audio_path)
    return text, chunk_units(units)


async def _gemini_words(audio_path: str):
    """gemini-3.5-transcribe với mốc từng từ -> (chữ, [(đầu, cuối, từ)]). Gọi API v1beta: google-genai 3.x mặc định
    v1, mà v1 (11/2026) chưa có trường audio_transcription_config (lỗi 400 'Unknown name audioTranscriptionConfig')."""
    from google import genai
    from google.genai import types
    with open(audio_path, "rb") as f:
        data = f.read()
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options={"api_version": "v1beta"})
    cfg = {"audio_transcription_config": {"word_timestamp": True, "language_codes": ["vi-VN"]}}
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(client.aio.models.generate_content(
                model=config.GEMINI_ASR_MODEL, contents=[types.Part.from_bytes(data=data, mime_type="audio/mpeg")],
                config=cfg), 120)
            break
        except Exception as e:
            if attempt or not any(x in str(e) for x in ("503", "UNAVAILABLE", "500")):
                raise
            await asyncio.sleep(3)
    units = gemini_units(resp)
    text = gemini_text(resp) or " ".join(u[2] for u in units)
    return text, units


def gemini_text(resp) -> str:
    """Ghép chữ từ các phần text (resp.text in cảnh báo vì kết quả có phần audio_transcription)."""
    out = []
    for cand in (getattr(resp, "candidates", None) or [])[:1]:
        for part in getattr(getattr(cand, "content", None), "parts", None) or []:
            if getattr(part, "text", None):
                out.append(part.text)
    return "".join(out).strip()


def _capcut_sync(audio_path: str):
    import tts_capcut
    units = tts_capcut.stt_words(audio_path)
    return " ".join(x[2] for x in units), chunk_units(units)


# Từ mở đầu một thao tác mới trong lời dẫn tiếng Việt: cắt đoạn TRƯỚC các từ này
_ACTION_START = {"cho", "thêm", "sau", "tiếp", "rồi", "xong", "đảo", "lật", "cắt", "thái", "đổ",
                 "bắc", "tắt", "trộn", "ướp", "rửa", "luộc", "chiên", "xào", "nêm", "rắc", "múc",
                 "dùng", "đun", "cuối", "trước", "lấy"}


def chunk_units(units, max_dur: float = 3.2, gap: float = 0.3, min_dur: float = 1.0, hard_max: float = 4.6):
    """Gộp từ thành đoạn ~1.5-3s ứng với MỘT thao tác: cắt ở chỗ ngắt hơi, dấu câu, hoặc trước
    từ mở đầu thao tác mới (khi đoạn hiện tại đã đủ dài). units: [(start, end, text)]."""
    out, cur = [], None
    for idx, (st, en, tx) in enumerate(units):
        first = tx.split()[0].lower().strip(".,!?") if tx.split() else ""
        nxt = units[idx + 1][2].lower().strip(".,!?") if idx + 1 < len(units) else ""
        if first == "cho" and (nxt.startswith("đến") or nxt.startswith("tới") or len(tx.split()) > 1 and tx.split()[1].lower().startswith(("đến", "tới"))):
            first = ""      # "cho đến khi" là từ nối, không phải "cho [nguyên liệu]"
        long_enough = cur is not None and cur[1] - cur[0] >= min_dur
        # chữ viết HOA giữa chừng = Whisper mở câu mới (nhiều khung không có dấu chấm: "...cả nhà rồi Kèo chiên giòn")
        # tên riêng nhiều chữ ("chợ Bà Chiểu", đơn vị từng chữ của Groq): chữ hoa mà chữ kế cũng hoa thì không phải câu mới
        nxt_u = units[idx + 1][2] if idx + 1 < len(units) else ""
        proper = len(tx.split()) == 1 and len(nxt_u.split()) == 1 and nxt_u[:1].isupper()
        sentence = bool(cur) and tx[:1].isupper() and not cur[2].split()[-1][:1].isupper() and not proper
        natural = (st - cur[1] >= gap or cur[2].rstrip().endswith((".", "!", "?", ","))
                   or (long_enough and (first in _ACTION_START or sentence))) if cur else False
        # quá max_dur mà chưa gặp chỗ ngắt tự nhiên thì đi tiếp (tránh cắt giữa cụm "vàng đều tám | mặt"),
        # chỉ cắt cưỡng bức khi chạm hard_max
        if cur and (natural or en - cur[0] > hard_max):
            out.append(tuple(cur))
            cur = None
        if cur is None:
            cur = [st, en, tx]
        else:
            cur[1], cur[2] = en, cur[2] + " " + tx
    if cur:
        out.append(tuple(cur))
    return [(round(a, 2), round(b, 2), t.strip()) for a, b, t in out if t.strip()]


_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stt_cache.json")
CACHE_V = 2     # 2: nghe lại quãng trống + bỏ câu chào kết/chữ HOA bịa (bản bóc lời cũ có thể thiếu cả quãng lời)


def _cache_key(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(2_000_000))
    return h.hexdigest()[:20]


async def transcribe_segments(audio_path: str, title: str = "", desc: str = ""):
    """Có cache theo hash audio: chạy lại cùng video (duyệt kịch bản, làm lại) không phải nghe lại. Khoá cache
    KHÔNG gồm từ điển/tiêu đề: lượt render bản đã duyệt có tiêu đề khác lượt đầu (link -> file giữ lại) mà vẫn
    phải dùng đúng lời gốc đã duyệt. title/desc: ngữ cảnh cho từ điển gửi Groq (xem vocab.py). Kết quả luôn qua
    bước sửa chính tả theo từ điển (làm SAU cache: đổi từ điển là áp ngay cho cả video đã nghe)."""
    prompt = vocab.groq_prompt(title, desc)
    try:
        key = _cache_key(audio_path)
    except OSError:
        key = ""
    # tệp cache hỏng (chết giữa lúc ghi) -> coi như trống rồi ghi đè; trước đây key bị xoá luôn nên cache tắt hẳn
    store = cache_file.read_json_file(_CACHE, {}) if key else {}
    hit = store.get(key) if isinstance(store, dict) else None
    if isinstance(hit, dict) and hit.get("v") == CACHE_V:
        try:
            return vocab.fix_segments(hit["text"], drop_fake_segments([tuple(x) for x in hit["segs"]]))
        except Exception:
            pass
    text, segs = await _transcribe_uncached(audio_path, prompt)
    if key and text:
        try:
            store = store if isinstance(store, dict) else {}
            store[key] = {"v": CACHE_V, "text": text, "segs": segs}
            for old in list(store)[:-40]:
                store.pop(old)
            cache_file.write_json_file(_CACHE, store)
        except Exception:
            pass
    return vocab.fix_segments(text, segs)


def asr_order() -> list:
    """Thứ tự nguồn bóc lời từ ASR_ORDER (bỏ nguồn thiếu key)."""
    order = [x.strip() for x in (config.ASR_ORDER or "groq,gemini,capcut").split(",") if x.strip()]
    have = {"groq": bool(config.GROQ_API_KEY), "gemini": bool(config.GEMINI_API_KEY), "capcut": True}
    return [x for x in order if have.get(x)]


async def _run_provider(name: str, audio_path: str, prompt: str = ""):
    if name == "groq":
        return await _groq(audio_path, prompt)
    if name == "gemini":
        return await _gemini(audio_path)
    return await asyncio.to_thread(_capcut_sync, audio_path)


async def _transcribe_uncached(audio_path: str, prompt: str = ""):
    """Thử lần lượt theo ASR_ORDER; nguồn trả lời rỗng hoặc lỗi thì sang nguồn sau.
    Video không lời thì trả ("", [])."""
    for k, name in enumerate(asr_order()):
        try:
            text, segs = await _run_provider(name, audio_path, prompt)
            if name != "groq":                         # groq tự ghi số liệu trong _groq (có mã lỗi HTTP)
                metrics.record("asr", name, fallback=k > 0)
            if text:
                return text, segs
        except Exception as e:
            metrics.record("asr", name, ok=False, fallback=k > 0, detail=str(e))
            logging.warning("Bóc lời bằng %s lỗi (%s), thử nguồn kế", name, str(e)[:120])
    return "", []


async def transcribe(audio_path: str) -> str:
    """Giữ tương thích: chỉ lấy text."""
    return (await transcribe_segments(audio_path))[0]
