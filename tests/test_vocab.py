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


def test_groq_prompt_has_no_label_to_echo(monkeypatch):
    # lỗi thật: Whisper chép nhãn "Từ khóa" của prompt thành phụ đề ở đoạn chỉ có nhạc
    monkeypatch.setattr(config, "ASR_VOCAB", "bò lá lốt")
    p = vocab.groq_prompt("Cách làm bò lá lốt", "")
    assert "Từ khóa" not in p and "bò lá lốt" in p


def test_groq_drops_hallucinated_segments(monkeypatch):
    import transcribe

    async def fake_post(path, prompt):
        return 200, {"text": "Từ khóa xin chào cả nhà Ghiền Mì Gõ",
                     "segments": [{"start": 0, "end": 0.4, "text": "Từ khóa", "no_speech_prob": 0.1, "avg_logprob": -0.3},
                                  {"start": 0.5, "end": 2.0, "text": "xin chào cả nhà", "no_speech_prob": 0.02,
                                   "avg_logprob": -0.2},
                                  {"start": 3.0, "end": 5.0, "text": "nhạc nhạc", "no_speech_prob": 0.9,
                                   "avg_logprob": -1.4},
                                  {"start": 6.0, "end": 8.0, "text": "Hãy subscribe cho kênh Ghiền Mì Gõ",
                                   "no_speech_prob": 0.3, "avg_logprob": -0.5}],
                     "words": [{"start": 0.0, "end": 0.2, "word": "Từ"}, {"start": 0.2, "end": 0.4, "word": "khóa"},
                               {"start": 0.5, "end": 0.9, "word": "xin"}, {"start": 0.9, "end": 1.3, "word": "chào"},
                               {"start": 1.3, "end": 1.6, "word": "cả"}, {"start": 1.6, "end": 2.0, "word": "nhà"},
                               {"start": 3.0, "end": 4.0, "word": "nhạc"}, {"start": 4.0, "end": 5.0, "word": "nhạc"},
                               {"start": 6.0, "end": 8.0, "word": "Ghiền"}]}
    monkeypatch.setattr(transcribe, "_groq_post", fake_post)
    text, segs = asyncio.run(transcribe._groq("a.mp3", ""))
    assert text == "xin chào cả nhà"
    assert [s[2] for s in segs] == ["xin chào cả nhà"]


def test_cached_fake_segments_dropped():
    import transcribe
    segs = [(0.0, 0.4, "Từ khóa"), (0.5, 2.0, "xin chào cả nhà"), (2.1, 3.0, "Vietsub by ABC")]
    assert transcribe.drop_fake_segments(segs) == [(0.5, 2.0, "xin chào cả nhà")]
    # "từ khóa" nằm trong câu nói thật thì giữ
    assert transcribe.drop_fake_segments([(0, 2, "từ khóa hôm nay là bò")]) == [(0, 2, "từ khóa hôm nay là bò")]


def test_merge_sentences_joins_cut_fragments():
    """Botkeep 11/2026: lời gốc bị cắt vụn ("Sau khi mình" | "luộc ... vớt ra" | "Rồi") -> AI viết từng mẩu thành câu
    riêng, đọc rời rạc. Gộp thành câu trọn. Danh sách nguyên liệu khe ~2s/món nói liền: gộp 2 món một câu (từ bản 25,
    khe 2s chỉ đủ 5-6 chữ giọng Mỹ Duyên -> AI viết câu cụt hoặc bị nén)."""
    import prompt_engine
    segs = [(19.9, 21.0, "Cái công dụng làm"), (21.1, 24.6, "cho cái bò mình nó mềm."),
            (24.7, 26.0, "Rồi"), (26.1, 27.7, "để nghỉ 5 phút."),
            (40.0, 41.9, "1 phần 3 muỗng đường,"), (42.0, 43.9, "1 phần 4 muỗng dầu hào,"),
            (50.0, 52.0, "xong lúc này mọi người"), (52.1, 58.5, "cho vào nồi luộc chín đều rồi vớt ra để ráo nước.")]
    out = prompt_engine.merge_sentences(segs, 80)
    assert [t for _, _, t in out] == ["Cái công dụng làm cho cái bò mình nó mềm.", "Rồi để nghỉ 5 phút.",
                                      "1 phần 3 muỗng đường, 1 phần 4 muỗng dầu hào,",
                                      "xong lúc này mọi người",
                                      "cho vào nồi luộc chín đều rồi vớt ra để ráo nước."]   # gộp sẽ dài > 6s
    assert out[0][:2] == (19.9, 24.6)
