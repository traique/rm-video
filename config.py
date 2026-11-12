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
    "GROQ_API_KEY": "",
    "GROQ_WHISPER_MODEL": "whisper-large-v3-turbo",
    "GEMINI_ASR_MODEL": "gemini-3.5-transcribe",   # bóc lời bằng Gemini (mốc từng từ), dùng chung GEMINI_API_KEY
    "ASR_ORDER": "groq,gemini,capcut",             # thứ tự nguồn bóc lời; nguồn thiếu key tự bỏ qua
    "ASR_VOCAB": "",                   # từ điển: tên món, nguyên liệu, tên riêng của kênh (cách nhau dấu phẩy) để bóc lời viết đúng
    "YTDLP_COOKIES": "",
    "YTDLP_PROXY": "",                 # proxy cho yt-dlp, vd http://user:pass@ip:port hoặc socks5://...
    "TTS_RATE": "+8%",
    "TTS_PITCH": "+0Hz",
    "TTS_PROVIDER": "vieneu",          # vieneu (giọng miền Nam đọc trên Kaggle; chưa có Kaggle/lỗi -> edge) | edge | capcut
    "VIENEU_VOICE_FEMALE": "Mỹ Duyên", # giọng nữ VieNeu (miền Nam: Mỹ Duyên, Thục Đoan, Thùy Dung, Kim Thanh; clone = giọng mẫu)
    "VIENEU_VOICE_MALE": "Adam",       # giọng nam VieNeu (miền Nam: Adam, Thái Sơn, Minh Triết, Đức Trí)
    "KAGGLE_USE": "1",                 # 1 = phần nặng (đọc giọng, tách giọng, dựng) chạy trên Kaggle khi đã điền tài khoản
    "KAGGLE_USERNAME": "",             # tên tài khoản Kaggle (kaggle.com/<tên>)
    "KAGGLE_API_TOKEN": "",            # Kaggle -> Settings -> API Tokens -> Generate New Token
    "CAPCUT_VOICE_FEMALE": "multi_female_yangguangnv_uranus_bigtts",
    "CAPCUT_VOICE_MALE": "multi_male_felipe_uranus_bigtts",
    "VOICE_SEPARATION": "1",           # 1 = tách giọng gốc bằng AI (chế độ nấu ăn); 0 = chỉ tắt tiếng nền lúc người gốc nói
    "VOICE_HUMANIZE": "1",             # 1 = xử lý giọng cho bớt chất AI (ấm, bớt chói, có không gian phòng)
    "SPEECH_STYLE": "mien_tay",        # mien_tay | mien_nam | pho_thong: giọng văn lời thoại
    "PUBLIC_URL": "",                  # link web Botkeep (vd https://okx.bot-keep.xyz) để Telegram gửi link tải file lớn
    "BRAND_NAME": "",                  # tên kênh cho màn kết (để trống = không có màn kết)
    "BRAND_LOGO": "1",                 # 1 = gắn logo assets/logo.png (upload trong Cài đặt) nếu có
    "BRAND_INTRO": "1",                # 1 = hiện tiêu đề video 3 giây đầu
    "SUB_STYLE": "karaoke",            # karaoke | box | outline | plain
    "COVER": "1",                      # 1 = tự tạo ảnh bìa (khung đẹp nhất + tiêu đề; AI chọn ảnh nếu có key Gemini)
    "TEXT_DETECT": "heuristic",        # heuristic | ocr (model PP-OCRv6 tiny, cần lệnh setup_offline): dò phụ đề in cứng
    "TTS_OFFLINE": "0",                # 1 = khi CapCut và edge-tts đều lỗi thì đọc bằng giọng offline Piper (setup_offline)
    "OST_AUTO": "0",                   # 1 = AI được đề xuất đoạn giữ nguyên tiếng gốc (tiếng xèo, câu cảm thán...)
    "SUB_TIMING": "spread",            # spread = rải chữ trên khoảng có tiếng của giọng (lệch ~70ms); stt = nghe lại bằng STT CapCut
    "REVIEW_SCRIPT": "0",              # 1 = luôn dừng cho duyệt kịch bản trước khi render
    "CUT_PROMO": "1",                  # 1 = cắt bỏ đoạn quảng cáo sản phẩm của người gốc ở cuối video
    "QUALITY": "balanced",             # fast (720p) | balanced (1080p, máy chậm tự 720p) | high (chuẩn TVC, chậm ~2x)
    "SOURCE_CREDIT": "0",              # 1 = thêm dòng "🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI" vào nội dung đăng (mặc định tắt)
    "ALLOWED_SOURCES": "",             # kênh nguồn được phép (tên kênh/uploader, cách nhau dấu phẩy); trống = mọi kênh
    "ALLOWED_USERS": "",               # chat_id/user_id Telegram được dùng bot, cách nhau dấu phẩy
    "ADMIN_CHAT_ID": "",               # nơi nhận cảnh báo (dịch vụ ngoài hỏng); trống = người đầu tiên trong ALLOWED_USERS
    # 🤖 Tự động đăng (autopost.py): lấy video ngẫu nhiên không trùng từ kênh nguồn -> làm lại -> đăng Fanpage giờ vàng
    "AUTO_POST": "0",                  # 1 = bật
    "AUTO_SOURCES": "",                # kênh Facebook nguồn (link kênh hoặc tên kênh), cách nhau dấu phẩy
    "AUTO_PER_DAY": "2",               # số video đăng mỗi ngày (1-4)
    "AUTO_TIMES": "",                  # trống = bot tự chọn giờ vàng theo lượt xem; hoặc cố định "11:30,20:30"
    "AUTO_MODE": "cook",               # chế độ làm lại: cook | review
    "FB_PAGE_ID": "",                  # id Fanpage đăng lên
    "FB_PAGE_TOKEN": "",               # Page access token (quyền pages_manage_posts, pages_read_engagement, pages_show_list)
}
SECRET_KEYS = {"YTDLP_PROXY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "YTDLP_COOKIES", "FB_PAGE_TOKEN",
               "KAGGLE_API_TOKEN"}
