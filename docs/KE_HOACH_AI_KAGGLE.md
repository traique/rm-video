# Đề xuất: mô hình AI offline trên Kaggle thay Gemini, ưu tiên CHẤT LƯỢNG LỜI — CHƯA CODE

**Mục tiêu (chốt 16/11/2026):**
- Không dùng Gemini.
- Mọi phần AI chạy bằng mô hình mở trên Kaggle.
- Lời đọc phải tốt nhất có thể: tiếng Việt tự nhiên, giọng miền Nam, đúng bước, đúng số, không chép lời gốc.

**Nói thẳng:** mô hình mở chạy được trên GPU miễn phí của Kaggle (2 x T4 = 32 GB) yếu hơn các mô hình Gemini lớn. Muốn lời tốt thì không chỉ chọn mô hình. Phải dùng thêm 4 đòn bẩy ở mục 3, và đo bằng chính tai / mắt bạn trước khi chuyển.

## 1. Hiện trạng: bot dùng AI ở đâu

| Việc | Hàm | Dữ liệu vào | Số lượt / video 4-5 phút |
| --- | --- | --- | --- |
| **Viết lời theo từng đoạn (quyết định chất lượng lời)** | `generate_timed` / `_any` | lời gốc + ảnh từng đoạn | 2-3 lô x (1 + tối đa 2 lượt viết lại) |
| Lời bản ngắn | như trên | | 1-3 |
| Đọc công thức, nhãn bước, mẹo | `recipe_facts` | lời gốc + 12 ảnh | 1 |
| Chọn ảnh bìa + cảnh món mở đầu | `cover.ai_judge` | 6-10 ảnh | 1 |
| Rút gọn câu quá dài | `shorten` | chữ | 0-2 |
| Video không lời / đoạn hay / bóc lời dự phòng | `generate_scenes`, `post_only`, `highlights`, Gemini ASR | ảnh / chữ / tiếng | 0-1 (bóc lời: Whisper trên Kaggle đã là chính) |

**Vị trí trong luồng:**
- Các lượt AI phụ thuộc nhau: đọc công thức → kế hoạch dựng → viết lời → mã kiểm → viết lại.
- Hiện chúng chạy trên Botkeep, nằm GIỮA phiên phân tích và phiên dựng của Kaggle.

**Hệ quả:** mô hình phải chạy **cùng phiên Kaggle** với cả chuỗi quyết định. Mỗi vòng hỏi đáp mà thành một phiên Kaggle thì mỗi vòng lại thêm một lần chờ máy.

## 2. Kiến trúc

```
Tự động đăng / không duyệt:  [Kaggle: tải -> tách giọng -> Whisper -> AI quyết định + viết lời -> đọc giọng -> dựng 2 bản] = 1 phiên
Có duyệt kịch bản:           [Kaggle: ... -> AI quyết định + viết lời] -> bạn duyệt / sửa -> [Kaggle: dựng]           = 2 phiên
```

- **Nhà cung cấp AI "local" mới:**
  - cách gọi giống Gemini hiện nay: `call(prompt, images, schema)`;
  - toàn bộ lời nhắc, luật và bộ kiểm có sẵn giữ nguyên (định lượng, nguyên liệu, mức chép lời, R1-R7, giọng miền Nam, tên nguyên liệu đúng).
- **Bộ chạy:** vLLM.
  - Ép đầu ra **đúng schema JSON** (không còn JSON hỏng), sinh nhiều bản song song rẻ.
  - Dự phòng: llama.cpp (GGUF).
- **Trọng số:** lấy từ thư viện Models của Kaggle, gắn sẵn vào phiên, không tải qua mạng.
- **Botkeep giữ:** tải nguồn, chống trùng, duyệt, giao, tự đăng, web / Telegram, dựng ảnh bìa.
- **Key Gemini:** không cần nữa (có thể giữ ô cài đặt cho ai muốn dùng).

## 3. Bốn đòn bẩy chất lượng lời (quan trọng hơn chọn mô hình)

