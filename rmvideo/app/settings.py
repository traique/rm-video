"""Schema cài đặt — MỘT nguồn sự thật cho mọi khoá sửa được từ web.

Mỗi `Setting` gom những gì trước đây nằm ở ba nơi (config.EDITABLE + config.META + chuỗi `if k == ...` trong
config.save_settings + web_panel._SETTING_OPTIONS): giá trị mặc định, nhóm/nhãn/chú thích hiển thị, lựa chọn của ô
xổ xuống, cách kiểm hợp lệ, có phải bí mật không, đổi có phải nhập lại mật khẩu không (A5), và có được áp dụng ngay
khi đang có job chạy không (C9: cài đặt ảnh hưởng tới cách dựng chỉ đổi giữa hai job, không đổi giữa chừng một job).

Module thuần (chỉ đọc/ghi settings.json khi được gọi), chạy được cả trong notebook Kaggle.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from rmvideo.domain import sources

Validator = Callable[[str], str]
ON_OFF = ("0", "1")


@dataclass(frozen=True)
class Setting:
    key: str
    default: str
    group: str = "advanced"
    label: str = ""
    help: str = ""
    link: str = ""
    need: str = ""                                   # must | should | optional | ""
    options: tuple[tuple[str, str], ...] = ()        # (giá trị, nhãn) cho ô xổ xuống trên web
    choices: tuple[str, ...] = ()                    # giá trị hợp lệ; rỗng = không giới hạn danh sách
    validate: Validator | None = None                # chuẩn hoá hoặc ném ValueError (chạy sau `choices`)
    secret: bool = False                             # che trên web, không bao giờ gửi sang Kaggle
    reauth: bool = False                             # đổi phải nhập lại mật khẩu web (đổi được nơi dữ liệu/quyền đi tới)
    live: bool = False                               # áp dụng ngay cả khi đang có job chạy

    def check(self, value: str) -> str:
        """Giá trị đã chuẩn hoá để lưu, hoặc ValueError với câu báo cho người dùng."""
        if self.choices and value not in self.choices:
            raise ValueError(f"{self.key} chỉ nhận: {', '.join(self.choices)}")
        return self.validate(value) if self.validate else value

    def meta(self) -> tuple[str, str, str, str, str]:
        """Bộ (nhóm, nhãn, chú thích, link, mức cần) — định dạng config.META cũ."""
        return self.group, self.label, self.help, self.link, self.need


# ---------------- kiểm hợp lệ ----------------

def _regex(pattern: str, msg: str) -> Validator:
    def check(v: str) -> str:
        if not re.fullmatch(pattern, v):
            raise ValueError(msg)
        return v
    return check


def _max_len(key: str, n: int) -> Validator:
    def check(v: str) -> str:
        if len(v) > n:
            raise ValueError(f"{key} tối đa {n} ký tự")
        return v
    return check


def _model(key: str) -> Validator:
    return _regex(r"[\w.\-/:@]{1,120}", f"{key}: tên model chỉ gồm chữ, số và . - _ / : @")


_ID_LIST = re.compile(r"\s*-?\d+(\s*,\s*-?\d+)*\s*")


def _id_list(key: str) -> Validator:
    def check(v: str) -> str:
        if not _ID_LIST.fullmatch(v):
            raise ValueError(f"{key} phải là các số chat_id cách nhau dấu phẩy")
        return v
    return check


def _asr_order(v: str) -> str:
    if not v or any(x.strip() not in ("groq", "gemini", "capcut") for x in v.split(",")):
        raise ValueError("ASR_ORDER là danh sách groq, gemini, capcut cách nhau dấu phẩy")
    return v


def _bgm_links(v: str) -> str:
    links = [x for x in re.split(r"[\s,]+", v) if x]
    bad = [x for x in links if not re.match(r"https?://[^\s/]+\.[^\s/]+", x)]
    if bad:
        raise ValueError(f"BGM_LINKS: không phải link: {bad[0][:60]}")
    return ",".join(dict.fromkeys(links))


def _kaggle_sources(v: str) -> str:
    """notebook:user/slug, dataset:user/slug hoặc slug (notebook của mình), cách nhau dấu phẩy."""
    for item in re.split(r"[,\s]+", v):
        if item and not re.fullmatch(r"(?:(?:notebook|dataset):)?[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)?", item):
            raise ValueError("KIDS_KAGGLE_SOURCES: mỗi mục dạng notebook:user/slug hoặc dataset:user/slug")
    return v


def _kaggle_user(v: str) -> str:
    """Nhận cả dạng dán link: https://www.kaggle.com/<tên>."""
    v = re.sub(r"(?i)^(https?://)?(www\.)?kaggle\.com/", "", v).strip("/@ ").split("/")[0].lower()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", v):
        raise ValueError("KAGGLE_USERNAME là tên tài khoản Kaggle (chữ, số, gạch dưới)")
    return v


def _per_day(v: str) -> str:
    if not (v.isdigit() and 1 <= int(v) <= 4):
        raise ValueError("AUTO_PER_DAY là số 1-4 (các khung giờ cách nhau ít nhất 3 tiếng)")
    return v


def _each(norm: Callable[[str], str]) -> Validator:
    """Kiểm từng mục của danh sách (ném ValueError ở mục sai), lưu nguyên chuỗi người dùng nhập."""
    def check(v: str) -> str:
        for s in re.split(r"[,\s]+", v):
            norm(s)
        return v
    return check


def _platforms(v: str) -> str:
    got = [p for p in re.split(r"[,\s]+", v.lower()) if p]
    if not got or any(p not in sources.PLATFORMS for p in got):
        raise ValueError("AUTO_PLATFORMS: tick ít nhất một nguồn (facebook, tiktok)")
    return ",".join(p for p in sources.PLATFORMS if p in got)


def _digits(key: str, msg: str) -> Validator:
    def check(v: str) -> str:
        if not v.isdigit():
            raise ValueError(msg)
        return v
    return check


def _brand_color(v: str) -> str:
    v = v.strip()
    if v == "0":                                   # 0 = không dùng màu kênh (vàng như cũ)
        return v
    if v and not re.fullmatch(r"#?[0-9A-Fa-f]{6}", v):
        raise ValueError("BRAND_COLOR là mã màu dạng #FF8A00 (0 = vàng như cũ)")
    return ("#" + v.lstrip("#").upper()) if v else ""


_PUBLIC_URL = _regex(r"https?://[^\s/?#]+(/[^\s]*)?", "PUBLIC_URL phải là link http(s)://tên-miền của web bot")
_PROXY = _regex(r"(https?|socks4a?|socks5h?)://\S+", "YTDLP_PROXY dạng http://user:pass@ip:port hoặc socks5://ip:port")


# ---------------- schema (thứ tự = thứ tự cũ của config.EDITABLE) ----------------
S = Setting
_ONOFF_OPTS = (("1", "Bật"), ("0", "Tắt"))

