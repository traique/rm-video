# Mã chạy bot cho Botkeep (phiên bản 1.4.0)

Nhánh này **tự đồng bộ** từ nhánh chính `ccr-0f4ff082-rshcy5` sau mỗi lần gộp; nội dung commit ghi rõ lấy từ commit nào.
Chỉ có mã bot cần để chạy (bỏ test, tài liệu, cấu hình CI), nên vừa giới hạn giải nén của Botkeep
(≤ 250 mục, ≤ 20 MB). Không phải bản phát hành mới: số phiên bản chỉ đổi khi bot thật sự đổi.

## Cập nhật bot trên Botkeep
1. Mở file `rm-video-botkeep.zip` ở nhánh này → **Download** (tải file gốc).
2. Botkeep → Files → tải zip lên → **Extract archive** → thư mục đích để `/` → giải nén.
   Zip không có thư mục bọc ngoài nên file vào thẳng chỗ bot đang chạy, không phải chuyển file.
3. Khởi động lại bot. Lên phiên bản mới thì bot tự báo qua Telegram / Zoom.

Dữ liệu của bot (settings.json, jobs.db, outputs/, logo / nhạc đã tải lên) không nằm trong zip nên giải nén đè
không mất gì. Cách khác: «Code → Download ZIP» của nhánh này cũng giải nén được, nhưng GitHub bọc thêm thư mục
`rm-video-botkeep/` nên phải chuyển file ra thư mục gốc.
