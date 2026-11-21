"""Xếp giọng mới vào khe từng đoạn (tốc độ chung, mượn khoảng lặng, nói nhanh có giới hạn), ghép track giọng, đo độ dài phần có tiếng, mốc từng từ cho phụ đề.

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import asyncio
import logging

import align
import config
from rmvideo.media import ff, proc


def speech_windows(speech: list, total: float, pad: float = 0.3, merge: float = 0.8) -> list:
    """Khoảng người gốc nói (đệm `pad`, gộp các khoảng cách nhau < `merge`), cắt trong [0, total]."""
    out = []
    for a, b in sorted((max(0.0, a - pad), min(total, b + pad)) for a, b in speech):
        if b <= a:
            continue
        if out and a - out[-1][1] < merge:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


VOICE_MAX_BASE = 1.12      # tốc độ chung tối đa cho cả video (nhanh hơn nghe vội)


VOICE_DEV = 1.05           # mỗi đoạn được nhanh hơn tốc độ chung tối đa 5%: tai gần như không nghe ra


VOICE_LAST = 1.10          # đoạn vẫn tràn sau khi AI đã rút gọn: nhanh hơn tốc độ chung tối đa 10%


VOICE_HARD = 1.25          # chỉ khi đụng mốc cứng (cửa sổ giữ tiếng gốc / hết video): thà nhanh còn hơn đè tiếng


VOICE_SPILL = 0.35         # đoạn dài được lấn sang khe đoạn sau tối thiểu chừng này (đoạn sau lùi theo)


VOICE_SPILL_MAX = 1.0      # ... và tới 1s nếu đoạn sau đọc ngắn, còn dư khe: đoạn sau lùi mà vẫn xong trong khe của nó


def _next_start(segs: list, i: int, total_dur: float) -> float:
    return segs[i + 1]["start"] if i + 1 < len(segs) else total_dur


# lời "gốc" của khúc điểm danh / tóm tắt (style_b.MONTAGE_MARK / RECAP_MARK = prompt_engine.SIDE_MARKS)
SIDE_MARKS = ("Nguyên liệu gồm:", "Tóm lại:")


def _side_next(segs: list, i: int) -> bool:
    """Đoạn sau là khúc điểm danh / tóm tắt: thẻ chữ của khúc đó hiện đúng mốc theo hình, câu trước lấn vào thì lời
    điểm danh trễ thẻ (bản 49, job 7: câu hook lấn 0.46s + "Rồi mình chuẩn bị" -> đọc "mực" khi thẻ đã sang "1/2 muỗng
    bột ngọt"). Không cho lấn, nhưng cũng không ép nói nhanh như mốc cứng (không đè tiếng ai)."""
    return i + 1 < len(segs) and side_orig(segs[i + 1].get("orig"))


def side_orig(orig) -> bool:
    return str(orig or "").lstrip().startswith(SIDE_MARKS)


def voice_base_factor(durs: list, segs: list, total_dur: float) -> float:
    """MỘT tốc độ đọc cho cả video: phân vị 80% của tốc độ từng đoạn cần để vừa khe (cho lấn nhẹ 0.35s), kẹp trong
    [1.0, VOICE_MAX_BASE]. Trước đây mỗi đoạn co giãn riêng 0.92x-1.35x -> nghe lúc nhanh lúc chậm (Botkeep 11/2026)."""
    need = []
    for i, (d, s) in enumerate(zip(durs, segs)):
        if s.get("ost") == "orig" or d <= 0:
            continue
        nxt = _next_start(segs, i, total_dur)
        hard = i + 1 >= len(segs) or segs[i + 1].get("ost") == "orig" or _side_next(segs, i)
        need.append(d / max(0.5, nxt - s["start"] + (0.0 if hard else 0.35)))
    if not need:
        return 1.0
    need.sort()
    return round(min(VOICE_MAX_BASE, max(1.0, need[int(0.8 * (len(need) - 1))])), 3)


def _spill(durs: list, segs: list, i: int, base: float, total_dur: float) -> float:
    """Đoạn i được lấn sang khe đoạn i+1 bao nhiêu giây: phần khe còn dư của đoạn i+1 (đọc ở tốc độ chung), trong
    [VOICE_SPILL, VOICE_SPILL_MAX]."""
    j = i + 1
    free = _next_start(segs, j, total_dur) - segs[j]["start"] - max(0.0, durs[j]) / base - 0.12
    return min(VOICE_SPILL_MAX, max(VOICE_SPILL, free))


def plan_voice(durs: list, segs: list, total_dur: float):
    """Xếp các đoạn giọng lên trục thời gian. durs: độ dài giọng (tới hết tiếng nói) từng đoạn;
    segs: [{start, slot, span}]. Cả video đọc cùng MỘT tốc độ (voice_base_factor), không bao giờ làm chậm. Đoạn dài
    hơn khe, theo thứ tự: (1) bắt đầu sớm hơn tối đa 0.5s nếu đoạn trước đã xong, (2) lấn sang khe sau (đoạn sau lùi
    theo): tối thiểu 0.35s, tới 1s nếu đoạn sau đọc ngắn còn dư khe, (3) nhanh hơn tốc độ chung tối đa 5%, (4) báo
    "quá dài" để AI viết ngắn lại; vẫn tràn thì nhanh hơn tối đa 10% (25% nếu đụng cửa sổ giữ tiếng gốc hoặc hết
    video). Không cắt cụt chữ. Lấn trước, tăng tốc sau: giọng giữ đều một tốc độ, và đỡ phải bắt AI nén lời (Botkeep
    12/11/2026: 16/46 đoạn bị rút gọn thành câu cụt kiểu "Thêm vào.").
    Trả về (placements [{start, factor, end}], danh sách chỉ số đoạn quá dài)."""
    base = voice_base_factor(durs, segs, total_dur)
    plan, too_long, prev_end = [], [], 0.0
    for i, (d, s) in enumerate(zip(durs, segs)):
        nxt = _next_start(segs, i, total_dur)
        if s.get("ost") == "orig":           # đoạn giữ nguyên tiếng gốc: không đọc, đoạn sau không được mượn lùi vào
            plan.append({"start": round(s["start"], 3), "factor": 1.0, "end": round(s["start"], 3)})
            prev_end = max(prev_end, nxt - 0.12)
            continue
        if d <= 0:                           # đoạn không có tiếng (TTS rỗng): giữ chỗ, không chia cho 0
            plan.append({"start": round(max(s["start"], prev_end), 3), "factor": base,
                         "end": round(max(s["start"], prev_end), 3)})
            continue
        last = i + 1 >= len(segs)
        hard = last or segs[i + 1].get("ost") == "orig"   # không lấn sang đoạn tiếng gốc / quá hết video
        pin = _side_next(segs, i)                          # ... hay khúc điểm danh / tóm tắt
        start = max(s["start"], prev_end + 0.12)
        f = base
        if start + d / f > nxt:                     # (1) mượn khoảng lặng phía trước
            start = max(prev_end + 0.12, s["start"] - 0.5, min(start, nxt - d / f))
        limit = total_dur - 0.05 if last else nxt + (0.0 if hard or pin else _spill(durs, segs, i, base, total_dur))
        if start + d / f > limit:                   # (2) đã lấn hết phần cho phép -> (3) nhanh hơn tốc độ chung chút
            f = max(base, min(d / max(0.3, limit - start), base * VOICE_DEV))   # không bao giờ chậm hơn tốc độ chung
        if start + d / f > limit:                   # (4) vẫn tràn quá giới hạn lấn
            too_long.append(i)
            f = max(f, min(d / max(0.3, limit - start), VOICE_HARD if hard else base * VOICE_LAST))
        end = start + d / f
        plan.append({"start": round(start, 3), "factor": round(f, 4), "end": round(end, 3)})
        prev_end = end
    return plan, too_long


async def compose_voice(clips: list, plan: list, out_wav: str, total_dur: float):
    """Ghép giọng theo plan. Chỉ cắt ở cuối video (đoạn cuối), có fade để không cụt gắt.
    Đoạn giữ tiếng gốc (không có file giọng) được bỏ qua; không còn đoạn nào thì ra track im lặng."""
    pairs = [(cl, p) for cl, p in zip(clips, plan) if cl.get("path")]
    if not pairs:
        await ff._run_ffmpeg("-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{total_dur:.2f}", out_wav)
        return
    inputs, filters, labels = [], [], []
    for i, (cl, p) in enumerate(pairs):
        inputs += ["-i", cl["path"]]
        trim = max(0.3, total_dur - p["start"])
        lead = float(cl.get("lead") or 0.0)      # khoảng lặng đầu mẩu đã đo (speech_span): cắt, lời vào đúng nhịp
        cut = f"atrim=start={lead:.3f},asetpts=PTS-STARTPTS," if lead >= 0.01 else ""
        filters.append(
            f"[{i}:a]aformat=sample_rates=44100:channel_layouts=mono,{cut}atempo={p['factor']:.4f},"
            f"atrim=0:{trim:.2f},adelay={int(p['start'] * 1000)}[c{i}]")
        labels.append(f"[c{i}]")
    fc = ";".join(filters) + ";" + "".join(labels) + \
        f"amix=inputs={len(pairs)}:normalize=0,apad=whole_dur={total_dur:.2f}," \
        f"afade=t=out:st={max(0.0, total_dur - 0.25):.2f}:d=0.25[a]"
    await ff._run_ffmpeg("-y", *inputs, "-filter_complex", fc, "-map", "[a]",
                      "-ar", "44100", "-ac", "1", "-t", f"{total_dur:.2f}", out_wav)


def _map_script(script: str, timed: list, s: float, e: float, clip: int) -> list:
    """Chữ kịch bản + mốc đo được của MỘT đoạn giọng -> [(start, end, word, clip)] (xem align.map_script)."""
    return [(a, b, w, clip) for a, b, w in align.map_script(script, timed, s, e)]


VOICED_SR = 16000


def _voiced_and_total_sync(path: str) -> tuple:
    """(khoảng có tiếng, tổng độ dài giây) của cả file giọng — MỘT lần giải mã 16kHz mono qua ffmpeg."""
    import numpy as np
    raw = proc.run_sync(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(VOICED_SR), "-f", "s16le", "-"],
                        capture_output=True, timeout=120).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    return align.voiced_intervals(x, VOICED_SR), len(x) / VOICED_SR


