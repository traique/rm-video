"""Tách lời kể / lời thoại để đọc bằng giọng riêng từng nhân vật (phản hồi 19/11: một giọng đọc cả lời kể lẫn lời nhân vật).

Lời thoại nằm trong «…» (hay "…", “…”); người nói là tên nhân vật đứng ngay trước ngoặc («Gấu Nâu nói: «…»», «Gấu Nâu cười:
«…»») hoặc ngay sau ngoặc («…» Sóc Nhỏ reo lên). Không nhận ra ai nói thì người kể đọc luôn. Mỗi cảnh -> danh sách
[(khoá nhân vật | "narrator", lời)]; Kaggle đọc từng đoạn bằng giọng VieNeu của nhân vật (data.json «voice») rồi nối lại
thành một clip của cảnh (chèn 120 ms lặng) — phần dựng không đổi."""
from __future__ import annotations

import re
from collections.abc import Sequence

QUOTE_RE = re.compile(r"[«\"“]([^«»\"“”]{2,}?)[»\"”]")
SPEECH_VERBS = ("nói", "bảo", "hỏi", "reo", "kêu", "cười", "gọi", "la", "thì thầm", "đáp", "lẩm bẩm", "hét", "mếu", "khóc",
                "thở dài", "gật đầu", "lắc đầu", "trả lời", "an ủi", "dặn", "nhắc", "hỏi lại")
GAP = 0.12


def _who(fragment: str, chars: dict) -> str | None:
    """Khoá nhân vật được nhắc tên trong đoạn chữ (lấy tên xuất hiện sau cùng)."""
    low = fragment.lower()
    best, pos = None, -1
    for key, c in chars.items():
        name = c["name_vi"].lower()
        i = low.rfind(name)
        if i > pos:
            best, pos = key, i
    return best


def split(text: str, chars: dict, main: str = "") -> list[tuple[str, str]]:   # main: giữ cho tương thích
    """[(speaker, lời)] theo thứ tự đọc. speaker = khoá nhân vật hoặc "narrator". Đoạn liền nhau cùng người nói gộp lại."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in QUOTE_RE.finditer(text):
        before = text[pos:m.start()]
        quote = m.group(1).strip()
        # người nói, theo thứ tự tin cậy: tên + động từ nói NGAY SAU ngoặc («…» Sóc Nhỏ reo) -> tên trong câu dẫn ngay trước
        # ngoặc (Gấu Nâu nói: «…») -> tên trong câu liền trước (Thỏ Trắng hoảng hốt. «…»)
        speaker = None
        sents = [x for x in re.split(r"[.!?…]\s+", before) if x.strip()]
        if re.search(r":\s*$", before):                           # câu dẫn rõ: «Gấu Nâu nói: «…»»
            speaker = _who(sents[-1], chars) if sents else None
        if speaker is None:
            after = text[m.end():m.end() + 60]
            tail = re.split(r"[.!?…«\"“]", after)[0]
            if ":" not in tail and any(v in tail.lower() for v in SPEECH_VERBS):   # «…» Sóc Nhỏ reo lên (không phải dẫn câu sau)
                speaker = _who(tail, chars)
        if speaker is None and sents:
            lead = sents[-1] if not re.search(r"[.!?…]\s*$", before) else ""
            speaker = _who(lead, chars) if lead.strip() else _who(sents[-1], chars)
        if before.strip():
            out.append(("narrator", before.strip()))
        out.append((speaker or "narrator", quote))
        pos = m.end()
    rest = text[pos:].strip()
    if rest:
        out.append(("narrator", rest))
    merged: list[tuple[str, str]] = []
    for who, t in out:
        t = t.strip()
        if not t:
            continue
        if merged and merged[-1][0] == who:
            merged[-1] = (who, merged[-1][1] + " " + t)
        else:
            merged.append((who, t))
    return merged or [("narrator", text.strip())]


def voice_for(speaker: str, chars: dict, narrator: str) -> str:
    """Tên giọng VieNeu cho người nói: nhân vật có «voice» trong data.json; không thì giọng người kể."""
    if speaker == "narrator":
        return narrator
    v = (chars.get(speaker) or {}).get("voice") or ""
    return str(v) if v and v != narrator else narrator


RMS_TARGET = -20.0     # dBFS: mỗi đoạn giọng kéo về mức này trước khi nối (giọng VieNeu to nhỏ khác nhau, phản hồi 19/11)
GAIN_MAX = 12.0


async def rms_db(path: str) -> float | None:
    """Mức RMS (dBFS) của một file tiếng (ffmpeg astats); không đo được thì None."""
    import re
    from rmvideo.media import ff
    try:
        _code, err = await ff._run_ffmpeg_full("-i", path, "-af", "astats=measure_perchannel=none:measure_overall=RMS_level",
                                                "-f", "null", "-", stderr_keep=20000)
    except Exception:
        return None
    m = re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", str(err))
    if not m or m[-1] == "-inf":
        return None
    return float(m[-1])


async def merge(paths: Sequence[str], out: str, gap: float = GAP, target_db: float = RMS_TARGET) -> str:
    """Nối các đoạn giọng của một cảnh thành một wav (chèn `gap` giây lặng sau mỗi đoạn); mỗi đoạn được kéo về cùng mức
    RMS `target_db` (±GAIN_MAX dB) để người kể và các bạn nghe đều nhau."""
    from rmvideo.media import ff
    paths = [p for p in paths if p]
    if len(paths) == 1 and gap <= 0 and target_db is None:
        return paths[0]
    args: list[str] = ["-y"]
    gains: list[float] = []
    for p in paths:
        args += ["-i", p]
        lvl = await rms_db(p) if target_db is not None else None
        gains.append(max(-GAIN_MAX, min(GAIN_MAX, target_db - lvl)) if lvl is not None else 0.0)
    chain = ";".join(f"[{i}:a]aformat=sample_rates=24000:channel_layouts=mono,volume={gains[i]:+.2f}dB,"
                     f"apad=pad_dur={gap:.3f}[s{i}]" for i in range(len(paths)))
    fc = chain + ";" + "".join(f"[s{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[a]"
    await ff._run_ffmpeg(*args, "-filter_complex", fc, "-map", "[a]", "-ar", "24000", "-ac", "1", out)
    return out
