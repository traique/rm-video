"""Cấu hình chung cho pytest: chạy từ thư mục gốc dự án, không đụng jobs.db / settings.json thật."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="vb-test-")
os.environ.setdefault("JOBS_DB", os.path.join(_TMP, "jobs.db"))
os.environ.pop("BOT_TOKEN", None)          # test không bao giờ nối Telegram thật
