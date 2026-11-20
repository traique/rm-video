import contextlib
import logging
import os
import re
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """Đọc file .env cạnh main.py (không cần thư viện). Biến môi trường thật được ưu tiên hơn.
    Ghi chú cuối dòng (`CACHE_MAX_MB=500   # trần`) bị bỏ: trước đây giá trị thành "500   # trần" và int() làm bot
    không khởi động được. Giá trị có dấu # hay khoảng trắng thì bọc trong "..." hoặc '...'."""
    path = os.path.join(_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, raw = line.split("=", 1)
            k, v = k.strip(), raw.strip()
            if v[:1] in ("'", '"') and v.find(v[0], 1) > 0:
                v = v[1:v.index(v[0], 1)]
            elif re.match(r"\s+#", raw):
                v = ""                                   # KEY=      # ghi chú
            else:
                v = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip('"').strip("'")
            if k and not os.environ.get(k):
                os.environ[k] = v


_load_dotenv()
if _DIR not in sys.path:                     # gói rmvideo/ nằm cạnh: notebook Kaggle chạy từ thư mục khác
    sys.path.insert(0, _DIR)
from rmvideo.app import settings as _schema  # noqa: E402

SETTINGS_FILE = os.path.join(_DIR, "settings.json")
UPLOAD_DIR = os.path.join(_DIR, "uploads_in")

# ===== Cài đặt SỬA ĐƯỢC TỪ WEB (mặc định lấy từ biến môi trường, lưu đè vào settings.json) =====
# Một nguồn sự thật: rmvideo/app/settings.py (mặc định, nhãn, lựa chọn, kiểm hợp lệ, bí mật, nhập lại mật khẩu,
# áp dụng ngay hay chờ job xong). Các bảng dưới đây giữ tên cũ cho mã/test đang dùng.
EDITABLE = {s.key: s.default for s in _schema.SCHEMA}
SECRET_KEYS = set(_schema.SECRET_KEYS)
GROUPS = list(_schema.GROUPS)
# Mỗi khoá: (nhóm, nhãn, giải thích ngắn, link lấy/hướng dẫn, mức cần: must | should | optional)
META = {k: _schema.BY_KEY[k].meta() for k in _schema.DISPLAY_ORDER}
_ENV = {k: os.environ.get(k, d) for k, d in EDITABLE.items()}

# Khai báo kiểu cho mọi khoá sửa được từ web (giá trị gán lúc chạy bởi _apply()): công cụ tĩnh (mypy, IDE) thấy được
# tên, gõ sai tên cài đặt báo lỗi ngay. Danh sách phải khớp schema (tests/test_settings_schema.py kiểm).
GEMINI_API_KEY: str; GEMINI_MODEL: str; GEMINI_FALLBACK_MODELS: str; OPENROUTER_API_KEY: str; OPENROUTER_MODEL: str; GROQ_API_KEY: str
GROQ_WHISPER_MODEL: str; GEMINI_ASR_MODEL: str; ASR_ORDER: str; ASR_VOCAB: str; YTDLP_COOKIES: str; YTDLP_PROXY: str
TTS_RATE: str; TTS_PITCH: str; TTS_PROVIDER: str; VIENEU_VOICE_FEMALE: str; VIENEU_VOICE_MALE: str; KAGGLE_USE: str
KAGGLE_ANALYZE: str; KAGGLE_ASR: str; VOICE_SEP_MODEL: str; KAGGLE_USERNAME: str; KAGGLE_API_TOKEN: str; CAPCUT_VOICE_FEMALE: str
CAPCUT_VOICE_MALE: str; VOICE_SEPARATION: str; VOICE_HUMANIZE: str; SPEECH_STYLE: str; PUBLIC_URL: str
BRAND_NAME: str; BRAND_LOGO: str; BRAND_INTRO: str; SUB_STYLE: str; COVER: str; TEXT_DETECT: str; TEXT_INPAINT: str; FRAMING_AUTO: str; TALK_ZOOM: str
SCENE_ANALYSIS: str; TTS_OFFLINE: str; TEASER: str; NO_SPEECH: str; BGM_LINKS: str; BGM_SOURCE: str; BGM_ORIG: str; VOICE_BED: str; OST_AUTO: str
SUB_TIMING: str; REVIEW_SCRIPT: str; CUT_PROMO: str; QUALITY: str; SOURCE_CREDIT: str; ALLOWED_SOURCES: str
ALLOWED_USERS: str; ADMIN_CHAT_ID: str; AUTO_POST: str; AUTO_SOURCES: str; AUTO_TIKTOK_SOURCES: str
AUTO_PLATFORMS: str; AUTO_PER_DAY: str; AUTO_TIMES: str; AUTO_MODE: str; FB_PAGE_ID: str; FB_PAGE_TOKEN: str
REMAKE_STYLE: str; POST_RECIPE: str; TIPS: str; QTY_CHECK: str; TIME_SKIP_LABEL: str; BRAND_COLOR: str
BRAND_LOOK: str; BRAND_GRAIN: str; BRAND_TAGLINE: str; BRAND_CLOSE: str; BRAND_SERIES: str; BRAND_STING: str
COVER_TEMPLATE: str; AUTO_SKIP_ADS: str; AUTO_MIN_HEIGHT: str; SHORT_VERSION: str; AUTO_POST_VERSION: str
NOTIFY_JOBS: str; ZOOM_CLIENT_ID: str; ZOOM_CLIENT_SECRET: str; ZOOM_BOT_JID: str; ZOOM_SECRET_TOKEN: str
ZOOM_APP_TYPE: str; ZOOM_ALLOWED_USERS: str; ZOOM_ADMIN: str
KIDS_AUTO: str; KIDS_TIMES: str; KIDS_FB_PAGE_ID: str; KIDS_FB_PAGE_TOKEN: str; KIDS_VOICE: str; KIDS_IMAGE_MODEL: str; KIDS_IMAGE_CHECK: str; KIDS_LORA: str
KIDS_MULTI_VOICE: str; KIDS_EN_VOICE: str; VOICE_QA: str
KIDS_LLM_PROVIDER: str; KIDS_GEMINI_MODEL: str; KIDS_GEMINI_REVISE_MODEL: str; KIDS_GEMINI_THINKING: str
KIDS_LLM_PATTERN: str; KIDS_LLM_HF: str; KIDS_LLM_THINK: str; KIDS_KAGGLE_SOURCES: str; KIDS_CHANNEL_NAME: str
KIDS_WORLD_NAME: str
KIDS_TAGLINE: str; KIDS_HASHTAGS: str; KIDS_FX: str; KIDS_BGM_LINKS: str; KIDS_BGM: str

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
LOGO_MAX_BYTES = 400 * 1024        # logo gắn vào MỌI việc gửi Kaggle (notebook tối đa 1MB, mã + lời đã ~260KB)
MAX_QUEUE = 5
OUTPUT_HD = os.environ.get("OUTPUT_HD", "1") != "0"   # 1080p khi nguồn đủ nét; đặt 0 nếu Botkeep thiếu RAM


# Cài đặt ảnh hưởng tới CÁCH DỰNG (không "live") không đổi giữa chừng một job (C9): trước đây lưu QUALITY/TTS_PROVIDER
# từ web trong lúc job chạy làm job quyết 720p theo giá trị cũ rồi render bằng preset mới, cache giọng lẫn nhà cung
# cấp. Worker bọc mỗi job trong hold(); lưu lúc đó thì giá trị mới chờ trong _DEFERRED, áp dụng khi job xong.
_HOLD = {"depth": 0}
_DEFERRED: dict = {}


def _read_overrides(quiet: bool = False) -> dict:
    try:
        return _schema.read_overrides(SETTINGS_FILE)
    except Exception:
        if not quiet:
            logging.exception("settings.json hỏng, dùng biến môi trường")
        return {}


def _desired(over: dict) -> dict:
    return {k: str(over.get(k) or _ENV[k]) for k in EDITABLE}


def _apply():
    g = globals()
    for k, v in _desired(_read_overrides()).items():
        if _HOLD["depth"] and not _schema.BY_KEY[k].live and v != g.get(k):
            _DEFERRED[k] = v                          # đang có job: chờ job xong
            continue
        _DEFERRED.pop(k, None)
        g[k] = v


@contextlib.contextmanager
def hold():
    """Bọc một job: cài đặt không "live" lưu trong lúc này chỉ áp dụng khi job xong (cho job kế tiếp)."""
    _HOLD["depth"] += 1
    try:
        yield
    finally:
        _HOLD["depth"] -= 1
        if not _HOLD["depth"] and _DEFERRED:
            globals().update(_DEFERRED)               # chỉ các khoá đã lưu trong lúc job chạy
            _DEFERRED.clear()


def brand_text(key: str) -> str:
    """Chữ / màu nhận diện kênh (BRAND_NAME, BRAND_TAGLINE, BRAND_CLOSE, BRAND_SERIES, BRAND_COLOR): "0" = tắt. Từ
    bản 43 các ô này có sẵn bộ «Bếp Đong Đủ», mà ô để trống trên web nghĩa là «không đổi», nên tắt bằng số 0."""
    v = str(globals().get(key) or "").strip()
    return "" if v in ("0", "off", "tắt") else v


def save_settings(updates: dict):
    """Lưu từ web. Giá trị rỗng = không đổi; giá trị '-' = xóa, quay về biến môi trường. Sai giá trị: ValueError
    (không ghi gì). Kiểm hợp lệ theo schema rmvideo/app/settings.py."""
    try:
        cur = _schema.read_overrides(SETTINGS_FILE)
    except Exception:
        cur = {}
    _schema.write_overrides(SETTINGS_FILE, _schema.merge_updates(cur, updates))
    _apply()


def reauth_keys(updates: dict) -> list:
    """Các khoá trong `updates` đổi được nơi dữ liệu/quyền đi tới (proxy, link web, token Kaggle/Fanpage, người dùng
    Telegram): web bắt nhập lại mật khẩu trước khi lưu (lộ phiên đăng nhập không đủ để đổi)."""
    over = _read_overrides()
    return _schema.changed_reauth_keys(_desired(over), over, updates)


def describe_settings() -> dict:
    g = globals()
    over = set(_read_overrides(quiet=True))
    shown = {k: str(_DEFERRED.get(k, g[k])) for k in EDITABLE}
    return _schema.describe(shown, over, _DEFERRED)


def setting_groups() -> list:
    """[{id, title, help}] theo thứ tự hiển thị."""
    return [{"id": g, "title": t, "help": h} for g, t, h in GROUPS]


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
