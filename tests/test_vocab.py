"""Từ điển bóc lời (vocab.py): prompt cho Groq, sửa lỗi chính tả máy nghe một cách thận trọng."""
import asyncio

import config
import vocab


def test_groq_prompt_has_title_terms_and_fits(monkeypatch):
    monkeypatch.setattr(config, "ASR_VOCAB", "bò lá lốt, Bếp Nhà Tui, chả cá Lã Vọng")
    p = vocab.groq_prompt("Cách làm BÒ LÁ LỐT thơm lừng 🔥 #nauan #fyp https://x.y/z", "Nguyên liệu: thịt bò, lá lốt, mỡ hành")
    assert "#" not in p and "http" not in p and "🔥" not in p
    assert "Bếp Nhà Tui" in p and "thịt bò" in p
    assert len(p.encode("utf-8")) <= vocab.PROMPT_MAX_BYTES
    monkeypatch.setattr(config, "ASR_VOCAB", ", ".join(f"món số {i} đặc biệt" for i in range(200)))
    assert len(vocab.groq_prompt("x" * 500).encode("utf-8")) <= vocab.PROMPT_MAX_BYTES


def test_fix_user_term_near_miss(monkeypatch):
    monkeypatch.setattr(config, "ASR_VOCAB", "bò lá lốt, Bếp Nhà Tui")
    assert vocab.fix_text("cuốn bò la lốt thật chặt,") == "cuốn bò lá lốt thật chặt,"
    assert vocab.fix_text("Chào bà con, bếp nhà tui đây") == "Chào bà con, Bếp Nhà Tui đây"
    # dấu câu giữa cụm: hai ý khác nhau, không ghép
    assert vocab.fix_text("thêm bò, la lốt") == "thêm bò, la lốt"


def test_fix_ingredient_three_syllables_only(monkeypatch):
    monkeypatch.setattr(config, "ASR_VOCAB", "")
    assert vocab.fix_text("đổ nước cốt dưa vào") == "đổ nước cốt dừa vào"
    # cụm 2 âm tiết KHÔNG tự sửa (đâu đó / đậu đỏ, ba chị / ba chỉ đều là câu thật)
    assert vocab.fix_text("để đâu đó cho ba chị em") == "để đâu đó cho ba chị em"
    # lệch quá 1 âm tiết với tên nguyên liệu 3 âm tiết: không sửa
    assert vocab.fix_text("nuốc cót dưa") == "nuốc cót dưa"


def test_prompt_hint_only_with_user_vocab(monkeypatch):
    import prompt_engine
    monkeypatch.setattr(config, "ASR_VOCAB", "")
    assert vocab.prompt_hint() == ""
    monkeypatch.setattr(config, "ASR_VOCAB", "chè khúc bạch")
    assert "chè khúc bạch" in prompt_engine.style_block()


def test_transcribe_passes_prompt_and_fixes_cached(monkeypatch, tmp_path):
    import transcribe
    monkeypatch.setattr(config, "ASR_VOCAB", "bò lá lốt")
    monkeypatch.setattr(config, "ASR_ORDER", "capcut")
    monkeypatch.setattr(transcribe, "_CACHE", str(tmp_path / "stt.json"))
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x" * 100)
    seen = []

    async def fake(name, path, prompt=""):
        seen.append(prompt)
        return "cuốn bò la lốt", [(0.0, 1.0, "cuốn bò la lốt")]
    monkeypatch.setattr(transcribe, "_run_provider", fake)
    out = asyncio.run(transcribe.transcribe_segments(str(audio), "Bò lá lốt", ""))
    assert out == ("cuốn bò lá lốt", [(0.0, 1.0, "cuốn bò lá lốt")])
    assert "bò lá lốt" in seen[0]
    out2 = asyncio.run(transcribe.transcribe_segments(str(audio), "Bò lá lốt", ""))   # từ cache, vẫn được sửa
    assert out2 == out and len(seen) == 1
    # lượt render bản đã duyệt: tiêu đề khác (file giữ lại) nhưng vẫn phải dùng đúng lời đã nghe, không nghe lại
    assert asyncio.run(transcribe.transcribe_segments(str(audio), "", "")) == out and len(seen) == 1


def test_groq_retries_without_prompt(monkeypatch):
    import transcribe
    calls = []

    async def fake_post(path, prompt):
        calls.append(prompt)
        if prompt:
            return 500, "internal"
        return 200, {"text": "xin chào", "words": [{"start": 0, "end": 0.5, "word": "xin"},
                                                  {"start": 0.5, "end": 1.0, "word": "chào"}]}
    monkeypatch.setattr(transcribe, "_groq_post", fake_post)
    text, segs = asyncio.run(transcribe._groq("a.mp3", "Từ khóa: x."))
    assert text == "xin chào" and calls == ["Từ khóa: x.", ""]
