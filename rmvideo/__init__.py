"""rm-video: gói mã đã tái cấu trúc (đọc docs/KE_HOACH_REFACTOR.md).

Hướng phụ thuộc một chiều:  interfaces -> app -> domain ;  app -> media / services.
- domain/   kiểu dữ liệu + quy tắc thuần, không I/O (chạy được cả trên Botkeep lẫn Kaggle)
- app/      điều phối: cài đặt, ngữ cảnh job, hàng đợi, bộ dựng
- media/    chạy ffmpeg / tiến trình con có giới hạn
- services/ tích hợp ngoài (AI, Kaggle, tải video)

Các module ở thư mục gốc (config, pipeline, core_video, ...) vẫn là điểm vào ổn định cho mã cũ, test và notebook
Kaggle; logic dần chuyển vào đây.
"""