1. **Mô hình lớn nhất vừa máy cho phần viết lời; mô hình nhìn ảnh riêng.**
   - Viết lời bằng mô hình chữ hạng ~30B (4-bit, chia trên 2 x T4).
   - Phần "nhìn ảnh" giao cho mô hình nhìn ảnh 7B: tả ngắn từng cảnh ("tay đang rưới nước mắm lên cánh gà vàng").
   - Mô hình viết nhận mô tả đó thay vì tự nhìn ảnh. Mô hình chữ 30B viết tiếng Việt tốt hơn rõ rệt so với mô hình nhìn ảnh 7B tự làm hết.
2. **Viết nhiều bản, mã chấm, chọn bản tốt nhất** (best-of-N).
   - Mỗi lô sinh 3-4 bản (vLLM chạy song song, tốn thêm ít thời gian).
   - **Mã chấm điểm:**
     - đủ nguyên liệu và số;
     - mức chép lời gốc;
     - đúng số chữ từng đoạn;
     - từ miền Nam (chén / muỗng / trụng... — bắt từ miền Bắc);
     - câu cụt;
     - lời kêu gọi đăng ký.
   - Bản đầu bảng còn được chính mô hình chấm độ tự nhiên.
   - Mô hình mở hay "lúc hay lúc dở": chọn trong nhiều bản bù được phần lớn.
3. **Ví dụ mẫu của kênh trong lời nhắc** (few-shot).
   - 5-10 cặp "lời gốc → lời mới" ĐẠT CHUẨN, giọng Bếp Đong Đủ, bạn duyệt.
   - Mô hình mở học theo ví dụ tốt hơn học theo luật.
4. **Tinh chỉnh riêng cho kênh** (LoRA, về sau).
   - Gom các kịch bản bạn đã duyệt / sửa (200-500 đoạn sau vài tuần chạy).
   - Tinh chỉnh mô hình bằng chính GPU Kaggle (Unsloth chạy được trên T4).
   - Đây là cách đưa mô hình mở lên ngang hoặc hơn mô hình lớn cho ĐÚNG kiểu lời của kênh.

**Kèm:** sửa từ miền Bắc bằng mã (bát → chén, thìa → muỗng, quả → trái, trần / chần → trụng...) sau khi mô hình viết. Không tin mô hình nhớ luật giọng.

## 4. Ứng viên (khi làm sẽ lấy bản mới nhất cùng dòng và kiểm lại giấy phép)

| Vai trò | Mô hình | Bộ nhớ 4-bit | Ghi chú |
| --- | --- | --- | --- |
| Viết lời (chữ) | **Qwen 32B Instruct** (dòng Qwen2.5 / Qwen3) | ~20 GB, 2 x T4 | tiếng Việt và làm theo luật tốt nhất trong tầm máy |
| Viết lời (chữ) | Qwen3 30B-A3B (MoE) | ~17 GB, 2 x T4 | chất lượng gần 32B, sinh chữ **nhanh hơn nhiều** (mỗi bước chỉ chạy ~3B) |
| Viết lời (chữ) | Gemma 3 27B | ~16 GB | đa ngôn ngữ mạnh, cũng nhìn được ảnh |
| Nhìn ảnh | Qwen2.5-VL 7B | ~6-8 GB | tả cảnh, đọc nguyên liệu, chọn ảnh bìa |
| Mốc so sánh | Qwen2.5-VL 7B làm hết | ~6-8 GB | xem "một mô hình nhỏ làm hết" kém bao nhiêu |
| Không đề xuất | 70B trở lên | > 40 GB | không vừa 2 x T4 (32 GB); TPU của Kaggle chạy được nhưng công cụ phức tạp, rủi ro cao |

**Mô hình riêng tiếng Việt** (Vistral, Arcee-VyLinh, PhoGPT) nhỏ (3-7B), làm theo luật và JSON kém hơn Qwen 30B. Chỉ đưa vào bảng thử để so giọng văn.

## 5. Chi phí ước

| | Ước |
| --- | --- |
| Cài vLLM + nạp mô hình 30B + 7B | 4-7 phút / phiên (trọng số gắn sẵn từ Kaggle Models) |
| Tả cảnh + đọc công thức + viết lời 2 bản (best-of-3) | 4-10 phút (30B-A3B nhanh hơn 32B khoảng 3-5 lần) |
| Giờ GPU / job | ~25-35 phút |
| Giờ GPU / tuần (2-4 video / ngày) | ~7-15 giờ (Kaggle cho ~30 giờ / tuần) |
| Chờ máy | 1 lần / job khi không duyệt (hiện nay 2) |