def _voiced_intervals_sync(path: str) -> list:
    """Khoảng có tiếng của cả file giọng (giải mã 16kHz mono qua ffmpeg)."""
    return _voiced_and_total_sync(path)[0]


async def voiced_intervals(path: str) -> list | None:
    """Như _voiced_intervals_sync, lỗi thì None (phụ đề rơi về rải đều)."""
    try:
        return await asyncio.to_thread(_voiced_intervals_sync, path)
    except Exception as e:
        logging.warning("Không đo được khoảng có tiếng của giọng (%s), rải phụ đề đều", str(e)[:100])
        return None


async def words_for_track(clips: list, plan: list, track_path: str, texts: list) -> list:
    """Mốc từng từ cho phụ đề, chữ luôn lấy nguyên văn kịch bản (texts[i] của đoạn i).
    Đoạn đọc bằng edge-tts có mốc từng từ thật. Đoạn CapCut/Piper (không có mốc): mặc định rải chữ trên các
    khoảng CÓ TIẾNG của chính track giọng (align.spread_voiced: đo lệch trung bình ~70ms, không gọi mạng);
    SUB_TIMING=stt thì nghe lại cả track bằng STT CapCut như bản cũ (lệch ~85ms, chậm 7-11 giây)."""
    per_clip = [[] for _ in clips]
    for i, (cl, p) in enumerate(zip(clips, plan)):
        if cl.get("words"):
            ld = float(cl.get("lead") or 0.0)
            per_clip[i] = [(p["start"] + max(0.0, a - ld) / p["factor"], p["start"] + max(0.0, b - ld) / p["factor"], w)
                           for a, b, w in cl["words"]]
    missing = {i for i, cl in enumerate(clips) if cl.get("words") is None}
    if missing and config.SUB_TIMING == "stt":
        try:
            import tts_capcut
            heard = await asyncio.to_thread(tts_capcut.stt_words, track_path)
        except Exception as ex:
            logging.warning("Không nghe lại được track, rải phụ đề theo âm tiết: %s", str(ex)[:100])
            heard = []
        for a, b, w in heard:
            mid = (a + b) / 2
            for i, p in enumerate(plan):
                if p["start"] - 0.15 <= mid <= p["end"] + 0.15:
                    if i in missing:
                        per_clip[i].append((a, b, w))
                    break
    todo = [i for i in missing if not per_clip[i] and texts[i].strip()]
    ivs = await voiced_intervals(track_path) if todo else None
    out = []
    for i, p in enumerate(plan):
        if i in todo and ivs:
            out += [(a, b, w, i) for a, b, w in align.spread_voiced(texts[i].split(), ivs, p["start"], p["end"] + 0.05)]
        else:
            out += _map_script(texts[i], per_clip[i], p["start"], p["end"], i)
    return out


