"""Bàn phím chọn của bot Telegram: chế độ, thời lượng, cách dùng nhạc, độ dài video ảnh, chất lượng tải."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MODE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🍲 Nấu ăn (giữ ASMR + tăng màu)", callback_data="mode:cook")],
    [InlineKeyboardButton(text="📦 Review sản phẩm (đổi giọng)", callback_data="mode:review")],
    [InlineKeyboardButton(text="🎵 Nhảy/Nhạc (thay nhạc mới + khớp beat)", callback_data="mode:music")],
    [InlineKeyboardButton(text="⬇️ Chỉ tải về (TikTok không logo / FB / YouTube)", callback_data="mode:download")],
])
DUR_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⏱ 30 giây", callback_data="dur:30"),
     InlineKeyboardButton(text="⏱ 60 giây", callback_data="dur:60")],
    [InlineKeyboardButton(text="⏱ 90 giây", callback_data="dur:90"),
     InlineKeyboardButton(text="Giữ nguyên", callback_data="dur:0")],
    [InlineKeyboardButton(text="🎞 Video dài: remake nguyên (≤5 phút)", callback_data="dur:full")],
    [InlineKeyboardButton(text="✂️ Cắt phần 60s", callback_data="dur:split60"),
     InlineKeyboardButton(text="✂️ Cắt phần 90s", callback_data="dur:split90")],
    [InlineKeyboardButton(text="✨ Đoạn hay nhất (AI chọn ≤3 đoạn 30-90s)", callback_data="dur:highlight")],
    [InlineKeyboardButton(text="📝 Bật/tắt duyệt kịch bản trước khi render", callback_data="dur:review")],
])
MIX_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🎯 Thay hẳn nhạc + khớp beat điệu nhảy", callback_data="mix:beat")],
    [InlineKeyboardButton(text="🎶 Làm nhạc nền (giữ tiếng gốc video)", callback_data="mix:bg")],
])
PHOTO_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⚡ Tự động theo nhạc (~12-16s)", callback_data="pdur:0")],
    [InlineKeyboardButton(text="10s", callback_data="pdur:10"), InlineKeyboardButton(text="15s", callback_data="pdur:15"),
     InlineKeyboardButton(text="30s", callback_data="pdur:30"), InlineKeyboardButton(text="60s", callback_data="pdur:60")],
])
PHOTO_ASK_MUSIC = ("🎵 Gửi nhạc cho video: link TikTok (bot lấy bản nhạc gốc của video) / YouTube, "
                   "hoặc gửi thẳng file nhạc (mp3/m4a ≤ 20MB).")
DLQ_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⭐ Tốt nhất (TikTok không logo)", callback_data="dlq:best")],
    [InlineKeyboardButton(text="2160p", callback_data="dlq:2160"),
     InlineKeyboardButton(text="1080p", callback_data="dlq:1080"),
     InlineKeyboardButton(text="720p", callback_data="dlq:720")],
    [InlineKeyboardButton(text="480p", callback_data="dlq:480"),
     InlineKeyboardButton(text="360p", callback_data="dlq:360"),
     InlineKeyboardButton(text="🎵 MP3", callback_data="dlq:mp3")],
])
