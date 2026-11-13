"""Bản 38 — từ log chạy thật 13/11/2026 (cháo hàu, gà đốt Ô Thum): bộ kiểm nguyên liệu không báo bịa khi AI chỉ sửa lỗi
máy nghe; cụm nghe nhầm hay gặp được sửa ngay trong lời gốc."""
import ingredients
import vocab


def test_near_tolerates_asr_typos_but_not_real_inventions():
    ok = [("Lúc này mọi người niêm cho mình là 1 muỗng hột ngọt", "Cho vô đây một muỗng bột ngọt nha bà con."),
          ("chấm kèm muối điêu chanh", "chấm muối tiêu chanh"),
          ("Hành lá xắt nhuyễn và hành tím phi", "Thêm hành lá xắt nhuyễn và hành phi nha."),
          ("cho rau găm vào", "cho rau răm vào"), ("nước mắn ngon", "nước mắm ngon")]
    for orig, new in ok:
        assert ingredients.check_rewrite(orig, new) == ([], []), (orig, new)
    bad = [("cá kho tộ với cơm nóng", "cua kho tộ với cơm nóng", "cua"),
           ("tôm rim nước mắm", "tỏi rim nước mắm", "tỏi"),
           ("gà kho gừng", "vịt kho gừng", "thịt vịt")]
    for orig, new, name in bad:
        assert name in ingredients.check_rewrite(orig, new)[1], (orig, new)
    assert ingredients._lev("hot", "bot") == 1 and ingredients._lev("xua", "xa") == 1 and ingredients._lev("", "ab") == 2


def test_fix_common_rewrites_frequent_mishearings_only():
    got = vocab.fix_common("Lúc này mọi người niêm cho mình là 1 muỗng hột ngọt, 1 muỗng hột niêm. Hành lá sắc nhiễn, "
                           "ngò ghía sắc nhiễn Và hành trém phi. Đốt ghiêu ghiêu. Cháo hào ngon, dầu hào thơm. "
                           "Gà đốt câu thơm. Giải nhiễn ra. Nửa muỗng satê. Một ít sữa cay. Niêm yết giá.")
    for want in ("nêm cho mình", "bột ngọt", "hạt nêm", "xắt nhuyễn, ngò gai xắt nhuyễn Và hành tím phi", "riu riu",
                 "Cháo hàu", "dầu hào thơm", "Gà đốt Ô Thum", "Giã nhuyễn", "sa tế", "sả cây", "Niêm yết giá"):
        assert want in got, want
    assert vocab.fix_common("Sữa cay") == "Sả cây" and vocab.fix_common("") == ""
    assert vocab.fix_common("hào hứng, mình niêm phong túi") == "hào hứng, mình niêm phong túi"
    text, segs = vocab.fix_segments("hột ngọt nè", [(0.0, 1.0, "hột ngọt nè")])
    assert text == "bột ngọt nè" and segs == [(0.0, 1.0, "bột ngọt nè")]       # áp cho cả đường Kaggle (from_whisper)