SCHEMA: tuple[Setting, ...] = (
    S("GEMINI_API_KEY", "", "api", "Key Gemini (Google AI Studio)",
      help="AI viết lời, chọn ảnh bìa, kiểm nguyên liệu. Không có key thì bot chỉ tải/cắt video, không viết lời được.",
      link="https://aistudio.google.com/apikey", need="must", secret=True, live=True),
    S("GEMINI_MODEL", "gemini-3.5-flash-lite", "advanced", "Model Gemini viết lời",
      validate=_model("GEMINI_MODEL"), live=True),        # live: lỗi 404 tự đổi model giữa job
    S("GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-2.5-flash-lite", "advanced",
      "Model Gemini dự phòng (khi quá tải / hết lượt)",
      help="Model chính báo quá tải (503) hay hết lượt trong ngày thì thử lần lượt các model này (cách nhau dấu phẩy, "
           "để trống = không thử). Mỗi model có hạn mức riêng.",
      validate=_each(lambda v: _model("GEMINI_FALLBACK_MODELS")(v) if v else v), live=True),
    S("OPENROUTER_API_KEY", "", "api", "Key OpenRouter",
      help="Dự phòng viết lời khi Gemini lỗi/hết hạn mức (model miễn phí).", link="https://openrouter.ai/settings/keys",
      need="optional", secret=True, live=True),
    S("OPENROUTER_MODEL", "auto", "advanced", "Model OpenRouter", help="auto = tự dò model miễn phí đang hoạt động.",
      options=(("auto", "auto (tự dò model miễn phí)"),), validate=_model("OPENROUTER_MODEL"), live=True),
    S("GROQ_API_KEY", "", "api", "Key Groq", help="Bóc lời (Whisper) khi KHÔNG dùng Kaggle hoặc Kaggle lỗi. Miễn phí.",
      link="https://console.groq.com/keys", need="optional", secret=True, live=True),
    S("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo", "advanced", "Model Whisper của Groq",
      validate=_model("GROQ_WHISPER_MODEL"), live=True),
    S("GEMINI_ASR_MODEL", "gemini-3.5-transcribe", "advanced", "Model Gemini bóc lời",
      validate=_model("GEMINI_ASR_MODEL"), live=True),
    S("ASR_ORDER", "groq,gemini,capcut", "advanced", "Thứ tự nguồn bóc lời",
      help="groq, gemini, capcut; nguồn thiếu key tự bỏ qua.",
      options=(("groq,gemini,capcut", "Groq → Gemini → CapCut"), ("gemini,groq,capcut", "Gemini → Groq → CapCut"),
               ("gemini,capcut", "Gemini → CapCut"), ("groq,capcut", "Groq → CapCut")),
      validate=_asr_order, live=True),
    S("ASR_VOCAB", "", "advanced", "Từ điển món / tên riêng", help="Cách nhau dấu phẩy, để bóc lời viết đúng.",
      validate=_max_len("ASR_VOCAB", 3000), live=True),
    S("YTDLP_COOKIES", "", "api", "Cookies tải video",
      help="Chỉ khi tải lỗi «cần đăng nhập». Dán nội dung cookies.txt (tiện ích Get cookies.txt LOCALLY) của tài "
           "khoản PHỤ.", need="optional", secret=True, live=True),
    S("YTDLP_PROXY", "", "api", "Proxy tải video", help="http://user:pass@ip:port hoặc socks5://… Chỉ khi bị chặn IP.",
      need="optional", secret=True, validate=_PROXY, reauth=True, live=True),
    S("TTS_RATE", "+8%", "voice", "Tốc độ đọc (edge)", help="Dạng +8% / -5%.",
      validate=_regex(r"[+-]\d{1,3}%", "TTS_RATE phải dạng +8% hoặc -5%")),
    S("TTS_PITCH", "+0Hz", "voice", "Cao độ (edge)", help="Dạng +2Hz / -3Hz.",
      validate=_regex(r"[+-]\d{1,3}Hz", "TTS_PITCH phải dạng +2Hz hoặc -3Hz")),
    S("TTS_PROVIDER", "vieneu", "voice", "Nguồn giọng đọc",
      options=(("vieneu", "VieNeu giọng miền Nam, đọc trên Kaggle (mặc định; chưa có Kaggle / lỗi -> Edge)"),
               ("edge", "Edge TTS (Hoài My; lỗi thì đoạn đó đọc bằng CapCut)"),
               ("capcut", "CapCut (lỗi thì đọc bằng edge-tts)")),
      choices=("vieneu", "edge", "capcut")),
    S("VIENEU_VOICE_FEMALE", "Mỹ Duyên", "voice", "Giọng nữ VieNeu",
      options=(("Mỹ Duyên", "Mỹ Duyên (đọc truyện)"), ("Thục Đoan", "Thục Đoan (kể chuyện)"),
               ("Thùy Dung", "Thùy Dung (tin tức, đọc nhanh)"), ("Kim Thanh", "Kim Thanh (đọc truyện)"),
               ("clone", "Giọng nhân bản (đoạn mẫu tải lên bên dưới)")),
      validate=_max_len("VIENEU_VOICE_FEMALE", 40)),
    S("VIENEU_VOICE_MALE", "Adam", "voice", "Giọng nam VieNeu",
      options=(("Adam", "Adam (tự nhiên)"), ("Thái Sơn", "Thái Sơn (kể chuyện)"), ("Minh Triết", "Minh Triết (tin tức)"),
               ("Đức Trí", "Đức Trí (đọc truyện)"), ("clone", "Giọng nhân bản (đoạn mẫu tải lên bên dưới)")),
      validate=_max_len("VIENEU_VOICE_MALE", 40)),
    S("KAGGLE_USE", "1", "kaggle", "Dùng Kaggle cho phần nặng",
      options=(("1", "Bật: đọc giọng + tách giọng + dựng chạy trên Kaggle (lỗi thì chạy trên Botkeep)"),
               ("0", "Tắt: mọi thứ chạy trên Botkeep")), choices=ON_OFF),
    S("KAGGLE_ANALYZE", "1", "kaggle", "Bóc lời + phân tích hình trên Kaggle",
      options=(("1", "Bật: bóc lời (Whisper nghe giọng đã tách khỏi nhạc) + phân tích hình trên Kaggle"),
               ("0", "Tắt: bóc lời bằng Groq/Gemini + phân tích hình trên Botkeep")), choices=ON_OFF),
    S("KAGGLE_ASR", "large-v3", "kaggle", "Model bóc lời trên Kaggle",
      options=(("large-v3", "Whisper large-v3 (mặc định, mọi ngôn ngữ)"),
               ("phowhisper", "PhoWhisper-large (VinAI, tinh chỉnh tiếng Việt; cần notebook rmvideo-phowhisper đã chạy)")),
      choices=("large-v3", "phowhisper")),
    S("KAGGLE_USERNAME", "", "api", "Tên tài khoản Kaggle",
      help="Dán link kaggle.com/<tên> hoặc gõ tên. Dùng cùng token bên dưới.", link="https://www.kaggle.com/settings",
      need="should", validate=_kaggle_user, reauth=True, live=True),
    S("KAGGLE_API_TOKEN", "", "api", "Token Kaggle",
      help="Kaggle → Settings → API → Generate New Token. Cho phép tách giọng, bóc lời, đọc giọng VieNeu và dựng trên "
           "GPU miễn phí (30 giờ/tuần). Thiếu thì mọi thứ chạy trên Botkeep (chậm hơn nhiều).",
      link="https://www.kaggle.com/settings", need="should", secret=True, reauth=True, live=True),
    S("CAPCUT_VOICE_FEMALE", "multi_female_yangguangnv_uranus_bigtts", "voice", "Giọng nữ CapCut",
      validate=_regex(r"[\w.\-]{1,80}", "CAPCUT_VOICE_FEMALE là mã giọng CapCut (chữ, số, _ . -)")),
    S("CAPCUT_VOICE_MALE", "multi_male_felipe_uranus_bigtts", "voice", "Giọng nam CapCut",
      validate=_regex(r"[\w.\-]{1,80}", "CAPCUT_VOICE_MALE là mã giọng CapCut (chữ, số, _ . -)")),
    S("VOICE_SEPARATION", "1", "video", "Tách giọng gốc bằng AI", help="Tắt = chỉ hạ tiếng nền lúc người gốc nói.",
      options=(("1", "Bật (tách giọng AI các đoạn có lời, tối đa 45s lời)"), ("0", "Tắt")), choices=ON_OFF),
    S("VOICE_HUMANIZE", "1", "voice", "Làm giọng bớt chất AI", help="Ấm hơn, bớt chói, có không gian phòng.",
      options=_ONOFF_OPTS, choices=ON_OFF),
    S("SPEECH_STYLE", "mien_nam", "voice", "Giọng văn", help="Miền Nam, miền Tây hay phổ thông.",
      options=(("mien_nam", "Miền Nam (mình, mọi người, nha, nè) — mặc định"), ("mien_tay", "Miền Tây (tui, bà con, nè, nghen)"),
               ("pho_thong", "Phổ thông")),
      choices=("mien_tay", "mien_nam", "pho_thong")),
    S("PUBLIC_URL", "", "api", "Link web của bot",
      help="Ví dụ https://ten-may.bot-keep.xyz. Cần để Kaggle tải file bạn upload, Telegram gửi link tải video lớn.",
      need="should", validate=_PUBLIC_URL, reauth=True, live=True),
    S("BRAND_NAME", "Bếp Đong Đủ", "brand", "Tên kênh (màn kết)", help="Nhập 0 = không có màn kết.",
      validate=_max_len("BRAND_NAME", 60)),
    S("BRAND_LOGO", "1", "brand", "Gắn logo", help="Logo tải lên ở cuối trang cài đặt; chưa tải thì dùng logo Bếp Đong Đủ.", options=_ONOFF_OPTS,
      choices=ON_OFF),
    S("BRAND_INTRO", "1", "brand", "Hiện tiêu đề 3 giây đầu", options=_ONOFF_OPTS, choices=ON_OFF),
    S("SUB_STYLE", "karaoke", "video", "Kiểu phụ đề",
      options=(("karaoke", "Karaoke (sáng dần theo giọng)"), ("box", "Hộp nền đen"), ("outline", "Chữ trắng viền đen"),
               ("plain", "Vàng gọn")), choices=("karaoke", "box", "outline", "plain")),
    S("COVER", "1", "video", "Tự tạo ảnh bìa",
      options=(("1", "Bật (khung đẹp nhất + tiêu đề; AI chọn ảnh nếu có key Gemini)"), ("0", "Tắt")), choices=ON_OFF),
    S("TEXT_DETECT", "heuristic", "video", "Dò chữ in cứng trên video",
      options=(("heuristic", "Heuristic (mặc định, không cần cài)"), ("ocr", "Model OCR (bắt chữ màu, cần setup_offline)")),
      choices=("heuristic", "ocr")),
    S("TEXT_INPAINT", "1", "video", "Xoá chữ in cứng bằng AI (LaMa) khi phân tích trên Kaggle",
      help="1: Kaggle (GPU) vẽ lại nền ở dải chữ gốc bằng big-lama.pt rồi gửi video sạch về, phụ đề mới đặt lên nền thật "
           "thay vì ô mờ. Chỉ khi KAGGLE_ANALYZE bật; lỗi thì che mờ như cũ. 0: luôn che mờ.",
      options=(("1", "Bật (mặc định): Kaggle xoá chữ bằng LaMa"), ("0", "Tắt: che mờ như cũ")), choices=("0", "1")),
    S("SCENE_ANALYSIS", "light", "video", "Mức phân tích hình", help="light nhanh nhất; full kỹ nhất (chậm trên Botkeep).",
      options=(("light", "Nhẹ (mặc định): chỉ đọc khung chính — dò chữ in cứng, ảnh bìa, ảnh cho AI; không căn câu "
                         "theo chuyển cảnh. Video 220s trên Botkeep ~20 giây"),
               ("fast", "Vừa: thêm dò chuyển cảnh để câu mới bắt đầu đúng lúc đổi cảnh (~200 giây)"),
               ("full", "Kỹ: giải mã mọi khung như bản cũ (~380 giây)")),
      choices=("light", "fast", "full")),
    S("TTS_OFFLINE", "0", "voice", "Giọng offline dự phòng (Piper)",
      help="Khi CapCut và edge-tts đều lỗi (cần lệnh setup_offline).",
      options=(("0", "Tắt"), ("1", "Bật giọng Piper khi CapCut + edge đều lỗi (xem lưu ý giấy phép)")), choices=ON_OFF),
    S("TEASER", "1", "video", "Đoạn mở đầu 3 giây cảnh món + câu hook", help="Chỉ video nấu ăn có lời.",
      options=(("1", "Bật: video nấu ăn có lời mở đầu bằng 3 giây cảnh món đã xong + câu hook (cần key Gemini)"),
               ("0", "Tắt: mở đầu như video gốc")), choices=ON_OFF),
    S("NO_SPEECH", "music", "video", "Video không lời", help="Chèn nhạc nền (mặc định) hay để AI viết lời.",
      options=(("music", "Chèn nhạc nền (mặc định): không thêm lời, tránh nói sai sự thật của video gốc"),
               ("narrate", "AI xem hình rồi viết lời đọc (cách cũ)")), choices=("music", "narrate")),
    S("BGM_LINKS", "", "video", "Thư viện nhạc nền (link)", help="Quản lý dễ hơn ở mục 🎵 Nhạc nền.",
      validate=_bgm_links, live=True),
    S("VOICE_BED", "on", "video", "Nhạc lót dưới lời đọc",
      help="Video có lời đọc: lót một bài trong thư viện 🎵 Nhạc nền thật nhỏ dưới giọng, tự hạ khi đang đọc hoặc khi "
           "tiếng gốc to; video gốc đã có nhạc thì không lót thêm. CHỈ dùng nhạc miễn phí bản quyền (vd YouTube Audio "
           "Library): nhạc có bản quyền bị YouTube nhận diện, TikTok / Facebook tắt tiếng.",
      options=(("on", "Bật (bản 1.0.0)"), ("off", "Tắt")), choices=("on", "off")),
    S("BGM_ORIG", "off", "video", "Tiếng gốc khi chèn nhạc",
      options=(("off", "Chỉ nhạc mới (mặc định): bỏ tiếng gốc, tránh hai bản nhạc chồng nhau"),
               ("low", "Giữ tiếng gốc nhỏ dưới nhạc (video ASMR: tiếng xèo, tiếng dao)"),
               ("keep", "Giữ tiếng gốc to, nhạc mới nằm dưới và tự nhỏ lại khi tiếng gốc to")),
      choices=("off", "low", "keep")),
    S("OST_AUTO", "0", "video", "AI đề xuất đoạn giữ tiếng gốc", help="Tiếng xèo, câu cảm thán…",
      options=(("0", "Tắt (chỉ khi bạn chọn trong màn duyệt)"), ("1", "AI được đề xuất đoạn giữ tiếng gốc")),
      choices=ON_OFF),
    S("SUB_TIMING", "spread", "video", "Canh thời gian phụ đề",
      options=(("spread", "Theo khoảng có tiếng của giọng (mặc định, lệch ~70ms, không gọi mạng)"),
               ("stt", "Nghe lại bằng STT CapCut (lệch ~85ms, chậm thêm 7-11 giây)")), choices=("spread", "stt")),
    S("REVIEW_SCRIPT", "0", "video", "Luôn dừng cho duyệt kịch bản",
      options=(("0", "Tắt"), ("1", "Luôn dừng cho duyệt kịch bản")), choices=ON_OFF),
    S("CUT_PROMO", "1", "video", "Cắt quảng cáo cuối video gốc",
      options=(("1", "Bật (cắt quảng cáo sản phẩm của người gốc ở cuối)"), ("0", "Tắt")), choices=ON_OFF),
    S("QUALITY", "balanced", "video", "Chất lượng dựng",
      options=(("fast", "Nhanh: luôn 720p, superfast (nhanh ~2.3 lần)"),
               ("balanced", "Cân bằng (mặc định): 1080p, máy chậm thì tự ra 720p"),
               ("high", "Chuẩn TVC: luôn 1080p, preset medium (chậm ~2x)")), choices=("fast", "balanced", "high")),
    # ---- bản 42: phong cách remake, bài đăng có công thức, nhận diện kênh; bản 43: mặc định = bộ «Bếp Đong Đủ» ----
    S("REMAKE_STYLE", "strong", "style", "Phong cách dựng lại",
      help="Nhẹ: đổi cỡ khung, bỏ thời gian chết, tua nhanh đoạn chờ, màu kênh. Mạnh: thêm điểm danh nguyên liệu, "
           "tóm tắt cuối, đảo thứ tự có luật (các bước nấu luôn đúng thứ tự).",
      options=(("off", "Tắt (dựng như bản gốc, như trước)"), ("light", "Nhẹ: đổi nhịp, khung hình, giữ thứ tự"),
               ("strong", "Mạnh: thêm đảo thứ tự có kiểm soát (nấu ăn / review)")), choices=("off", "light", "strong")),
    S("POST_RECIPE", "1", "style", "Công thức trong bài đăng",
      help="Nguyên liệu + cách làm; chỉ in định lượng đối chiếu được với lời gốc (cần key Gemini).",
      options=(("0", "Tắt"), ("1", "Bật (video nấu ăn)")), choices=ON_OFF),
    S("TIPS", "video", "style", "Mẹo hay",
      options=(("off", "Tắt"), ("post", "Trong bài đăng"), ("video", "Bài đăng + thẻ «💡 MẸO» trên video")),
      choices=("off", "post", "video")),
    S("QTY_CHECK", "strict", "style", "Định lượng không đối chiếu được",
      options=(("strict", "Chặt: bỏ số, ghi «vừa đủ»; số mâu thuẫn thì không tự đăng"),
               ("soft", "Mềm: in kèm «(ước lượng)», chỉ ghi chú")), choices=("strict", "soft")),
    S("TIME_SKIP_LABEL", "auto", "style", "Chữ ở đoạn tua nhanh",
      options=(("auto", "Tự động: «⏩ 30 PHÚT SAU» khi lời gốc có thời gian, không thì chỉ ⏩"),
               ("icon", "Chỉ biểu tượng ⏩"), ("off", "Tắt")), choices=("auto", "icon", "off")),
    S("SHORT_VERSION", "on", "style", "Thêm bản ngắn ≤ 90 giây (Reels)",
      help="Video nấu ăn dài hơn 90 giây: thêm một bản giữ các bước chính + điểm danh nguyên liệu (cần key Gemini).",
      options=(("off", "Tắt"), ("on", "Bật: ra thêm «Bản ngắn» sau bản đầy đủ")), choices=("off", "on")),
    S("BRAND_COLOR", "#F28C28", "brand", "Màu kênh", help="Mã màu #RRGGBB: chữ đang đọc của phụ đề, khung chữ, thẻ, ảnh bìa. "
      "Nhập 0 = vàng như cũ.", validate=_brand_color),
    S("BRAND_LOOK", "auto", "brand", "Tông màu video",
      help="Tự động (bản 1.0.0): đo màu video gốc rồi chọn — ánh sáng lạnh / ám xanh → Ấm mộc; tối, màu nhạt hoặc đã "
           "ám vàng → Tươi sáng; còn lại → Gốc. Ảnh bìa cùng tông với video.",
      options=(("auto", "Tự động theo từng video"), ("goc", "Gốc (công thức màu hiện tại)"), ("am_moc", "Ấm mộc"),
               ("tuoi_sang", "Tươi sáng"), ("dien_anh", "Điện ảnh")),
      choices=("auto", "goc", "am_moc", "tuoi_sang", "dien_anh")),
    S("BRAND_GRAIN", "0", "brand", "Hạt phim nhẹ", help="Chỉ ở bản 1080p (720p hạt bị nén thành khối).",
      options=_ONOFF_OPTS, choices=ON_OFF),
    S("BRAND_TAGLINE", "Đong đủ, nấu là ngon", "brand", "Câu dưới tên kênh (màn kết)", help="Nhập 0 = không có.", validate=_max_len("BRAND_TAGLINE", 60)),
    S("BRAND_CLOSE", "Đong đủ là ngon, cả nhà nấu thử nha", "brand", "Câu chốt đọc cuối video",
      help="Ví dụ «Chúc cả nhà nấu ngon nha». Nhập 0 = không có. Không kêu gọi đăng ký/theo dõi (bị bỏ qua nếu có).",
      validate=_max_len("BRAND_CLOSE", 120)),
    S("BRAND_SERIES", "Hôm nay nấu gì", "brand", "Tên loạt video (ảnh bìa)", help="Ví dụ «CÔNG THỨC CHUẨN»: ảnh bìa ghi kèm số tập. Nhập 0 = không có.",
      validate=_max_len("BRAND_SERIES", 40)),
    S("BRAND_STING", "1", "brand", "Âm hiệu màn kết", help="Tệp âm thanh ≤1,5 giây tải lên ở cuối trang cài đặt; chưa tải thì dùng âm hiệu Bếp Đong Đủ.",
      options=_ONOFF_OPTS, choices=ON_OFF),
    S("COVER_TEMPLATE", "kenh", "brand", "Mẫu ảnh bìa",
      options=(("goc", "Gốc"), ("kenh", "Theo kênh: vạch màu kênh, logo, tên loạt + số tập")), choices=("goc", "kenh")),
    S("SOURCE_CREDIT", "0", "video", "Ghi nguồn vào nội dung đăng",
      options=(("0", "Tắt (mặc định)"), ("1", "Bật (ghi nguồn + Giọng đọc AI)")), choices=ON_OFF),
    S("ALLOWED_SOURCES", "", "telegram", "Kênh gốc được phép", help="Tên kênh/uploader; trống = mọi kênh.",
      validate=_max_len("ALLOWED_SOURCES", 2000), live=True),
    S("ALLOWED_USERS", "", "telegram", "Người được dùng bot Telegram",
      help="chat_id cách nhau dấu phẩy (nhắn bot để biết id). Trống = bot từ chối tất cả.", need="should",
      validate=_id_list("ALLOWED_USERS"), reauth=True, live=True),
    S("ADMIN_CHAT_ID", "", "telegram", "Chat nhận cảnh báo", help="Trống = người đầu tiên trong danh sách trên.",
      validate=_id_list("ADMIN_CHAT_ID"), reauth=True, live=True),
    # ---- bản 1.0.0: thông báo cho job làm trên web / tự đăng; kênh Zoom ----
    S("NOTIFY_JOBS", "1", "telegram", "Báo job làm trên web / tự đăng",
      help="Video xong (kèm link tải), lỗi, chờ duyệt kịch bản: gửi về Telegram (chat nhận cảnh báo) và Zoom (người "
           "nhận thông báo). Trước đây chỉ job gửi từ Telegram mới được báo.",
      options=(("1", "Bật"), ("0", "Tắt")), choices=ON_OFF, live=True),
    S("ZOOM_CLIENT_ID", "", "api", "Zoom · Client ID", help="Zoom Marketplace → app General → App Credentials.",
      link="https://marketplace.zoom.us/develop/create", need="optional", validate=_max_len("ZOOM_CLIENT_ID", 200),
      live=True),
    S("ZOOM_CLIENT_SECRET", "", "api", "Zoom · Client Secret", need="optional", secret=True, reauth=True, live=True),
    S("ZOOM_BOT_JID", "", "api", "Zoom · Bot JID", help="Features → Surface → Team Chat → Chatbot (dạng …@xmpp.zoom.us). "
      "Bot endpoint URL đặt là <Link web của bot>/zoom/events.", need="optional",
      validate=_max_len("ZOOM_BOT_JID", 200), live=True),
    S("ZOOM_SECRET_TOKEN", "", "api", "Zoom · Secret Token", help="Features → Access → Secret Token (kiểm chữ ký tin Zoom gửi về).",
      need="optional", secret=True, reauth=True, live=True),
    S("ZOOM_APP_TYPE", "user", "zoom", "Kiểu app Zoom",
      options=(("user", "User-managed (mặc định: app cá nhân)"), ("admin", "Admin-managed (app cả tài khoản)")),
      choices=("user", "admin"), live=True),
    S("ZOOM_ALLOWED_USERS", "", "zoom", "Người được dùng bot Zoom",
      help="JID (…@xmpp.zoom.us) cách nhau dấu phẩy; nhắn bot «id» để biết JID. Trống = bot từ chối tất cả.",
      validate=_max_len("ZOOM_ALLOWED_USERS", 2000), reauth=True, live=True),
    S("ZOOM_ADMIN", "", "zoom", "Người nhận thông báo trên Zoom",
      help="JID nhận thông báo job web / tự đăng và phiên bản mới. Trống = người đầu tiên ở ô trên (phải nhắn bot một "
           "lần để bot biết nơi gửi).", validate=_max_len("ZOOM_ADMIN", 200), reauth=True, live=True),
    S("AUTO_POST", "0", "auto", "Tự động đăng",
      options=(("0", "Tắt"), ("1", "Bật: tự lấy video từ kênh nguồn, làm lại, đăng Fanpage")), choices=ON_OFF, live=True),
    S("AUTO_SOURCES", "", "auto", "Kênh Facebook nguồn", help="Thêm/bỏ ở mục Tự động đăng.",
      validate=_each(sources.normalize_source), live=True),
    S("AUTO_TIKTOK_SOURCES", "", "auto", "Kênh TikTok nguồn", help="Thêm/bỏ ở mục Tự động đăng.",
      validate=_each(sources.normalize_tiktok), live=True),
    S("AUTO_PLATFORMS", "facebook,tiktok", "auto", "Nguồn đang tick", help="Sửa bằng ô tick ở mục Tự động đăng.",
      validate=_platforms, live=True),
    S("AUTO_PER_DAY", "2", "auto", "Số video mỗi ngày", help="1-4.", validate=_per_day, live=True),
    S("AUTO_TIMES", "", "auto", "Giờ đăng cố định",
      help="Trống = bot tự chọn giờ vàng theo lượt xem; hoặc 11:30,20:30.",
      validate=_regex(r"\s*([01]\d|2[0-3]):[0-5]\d(\s*,\s*([01]\d|2[0-3]):[0-5]\d)*\s*",
                      "AUTO_TIMES dạng 11:30,20:30 (giờ Việt Nam)"), live=True),
    S("AUTO_POST_VERSION", "short", "auto", "Bản đăng khi có bản ngắn",
      help="Chỉ có tác dụng khi bật «Thêm bản ngắn».",
      options=(("short", "Bản ngắn (Reels, lan toả tốt hơn)"), ("full", "Bản đầy đủ")), choices=("short", "full")),
    S("AUTO_SKIP_ADS", "1", "auto", "Bỏ video quảng cáo của nguồn",
      help="Khóa học, tiệc, tuyển dụng… (theo tiêu đề) và video AI xác định không phải công thức.",
      options=(("1", "Bật"), ("0", "Tắt")), choices=ON_OFF),
    S("AUTO_MIN_HEIGHT", "540", "auto", "Độ nét tối thiểu của nguồn", help="Chiều cao (px); 0 = không lọc.",
      validate=_digits("AUTO_MIN_HEIGHT", "AUTO_MIN_HEIGHT là số điểm ảnh, vd 540")),
    S("AUTO_MODE", "cook", "auto", "Chế độ làm lại", options=(("cook", "Nấu ăn"), ("review", "Review")),
      choices=("cook", "review"), live=True),
    S("FB_PAGE_ID", "", "api", "ID Fanpage",
      help="Dãy số id của Fanpage (Trang → Giới thiệu → ID trang). Chỉ cần khi tự động đăng.",
      link="https://www.facebook.com/help/1503421039731588", need="optional",
      validate=_digits("FB_PAGE_ID", "FB_PAGE_ID là dãy số id của Fanpage"), reauth=True, live=True),
    S("FB_PAGE_TOKEN", "", "api", "Token Fanpage",
      help="Page access token dài hạn, quyền pages_manage_posts + pages_read_engagement + pages_show_list. Bấm «Thử "
           "token Fanpage» ở mục Tự động đăng để kiểm.", link="https://developers.facebook.com/tools/explorer/",
      need="optional", secret=True, reauth=True, live=True),
    # ----- 🧸 truyện bé (kênh «Kể Bé Nghe», docs/KE_HOACH_KIDS_STORY.md) -----
    S("KIDS_AUTO", "0", "kids", "Tự động làm + đăng truyện bé",
      options=(("0", "Tắt (làm tay bằng nút «Làm 1 truyện ngay»)"),
               ("1", "Bật: làm trước cho các khung giờ trong 36 giờ tới, đăng Fanpage truyện bé")),
      choices=ON_OFF, live=True),
    S("KIDS_TIMES", "08:00,19:00", "kids", "Giờ đăng truyện bé", help="Giờ Việt Nam, cách nhau dấu phẩy.",
      validate=_regex(r"\s*([01]\d|2[0-3]):[0-5]\d(\s*,\s*([01]\d|2[0-3]):[0-5]\d)*\s*",
                      "KIDS_TIMES dạng 08:00,19:00 (giờ Việt Nam)"), live=True),
    S("KIDS_FB_PAGE_ID", "", "api", "ID Fanpage truyện bé",
      help="Fanpage RIÊNG của kênh truyện bé (khác Fanpage kênh bếp). Lấy id + token như FB_PAGE_TOKEN: me/accounts liệt "
           "kê cả hai Page.", validate=_digits("KIDS_FB_PAGE_ID", "KIDS_FB_PAGE_ID là dãy số id của Fanpage"),
      reauth=True, live=True),
    S("KIDS_FB_PAGE_TOKEN", "", "api", "Token Fanpage truyện bé",
      help="Page access token dài hạn của Fanpage truyện bé. Bấm «Thử token» ở mục 🧸 Truyện bé để kiểm.",
      link="https://developers.facebook.com/tools/explorer/", secret=True, reauth=True, live=True),
    S("KIDS_VOICE", "Thục Đoan", "kids", "Giọng kể (VieNeu miền Nam)",
      options=(("Thục Đoan", "Thục Đoan (kể chuyện)"), ("Mỹ Duyên", "Mỹ Duyên (đọc truyện, chậm, ấm)"),
               ("Kim Thanh", "Kim Thanh (đọc truyện)"), ("Thùy Dung", "Thùy Dung (rõ, nhanh)")),
      validate=_max_len("KIDS_VOICE", 40)),
    S("KIDS_MULTI_VOICE", "1", "kids", "Giọng riêng cho từng nhân vật",
      help="Lời thoại trong «…» đọc bằng giọng VieNeu của nhân vật (data.json: Thỏ Trắng Kim Thanh, Gấu Nâu Thái Sơn, Mèo Cam "
           "Thùy Dung, Sóc Nhỏ Mỹ Duyên); lời kể vẫn là KIDS_VOICE. 0 = một giọng đọc hết.",
      options=(("1", "Bật (mặc định)"), ("0", "Tắt: một giọng")), choices=("0", "1")),
    S("KIDS_IMAGE_MODEL", "schnell", "kids", "Model vẽ ảnh",
      options=(("schnell", "FLUX.1-schnell 4-bit (Apache-2.0, 4 bước — mặc định)"),
               ("zimage", "Z-Image-Turbo (Apache-2.0, 8 bước — đo 19/11: ảnh ĐEN trên T4 vì fp16 tràn số, chưa dùng được)")),
      choices=("schnell", "zimage")),
    S("KIDS_IMAGE_CHECK", "1", "kids", "Kiểm ảnh hai đầu / nhân vật nhân đôi",
      options=(("1", "Bật: OWLv2 đếm đầu nhân vật sau khi vẽ, thừa đầu thì vẽ lại cảnh (≈ +35 giây mỗi lần, mặc định)"),
               ("0", "Tắt")), choices=("1", "0")),
    S("KIDS_LLM_PROVIDER", "auto", "kids", "Ai viết truyện",
      options=(("auto", "Gemini trên Botkeep (1 lượt gọi/truyện, +1 mỗi lần viết lại); lỗi thì Qwen trên Kaggle — mặc định"),
               ("gemini", "Chỉ Gemini (không có key / lỗi thì job lỗi)"),
               ("qwen", "Chỉ Qwen trên Kaggle (không dùng Gemini)")), choices=("auto", "gemini", "qwen")),
    S("KIDS_GEMINI_MODEL", "gemini-4-hydrogen", "kids", "Model Gemini viết nháp truyện bé",
      help="Gemini 4 Hydrogen (free tier, ra 12/11) viết hay hơn flash-lite rõ; hạn mức free mỗi ngày của model lớn nhỏ, chỉ "
           "dùng cho bản nháp (1 lượt/truyện). Trống = GEMINI_MODEL như kênh bếp. Quá tải / hết lượt thì model sửa viết thay.",
      validate=_max_len("KIDS_GEMINI_MODEL", 60)),
    S("KIDS_GEMINI_REVISE_MODEL", "gemini-3.5-flash-lite", "kids", "Model Gemini sửa / soát truyện bé",
      help="Viết lại theo lỗi bộ kiểm và lượt tự soát (hạn mức free rộng, 2-3 giây). Trống = cùng model viết nháp.",
      validate=_max_len("KIDS_GEMINI_REVISE_MODEL", 60)),
    S("KIDS_GEMINI_THINKING", "low", "kids", "Mức suy nghĩ của Gemini 4 khi viết truyện",
      options=(("low", "low — 3-10 giây, đủ cho truyện 8 cảnh (mặc định)"), ("medium", "medium — kỹ hơn, chậm hơn"),
               ("high", "high — mặc định của Google, chậm và tốn hạn mức token")), choices=("low", "medium", "high")),
    S("KIDS_LLM_PATTERN", "*Qwen3.6-35B-A3B*Q4_K_M*.gguf", "kids", "File model chữ (.gguf)",
      help="Mẫu tên file tìm trong /kaggle/input; không thấy thì tải từ repo Hugging Face bên dưới.",
      validate=_max_len("KIDS_LLM_PATTERN", 200)),
    S("KIDS_LLM_HF", "lmstudio-community/Qwen3.6-35B-A3B-GGUF", "kids", "Repo Hugging Face của model chữ",
      validate=_regex(r"[\w.\-]+/[\w.\-]+", "KIDS_LLM_HF dạng user/repo trên Hugging Face")),
    S("KIDS_LLM_THINK", "1200", "kids", "Model chữ suy nghĩ trước khi viết (token)",
      help="0 = tắt. Suy nghĩ giúp truyện mạch lạc, đủ từ tiếng Anh hơn; ~60 token/giây trên 2 T4 (1200 token ≈ +20 giây "
           "mỗi lượt).", validate=_regex(r"\d{1,5}", "KIDS_LLM_THINK là số token (0 = tắt)")),
    S("KIDS_KAGGLE_SOURCES", "", "kids", "Nguồn gắn vào phiên Kaggle",
      help="Trống = notebook rmvideo-kids-models (lệnh kids_setup tạo: llama-server đã biên dịch + FLUX 4-bit). Hoặc "
           "notebook:user/slug, dataset:user/slug cách nhau dấu phẩy.", validate=_kaggle_sources),
    S("KIDS_CHANNEL_NAME", "Kể Bé Nghe", "kids", "Tên kênh truyện bé", validate=_max_len("KIDS_CHANNEL_NAME", 60)),
    S("KIDS_WORLD_NAME", "Đồi Cỏ Non", "kids", "Tên thế giới truyện", help="Nơi bốn bạn nhỏ sống; vào lời nhắc và màn mở đầu.",
      validate=_max_len("KIDS_WORLD_NAME", 60)),
    S("KIDS_TAGLINE", "Mỗi ngày một chuyện nhỏ ở Đồi Cỏ Non, học vài từ tiếng Anh", "kids",
      "Câu giới thiệu (màn kết + bài đăng)", validate=_max_len("KIDS_TAGLINE", 160)),
    S("KIDS_HASHTAGS", "#kebenghe,#doiconon,#truyenchobe,#hoctienganhchobe,#kidsstory", "kids", "Hashtag cố định", help="Cách nhau dấu phẩy.",
      validate=_max_len("KIDS_HASHTAGS", 300), live=True),
    S("KIDS_FX", "full", "kids", "Hiệu ứng động cho truyện bé",
      options=(("full", "Đầy đủ (mặc định): Ken Burns mạnh + nảy/rung khi có tiếng động, hạt bokeh, whoosh + ting"),
               ("motion", "Chỉ chuyển động + phụ đề bật lên (không hạt, không tiếng động)"),
               ("off", "Tắt: Ken Burns nhẹ như bản 1.1.0")), choices=("full", "motion", "off")),
    S("KIDS_BGM_LINKS", "", "kids", "Link nhạc nền trẻ em",
      help="Link mp3 / YouTube / TikTok (Kaggle tự tải), cách nhau dấu phẩy; hoặc chép file nhạc vào assets/kids_bgm/ "
           "(cần PUBLIC_URL). Chỉ dùng nhạc được phép dùng thương mại.", validate=_bgm_links, live=True),
    S("KIDS_BGM", "auto", "kids", "Nhạc nền truyện bé",
      options=(("auto", "File trong assets/kids_bgm hay KIDS_BGM_LINKS nếu có, không thì Lyria RealTime tự tạo kho (mặc định)"),
               ("lyria", "Chỉ kho Lyria RealTime (Gemini API, free; 3 màu theo khung truyện, 3 bài mỗi màu)"),
               ("files", "Chỉ file / link của mình"), ("off", "Không nhạc nền")),
      choices=("auto", "lyria", "files", "off")),
)
del S

