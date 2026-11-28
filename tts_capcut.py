"""Giọng CapCut/TikTok (ByteDance) qua gói capcut_tts_api (MIT, đóng kèm thư mục capcut_tts_api/).
API không chính thức, không cần đăng nhập, có thể đổi bất cứ lúc nào -> luôn có fallback edge-tts.
Mốc từng từ cho phụ đề lấy từ STT của chính CapCut trên file giọng vừa tạo."""
import asyncio
import json
import logging

import align
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


def parse_stt(stt: dict) -> list:
    """Kết quả STT CapCut -> [(start_s, end_s, text)]: mốc từng từ; câu nào không có mốc từ thì lấy cả câu.
    (Trước đây đoạn đọc kết quả này bị chép ở 3 nơi.)"""
    task = ((stt or {}).get("data", {}).get("tasks") or [{}])[0]
    payload = task.get("payload") or "{}"
    if isinstance(payload, str):
        payload = json.loads(payload)
    units = []
    for utt in payload.get("utterances", []):
        ws = utt.get("words") or []
        if ws:
            for w in ws:
                tx = (w.get("text") or "").strip()
                if tx:
                    units.append((w["start_time"] / 1000.0, w["end_time"] / 1000.0, tx))
        elif (utt.get("text") or "").strip():
            units.append((utt["start_time"] / 1000.0, utt["end_time"] / 1000.0, utt["text"].strip()))
    return units


def stt_words(audio_path: str, client: CapCutClient | None = None) -> list:
    """Mốc từng từ (STT CapCut, không cần key) của một file âm thanh bất kỳ. Hàm đồng bộ: gọi qua to_thread."""
    c = client or CapCutClient(timeout=60)
    return parse_stt(c.transcribe_file(audio_path, language="vi-VN", timeout=180))


def _speak_sync(text: str, voice: str, mp3_path: str, want_words: bool):
    c = CapCutClient(timeout=60)
    result = c.generate_speech(text, voice=voice, timeout=180)
    c.download_tts_audio(result, mp3_path)
    if not want_words:
        return []
    words = stt_words(mp3_path, c)
    if not words:
        return []
    return align.map_script(text, words, words[0][0], words[-1][1])


async def speak(text: str, voice: str, mp3_path: str, want_words: bool) -> list:
    """Trả về list (start_s, end_s, word); rỗng nếu không cần/không lấy được mốc."""
    return await asyncio.to_thread(_speak_sync, text, voice, mp3_path, want_words)
