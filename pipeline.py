"""Mặt tiền tương thích của bộ điều phối — mã thật nằm trong gói rmvideo/app/flow/ (Phase 4):

    dispatch    process(), run_job(): chọn luồng theo chế độ, huỷ việc nền khi job dừng, giao kết quả
    remake      video có lời (nấu ăn / review): phân tích -> quyết định -> AI viết lời -> duyệt -> đọc giọng + dựng
    analysis    bóc lời + phân tích hình trên Kaggle (cache theo video), dò chữ OCR
    teaser      đoạn mở đầu cảnh món đã xong
    music       video không lời chèn nhạc nền; lấy nhạc của job
    photo       📸 ảnh + nhạc
    long_video  ✂️ cắt phần / ✨ đoạn hay, gom một phiên Kaggle
    delivery    giao kết quả (outputs/, Telegram)
    backends    dựng 🎵 / 📸 trên Kaggle
    common      tiện ích dùng chung

Tên cũ (`pipeline.process`, `pipeline.no_speech`, ...) vẫn dùng được. GHI một tên (kể cả monkeypatch trong test) được
chuyển tới đúng module đang dùng tên đó, nên bản vá có tác dụng thật thay vì âm thầm chỉ sửa bản sao ở đây.
"""
import prompt_engine  # noqa: F401  (test vá pipeline.prompt_engine.<hàm>)
import transcribe
from rmvideo import compat
from rmvideo.app.flow import analysis, backends, common, delivery, dispatch, long_video, music, photo, remake, teaser

# tên cũ -> (module thật, tên trong module đó)
_ROUTES = {
    "process": (dispatch, "process"), "run_job": (dispatch, "run_job"),
    "deliver": (delivery, "deliver"), "OUT_DIR": (delivery, "OUT_DIR"),
    "_gather_all": (common, "gather_all"), "auto_full": (common, "auto_full"),
    "check_source_allowed": (common, "check_source_allowed"), "final_timeline": (common, "final_timeline"),
    "credit_line": (common, "credit_line"),
    "analyze_remote": (analysis, "analyze_remote"), "kaggle_analysis": (analysis, "kaggle_analysis"),
    "ANALYZE_V": (analysis, "ANALYZE_V"), "_ocr_bands": (analysis, "ocr_bands"),
    "plan_teaser": (teaser, "plan_teaser"),
    "_kaggle_render": (backends, "kaggle_render"),
    "BED_ORIG_VI": (music, "BED_ORIG_VI"), "_pick_bgm": (music, "_pick_bgm"), "_music_only": (music, "_music_only"),
    "_get_music": (music, "_get_music"),
    "process_photo": (photo, "process_photo"),
    "_long_source": (long_video, "_long_source"), "_remake_pieces": (long_video, "_remake_pieces"),
    "_even_pieces": (long_video, "_even_pieces"), "process_split": (long_video, "process_split"),
    "_highlight_images": (long_video, "_highlight_images"), "process_highlights": (long_video, "process_highlights"),
    "AUTO_MIN_FRESH": (remake, "AUTO_MIN_FRESH"), "SEP_MAX_SPEECH": (remake, "SEP_MAX_SPEECH"),
    "SEP_MAX_VIDEO": (remake, "SEP_MAX_VIDEO"), "REEL_MAX": (remake, "REEL_MAX"),
    "NO_SPEECH_WORDS": (remake, "NO_SPEECH_WORDS"), "no_speech": (remake, "no_speech"),
    "promo_cut": (remake, "promo_cut"), "_process": (dispatch, "_process"),
    "transcribe_segments": (transcribe, "transcribe_segments"),
}


compat.install(__name__, _ROUTES)
