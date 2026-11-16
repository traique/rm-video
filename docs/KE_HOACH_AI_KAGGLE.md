# Đề xuất: mô hình AI chạy trên Kaggle thay Gemini — CHƯA CODE

## 1. Hiện trạng: bot gọi Gemini ở đâu

| Việc | Hàm | Dữ liệu vào | Số lượt / video 4-5 phút |
| --- | --- | --- | --- |
| Đọc công thức, nhãn bước, mẹo | `prompt_engine.recipe_facts` | lời gốc + 12 ảnh | 1 |
| Chọn ảnh bìa + cảnh món cho đoạn mở đầu | `cover.ai_judge` | 6-10 ảnh | 1 |
| Viết lời theo từng đoạn (cốt lõi) | `generate_timed` / `_any` | lời gốc + ảnh từng đoạn | 1 mỗi lô 28 đoạn (video 4-5 phút: 2-3 lô) + tối đa 2 lượt viết lại mỗi lô |
| Lời bản ngắn | như trên | | 1-3 |
| Rút gọn câu quá dài | `prompt_engine.shorten` | chữ | 0-2 |
| Video không lời: viết theo cảnh / bài đăng | `generate_scenes`, `post_only` | ảnh / chữ | 0-1 |
| Đoạn hay (video dài) | `highlights.choose` | ảnh | 0-1 |
| Bóc lời dự phòng | `transcribe` (Gemini ASR) | tiếng | 0 (Whisper trên Kaggle là chính) |

**Vị trí trong luồng:**
- Mọi lượt AI chạy trên **Botkeep**, NẰM GIỮA hai phiên Kaggle: phiên phân tích (tách giọng, Whisper, ảnh) → Botkeep gọi Gemini → phiên dựng.
- Các lượt phụ thuộc nhau: đọc công thức → kế hoạch dựng → viết lời → mã kiểm → viết lại.

**Hệ quả:** không thể gom "các câu hỏi gửi Kaggle trả lời sau", vì mỗi vòng hỏi đáp sẽ là một phiên Kaggle và một lần chờ máy. Mô hình phải chạy **cùng phiên** với chuỗi quyết định.

## 2. Kiến trúc đề xuất

**Đưa cả chuỗi quyết định vào phiên Kaggle**: hiểu món → kế hoạch dựng → chọn ảnh bìa / mở đầu → viết lời bản đầy đủ + bản ngắn → kiểm và viết lại.

**Mô hình chạy ngay trong phiên** qua một "nhà cung cấp AI" mới. Cách gọi giống hệt Gemini: `call(prompt, images, schema)`. Toàn bộ lời nhắc, luật và bộ kiểm hiện có (định lượng, nguyên liệu, mức chép lời, R1-R7) giữ nguyên, chỉ đổi nơi trả lời.

**Mỗi job:**

```
Tự động đăng / không duyệt:   [Kaggle: tải -> tách giọng -> Whisper -> AI quyết định -> đọc giọng -> dựng 2 bản]   = 1 phiên
Có duyệt kịch bản:            [Kaggle: tải -> tách giọng -> Whisper -> AI quyết định] -> bạn duyệt -> [Kaggle: dựng]  = 2 phiên
```

- **So với hiện nay** (2 phiên + Gemini ở giữa):
  - không duyệt thì còn 1 phiên: một lần chờ máy, tải nguồn một lần, khỏi gửi kết quả qua lại;
  - có duyệt thì vẫn 2 phiên như cũ.
- **Botkeep giữ:** tải nguồn (yt-dlp), chống trùng, giao kết quả, tự đăng, web / Telegram, dựng ảnh bìa.
- **Gemini thành tuỳ chọn:** dự phòng hoặc tắt hẳn.
- **Cài đặt mới `AI_PROVIDER`:**
  - `gemini`: như hiện nay;
  - `kaggle`: chỉ mô hình trên Kaggle;
  - `kaggle_gemini`: Kaggle trước; lời viết không qua được bộ kiểm hoặc Kaggle lỗi thì gọi Gemini.

## 3. Chọn mô hình

**Yêu cầu:**
- viết tiếng Việt tự nhiên (giọng miền Nam), chính tả đúng;
- làm theo luật chặt (giữ đủ bước, đúng số, không chép lời gốc);
- **nhìn được ảnh** (khớp lời với hình, chọn ảnh bìa, thấy nguyên liệu);
- trả JSON đúng khuôn;
- chạy được trên GPU Kaggle (T4 16 GB, phiên 2 x T4 = 32 GB).

**Ứng viên** (khi làm sẽ lấy bản mới nhất cùng dòng và kiểm lại giấy phép):

| Mô hình | Nhìn ảnh | Bộ nhớ (lượng tử 4-bit) | Ghi chú |
| --- | --- | --- | --- |
| **Qwen2.5-VL 7B Instruct** | có | ~6-8 GB, 1 x T4 | đa ngôn ngữ tốt, JSON ổn; **thử đầu tiên** |
| Gemma 3 12B (Google) | có | ~8-10 GB, 1 x T4 | 140+ ngôn ngữ, cùng họ với Gemini |
| Qwen2.5-VL 32B | có | ~20 GB, 2 x T4 | tốt hơn nhiều, chậm hơn khoảng 3-4 lần |
| Qwen2.5 14B / SeaLLMs / Vistral 7B | không | 6-9 GB | chỉ để so phần viết chữ; thiếu phần nhìn ảnh |

