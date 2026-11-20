"""(Kaggle) Nghe lại giọng VieNeu bằng Whisper, đọc lại mẩu đọc sai / lặp.

VieNeu tự hồi quy, lấy mẫu ngẫu nhiên (temperature 0.8): thỉnh thoảng lặp hoặc nói nhịu (đo 20/11: «À, mặt trời đó
tiếng Anh là sun. Bé nói theo nè: sun!» đọc thành «Bé nói theo nè, súng» hai lần). Trước đây không bước nào nghe lại
giọng (voiceplan chỉ đo độ dài) -> đọc sai một con số / nguyên liệu là hỏng công thức.

Cách làm: Whisper (faster-whisper large-v3-turbo trên GPU) nghe từng mẩu từ MIN_WORDS từ trở lên, so với lời đã đưa
vào VieNeu (cả hai qua vi_text.normalize: số -> chữ, viết thường, bỏ dấu câu): tỉ lệ lỗi ký tự (CER) quá CER_MAX, hoặc
nghe ra lặp một khúc -> đọc lại (mỗi lần VieNeu ra một bản khác), tối đa RETRIES lần, giữ bản tốt nhất.
Không có GPU / faster-whisper, hay bước này lỗi gì (hết VRAM...) thì giữ nguyên giọng chưa kiểm, không làm hỏng job.

Đo 20/11 (VieNeu v3 Turbo CPU, Thục Đoan + Mỹ Duyên, Whisper large-v3-turbo CPU int8, 76 clip): 54 câu nấu ăn đọc
đúng có CER trung vị 0.022, p90 0.065, cao nhất 0.097 (Whisper nghe giọng Nam nhầm vài chữ: «nêm» -> «nem», «chiên» ->
«yên»); câu bé có từ tiếng Anh VieNeu tự đọc 0.08-0.197 («sun» -> «súng»). Đoạn 33 từ của lỗi 20/11 đọc 3 lần: 2 lần
lặp lại đúng lỗi cũ (CER 0.19 / 0.21, đều bị bắt nhờ cụm lặp), 1 lần đọc đúng (0.063) -> đọc lại là sửa được."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import vi_text
from ingredients import _lev

MODEL = "large-v3-turbo"
MIN_WORDS = 4              # mẩu ngắn hơn («là vui,») Whisper nghe không chắc: bỏ qua
CER_MAX = 0.25             # ~2.5 lần câu đọc đúng tệ nhất (0.097, đo 20/11); dưới mức này coi như Whisper nghe nhầm
RETRIES = 2
Listen = Callable[[str], str]                       # đường dẫn file giọng -> chữ nghe được
Synth = Callable[[list, str], Awaitable[dict]]
_W: dict[str, Any] = {}


def norm(text: str) -> str:
    """Chữ để so: số -> chữ như lúc đưa vào VieNeu, viết thường, bỏ dấu câu (giữ dấu thanh)."""
    t = unicodedata.normalize("NFC", vi_text.normalize(text)).lower()
    return " ".join(re.sub(r"[^\w\s]|_", " ", t).split())


def _grams(s: str, n: int = 3) -> Counter:
    w = s.split()
    return Counter(tuple(w[i:i + n]) for i in range(len(w) - n + 1))


def score(ref: str, hyp: str) -> tuple[float, bool]:
    """(CER, có phải đọc hỏng không) giữa lời (ref) và chữ Whisper nghe được (hyp), cả hai đã qua norm(). Hỏng: CER quá
    CER_MAX (nghe ra dài hơn lời 25% là CER đã quá), hoặc một cụm 3 từ nghe lặp nhiều lần hơn trong lời: lặp một khúc
    trong câu dài chỉ làm CER lên 0.17-0.21 (3 clip lặp «Bé nói theo nè, súng», đo 20/11), lẫn với câu đúng có tiếng
    Anh; cụm lặp thì không clip đọc đúng nào có (0/74)."""
    cer = _lev(ref, hyp) / max(1, len(ref))
    want = _grams(ref)
    rep = any(c > 1 and c > want[g] for g, c in _grams(hyp).items())
    return round(cer, 3), cer > CER_MAX or rep


def _whisper() -> Any:
    """Model nghe lại, chỉ khi có GPU CUDA (CPU Kaggle: large-v3-turbo quá chậm -> bỏ kiểm). Không có thì None."""
    if "m" not in _W:
        _W["m"] = None
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                import asr_local
                asr_local.preload_cuda()
                from faster_whisper import WhisperModel
                _W["m"] = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        except Exception as e:
            logging.warning("Không nạp được Whisper nghe lại giọng (%s): bỏ bước kiểm giọng", str(e)[:200])
    return _W["m"]


def listen(path: str) -> str:
    """Chữ Whisper nghe được (giải mã bằng ffmpeg như asr_local: PyAV của faster-whisper hay lệch phiên bản)."""
    import asr_local
    segs, _ = _W["m"].transcribe(asr_local._load(path), language="vi", beam_size=5, temperature=0.0,
                                 condition_on_previous_text=False, without_timestamps=True)
    return " ".join(s.text.strip() for s in segs)


def checked(synth: Synth, hear: Listen | None = None) -> Synth:
    """Bọc synth của VieNeu: đọc xong thì nghe lại, đọc lại mẩu hỏng. Thống kê ở thuộc tính .qa của hàm trả về:
    {checked, redo, worst_cer, secs, bad: [(khoá, CER, chữ nghe được)]} — bad là mẩu vẫn hỏng sau RETRIES lần."""
    qa: dict[str, Any] = {"checked": 0, "redo": 0, "worst_cer": 0.0, "secs": 0.0, "bad": []}

    async def run(items: list, tmp: str) -> dict:
        out = await synth(items, tmp)
        t0 = time.time()
        try:
            await _check(synth, items, tmp, out, hear, qa)
        except Exception as e:                 # hết VRAM, Whisper lỗi...: giữ giọng (đã đọc lại được bản nào thì giữ bản đó)
            logging.warning("Nghe lại giọng lỗi (%s): giữ giọng chưa kiểm", str(e)[:200])
            qa["error"] = str(e)[:200]
        qa["secs"] = round(qa["secs"] + time.time() - t0, 1)
        return out
    run.qa = qa                                # type: ignore[attr-defined]
    return run


async def _check(synth: Synth, items: list, tmp: str, out: dict, hear: Listen | None, qa: dict) -> None:
    refs = {it[0]: norm(it[1]) for it in items if (out.get(it[0]) or {}).get("path")}
    refs = {k: r for k, r in refs.items() if len(r.split()) >= MIN_WORDS}
    if not refs:
        return
    if hear is None:
        if await asyncio.to_thread(_whisper) is None:
            return
        hear = listen

    async def rate(k: Any, path: str) -> tuple[float, bool, str]:
        hyp = norm(await asyncio.to_thread(hear, path))
        return (*score(refs[k], hyp), hyp)
    best = {k: await rate(k, out[k]["path"]) for k in refs}
    qa["checked"] += len(refs)
    todo = [k for k in refs if best[k][1]]
    for n in range(1, RETRIES + 1):
        if not todo:
            break
        sub = os.path.join(tmp, f"qa{n}")      # file mới không đè bản cũ (VieNeu ghi v_<khoá>.wav)
        os.makedirs(sub, exist_ok=True)
        logging.info("Nghe lại giọng: đọc lại %d mẩu (lần %d): %s", len(todo), n,
                     "; ".join(f"«{refs[k]}» -> «{best[k][2]}»" for k in todo)[:600])
        again = await synth([it for it in items if it[0] in todo], sub)
        qa["redo"] += len(todo)
        for k in todo:
            if (again.get(k) or {}).get("path"):
                r = await rate(k, again[k]["path"])
                if (r[1], r[0]) < (best[k][1], best[k][0]):     # bản không hỏng trước, rồi tới CER thấp hơn
                    out[k], best[k] = again[k], r
        todo = [k for k in todo if best[k][1]]
    qa["worst_cer"] = max(qa["worst_cer"], *(b[0] for b in best.values()))
    qa["bad"] += [(k, best[k][0], best[k][2]) for k in todo]


def report(synth: Any) -> tuple[dict, list[str]]:
    """({"voice_qa": thống kê} | {}, câu báo các mẩu vẫn đọc hỏng) của synth đã bọc checked(); synth khác: ({}, [])."""
    qa = getattr(synth, "qa", None)
    if not qa or not (qa["checked"] or qa.get("error")):
        return {}, []
    stats = {k: qa[k] for k in ("checked", "redo", "worst_cer", "secs", "error") if k in qa}
    return {"voice_qa": stats}, [f"Giọng VieNeu đọc lại {RETRIES} lần vẫn chưa đúng lời (CER {c:.2f}), nghe ra: «{h[:120]}»"
                                 for _, c, h in qa["bad"]]
