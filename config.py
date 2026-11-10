import json
import logging
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """Đọc file .env cạnh main.py (không cần thư viện). Biến môi trường thật được ưu tiên hơn."""
    path = os.path.join(_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and not os.environ.get(k):
                os.environ[k] = v


_load_dotenv()
SETTINGS_FILE = os.path.join(_DIR, "settings.json")
UPLOAD_DIR = os.path.join(_DIR, "uploads_in")

# ===== Cài đặt SỬA ĐƯỢC TỪ WEB (mặc định lấy từ biến môi trường, lưu đè vào settings.json) =====
EDITABLE = {
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "OPENROUTER_API_KEY": "",
    "OPENROUTER_MODEL": "auto",          # auto = tự dò model free đang hoạt động
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "GROQ_API_KEY": "",
    "GROQ_WHISPER_MODEL": "whisper-large-v3-turbo",
    "YTDLP_COOKIES": "",
    "TTS_RATE": "+8%",
    "TTS_PITCH": "+0Hz",
    "TTS_PROVIDER": "capcut",          # capcut | edge
    "CAPCUT_VOICE_FEMALE": "multi_female_yangguangnv_uranus_bigtts",
    "CAPCUT_VOICE_MALE": "multi_male_felipe_uranus_bigtts",
    "VOICE_SEPARATION": "1",
    "PUBLIC_URL": "",
    "BRAND_NAME": "",                 # tên kênh cho màn kết (để trống = không có màn kết)
    "BRAND_LOGO": "1",                # 1 = gắn logo assets/logo.png (upload trong Cài đặt) nếu có
    "BRAND_INTRO": "1",               # 1 = hiện tiêu đề video 3 giây đầu
    "SUB_STYLE": "karaoke",           # karaoke | box | outline | plain
    "REVIEW_SCRIPT": "0",
    "CUT_PROMO": "1",                 # 1 = cắt bỏ đoạn quảng cáo sản phẩm của người gốc ở cuối video
    "QUALITY": "balanced",
    "POT_SERVER": "",
    "YTDLP_PROXY": "",                # proxy cho yt-dlp, vd http://user:pass@ip:port hoặc socks5://... (vượt chặn IP YouTube)                 # URL server bgutil POT chạy ở máy khác (http://ip:4416); để trống nếu dùng script            # fast (nhanh nhất) | balanced (mặc định) | high (chuẩn TVC, chậm ~2x)             # 1 = luôn dừng cho duyệt kịch bản trước khi render                 # link web Botkeep (vd https://okx.bot-keep.xyz) để Telegram gửi link tải file lớn
    "SPEECH_STYLE": "mien_tay",       # mien_tay | mien_nam | pho_thong: giọng văn lời thoại
    "VOICE_HUMANIZE": "1",            # 1 = xử lý giọng cho bớt chất AI (ấm, bớt chói, có không gian phòng)          # 1 = tách giọng gốc bằng AI (chế độ nấu ăn); 0 = chỉ tắt tiếng nền lúc người gốc nói
}
SECRET_KEYS = {"YTDLP_PROXY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "YTDLP_COOKIES"}
_ENV = {k: os.environ.get(k, d) for k, d in EDITABLE.items()}

# ===== Chỉ đặt qua biến môi trường =====
# (BOT_TOKEN đổi là phải restart polling; WEB_PASSWORD không cho sửa từ web để kẻ lạ không tự đổi khóa)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Botkeep (Pterodactyl) cấp cổng qua SERVER_PORT; nền tảng khác thường dùng PORT
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8080")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

MAX_DURATION = 90                  # giây: giới hạn video thường
LONG_MAX = 300                     # giây: chế độ remake nguyên video dài (≤5 phút)
SPLIT_MAX = 600                    # giây: nguồn tối đa cho chế độ cắt thành nhiều phần (≤10 phút)
HARD_MAX = LONG_MAX                # trần tuyệt đối cho mọi bước render/bóc audio
LONG_FILESIZE = 300 * 1024 * 1024  # file nguồn tối đa cho video dài
TG_LIMIT = 48 * 1024 * 1024        # Telegram không cho bot gửi file > 50MB
OUT_MAX_MB = int(os.environ.get("OUT_MAX_MB", "400"))  # tổng video giữ cho web tải về (ổ Botkeep 2GB); thiếu chỗ thì tự dọn thêm
MAX_FILESIZE = 50 * 1024 * 1024    # chặn file nguồn yt-dlp > 50MB
MAX_UPLOAD = 300 * 1024 * 1024     # upload trực tiếp qua web tối đa 300MB (video dài)
MAX_QUEUE = 5
KEEP_WEB_OUTPUTS = 3
OUTPUT_HD = os.environ.get("OUTPUT_HD", "1") != "0"   # 1080p khi nguồn đủ nét; đặt 0 nếu Botkeep thiếu RAM


def _apply():
    over = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            over = json.load(f)
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("settings.json hỏng, dùng biến môi trường")
    for k in EDITABLE:
        globals()[k] = str(over.get(k) or _ENV[k])


def save_settings(updates: dict):
    """Lưu từ web. Giá trị rỗng = không đổi; giá trị '-' = xóa, quay về biến môi trường."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        cur = {}
    for k, v in (updates or {}).items():
        if k not in EDITABLE:
            continue
        v = str(v).strip()
        if v == "-":
            cur.pop(k, None)
        elif v:
            if k == "TTS_RATE" and not re.fullmatch(r"[+-]\d{1,3}%", v):
                raise ValueError("TTS_RATE phải dạng +8% hoặc -5%")
            if k == "QUALITY" and v not in ("fast", "balanced", "high"):
                raise ValueError("QUALITY chỉ nhận: fast, balanced, high")
            if k == "SUB_STYLE" and v not in ("karaoke", "box", "outline", "plain"):
                raise ValueError("SUB_STYLE chỉ nhận: karaoke, box, outline, plain")
            if k == "SPEECH_STYLE" and v not in ("mien_tay", "mien_nam", "pho_thong"):
                raise ValueError("SPEECH_STYLE chỉ nhận: mien_tay, mien_nam, pho_thong")
            if k == "TTS_PITCH" and not re.fullmatch(r"[+-]\d{1,3}Hz", v):
                raise ValueError("TTS_PITCH phải dạng +2Hz hoặc -3Hz")
            cur[k] = v
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    _apply()


def describe_settings() -> dict:
    try:
        over = set(json.load(open(SETTINGS_FILE, encoding="utf-8")))
    except Exception:
        over = set()
    out = {}
    for k in EDITABLE:
        v = globals()[k]
        if k in SECRET_KEYS:
            shown = (v[:4] + "•••" + v[-3:]) if len(v) > 10 else ("(đã đặt)" if v else "")
        else:
            shown = v
        out[k] = {"value": shown, "set": bool(v), "overridden": k in over}
    return out


_apply()