SPEECH_TAIL = 0.15
# 1.2.0 (cách FreeVideo khớp lời lồng tiếng, dubbing/align.py): bỏ khoảng lặng ĐẦU mẩu TTS, chừa LEAD_KEEP giây cho phụ âm
# nhẹ. Mẩu VieNeu đo 21/11 mở đầu bằng ~0,09-0,11s im lặng: lời vào trễ nhịp hình và bị tính vào độ dài -> nói nhanh hơn
# cần. Không cắt quá LEAD_MAX (mẩu mở bằng tiếng thở dài / nhạc hiệu thì đo sai, thà giữ nguyên).
LEAD_KEEP = 0.05
LEAD_MAX = 0.5


async def speech_span(path: str, words=None) -> tuple[float, float]:
    """(lead, độ dài) của một đoạn giọng TTS: lead = khoảng lặng đầu cắt được (giữ LEAD_KEEP), độ dài = từ sau lead tới hết
    tiếng nói + SPEECH_TAIL. compose_voice cắt đúng `lead` (clip["lead"]) trước khi co giãn."""
    if words:
        first = min(float(w[0]) for w in words)
        lead = min(LEAD_MAX, max(0.0, first - LEAD_KEEP))
        return round(lead, 3), max(0.0, await speech_duration(path, words) - lead)
    try:
        ivs, decoded = await asyncio.to_thread(_voiced_and_total_sync, path)
    except Exception as e:
        logging.warning("Không đo được khoảng có tiếng của giọng (%s)", str(e)[:100])
        return 0.0, await speech_duration(path, words)
    if not ivs:
        return 0.0, await speech_duration(path, words)
    lead = min(LEAD_MAX, max(0.0, ivs[0][0] - LEAD_KEEP))
    end = ivs[-1][1]
    dur = end + SPEECH_TAIL if end + SPEECH_TAIL <= decoded else min(await ff.probe_duration(path), end + SPEECH_TAIL)
    return round(lead, 3), max(0.0, dur - lead)