BY_KEY: dict[str, Setting] = {s.key: s for s in SCHEMA}

# Nhóm hiển thị trên web (thứ tự này): mã nhóm -> tiêu đề. Nhóm "api" mở sẵn, các nhóm khác thu gọn.
GROUPS: list[tuple[str, str, str]] = [
    ("api", "🔑 Tài khoản & API", "Điền key một lần. Bắt buộc: Gemini. Nên có: Kaggle (GPU miễn phí). Chỉ khi cần: Groq, "
            "OpenRouter, Fanpage, cookies, Zoom."),
    ("voice", "🗣️ Giọng đọc", "Giọng, tốc độ, cách nói."),
    ("video", "🎬 Video & lời", "Chất lượng, đoạn mở đầu, phụ đề, tiếng gốc."),
    ("style", "✨ Phong cách remake & bài đăng", "Nhịp dựng khác bản gốc, công thức và mẹo trong bài đăng, đối chiếu "
              "định lượng."),
    ("brand", "🎨 Nhận diện kênh", "Màu, tông, màn kết, âm hiệu, ảnh bìa: cố định để người xem nhận ra kênh."),
    ("kaggle", "☁️ Kaggle", "Phần nặng chạy trên GPU Kaggle (cần tài khoản ở nhóm Tài khoản & API)."),
    ("auto", "🤖 Tự động đăng", "Bật/tắt và lịch. Kênh nguồn và link dán tay: sửa ngay trong mục 🤖 Tự động đăng bên dưới."),
    ("kids", "🧸 Truyện bé", "Kênh «Kể Bé Nghe»: model trên Kaggle tự viết truyện, vẽ 8 cảnh, đọc giọng miền Nam, dựng rồi "
             "đăng Fanpage riêng (id + token Fanpage truyện bé điền ở nhóm 🔑 Tài khoản & API). Chạy lệnh console kids_setup "
             "một lần để chuẩn bị model."),
    ("telegram", "💬 Telegram & thông báo", "Ai được dùng bot qua Telegram, báo job làm trên web / tự đăng."),
    ("zoom", "🎥 Zoom", "Gửi link cho bot và nhận thông báo qua Zoom Team Chat (chỉ chữ + link tải, không gửi file "
             "video). Khoá Zoom (Client ID / Secret, Bot JID, Secret Token) điền ở nhóm 🔑 Tài khoản & API."),
    ("advanced", "🔧 Nâng cao", "Tên model, thứ tự bóc lời, từ điển… thường không cần đổi."),
]

