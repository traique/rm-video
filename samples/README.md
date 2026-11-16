# Code mẫu chuẩn production (đi kèm docs/DANH_GIA_KIEN_TRUC_PRODUCTION.md, Phần 4)

Module nhỏ, tự chạy được, có test (`tests/test_samples.py`), **chưa nối vào bot** — là đích đến của lộ trình
tái cấu trúc (docs/KE_HOACH_REFACTOR.md). Mẫu nào đã nối vào bot thì chuyển hẳn vào gói `rmvideo/` ở gốc repo và
xoá khỏi đây:

| Module | Thay cho | Điểm khác |
|---|---|---|
| `rmvideo/services/router.py` | 7 vòng "Gemini → OpenRouter → thử lại" trong prompt_engine/cover/highlights | một chỗ: thứ tự dịch vụ, thử lại lỗi tạm, đổi dịch vụ, bóc JSON, ghi số liệu |

Chạy test riêng: `python -m pytest tests/test_samples.py -q`.

Đã chuyển vào bot (Phase 1): `rmvideo/app/settings.py` (schema cài đặt), `rmvideo/media/proc.py` (tiến trình con theo job).
