# Kế hoạch: đẩy phần nặng sang Kaggle + giọng miền Nam/miền Tây chạy local

Trạng thái: **đã thống nhất, chưa code tích hợp**. Ghi lại để phiên làm việc sau tiếp tục đúng hướng.

## Quyết định của người dùng (12/11/2026)

1. Dùng Kaggle cho **mọi video**, nhưng bước nào Botkeep chạy được thì giữ ở Botkeep (chỉ đẩy phần nặng).
2. Giọng: làm **cả hai** — giọng miền Nam có sẵn của model và giọng **nhân bản** từ đoạn mẫu 5-10 giây (chỉ giọng
   người dùng có quyền dùng: giọng chính họ / người đã đồng ý; không bao giờ nhân bản giọng chủ kênh nguồn).
3. Fanpage **chưa bật kiếm tiền, sau này có thể bật** -> chỉ dùng model cho phép thương mại (không dùng F5-TTS
   tiếng Việt / viXTTS: giấy phép phi thương mại).
4. Đã tạo tài khoản Kaggle + **API token kiểu mới** (Settings -> API Tokens -> Generate New Token; không có
   kaggle.json). Cần kiểm: đã xác minh số điện thoại (bắt buộc để notebook có Internet + GPU).

## Vì sao

Log Botkeep 12/11: video 173s mất 36.9 phút, trong đó render 1674s (máy 19.4 giây máy / giây video 1080p). Tách giọng
gốc (Kim_Vocal_2) bị bỏ qua vì máy chậm. Giọng edge Hoài My là giọng Bắc, không có giọng miền Tây thật.

## Kaggle (thông tin bên thứ ba, chưa có trang chính thức ghi số cố định)

- ~30 giờ GPU/tuần (P100 hoặc 2xT4), phiên tối đa ~12 giờ; giờ cao điểm có thể phải chờ GPU.
- Notebook cần xác minh SĐT mới có Internet. Mỗi lần chạy tốn vài phút khởi động (máy, pip, model).
- Điều khoản: Kaggle dành cho khoa học dữ liệu/ML; dùng làm "máy chạy" cho kênh nội dung có thể bị coi là lạm dụng
  -> **không phụ thuộc hoàn toàn**: Kaggle lỗi / hết giờ / bị khoá thì chạy trên Botkeep như hiện nay.
- Token mới: biến môi trường `KAGGLE_API_TOKEN` (hoặc file `~/.kaggle/access_token`), REST
  `https://www.kaggle.com/api/v1`, header `Authorization: Bearer <token>`.

## Giọng: VieNeu-TTS v3 Turbo

- `pnnbao-ump/VieNeu-TTS-v3-Turbo` (Hugging Face): **Apache-2.0**; FAQ: giọng có sẵn dùng được cho nội dung
  thương mại/kiếm tiền. Giữ thông báo giấy phép + ghi công.
- 8 giọng miền Nam: Adam, Kim Thanh, Minh Triết, Mỹ Duyên, Thái Sơn, Thùy Dung, Thục Đoan, Đức Trí.
  ("miền Nam" chưa chắc đúng chất "miền Tây": dùng giọng nhân bản nếu cần đúng miền Tây.)
- Nhân bản giọng: đoạn mẫu 3-8 giây (được khử nhiễu, cắt <= 8s). Không nhân bản người thật khi chưa được phép.
- CPU chạy được (RTF 0.35-0.6 trên Core i5 đời 12); GPU nhanh hơn nhiều. Không trả mốc từng từ -> phụ đề rải theo
  khoảng có tiếng (đã có, như giọng CapCut).
- Cần thêm: chuẩn hoá chữ trước khi đọc ("100g" -> "một trăm gam", "1/3" -> "một phần ba", "60%"...); kiểm tra lại
  bằng ASR, câu đọc sai thì đọc lại.
- Demo nghe thử: https://huggingface.co/spaces/pnnbao-ump/VieNeu-TTS-v3-Turbo
- Máy thử của Claude không được cài gói `vieneu` (bị chặn tải code bên ngoài) -> thử trên Kaggle.

## Kiến trúc

Botkeep = điều khiển (Telegram, web, hàng đợi, Groq bóc lời, Gemini viết lời, autopost, đăng Fanpage).
Kaggle = thợ chạy theo lô (job nặng):

1. Bot gửi: video nguồn (link -> notebook tự tải; file upload -> Kaggle Dataset riêng tư) + kịch bản có mốc
   (segments) + cài đặt (giọng, chất lượng, phụ đề...).
2. Notebook (code của repo này, đóng gói thành Dataset riêng tư theo phiên bản): đọc giọng VieNeu, tách giọng gốc
   (GPU), dựng 1080p, ảnh bìa -> output.
3. Bot theo dõi trạng thái, tải output về, giao/đăng như hiện nay.
4. Kaggle lỗi / hết hạn mức / quá thời gian -> chạy bước đó trên Botkeep (giọng edge/CapCut như cũ).

Ước tính: video 3 phút ~10-15 phút trên Kaggle (vài phút khởi động). 2 video/ngày ~3 giờ GPU/tuần.

## Các bước

1. **Thử trên Kaggle (chưa đụng code bot)**: notebook đo thời gian thật: 8 giọng miền Nam đọc kịch bản mì xào bò
   (lấy trong log, xem `docs/` hoặc CHANGELOG), ghép vào video để người dùng chọn giọng; 1 mẫu giọng nhân bản nếu
   có đoạn ghi âm được phép; tách giọng gốc GPU; dựng 1080p; thử Kaggle tải video Facebook (nếu bị chặn: Botkeep
   tải rồi đẩy lên Dataset).
2. **Tích hợp**: cài đặt `KAGGLE_API_TOKEN` (bí mật), `KAGGLE_USERNAME`, `KAGGLE_MODE` (tắt / phần nặng / mọi
   video), `VOICE_ENGINE` (edge / capcut / vieneu), `VIENEU_VOICE`, file giọng mẫu nhân bản; worker gửi/theo dõi
   job Kaggle; lùi về Botkeep khi lỗi; test với máy chủ Kaggle API giả.

## Để phiên sau dùng được token

Thêm token vào cài đặt môi trường cloud (menu môi trường trên thanh tiêu đề phiên -> Edit -> Network secrets / biến
môi trường) với tên **`KAGGLE_API_TOKEN`**, rồi mở phiên mới và bảo Claude làm tiếp theo file này. Không dán token
vào khung chat. Trong bot, token sẽ nhập ở Cài đặt (ô che như các key khác).
