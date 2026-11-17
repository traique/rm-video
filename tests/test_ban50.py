"""Bản 50: số đọc bằng chữ («dầu nóng khoảng một trăm bảy mươi độ»): trước đây bộ kiểm chỉ hiểu MỘT chữ số đứng ngay
trước đơn vị, nên AI viết đúng «170 độ» lại bị coi là bịa số và bị xoá khỏi lời đọc."""
import ingredients


def test_spoken_numbers():
    sn = ingredients.spoken_number
    cases = {"một trăm bảy mươi": 170, "hai trăm": 200, "ba mươi": 30, "mười lăm": 15, "mười": 10,
             "hai mươi lăm": 25, "hai lăm": 25, "hai mươi mốt": 21, "bảy mươi tư": 74, "một trăm rưỡi": 150,
             "trăm rưỡi": 150, "một trăm linh năm": 105, "một trăm lẻ năm": 105, "một trăm hai": 120,
             "một trăm năm mươi": 150, "chín mươi chín": 99, "năm": 5, "ba mốt": 31, "hai chục": 20}
    for words, want in cases.items():
        assert sn(words.split()) == want, words
    for words in ("không", "mươi", "một mốt", "lăm", "chín năm", "rưỡi", "trăm trăm", "hai ba", "năm mươi nghìn"):
        assert sn(words.split()) is None, words


def test_numbers_understand_spelled_quantities():
    n = ingredients.numbers
    assert n("dầu nóng khoảng một trăm bảy mươi độ") == ["170 độ"]
    assert n("hai trăm gam thịt ba chỉ, mười lăm phút") == ["200 gam", "15 phút"]
    assert n("một trăm năm mươi tới hai trăm gam") == ["150-200 gam"]
    assert n("năm mươi nghìn đồng") == ["50 nghìn"]                    # nghìn là đơn vị giá, như AI viết "50 nghìn"
    assert n("luộc chín năm phút") == ["5 phút"]                        # "chín" (chín tới) không ghép vào số
    assert n("ba tới bốn giọt") == ["3-4 giọt"] and n("một con mực") == [] and n("nửa muỗng") == ["0.5 muỗng"]


def test_correct_digits_are_not_called_invented():
    orig = "Rồi mình bắc chảo dầu, đợi dầu nóng khoảng một trăm bảy mươi độ, chiên chừng ba phút là vàng."
    assert ingredients.check_numbers(orig, "Dầu nóng chừng 170 độ thì thả mực vô chiên 3 phút cho vàng giòn.") == \
        ([], [])
    assert ingredients.check_numbers(orig, "Chiên ở 180 độ chừng 3 phút.") == (["170 độ"], ["180 độ"])


def test_spelled_invented_numbers_can_be_dropped():
    d = ingredients.drop_numbers
    assert d("Chiên ở khoảng một trăm bảy mươi độ cho vàng giòn.", {"170 độ"}) == "Chiên ở cho vàng giòn."
    assert d("Chiên ở nhiệt độ một trăm bảy mươi độ cho vàng.", {"170 độ"}) == "Chiên ở nhiệt độ cho vàng."
    assert d("Luộc chín năm phút là được.", {"5 phút"}) == "Luộc chín là được."
    assert d("Cho một trăm năm mươi tới hai trăm gam thịt.", {"150-200 g"}) == "Cho thịt."
    assert d("Thêm hai muỗng nước mắm, ba muỗng đường.", {"2"}) == "Thêm nước mắm, ba muỗng đường."
