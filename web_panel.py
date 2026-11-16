"""Mặt tiền tương thích của web panel — mã thật nằm trong gói rmvideo/interfaces/web/ (Phase 5):

    auth             phiên đăng nhập (cookie ký HMAC, thu hồi), mật khẩu, khoá dò mật khẩu, chống CSRF, header bảo mật
    http             tiện ích request (JSON / multipart có trần), giới hạn, kiểm link, file tải về
    routes_auth      trang, đăng nhập / đăng xuất, /health
    routes_jobs      danh sách job, SSE, tạo job (link / tải lên), huỷ, duyệt, tải kết quả
    routes_settings  cài đặt (nhập lại mật khẩu khi đổi khoá nhạy cảm), giọng, logo, giọng mẫu
    routes_autopost  🤖 tự động đăng
    routes_media     🎵 nhạc nền, link tạm cho Kaggle
    app              build_app(): ghép middleware + các nhóm

Tên cũ (`web_panel.build_app`, `web_panel.valid_session`, `web_panel._KEY_FILE`...) vẫn dùng được; GHI một tên (kể cả
monkeypatch) chuyển tới đúng module đang dùng tên đó (rmvideo/compat.py).
"""
from rmvideo import compat
from rmvideo.interfaces.web import app, auth, http, routes_auth

compat.install(__name__, compat.routes_of(app, routes_auth, auth, http))
