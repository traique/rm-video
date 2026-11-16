# Bộ nhận diện kênh «Bếp Đong Đủ»

Tạo bằng `python brand/brand_kit.py "Bếp Đong Đủ" "Đong đủ, nấu là ngon" "#F28C28" brand/bep_dong_du "Hôm nay nấu gì?"`
(chữ Be Vietnam Pro ExtraBold đã kèm trong `assets/`, âm hiệu là hai nốt tự tổng hợp — không vướng bản quyền).

| Tệp | Dùng làm gì |
| --- | --- |
| `logo.png` | Logo tròn nền trong suốt 512px: tải lên ở cuối trang ⚙️ Cài đặt («Logo kênh»), hiện góc phải trên video + ảnh bìa |
| `sting.mp3` | Âm hiệu "đinh - đong" 1,3 giây: tải lên ở «Âm hiệu kênh cho màn kết» |
| `fb_avatar.jpg` | **Ảnh đại diện Fanpage / TikTok** 1080x1080: chỉ có tên, chữ to (Facebook cắt tròn, thu nhỏ còn ~40px) |
| `fb_cover.jpg` | **Ảnh bìa Fanpage** 1640x924: nội dung ở giữa (máy tính cắt trên dưới, điện thoại cắt hai bên, góc dưới trái bị ảnh đại diện đè) |
| `avatar.jpg` | Bản ảnh vuông có câu dưới tên (dùng cho bài đăng / ảnh chia sẻ) |

**Từ bản 43 đây là bộ mặc định của bot**: không cần điền gì. Logo và âm hiệu trong thư mục này được dùng khi chưa tải
file riêng lên trang cài đặt; các giá trị dưới đây là mặc định (đổi ở ⚙️ Cài đặt → nhóm 🎨 Nhận diện kênh, ✨ Phong cách
remake & bài đăng; nhập `0` để tắt chữ / màu):

| Khoá | Giá trị |
| --- | --- |
| Tên kênh (`BRAND_NAME`) | Bếp Đong Đủ |
| Câu dưới tên kênh (`BRAND_TAGLINE`) | Đong đủ, nấu là ngon |
| Câu chốt đọc cuối video (`BRAND_CLOSE`) | Đong đủ là ngon, cả nhà nấu thử nha |
| Màu kênh (`BRAND_COLOR`) | #F28C28 |
| Tông màu video (`BRAND_LOOK`) | Tươi sáng |
| Tên loạt video (`BRAND_SERIES`) | Hôm nay nấu gì |
| Mẫu ảnh bìa (`COVER_TEMPLATE`) | Theo kênh |
| Gắn logo / Âm hiệu màn kết | Bật |
| Công thức trong bài đăng (`POST_RECIPE`) | Bật |
| Mẹo hay (`TIPS`) | Bài đăng + thẻ «💡 MẸO» trên video |
| Phong cách dựng lại (`REMAKE_STYLE`) | Mạnh (tự lùi về Nhẹ khi không đủ dữ liệu / vi phạm luật) |
| Thêm bản ngắn ≤ 90 giây (`SHORT_VERSION`) | Bật |
