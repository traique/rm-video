# Code mẫu chuẩn production (đi kèm docs/DANH_GIA_KIEN_TRUC_PRODUCTION.md, Phần 4)

Ba module nhỏ, tự chạy được, có test (`tests/test_samples.py`), **chưa nối vào bot** — là đích đến của lộ trình
tái cấu trúc, để đối chiếu khi chuyển dần từng vùng:

| Module | Thay cho | Điểm khác |
|---|---|---|
| `rmvideo/media/proc.py` | `core_video._run_ffmpeg_full`, `vocal_sep.remove_voice`, `offline.*` (4 bản "spawn + RUNNING + kill") | timeout bắt buộc, giết **cả nhóm** tiến trình (`start_new_session` + `killpg`), stderr đọc có trần, lỗi có kiểu |
| `rmvideo/app/settings.py` | `config.EDITABLE` + `config.save_settings` + `web_panel._SETTING_OPTIONS` | một schema: mặc định, lựa chọn, bí mật, kiểm tra; lưu atomically, tạo file đã là 0600; snapshot Kaggle không bao giờ có bí mật |
| `rmvideo/services/router.py` | 7 vòng "Gemini → OpenRouter → thử lại" trong prompt_engine/cover/highlights | một chỗ: thứ tự dịch vụ, thử lại lỗi tạm, đổi dịch vụ, bóc JSON, ghi số liệu |

Chạy test riêng: `python -m pytest tests/test_samples.py -q`.