## 6. Rủi ro

| Rủi ro | Cách giảm |
| --- | --- |
| Lời vẫn kém Gemini | Đo mù ở K0 (mục 7); không đạt thì dừng hoặc chỉ dùng cho bản nháp; tinh chỉnh LoRA về sau |
| Phiên GPU nhận P100 (vLLM không chạy trên P100) | K0 kiểm cách chọn T4 x2 khi gửi notebook; không được thì llama.cpp |
| 2 x T4: không có bf16, nhân lượng tử chậm hơn GPU mới | Đã tính vào ước thời gian; ưu tiên Qwen3 30B-A3B nếu 32B quá chậm |
| Môi trường Kaggle đổi (CUDA, phiên bản) | Ghim phiên bản; lỗi cài thì llama.cpp |
| Hết giờ GPU trong tuần | Đã có cơ chế chạy CPU khi còn < 20 phút GPU; AI trên CPU quá chậm, nên khi đó tạm dừng tự đăng tới tuần sau và báo bạn |
| Bộ mã gửi Kaggle lớn lên | Test "bundle tự đủ" đã có; notebook < 1 MB |

## 7. K0 — thử MÙ trước khi làm (điểm dừng)

**Cách thử:**
- Một notebook trên Kaggle (2 x T4) chạy ĐÚNG lời nhắc hiện tại cho 6 video vàng của Bảy Cua Quán, với 3 cấu hình:
  - **A:** Qwen2.5-VL 7B làm hết;
  - **B:** 7B tả cảnh + Qwen 32B viết, best-of-3;
  - **C:** 7B tả cảnh + Qwen3 30B-A3B viết, best-of-3.
- Mỗi cấu hình đo:
  - thời gian nạp / sinh chữ;
  - giờ GPU;
  - điểm bộ chấm tự động (số liệu đúng, mức chép lời, số chữ, từ miền Nam, câu cụt).

**Phiếu chấm mù:**
- Mỗi video, các bản lời xếp ngẫu nhiên, không ghi mô hình nào. Bạn chấm 1-5 về tự nhiên, đúng giọng, hấp dẫn.
- Có thể đưa thêm bản Gemini đã chạy thật hôm nay làm mốc, nếu bạn muốn biết khoảng cách.

**Kết quả:** chọn cấu hình chính thức, hoặc dừng nếu chưa đạt.
- **Công:** khoảng 1-1,5 ngày.
- **Giờ GPU:** khoảng 2-3 giờ.

## 8. Lộ trình sau K0 (~7-9 ngày công)

| Bước | Nội dung | Công |
| --- | --- | --- |
| K1 | Nhà cung cấp "local" (vLLM, ép schema, sinh nhiều bản), tả cảnh bằng mô hình nhìn ảnh, sửa từ miền Bắc bằng mã | 1,5 ngày |
| K2 | Bộ chấm điểm + chọn bản tốt nhất; khung ví dụ mẫu của kênh (bạn duyệt 5-10 cặp) | 1,5 ngày |
| K3 | Chuỗi quyết định chạy trong phiên Kaggle (việc mới `decide`), Botkeep nhận kết quả rồi duyệt / dựng | 2-3 ngày |
| K4 | Gộp một phiên khi không duyệt (phân tích, quyết định, đọc giọng, dựng 2 bản); bỏ phụ thuộc Gemini; dọn cài đặt | 1-2 ngày |
| K5 | Lưu cặp "lời gốc → lời đã duyệt" mỗi lần bạn duyệt / sửa, làm dữ liệu tinh chỉnh LoRA về sau | 0,5 ngày |
| (sau) | Tinh chỉnh LoRA khi đủ dữ liệu, chấm mù lại | 1-2 ngày |

## 9. Cần bạn quyết

1. Làm K0 (thử mù 1-1,5 ngày, ~2-3 giờ GPU) trước?
2. Phiếu chấm mù có đưa bản Gemini làm mốc không?
3. Đồng ý ~7-15 giờ GPU / tuần cho phần AI (Kaggle cho ~30 giờ)?
4. Bắt đầu gom ví dụ mẫu của kênh: bạn sẽ duyệt / sửa 5-10 đoạn lời mẫu "giọng Bếp Đong Đủ"?