# Thứ tự hiện trên web (trong mỗi nhóm theo thứ tự này).
DISPLAY_ORDER: tuple[str, ...] = (
    "GEMINI_API_KEY", "KAGGLE_USERNAME", "KAGGLE_API_TOKEN", "PUBLIC_URL", "FB_PAGE_ID", "FB_PAGE_TOKEN", "GROQ_API_KEY",
    "OPENROUTER_API_KEY", "YTDLP_COOKIES", "YTDLP_PROXY", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET", "ZOOM_BOT_JID",
    "ZOOM_SECRET_TOKEN", "TTS_PROVIDER", "VIENEU_VOICE_FEMALE", "VIENEU_VOICE_MALE",
    "CAPCUT_VOICE_FEMALE", "CAPCUT_VOICE_MALE", "TTS_RATE", "TTS_PITCH", "SPEECH_STYLE", "VOICE_HUMANIZE", "TTS_OFFLINE",
    "QUALITY", "TEASER", "COVER", "SUB_STYLE", "SUB_TIMING", "REMAKE_STYLE", "SHORT_VERSION", "POST_RECIPE", "TIPS",
    "QTY_CHECK",
    "TIME_SKIP_LABEL", "BRAND_NAME", "BRAND_COLOR", "BRAND_LOOK", "BRAND_GRAIN", "BRAND_TAGLINE", "BRAND_CLOSE",
    "BRAND_SERIES", "BRAND_STING", "COVER_TEMPLATE", "BRAND_LOGO", "BRAND_INTRO", "VOICE_SEPARATION",
    "OST_AUTO", "CUT_PROMO", "REVIEW_SCRIPT", "NO_SPEECH", "BGM_ORIG", "BGM_LINKS", "VOICE_BED", "SCENE_ANALYSIS", "TEXT_DETECT", "TEXT_INPAINT",
    "SOURCE_CREDIT", "KAGGLE_USE", "KAGGLE_ANALYZE", "KAGGLE_ASR", "AUTO_POST", "AUTO_MODE", "AUTO_PER_DAY", "AUTO_TIMES",
    "AUTO_POST_VERSION", "AUTO_SKIP_ADS", "AUTO_MIN_HEIGHT",
    "AUTO_PLATFORMS", "AUTO_SOURCES", "AUTO_TIKTOK_SOURCES", "ALLOWED_USERS", "ADMIN_CHAT_ID", "NOTIFY_JOBS",
    "ALLOWED_SOURCES", "ZOOM_APP_TYPE", "ZOOM_ALLOWED_USERS", "ZOOM_ADMIN",
    "KIDS_AUTO", "KIDS_TIMES", "KIDS_FB_PAGE_ID", "KIDS_FB_PAGE_TOKEN", "KIDS_VOICE", "KIDS_MULTI_VOICE", "KIDS_IMAGE_MODEL", "KIDS_IMAGE_CHECK",
    "KIDS_LLM_PROVIDER", "KIDS_GEMINI_MODEL", "KIDS_GEMINI_REVISE_MODEL", "KIDS_GEMINI_THINKING",
    "KIDS_LLM_PATTERN", "KIDS_LLM_HF", "KIDS_LLM_THINK", "KIDS_KAGGLE_SOURCES", "KIDS_CHANNEL_NAME", "KIDS_WORLD_NAME",
    "KIDS_TAGLINE",
    "KIDS_HASHTAGS", "KIDS_FX", "KIDS_BGM_LINKS", "KIDS_BGM",
    "GEMINI_MODEL", "GEMINI_FALLBACK_MODELS", "GEMINI_ASR_MODEL", "OPENROUTER_MODEL", "GROQ_WHISPER_MODEL", "ASR_ORDER",
    "ASR_VOCAB",
)
assert sorted(DISPLAY_ORDER) == sorted(BY_KEY), "DISPLAY_ORDER phải liệt kê đủ mọi khoá trong SCHEMA"