_ID_LIST = re.compile(r"\s*-?\d+(\s*,\s*-?\d+)*\s*")
_ENV = {k: os.environ.get(k, d) for k, d in EDITABLE.items()}

# ===== Chỉ đặt qua biến môi trường =====
# (BOT_TOKEN đổi là phải restart polling; WEB_PASSWORD không cho sửa từ web để kẻ lạ không tự đổi khóa)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Botkeep (Pterodactyl) cấp cổng qua SERVER_PORT; nền tảng khác thường dùng PORT
PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8080")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
# Hai địa chỉ dưới đây KHÔNG cho sửa từ web: lộ mật khẩu web sẽ không kéo theo lộ key (đổi base URL để hứng key)
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
POT_SERVER = os.environ.get("POT_SERVER", "")   # URL server bgutil POT chạy ở máy khác (http://ip:4416)
# Số luồng FFmpeg: 0 = tự theo số lõi container (cgroup)
FFMPEG_THREADS = int(os.environ.get("FFMPEG_THREADS", "0") or 0)
CACHE_MAX_MB = int(os.environ.get("CACHE_MAX_MB", "500"))   # trần ổ đĩa cho cache theo bước
HEALTH_CHECK_HOURS = float(os.environ.get("HEALTH_CHECK_HOURS", "6"))   # 0 = tắt kiểm tra định kỳ
LOUDNESS_2PASS = os.environ.get("LOUDNESS_2PASS", "1")      # 0 = quay về loudnorm một lượt (động) như cũ

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
            if k == "ASR_ORDER" and (not v or any(x.strip() not in ("groq", "gemini", "capcut") for x in v.split(","))):
                raise ValueError("ASR_ORDER là danh sách groq, gemini, capcut cách nhau dấu phẩy")
            if k == "TEXT_DETECT" and v not in ("heuristic", "ocr"):
                raise ValueError("TEXT_DETECT chỉ nhận: heuristic, ocr")
            if k == "SUB_TIMING" and v not in ("spread", "stt"):
                raise ValueError("SUB_TIMING chỉ nhận: spread, stt")
            if k in ("ALLOWED_USERS", "ADMIN_CHAT_ID") and not _ID_LIST.fullmatch(v):
                raise ValueError(f"{k} phải là các số chat_id cách nhau dấu phẩy")
            if k in ("TTS_PROVIDER",) and v not in ("capcut", "edge", "vieneu"):
                raise ValueError("TTS_PROVIDER chỉ nhận: vieneu, edge, capcut")
            if k == "KAGGLE_USE" and v not in ("0", "1"):
                raise ValueError("KAGGLE_USE chỉ nhận: 0, 1")
            if k == "KAGGLE_USERNAME":     # nhận cả dạng dán link: https://www.kaggle.com/<tên>
                v = re.sub(r"(?i)^(https?://)?(www\.)?kaggle\.com/", "", v).strip("/@ ").split("/")[0].lower()
            if k == "KAGGLE_USERNAME" and not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", v):
                raise ValueError("KAGGLE_USERNAME là tên tài khoản Kaggle (chữ, số, gạch dưới)")
            if k == "AUTO_POST" and v not in ("0", "1"):
                raise ValueError("AUTO_POST chỉ nhận: 0, 1")
            if k == "AUTO_PER_DAY" and not (v.isdigit() and 1 <= int(v) <= 4):
                raise ValueError("AUTO_PER_DAY là số 1-4 (các khung giờ cách nhau ít nhất 3 tiếng)")
            if k == "AUTO_TIMES" and not re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d(\s*,\s*([01]\d|2[0-3]):[0-5]\d)*\s*", v):
                raise ValueError("AUTO_TIMES dạng 11:30,20:30 (giờ Việt Nam)")
            if k == "AUTO_MODE" and v not in ("cook", "review"):
                raise ValueError("AUTO_MODE chỉ nhận: cook, review")
            if k == "FB_PAGE_ID" and not v.isdigit():
                raise ValueError("FB_PAGE_ID là dãy số id của Fanpage")
            if k == "AUTO_SOURCES":
                import autopost
                for s in re.split(r"[,\s]+", v):
                    autopost.normalize_source(s)        # ném ValueError nếu không phải kênh Facebook
            cur[k] = v
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:          # ghi tệp tạm rồi đổi tên: không bao giờ để settings.json dở dang
        json.dump(cur, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)                              # chứa key API: chỉ chủ tiến trình đọc được
    except OSError:
        pass
    os.replace(tmp, SETTINGS_FILE)
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