async def speech_duration(path: str, words=None) -> float:
    """Độ dài tới hết TIẾNG NÓI của một đoạn giọng TTS (bỏ khoảng lặng cuối file). edge-tts để ~0.78s lặng ở cuối mọi
    file (đo 28 câu, cả NamMinh lẫn HoaiMy): tính cả vào thì khe ngắn nào cũng "quá dài" — Botkeep: 13/14 đoạn bị AI
    rút gọn rồi đọc lại vô ích. words: mốc từ của edge-tts; không có (CapCut/Piper) thì đo khoảng có tiếng."""
    if words:
        total = await ff.probe_duration(path)
        end = max(float(w[1]) for w in words)
    else:
        # CapCut / Piper / VieNeu: đằng nào cũng phải giải mã để tìm chỗ hết tiếng. Hết tiếng + đuôi nằm gọn trong độ
        # dài giải mã được (gần như mọi đoạn) thì kết quả chắc chắn là end + đuôi (độ dài container ffprobe báo luôn
        # >= độ dài giải mã: có thêm phần đệm của bộ mã hoá) -> khỏi chạy ffprobe; còn lại hỏi ffprobe như cũ.
        try:
            ivs, decoded = await asyncio.to_thread(_voiced_and_total_sync, path)
        except Exception as e:
            logging.warning("Không đo được khoảng có tiếng của giọng (%s), rải phụ đề đều", str(e)[:100])
            ivs, decoded = None, 0.0
        end = ivs[-1][1] if ivs else 0.0
        if end > 0 and end + SPEECH_TAIL <= decoded:
            return end + SPEECH_TAIL
        total = await ff.probe_duration(path)
    if end <= 0:
        return total
    return min(total, end + SPEECH_TAIL)
