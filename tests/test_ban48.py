"""Bản 48: sổ giọng Bếp Đong Đủ (cặp mẫu trong lệnh viết lời), đổi từ miền Bắc sang miền Nam sau khi AI viết, kho lời
đã duyệt, dòng chỉ số lời đọc mỗi job."""
import asyncio
import json
import os

import config
import prompt_engine
from rmvideo.app import voice_book
from rmvideo.app.flow import common
from rmvideo.domain.dialect import south
from rmvideo.media import ff

KIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brand", "bep_dong_du")


# ---------------- đổi từ miền Bắc ----------------

def test_south_words():
    assert south("Cho 2 thìa nước mắm vào bát.") == "Cho 2 muỗng nước mắm vào chén."
    assert south("Đĩa mực bày lên.") == "Dĩa mực bày lên."
    assert south("Trần qua rau muống rồi vớt ra.") == "Trụng qua rau muống rồi vớt ra."
    assert south("Thêm 2 quả trứng và quả chanh.") == "Thêm 2 trái trứng và trái chanh."
    assert south("Rán vàng đậu phụ.") == "Chiên vàng đậu hũ."
    assert south("Thịt lợn ninh nhừ.") == "Thịt heo hầm nhừ."
    assert south("Rắc vừng và lạc rang.") == "Rắc mè và đậu phộng rang."
    assert south("Đậy vung nồi lại.") == "Đậy nắp nồi lại."
    assert south("Nem rán giòn rụm, chấm xì dầu, bỏ rau mùi với mì chính.") == \
        "Chả giò giòn rụm, chấm nước tương, bỏ ngò với bột ngọt."


def test_south_keeps_other_meanings():
    for t in ("Thìa là thái nhỏ.", "Nhìn lên trần nhà.", "Kết quả rất hiệu quả.", "Lá dứa thơm lừng.",
              "Đặc sản Ninh Bình.", "Bát ngát hương thơm.", "Mùi thơm phức.", "Lạc mất đường về.", ""):
        assert south(t) == t


def test_dialect_applied_before_checks(monkeypatch):
    """AI viết "2 thìa" (gốc "2 muỗng"): đổi thành muỗng, không bị coi là đổi định lượng."""
    seen: list = []
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        return json.dumps({"segments": [{"i": 0, "text": "Giờ mình nêm 2 thìa nước mắm vô bát nước dùng, khuấy đều."}],
                           "title": "Món ngon đĩa đầy", "caption": "c", "hashtags": "#a", "hook": "nước dùng"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", [(0.0, 5.0, "cho vào đây 2 muỗng nước mắm nha")],
                                                   5.0))
    assert out["segments"][0]["text"] == "Giờ mình nêm 2 muỗng nước mắm vô chén nước dùng, khuấy đều."
    assert out["title"] == "Món ngon dĩa đầy" and len(seen) == 1


# ---------------- sổ giọng ----------------

def test_curated_examples_are_well_formed(monkeypatch):
    monkeypatch.setattr(ff, "BRAND_DIR", KIT)
    ex = voice_book.curated()
    assert len(ex) >= 10 and {x["kind"] for x in ex} >= {"hook", "action", "wait", "tip", "plate", "recap"}
    endings = [x["new"].rstrip(".!?").split()[-1].lower() for x in ex]
    assert endings.count("nha") <= 1                                   # mẫu không dạy kết "nha"
    for x in ex:
        assert 8 <= len(x["new"].split()) <= 22, x["new"]               # đủ dài (lời thưa: job 5)
        assert prompt_engine.copy_ratio(x["new"], x["orig"]) < 0.45, x["new"]   # mẫu không chép lời gốc
        assert south(x["new"]) == x["new"]                               # mẫu đã là từ miền Nam


def test_pick_rotates_and_always_has_hook_and_action(monkeypatch):
    monkeypatch.setattr(ff, "BRAND_DIR", KIT)
    a = voice_book.pick("mực luộc", "video-1")
    b = voice_book.pick("mực luộc", "video-2")
    assert len(a) == voice_book.PICK and {"hook", "action"} <= {x["kind"] for x in a}
    assert a == voice_book.pick("mực luộc", "video-1")                  # cùng video: cùng bộ mẫu
    assert [x["new"] for x in a] != [x["new"] for x in b]                # video khác: xoay vòng
    monkeypatch.setattr(ff, "BRAND_DIR", "/khong/co")
    assert voice_book.pick("x", "y") == []


