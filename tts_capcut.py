"""Giọng CapCut/TikTok (ByteDance) qua gói capcut_tts_api (MIT, đóng kèm thư mục capcut_tts_api/).
API không chính thức, không cần đăng nhập, có thể đổi bất cứ lúc nào -> luôn có fallback edge-tts.
Mốc từng từ cho phụ đề lấy từ STT của chính CapCut trên file giọng vừa tạo."""
import asyncio
import json
import logging

from capcut_tts_api import CapCutClient

DEFAULT_FEMALE = "multi_female_yangguangnv_uranus_bigtts"   # "Ban Mai"
DEFAULT_MALE = "multi_male_felipe_uranus_bigtts"           # "Giọng Nam Trầm"


def list_voices() -> list[dict]:
    """Danh mục giọng vi-VN (đọc từ file local, không gọi mạng)."""
    try:
        return [{"id": v.voice_type, "name": v.display_name}
                for v in CapCutClient(timeout=30).list_voices(lang="vi-VN")]
    except Exception:
        logging.exception("Không đọc được danh mục giọng CapCut")
        return []


def _words_sync(audio_path: str) -> list:
    """Mốc từng từ (STT CapCut) của một file giọng bất kỳ."""
    c = CapCutClient(timeout=60)
    stt = c.transcribe_file(audio_path, language="vi-VN", timeout=180)
    task = (stt.get("data", {}).get("tasks") or [{}])[0]
    payload = task.get("payload") or "{}"
    if isinstance(payload, str):
        payload = json.loads(payload)
    words = []
    for utt in payload.get("utterances", []):
        for w in utt.get("words", []):
            tx = (w.get("text") or "").strip()
            if tx:
                words.append((w["start_time"] / 1000.0, w["end_time"] / 1000.0, tx))
    return words


async def words_of(audio_path: str, script: str) -> list:
    return _align(script, await asyncio.to_thread(_words_sync, audio_path))


def _speak_sync(text: str, voice: str, mp3_path: str, want_words: bool):
    c = CapCutClient(timeout=60)
    result = c.generate_speech(text, voice=voice, timeout=180)
    c.download_tts_audio(result, mp3_path)
    if not want_words:
        return []
    stt = c.transcribe_file(mp3_path, language="vi-VN", timeout=180)
    task = (stt.get("data", {}).get("tasks") or [{}])[0]
    payload = task.get("payload") or "{}"
    if isinstance(payload, str):
        payload = json.loads(payload)
    words = []
    for utt in payload.get("utterances", []):
        for w in utt.get("words", []):
            t = (w.get("text") or "").strip()
            if t:
                words.append((w["start_time"] / 1000.0, w["end_time"] / 1000.0, t))
    return _align(text, words)


def _align(script: str, stt_words: list) -> list:
    """Dùng chữ của kịch bản (đúng hoa/thường, dấu câu) với mốc thời gian của STT khi số từ khớp;
    lệch thì dùng luôn chữ STT để phụ đề không bị trượt."""
    sw = script.split()
    if stt_words and len(sw) == len(stt_words):
        return [(s, e, w) for (s, e, _), w in zip(stt_words, sw)]
    if stt_words and abs(len(sw) - len(stt_words)) <= 2:
        n = min(len(sw), len(stt_words))
        return [(stt_words[i][0], stt_words[i][1], sw[i]) for i in range(n)]
    return stt_words


async def speak(text: str, voice: str, mp3_path: str, want_words: bool) -> list:
    """Trả về list (start_s, end_s, word); rỗng nếu không cần/không lấy được mốc."""
    return await asyncio.to_thread(_speak_sync, text, voice, mp3_path, want_words)
