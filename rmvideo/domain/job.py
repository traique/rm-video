"""Từ vựng của một job: chế độ, chế độ video dài, trạng thái, nguồn gửi.

StrEnum: so sánh bằng chuỗi cũ (`Mode.COOK == "cook"`), json.dumps ra đúng chuỗi cũ -> jobs.db đang có đọc/ghi
y nguyên, không cần chuyển dữ liệu. Thay cho chuỗi tự do rải trong ~10 tệp (chú thích `mode: "cook" | "review"` cũ
đã sai: còn music/photo/download).
"""
from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    COOK = "cook"              # 🍲 nấu ăn: viết lời mới, tách giọng gốc, đoạn mở đầu món đã xong
    REVIEW = "review"          # 📦 review sản phẩm/quán
    MUSIC = "music"            # 🎵 thay nhạc khớp beat / chèn nhạc nền
    PHOTO = "photo"            # 📸 ảnh + nhạc
    DOWNLOAD = "download"      # ⬇️ chỉ tải về

    @property
    def narrated(self) -> bool:
        """Chế độ có bóc lời + AI viết lời đọc mới."""
        return self in (Mode.COOK, Mode.REVIEW)


class LongMode(StrEnum):
    NONE = ""                  # video thường (≤ 90 giây)
    FULL = "full"              # làm nguyên video ≤ 5 phút
    SPLIT60 = "split60"        # cắt thành phần ~60 giây
    SPLIT90 = "split90"        # cắt thành phần ~90 giây
    HIGHLIGHT = "highlight"    # AI chọn tối đa 3 đoạn hay

    @property
    def split(self) -> bool:
        return self in (LongMode.SPLIT60, LongMode.SPLIT90)

    @property
    def part_len(self) -> int:
        return 90 if self is LongMode.SPLIT90 else 60


class Status(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    REVIEW = "review"          # dừng chờ người dùng duyệt kịch bản
    APPROVED = "approved"      # đã duyệt -> render ở job mới
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Source(StrEnum):
    TELEGRAM = "tg"
    WEB = "web"
    AUTO = "auto"              # 🤖 tự động đăng


ACTIVE = (Status.QUEUED, Status.PROCESSING, Status.REVIEW)   # job còn dở: luôn giữ trong bộ nhớ, chạy tiếp sau restart
NARRATED_MODES = tuple(m.value for m in Mode if m.narrated)
WEB_VIDEO_MODES = (Mode.COOK, Mode.REVIEW, Mode.MUSIC, Mode.DOWNLOAD)   # chế độ nhận từ form video (📸 đi đường ảnh)