def test_bank_remembers_edits_and_prefers_matching_ingredients(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_BANK", str(tmp_path / "bank.json"))
    monkeypatch.setattr(ff, "BRAND_DIR", KIT)
    before = [{"i": 0, "text": "Cá kho xong rồi đó cả nhà ơi, ăn liền thôi nha.", "orig": "cá kho xong rồi"},
              {"i": 1, "text": "Giờ mình thả gà vô chảo dầu nóng cho vàng đều.", "orig": "thả gà vô chảo"},
              {"i": 2, "text": "Ngắn quá.", "orig": "x"}]
    after = [{"i": 0, "text": "Nồi cá kho keo lại sóng sánh, ăn với cơm nóng là hết sẩy.", "orig": "cá kho xong rồi"},
             {"i": 1, "text": "Giờ mình thả gà vô chảo dầu nóng cho vàng đều.", "orig": "thả gà vô chảo"},
             {"i": 2, "text": "Ngắn quá.", "orig": "x"}, {"i": 3, "text": "", "orig": "y", "ost": "orig"}]
    assert voice_book.remember(before, after) == 2
    assert voice_book.remember(before, after) == 0                       # không lưu trùng
    rows = voice_book.bank()
    assert [r["edited"] for r in rows] == [True, False]
    ex = voice_book.pick("hôm nay mình kho cá", "v")
    assert sum(1 for x in ex if x["kind"] == "bank") == 1 and len(ex) == voice_book.PICK
    assert any(x["new"].startswith("Nồi cá kho") for x in ex)            # chỉ đoạn SỬA tay mới thành mẫu


def test_examples_go_into_prompt_and_copying_them_is_rejected(monkeypatch):
    monkeypatch.setattr(ff, "BRAND_DIR", KIT)
    ex = voice_book.pick("mực", "v")
    copied = ex[1]["new"]
    fresh = "Giờ mình bắc nồi nước lên bếp, chờ sôi lăn tăn rồi thả mực vô luộc."
    seen: list = []
    replies = iter([[copied], [fresh]])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(prompt_engine, "genai", object())

    async def fake(prompt, images=None, **kw):
        seen.append(prompt)
        return json.dumps({"segments": [{"i": 0, "text": next(replies)[0]}], "title": "t", "caption": "c",
                           "hashtags": "#a", "hook": "mực luộc"})
    monkeypatch.setattr(prompt_engine, "_call_gemini", fake)
    segs = [(0.0, 5.0, "đầu tiên mọi người bắc nồi nước lên cho sôi rồi bỏ mực vô")]
    out = asyncio.run(prompt_engine.generate_timed("", "", "cook", segs, 5.0, examples=ex))
    assert "GIỌNG KÊNH" in seen[0] and all(x["new"] in seen[0] for x in ex)
    assert len(seen) == 2 and out["segments"][0]["text"] == fresh       # chép câu mẫu -> bắt viết lại


def test_no_examples_prompt_unchanged(monkeypatch):
    assert prompt_engine.voice_block(None) == "" and prompt_engine.voice_block([]) == ""


# ---------------- chỉ số lời đọc ----------------

def test_voice_report():
    segs = [{"start": 0.0, "text": "Giờ mình luộc mực nha.", "ost": "new"},
            {"start": 5.0, "text": "Tóm lại là xong. Ngon lắm nha.", "ost": "new"},
            {"start": 20.0, "text": "", "ost": "orig"}]
    plan = [{"start": 0.1, "end": 3.0, "factor": 1.0}, {"start": 5.0, "end": 9.0, "factor": 1.0},
            {"start": 20.0, "end": 20.0, "factor": 1.0}]
    r = common.voice_report(segs, plan, 25.0)
    assert r == "ℹ️ Lời đọc: 12 chữ, giọng phủ 28% video, 1 quãng lặng trên 4 giây, 2/3 câu kết «nha». "
    assert common.voice_report([], [], 10.0) == ""
