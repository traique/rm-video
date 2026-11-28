"""Kênh truyện thiếu nhi «Kể Bé Nghe» (thế giới Đồi Cỏ Non): truyện ngắn 60-90 giây, 4 nhân vật cố định, một bài học,
3-5 từ tiếng Anh, dựng hoàn toàn trên Kaggle (model chữ llama.cpp + model ảnh diffusers + giọng VieNeu + FFmpeg).

    story     dữ liệu (data.json), chọn đề + chống trùng, kiểm kịch bản bằng mã, chuyển giọng miền Nam — thuần, hai bên
    prompts   lời nhắc cho model chữ, ghép prompt ảnh từ character bible — thuần
    llm       (Kaggle) chạy llama-server, sinh JSON theo schema, viết lại khi bộ kiểm báo lỗi
    images    (Kaggle) sinh 8 ảnh 9:16 bằng FLUX.1-schnell 4-bit / Z-Image-Turbo, phóng lên 1080x1920
    render    (Kaggle) kế hoạch cảnh theo giọng đọc, phụ đề tô màu từ tiếng Anh, thẻ nghĩa, nhạc nền, FFmpeg
    job       (Kaggle) chuỗi chữ -> ảnh -> giọng -> dựng, trả result.json; kiểm video bằng mã (dùng cả hai bên)
Phía Botkeep: rmvideo/app/flow/kids.py (gửi Kaggle, nhận, QA, giao) và kids_auto.py (lịch 2 video/ngày, đăng Fanpage riêng).
"""
