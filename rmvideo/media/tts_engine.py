"""Đọc giọng: edge-tts (mốc từng từ) / CapCut / Piper offline, dự phòng chéo đúng giới tính, cache theo chữ + giọng + tốc độ.

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import asyncio
import logging
import os
import random
import shutil

import edge_tts

import align
import cache
import config
import metrics
import subs as subs_mod
from rmvideo.domain import voices
from rmvideo.media import voiceplan

_EDGE_VOICES = {config.VOICE_FEMALE, config.VOICE_MALE}


def _vieneu_male(voice: str) -> bool:
    return voices.is_male(voice)


def _capcut_fallback_voice(voice: str) -> str:
    """Giọng CapCut thay khi edge-tts lỗi hẳn. CapCut có liệt kê "Hoai My"/"Nam Minh" nhưng gọi thì lỗi
    TTSInvalidSpeaker (thử 11/2026) -> dùng giọng nữ/nam CapCut trong Cài đặt (mặc định Ban Mai / Giọng Nam Trầm)."""
    male = voice == config.VOICE_MALE or ("male" in voice.lower() and "female" not in voice.lower()) or _vieneu_male(voice)
    return config.CAPCUT_VOICE_MALE if male else config.CAPCUT_VOICE_FEMALE


def _edge_fallback_voice(voice: str) -> str:
    """Giọng edge thay thế: VieNeu (chạy trên Kaggle) lỗi / chưa cấu hình -> Hoài My hoặc Nam Minh đúng giới tính."""
    if voice in _EDGE_VOICES:
        return voice
    if (voice or "").startswith("vieneu:"):
        return config.VOICE_MALE if _vieneu_male(voice) else config.VOICE_FEMALE
    return config.VOICE_MALE if ("male" in voice and "female" not in voice) else config.VOICE_FEMALE


async def tts(text: str, mp3_path: str, voice: str, ass_path: str | None = None,
              pos_y: int | None = None) -> bool:
    """Đọc giọng AI + mốc từng từ cho phụ đề. Giọng CapCut (API không chính thức) đi trước,
    lỗi gì cũng rơi về edge-tts để job không bao giờ hỏng. Trả về True nếu ghi được phụ đề."""
    if config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES:
        try:
            import tts_capcut
            words = await tts_capcut.speak(text, voice, mp3_path, want_words=bool(ass_path) and config.SUB_TIMING == "stt")
            metrics.record("tts", "capcut")
            if ass_path and not words:              # mốc chữ theo khoảng có tiếng của chính file giọng
                ivs = await voiceplan.voiced_intervals(mp3_path)
                if ivs:
                    words = align.spread_voiced(text.split(), ivs, 0.0, ivs[-1][1] + 0.05)
            if ass_path and words:
                subs_mod.write_ass(words, ass_path, pos_y, config.SUB_STYLE)
                return True
            return False
        except Exception as e:
            metrics.record("tts", "capcut", ok=False, detail=str(e))
            logging.warning("Giọng CapCut lỗi (%s), chuyển sang edge-tts", str(e)[:150])
    return await _tts_edge(text, mp3_path, _edge_fallback_voice(voice), ass_path, pos_y)


# edge-tts hay trả NoAudioReceived chập chờn phía Microsoft (đo 11/2026: giọng NamMinh lỗi 0-35% số lần gọi tuỳ lúc,
# ngẫu nhiên, không do câu chữ hay tham số — cùng câu gọi lại là được; HoaiMy hiếm lỗi). 3 lần là không đủ: Botkeep
# từng hỏng cả job vì một đoạn lỗi 3 lần liền.
EDGE_TRIES = 6


async def _edge_once(text: str, mp3_path: str, voice: str) -> list:
    com = edge_tts.Communicate(text, voice, rate=config.TTS_RATE, pitch=config.TTS_PITCH,
                               boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


async def _edge_words(text: str, mp3_path: str, voice: str, fallback: bool = False) -> list:
    """Thử lại tối đa EDGE_TRIES lần, chờ tăng dần + ngẫu nhiên (các đoạn đọc song song không cùng gọi lại một lúc)."""
    for attempt in range(EDGE_TRIES):
        try:
            words = await _edge_once(text, mp3_path, voice)
            metrics.record("tts", "edge", fallback=fallback)
            return words
        except FileNotFoundError:          # thư mục tạm đã bị xoá (job đã dừng): thử lại vô ích
            raise
        except Exception as e:
            if attempt == EDGE_TRIES - 1:
                metrics.record("tts", "edge", ok=False, fallback=fallback, detail=str(e))
                raise
            logging.warning("edge-tts lỗi lần %d/%d (%s), thử lại", attempt + 1, EDGE_TRIES, str(e)[:80])
            await asyncio.sleep(min(5.0, 1.0 + 1.2 * attempt) + random.random())
    return []


async def _tts_edge(text: str, mp3_path: str, voice: str, ass_path: str | None,
                    pos_y: int | None) -> bool:
    words = await _edge_words(text, mp3_path, voice)
    if ass_path and words:
        subs_mod.write_ass(words, ass_path, pos_y, config.SUB_STYLE)
        return True
    return False


async def synth_clip_ex(text: str, voice: str, path: str) -> tuple:
    """TTS 1 đoạn. Trả (mốc từ hoặc None, provider đã đọc: capcut | edge | piper). CapCut/Piper không có mốc từ:
    phụ đề rải theo âm tiết."""
    capcut = config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES
    if capcut:
        try:
            import tts_capcut
            await tts_capcut.speak(text, voice, path, want_words=False)
            metrics.record("tts", "capcut")
            return None, "capcut"
        except Exception as e:
            metrics.record("tts", "capcut", ok=False, detail=str(e))
            logging.warning("CapCut lỗi đoạn (%s), dùng edge-tts", str(e)[:100])
    try:
        return await _edge_words(text, path, _edge_fallback_voice(voice), fallback=capcut), "edge"
    except FileNotFoundError:
        raise
    except Exception as e:
        err = e
    if not capcut:                 # edge là giọng chính mà lỗi hẳn: đọc đoạn này bằng CapCut cùng giới tính, job không hỏng
        try:
            import tts_capcut
            await tts_capcut.speak(text, _capcut_fallback_voice(voice), path, want_words=False)
            metrics.record("tts", "capcut", fallback=True)
            logging.warning("edge-tts lỗi %d lần liền (%s): đoạn «%s» đọc bằng giọng CapCut", EDGE_TRIES,
                            str(err)[:80], text[:40])
            return None, "capcut"
        except Exception as e2:
            metrics.record("tts", "capcut", ok=False, fallback=True, detail=str(e2))
            logging.warning("CapCut dự phòng cũng lỗi (%s)", str(e2)[:100])
    if config.TTS_OFFLINE != "1":
        raise err
    try:                                       # cả hai dịch vụ ngoài đều lỗi: giọng offline Piper (nếu đã cài)
        import offline  # không gửi lên Kaggle (kaggle_bundle.OPTIONAL)
    except ImportError:
        raise err from None
    try:
        await offline.speak(text, path)
        metrics.record("tts", "piper", fallback=True)
        return None, "piper"
    except Exception as e:
        metrics.record("tts", "piper", ok=False, fallback=True, detail=str(e))
        raise


async def synth_clip(text: str, voice: str, path: str):
    """Như synth_clip_ex nhưng chỉ trả mốc từ (giữ tương thích)."""
    return (await synth_clip_ex(text, voice, path))[0]


async def synth_clip_cached(text: str, voice: str, path: str):
    """synth_clip có cache: cùng chữ + giọng + tốc độ + cao độ thì dùng lại file cũ (render lại sau khi duyệt
    chỉ đọc lại các đoạn đã sửa). Chỉ cache khi provider CHÍNH đọc được: đoạn rơi về edge-tts vì CapCut lỗi
    sẽ được đọc lại bằng CapCut ở lần sau, không bị kẹt giọng dự phòng."""
    primary = "capcut" if (config.TTS_PROVIDER == "capcut" and voice not in _EDGE_VOICES) else "edge"
    tune = (config.TTS_RATE, config.TTS_PITCH) if primary == "edge" else ()
    k = cache.key("tts", primary, voice, tune, text)
    hit, meta = cache.get("tts", k, ".mp3"), cache.get_json("tts", k)
    if hit and meta is not None:
        await asyncio.to_thread(shutil.copyfile, hit, path)
        return meta.get("words")
    words, used = await synth_clip_ex(text, voice, path)
    if used == primary and os.path.exists(path) and os.path.getsize(path) > 0:     # Piper/edge dự phòng: không cache
        try:
            cache.put("tts", k, path, ".mp3")
            cache.put_json("tts", k, {"words": words})
        except OSError:
            pass
    return words
