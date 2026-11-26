"""Phiên bản bot. Từ 1.0.0 đánh số kiểu MAJOR.MINOR.PATCH (trước đó là «bản 1» … «bản 50» theo từng lần sửa).

- sửa lỗi, chỉnh nhỏ: tăng PATCH (1.0.0 -> 1.0.1)
- thêm tính năng: tăng MINOR (1.0.1 -> 1.1.0)
- đổi lớn, phải cài đặt lại: tăng MAJOR (1.1.0 -> 2.0.0)

Mỗi lần lên phiên bản: thêm mục «## <phiên bản> — <tóm tắt> (<ngày>)» ở đầu CHANGELOG.md (các dòng «- …» đầu mục là
phần tóm tắt). Lần khởi động đầu tiên của phiên bản mới, bot tự báo qua Telegram và Zoom (rmvideo/app/release.py).
"""
VERSION = "1.4.16"