**Bộ chạy:** vLLM.
- Nhanh, chạy nhiều lời nhắc song song.
- Ép đầu ra theo **đúng schema JSON** đang dùng (thay `response_schema` của Gemini), nên không còn JSON hỏng.
- Dự phòng: llama.cpp (GGUF), cài đơn giản hơn nhưng chậm hơn.
- Trọng số lấy từ thư viện Models của Kaggle: gắn sẵn vào phiên, không tải qua mạng.

**Cần kiểm ở bước thử:**
- Phiên GPU của bot là T4 hay P100. vLLM không chạy trên P100, khi đó dùng llama.cpp.
- Thời gian cài vLLM và nạp mô hình.

## 4. Ước chi phí

| | Gemini (hiện nay) | Mô hình trên Kaggle (ước) |
| --- | --- | --- |
| Tiền | miễn phí (giới hạn lượt) | miễn phí |
| Thời gian AI / job | ~1-3 phút (mạng) | + 2-4 phút cài / nạp mô hình + 2-5 phút sinh chữ |
| Chờ máy Kaggle | 2 lần / job | **1 lần** khi không duyệt |
| Giờ GPU / job | ~10-15 phút | ~20-25 phút |
| Giờ GPU / tuần (2-4 video / ngày) | ~3-6 giờ | ~5-10 giờ (Kaggle cho ~30 giờ) |
| Chất lượng lời | tốt (đã chạy thật) | **chưa biết** — phải đo |

Tổng thời gian một job tự đăng có thể **nhanh hơn** hiện nay, vì bớt một lần chờ máy (lần chờ có thể 0-20+ phút), dù phần AI chậm hơn.

## 5. Rủi ro

| Rủi ro | Cách giảm |
| --- | --- |
| Mô hình 7-12B viết kém Gemini: chép lời gốc, mất định lượng, sai chính tả, thiếu giọng miền Nam | Bộ kiểm hiện có chặn và bắt viết lại (trên Kaggle viết lại không tốn lượt); đo trên bộ video vàng TRƯỚC khi chuyển; chế độ `kaggle_gemini` dự phòng |
| Nhìn ảnh kém (chọn ảnh bìa, cảnh món, thấy nguyên liệu) | Giữ phần chấm điểm khung bằng mã (đã có), AI chỉ chọn trong vài ảnh; số liệu vẫn chỉ tin khi có trong lời gốc |
| Môi trường Kaggle đổi (CUDA, phiên bản vLLM) | Ghim phiên bản; lỗi cài thì lùi về llama.cpp, rồi Gemini |
| Bộ mã gửi Kaggle lớn lên (thêm lời nhắc, luồng quyết định) | Test "bundle tự đủ" đã có tự bắt thiếu module; notebook < 1 MB (hiện ~260 KB mã) |
| Hết giờ GPU trong tuần | Đã có cơ chế: còn dưới 20 phút GPU thì chạy CPU. Với AI: hết GPU thì dùng Gemini (nếu còn key) hoặc dừng tự đăng tới tuần sau |
| Giấy phép mô hình | Qwen2.5-VL 7B: Apache-2.0. Gemma: điều khoản riêng của Google. Kiểm trước khi chọn |

## 6. Lộ trình

| Bước | Nội dung | Công | Ra gì |
| --- | --- | --- | --- |
| K0 — thử | Notebook đo 2-3 mô hình (Qwen2.5-VL 7B, Gemma 3 12B, có thể Qwen2.5-VL 32B) với ĐÚNG các lời nhắc hiện tại trên 6 video vàng của Bảy Cua Quán: đọc công thức, viết lời, chọn ảnh bìa. Đo thời gian cài / nạp / sinh chữ và chấm bằng bộ kiểm có sẵn | 1 ngày, ~1-2 giờ GPU | Bảng so với Gemini + lời mẫu để bạn đọc. **Điểm dừng: bạn quyết có chuyển không** |
| K1 | Nhà cung cấp AI "local" trong `rmvideo/services/llm.py` (vLLM, ép schema JSON); test bằng mô hình giả | 1 ngày | |
| K2 | Chuỗi quyết định chạy được trong phiên Kaggle (việc mới `decide`): nhận kết quả phân tích, trả kế hoạch, lời, bài đăng, bảng định lượng. Botkeep nhận rồi đi tiếp (duyệt / dựng) | 2-3 ngày | |
| K3 | Gộp một phiên khi không duyệt: phân tích, quyết định, đọc giọng, dựng 2 bản | 1-2 ngày | |
| K4 | `AI_PROVIDER`, dự phòng Gemini, ghi chú, số liệu, tài liệu, chạy thật trên Kaggle | 1 ngày | |

**Tổng ~6-8 ngày công.** Làm K0 trước: nếu chất lượng không đạt thì dừng ở đó, chỉ tốn 1 ngày.

## 7. Cần bạn quyết

1. **Mục tiêu:**
   - **(a) Thay hẳn Gemini:** không cần key Google;
   - **(b) Kaggle trước, Gemini dự phòng:** mình đề xuất, an toàn nhất;
   - **(c) Chỉ dùng Kaggle khi Gemini lỗi hoặc hết lượt.**
2. Đồng ý tốn thêm ~5 giờ GPU Kaggle mỗi tuần?
3. Làm K0 (thử 1 ngày, có bảng so sánh và lời mẫu) trước rồi mới quyết làm tiếp?
