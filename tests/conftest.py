import pytest
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
os.environ.setdefault("KIDS_STATE", os.path.join(_TMP, "kids_auto.json"))      # 🧸 truyện bé: truyện đã làm, lịch (1.1.0)
os.environ.setdefault("YT_STATE", os.path.join(_TMP, "youtube.json"))         # ▶️ YouTube: hàng chờ tải lên (1.2.0)
os.environ.setdefault("TIKTOK_STATE", os.path.join(_TMP, "tiktok_kit.json"))  # 🎵 gói đăng TikTok (1.2.0)
os.environ.setdefault("HOOK_STATE", os.path.join(_TMP, "hooks.json"))          # kiểu câu hook đã dùng (1.4.0)
os.environ.setdefault("BGM_LYRIA_DIR", os.path.join(_TMP, "bgm_lyria"))     # kho nhạc Lyria kênh bếp (1.1.2): không dùng assets/
os.environ.setdefault("VOICE_BANK", os.path.join(_TMP, "voice_bank.json"))   # kho lời đã duyệt (sổ giọng)
os.environ.setdefault("ZOOM_TARGETS", os.path.join(_TMP, "zoom_targets.json"))   # nơi trả lời Zoom đã học (1.0.0)
os.environ.setdefault("VERSION_STATE", os.path.join(_TMP, "version_announced.json"))   # đã báo phiên bản nào
os.environ.pop("BOT_TOKEN", None)          # test không bao giờ nối Telegram thật
# Bản 43 đổi mặc định sang bộ nhận diện «Bếp Đong Đủ». Các test cũ (và ảnh chụp lệnh dựng) viết cho hành vi khi CHƯA
# đặt nhận diện: ghim lại như cũ; test của bộ mặc định mới (test_ban43.py) tự đặt lại giá trị.
_OLD_DEFAULTS = {"BRAND_NAME": "0", "BRAND_TAGLINE": "0", "BRAND_CLOSE": "0", "BRAND_SERIES": "0", "BRAND_COLOR": "0",
                 "BRAND_LOOK": "goc", "COVER_TEMPLATE": "goc", "POST_RECIPE": "0", "TIPS": "off", "REMAKE_STYLE": "off",
                 "SHORT_VERSION": "off", "VOICE_BED": "off",      # 1.0.0: nhạc lót dưới lời (test riêng ở test_v100.py)
                 "FRAMING_AUTO": "0", "TALK_ZOOM": "0",                             # 1.1.2: khung dọc theo nội dung (test riêng: test_framing.py)
                 "SFX_SEP": "0", "INTERP": "0", "VOICE_LEAD_TRIM": "0", "IMG_UPSCALE": "lanczos",
                 "HOOK_PICK": "0"}                                  # 1.4.0: chọn câu hook (test riêng: test_hooks.py)                                     # 1.2.0: bỏ nhạc gốc / nội suy khung (test_sfx_sep.py, test_interp.py)
for _k, _v in _OLD_DEFAULTS.items():
    os.environ.setdefault(_k, _v)
os.environ.setdefault("RM_BRAND_DIR", os.path.join(_TMP, "no_brand_kit"))   # không có logo / âm hiệu mặc định



@pytest.fixture(autouse=True)
def _no_kaggle_resource_calls(monkeypatch):
    """1.4.9: kids_ready / lyria.remote_ok hỏi Kaggle model, dataset còn không. Test không gọi mạng thật: coi như còn;
    test nào cần trường hợp mất thì tự đặt lại (tests/test_v149.py)."""
    import time as _t
    import kaggle_worker as _kw
    from rmvideo.media import lyria as _ly

    class _Fresh(dict):                       # mọi dataset kho nhạc: «vừa kiểm thấy còn»
        def get(self, k, d=None):
            return _t.time()
    monkeypatch.setattr(_kw, "_READY", {"ok": _t.time()})
    monkeypatch.setattr(_kw, "_COOL", {"until": 0.0, "why": ""})        # 1.4.11: không nghỉ gửi GPU lây sang test khác
    monkeypatch.setattr(_ly, "_REMOTE", _Fresh())
