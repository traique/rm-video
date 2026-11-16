# Code mẫu chuẩn production (đi kèm docs/DANH_GIA_KIEN_TRUC_PRODUCTION.md, Phần 4)

Module nhỏ, tự chạy được, có test (`tests/test_samples.py`), **chưa nối vào bot** — là đích đến của lộ trình
tái cấu trúc (docs/KE_HOACH_REFACTOR.md). Mẫu nào đã nối vào bot thì chuyển hẳn vào gói `rmvideo/` ở gốc repo và
xoá khỏi đây:

| Module | Thay cho | Điểm khác |
|---|---|---|
| `rmvideo/media/proc.py` | `core_video._run_ffmpeg_full`, `vocal_sep.remove_voice`, `offline.*` (4 bản "spawn + RUNNING + kill") | timeout bắt buộc, giết **cả nhóm** tiến trình (`start_new_session` + `killpg`), stderr đọc có trần, lỗi có kiểu |
| `rmvideo/services/router.py` | 7 vòng "Gemini → OpenRouter → thử lại" trong prompt_engine/cover/highlights | một chỗ: thứ tự dịch vụ, thử lại lỗi tạm, đổi dịch vụ, bóc JSON, ghi số liệu |

Chạy test riêng: `python -m pytest tests/test_samples.py -q`.

Đã chuyển vào bot: `rmvideo/app/settings.py` (Phase 1 — schema cài đặt).