def id_set(value: str) -> set[int]:
    """'123, -456' -> {123, -456}."""
    return {int(x) for x in re.findall(r"-?\d+", value or "")}


def allowed_ids() -> set[int]:
    return id_set(globals()["ALLOWED_USERS"])


def admin_chat_id() -> int | None:
    """Nơi nhận cảnh báo: ADMIN_CHAT_ID, không đặt thì người ĐẦU TIÊN trong ALLOWED_USERS."""
    for src in (globals()["ADMIN_CHAT_ID"], globals()["ALLOWED_USERS"]):
        ids = re.findall(r"-?\d+", src or "")
        if ids:
            return int(ids[0])
    return None


def default_voice(male: bool = False) -> str:
    """Giọng mặc định theo TTS_PROVIDER (web 'Nữ/Nam mặc định', Telegram, tự động đăng)."""
    g = globals()                        # các cài đặt sửa được nạp động bằng _apply()
    if g["TTS_PROVIDER"] == "vieneu":
        return "vieneu:" + (g["VIENEU_VOICE_MALE"] if male else g["VIENEU_VOICE_FEMALE"])
    if g["TTS_PROVIDER"] == "capcut":
        return g["CAPCUT_VOICE_MALE"] if male else g["CAPCUT_VOICE_FEMALE"]
    return VOICE_MALE if male else VOICE_FEMALE