SECRET_KEYS = frozenset(s.key for s in SCHEMA if s.secret)
REAUTH_KEYS = frozenset(s.key for s in SCHEMA if s.reauth)
# khoá KHÔNG gửi sang notebook Kaggle: bí mật + tài khoản/quyền/tự đăng (Kaggle không cần, lộ ra là thiệt)
SNAPSHOT_EXCLUDE_PREFIXES = ("KAGGLE_", "FB_", "AUTO_", "ALLOWED_", "ADMIN_", "ZOOM_", "NOTIFY_", "KIDS_")


# ---------------- lưu trữ ----------------

def read_overrides(path: str) -> dict[str, str]:
    """settings.json -> {khoá: giá trị}. Không có tệp: {}; tệp hỏng: ValueError/OSError cho caller quyết."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("settings.json không phải object JSON")
    return {str(k): str(v) for k, v in data.items()}


def write_overrides(path: str, data: Mapping[str, str]) -> None:
    """Ghi atomically (tệp tạm + os.replace), tệp chứa key API nên tạo ra đã là 0600, không có lúc 0644."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dict(data), f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)                                  # tệp tạm còn sót từ trước mang quyền cũ: ép lại
    os.replace(tmp, path)


def merge_updates(current: Mapping[str, str], updates: Mapping[str, object]) -> dict[str, str]:
    """Áp các thay đổi từ web lên bản đè hiện có. Giá trị rỗng = không đổi; "-" = xoá (quay về biến môi trường).
    Khoá lạ bị bỏ qua. Ném ValueError (câu báo cho người dùng) ở giá trị sai đầu tiên; không ghi gì khi lỗi."""
    out = dict(current)
    for k, raw in (updates or {}).items():
        s = BY_KEY.get(k)
        if s is None:
            continue
        v = str(raw).strip()
        if v == "-":
            out.pop(k, None)
        elif v:
            out[k] = s.check(v)
    return out


