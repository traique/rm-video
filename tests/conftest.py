"""Cấu hình chung cho pytest: chạy từ thư mục gốc dự án, không đụng jobs.db / settings.json thật."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="vb-test-")
os.environ.setdefault("JOBS_DB", os.path.join(_TMP, "jobs.db"))
os.environ.setdefault("RECENT_LOOKS", os.path.join(_TMP, "recent_looks.json"))   # lịch sử công thức hiệu ứng 📸
os.environ.setdefault("RENDER_SPEED_FILE", os.path.join(_TMP, "render_speed.json"))   # tốc độ dựng đo được
os.environ.setdefault("AUTOPOST_STATE", os.path.join(_TMP, "autopost.json"))   # trạng thái tự động đăng
os.environ.pop("BOT_TOKEN", None)          # test không bao giờ nối Telegram thật