def changed_reauth_keys(effective: Mapping[str, str], overrides: Mapping[str, str],
                        updates: Mapping[str, object]) -> list[str]:
    """Các khoá cần nhập lại mật khẩu mà `updates` THẬT SỰ đổi (gửi lại đúng giá trị cũ thì không hỏi)."""
    out = []
    for k, raw in (updates or {}).items():
        s = BY_KEY.get(k)
        if s is None or not s.reauth:
            continue
        v = str(raw).strip()
        if not v:
            continue
        if v == "-":
            if k in overrides:
                out.append(k)
            continue
        try:
            v = s.check(v)
        except ValueError:
            continue                                      # giá trị sai: báo lỗi hợp lệ ở bước lưu, không hỏi mật khẩu
        if v != effective.get(k, ""):
            out.append(k)
    return out


def mask(value: str) -> str:
    """Hiển thị bí mật: 'AIza•••ijk' / '(đã đặt)'."""
    return (value[:4] + "•••" + value[-3:]) if len(value) > 10 else ("(đã đặt)" if value else "")


def snapshot(values: Mapping[str, str]) -> dict[str, str]:
    """Cài đặt gửi sang notebook Kaggle: KHÔNG bao giờ có bí mật hay tài khoản, dù ai thêm khoá mới sau này."""
    return {s.key: values[s.key] for s in SCHEMA
            if not s.secret and not s.key.startswith(SNAPSHOT_EXCLUDE_PREFIXES)}


def describe(effective: Mapping[str, str], overridden: Iterable[str], pending: Iterable[str] = ()) -> dict[str, dict]:
    """Cho web: giá trị (bí mật thì che), đã đè chưa, lựa chọn, nhóm, nhãn, chú thích, cần nhập lại mật khẩu không,
    đang chờ job hiện tại xong mới áp dụng không."""
    over, wait = set(overridden), set(pending)
    out: dict[str, dict] = {}
    for k in DISPLAY_ORDER:
        s = BY_KEY[k]
        v = effective.get(k, "")
        d: dict = {"value": mask(v) if s.secret else v, "set": bool(v), "overridden": k in over, "group": s.group,
                   "label": s.label, "help": s.help, "link": s.link, "need": s.need}
        if s.options:
            d["options"] = [list(o) for o in s.options]
        if s.reauth:
            d["reauth"] = True
        if k in wait:
            d["pending"] = True
        out[k] = d
    return out
