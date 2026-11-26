# Thay đổi so với bản botkeep_7

Làm theo "Kế hoạch nâng cấp Video Bot (botkeep_7) — Bản hoàn thiện". Mỗi giai đoạn là một commit riêng trên nhánh.
Kiểm tra: `ruff check .`, `mypy` sạch, `pytest -q` (hơn 900 test, Python 3.13 — bản Botkeep và Kaggle chạy; CI cũng 3.13,
3.11–3.12 vẫn chạy được nhưng không còn được kiểm). Đã chạy trọn job với dịch vụ thật (Gemini bóc lời + viết kịch bản,
edge-tts, tách giọng, render) và các model offline thật.

## Cần làm khi cập nhật lên Botkeep

1. Python runtime **≥ 3.11** (yt-dlp và google-genai bản mới không còn hỗ trợ 3.10).
2. `pip install -r requirements.txt` (google-genai < 4: đã kiểm với key thật trên 2.34 và 3.0.0).
3. Đặt `WEB_PASSWORD` (thiếu là web khoá), `ALLOWED_USERS` (thiếu là bot Telegram từ chối mọi người; nhắn bot một tin
   để biết chat id). Nếu trước đây đã sửa `OPENROUTER_BASE_URL` / `POT_SERVER` trên web: đặt lại bằng biến môi trường.
4. Mở web, đăng nhập lại (mật khẩu cũ lưu trong trình duyệt được xoá).

## 1.4.14 — Trang chính sách quyền riêng tư công khai để kết nối YouTube (26/11/2026)

- **Trang `/privacy` và `/terms` công khai** (không cần đăng nhập): Google Auth Platform giờ khoá nút «Publish app» tới
  khi trang Branding có link trang chủ và link chính sách quyền riêng tư, nên ứng dụng OAuth YouTube kẹt ở «Testing» và
  tài khoản Kể Bé Nghe bị chặn «Lỗi 403: access_denied». Trang nói rõ bot chỉ dùng quyền tải video lên và đọc tên kênh /
  lượt xem, token lưu trên máy chủ riêng, cách thu hồi quyền, tuân thủ Limited Use và điều khoản YouTube API.
- Chú thích ô «YouTube · Client ID» trong Cài đặt ghi đủ các ô cần điền ở Branding (trang chủ, chính sách, điều khoản,
  không tải logo) và cách qua trang «Google chưa xác minh ứng dụng này».

## 1.4.13 — Đổi tài khoản Kaggle lúc đang chạy; gõ sai lệnh console được gợi ý (25/11/2026)

Chuyển sang tài khoản traique (đo thật: cấp máy GPU 20 giây, 2×T4, có internet; kids_setup chờ 15 phút + chạy 45 phút,
0,7 giờ GPU). Bot khởi động lúc 14:40 còn token quetrai nên tự gửi kids_setup lên quetrai (kẹt hàng chờ ở đó) rồi mới đổi
token.
- **Đổi tài khoản Kaggle trong Cài đặt lúc bot đang chạy**: giờ nghỉ gửi GPU và kết quả «model truyện bé sẵn sàng» của
  tài khoản cũ bị bỏ, tài khoản mới được xét lại từ đầu (trước đây có thể mang theo 1–6 giờ nghỉ của tài khoản cũ).
- **Gõ sai lệnh console**: «kids_setup-status» (gạch ngang) chạy luôn; «kid_setup», «kids_check» được gợi ý lệnh gần nhất
  thay vì in cả trang trợ giúp.
- Video dài hơn chế độ đã chọn không còn tính là «lỗi tải video» trong `health`.
- **Truyện bé không còn bị thu kết quả hai lần**: job 7 (truyện đầu tiên trên traique: chờ máy 42 phút, chạy 14 phút, tải
  78 MB về mất 16 phút) tự thu được video nhưng mục giữ chỗ ghi từ lúc gửi (1.4.11) không được xoá, nên vòng tự động tạo
  job 8 tải lại cùng video thêm 15 phút. Có kết quả trong tay là bỏ mục giữ chỗ ngay.
- **Gửi việc lên Kaggle từ tốn như người dùng bình thường** (tài khoản quetrai bị kẹt hàng chờ sau một ngày bot gửi rồi
  xoá nhiều notebook; không giả làm trình duyệt — bot vẫn xưng đúng là client API):
  - Hỏi trạng thái thưa dần: chờ máy quá 2 phút hỏi mỗi phút, quá 10 phút mỗi 2 phút; đang chạy mỗi 30 giây. Job 7
    (chờ 42 phút + chạy 14 phút) từ ~170 lần hỏi còn ~60.
  - Nhớ thời gian chờ máy của phiên GPU gần nhất (3 giờ, theo từng tài khoản, tệp `kaggle_queue.json`): lâu hơn hạn của
    việc sắp gửi (bóc lời bếp 8 phút, dựng bếp) thì không gửi, làm thẳng trên Botkeep — hết cảnh gửi, chờ, rồi xoá
    notebook. Truyện bé (chờ được lâu, giữ chỗ) vẫn gửi và cập nhật số đo; phiên sau được cấp máy nhanh là gửi lại.
  - Phải bỏ phiên vì chờ quá lâu nhiều lần trong 24 giờ thì nghỉ dài dần: 1, 2, 4, tối đa 6 giờ (trước: luôn 1 giờ).
  - Bot chủ động không gửi thì ghi «Kaggle đang đông…», không tính là lỗi Kaggle trong `health` (cả phần của video dài
    và bản ngắn).
- Rà soát (code-review + security-review, 25/11 tối): truyện bé nhường Botkeep cho job khác (giữ chỗ sau 5 phút) không bị
  tính là «Kaggle chậm» (trước: job bếp vừa tới bị dồn lên Botkeep cả giờ); thu kết quả phiên giữ chỗ không ghi nhầm «chờ 0
  phút» che số đo thật; số đo hàng chờ xét mục mới nhất còn có ý nghĩa; nghỉ giữa hai lần hỏi (tới 2 phút) vẫn thấy Hủy /
  tắt bot trong 5 giây; mục giữ chỗ truyện bé giữ tới khi làm xong bài đăng (bot tắt lúc đang kiểm video thì chạy lại thu
  kết quả, không gửi lại cả phiên); `kaggle_check` xử lý notebook chưa có đầu ra; lệnh console giữ nguyên chữ hoa ở link.
  Security review: không phát hiện lỗ hổng mới.
- **Gemini quá tải (503) không còn cho ra video đọc kịch bản mẫu** (job 9, 25/11 tối: model chính lẫn dự phòng 503 trong
  ~20 giây thử lại → bot dựng 5-10 phút một video đọc «Bạn sẽ không tin nổi video này đâu…» vô dụng, log in hai traceback
  dài). Giờ: 503 thử lại 5 lần với 3+6+12+24 giây mỗi model (cơn quá tải thường vài phút); vẫn không được thì job dừng với
  «Dịch vụ AI đang quá tải / hết lượt tạm thời — gửi lại link sau vài phút» thay vì dựng kịch bản mẫu (key / model sai
  vẫn dùng mẫu + cảnh báo như cũ); lỗi quá tải / hết lượt ghi một dòng cảnh báo, chỉ lỗi lạ mới kèm traceback.
- **Kiểm tra lại API Gemini theo tài liệu Google (25/11)**: key dạng mới «AQ.…» (Google AI Studio cấp mặc định từ
  28/5/2026, gọi là auth key, chỉ dùng được cho Gemini API) chạy bình thường với bot — bot gửi key qua SDK google-genai
  3.6.1 (bản mới nhất), không cần sửa. Model bot dùng đều còn: gemini-3.5-flash-lite (viết lời), gemini-3.5-transcribe
  (bóc lời), gemini-4-hydrogen (nháp truyện bé, GA 12/11); gemini-3.1-flash-lite (dự phòng) Google hẹn tắt 7/5/2027;
  gemini-2.5-flash / 2.5-pro bị chuyển hướng từ 1/12/2026 (bot không dùng). Đo thật tối 25/11: 3.1-flash-lite, 3.8-flash
  và 4-hydrogen 503 «high demand» liên tục nhiều phút, 3.5-flash-lite thỉnh thoảng 503 — nên **thêm gemini-3.6-flash vào
  danh sách dự phòng mặc định** (1,2-1,7 giây khi đặt mức suy nghĩ «low»; để Google tự chọn thì 22-42 giây vì nghĩ dài).
  Gemini 3.x flash thường (3.5 / 3.6 / 3.8-flash) không truyền mức suy nghĩ thì bot đặt «low»; model -lite không nhận
  thinking_config nên giữ nguyên. gemini-3.5-flash giờ chỉ là tên cũ chuyển hướng sang 3.6-flash. Ai đã lưu ô «Model
  Gemini dự phòng» trong Cài đặt thì tự thêm `,gemini-3.6-flash`.

## 1.4.12 — Dựng hết RAM thì tự dựng lại; thấy tiến độ tải nguồn (25/11/2026)

Job 6 (TikTok 75s, 14MB): tải nguồn mất ~10 phút (các job trước 42–85 giây) mà chỉ thấy «Tải video (yt-dlp)»; Kaggle vẫn
không cấp máy GPU nên bóc lời trên Botkeep; tới bước dựng 720p thì ffmpeg bị hệ thống giết (tín hiệu 9, hết RAM) sau 27
phút làm job. Đo lại trên máy thật với đúng video đó: ffmpeg dựng chỉ cần 330–455 MB, bước phân tích hình ~110 MB — không
phải mã mới làm phình RAM (bộ dựng không đổi từ 21/11), nhưng máy Botkeep ~2 GB lúc đó không còn đủ.
- **ffmpeg dựng bị giết vì hết RAM → tự dựng lại một lần** ở chế độ 1 luồng (sau khi trả RAM), ghi chú cho người dùng; lần
  hai vẫn hết RAM thì báo rõ kèm số RAM. Huỷ job / tắt bot (cũng giết bằng tín hiệu 9) không bị hiểu nhầm là hết RAM.
- **Trước khi dựng trả RAM**: bỏ các phiên ONNX đã dùng xong (dò chữ, tách giọng, dò mặt — cần lại thì tự mở), dọn rác,
  trả bộ nhớ trống về hệ điều hành. Log ghi «Trước khi dựng: RAM trống … / giới hạn … , bot giữ …»; bị giết thì ghi thêm 5
  tiến trình chiếm RAM nhất để biết ai giữ.
- **Tải nguồn hiện tiến độ** «Tải video (yt-dlp) 42% của 14MB · 23 KB/s» (30 giây một lần) và ghi «Tải nguồn xong: 14MB
  trong 600s» — quá 2 phút thì cảnh báo mạng tới nền tảng video đang chậm.

## 1.4.11 — Không «dồn» phiên lên Kaggle; phát hiện Kaggle không cấp GPU (25/11/2026)

Tài khoản quetrai kẹt hàng chờ chạy nền GPU hơn 20 giờ (notebook mở tay vẫn có GPU ngay) sau một ngày bot gửi rồi bỏ nhiều
phiên; người dùng chuyển sang tài khoản khác và nhờ «đóng hết notebook đang chạy rồi mới chạy notebook tiếp theo».
- **Trước MỖI lần gửi phiên GPU, đóng hết phiên công việc còn xếp hàng / chạy** (`rmvideo-worker`, `rmvideo-kids`,
  `rmvideo-analyze` — bot chạy một phiên một lúc nên phiên nào còn đó đều là ma), trừ truyện bé đang giữ chỗ. Không đụng
  notebook model / nhạc / PhoWhisper.
- **Kaggle lâu không cấp máy thì nghỉ gửi phiên GPU mới 1 giờ** (một phiên phải bỏ hoặc giữ chỗ vì chờ máy quá lâu): bếp
  làm trên Botkeep, truyện bé không tạo job (lượt tự động báo «tạm nghỉ… thêm N phút»), thay vì job sau lại gửi, lại chờ,
  lại bỏ. Thu kết quả phiên đang giữ chỗ vẫn chạy bình thường.
- **Kaggle không cấp GPU** (tài khoản chưa xác minh số điện thoại: phiên chạy ngay nhưng bằng CPU, không có internet — đo
  thật 25/11): notebook thấy không có GPU thì ghi lỗi rõ ràng rồi thoát ngay (trước đây chạy hàng chục phút trên CPU rồi
  hỏng), bot nghỉ gửi phiên GPU 6 giờ và báo «tài khoản đã xác minh số điện thoại chưa?». `kids_setup` cũng dừng ngay.
- `kaggle_check` kiểm LoRA đúng trong notebook chạy tay (KIDS_KAGGLE_SOURCES), không còn báo nhầm `rmvideo-kids-models`.
- **Video dài hơn chế độ đã chọn** (job 5: TikTok 365s ở «remake nguyên ≤5 phút»): báo thẳng «Video dài 365s, vượt giới hạn
  300s» + nút cần bấm, không còn gói thành «Không tải được video TikTok… tải về máy rồi upload» (tải được, chỉ dài quá) và
  không gọi tikwm vô ích. Phần tải video không đổi từ 16/11.

## 1.4.10 — Nút «Xóa» / «Xóa tất cả» trong Hàng đợi & kết quả (25/11/2026)

- **Xóa từng job** (nút «Xóa» ở cột Trạng thái) và **«🗑 Xóa tất cả job đã xong»**: bỏ job khỏi danh sách VÀ xoá luôn trên
  máy cho nhẹ — video kết quả, bản ngắn, ảnh bìa, file tải lên của job, dòng trong jobs.db (bảng jobs + steps; nhật ký
  nguồn để xử lý khiếu nại bản quyền vẫn giữ). «Xóa tất cả» còn dọn job cũ chỉ còn trong jobs.db, video mồ côi trong
  outputs/ và file tải lên không job nào dùng, rồi thu nhỏ jobs.db.
- **Không xoá**: job đang chờ / chạy / chờ duyệt (bấm Hủy trước), video đang chờ đăng YouTube / gửi gói TikTok / tự động
  đăng Fanpage (bếp, truyện bé), truyện bé đang giữ chỗ Kaggle (Xóa tất cả bỏ qua; Xóa từng job thì bỏ luôn mục giữ chỗ),
  file vừa ghi (< 10 phút kết quả, < 1 giờ tải lên: có thể đang giao / đang tải). Chỉ xoá file trong outputs/, uploads_in/,
  work/ của bot. Cả hai nút đều hỏi lại trước khi xoá.
- API: `POST /api/jobs/{id}/delete` (409 kèm lý do khi không xoá được), `POST /api/jobs/clear`.
- Không xoá file mà job đang dở khác còn dùng (duyệt kịch bản: job render mới dùng chung file nguồn với job gốc); mã job
  không bao giờ cấp lại sau khi xoá (YouTube / TikTok chống đăng trùng theo mã job); không đọc được danh sách video chờ
  đăng thì không xoá gì; xoá truyện bé đang giữ chỗ thì bỏ luôn phiên Kaggle đó.

**Rà soát 1.4.8–1.4.10 (25/11):**
- Bỏ phiên Kaggle chỉ xoá notebook khi phiên CÒN xếp hàng / chạy (phiên đã xong giữ đầu ra — tiếng nền đã tách mà lần dựng
  sau dùng lại — và log), xoá trong luồng nền (Hủy / tắt bot không phải chờ mạng); gắn đầu ra phân tích mà Kaggle từ chối
  thì gửi lại không gắn (phiên dựng tự tách lại) thay vì đổ về Botkeep.
- `kids_setup` (nút web, lệnh, tự chạy lúc khởi động) không bao giờ dọn phiên của job khác; Kaggle kín phiên thì báo chạy
  lại sau. Dọn phiên ma chỉ đụng 3 notebook công việc và đợi 10 giây cho Kaggle nhả chỗ rồi mới gửi lại.
- Hủy / tắt bot lúc truyện bé đang chờ chỗ GPU có tác dụng ngay (trước đây luồng nền vẫn gửi notebook và giữ chỗ cho
  truyện đã huỷ).
- LoRA tải theo mã commit (không theo nhánh), có thời hạn kết nối; `kaggle_check` kiểm file LoRA có thật trong notebook.
- Model Gemini bị coi là «đã gỡ» chỉ khi lỗi đúng là 404 / NOT_FOUND (không nhầm «retry in 40.404s» của lỗi 429).
- Khởi động: tự chạy kids_setup lỗi không làm bỏ qua bước đẩy lại kho nhạc; nút «setup» trên web không chặn vòng sự kiện;
  Kaggle lỗi lúc hỏi dataset kho nhạc thì 5 phút sau mới hỏi lại.

## 1.4.9 — Tự nhận ra và tự khôi phục khi tài nguyên trên Kaggle bị xoá (24/11/2026)

Người dùng lỡ xoá hết «Your Work» trên Kaggle (notebook + có thể cả dataset).
- **Notebook công việc** (`rmvideo-worker`, `rmvideo-kids`, `rmvideo-analyze`, `rmvideo-music`): bot tự tạo lại ở lần gửi
  sau, không cần làm gì.
- **Model truyện bé** (`rmvideo-kids-models`): không tự tạo lại được vì tốn ~1 giờ GPU, cần người gõ `kids_setup`. Thiếu
  model thì job truyện bé dừng NGAY với lời nhắc «gõ kids_setup» (không viết truyện / gửi Kaggle vô ích), lượt tự động
  không tạo job (khỏi 3 lần lỗi liền -> dừng tự động); đang chạy kids_setup thì báo «đang chuẩn bị model».
- **LoRA nhân vật**: `kids_setup` giờ tải luôn file LoRA từ repo GitHub công khai (Git LFS, 164 MB, kiểm sha256) vào đầu
  ra notebook model; mất dataset `rmvideo-kids-lora` thì phiên dựng dùng bản đó (trước đây vẽ không LoRA). Không cần token
  Kaggle ngoài Botkeep, không cần chạy `scripts/push_kids_lora.py`.
- **Kho nhạc** (`rmvideo-kids-bgm`, `rmvideo-cook-bgm`): dataset mất mà library.json vẫn ghi «đã đẩy» thì phiên dựng gắn một
  dataset không còn. Giờ bot hỏi Kaggle (nhớ 1 giờ), mất thì tự đẩy lại kho từ bản trên Botkeep.
- **Lệnh `kaggle_check`**: kiểm model truyện bé, LoRA, kho nhạc, nguồn tự đặt trong KIDS_KAGGLE_SOURCES, PhoWhisper (nếu
  chọn) và tự đẩy lại kho nhạc bị mất. Lúc khởi động bot cũng kiểm và ghi cảnh báo nếu thiếu.
- **Khởi động mà notebook model truyện bé không còn thì TỰ chạy `kids_setup`** (người dùng nhờ ngày 24/11): chỉ khi thật sự
  mất (đang chạy / lỗi thì không gửi lại), còn ≥ 75 phút GPU trong tuần, đang dùng notebook mặc định.

## 1.4.8 — Kaggle kín 2 phiên GPU thì xoá phiên «ma» rồi gửi lại; model Gemini đã gỡ không chặn bước nghĩ ý (24/11/2026)

Theo log tối 24/11 (job 4 truyện bé hỏng sau 3,5 phút).
- **«Maximum batch GPU session count of 2 reached»**: Kaggle chỉ cho 2 phiên GPU xếp hàng / chạy cùng lúc. Phiên bot đã bỏ
  (chờ máy quá lâu rồi dựng trên Botkeep, hay bị huỷ) vẫn nằm trong hàng chờ và chiếm chỗ tới khi được cấp máy — giờ cao
  điểm là nhiều giờ. Giờ (1) khi bỏ một phiên bot **xoá notebook** đó (`DeleteKernel`, cách duy nhất qua API công khai để
  bỏ phiên; notebook được tạo lại ở lần gửi sau), trừ notebook đang có phiên truyện bé giữ chỗ; (2) gửi mà bị từ chối vì kín
  phiên thì xoá các notebook của bot còn phiên xếp hàng / chạy (đều là phiên ma: bot chỉ chạy một phiên một lúc) rồi gửi
  lại; truyện bé vẫn kín thì chờ tới 75 phút (2 phút gửi lại một lần), bếp thì dựng trên Botkeep như khi chờ máy quá lâu.
- **Model dự phòng Google đã gỡ không làm hỏng bước**: `gemini-2.5-flash-lite` (mặc định cũ trong GEMINI_FALLBACK_MODELS)
  trả 404 «no longer available» → bước nghĩ ý truyện bỏ dù lỗi thật là quá tải. Giờ model 404 bị bỏ khỏi danh sách dự
  phòng cho tới khi bot khởi động lại (và ghi log để sửa Cài đặt), lỗi báo ra là «quá tải» của model chính; mặc định chỉ
  còn `gemini-3.1-flash-lite`. Ai đã lưu Cài đặt với giá trị cũ thì sửa ô «Model Gemini dự phòng».

## 1.4.7 — Rà soát toàn bộ mã (4 tổ kiểm song song) (24/11/2026)

Sửa theo kết quả rà soát: giữ chỗ Kaggle, nhạc Lyria, dựng bếp, cấu hình / đóng gói.
- **Giữ chỗ Kaggle (truyện bé):**
  - Mọi phiên truyện bé dùng chung một notebook: đang có phiên giữ chỗ mà gửi truyện mới là mất phiên đó (và tốn GPU gấp
    đôi). Giờ `kids_now` / lượt tự động từ chối làm truyện mới khi còn phiên giữ chỗ; gửi lên Kaggle cũng chặn ở tầng dưới.
  - Mục giữ chỗ giữ tới khi job thu kết quả XONG (trước đây xoá ngay khi tạo job thu: job thu đứt mạng là mất video đã dựng).
    Job thu lỗi thì lượt sau thu lại, tối đa 3 lần rồi báo admin.
  - Mục giữ chỗ ghi ngay từ lúc gửi phiên (bot tắt lúc đang tải kết quả 15–20 phút vẫn còn mục để chạy lại thu về); lỗi
    thật sau khi gửi (video không đạt, sai mã) thì bỏ mục.
  - Người dùng bấm Hủy thì bỏ cả mục giữ chỗ (trước đây bot vẫn làm tiếp và giao truyện đã huỷ); log ghi đúng là phiên có
    thể vẫn chạy nếu được cấp máy trước hạn (Kaggle không có lệnh huỷ).
  - Phiên đang chạy mà hỏi trạng thái lỗi mạng sau hạn thì không bị bỏ nhầm; job quá hạn khi đang giữ chỗ báo «⏳ … giữ chỗ»
    thay vì «❌ … thử chất lượng fast»; thông báo giữ chỗ nêu đúng lý do (chưa cấp máy / chưa xong).
  - Ghi `kids_auto.json` có khoá (luồng Kaggle và vòng tick không ghi đè nhau).
- **Nhạc Lyria:**
  - Tên bài chốt lúc ghi vào kho (trong khoá), không phải lúc bắt đầu tạo: Botkeep tạo nền và notebook Kaggle cùng làm một màu
    không còn đè bài nhau; mã hoá ra file tạm rồi mới đưa vào kho, wav / m4a dở được dọn.
  - Notebook nhạc: bot khởi động lại giữa chừng thì lần sau **nối lại** phiên đã gửi (lưu mã lần gửi), không gửi lại từ đầu;
    không gửi đè khi phiên cũ còn chạy; bot tắt thì luồng chờ Kaggle dừng trong 30 giây (trước đây giữ tiến trình tới 5 phút).
  - Phiên đứng im không bị tính là «mạng chậm» (khỏi nghỉ Lyria 6 giờ oan); hạn riêng từng phiên nên phần đã nhận không mất
    khi hết giờ; `kids_bgm` / `cook_bgm` / nút web tự chuyển sang Kaggle khi Kaggle đã tạo được nhạc.
- **Dựng bếp:** khối cuối (cảm thán + điểm danh + tóm tắt) không bao giờ đứng trước cảnh mở đầu nữa (mốc chèn rơi vào quãng
  đã bỏ từng cho chỉ số 0 — chèn lên đầu video, trước câu hook, mà R1–R8 không bắt; thêm luật R9 làm lưới cuối); không để
  cảnh món cuối loé lên < 2 giây rồi mới tới khối cuối; bản ngắn mà mọi quãng giữ gộp thành một khúc thì tách cảnh cuối ra
  để điểm danh không mở video; bước cảm thán không có lời không bị dời; bước không có mô tả không đọc tên loại («cook»)
  trong tóm tắt; nối mẩu câu giữ chữ hoa tên riêng («Hà Nội», «Gấu Nâu»).
- **Cấu hình / đóng gói:** README phần nhạc bé theo cách mới; `.gitignore` thêm file trạng thái bot tự ghi; `pydantic` ghi
  trong requirements; gói Botkeep bỏ tài liệu / ảnh Fanpage không dùng lúc chạy (còn 1,16 MB); lệnh `kids_tick` có trong help.

## 1.4.6 — Truyện bé giữ chỗ trong hàng chờ GPU Kaggle; Lyria đứng im theo gói; ý truyện dài không bị loại hết (24/11/2026)

Theo log tối 24/11 (job 3 truyện bé hỏng sau 77 phút «Kaggle chờ máy quá 75 phút»; notebook nhạc 17/18 bài).
- **Chờ GPU quá lâu không làm hỏng truyện nữa.** Trước đây bot bỏ phiên sau 75 phút chờ máy: mất truyện đã viết, mất chỗ
  xếp hàng (gửi lại là xếp hàng từ đầu, giờ cao điểm không bao giờ tới lượt). Giờ phiên truyện bé gửi với hạn bắt đầu
  4 giờ; bot thôi chờ khi quá 75 phút, khi có job khác đang chờ (nhường worker sau 5 phút), khi job quá hạn hay bot tắt /
  khởi động lại — nhưng phiên **vẫn giữ chỗ**. `kids_auto` hỏi trạng thái mỗi 2 phút (kể cả khi KIDS_AUTO tắt, job
  `kids_now` cũng được theo dõi); Kaggle dựng xong thì tạo job thu kết quả (cùng nguồn / chat với job cũ, truyện ghi dưới mã
  job cũ); notebook lỗi hay quá 4 giờ chưa được cấp máy thì bỏ và báo admin. Job bị dừng vì bot tắt lúc đang giữ chỗ, khi
  chạy lại sau khởi động sẽ tự thu kết quả thay vì nghĩ truyện mới. Người dùng bấm Hủy thì vẫn huỷ hẳn, không giữ chỗ.
  Lệnh `kids` liệt kê các phiên đang giữ chỗ.
- **Lyria: không có gói nào trong 30 giây thì ngắt phiên, giữ phần đã nhận, nối phiên mới** (notebook nhạc 24/11: một bài
  «kết nối đứng im» 250 giây, mất cả phần đã nhận). Trên Botkeep nhận chậm rõ rệt thì ngắt ngay trong phiên (~20 giây)
  thay vì đợi máy chủ cắt sau 51 giây. Notebook nhạc trên Kaggle không bao giờ «nghỉ vì chậm» (chỉ nối phiên), có hạn bắt
  đầu như các notebook khác; ước lượng thời gian theo số đo thật (17 bài hết 68 phút).
- **Kaggle đã tạo được nhạc thì kho thiếu bài nhờ Kaggle luôn** (14 ngày), khỏi mất ~1 phút thử Lyria trên Botkeep mỗi job;
  tự chạy lại tối đa 3 giờ một lần (bài còn thiếu được bổ sung dần).
- **Ý truyện dài quá không bị loại hết nữa.** Job 3: cả 4 ý 98-110 chữ bị LOẠI (giới hạn 90) nên viết truyện không có ý
  (mất màu nhạc, đồ vật đã chọn). Giới hạn lên 120 chữ, lời nhắc xin 2-3 câu ngắn (tối đa 70 chữ), ý chỉ dài quá vẫn
  được chấm khi không còn ý nào khác.
- **Tiêu đề có từ trừu tượng («bí mật», «điều kỳ lạ», «bất ngờ», «phép màu», «giấc mơ») không bị báo lạc đề** (job 3: «Bí
  mật dưới cái mền» bị báo thiếu «bí», «mật» -> mất một lượt viết lại).
- Nhỏ: log truyện bé không lặp «Truyện bé: Truyện bé:»; không ghi dòng bước rỗng «Job N [ 3s] » lúc bắt đầu.

## 1.4.5 — Kho nhạc nền tạo trên Kaggle (mạng Botkeep quá chậm cho Lyria) (24/11/2026)

- **Nhạc nền bé + bếp do Kaggle tạo** (người dùng chọn ngày 24/11). Trên Botkeep mỗi phiên Lyria chỉ nhận ~4 giây nhạc
  trong 51 giây rồi bị cắt (luồng Lyria cần ~190 KB/s, mạng Botkeep ~80-100 KB/s), nên kho nhạc chưa bao giờ có bài.
  Khi Lyria trên Botkeep quá chậm mà kho còn thiếu bài, bot **tự** gửi notebook riêng `rmvideo-music` lên Kaggle (CPU,
  không tốn giờ GPU): tạo lần lượt các bài còn thiếu (kho bé 3 màu x 3 bài x 100 giây, kho bếp 3 màu x 3 bài x 150
  giây; xen kẽ màu nên dừng giữa chừng thì màu nào cũng đã có bài), ~45-60 phút. Xong bot tải m4a về kho trên Botkeep,
  ghi library.json, đẩy dataset `rmvideo-kids-bgm` / `rmvideo-cook-bgm` như cũ: các video sau có nhạc. Tự chạy tối đa
  6 giờ một lần; gõ `bgm_kaggle` để chạy ngay.
- **Lưu ý bảo mật:** notebook `rmvideo-music` CHỨA KEY GEMINI trong mã (dạng base64, ai mở được notebook là đọc được).
  Notebook để riêng tư; đừng chia sẻ / công khai notebook này. Các notebook khác của bot vẫn không chứa key.
- Dùng lại đúng mã Lyria của Botkeep (nối phiên khi đứt, hoà tiếng chỗ nối, bỏ khi quá chậm) và cùng thông số mã hoá
  m4a (`lyria.encode_args`).

## 1.4.4 — Tải video Kaggle đứt thì tải tiếp; Lyria đứt giữa chừng không mất nhạc (24/11/2026)

Theo log sáng 24/11 (job 1 truyện bé thất bại sau 61 phút).
- **Tải video từ Kaggle bị đứt thì tải tiếp từ chỗ đứt** (không tải lại từ đầu, không bỏ job). Job 1: Kaggle dựng xong
  video 80 MB, mạng Botkeep (~80-100 KB/s) đứt ở 7,7 MB sau 96 giây («IncompleteRead») -> mất trắng ~1 giờ. Giờ bot nối
  lại và xin phần còn thiếu (Range), link tải hết hạn thì xin link mới; chỉ bỏ cuộc khi 6 lần liền không tải thêm được byte
  nào. Áp dụng cho mọi file lấy từ Kaggle (video, kết quả, ảnh bìa, tiếng nền đã tách). Bước «tải video về» hiện số MB đã
  tải (mỗi phút một lần) để biết bot không đứng im.
- **Lyria đứt giữa chừng thì giữ phần nhạc đã nhận, mở phiên mới nối tiếp** (hoà tiếng 1,5 giây ở chỗ nối). Log 22-24/11:
  mọi phiên Lyria đóng «1006 abnormal closure» sau ~51 giây, cả hai lần thử đều vứt hết -> kho nhạc bé chưa có bài nào,
  video bé không có nhạc nền. Nhận được ít nhất 40% độ dài cần thì vẫn dùng (nhạc nền tự lặp khi dựng).
- **Lyria trả nhạc quá chậm thì bỏ ngay và nghỉ 6 giờ.** Log job 2 (24/11 chiều): phiên đầu chỉ nhận 4 giây nhạc trong
  51 giây. Luồng Lyria cần ~190 KB/s mà mạng Botkeep chỉ ~80-100 KB/s, nên máy chủ cắt kết nối. Trước đây bot nối thêm
  3 phiên và thử lại thêm 1 lần, mất ~7 phút mỗi job mà vẫn không có nhạc. Nhận chậm hơn 0,3 lần thời gian thật thì bot
  thôi luôn, ghi «bỏ qua Lyria (…mạng không theo kịp…)», 6 giờ sau mới thử lại.

## 1.4.3 — Kaggle lỗi mạng không làm hỏng job; truyện bé đỡ chờ Gemini quá tải, ý truyện chọn sẵn đồ vật (22/11/2026)

Theo log tối 22/11 (job 12, 13 truyện bé thất bại; Google quá tải, Gemini báo 503 liên tục).
- **Gửi việc lên Kaggle lỗi tạm thì gửi lại** (mất kết nối, 429, 499, 5xx; tối đa 4 lần trong ~50 giây). Trước mỗi lần gửi lại,
  bot hỏi Kaggle đã nhận việc chưa (phiên đang chờ máy / đang chạy thì thôi, khỏi chạy hai phiên tốn gấp đôi giờ GPU). Job 12,
  13 hỏng ngay lần gửi đầu vì «Connection aborted» và «Kaggle lỗi 499». Các lời gọi chỉ đọc (hỏi trạng thái, lấy đầu ra)
  lỗi tạm cũng hỏi lại 2 lần.
- **Gemini quá tải / quá giờ thì nhớ 15 phút**: lượt sau dùng ngay model kia. Trước đây mỗi truyện chờ ~4 phút
  (2 lượt nghĩ ý x 60 giây + bản nháp 120 giây) trước khi đổi sang flash-lite. Log ghi tên lỗi thay vì «lỗi ()».
- **Ý truyện chọn sẵn 3 đồ vật / việc** trong danh sách gợi ý; lời nhắc viết truyện dặn giữ đủ. Job 12: 4 lượt viết liền
  đều bị bộ kiểm báo «mới nhắc tới 2 trong các thứ gợi ý» (ý truyện không biết phải dùng các thứ đó).
- **Bếp: khối cuối (cảm thán + điểm danh + tóm tắt) chèn đúng khoảng lặng**. Chỗ chèn mà cắt đôi một câu (luật R8) thì
  dời tới khoảng lặng sau câu kế tiếp, thay vì bỏ lần lượt từng phép. Từ 1.4.2 điểm danh đứng chung khối cuối với tóm tắt
  nên một chỗ chèn xấu có thể làm mất cả hai (job 11 không có điểm danh lẫn tóm tắt).
- Job 11 (bếp) chạy 69 phút là do Kaggle không cấp máy trong 35 phút nên bot dựng trên Botkeep (giọng edge-tts, 720p):
  đúng thiết kế, không phải lỗi mã.

## 1.4.2 — bếp: đầu video chỉ để giữ chân, câu đọc liền mạch, phụ đề ngắt đúng chỗ; bé: tắt được phụ đề, ảnh ít lỗi hơn (22/11/2026)

Theo phản hồi job 8 (truyện bé) và job 9 (đồ chua).
- **Bếp: điểm danh nguyên liệu dời về cuối video**, ngay trước khúc tóm tắt (cả bản đầy đủ lẫn bản ngắn). Phần đầu chỉ
  còn cảnh thành phẩm + câu hook rồi vào thẳng video gốc. Lời đọc khúc này là «Nguyên liệu gồm…» (không còn «Chuẩn bị…»).
  Khúc tóm tắt dài 1,2 giây mỗi bước (trước 0,7 giây: thẻ TÓM TẮT tắt khi lời mới đọc được nửa). Mỗi bước trong lời tóm
  tắt không còn bị cắt giữa tên nguyên liệu («rửa sạch củ cải và cà» → «rửa sạch củ cải và cà rốt»).
- **Bếp: câu hook nêu tên món**. Bộ lọc nhận cả tên món rút gọn từ tên món / tiêu đề («Làm đồ chua thơm ngon đơn giản tại
  nhà» → «đồ chua»). Job 9: 8/8 câu «Đồ chua…» bị loại oan vì «không nêu tên món», video còn lại câu viết cùng kịch bản.
  Chưa chọn được câu hook trước thì đoạn mở đầu dài 3 giây (trước 2 giây: lượt viết thêm chỉ được 3-5 chữ).
- **Câu đọc liền mạch**: lời gốc bị máy nghe cắt mỗi 3-4 giây giữa câu, AI viết tiếp một câu qua nhiều đoạn và mỗi đoạn được
  đọc riêng với ngữ điệu kết câu («…giúp cho cà rốt và củ cải trắng» | «ra bớt phần nhựa đắng đi…»). Giờ:
  - trước khi viết: lời gốc không có dấu câu nhưng có viết hoa đầu câu thì đoạn bắt đầu bằng chữ thường được gộp với đoạn
    trước (tối đa 9 giây);
  - sau khi viết: đoạn lời mới chưa hết câu được đọc liền với đoạn sau thành một câu (tối đa 16 giây / 230 ký tự; dài
    hơn thì tách ở dấu phẩy). Không gộp câu hook, đoạn giữ tiếng gốc, khúc điểm danh / tóm tắt.
- **Phụ đề ngắt dòng đúng chỗ** (cả bếp lẫn bé): không tách số khỏi đơn vị và tên nguyên liệu («1 kg cà rốt»), không tách từ
  ghép / tên riêng («Tiếng Anh», «Thỏ Trắng»), dòng không kết bằng hư từ, ưu tiên ngắt trước «rồi», «thì», «và»… Trước đây
  chia đều theo số chữ: «Giờ mình lấy 1 | kg cà rốt gọt | vỏ rồi tới 1».
- **Cài đặt phụ đề**: `SUBS` (video bếp làm tự động / Telegram / Zoom; trang web vẫn chọn từng video) và `KIDS_SUBS` (truyện bé;
  tắt thì không in lời kể, thẻ từ tiếng Anh, chữ to câu móc, bảng từ cuối vẫn giữ, file `.srt` vẫn có).
- **Ảnh truyện bé**:
  - hành động tả thứ không vẽ được (bóng, bóng tối, tiếng động, mùi, gió…) bị bỏ khỏi prompt ảnh, lời nhắc viết truyện
    dặn tả nhân vật phản ứng thay vì tả cái bóng (job 8 cảnh 2: «chiếc bóng đen» vẽ thành khối tròn đen lơ lửng);
  - bỏ chữ «toddler» khỏi câu tỉ lệ (FLUX vẽ tã / quần lót trắng cho Thỏ và Gấu);
  - cảnh hai bạn ghi rõ bạn nào bên trái, bạn nào bên phải (job 8 cảnh 6: Thỏ có tay chân lông nâu của Gấu);
  - prompt dài quá giới hạn thì bỏ câu tả thế giới chung trước khi vượt.
  - Đã thử đo màu lông dưới đầu nhân vật để tự bắt lỗi lẫn nét: nền đất / hàng rào cùng màu nâu nên không tách được ảnh
    lỗi, chưa đưa vào.
- Kiểm ảnh OWLv2 chạy được cả với transformers 5 (bản 5 bỏ `post_process_object_detection`).

## 1.4.1 — câu hook bếp trọn ý, đoạn mở đầu dài vừa câu hook; truyện bé gây cấn hơn (21/11/2026)

- Bếp, phản hồi job 6 («câu mở không ăn nhập»): bản 1.4.0 chọn câu hook SAU khi viết kịch bản và ép vào đoạn mở đầu 2 giây
  nên câu hook chỉ còn tối đa 5 chữ («Đồ chua bị hăng?»: cụt, không hứa gì, lạc với câu «Chuẩn bị 1 kg cà rốt…» ngay sau).
  Giờ câu hook được chọn TRƯỚC, đoạn mở đầu dài vừa đọc hết câu (2-4 giây, 7-11 chữ). Câu hook phải nêu tên món, là câu trọn
  ý có lời hứa (câu hỏi phải kèm lời hứa), đúng ý lời gốc; AI chấm điểm biết câu ngay sau hook, câu cụt / không nêu món tối
  đa 4 điểm; «best» lệch điểm chấm thì theo điểm.
- Truyện bé, phản hồi job 7 («chưa gây cấn, na ná nhau») và «ép khung có cứng nhắc không»: nguyên nhân là khuôn — mỗi khung
  quy định từng cảnh làm gì (thử ba lần, bạn giúp, cảnh 7 nói bài học) nên truyện nào cũng cùng một nhịp. Giờ:
  - **Ý truyện** (`rmvideo/kids/pitch.py`): Gemini nghĩ 4 ý khác kiểu nhau, tránh ý / tiêu đề / chi tiết của các truyện gần đây;
    mã bỏ ý có từ cấm, trùng tiêu đề cũ; một lượt AI khác chấm (mở đầu có chuyện ngay, hồi hộp, bất ngờ, dễ hiểu cho bé 3-6,
    khác truyện gần đây; ý theo khuôn «thử ba lần, bạn giúp» tối đa 5 điểm) rồi mới viết truyện. Ý đã dùng lưu vào bộ nhớ
    kids_auto để lần sau tránh. Lỗi thì viết theo ý gợi ý của khung như trước.
  - **Lời nhắc không còn 8 nhịp bắt buộc**: chỉ giữ bộ xương (câu đầu cảnh 1 đã có chuyện xảy ra, leo thang tới lúc căng nhất,
    gần cuối xoay chiều / bất ngờ, cảnh 8 kết vui hoặc ấm) và giới hạn kỹ thuật. Thử mấy lần, ai giúp, điệp khúc, câu hỏi bé,
    bài học nói ra hay chỉ thể hiện qua việc làm: tuỳ truyện. Bộ kiểm bỏ lỗi «thiếu câu hỏi cho bé»; giá trị gửi gắm chỉ cần
    hiện ra ở một chỗ (không còn «cảnh 7 phải nói bài học»). Truyện mẫu chỉ để học giọng kể, không học cốt truyện.
  - Khung truyện chỉ còn là một ý gợi ý một câu (`premise`); thêm 5 khung có hồi hộp (dấu vết bí ẩn, giải cứu, đua giờ, hiểu
    lầm, khoe rồi lật ngược) làm gợi ý. Câu móc 3 giây đầu phải nêu đồ vật / chuyện cụ thể. Nhạc nền theo không khí của ý.
  - Ảnh đổi góc máy theo cảnh (thấp, cận mặt, toàn cảnh) và tả tư thế đang làm thay vì đứng nhìn thẳng.

## 1.4.0 — luôn mở đầu bằng thành phẩm hấp dẫn nhất; viết nhiều câu hook rồi chọn câu mạnh nhất (21/11/2026)

- Đoạn mở đầu (`TEASER`) rút còn **2 giây** và chọn theo khoảnh khắc **thành phẩm hấp dẫn nhất**: AI xếp hạng riêng các
  khung món đã xong, ưu tiên có hành động (cắn, kéo sợi, cắt đôi, rưới sốt); bot gửi thêm 4 khung có chuyển động ở nửa
  sau video. Bắt đầu trước khung đó 0,5 giây (cùng cảnh). Ảnh bìa vẫn là khung đẹp nhất AI chọn riêng.
- Bớt các trường hợp bỏ đoạn mở đầu: trước đây AI thấy video gốc «có món trong 12 giây đầu» là bỏ; giờ chỉ bỏ khi gốc mở
  NGAY bằng món (giây 0,5), còn không thì vẫn mở bằng thành phẩm — cảnh xếp nhất trùng phần mở đầu gốc thì thử cảnh xếp
  nhì, nhì (tối đa 3).
- Câu hook (`HOOK_PICK=1`, mới): AI viết thêm ~8 câu theo các kiểu vấn đề / mẹo bất ngờ / kết quả / tò mò / con số, bot
  loại câu sai luật (dài quá, chào hỏi, «mình thử», «triệu view», nguyên liệu / số bịa), AI chấm điểm chọn câu mạnh nhất
  (câu cũ chấm cùng). Kiểu hook lưu `hooks.json`, trạng thái tự động đăng ghép với lượt xem YouTube sau 24h.
- Câu hook của đoạn mở đầu được đọc lấn sang video chính tối đa 1,5 giây (trước 0,4) khi người gốc chưa nói.
- **Sửa lỗi video bếp không dựng được trên Kaggle**: bot gửi tiêu đề video («ĐỒ CHUA GIÒN SẦN SẬT») làm tiêu đề notebook
  dựng, Kaggle đổi slug notebook theo tiêu đề (`rmvideo-worker` -> `chua-gi-n-s-n-s-t`), bot hỏi trạng thái bằng slug cũ
  bị 404 nên bỏ Kaggle và dựng trên Botkeep bằng giọng edge-tts, trong khi phiên Kaggle vẫn chạy xong (tốn giờ GPU). Đây
  mới là nguyên nhân thật của lỗi 404 ở job 3 (1.3.4 chỉ chờ thêm 3 phút nên chưa sửa được). Giờ tiêu đề notebook luôn
  theo slug, và bot hỏi trạng thái theo slug Kaggle trả về sau khi lưu.
- Nội dung đăng luôn gửi kèm: job web / tự đăng / truyện bé báo về Telegram và Zoom kèm tin «📝 Nội dung đăng» đầy đủ
  (trước đây chỉ có link); video gửi thẳng Telegram có bài dài hơn 1.024 ký tự (giới hạn chú thích) thì gửi đủ ở tin riêng
  thay vì bị cắt.

## 1.3.5 — khôi phục các phần của 1.3.0 (bỏ nhạc gốc, nội suy khung) kèm các bản sửa 1.3.3-1.3.4 (21/11/2026)

- Video bếp bản 1.3.0 bị kém KHÔNG phải do 1.3.0: phiên dựng Kaggle báo 404 ngay khi gửi việc nên bot dựng trên Botkeep bằng
  giọng edge-tts (đã sửa ở 1.3.3 / 1.3.4). Khôi phục: bỏ nhạc gốc giữ tiếng động (`SFX_SEP=1`), nội suy khung RIFE khi kéo
  chậm (`INTERP=1`).
- Hai phần người dùng thấy kém thật vẫn có nhưng mặc định TẮT: Real-ESRGAN cho ảnh truyện bé (ảnh «cảm giác nhựa»;
  `IMG_UPSCALE=esrgan` để bật) và cắt khoảng lặng đầu mẩu giọng («tiếng bị sớm»; `VOICE_LEAD_TRIM=1` để bật).

## 1.3.4 — Kaggle không bỏ cuộc vì 404 lúc mới gửi việc; truyện bé đỡ lặp tình tiết (21/11/2026)

- Kaggle: vừa gửi notebook, Kaggle có thể trả 404 «Not found» khi hỏi trạng thái (phiên chưa kịp tạo). Trước đây bot coi là
  lỗi và dựng trên Botkeep bằng giọng edge-tts (job 3 ngày 21/11: 404 sau 4 giây -> đọc «100 dê muối», giọng khác hẳn).
  Giờ chờ tiếp tới 3 phút.
- Truyện bé đỡ lặp «chạy rồi té», «đã hứa thì giữ lời»: bot nhớ chi tiết / đồ vật chính của 4 truyện gần nhất và dặn model
  không dùng lại; khung truyện không còn viết sẵn «vấp té Bịch!», «Rầm!», «cả hai ngã» (model chép gần nguyên chữ); lời
  dặn không còn gợi ý điệp khúc «Chạy, chạy, chạy nào!» và nói rõ rắc rối không phải lúc nào cũng là té ngã.

### Chi tiết
- `kaggle_api.wait`: 404 / NOT_FOUND trong `NOT_FOUND_GRACE` (180s) đầu coi là đang chờ.
- `story.MOTIFS` / `motifs()` / `avoid_list()`: nhãn chi tiết (té ngã, chạy vội, đồ rơi, thả diều, trái banh, quên lời
  hứa, làm vỡ đồ) + `prop_vi`, ghi ở `kids_auto.remember_done` (`motifs`), `pick()` trả `avoid`, `prompts.user_prompt`
  đưa vào lời nhắc. Truyện cũ chưa có `motifs` thì chưa tránh được (bắt đầu từ truyện làm sau bản này).
- Test: `tests/test_kids_variety.py`, `test_review31.py::test_kaggle_wait_tolerates_404_right_after_push`.

## 1.3.3 — giọng dự phòng đọc đúng đơn vị, «1/2 kg» thành «nửa ký» (21/11/2026)

- Kaggle lỗi phải đọc giọng trên Botkeep (edge-tts / CapCut) thì số và đơn vị cũng được đổi sang chữ trước như giọng
  VieNeu: «50g» đọc «năm mươi gam» thay vì «năm mươi g» (phản hồi 21/11 về video bản 1.3.0).
- «1/2 kg» đọc «nửa ký» (trước đây «nửa kg»: đơn vị đứng sau phân số không được đọc thành chữ).

### Chi tiết
- `tts_engine.synth_clip_ex` gọi `vi_text.normalize`; phụ đề vẫn lấy nguyên văn kịch bản (align.map_script khớp phần lệch).
- `vi_text.normalize`: phân số kèm đơn vị đọc một lượt. Test: `tests/test_tts_units.py`.

## 1.3.2 — gỡ các phần của 1.3.0, quay về cách làm video của 1.2.0 (21/11/2026)

- Gỡ bỏ nhạc gốc / giữ tiếng động (Bandit), phóng nét Real-ESRGAN cho truyện bé, nội suy khung RIFE và cắt khoảng lặng
  đầu mẩu giọng của bản 1.3.0: video ra kém tự nhiên hơn bản 1.2.0 (phản hồi 21/11). Cách làm video về đúng như 1.2.0;
  giữ phần của 1.3.1 (log theo giờ Việt Nam, giờ GPU Kaggle còn lại trên web và log).
- Cài đặt SFX_SEP / INTERP không còn; giá trị đã lưu trong settings.json được bỏ qua.

## 1.3.1 — log Botkeep theo giờ Việt Nam, xem giờ GPU Kaggle còn lại (21/11/2026)

- Giờ trong log Botkeep (và log notebook Kaggle) là giờ Việt Nam (UTC+7), bất kể máy chủ đặt múi giờ nào.
- Web: dòng «☁️ Kaggle: còn 21,5 / 30 giờ GPU tuần này, làm mới 07:00 22/11» dưới tiêu đề Hàng đợi (tự cập nhật mỗi 5
  phút). Log Botkeep ghi số giờ GPU còn lại lúc khởi động và sau mỗi phiên Kaggle.

### Chi tiết
- `logging.Formatter.converter` = gmtime(giây + 7 giờ) trong `main.py` / `kaggle_job.py`.
- `kaggle_api.quota_cached` (nhớ 5 phút; sau phiên Kaggle đọc mới), `quota_text`, `vn_time` (giờ làm mới đổi sang UTC+7);
  `kaggle_worker.log_quota` sau mỗi phiên phân tích / dựng / lô (chưa có token thì không gọi); web `/api/kaggle/quota`;
  lệnh console `kaggle_test` dùng cùng câu chữ. Test: `tests/test_kaggle_quota.py`.

## 1.3.0 — bỏ nhạc gốc giữ tiếng động, ảnh truyện bé nét hơn, đoạn kéo chậm hết giật, lời vào đúng nhịp (21/11/2026)

- Kênh bếp: sau khi bỏ giọng người gốc, tiếng nền được tách tiếp thành nhạc / tiếng động; chỉ giữ tiếng động (xèo xèo,
  dao thớt, nước sôi), nhạc gốc bỏ hẳn nên bot tự lót nhạc Lyria dưới lời. Bật sẵn (`SFX_SEP=1`); model này chỉ dùng phi
  thương mại: KHI BẬT KIẾM TIỀN cho kênh thì đặt `SFX_SEP=0`.
- Truyện bé: ảnh phóng bằng Real-ESRGAN thay cho phóng thường: viền, mắt, áo nét hơn mà vẫn giữ lớp lông nỉ; Ken Burns
  dùng bản 2160x3840 nên phóng / lia không mờ.
- Kênh bếp: video gốc bị kéo chậm (tới 1,4 lần cho đủ thời lượng) không còn giật — RIFE chèn khung giữa mỗi cặp khung
  trước khi dựng (`INTERP=1`).
- Giọng đọc: bỏ khoảng lặng đầu mỗi mẩu giọng (VieNeu mở đầu bằng ~0,1 giây im): lời vào đúng nhịp hình, đỡ phải nói
  nhanh cho vừa khe.

### Chi tiết
- `rmvideo/media/stems.py` + `rmvideo/media/bandit/` (mã Bandit, Apache-2.0, bản rút gọn của FreeVideo; phần biến đổi phổ
  viết lại bằng torch.stft / torch.istft vì torchaudio không còn bản cho torch mới, cùng tên bộ đệm nên nạp được
  checkpoint gốc). Trọng số Bandit Plus «cocktail» (Watcharasupat và cộng sự, Zenodo 10160698, CC BY-NC 4.0 — xem
  `rmvideo/media/bandit/WEIGHTS-CC-BY-NC-4.0.txt`), tải từ bản FreeVideo đã chuyển state dict, ghim revision + SHA-256.
  `vocal_sep.strip_music`: chạy sau khi tách giọng thành công (RoFormer hoặc Kim), tiến trình riêng, chỉ Kaggle GPU;
  nhạc thấp hơn tiếng động 20 dB thì giữ nguyên; lỗi / quá 5 phút thì trả lại tiếng nền cũ. Renderer đo «điểm nhạc» trên
  tiếng nền đã tách nên tự lót nhạc khi nhạc gốc đã bỏ. Đo 21/11 (CPU): video bếp có nhạc Lyria — điểm nhạc tiếng nền
  0,258, phần tiếng động 0,0, phần nhạc 0,948; ba phần cộng lại khớp bản gốc -36 dB; 30 giây audio 96 giây CPU.
- `rmvideo/kids/esrgan.py`: SRVGGNetCompact (Real-ESRGAN, BSD-3-Clause), trộn trọng số realesr-general-x4v3 (khử nhiễu
  mạnh) 0,2 + wdn-x4v3 (nhẹ) 0,8 như tuỳ chọn «-dn» của Real-ESRGAN: bản mạnh làm lông nỉ thành da nhựa (đo trên ảnh
  truyện bé), 0,2 giữ lông. `images.upscale` ghi `scene_XX.png` 1080x1920 (gửi về Botkeep như cũ) + `scene_XX_hd.png`
  2160x3840 cho dựng (`images.for_render`); không GPU / lỗi thì lanczos như cũ. CPU 13,8 giây / ảnh (T4 nhanh hơn nhiều).
- `rmvideo/media/interp.py` + `rmvideo/media/rife.py` (RIFE 4.25 IFNet, MIT, từ FreeVideo; model 22MB ghim SHA-256):
  `kaggle_job.produce` gấp đôi khung video gốc khi kéo chậm ≥ 1,1 lần (ffmpeg giải mã -> khung giữa -> ffmpeg mã hoá, tiếng
  chép nguyên, cùng thời lượng nên mọi mốc kế hoạch giữ nguyên; `out_fps` chia đều nên tốc độ khung đầu ra không đổi). Hai
  khung khác hẳn nhau (chuyển cảnh) thì chép khung trước. Đo tốc độ trên 24 cặp đầu, dự tính quá 5 phút thì bỏ. Đo 21/11
  trên 3 video bếp (đoán khung giữa từ khung trước/sau, so khung thật): PSNR 26,5-28,6 dB, lặp khung (cách cũ) 20,8-23,3,
  trộn hai khung 23,7-26,7.
- `voiceplan.speech_span` + `compose_voice`: cắt khoảng lặng đầu mẩu (giữ 0,05s, tối đa 0,5s), mốc từ edge-tts dời theo.
  Đã xem cách FreeVideo khớp lời (đọc lại 2-3 lượt bằng tham số tốc độ của TTS rồi mới kéo nhẹ ≤ 1,25 lần): VieNeu
  (vieneu 3.8.5, TTS kiểu mô hình ngôn ngữ) không có tham số tốc độ nên chưa áp được phần đó; bot vẫn giữ một tốc độ
  chung ≤ 1,12 lần như trước.
- Cài đặt mới (nhóm ☁️ Kaggle): `SFX_SEP`, `INTERP`. Test: `tests/test_sfx_sep.py`, `test_kids_esrgan.py`,
  `test_interp.py`, `test_voice_lead.py` (phần cần torch chạy khi máy có torch).

## 1.2.0 — đăng YouTube Shorts + gói đăng TikTok cho kênh bếp và kênh truyện bé (20/11/2026)

- Video đã hẹn đăng Fanpage được tải lên YouTube cùng giờ hẹn: kênh bếp và kênh truyện bé là hai kênh YouTube riêng.
  Kết nối bằng nút «▶️ Kết nối YouTube» trên web (Google hỏi chọn kênh), không phải chép token tay.
- Tiêu đề / mô tả dùng phần «▶ YouTube Shorts» AI đã viết sẵn; kênh bé khai «dành cho trẻ em».
- Lỗi YouTube tách khỏi Facebook: tự thử lại, hết hạn mức chờ 6 giờ, token bị thu hồi thì báo admin kết nối lại.
- TikTok bán tự động: 10 phút trước giờ vàng, Telegram nhận file video + một tin riêng chỉ có chữ đăng TikTok
  (Zoom: chữ + link tải) để đăng tay ~30 giây hoặc hẹn giờ trong TikTok Studio. TikTok không cho bot đăng
  thẳng lên tài khoản của chính mình (API chỉ đăng «chỉ mình tôi» khi chưa kiểm duyệt; kiểm duyệt từ chối
  công cụ đăng cho tài khoản của mình và ứng dụng chép nội dung từ nền tảng khác).

### Cần làm
- Tạo OAuth client trên Google Cloud, dán `YT_CLIENT_ID` / `YT_CLIENT_SECRET`, bấm kết nối từng kênh (README, mục
  «▶️ Đăng YouTube Shorts»). Chưa kết nối thì bot chạy như cũ, chỉ đăng Facebook.

### Chi tiết
- `rmvideo/app/youtube.py`: OAuth 2.0 web (state ngẫu nhiên dùng một lần 15 phút: cookie phiên SameSite=Strict không
  đi kèm lượt Google chuyển về), refresh token lưu cài đặt bí mật (`YT_REFRESH_TOKEN`, `KIDS_YT_REFRESH_TOKEN`, không
  gửi sang Kaggle: tiền tố `YT_` / `KIDS_`), tải lên resumable bằng aiohttp (không thêm thư viện), `publishAt` khi giờ
  hẹn còn ≥ 15 phút, `selfDeclaredMadeForKids`, lượt xem sau 24 giờ. Hàng chờ `youtube.json` (`YT_STATE`); ghi từng mục
  bằng đọc-sửa-ghi để autopost / kids_auto thêm video trong lúc đang tải lên không bị ghi đè.
- `autopost._finish_pending` / `kids_auto._finish_pending`: Fanpage nhận bài xong thì `youtube.after_facebook` (lỗi gì
  cũng không làm hỏng bài Facebook). `main.py` chạy `youtube.run_forever` (mỗi 2 phút).
- Web: `/api/youtube`, `/api/youtube/{cook|kids}/{connect|test|disconnect}` (kết nối / ngắt phải nhập lại mật khẩu web
  như token Fanpage), `/youtube/callback`. Trạng thái YouTube hiện trong ô 🤖 Tự động đăng và 🧸 Truyện bé.
- `rmvideo/app/tiktok_kit.py`: `TIKTOK_KIT` / `KIDS_TIKTOK_KIT` = slot (mặc định, gửi trước giờ hẹn Fanpage 10
  phút) | ready (gửi ngay, tự hẹn giờ trong TikTok Studio) | off. Caption = phần «▶ TikTok» của gói nội dung.
  Không kênh nào nhận được thì thử lại 10 phút / lần (3 lần); quá giờ vàng 6 giờ (bot tắt lâu) thì bỏ, không
  gửi gói cũ. Hàng chờ `tiktok_kit.json` (`TIKTOK_STATE`). `tests/test_tiktok_kit.py`.
- Soát lại trước khi gộp: video còn chờ gửi gói TikTok / tải lên YouTube không bị `storage` dọn (OUT_MAX_MB chỉ giữ
  ~10 video mà gói TikTok có thể chờ hơn một ngày), ổ đầy thật thì vẫn xoá sau cùng để job mới có chỗ; video YouTube chờ
  quá 3 ngày thì bỏ. Giới hạn theo phút / HTTP 429 thử lại sau 10, 20, 40… phút (trước đây chờ 6 giờ như hết hạn mức
  ngày, lỡ giờ hẹn); HTTP 401 không còn dừng kênh mà xin access token mới (token có thể hết hạn giữa lượt tải 30 phút; xin
  token mới nếu còn dưới 30 phút); dấu khoá lấy trước khi tải và gồm cả client id/secret (kết nối lại giữa lúc tải không
  bị dừng nhầm; sửa secret là chạy lại; «Thử YouTube» được cũng bỏ dừng); giờ thử lại tính từ lúc lỗi; YouTube để video
  khác chế độ yêu cầu / từ chối video thì báo admin; nút «Ngắt YouTube» báo rõ khi token đặt bằng biến môi trường (trước
  đây báo «đã ngắt» mà token môi trường vẫn chạy); gói TikTok lỗi ở tin chữ thì lượt sau không gửi lại video.
- Codex (PR #16): lưu địa chỉ phiên tải resumable TRƯỚC khi gửi file; lượt thử lại / bot khởi động lại hỏi phiên
  (`Content-Range: bytes */size`): YouTube đã nhận đủ (mạng rớt đúng lúc trả kết quả) thì lấy id thay vì tải lại
  thành video trùng, còn dở thì tải tiếp từ byte đã nhận (đỡ gửi lại cả file trên đường truyền yếu), phiên hết
  hạn (404) thì mở phiên mới. Bot dừng giữa lúc tải không còn bỏ video «không rõ» mà tải tiếp.
- Test: `tests/test_youtube.py` (máy chủ Google giả: đổi mã, refresh token, tải lên, hạn mức, token bị thu hồi, bot dừng
  giữa lúc tải, web).

## 1.1.3 — kênh bếp: video mở bằng món đã xong, đoạn điểm danh hết chữ trùng; kênh bé: kiểm ảnh cỡ nhân vật (20/11/2026)

- Kênh bếp (dựng mạnh): câu đầu kiểu «Hôm nay mình chỉ mọi người công thức…» không còn bị coi là lời chào — giữ làm câu mở
  đầu (cảnh món đã xong của bản gốc), điểm danh nguyên liệu đứng SAU nó. Job 2 ngày 20/11 (canh chua cá lóc): video mở bằng
  nồi nước còn khóm, chữ hook đè lên nồi, tô canh chua của bản gốc bị đẩy xuống giây 6.
- Kênh bếp: đoạn điểm danh nguyên liệu không còn phụ đề — thẻ «1/4 trái khóm» đã hiện từng món, phụ đề lặp đúng chữ đó ngay
  bên dưới mà lệch nhịp (thẻ «4 muỗng đường» trong lúc phụ đề còn «50g me cục»).
- Giọng miền Nam: «nhé» -> «nha» (lời kết job 2 «Mọi người thử ngay nhé»).
- Kênh bé (video job 1 ngày 20/11, bản 1.1.2): bộ kiểm ảnh so cỡ hai bạn theo BỀ NGANG đầu (≤ 1,35 lần) thay vì diện tích
  (≤ 4 lần) — cảnh 7 Gấu to gấp đôi Thỏ lọt qua vì khung đầu thỏ gồm cả tai. Thỏ đứng một mình không còn bị báo «2 đầu»
  nhầm (OWLv2 tách tai và mặt thỏ thành hai khung) nên đỡ vẽ lại thừa.
- Kênh bé: mô tả Thỏ «lông trắng từ đầu tới chân», bỏ chữ «small» (cảnh 2 Thỏ mang thân lông nâu của Gấu khi đứng sát
  nhau; «small» cãi câu «hai bạn cao bằng nhau» của prompt).

### Chi tiết
- `style_b._hook_end` dùng `_SALUTE_RE` (chỉ «xin chào», «chào mừng / cả nhà / mọi người / các bạn / bà con», «hello») thay cho
  `prompt_engine._GREET_RE` (bắt cả «hôm nay mình…», mẫu đó vẫn dùng để nhận đoạn mở đầu khi AI viết lời). Phần chào thật vẫn
  bị bỏ như bản 43.
- `filtergraph.montage_windows(edit, teaser)` + `subtitle_texts(quiet=)`: đoạn lời có mốc bắt đầu trong quãng điểm danh (trục
  video ra, đã cộng đoạn mở đầu) không phụ đề; giọng vẫn đọc, thẻ nguyên liệu vẫn hiện. Áp cho cả Botkeep và Kaggle (cùng
  `assemble`).
- Soát cả video job 2 (108 giây gốc -> 114 giây): lời đủ bước, đúng số liệu (Whisper nghe lại khớp kịch bản); −13,9 LUFS; có
  nhạc lót Lyria (điểm nhạc 0,186; gốc 0,004 — gốc không nhạc); quãng không lời còn tiếng nền + nhạc (−23 đến −25 dB); nguồn
  dọc nên khung không đổi; không có chữ in cứng.
- `imgcheck._groups`: hai khung bên trong chồng dọc thẳng hàng (`_stacked`, luật `_is_body` theo một chiều) là hai phần của
  một bạn, khung ngoài không bị bỏ như khung nhóm. Đo lại 87 khung hình video kid job 1 (1.1.2): 9 khung Thỏ một mình bị
  đếm 2 đầu -> 0; video job 1 cũ (85 khung): 1 -> 0, các khung nhân đôi thật (Gấu ôm hai Thỏ, gấu con thừa) vẫn 3 đầu.
  `sizes_ok`: bề ngang, `SIZE_RATIO` 4 (diện tích) -> 1,35 (bề ngang): hai bạn cỡ nhau đo được 1,00-1,15, cảnh lệch cỡ
  1,45-1,50. Chưa bắt: cảnh mà OWLv2 không thấy đầu bạn kia (gấu điểm < 0,18) thì không xét cỡ; lông Gấu lan sang thân
  Thỏ chưa có cách đo tin được (màu nâu dưới đầu thỏ trùng màu lối đất) nên sửa ở prompt.
- Soát cả video kid job 1 (86,8 giây): không nhân vật nhân đôi; từ tiếng Anh đúng (Whisper tiếng Anh nghe «balloon»,
  «sing», «happy» cả phần ôn cuối); −14,1 LUFS; lời mạch lạc, có điệp khúc, câu hỏi cho bé, kết vòng lại đầu truyện.

## 1.1.2 — truyện bé: bắt nhân vật nhân đôi, từ tiếng Anh giọng bản xứ; kênh bếp: nhạc Lyria, khung dọc theo nội dung (20/11/2026)

- Kênh bếp: khung dọc theo nội dung (`FRAMING_AUTO`, mặc định bật): nguồn có dải đen thì bỏ dải đen; nguồn ngang cắt
  khung 9:16 theo vùng có tay / chảo, hoạt động rải khắp khung thì đặt nguyên khung lên nền mờ thay vì cắt mất 2/3 hình.
- Kênh bếp: dò cảnh người gốc nói vào máy (YuNet) và cận neo đáy xuống tay / món (`TALK_ZOOM`, tắt mặc định — xem chi tiết).
- Kênh bếp: dò chuyển cảnh thích nghi (so mỗi khung với các khung quanh nó, theo AdaptiveDetector của PySceneDetect)
  thay ngưỡng cứng: máy rung / lia nhanh không còn bị tính là chuyển cảnh, cắt nhảy cùng bối cảnh được bắt. Đo 20/11:
  F1 0,879 -> 0,933 (Botkeep mức nhanh 0,893 -> 0,966), CPU +2-5%.
- Kênh bếp, xoá chữ LaMa (Kaggle): chỉ vẽ lại chỗ có chữ thật trong dải (nét chữ từng khung, nới phủ viền / bóng)
  thay cho cả hình chữ nhật — tay, đồ ăn, nồi đi qua dải giữ nguyên; phụ đề 2 dòng tràn ra ngoài dải vẫn xoá trọn; mặt nạ
  giữ thêm 3 khung trước / sau chữ, nền đứng yên trộn với khung trước nên dải đỡ nhấp nháy. `TEXT_INPAINT=band` = cách cũ.
- Giọng VieNeu (kênh bếp + truyện bé, Kaggle): Whisper large-v3-turbo nghe lại từng câu vừa đọc; câu đọc lặp / sai nhiều
  (CER > 0,25 hoặc một cụm 3 từ nghe lặp) thì đọc lại, tối đa 2 lần, giữ bản tốt nhất; còn sai thì ghi chú trong job.
  `VOICE_QA=0` để tắt; không có GPU / Whisper lỗi thì giữ giọng như cũ.
- Kênh bếp, thử nghiệm: tách giọng gốc bằng Mel-Band RoFormer trên Kaggle (`VOICE_SEP_MODEL=auto`, mặc định vẫn `kim`).
- Kênh bếp: nhạc nền (video không lời) và nhạc lót dưới lời đọc lấy từ kho tự tạo bằng Lyria RealTime như truyện bé
  (`BGM_SOURCE=lyria`, mặc định): 3 màu theo món (ấm cúng / sôi động / thư giãn), tạo dần trong nền, đẩy lên Kaggle thành
  dataset `rmvideo-cook-bgm`, không cần PUBLIC_URL, không bị tắt tiếng vì bản quyền. Lệnh `cook_bgm` / nút web tạo đủ kho.
- Truyện bé: từ tiếng Anh (câu dạy từ, câu ôn từ màn kết) đọc bằng giọng Anh bản xứ (edge-tts, `KIDS_EN_VOICE`, mặc định
  en-US-JennyNeural), phần tiếng Việt vẫn VieNeu, nối lại trong clip của cảnh. Đo bằng Whisper trên video job 1: VieNeu đọc
  «sun» thành «súng / xuống», «jump» thành «Trân / trâm»; bản mới Whisper nghe ra đúng «Sun», «Jump», «Happy».
- Truyện bé: bộ kiểm ảnh bắt được nhân vật nhân đôi trên ảnh vẽ bằng LoRA (job 1: Gấu ôm HAI Thỏ ở cảnh 7, thêm một gấu con ở
  cảnh 8 — bộ kiểm cũ cho qua cả hai). Ngưỡng đầu 0,3 -> 0,18, khung bao nhiều đầu không nuốt các đầu bên trong, đầu thứ hai
  lệch sang bên không bị coi là thân áo, thêm phép đếm theo loài. Đo trên 85 khung hình video job 1: bắt 13/13 khung nhân
  đôi (trước: 0), 0 khung báo nhầm ở 7 cảnh sạch.
- Truyện bé: prompt ảnh nói đúng số bạn (cảnh 1 bạn không còn «All friends…»), «each appearing only once» ngay đầu prompt;
  vật đang dạy («mặt trời đó») phải có trong hình (job 1 cảnh 1 dạy «sun» mà hình không có mặt trời).

### Chi tiết
- **Khung dọc theo nội dung** (`rmvideo/media/framing.py`): đo một lần mỗi job trên Botkeep (8 cặp khung xám 240px cách
  0,25 giây, cùng chỗ với chọn tông màu): hàng / cột tối ở MỌI khung mẫu là dải đen (cảnh tối không bị nhận nhầm); nội dung
  ngang (rộng/cao > 0,66) đo chuyển động theo cột, mỗi cặp khung chuẩn hoá riêng; cửa sổ 9:16 chứa ≥ 55% chuyển động -> cắt
  cửa sổ đó, ít hơn mà nguồn rất ngang (≥ 1,2) -> nguyên khung vừa bề ngang trên nền mờ của chính nó (nền làm ở 1/8 cỡ,
  gần như không tốn CPU), nguồn hơi ngang -> cắt giữa như cũ. `look_chain` dùng khung đã chọn ở mọi nơi (video chính, khúc
  kế hoạch dựng, clip phụ, đoạn mở đầu, ảnh bìa; nhãn bộ lọc riêng mỗi lần gọi); dải chữ gốc đổi sang khung ra
  (`map_bands`: che mờ + vị trí phụ đề); Kaggle nhận `spec["framing"]`. Đo trên video nấu ăn Wikimedia Commons (giấy phép
  mở): video ngang trong khung dọc có dải đen -> bỏ 69% khung đen, nền mờ; nguồn ngang bếp ngoài trời và quay tay -> nền
  mờ (tâm chuyển động lệch 73-195/240 cột giữa các cảnh, cắt cố định nào cũng mất chảo ở vài cảnh); nguồn vuông -> cắt.
  Đo 5-9 giây / video trên máy phát triển đang tải nặng. Cắt cửa sổ theo TỪNG CẢNH (chủ thể đổi chỗ) chưa làm.
- **Cảnh người gốc nói vào máy** (`rmvideo/media/talking.py`, `TALK_ZOOM`): YuNet (OpenCV Zoo, MIT, 230KB ONNX, tải từ
  Hugging Face / GitHub media, kiểm SHA-256, chạy bằng onnxruntime có sẵn) trên ảnh thu nhỏ bước phân tích đã trích, ≤ ~1
  ảnh/giây (46-60 ms/ảnh 384px trên CPU). Bỏ mặt đứng yên tuyệt đối qua ≥ 6 ảnh (đo: hai bức tranh chân dung treo tường
  được nhận là mặt 0,88-0,90 điểm); mỗi ảnh lấy mặt cao ≥ 60% mặt lớn nhất và ≥ 10% khung; quãng có lời gốc dài ≥ 2 giây,
  miệng (phân vị 80) ở trên 42% khung -> khúc chính nằm ≥ 50% trong quãng cận vừa đủ cắt miệng (≤ 1,6), neo đáy
  (`edl.Piece.ay`, `look_chain(anchor_y=)`, `zoomed_bands` theo neo; bản lưu cũ không đổi). Tắt mặc định: trên video hai
  người dẫn đứng nói, cận neo đáy chỉ còn thân người cụt đầu; chuyển động dưới miệng không tách được «tay đang làm món»
  (trung vị 0,04-0,13) với «đứng nói, vung tay» (0,20). Bật khi nguồn chủ yếu «vừa nói vừa làm món».
- **Dò chuyển cảnh thích nghi** (`scenes.py`): vẫn một lượt giải mã 320px như cũ, nhưng `scdet` in độ khác khung trước
  (mafd) của mọi khung. Điểm cắt là khung có mafd ≥ 8 và gấp ≥ 3 lần trung bình ±2 khung lân cận (đổi rất mạnh ≥ 16 chỉ
  cần gấp 2,2: máy rung kèm bỏ khung ở mức fast); khung trùng (mafd≈0) bỏ qua; vẫn giữ khoảng cách 0,8 giây. Bộ cũ giữ ở
  `scenes.CUT_DETECTOR = "fixed"`; FFmpeg chưa có scdet (< 4.4) tự dùng bộ cũ. Đo bằng nhãn tay (lệch ≤ 0,25 giây), FFmpeg
  1 luồng, giải mã full + fast, trên video nấu ăn Wikimedia Commons (100 giây 10 cắt; 58 giây quay tay 0 cắt; 9,6 giây 0 cắt),
  video ghép có hơi nước / nhấp nháy / loé trắng / 2 hoà tan 0,5 giây và video 12 cắt nhảy cùng bối cảnh: cũ P 0,911 R 0,850
  F1 0,879, mới P 0,933 R 0,933 F1 0,933. Cắt cứng bắt 56/56 (cũ 51/56); bỏ được cú lia nhanh bộ cũ báo nhầm. Hoà tan cả hai
  đều không bắt (đã thử mafd 4 khung/giây và màu trung bình: không tách được khỏi máy rung; TransNetV2 trên Kaggle bắt được
  nhưng cần trọng số + torch, chưa làm); loé trắng một khung cả hai đều báo nhầm. CPU cả lượt phân tích video 100 giây: fast
  4,5 -> 4,8 giây, full 9,0 -> 9,5 giây. Khoá cache `scenes` không đổi: bản đã duyệt dựng lại vẫn dùng điểm cắt đã duyệt.
- **Xoá chữ theo nét chữ** (`rmvideo/media/inpaint.py`, `MASK="text"`): pixel chữ (sáng sát tối như textzone) -> cắt XY
  thành dòng chữ, bỏ cụm thấp < 1% khung hay mỗi hàng < 3 nét (mép mực trắng trên thớt tối), chỉ lấy cụm đứng yên (khung kề
  chồng ≥ 50%), nới 0,8% chiều cao; cụm nằm ≥ 1/3 trong dải được lấn sang phần ngữ cảnh (dải textzone hẹp với phụ đề 2 dòng).
  `HOLD=3` khung trước / sau (đọc trước 4 khung), `BLEND=0.6` trộn vùng vẽ khung trước ở cột nền đứng yên. Không dùng STTN:
  kho researchmm/STTN không ghi giấy phép cho mã / trọng số, trọng số chỉ trên Google Drive. Đo 20/11 trên CPU (video
  720x1280 10 giây có tay, mực trắng, dao đi qua; phụ đề drawtext trắng viền đen có hiện / tắt dần và 2 dòng; bản không chữ
  làm đáp án; LaMa nửa cỡ như nguồn 1080p): trong dải, lệch gốc ngoài chữ MAE 12,8 -> 5,6 (PSNR 20,8 -> 28,2 dB; mức sàn
  nén lại 3,7 / 32,8), trên chữ 31,4 -> 14,4 (14,0 -> 21,0 dB); khung còn chữ 14,4% -> 0%; đổi giữa hai khung lệch gốc
  10,2 -> 6,0 (vùng chữ 14,3 -> 8,8). Vẽ thừa ngoài chữ 44% dải -> 14%. CPU thêm ~6 ms/khung ở 1080x1920 (~16 giây cho video
  90 giây). Chưa đo trên T4; chữ vàng / chữ trong ô nền màu chưa thử (ô nền màu: `TEXT_INPAINT=band`).
- **Nghe lại giọng VieNeu** (`rmvideo/media/voice_qa.py`, `VOICE_QA`): `kaggle_job.make_synth` (nhánh VieNeu) bọc synth;
  faster-whisper large-v3-turbo (cuda, int8_float16, vi, beam 5) nghe từng mẩu ≥ 4 từ, hai bên chuẩn hoá qua
  `vi_text.normalize` (số -> chữ), giữ dấu thanh. Hỏng khi CER > 0,25 hoặc một cụm 3 từ nghe ra lặp nhiều hơn trong lời;
  đọc lại tối đa 2 lần (VieNeu ngẫu nhiên: 12/12 lần đọc lại ra file khác), giữ bản không hỏng / CER thấp nhất; thống kê
  `timings.voice_qa` {checked, redo, worst_cer, secs}. Đo 20/11 trên 76 clip (Thục Đoan + Mỹ Duyên, Whisper turbo CPU): 54
  câu nấu ăn đọc đúng CER trung vị 0,022, cao nhất 0,097 (Whisper nghe nhầm giọng Nam «nêm» -> «nem»); câu có từ tiếng Anh
  VieNeu tự đọc tới 0,197. Đoạn lỗi 20/11 («Bé nói theo nè, súng» hai lần) đọc lại 3 lần thì 2 lần lặp y lỗi cũ (CER chỉ
  0,19-0,21, lẫn với câu đúng — bắt bằng cụm 3 từ lặp: 3/3 clip lặp, 0/74 báo nhầm), 1 lần đúng; chạy thật hết đường
  make_synth: bản đầu lặp, đọc lại 1 lần là đúng (CER 0,049). Giới hạn: đọc sai MỘT con số trong câu dài chỉ làm CER lên
  ~0,05-0,07, chưa bắt được. `faster-whisper` cài bằng lệnh pip RIÊNG nối cuối (`kaggle_worker.PIP_QA`): cài lỗi thì chỉ mất
  bước nghe lại. Thời gian trên T4 chưa đo (ước vài chục giây / job: tải model 1,6 GB + ~0,2-0,5 giây / mẩu).
- **Tách giọng RoFormer** (`vocal_sep.py`, `VOICE_SEP_MODEL=auto`, thử nghiệm): phiên phân tích Kaggle (GPU) tách bằng
  Mel-Band RoFormer (Kimberley Jensen, `vocals_mel_band_roformer.ckpt` 913MB qua audio-separator 0.48.0 MIT; file trùng
  SHA-256 `MelBandRoformer.ckpt` ở HF KimberleyJSN/melbandroformer, thẻ model ghi MIT; repo GitHub không có LICENSE, dữ liệu
  train không công bố) trong tiến trình con ≤ 7 phút; lỗi / quá giờ / không CUDA thì Kim_Vocal_2 như cũ. Lấy giọng từ
  `demix` (đúng biên độ), mặt nạ phổ k=8, không lượt 2. Đo 20/11 trên 3 hỗn hợp 45 giây biết trước (giọng edge-tts + nền
  thật / xèo tổng hợp / xèo nhỏ hơn giọng 25 dB; cửa sổ 250ms như bản 32), so với Kim 2 lượt + mặt nạ: giọng sót -46,5 /
  -56,6 / -74,4 dB (Kim -46,6 / -57,3 / -69,2), xèo >3,5kHz lúc có lời mất 0,5 / 0,1 / 1,1 dB (Kim 0,8 / 0,5 / 1,8). CPU ~145
  giây máy cho 10 giây audio (một lượt Kim ~21 giây). Mặc định vẫn `kim`: chưa chạy thật trên Kaggle (gói đòi numpy ≥ 2 +
  onnx-weekly / protobuf mới, tải 913MB mỗi phiên), lợi ích đo được nhỏ. Bật `auto` rồi xem log một video.
- **Soát lại cả bản 1.1.2 (20/11)**: kho Lyria bếp đủ bài mà còn bài chưa lên Kaggle (lần đẩy trước lỗi, kho tạo lúc
  Kaggle tắt) thì tự đẩy trong nền (`bgm.fill_later`); model dò mặt không tải được thì báo lỗi MỘT lần thay vì thử tải lại
  với từng ảnh (mỗi lần tới 60 giây); khung dọc khớp theo TỈ LỆ khung, đổi cỡ vùng cắt khi Kaggle tự tải nguồn ở độ phân
  giải khác bản Botkeep đã đo (trước đây Kaggle cắt giữa mà phụ đề vẫn đặt theo khung mới); nguồn hơi ngang không rõ chủ thể
  giữ cách cũ, không ghi chú rỗng; lý do vẽ lại ảnh ghi đúng số bạn cùng loài; từ dạy «run» trùng chữ tiếng Việt («run run vì
  sợ») chỉ đọc giọng Anh ở câu dạy / câu ôn từ (`dialogue.VI_HOMOGRAPHS`). Giữ nguyên: dựng lại bản đã duyệt vẫn dùng điểm
  cắt đã lưu (bộ nhớ đệm `scenes`), để khớp đúng lời đã duyệt.
- **Nhạc nền bếp bằng Lyria**: lõi kho nhạc Lyria tách từ `rmvideo/kids/bgm.py` thành `rmvideo/media/lyria.py` (`Lib`: thư mục,
  dataset, các màu, độ dài bài) dùng chung; `rmvideo/kids/bgm.py` giữ nguyên giao diện. Bếp (`bgm.py`): `MOODS` cozy / upbeat /
  chill (prompt chỉ tả nhạc cụ, nhịp, cảm giác — Lyria lọc tên nghệ sĩ), bài 150 giây (video dài hơn thì nhạc lặp như cũ),
  `mood_for` đếm chữ khớp trong tên món + tiêu đề + mô tả (hoà / không khớp: ấm cúng, review: sôi động). `choose`: đúng màu,
  thiếu thì màu khác, kho trống thì thư viện của mình; dựng trên Kaggle chỉ lấy bài đã đẩy dataset. Màu thiếu bài thì
  `fill_later` tạo MỘT bài trong nền rồi đẩy dataset (một việc một lúc, Lyria lỗi nghỉ 30 phút) — khác truyện bé (tạo ngay
  trong job), vì job bếp không nên chờ ~3 phút. Kaggle: bài Lyria gửi dạng file trong dataset gắn vào phiên
  (`bed_spec(music_ref=)`, `build_spec` nhạc lót, `produce_batch` gộp `dataset_sources` của cả lô). Web: dòng trạng thái kho +
  nút «Tạo đủ kho Lyria» (`POST /api/bgm/lyria`); console `cook_bgm`. Chưa chạy Lyria thật với 3 prompt bếp (máy phát triển
  không có key Gemini): lần tạo đầu xem log «Nhạc bếp: Lyria lọc prompt» nếu có.
- **Giọng Anh** (`rmvideo/kids/dialogue.py`: `split_english`, `english_clip`; `rmvideo/kids/job.py`): mỗi đoạn lời (người kể /
  nhân vật) tách theo các từ tiếng Anh của đề (đứng riêng, không phân biệt hoa thường, dấu câu dính sau đi kèm để giọng Anh lên
  giọng đúng «sun!»): «À, mặt trời đó tiếng Anh là» (VieNeu) + «sun.» (giọng Anh) + «Bé nói theo nè:» (VieNeu) + «sun!» (giọng
  Anh). edge-tts đệm ~0,2 s lặng đầu và ~1,2 s lặng cuối -> cắt còn 40/60 ms; đọc chậm 10%; mỗi mẩu kéo về cùng mức RMS như
  trước. Mỗi cụm chữ Anh chỉ gọi edge-tts một lần, thử lại 3 lần; vẫn lỗi thì đoạn chứa nó VieNeu đọc nguyên như cũ, ghi vào
  `notes` (không mất tiếng, không hỏng job). Kaggle đã cài sẵn edge-tts (gói PIP của notebook). `timings.en_voice` ghi giọng đã
  dùng. Cài đặt `KIDS_EN_VOICE`: Jenny (nữ, chuẩn) / Ana (bé gái) / Ava / Guy / `off`. Nghe thử VieNeu CPU + edge thật: VieNeu
  đọc nguyên câu còn tự lặp «Bé nói theo nè, súng» hai lần; bản tách không lặp.
- **Kiểm ảnh** (`rmvideo/kids/imgcheck.py`): OWLv2 chấm đầu trên ảnh LoRA (lông nỉ) thấp hơn hẳn ảnh FLUX trơn đo 19/11 — đầu
  gấu 0,15–0,23, đầu thỏ thứ hai 0,297 — nên ngưỡng 0,3 không thấy bản sao. `THRESHOLD` 0,18 (dải an toàn đo được 0,15–0,2;
  0,12 bắt đầu báo nhầm). `_groups`: khung bao trọn ≥ 2 khung tách rời (một khung «a rabbit head» 0,32 bao cả hai thỏ, điểm cao
  nhất) bị bỏ, các đầu bên trong giữ lại. `_is_body`: khung dưới một đầu chỉ là thân áo khi thẳng hàng (tâm lệch ≤ 25%) hoặc
  vùng giữa mang màu áo — luật cũ (mọi khung dưới đầu) gộp mất đầu thỏ thứ hai ló dưới cằm thỏ thứ nhất (lệch 54%). `species_twins`:
  hai đầu cùng loài ≥ 0,25 mà cảnh chỉ có một bạn loài đó -> nhân đôi. Test hồi quy dùng khung thật đo từ video job 1.
- **Prompt ảnh** (`rmvideo/kids/prompts.py`): `PROPORTION_ONE_EN` / `PROPORTION_TWO_EN` thay câu «All friends…» cho mọi cảnh;
  «Exactly one character in the picture, alone» / «Exactly two friends in the picture, each appearing only once» (nói khẳng định,
  FLUX schnell không có prompt âm); từ dạy nhóm vật / thiên nhiên không có tư thế -> «the sun clearly visible in the sky» (con
  vật không ép: bộ kiểm sẽ đếm nó là đầu thừa).

## 1.1.1 — xoá chữ in cứng bằng LaMa (19/11/2026)

- Nhạc bé: cả phiên Lyria giới hạn `TIMEOUT + STALL` giây (`asyncio.wait_for`): trước đây hạn giờ chỉ xét khi có gói
  gửi về, kết nối đứng im thì job truyện bé kẹt mãi ở «chọn nhạc nền». Test truyện bé không gọi Lyria thật nữa (CI 20/11
  treo 30 phút ở 94%). CI: Pillow, pytest-timeout 5 phút/test, job 25 phút, in bản ffmpeg (ubuntu-latest 26.04 = FFmpeg 8).
- Kênh bếp, LaMa: video có cờ xoay 90°/270° đọc theo khung hiển thị (`inpaint._probe`), không cắt khung sai chiều.
- Truyện bé: lưu LoRA nhân vật trong repo (`lora/dcn_lora_v1.safetensors`, Git LFS, sha256 e5e1d8a5…) +
  `scripts/push_kids_lora.py` tạo lại dataset Kaggle `rmvideo-kids-lora` từ bản này; `lora/` không vào gói Botkeep.
- Truyện bé, hình theo lời kể (xem mẫu 32 với mắt đạo diễn, 20/11): model ghi đồ vật chính `prop_vi`/`prop_en` (bộ kiểm đòi có
  trong lời cảnh 1–2), prompt ảnh ép đồ vật «clearly visible» ở cảnh 1 và cảnh lời nhắc; từ dạy là hành động / cảm xúc có `pose`
  (data.json) ghép vào prompt, từ cần chạm nhau (`contact`: hug/help/share) được OWLv2 soát hai áo tách rời (`imgcheck.contact_ok`)
  và vẽ lại; đồ vật chính được OWLv2 dò thêm («a toy box», `imgcheck.PROP_THR`), thiếu thì vẽ lại (trừ cảnh lời nói nó đang
  mất / đang tìm, `prompts.PROP_LOST_RE`); ánh sáng theo lời (`prompts.lighting_en`: sắp tối → hoàng hôn, trăng → đêm, sáng sớm, mưa) và cảm xúc theo cụm cuối
  trong lời (`emotion_en`); ngân sách 165 từ cho T5. Ảnh câu móc chọn theo lời (`story.hook_scene`: cảnh 2–5 nhiều dấu hiệu rắc
  rối nhất, hoà thì cảnh sau), clip móc zoom chặt hơn 0,22 (`render.HOOK_ZOOM`).
- Soát code đợt 20/11 (truyện bé + Kaggle): `_call_gemini(model=…)` nhận model riêng cho từng lượt — `writer.GeminiBackend`
  không đổi `config.GEMINI_MODEL` chung nữa (kênh bếp gọi Gemini song song trên cùng tiến trình; model truyện bé bị ngừng 404
  không còn ghi đè Cài đặt). Kaggle trả `brief` cuối (Qwen viết trên Kaggle thì từ hôm nay chốt ở đó) → bài đăng và bộ nhớ
  từ (`remember_done` ghi `words`/`plot` thật) theo đúng từ đã dạy. Vòng tự động ghi `pending/posts/streak/paused` lên bản mới
  nhất của `kids_auto.json` (`save_tick`) — không ghi đè mất truyện worker / nút «Làm 1 truyện ngay» ghi trong lúc chờ Facebook
  tải video. Phụ đề trả theo thời gian (câu móc đứng đầu SRT, mốc tiếng động «Bịch!» trong câu móc có nảy). Bộ kiểm ảnh hết
  lượt giữ ảnh ÍT LỖI nhất (số lý do hỏng, lệch số đầu) thay vì ít đầu nhất. `images.find_local` so tên THƯ MỤC model («dev»
  trong tên tài khoản Kaggle không bị nhận nhầm, «flux» không bắt thư mục flux-dev). Qwen: soát lại (`_revise`) trước khi tắt
  llama-server; thẻ chữ font dự phòng có `.size`.
- Kênh bếp: `TEXT_INPAINT=1` (mặc định) — khi bóc lời + phân tích hình trên Kaggle, `rmvideo/media/inpaint.py` chạy big-lama.pt
  (TorchScript LaMa) vẽ lại nền ở các dải chữ gốc trên mọi khung (cắt dải + ngữ cảnh 48px, mặt nạ bỏ 6% hai mép cho logo góc,
  lô 8 khung, NVENC) → `clean.mp4` về Botkeep, cache theo video; dựng (Botkeep hoặc Kaggle qua `PUBLIC_URL`) dùng video sạch,
  chỉ che mờ dải nào chưa xoá (`remake.uncovered`), ghi chú «Đã xoá chữ gốc ở N dải bằng LaMa». Lỗi → che mờ như cũ.
- Truyện bé: bộ kiểm ảnh soát áo đúng chủ (`imgcheck.shirt_owner` / `owners_ok`: áo dưới đầu thỏ phải đỏ, dưới đầu gấu phải
  xanh dương…; đo thật mẫu 32: Thỏ mặc áo xanh của Gấu, Gấu không áo — chỉ một áo nên kiểm «hai áo cùng màu» không bắt);
  `check` trả lý do hỏng cụ thể vào log.
- Truyện bé: LoRA 4 nhân vật (`KIDS_LORA`, mặc định `dcn_lora_v1`) — train 20/11 trên Kaggle T4 bằng ai-toolkit (FLUX.1-schnell
  + adapter huấn luyện của ostris, Apache-2.0; 144 ảnh do pipeline tự vẽ, 36 ảnh / bạn; 600 bước fp16, transformer + T5 nén
  uint4, embedding T5 tính sẵn ngoài trainer để vừa 14,5 GB; 75 phút). File lưu ở dataset riêng tư `rmvideo-kids-lora`; phiên
  dựng gắn dataset, nạp bằng `pipe.load_lora_weights` (scale 0,9), prompt thêm từ khoá thotrang / gaunau / meocam / socnho
  (`characters[].lora_token`). Không có dataset thì tự vẽ không LoRA (`kaggle_worker.lora_source`). Đo trên 8 cảnh mẫu 31:
  cùng một gương mặt Thỏ Trắng ở mọi cảnh, hết ảnh hai đầu và chữ in lên áo; +80 giây mỗi truyện. Notebook cài thêm `peft`.
  `kaggle_api.outputs` gộp mọi trang đầu ra.
- Truyện bé: prompt ảnh khoá tỉ lệ nhân vật (`PROPORTION_EN`: cùng chiều cao, dáng bé con), sóc không «tiny»; bộ kiểm ảnh
  thêm phép đo hai đầu to nhỏ lệch quá 2 lần (`imgcheck.sizes_ok`). Thử Z-Image-Turbo trên T4: ảnh đen (fp16), giữ FLUX.
- Truyện bé: bỏ câu kết «Các bé ơi, nhớ… nha!» (phản hồi 19/11: nghe giảng, «trớt»): cảnh 8 kết bằng hình ảnh ấm vòng lại
  cảnh 1 (khung truyện, lời nhắc, truyện mẫu, bộ kiểm báo khi cảnh 8 nói với «các bé»); câu ôn từ trong màn kết đổi
  «Giỏi lắm, bé nhớ hết rồi!» -> «Hẹn gặp lại bé ở chuyện sau!».
- Truyện bé: kiểm ảnh hai đầu / nhân vật nhân đôi (`rmvideo/kids/imgcheck.py`, `KIDS_IMAGE_CHECK`): OWLv2 đếm đầu nhân vật
  sau khi FLUX vẽ (gộp khung lồng nhau, bỏ khung thân áo dưới đầu, ngưỡng 0,3; đo thật 16 ảnh: 0 báo nhầm), thừa đầu so với
  số bạn trong cảnh thì vẽ lại với seed khác (tối đa 2 lần), timings ghi `image_checked` / `image_redo`. Soát cả màu áo:
  khung «a t-shirt» -> sắc độ vùng giữa (đỏ / xanh lá / xanh dương / vàng; cam = lông mèo không tính); áo màu ngoài bộ màu
  của các bạn trong cảnh, hay hai áo cùng màu khi hai bạn khác màu (đo thật mẫu 29: Mèo Cam mặc áo đỏ của Thỏ) -> vẽ lại.
- Truyện bé: câu dạy từ — dạng đố hỏi rồi mới đáp («Đố bé biết trái banh đó tiếng Anh là gì? Là ball!»), vật trong cảnh gọi
  «đó», không chèn sau câu hỏi cho bé (câu hỏi vẫn là câu cuối cảnh); cân âm lượng từng đoạn giọng trước khi nối
  (`dialogue.rms_db`, RMS −20 dBFS ±12 dB).
- Truyện bé: viết lại theo lỗi thì SỬA bản nháp trước (`prompts.messages(previous=)`) thay vì viết truyện khác; truyện tối đa
  200 chữ, trên 215 là lỗi chặn (đo thật mẫu 27: 221 chữ -> 91,6 s, QA loại); thông báo dài kèm cảnh dài nhất; dựng có mức đọc
  nhanh đường cùng `TEMPO_HARD` 1,22.
- Truyện bé: Gemini hai model theo vai — `KIDS_GEMINI_MODEL` (mặc định gemini-4-hydrogen, `KIDS_GEMINI_THINKING` low) viết nháp,
  `KIDS_GEMINI_REVISE_MODEL` (gemini-3.5-flash-lite) viết lại theo lỗi và tự soát; `prompt_engine._call_gemini(thinking=)` đặt mức
  suy nghĩ cho Gemini 4; model của vai quá tải / hết lượt thì model kia làm thay (`writer.GeminiBackend`, `llm.Backend.chat(role=)`).
- Truyện bé: nhạc nền Lyria RealTime thành kho theo màu khung truyện (`rmvideo/kids/bgm.py`, `plots[].mood`, `KIDS_BGM`, lệnh
  `kids_bgm`), đẩy lên Kaggle dạng dataset riêng tư (`kaggle_api.upload_blob / dataset_push / dataset_wait`), Kaggle lấy file từ
  dataset đã gắn (`kaggle_job.fetch` «mounted»); `kids_auto.pick_music` theo chính sách KIDS_BGM.
- Truyện bé: kể cho cuốn theo sách tranh — lời nhắc có «bí kíp» (hỏi bé cuối cảnh rồi cảnh sau trả lời, điệp khúc có nhịp và
  đảo ở cảnh 6 theo `plots[].twist`, thất bại hài + tiếng động, cảm xúc qua hành động, nét riêng / câu cửa miệng từng bạn
  `characters[].quirk_vi`, kết vòng lại mở đầu), truyện mẫu viết lại theo đó; bộ kiểm thêm: thiếu câu hỏi cho bé, «các bạn / cả
  nhóm» khi hình chỉ 2 bạn, tiêu đề lạc truyện (`story.title_mismatch`); khung truyện chỉ gọi tên 1–2 bạn; checklist soát thêm
  mục «cuốn chưa?»; 3 từ tiếng Anh mỗi truyện, câu dạy từ không vào cảnh kết và không dồn 3 câu một cảnh; gạch ngang dài → phẩy.
- Truyện bé: giọng riêng từng nhân vật (`KIDS_MULTI_VOICE`, `rmvideo/kids/dialogue.py` tách lời thoại «…» theo tên người nói,
  `kaggle_job.make_synth` nhận giọng theo từng đoạn, nối lại theo cảnh); lời nhắc đòi lời kể trôi chảy (câu 5–14 chữ, từ nối),
  lời thoại luôn trong «…» có tên người nói; câu dạy từ có từ dẫn.
- Truyện bé: từ tiếng Anh chọn từ chính truyện (kho gợi ý theo bối cảnh, `story.choose_words` sau khi viết), tiêu đề không lộ bài
  học, câu dạy từ tránh cảnh 3 và 6; thẻ nghĩa trong truyện đổi sang viên vàng + bảng trắng (PIL) cùng kiểu bảng kết.
- Truyện bé: kho 10 khung cốt truyện (`data.json` «plots», `story.pick_plot` / `plot_beats`) rút từ truyện mầm non và podcast kể
  chuyện; lời nhắc theo 8 nhịp của khung + sợi chỉ xuyên suốt. Lời thoại: bộ kiểm chặn «nha/hen» > 3, câu móc kết «nha», câu lặp
  ngoài điệp khúc (`story.repeated_sentences`); câu dạy từ xoay vòng không «nha»; màn kết không phụ đề.
- Truyện bé: không còn màn hình đen — bỏ mờ từ đen / về đen, màn kết giữ nền sáng (phủ trắng 14%) với bảng «Hôm nay bé học»
  bo góc vẽ bằng PIL (`rmvideo/kids/cards.py`: viên vàng chữ Anh, nghĩa tím than, tagline xanh), thẻ tiêu đề cùng kiểu;
  thẻ nghĩa không hiện trong màn kết.
- Truyện bé: mở đầu «móc» 3 giây (`hook`/`hook_text` trong schema, `render.plan(hook_dur)`: clip móc = ảnh cảnh 3 zoom mạnh +
  câu móc đọc ngay + chữ to, thẻ tiêu đề lùi sau 2,2 giây, lời vào sớm), vai cảnh nhấn nhân quả và ba lần thử leo thang,
  Gemini tự soát lại theo checklist (`llm._revise`, `prompts.revise_messages`, bản sửa phải qua bộ kiểm).
- Truyện bé: Gemini viết trên Botkeep (xem 1.1.0, mục Gemini). Hiệu ứng động `KIDS_FX` (render.py): Ken Burns mạnh + nhịp thở,
  nảy zoom + rung máy khi lời có tiếng động (`hits_from_words`), hạt bokeh (PIL, overlay theo sin), vignette, phụ đề bật lên
  (`subs.write_ass(pop=True)`), whoosh khi chuyển cảnh + ting khi dạy từ (lavfi, trộn trước loudnorm).

## 1.1.0 — kênh truyện bé «Kể Bé Nghe» (18/11/2026)

- Thêm chế độ 🧸 Truyện bé: model trên Kaggle tự viết truyện 60–90 giây ở Đồi Cỏ Non (4 nhân vật cố định, một bài học, 3–5 từ tiếng Anh), vẽ 8 cảnh, đọc giọng miền Nam, dựng rồi đăng Fanpage RIÊNG.
- Đề truyện chọn bằng mã (bài học × bối cảnh × xung đột × kho từ), không lặp trong 30 ngày; kịch bản kiểm bằng mã, model viết lại khi chưa đạt.
- Nút «Làm 1 truyện ngay» trên web / lệnh `kids_now` để soát vài video đầu; bật `KIDS_AUTO=1` là tự làm 2 truyện/ngày, hẹn đăng 08:00 và 19:00.
- Lệnh `kids_setup` chuẩn bị model trên Kaggle một lần (biên dịch llama.cpp, lượng tử FLUX.1-schnell 4-bit).

### Chi tiết
- **Kiến trúc** (kế hoạch: `docs/KE_HOACH_KIDS_STORY.md`): Botkeep chọn đề (`rmvideo/kids/story.pick`) rồi gửi MỘT việc `kind="kids"`
  sang notebook `rmvideo-kids`; trên Kaggle `rmvideo/kids/job.py` chạy lần lượt model chữ (`llm.py`: llama-server + Qwen3.6-35B-A3B
  GGUF 4-bit, JSON theo schema, suy nghĩ trước tối đa `KIDS_LLM_THINK` token, viết lại tối đa 3 lần theo lỗi bộ kiểm), model ảnh
  (`images.py`: FLUX.1-schnell NF4 hoặc Z-Image-Turbo, 768×1344 → 1080×1920), giọng VieNeu (`KIDS_VOICE`, mặc định Thục Đoan), dựng
  (`render.py`). Không có đường lùi về Botkeep: Kaggle lỗi là job lỗi, vòng lịch chọn khung giờ khác và báo admin.
- **Đo thật 18/11 (9 lần chạy Kaggle, video đầu tiên 84 giây đạt QA)**: `/kaggle/input` chỉ đọc và file đầu ra mất quyền chạy → chép
  llama-server ra `/tmp`; `huggingface_hub<1.0` cho diffusers; T5 8-bit + transformer NF4 tràn T4 → mã hoá 8 prompt trước rồi bỏ T5, vẽ
  768×1344; VAE giải mã fp32 trên đúng thiết bị; chờ máy giờ cao điểm tới 60 phút → `KIDS_TIMEOUT` 125 phút; 236 chữ → 88,9 giây trượt
  QA → `render.plan` tự co đệm rồi đọc nhanh hơn (≤ ×1,15) cho ≤ 87 giây, QA hỏng vẫn giữ video + ảnh để xem; nạp FLUX từ ổ gắn 7–8 phút
  → chép về đĩa cục bộ bằng 4 luồng ngay đầu việc; model viết `action_en` bằng tiếng Việt, hỏi dồn dập và liệt kê «X nghĩa là Y» cho
  đủ chữ → bộ kiểm báo lỗi để viết lại (hành động dự phòng theo vai cảnh), prompt ảnh gọn cho 256 token T5, màu áo nói trước tên.
- **Dựng**: độ dài cảnh theo giọng đọc (tối thiểu 5 giây), Ken Burns + xfade êm dùng lại biểu thức của 📸, thẻ tiêu đề 3 giây đầu, thẻ
  «sorry = xin lỗi» khi từ được đọc lần đầu, màn kết «Hôm nay bé học» + tagline, phụ đề rải theo khoảng có tiếng, từ tiếng Anh tô màu
  (`subs.write_ass(highlight=…)`, style `kids`), nhạc nền ~16 dB dưới giọng tự hạ khi đang đọc (`assets/kids_bgm/` hoặc `KIDS_BGM_LINKS`),
  loudness −14 LUFS, NVENC. Ảnh bìa = khung lúc thẻ tiêu đề đang hiện.
- **Gemini viết truyện trên Botkeep (19/11)**: `KIDS_LLM_PROVIDER` auto/gemini/qwen, `KIDS_GEMINI_MODEL`; `rmvideo/kids/writer.py`
  (GeminiBackend cùng giao diện llama-server, dùng chung vòng kiểm/viết lại của `llm.generate_story`, gọi qua
  `prompt_engine._call_gemini` nên có thử lại / model dự phòng); truyện sẵn nằm trong `spec["story"]`, Kaggle bỏ bước model chữ.
  Key Gemini không bao giờ sang Kaggle. Đo: gemini-3.5-flash-lite 1 lượt, 3 giây, mạch lạc hơn Qwen 35B 4-bit.
- **Cách kể cho bé nhỏ (19/11)**: sau 6 video đo thật, lời còn rời rạc và từ tiếng Anh bị ép vào thoại; viết lại theo cách của
  truyện kể mầm non: truyện thuần tiếng Việt, 8 nhịp (chào bé, chuyện nhỏ, tiếng động, thử ba lần với điệp khúc, bạn giúp, bài học
  nói nhẹ, nhắn bé), truyện mẫu trong lời nhắc (chép ≥ 2 câu là lỗi). Kho từ đổi sang đồ vật / màu / con vật / lời chào / hành động
  đơn giản (107 từ), chọn theo bối cảnh + 1 từ theo bài học, 3 từ/truyện; mã chèn câu dạy từ «Trái banh, tiếng Anh là ball. Bé nói
  theo nha: ball!» ngay cảnh đồ vật xuất hiện (`story.add_beats`), model không còn thấy từ tiếng Anh.
- **Giọng miền Nam**: lời model qua `rmvideo/domain/dialect.south` + vài từ trẻ em (`story.south_kids`: ba, trái banh, cái nón, té, mắc cỡ, nha).
- **Đăng**: `autopost.publish/check_token/views_of` nhận `page`/`token` (Fanpage truyện bé: `KIDS_FB_PAGE_ID`, `KIDS_FB_PAGE_TOKEN`, không bao giờ
  gửi sang Kaggle). `kids_auto.py`: trạng thái `kids_auto.json`, khung `KIDS_TIMES`, làm trước 36 giờ, 3 truyện lỗi liên tiếp thì dừng.
- **Kaggle**: `kaggle_api.push` nhận notebook của người khác (`user/slug`) và dataset; `kaggle_worker._run_sync` theo `spec["slug"]`, gắn
  `kernel_sources`/`dataset_sources` của việc, tải thêm `fetch_files` (ảnh bìa, story.json); việc kids không chạy CPU khi hết giờ GPU.
  `KIDS_KAGGLE_SOURCES` trỏ tới notebook/dataset có sẵn llama-server (vd bản bạn đã biên dịch khi đo Qwen).
- **Cài đặt** nhóm 🧸 Truyện bé: `KIDS_AUTO`, `KIDS_TIMES`, `KIDS_FB_PAGE_ID`, `KIDS_FB_PAGE_TOKEN`, `KIDS_VOICE`, `KIDS_IMAGE_MODEL`,
  `KIDS_LLM_PROVIDER`, `KIDS_GEMINI_MODEL`, `KIDS_LLM_PATTERN`, `KIDS_LLM_HF`, `KIDS_LLM_THINK`, `KIDS_KAGGLE_SOURCES`, `KIDS_CHANNEL_NAME`, `KIDS_WORLD_NAME`, `KIDS_TAGLINE`,
  `KIDS_HASHTAGS`, `KIDS_BGM_LINKS`.
- Web: mục 🧸 Truyện bé (trạng thái, làm ngay, thử token, chuẩn bị model, chạy lại); API `/api/kids`, `/api/kids/{now|test|setup|setup_status|resume|tick}`.
  Console: `kids`, `kids_now`, `kids_tick`, `kids_setup`, `kids_setup_status`, `kids_fb_test`, `kids_resume`.
- Test: `tests/test_kids.py` (chọn đề, kiểm kịch bản, prompt, kế hoạch cảnh, dựng thật cỡ nhỏ, việc Kaggle với model giả, luồng Botkeep,
  lịch đăng, cài đặt, route). Chưa chạy thật trên Kaggle: phần model (llama-server, diffusers) sẽ chỉnh theo lần chạy đầu.
- Đo trên Kaggle 18/11 khi chạy `kids_setup`: cmake của llama.cpp đòi `libcuda.so` mà Kaggle chỉ có `libcuda.so.1` -> build với
  `GGML_CUDA_NO_VMM=ON`; repo `black-forest-labs/FLUX.1-schnell` đã khoá (gated) -> lấy bản sao công khai `unsloth/FLUX.1-schnell`;
  model chữ mặc định `lmstudio-community/Qwen3.6-35B-A3B-GGUF` (một file Q4_K_M 20,2GB). Notebook chuẩn bị lưu thêm Z-Image-Turbo
  4-bit khi còn chỗ.

## 1.0.1 — sửa theo soát video mực của job 1 (18/11/2026)

- Thẻ MẸO không còn bị logo che chữ: thẻ phía trên (mẹo, tua nhanh) đặt xuống dưới logo.
- Bản ngắn mở bằng câu hook + cảnh món của video gốc rồi mới điểm danh nguyên liệu (trước đây mở bằng mực sống trong nồi, chữ hook chồng lên thẻ nguyên liệu).
- AI không được lặp ý: nhắc lại thời lượng hay cụm đã nói (vd «luộc 4 phút» 3 lần, «không rách da» 3 lần) từ 2 đoạn trở lên là phải viết lại bằng điều mới đang thấy.
- Cảnh tĩnh (nồi nước sôi quay từ trên xuống) đổi cỡ khung rõ và dày hơn: tối đa 4 giây một lần, cận tới 1,3 lần.
- Câu chốt kênh luôn là câu riêng, không lặp «cả nhà»; phụ đề không cắt đôi tên kênh; câu hook không hiện phụ đề khi chữ hook to đang hiện.

### Chi tiết
- **Thẻ đè logo**:
  - bản 50 hạ logo xuống 9–18% chiều cao, thẻ MẸO vẫn đặt ở 12% và rộng gần hết khung;
  - logo vẽ sau nên che chữ: «Không cần rã đông mự[c]» ở 0:18–0:22.
  - Giờ `renderer.logo_bottom` tính đáy logo theo đúng tỉ lệ ảnh (logo tải lên có thể không vuông).
  - Thẻ ở nửa trên khung đặt dưới mức đó 1%; thẻ nguyên liệu / giá ở dưới giữ nguyên.
- **Hook bản ngắn**:
  - `short.plan_short(hook=…)`: khi không có đoạn mở đầu, giữ câu đầu của nguồn (`style_b._hook_end`, cùng luật bản đầy đủ có từ bản 43) làm khúc đầu;
  - khúc điểm danh, chữ «Nguyên liệu gồm» và thẻ số dời ra sau câu đó.
  - Kéo theo sửa một lỗi phụ: thẻ điểm danh trước đây bắt đầu ở giây 0, `hook_title_end` trả 0 và bị hiểu là «mặc định 3,3 giây», nên chữ hook đè lên thẻ.
  - Có đoạn mở đầu (cảnh món xong chiếu trước) thì đoạn đó đã là hook, bản ngắn giữ cách cũ.
- **Lời lặp ý**:
  - `prompt_engine.repeated_ideas` bắt một thời lượng (4 phút, bốn phút) hay một cụm 3 chữ có ≥ 2 chữ có nghĩa đã nói ở đoạn trước.
  - Bỏ qua tên món / nguyên liệu / cách nấu («mực hấp gừng», «nồi nước sôi»), khúc điểm danh / tóm tắt và câu hook.
  - Từ 2 đoạn lặp thì bắt AI viết lại «điều MỚI đang thấy trên hình»; bản viết lại ít lặp hơn được ưu tiên.
  - Trên lời thật của job 1, bộ dò bắt đúng 4 chỗ lặp: «lửa riu riu», «không rách da», «4 phút», «mực tươi ngon».
- **Cảnh tĩnh**:
  - khúc thuộc 35% ít chuyển động nhất của video dùng cỡ khung (1.0, 1.25, 1.1, 1.3) thay cho (1.0, 1.12, 1.0, 1.18);
  - chia tối đa 4 giây một khúc thay vì 6;
  - xét từng khúc, nên một quãng nói liền vừa có cảnh động vừa có cảnh tĩnh vẫn được chia đúng chỗ tĩnh.
- **Câu chốt**:
  - `brand._join_close`: lời đoạn cuối chưa chấm câu thì chấm; lời gọi «cả nhà / mọi người / bà con» ở cuối câu bỏ đi khi câu chốt đã gọi «cả nhà»;
  - `subs.group_words` không ngắt dòng giữa hai chữ liền nhau của tên kênh (`BRAND_NAME`);
  - `filtergraph.subtitle_texts(title_shown=True)` ẩn phụ đề câu hook khi câu đó dứt trong lúc chữ hook to còn hiện, kể cả khi không có đoạn mở đầu.
- Test: `tests/test_v101.py`.

## 1.0.0 — phiên bản mới: Zoom, thông báo, tông màu tự động, nhạc lót (18/11/2026)

- Telegram báo cả job làm trên web / tự đăng (xong kèm link tải, lỗi, chờ duyệt); nút «🔔 Gửi thử thông báo» trong Cài đặt nói rõ vì sao chưa nhận được.
- Thêm kênh Zoom Team Chat ngang hàng Telegram: nhắn link cho bot, nhận thông báo kèm link tải (không gửi file video).
- Tông màu tự chọn theo từng video: ánh sáng lạnh → Ấm mộc; tối, màu nhạt hoặc đã ám vàng → Tươi sáng; còn lại → Gốc.
- Nhạc lót thật nhỏ dưới lời đọc (lấy từ thư viện 🎵 Nhạc nền), tự hạ khi đang đọc; video gốc đã có nhạc rõ thì không lót.
- Đánh số phiên bản kiểu 1.0.0; mỗi lần lên phiên bản bot tự báo qua Telegram và Zoom.

Từ bản này đánh số MAJOR.MINOR.PATCH (`rmvideo/version.py`): sửa lỗi tăng số cuối (1.0.1), thêm tính năng tăng
số giữa (1.1.0), đổi lớn phải cài lại tăng số đầu. Mỗi phiên bản mở đầu bằng mục `## <phiên bản> — <tóm tắt> (<ngày>)`
và các dòng gạch đầu dòng tóm tắt: đó chính là tin bot gửi khi lên phiên bản.

### Thông báo Telegram: vì sao trước đây không nhận được
- Chỉ job gửi từ Telegram mới được báo, và báo vào đúng chat đó. Job tạo trên web hay tự đăng thì xong, lỗi hay chờ
  duyệt đều im lặng.
- Giờ mọi đường báo đi qua `rmvideo/app/notifier.py`:
  - job Telegram báo vào chat đó như cũ;
  - job Zoom báo vào đúng cuộc trò chuyện Zoom;
  - job web / tự đăng báo về chat quản trị (`ADMIN_CHAT_ID`, trống thì người đầu tiên trong `ALLOWED_USERS`) và
    người nhận trên Zoom. Tắt bằng `NOTIFY_JOBS=0`.
- Tin «xong» có link tải từng file (cần `PUBLIC_URL`) và ảnh bìa.
- Job bị từ chối vì máy thiếu chỗ trống trên đĩa cũng được báo (trước đây kết thúc im lặng, kể cả job Telegram;
  Codex soát PR #10 tìm ra).
- Nút **🔔 Gửi thử thông báo** (Cài đặt) gửi thật một tin và trả lý do cụ thể cho từng kênh, vd:
  - chưa đặt `BOT_TOKEN`;
  - chưa đặt `ADMIN_CHAT_ID`;
  - người nhận chưa bấm /start với bot;
  - bot bị chặn;
  - token sai.

### Kênh Zoom
- Đường nhận tin `POST /zoom/events`:
  - kiểm chữ ký HMAC bằng Secret Token, bỏ tin cũ hơn 5 phút;
  - trả lời xác minh địa chỉ `endpoint.url_validation`;
  - bỏ sự kiện Zoom gửi lặp;
  - trả lời trong 3 giây (việc nặng chạy nền);
  - chỉ người trong `ZOOM_ALLOWED_USERS` được dùng, người lạ được báo JID để chủ bot thêm vào.
- Lệnh:
  - gửi link (tối đa 5): làm lại (nấu ăn, giữ nguyên thời lượng);
  - thêm `review`, `30` / `60` / `90`, `full` / `split60` / `split90` / `highlight`, `tải`, `duyệt` để đổi;
  - `trạng thái`, `huỷ`, `id`, `help`.
- Bot trả lời bằng tin chữ: đã nhận, đang xử lý, xong + link tải + nội dung đăng, lỗi, chờ duyệt kịch bản trên web.
- Nơi trả lời đi theo job qua bước duyệt kịch bản và qua các phần của video dài.
- Cài một lần (Zoom App Marketplace → Develop → Build App → General App):
  1. App Credentials: Client ID, Client Secret.
  2. Features → Access: Secret Token.
  3. Features → Surface → Team Chat → Chatbot: Bot endpoint URL = `<PUBLIC_URL>/zoom/events`; ghi lại Bot JID.
  4. Điền 4 khoá ở Cài đặt → 🔑 Tài khoản & API.
  5. Cài app vào tài khoản (Local Test → Add App), nhắn bot `id` để lấy JID.
  6. Điền JID vào Cài đặt → 🎥 Zoom → «Người được dùng bot Zoom».
- Khoá Zoom là bí mật / tài khoản: phải nhập lại mật khẩu web khi đổi, không bao giờ gửi sang Kaggle.

### Phiên bản và báo lên phiên bản
- Lần khởi động đầu của phiên bản mới: gửi các dòng tóm tắt về Telegram và Zoom.
- Nhớ riêng từng kênh đã nhận (`.version_announced.json`): kênh chưa gửi được (chưa đặt chat id, chưa nhắn bot Zoom
  lần nào) thì lần khởi động sau gửi lại, kênh đã nhận không bị báo lặp.
- Phiên bản hiện cạnh tiêu đề trang web (chỉ khi đã đăng nhập) và trong log khởi động.

### Tông màu tự động (`BRAND_LOOK=auto`, mặc định mới)
- Đo 8 khung rải đều video gốc (chỉ giải mã khung khoá, ~1,4 giây cho nguồn 1080p dài 3 phút): độ sáng, độ tươi,
  độ ấm (đỏ trừ xanh lam). Dải đen viền trên / dưới không tính.
- Luật:
  - ám xanh / lạnh → Ấm mộc;
  - tối, màu nhạt hoặc đã ám vàng → Tươi sáng;
  - còn lại → Gốc.
- Đo trên video thật:
  - video mực (nồi inox, tường xám, rất nhạt) → Tươi sáng;
  - video gà → Gốc;
  - bản tạo lạnh 9000K / đèn huỳnh quang → Ấm mộc.
- Chọn một lần mỗi job, dùng chung cho video, đoạn mở đầu, đoạn chen và ảnh bìa; các phần của video dài cùng một tông.
- Kaggle dựng đúng tông Botkeep đã chọn.
- Máy đang lưu «Tươi sáng» (mặc định cũ) tự chuyển sang Tự động một lần; đã chọn tông khác thì giữ.

### Nhạc lót dưới lời đọc (`VOICE_BED=on`)
- Video có lời đọc lấy một bài trong thư viện 🎵 Nhạc nền (tránh 3 bài vừa dùng, lấy đoạn hay nhất của bài).
- Cách trộn:
  - bỏ bass dưới 150 Hz và phần trên 9 kHz;
  - mức trung bình thấp hơn giọng 18 dB;
  - tự hạ thêm khi giọng đang đọc;
  - vào 1 giây, ra 2 giây, tắt trong quãng giữ tiếng gốc.
- Đo trên video gà 20 giây:
  - âm lượng tổng vẫn -14 LUFS;
  - giọng giữ nguyên mức;
  - ở quãng lặng nhạc thấp hơn lời ~13 dB.
- Bản thử đầu tiên chưa bỏ bass: bass đẩy đỉnh tiếng vào bộ giới hạn, cả video nhỏ đi 2 dB. Đã sửa.
- Video gốc có nhạc rõ (nốt ngân trong tiếng nền / ngoài quãng có người nói) thì không lót thêm.
  - Nhạc gốc rất nhỏ dưới tiếng xèo thì bộ dò có thể không thấy. Nghe hai bài chồng nhau thì tắt ở Cài đặt → 🎬 Video & lời.
- Đường dự phòng «đọc một mạch» (AI không viết được lời có mốc thời gian) cũng có nhạc lót (Codex soát PR #10).
- Thư viện trống thì ghi chú và làm như cũ.
- Chỉ dùng nhạc miễn phí bản quyền: nhạc có bản quyền bị YouTube nhận diện, TikTok / Facebook tắt tiếng.

### Kiểm tra
- `tests/test_v100.py`: chữ ký Zoom, luồng nhận tin + lệnh + người lạ, định tuyến thông báo, lý do Telegram, phiên
  bản + báo một lần mỗi kênh, luật tông màu + đo khung thật, chuyển mặc định tông màu, bộ dò nhạc, đồ thị trộn nhạc
  lót, việc Kaggle mang nhạc lót + tông màu.
- Đã dựng thật trên video gà 60 giây (có / không nhạc lót) để đo các con số trên.
- CI chỉ chạy Python 3.13: Botkeep và Kaggle đều chạy 3.13. Mã gửi Kaggle vẫn được kiểm cú pháp 3.10.
  - Ba bản cũ chạy song song nên bỏ bớt không nhanh hơn. Phần nhanh hơn đến từ cache thư viện + pytest song song
    (`-n auto`), mỗi job trước đây mất 7–9 phút.
  - Mỗi lần đẩy chỉ chạy một lượt (trước đây chạy cả lượt push lẫn lượt PR); đẩy commit mới thì huỷ lượt cũ.
- Nhánh `botkeep` (không đổi phiên bản, không tạo bản phát hành): mã chạy bot + `rm-video-botkeep.zip` phẳng, vừa
  trình giải nén của Botkeep (≤ 250 mục; zip «Download ZIP» của nhánh chính đã ~270 mục nên bị từ chối). CI tự đồng
  bộ sau mỗi lần gộp vào nhánh chính (`scripts/botkeep_zip.py --commit`, nội dung không đổi thì không đẩy).

## Bản 50 — số đọc bằng chữ + soát video job 7 dưới góc nhìn đạo diễn TikTok (17/11/2026)

**Số đọc bằng chữ.** Gốc «dầu nóng khoảng một trăm bảy mươi độ»: bộ kiểm số trước đây chỉ hiểu MỘT chữ số đứng ngay
trước đơn vị, nên coi như gốc không có số, và AI viết đúng «170 độ» lại bị coi là bịa rồi bị xoá. Giờ hiểu hàng trăm /
chục / đơn vị, «linh / lẻ», «mốt / tư / lăm» và cách nói tắt («hai lăm» 25, «một trăm hai» 120, «trăm rưỡi» 150). Bỏ số
bịa viết bằng chữ cũng bỏ được trọn cụm.

**Góc nhìn đạo diễn (job 7, 3:08, 720p).** Đo trên video ra:
- 0,33 giây đầu tối đen (mờ từ đen vào).
- Câu hook khen chung chung.
- Hình đổi trung bình 3 giây một lần, nhưng có quãng 13 giây không đổi khung.
- 3 quãng lặng 4-5 giây.
- Câu cuối dứt sát màn kết.
- Âm lượng -14 LUFS, đúng chuẩn TikTok.

| Vấn đề | Sửa |
| --- | --- |
| Khung đầu tiên đen (mờ từ đen 0,33 giây): người lướt TikTok thấy màn đen trước | Vào thẳng hình từ khung đầu (đo lại: khung 0 sáng như khung giữa) |
| Câu hook «Mực hấp gừng giữ trọn vị ngọt tươi» khen chung, trong khi điểm ăn tiền là mẹo «mực đông đá không cần rã đông, thả thẳng vô nước sôi» (đã có sẵn trong phân tích món) | Lệnh viết lời nhận mẹo chính của video (mẹo người gốc nói, đã đối chiếu lời gốc): câu hook + chữ hook xoáy vào điều bất ngờ / kết quả của mẹo |
| Quãng 13 giây không đổi khung (người gốc nói một mạch, không ngắt câu nên không có chỗ đổi cỡ) | Không cỡ khung nào giữ quá 6 giây: quãng dài chia đều, đổi cận / xa. Video mực: 46 → 59 khúc, khúc dài nhất 13,6 → 5,6 giây. RAM dựng đo lại vẫn 571 MB |
| 3 quãng lặng 4-5 giây (1:35, 2:10, 2:24): khe dài 8-10 giây mà AI viết ~55% mức tối thiểu, lọt luật «quá ngắn» (dưới 60%) | Đoạn đọc xong còn lặng quá 3,5 giây mà chưa tới mức tối thiểu là «quá ngắn»; từ 2 đoạn như vậy thì bắt viết lại (tả thêm điều đang thấy) |
| 2:28 «Dĩa mực nóng hổi» khi mực còn trong vợt (gốc tới 2:43 mới «đổ ra») | Bắt lời gọi «dĩa + món» trước đoạn gốc đổ / bày ra dĩa → viết lại; vẫn còn thì đổi thành «mẻ mực». Chạy trên kịch bản các job trước: bắt đúng 2 chỗ cùng lỗi |
| 3:01 «thơm lừng rảnh rỗi nè» (chữ lạc nghĩa) | Thêm một lượt AI soát lời sau khi viết: chỉ sửa chữ sai nghĩa / vô nghĩa. Chỗ sửa phải nhỏ (≤ 30% số chữ), giữ nguyên liệu và con số, không chép lời gốc; sai thì bỏ chỗ sửa |
| Chữ «nha» cuối dứt đúng lúc màn kết tối lại | Lời đọc và phụ đề dứt trước màn kết 0,3 giây |
| (thấy khi viết test) «Mùi gừng bốc lên thơm lừng» bị coi là BỊA nguyên liệu «mùi» (rau mùi), bản viết lại tốt hơn bị bỏ | «mùi» đứng trước tên nguyên liệu khác là hương của nó, không phải rau mùi |

### Sửa theo đề xuất + góc nhìn đạo diễn TVC / YouTube

| Vấn đề | Sửa |
| --- | --- |
| Màn kết tối 72% cả khung rồi mờ về đen: dấu hiệu «hết rồi», người xem TikTok / Shorts lướt đi trước khi video tự quay vòng | Màn kết SÁNG: giữ cảnh món, chỉ một dải mờ (40-60% chiều cao) sau tên kênh + câu dưới tên; cuối video không mờ về đen (tiếng vẫn nhỏ dần 0,4 giây) — khung cuối nối thẳng về khung đầu |
| Logo sát mép trên bị nút tìm kiếm / tab của TikTok, Shorts, Reels che | Logo hạ xuống 9% chiều cao |
| Bản đầy đủ luôn 720p (thiết kế cũ để dựng nhanh), kể cả khi dựng trên Kaggle | Dựng trên Kaggle: bản đầy đủ cũng 1080p (Kaggle đủ RAM; giới hạn thời gian dựng vẫn áp). Trên Botkeep bản đầy đủ giữ 720p. Bản ngắn ≤ 90 giây vốn đã 1080p |
| Bài đăng của bản đầy đủ 3:08 vẫn ghi «YouTube Shorts … #Shorts» — YouTube chỉ xếp video dọc ≤ 3 phút vào Shorts | Bản dài hơn 3 phút: ghi «YouTube (video thường; muốn lên Shorts thì đăng bản ngắn)», bỏ #Shorts |
| Tiêu đề khen chung, không nêu điểm ăn tiền | Lệnh viết lời: tiêu đề nêu tên món kèm đúng mẹo chính (YouTube / Facebook bấm vì lời hứa trong tiêu đề) |
| (thấy khi đo cho mục 1080p) Bản ngắn ≤ 90 giây dựng 1080p: đo thật ffmpeg ngốn **1.235 MB** (dựng mạnh, cân bằng, 2 luồng); bản đầy đủ 1080p preset Chuẩn TVC **2.465 MB**. Botkeep 2 GB bị giết từ ~970 MB (job 6) → hễ Kaggle chờ máy, lùi về Botkeep là bản ngắn chết ở bước cuối | Trước khi chọn 1080p, đọc RAM trống của container: dưới 1,8 GB (cân bằng) / 3 GB (Chuẩn TVC) thì xuất 720p, kèm ghi chú «dựng trên Kaggle thì được 1080p» |

Codex PR #9:
- Lượt soát lời không được đổi THỨ TỰ số hay nguyên liệu. Trước đây chỉ so tập hợp, nên tráo «2 muỗng nước mắm, 1 muỗng đường» thành «1 muỗng nước mắm, 2 muỗng đường» vẫn lọt.
- Lời đọc dứt trước màn kết cả khi màn kết đè lên cuối video (chạm trần độ dài, không nối thêm được).

Đề xuất còn lại (cần quyết định của bạn, chưa làm bằng mã):
- **Nhạc nền nhẹ dưới lời đọc** (TVC luôn có): cần thư viện nhạc có bản quyền / miễn phí bản quyền. Nhạc có bản quyền sẽ bị YouTube Content ID nhận và TikTok tắt tiếng. Muốn thì làm ở bản sau (trộn -24 dB, tự nhỏ lại khi có lời).
- **Tông màu:** mặc định «tươi sáng» làm nồi inox, tường xám càng nhạt. Món luộc / hấp nên thử «ấm mộc» (Cài đặt → Tông màu video).
- **YouTube Shorts:** đăng bản ngắn ≤ 90 giây; bản 3 phút để đăng Facebook / YouTube video thường.

## Bản 49 — job 6 lỗi khi dựng trên Botkeep (17/11/2026)

Job 6: Kaggle chờ máy quá 35 phút (hàng chờ GPU T4 hôm đó 12-45 phút) → lùi về Botkeep → ffmpeg dựng mạnh chết ở giây
68 / 188, log chỉ có dòng tiến độ («ffmpeg lỗi: frame= 2062 fps=6.6 …»). Đây là lần đầu bản dựng mạnh phải dựng trên
Botkeep (job 3-5 dựng trên Kaggle, 31 GB RAM).

Đo lại bằng đúng lệnh dựng của bot trên video mực 1080×1920, kế hoạch dựng mạnh 46 khúc: ffmpeg ngốn **1,08 GB** (bản
thường 0,34 GB); ở 2 luồng như Botkeep là 0,97 GB, cộng bot trên máy 2 GB RAM → bị hệ thống giết.

| Lỗi | Sửa |
| --- | --- |
| Mỗi khúc chính (36 khúc) chạy riêng cả chuỗi màu: khử nhiễu hqdn3d (giữ khung trước), chỉnh màu, làm ấm, làm nét | Nguồn đủ lớn (không phải phóng to): mỗi khúc chỉ cắt khung + đổi cỡ, khử nhiễu / màu / làm nét chạy MỘT lần sau khi ghép (cả đoạn mở đầu và clip phụ); nguồn nhỏ giữ cách cũ |
| `-threads` chỉ đặt cho đầu vào đầu tiên; đầu vào phụ (đoạn mở đầu, clip phụ, các khúc của clip phụ) giải mã theo số lõi MÁY CHỦ — HEVC 1080×1920 ngốn 120 MB với 2 luồng, 277 MB với 16 luồng | Mọi đầu vào của mọi lệnh ffmpeg giải mã theo số lõi container |
| Lỗi ffmpeg bị giết không nói gì | Báo mã thoát; bị giết bằng tín hiệu thì ghi «bị hệ thống dừng (tín hiệu 9, thường do máy hết RAM)» |

Sau khi sửa, cùng lệnh dựng ở 2 luồng: **0,57 GB** (giảm 41%; trên máy chủ nhiều lõi bản cũ còn tốn hơn).

Lỗi nội dung thấy trong log job 6:

| Lỗi | Sửa |
| --- | --- |
| Lời «Tóm lại: dùng mực đông lạnh, đun sôi nước, cho hành tây, vớt mực ra đĩa» bị AI viết thành «Nấu kiểu này đơn giản lắm, ai làm cũng ngon» → thẻ TÓM TẮT chiếu với lời khen chung chung | Lệnh cho AI có luật riêng cho đoạn «Nguyên liệu gồm:» / «Tóm lại:» (đọc đủ ý, đúng thứ tự); mất quá nửa ý thì bắt viết lại; vẫn mất thì đọc đúng lời máy soạn («Tóm lại là …», «Chuẩn bị …») |
| Bỏ số bịa «3-4 muỗng» để lại câu treo «rưới thêm dầu mè, độ chừng cho dậy mùi» | Bỏ luôn chữ ước lượng đứng trước (khoảng, chừng, độ chừng, tầm, cỡ…) |
| «1/2 muỗng hạt nêm, 1/2 muỗng hạt nêm» (Whisper nghe lặp, AI chép theo) | Bỏ mục lặp liền nhau trong câu liệt kê |

Dọn Kaggle theo yêu cầu: xoá dự án Qwen (rmvideo-bench-qwen, rmvideo-bench-probe, rmvideo-lab-llm) và 4 notebook thử
nghiệm cũ (rmvideo-lab, rmvideo-lab-an, rmvideo-lab-mount, rm-video-poc); còn rmvideo-worker, rmvideo-analyze,
rmvideo-phowhisper. Bỏ hướng Qwen offline (đo thử: Qwen3.6 lệch ý / bịa nguyên liệu, Qwen3-30B lệch đoạn hình).

### Soát video job 7 (chạy bản 48)

Hình đúng thứ tự từ đầu tới cuối (so từng khung với video gốc). Chỗ «phút 1:51 như bị sai thứ tự» là do LỜI:

| Lỗi | Sửa |
| --- | --- |
| 1:47-1:57 hình là mực đã bung trong nồi (gốc: «sau khi mình luộc khoảng tầm 4 phút, lúc này cái mực bung ra»), AI đọc «giờ mình thả mực vô nồi nước đang sôi, đun chừng 4 phút…» — bước thả mực đã làm ở 0:26 → nghe như video quay ngược | Lệnh cho AI thêm luật «không kể lại bước đã làm». Máy tự bắt: đoạn có «cho nguyên liệu X vào» (thả / bỏ / đổ / nêm / rắc / rưới / thêm…) mà lời gốc của chính đoạn không có thao tác cho vào, và X đã được cho vào ở đoạn trước → bắt viết lại; vẫn còn thì bỏ vế câu đó (không có con số). Chạy lại trên cả kịch bản job 7: chỉ đúng đoạn 1:51 bị bắt |
| Lời tóm tắt «Dùng mực đông lạnh, trụng hành tây rồi vớt mực» bỏ hẳn bước «đun sôi nước» (còn 58% chữ nên lọt kiểm bản 49) | Kiểm thêm TỪNG Ý: ý nào không còn chữ riêng nào (vd «đun / sôi / nước») là mất ý → viết lại / đọc lời máy soạn. Lời máy soạn cũng đổi từ miền Bắc («ra dĩa») |
| Lời điểm danh trễ thẻ chữ ~1 giây (thẻ «1/2 muỗng đường» hiện cùng phụ đề «nửa muỗng bột ngọt»): câu hook lấn 0,46s sang khúc điểm danh + AI mở bằng «Rồi mình chuẩn bị…» | Câu ngay trước khúc điểm danh / tóm tắt không được lấn sang khúc đó (mượn khoảng lặng phía trước, hoặc rút gọn); bỏ chữ dẫn «Rồi mình / Giờ mình…» trước «chuẩn bị»; lệnh cho AI: vào thẳng món đầu. Kaggle nhận kèm lời gốc của khúc điểm danh để xếp giọng giống Botkeep |
| (thấy khi viết test) Thẻ điểm danh ghi «1/2 muỗng», AI đọc «nửa muỗng» → bị coi là đổi số; bỏ số thì «1/2 muỗng đường» thành «1/ đường» | «1/2», «1/4», «3/4» so như 0,5 / 0,25 / 0,75; bỏ số coi «1/2» là một số |

Còn thấy, chưa sửa bằng máy: 2:28 «Dĩa mực nóng hổi» khi hình là mực trong vợt; 3:01 AI viết «thơm lừng rảnh rỗi nè» (chữ
lạc nghĩa, sửa tay lúc duyệt); 3 quãng lặng 4-5 giây (1:35, 2:10, 2:24); câu cuối dứt sát màn kết (~0,1s). Số «3 phút»
(1:33) và «4 phút» (1:25, 1:54) đều đúng lời gốc (người làm video nói cả hai).

### Soát lại trước khi gộp (PR #8: Codex + một lượt soát độc lập)

| Lỗi | Sửa |
| --- | --- |
| Lời gốc có cho BẤT KỲ thứ gì vào là bỏ qua kiểm «kể lại bước» (gốc «cho hành tây vô» thì AI viết lại «thả mực vô» vẫn lọt) | Chỉ bỏ qua khi gốc nêu đúng nguyên liệu đó, hoặc có thao tác cho vào mà không nói cho gì («bỏ trực tiếp vô luôn») |
| Món nhiều công đoạn bị bắt nhầm là kể lại: «ướp mực» rồi «thả mực vô chảo chiên», «luộc trong nồi» rồi «trút mực ra dĩa» → vế câu đúng hình bị xoá | Chỉ coi là kể lại khi cho CÙNG nguyên liệu vô ĐÚNG chỗ cũ (cùng nồi / chảo / tô…); «ướp» và «trút / đổ … ra» không phải cho vào |
| Bỏ số bịa cắt luôn chữ của từ ghép: «chiên ở nhiệt độ 170 độ» → «chiên ở nhiệt», «kích cỡ 2 cm» → «kích» | «độ» / «cỡ» đứng sau nhiệt, mức, tốc, kích… được giữ lại; thêm chữ ước lượng «khoảng tầm» |

Chạy lại trên 160 đoạn lời thật của các job trước: không bắt nhầm đoạn nào; job 7 vẫn bắt đúng đoạn 1:51. Codex còn nêu: khử
nhiễu hqdn3d chạy sau khi ghép khúc thì «trộn» qua điểm cắt. Đo thật trên video mực: khung đầu sau điểm cắt lệch tối đa
2/255, PSNR 53-56 dB (mắt không phân biệt), nên giữ (tách lại từng khúc là quay về lỗi hết RAM của job 6).

## Bản 48 — soát video job 5 (17/11/2026, chạy bản 47)

Bản 47 đã có hiệu lực: lời tóm tắt đọc khớp thẻ TÓM TẮT, «3 đến 4 giọt dầu» đúng đơn vị, chỉ một thẻ «»» 4 PHÚT SAU»,
giọng và phụ đề dứt trước màn kết, câu chốt đủ.

| Lỗi | Sửa |
| --- | --- |
| Lời đọc thưa: 322 chữ (job 4 cùng video: 477), giọng phủ 54% video (job 4: 80%), 7 quãng lặng 5-10 giây. Từng đoạn chỉ hơi ngắn nên lọt luật «đoạn quá ngắn» (phải ≥ 1/4 số đoạn); câu mới «từ đệm dùng TIẾT CHẾ» ở bản 47 có thể khiến AI viết gọn cả câu | Thêm luật cả bài: tổng chữ < 70% mức tối thiểu thì bắt viết lại (kể tên các đoạn thiếu nhiều nhất); câu về từ đệm viết lại rõ «chỉ bớt từ đệm, mỗi đoạn vẫn viết ĐỦ số từ» |
| Thẻ điểm danh «4 giọt dầu mè», gốc «3-4 giọt» (bản 47 thêm đơn vị giọt nhưng khoảng số chỉ lấy số sau) | Khoảng «3-4 giọt» / «3 đến 4 giọt» / «ba tới bốn giọt» giữ nguyên khoảng; không thành MỘT con số nên thẻ chỉ ghi tên nguyên liệu, không bao giờ ghi số sai; kiểm lời đọc so cả khoảng |
| Cuối video hai lời mời liền nhau: «…cả nhà tranh thủ làm thử nha. Dễ làm nha. Đong đủ là ngon, cả nhà nấu thử nha.» | Câu chốt của kênh có mời nấu thử thì bỏ lời mời AI tự viết ở hai đoạn cuối |
| Đỉnh âm +0,1 dBTP (bộ chặn -1,4 dBFS, nén AAC đẩy đỉnh lên 0,5-1 dB) | Bộ chặn -1,9 dBFS |

Thêm (theo ý tưởng của bạn: mô hình học theo ví dụ tốt hơn theo luật):

| Phần | Làm gì |
| --- | --- |
| Sổ giọng Bếp Đong Đủ | `brand/bep_dong_du/voice_examples.json`: 13 cặp «lời gốc → lời kênh» (câu mở đầu, thao tác có định lượng, lúc chờ, mẹo, bày món, cảm thán, tóm tắt, chào kết) ở nhiều món; mỗi lần viết lời đưa 6 cặp (luôn có câu mở đầu + thao tác có định lượng, còn lại xoay vòng theo video); chép gần nguyên câu mẫu thì bắt viết lại |
| Kho lời đã duyệt | Duyệt kịch bản thì lưu các đoạn đã chốt (`voice_bank.json`); đoạn sửa tay được đưa vào mẫu ở video sau (tối đa 2, ưu tiên cùng nguyên liệu) |
| Đổi từ miền Bắc | Sau khi AI viết (lời, tiêu đề, caption, rút gọn), trước bước kiểm: thìa → muỗng, bát → chén, đĩa → dĩa, rán → chiên, lợn → heo, vừng → mè, ngô → bắp, mì chính → bột ngọt, trần qua → trụng qua, quả chanh / 2 quả → trái…; theo ngữ cảnh («trần nhà», «kết quả», «lá dứa», «Ninh Bình», «bát ngát» giữ nguyên) |
| Chỉ số lời đọc | Mỗi job thêm dòng «Lời đọc: N chữ, giọng phủ X% video, K quãng lặng trên 4 giây, a/b câu kết «nha»» |

Còn lại (chưa sửa): thẻ điểm danh đi trước lời đọc ~1 giây (câu hook dài sát khe 2,6 giây); đoạn giải thích mực đông /
mực tươi lần này lại bị coi là tán gẫu, tua nhanh (thẻ mẹo vẫn có ý chính) — tuỳ kết quả AI đọc công thức từng lần.

## Bản 47 — soát video job 4 (17/11/2026, Kaggle, dựng mạnh + màn kết mặc định)

Soát bằng nhận dạng giọng bản ra, khung 2 giây / lần kèm lời đang đọc, khung nửa giây ở đoạn điểm danh và đoạn kết, so
khung với video gốc, đo âm thanh từng 0,25 giây ở cuối.

| Lỗi | Sửa |
| --- | --- |
| Thẻ TÓM TẮT CÁCH LÀM chiếu 2 giây mà không có lời tóm tắt (lại). AI ghi bước bày món hết 188,9s, nằm GIỮA câu Kaggle gộp «Rất là ngon luôn nè Vậy là đã xong…» (188,2-192,0s) → khúc tóm tắt chen giữa câu, câu đó phủ lên khúc tóm tắt, AI viết một câu «Mực tươi ngon, vậy là xong…» đọc đè | Khúc tóm tắt chèn vào khoảng lặng TRƯỚC câu (lùi qua các câu nối liền); luật mới R8: không khúc phụ nào được cắt đôi một câu (phạm thì bỏ đúng phép đó) |
| Hai thẻ «»» 4 PHÚT SAU» cho cùng một lần luộc («luộc khoảng 4 phút» … «sau khi luộc khoảng tầm 4 phút») | Mỗi mốc thời gian chỉ ghi một lần, lần sau chỉ «»»» |
| Gốc «dầu mè khoảng 3-4 giọt», lời đọc «3-4 muỗng dầu mè»: bộ kiểm chỉ so con số («4» có trong «luộc 4 phút») và không biết «giọt» | Thêm đơn vị giọt / nhúm; với đơn vị đo (muỗng, giọt, g, ml, phút…) đổi đơn vị cũng tính là đổi định lượng; còn sót thì chỉ bỏ đúng cụm «3-4 muỗng», giữ «luộc 4 phút» |
| Câu chốt «… cả nhà nấu thử nha» đọc đè lên màn kết 1 giây, phụ đề hiện xuyên qua màn kết, âm hiệu chen vào chữ «nha» cuối: giọng được xếp tới hết CẢ màn kết | Lời đọc (AI viết, ước lượng, xếp giọng, rút gọn) và phụ đề dứt trước màn kết |
| 8/26 câu kết bằng «nha» (11 lần trong 3 phút): nghe đều đều như máy (câu mẫu trong lệnh cho AI đều kết bằng «nha») | Lệnh cho AI: từ đệm cuối câu dùng tiết chế, đổi qua lại; hai câu liền nhau cùng kết bằng «nha» / «nè» thì bỏ ở câu sau |
| Bảng thời gian ghi «Khác 68s» | Tách tên: AI hiểu món, AI chọn cảnh mở đầu, lập kế hoạch dựng |

Đã ổn: chữ hook vừa khung và tắt đúng lúc điểm danh; 5 thẻ nguyên liệu khớp lời đọc (lệch ≤ 0,5s); thẻ mẹo đúng lúc;
đoạn nêm gia vị không còn bị tua; đoạn giải thích mực đông / mực tươi giờ có lời; âm lượng -14 LUFS; âm hiệu nghe rõ;
câu chốt kênh đọc đủ.

## Bản 46 — lời đọc lệch khúc điểm danh / tóm tắt (17/11/2026, job 3: dựng mạnh đã chạy)

Soát bằng nhận dạng giọng + dò thẻ trên từng khung: thẻ nguyên liệu hiện 3,2-8,5s nhưng lời «Các nguyên liệu gồm…» đọc
5,9-14,7s; thẻ TÓM TẮT hiện 161-164s mà không có lời tóm tắt.

Soát kỹ lượt 2 trên chính video job 3 (khung 2 giây / lần kèm lời đang đọc, khung dày ở đoạn điểm danh và nêm gia vị,
so với video gốc, đo âm thanh): thêm 4 lỗi —

| Lỗi | Sửa |
| --- | --- |
| Đoạn đầu bếp múc đường / hạt nêm (33-35s gốc) bị tua nhanh: người gốc nói «nêm nửa muỗng…» xong mới múc, quãng lặng 4,6s chuyển động thấp (muỗng ở mép khung) | Sau câu dặn thao tác có nguyên liệu / định lượng (không phải câu báo thời gian chờ như «nướng 30 phút») giữ tốc độ thường 4 giây rồi mới được tua |
| Câu chốt kênh bị AI rút gọn còn «cả nhà nấu thử nha» (mất «Đong đủ là ngon») | Câu chốt không đưa cho AI rút gọn, chỉ rút phần trước nó |
| Âm hiệu màn kết ~-40 dB, gần như không nghe (file tạo bằng nguồn sine của FFmpeg chỉ 1/8 biên độ: -35 LUFS, đỉnh -29 dBFS) | Tạo lại file (-17 LUFS); lúc dựng tự chuẩn hoá đỉnh mọi âm hiệu (kể cả file tải lên) về -4 dBFS |
| Chữ hook đè thẻ nguyên liệu đầu tiên 0,5 giây | Chữ hook tắt đúng lúc điểm danh bắt đầu |

Đã ổn: âm lượng -14 LUFS, giọng cao hơn tiếng nền ~10 dB, không lọt tiếng gốc; thẻ nguyên liệu đúng cảnh; thẻ mẹo; nhãn
tua; màn kết nối thêm.

| Lỗi | Sửa |
| --- | --- |
| Câu thứ hai bắt đầu 2,68s, khúc điểm danh chèn ở 2,7s (ranh giới chuyển cảnh) → câu bị tính bắt đầu TRƯỚC khúc điểm danh, gộp chung, AI đọc câu đó đè lên thẻ rồi mới đọc nguyên liệu | Dời ranh giới khúc về đúng cuối câu mở đầu (2,58s) để chèn |
| Lời «gốc» của tóm tắt gộp vào câu chào kết không dấu chấm → AI viết một câu chào kết, bỏ ý tóm tắt | Lời của khúc điểm danh / tóm tắt (đánh dấu «Nguyên liệu gồm:» / «Tóm lại:») không bao giờ gộp với câu khác; cảm thán dời chỗ và tóm tắt có lời riêng |
| Tóm tắt chen giữa «chúc thành công» và «cho mấy điểm» | Tóm tắt đặt sau bước bày món, trước lời chào kết |

## Bản 45 — dựng mạnh vẫn bị bỏ trên job thật (17/11/2026, lần 3)

Dựng lại đúng dữ liệu phiên Kaggle (tách giọng Kim_Vocal_2 → Whisper large-v3 cùng tham số `asr_local` trên giọng đã
tách → 58 đoạn như log; điểm chuyển cảnh `scenes._analyze_sync` mức full) + 5 lần AI đọc công thức thật: cả 5 lần
«lớp B vi phạm luật (R1 mất câu ở giây 8.6)». Lỗi nằm ở BỘ KIỂM, không ở AI:

| Lỗi | Sửa |
| --- | --- |
| Lời Kaggle gộp câu dài (8,56-14,28s) vắt qua điểm chuyển cảnh 8,67s → câu nằm ở hai khúc liền nhau; luật R1 đòi câu nằm trọn MỘT khúc → báo «mất câu», bỏ cả lớp B | R1 tính trên dãy khúc liền nhau cùng tốc độ thường |
| Một phép phạm luật là bỏ cả lớp B | Bỏ đúng phép bị nghi theo luật hỏng rồi dựng lại; bỏ hết vẫn phạm mới lùi về lớp A. Ghi chú nêu phép đã bỏ |
| Câu mở đầu hết 2,58s, chuyển cảnh 2,7s: không tách được khúc → điểm danh nguyên liệu chèn lên đầu video | Sát cuối khúc thì chèn sau khúc |
| «sôi vừa vừa» nghe thành «sâu dừa dừa»: bộ kiểm tưởng thiếu nguyên liệu «dừa», bắt AI viết lại 2 lần | Thêm vào bảng sửa nghe nhầm |

Sau sửa: 10/10 kết quả AI (5 trên lời như Kaggle, 5 trên lời large-v3 thường) đạt dựng mạnh, mở bằng cảnh món rồi điểm
danh 4-5 nguyên liệu, tóm tắt 4-5 bước. Dữ liệu: `tests/data/muc_hap_gung_kaggle.json`.

## Bản 44 — soát lần 2 video «mực hấp gừng» (17/11/2026, Kaggle bóc lời + dựng)

Bản 43 chạy đúng trên máy thật (chữ hook vừa khung, đúng dấu, logo, màu kênh, thẻ mẹo, màn kết; lời khớp hình — đo bằng
nhận dạng giọng + so khung hình với video gốc). Lỗi còn lại, dựng lại trên dữ liệu thật (`tests/data/muc_hap_gung.json`:
lời gốc Whisper large-v3, kết quả AI đọc công thức, chuyển động):

| Lỗi | Sửa |
| --- | --- |
| Dựng mạnh lùi về dựng nhẹ: khúc tóm tắt lấy giữa bước «bắc nước sôi» đúng quãng lớp A đã tua (luật R5) → mất điểm danh nguyên liệu + tóm tắt | Khúc tóm tắt / điểm danh chỉ lấy cảnh còn trong video chính, tốc độ thường (`_kept_at`); video thật: đạt R1-R7, điểm danh 5 nguyên liệu, tóm tắt 5 bước |
| Bỏ «chào hỏi» từ giây 0 làm mất cảnh đầu bếp nhấc mực thành phẩm, video mở bằng thẻ «1/2 muỗng bột ngọt» | Câu đầu (không phải câu chào) giữ làm câu hook, điểm danh nguyên liệu chèn ngay sau |
| Gốc «nửa muỗng bột ngọt» (Whisper nghe «nổ muỗng») → lời đọc «1 muỗng» x3; bộ kiểm bắt được mà vẫn để lọt | «nổ + đơn vị» = «nửa»; thêm sửa «bột mọt», «mật hấp gừng», «sóc nhiệt», «cằn tàu»; còn số AI tự điền sau 2 lần viết lại thì bỏ con số khỏi câu |
| Lời cụt kiểu tít báo / mệnh lệnh («Bắt nồi nước sôi sùng sục rồi thả vô luôn.»), AI viết ~nửa số chữ → lặng 5-7 giây, nghe «chất AI» | Lời nhắc: giọng nói chuyện có «mình / mọi người», từ nối; luật «động từ đi trước» cho phép 1-3 chữ dẫn («Giờ mình…»); đoạn viết dưới 60% số chữ tối thiểu thì bắt viết lại |

Giọng đọc: giọng mặc định VieNeu «Mỹ Duyên» là giọng nữ miền Nam **phong cách đọc truyện**; mẫu so sánh các giọng
khác trong buổi soát (đổi ở ô giọng VieNeu trong Cài đặt).

## Bản 43 — sửa lỗi chạy thật «mực hấp gừng» (16/11/2026) + bộ nhận diện «Bếp Đong Đủ» làm mặc định

Soát bản ra bằng nhận dạng giọng + so khung hình với video gốc: lời đọc đúng mốc của lời gốc (phần tua nhanh đã dời
mốc đúng), lệch nằm ở NỘI DUNG và chỗ trống. Kiểm tra: ruff, mypy sạch; pytest toàn bộ xanh (thêm `tests/test_ban43.py`:
dựng thật bằng FFmpeg đo điểm ảnh mép khung, Gemini quá tải giả lập, hạn tự thoát của phiên Kaggle chạy thật bằng
Python); snapshot cũ chỉ thêm khoá `GEMINI_FALLBACK_MODELS`, thêm 2 kịch bản dùng bộ nhận diện mặc định.

| Lỗi thấy trong log / video | Sửa |
| --- | --- |
| Chữ mở đầu «MỰC HẤP GÙNG GIÒN NGỌT» chạm hai mép (xuống dòng theo 22 ký tự, chữ in hoa rộng 752px > 720px) | `rmvideo/media/textfit.py`: đo theo bề rộng thật của font, chọn số dòng + cỡ chữ; áp cho hook, thẻ chữ, màn kết, ảnh bìa |
| Hook sai dấu «GÙNG» (AI viết IN HOA hay rơi dấu móc) | AI viết chữ thường, máy in hoa; chữ không có trong tiêu đề / lời mà có chữ cùng khung (cùng dấu thanh) thì sửa theo |
| Gemini 503 «high demand» suốt 5 phút → «Gemini không nhận ảnh, viết lại không kèm ảnh»: AI viết lời KHÔNG nhìn hình («thả mực vô» lúc mực đã trong nồi), không đọc được công thức | Quá tải / hết lượt không còn bị coi là lỗi ảnh; thử lần lượt model dự phòng (`GEMINI_FALLBACK_MODELS`, mặc định gemini-3.1-flash-lite, gemini-2.5-flash-lite), áp cho mọi lượt gọi Gemini |
| Video im 14-16 giây hai chỗ dù đầu bếp đang nói (Whisper kéo một chữ phủ cả quãng, quãng trống < 8s nên không nghe lại) | Chữ dài quá 1,2 giây chỉ tính 1,2 giây khi dò quãng trống; nghe lại quãng từ 5 giây, tối đa 6 quãng |
| Bóc lời chờ máy Kaggle 30 phút rồi mới làm trên Botkeep (12 giây) | Chờ máy tối đa 8 phút cho bước bóc lời (dựng: 35 phút) |
| «Không huỷ được phiên Kaggle: Token Kaggle sai hoặc hết hạn (403)» — token đúng, lệnh huỷ gửi tên notebook trong khi Kaggle chỉ huỷ theo mã phiên (không lệnh nào trả mã đó) | Bỏ lệnh huỷ giả; mỗi việc gửi Kaggle mang hạn bắt đầu, phiên được cấp máy sau khi bot đã thôi chờ thì thoát ngay (không tốn giờ GPU, không giữ chỗ) |
| «Lập kế hoạch dựng» 155 giây trên Botkeep | Đo chuyển động bỏ giải mã khung không làm mốc: nhanh ~2 lần, tín hiệu tương quan 0,99 |
| Màn kết đè 2,2 giây cuối (che món, âm hiệu chen câu cuối) | Màn kết nối thêm sau video (giữ khung cuối); âm hiệu vang sau câu đọc cuối |

**Bộ nhận diện mặc định**: tên kênh, câu dưới tên, câu chốt, màu, tông tươi sáng, tên loạt, ảnh bìa theo kênh, logo +
âm hiệu `brand/bep_dong_du/` (khi chưa tải file riêng), dựng mạnh, công thức + mẹo, bản ngắn. Nhập `0` để tắt chữ / màu.

**Cần làm khi cập nhật lên Botkeep**: không. Lần khởi động đầu, các ô đang đúng mặc định cũ tự chuyển sang bộ nhận
diện (log «Đã chuyển N cài đặt sang bộ nhận diện mặc định»); ô bạn đã chọn khác giữ nguyên.

## Bản 42 — phong cách remake, công thức đã đối chiếu, nhận diện kênh (chi tiết: docs/KE_HOACH_REMAKE_STYLE.md)

Mọi khoá mới mặc định = hành vi cũ, trừ: giọng văn mặc định đổi sang **miền Nam** (`SPEECH_STYLE=mien_nam`), tự đăng
**bỏ video quảng cáo của nguồn** (`AUTO_SKIP_ADS=1`) và **lọc nguồn dưới 540p** (`AUTO_MIN_HEIGHT=540`). Kiểm tra: ruff,
mypy sạch; pytest toàn bộ xanh (thêm test đối chiếu định lượng, kế hoạch dựng với dữ liệu ngẫu nhiên, luật R1-R7,
dựng thật bằng FFmpeg, dấu vân hình); snapshot cũ chỉ đổi ở khối giọng văn + khoá cài đặt gửi Kaggle.

**Cần làm khi cập nhật lên Botkeep**: không bắt buộc. Muốn dùng: nhóm ✨ (REMAKE_STYLE, POST_RECIPE, TIPS) và 🎨 (màu,
tông, tagline, câu chốt, âm hiệu, mẫu ảnh bìa) trên web. Đã đặt `SPEECH_STYLE=mien_tay` trên web trước đây thì đổi tay.

| Giai đoạn | Nội dung |
| --- | --- |
| S0 | Giọng văn miền Nam mặc định; khoá cài đặt mới (nhóm ✨, 🎨); Job lưu `edit`, `recipe` (bản duyệt dùng lại) |
| S1 | `rmvideo/domain/recipe.py` + `flow/dish.py`: đọc số tiếng Việt, quy đơn vị chuẩn VN, kiểm từng định lượng với lời gốc / mô tả, cộng dồn, công thức + mẹo trong bài đăng, bảng đối chiếu ở màn duyệt, lọc video quảng cáo |
| S2 | Bộ nhận diện kênh: bộ màu, hạt phim, màu kênh (phụ đề, khung hook, thẻ, ảnh bìa), màn kết + tagline, âm hiệu (web + Kaggle), câu chốt, mẫu ảnh bìa + số tập |
| S3 | `rmvideo/media/edl.py` (kế hoạch dựng, dùng chung Kaggle) + `flow/style.py` lớp A: cỡ khung luân phiên, bỏ khoảng chết, tua nhanh + thẻ, đẩy cận; khúc lặp / đảo dựng bằng clip phụ |
| S4 | `flow/style_b.py` lớp B: điểm danh nguyên liệu, tóm tắt, dời cảm thán, đổi chỗ sơ chế độc lập, bỏ lời tán gẫu; luật R1-R7, lùi về lớp A |
| S5-S6 | Dấu vân hình chống trùng (`flow/dedupe.py`), thẻ «MẸO» trên video, tài liệu, chạy thử trên video thật của nguồn |
| Tăng tốc Kaggle | Bản ngắn dựng chung một phiên Kaggle với bản đầy đủ (`Remake.prepare_short` + `produce_batch`), bản đầy đủ 720p / bản ngắn 1080p, tách giọng một lần mỗi video trong phiên, mã hoá GPU h264_nvenc khi có (`renderer.nvenc`) |
| Bản ngắn | `flow/short.py` + `Remake.render_short`: thêm «Bản ngắn» ≤ 90 giây (`SHORT_VERSION`) sau bản đầy đủ, giữ mọi bước chính đúng thứ tự + điểm danh nguyên liệu; tự đăng chọn bản (`AUTO_POST_VERSION`) |

Hoãn có chủ đích: đọc số tiếng Trung / Anh + đổi cup / oz / °F, đọc chéo chữ định lượng trên hình (S7 — khi dùng nguồn
nước ngoài); chế độ riêng cho từng kênh nguồn (hiện chỉ một nguồn nấu ăn).

## Bản 41 — tái cấu trúc kiến trúc (chi tiết: docs/KE_HOACH_REFACTOR.md)

Không đổi chức năng người dùng thấy; giữ nguyên prompt AI từng byte và quyết định dựng (chụp bằng 25 kịch bản
snapshot trước khi sửa). Kiểm tra: `ruff check .` sạch, `mypy` sạch cho gói `rmvideo/`, `pytest` 503 test xanh + 5 bỏ
qua (cần key / model offline); CI chạy Python 3.11, 3.12, 3.13.

**Cần làm khi cập nhật lên Botkeep**: không có bước bắt buộc. `jobs.db` tự chuyển sang chế độ WAL (sinh thêm
`jobs.db-wal`, `jobs.db-shm` cạnh nó — đừng xoá khi bot đang chạy). Đổi `YTDLP_PROXY` / `PUBLIC_URL` / tài khoản
Kaggle / Fanpage / `ALLOWED_USERS` trên web giờ hỏi lại mật khẩu.

| Vùng | Trước | Sau |
| --- | --- | --- |
| Bộ điều phối | `pipeline.py` 1.230 dòng, `_process` một hàm 519 dòng / 15 hàm lồng | `rmvideo/app/flow/` 10 module; lớp `Remake` với các bước có tên, trạng thái có kiểu |
| Dựng Botkeep ↔ Kaggle | hai bản giữ giống nhau bằng tay (`pipeline` và `kaggle_job.produce`) | một `rmvideo/app/assemble.py` cho cả hai |
| Media | `core_video.py` 1.802 dòng (tải, giọng, xếp giọng, bộ lọc, render) | `rmvideo/media/` 7 module |
| Web / Telegram | `build_app` 592 dòng, `tg_bot.setup` 316 dòng | `rmvideo/interfaces/web/` (auth, http, 5 nhóm route), `telegram/` (access, keyboards, handlers) |
| Cài đặt | 3 nguồn sự thật (EDITABLE, META + chuỗi if 70 dòng, `_SETTING_OPTIONS`); đổi giữa job | một schema (`rmvideo/app/settings.py`); cài đặt dựng chờ job xong; khoá nhạy cảm hỏi lại mật khẩu (A5) |
| Gọi AI | 6 vòng Gemini→OpenRouter tự viết; module khác gọi `_call_gemini` riêng tư | `rmvideo/services/llm.py`; `prompt_engine.call_gemini` công khai |
| Tạo Job | 11 lời gọi `Job(...)` ở 6 tệp | `rmvideo/app/job_factory.py` |
| Tiến trình con | ~40 chỗ, không timeout, huỷ giết cả ffprobe của web, không giết cháu, yt-dlp không dừng | `rmvideo/media/proc.py`: theo job, giết cả nhóm, stderr có trần; yt-dlp dừng khi huỷ (C8, C9) |
| Theo dõi tiến độ | 3 vòng hỏi lại 1-2 giây; SSE dựng lại danh sách mỗi giây/khách | bus sự kiện; SSE gửi khi đổi, dựng một lần cho mọi khách |
| SQLite | 2 kết nối, nhật ký mặc định | WAL + `busy_timeout` (C10) |
| Bundle Kaggle | danh sách tệp viết tay | tự tính từ import thật; test chạy được trên Python 3.10 |

Lỗi sửa được khi rà: ghi chú "⚠️ AI viết kịch bản lỗi" bị xoá khi video có dải chữ gốc (đường đọc một mạch); nút
Telegram trên tin nhắn đã cũ làm handler lỗi im lặng (giờ báo gửi lại); `cookies.txt` tạo ra 0644 (giờ 0600).
Hiệu năng: chấm điểm khung ảnh bìa và đọc ảnh đoạn hay ra khỏi vòng lặp sự kiện; đo độ dài giọng không mốc từ bớt một
ffprobe mỗi đoạn (95ms → 56ms, kết quả giống hệt); đo song song tối đa 4 tiến trình (máy 2GB RAM).

## Giai đoạn 0 — vá khẩn

| Lỗi | Sửa |
| --- | --- |
| #1 Bot Telegram ai cũng dùng được | `ALLOWED_USERS` + middleware aiogram; người lạ được báo chat id |
| #2 Mật khẩu web qua `?auth=`, so sánh `!=`, localStorage, không chặn dò, thiếu mật khẩu là mở API | Cookie phiên ký HMAC (HttpOnly, SameSite=Strict), kiểm Origin chống CSRF, `hmac.compare_digest`, khoá 5 lần sai/phút (và trần toàn cục), thiếu `WEB_PASSWORD` thì khoá `/api` |
| #3 Màn duyệt hiện sai lời gốc của đoạn đã gộp | `generate_timed` trả `orig` từng đoạn |
| #4 Duyệt lúc hàng đợi đầy mất kịch bản | `put()` trước, đổi trạng thái sau; job đã duyệt được vượt giới hạn; Telegram báo lỗi thay vì văng |
| #5 SSRF qua yt-dlp | `url_guard.py`: danh sách nền tảng, chặn IP nội bộ sau khi phân giải DNS, tắt extractor generic; áp ở cả 4 lối vào và trong mọi hàm gọi yt-dlp |
| N1 Bản duyệt không cắt đoạn quảng cáo cuối | Lưu `cut_at` lúc viết kịch bản, áp lại khi render bản duyệt |
| N2 Gemini 429 không thử lại; một lô lỗi là mất cả kịch bản khớp hình | Thử lại theo `retryDelay` (tối đa 60s, 3 lần), tối đa 2 lô song song, lô lỗi gọi lại riêng |
| N3 Tải model/mã ngoài không kiểm | SHA-256 cho Kim_Vocal_2; server bgutil build đúng tag phiên bản plugin, `npm ci` |
| N5 `/health` lộ thông tin; web đổi được base URL | `/health` chỉ trả `ok` + số job chờ; `OPENROUTER_BASE_URL`, `POT_SERVER` chỉ đặt qua biến môi trường |
| #10 Thư viện không ghim | `requirements.txt` ghim theo khoảng đã kiểm; khai báo aiohttp, bỏ aiofiles không dùng |
| — static-ffmpeg tải lỗi làm bot chết lúc khởi động | Ưu tiên ffmpeg có sẵn; tải lỗi chỉ cảnh báo |

## Giai đoạn 1 — độ bền và dọn mã

- Hàng đợi sống qua restart (#9): job chờ chạy tiếp, job đang chạy dở chạy lại một lần.
- Job chờ duyệt không bị đẩy khỏi bộ nhớ, duyệt qua Telegram được sau restart, hết hạn 24 giờ (N4).
- File tải lên dùng tại chỗ; không chép file lớn trong vòng lặp sự kiện (N6).
- Worker không chết khi gửi Telegram lỗi; video xong mà gửi lỗi thì báo link.
- Bảng `steps`, `events`, `sources` trong jobs.db; `metrics.py`; lệnh `status` có tỉ lệ rơi dự phòng.
- `healthcheck.py`: kiểm tra định kỳ + lệnh `health`; báo `ADMIN_CHAT_ID` khi trạng thái đổi.
- `ytdlp_update.py`: lệnh `update_ytdlp` có thử nghiệm và tự quay về bản cũ.
- `align.py`: căn chữ phụ đề bằng difflib (#6); gộp 3 chỗ đọc STT CapCut; mặc định không gọi STT lần hai.
- edge-tts thử lại 3 lần; ASR dự phòng CapCut không còn phụ thuộc `TTS_PROVIDER`.
- Số luồng FFmpeg/x264 theo số lõi container (#12); xoá mã chết (#11); thoát ký tự lệnh ASS.
- pytest + ruff, CI Python 3.11/3.12 với ffmpeg hệ thống.

## Giai đoạn 2 — tốc độ

- `cache.py` có trần dung lượng và `STEP_VERSION`: đoạn giọng, dò cảnh/dải chữ, tiếng nền đã tách giọng.
- Duyệt rồi render lại: không tải lại, không gọi AI, không dò cảnh lại, chỉ đọc lại đoạn đã sửa (#8).
- Kéo dài video làm trong lần render cuối (#7). Đo: clip 1080p 20s, 1 luồng, kéo 1.2x: 79.5s → 66.7s (−16%),
  SSIM 0.988 so với cách cũ.
- Tách giọng chỉ các khúc có lời; điều kiện bật theo số giây có lời (≤ 45s) thay vì độ dài video.
- Đo độ dài đoạn giọng song song; ước lượng độ dài trước khi đọc để rút gọn sớm đoạn chắc chắn tràn.

## Giai đoạn 3 — chất lượng và ghi nguồn

- Gemini structured output (schema pydantic) cho mọi lời gọi; model không nhận schema thì lùi về JSON mode.
- Dòng "🎬 Nguồn: <kênh> · 🔊 Giọng đọc AI" trong nội dung đăng (`SOURCE_CREDIT`), `ALLOWED_SOURCES`,
  nhật ký nguồn ↔ video ra.

## Sửa sau review độc lập

Một lượt đọc lại toàn bộ thay đổi xác nhận trục thời gian khi kéo dài video là đúng, và tìm thêm các lỗi sau (đã sửa, có test):

- Header `X-Auth` không bị giới hạn thử mật khẩu, `/api/me` trả `authed` theo header → dò được mật khẩu.
- Job đã huỷ được chạy lại sau restart (cờ huỷ chưa lưu); job bị đánh lỗi lúc khởi động không được lưu.
- File tải lên dở (đầy đĩa, ngắt kết nối) không bị xoá; job huỷ trước khi chạy / thiếu chỗ giữ lại file nguồn.
- Bấm duyệt trên web đúng lúc worker vừa dừng ở bước duyệt có thể xoá mất file nguồn của bản duyệt.
- Cắt phần: một lỗi gửi Telegram làm hỏng cả job; mất dòng ghi nguồn; `REVIEW_SCRIPT=1` làm hỏng mọi phần;
  khởi động lại thì giao lại các phần đã giao.
- Cookie/mã tải có ký tự có dấu làm lỗi 500.
- Job chờ duyệt cũ hơn 50 job gần nhất bị bỏ rơi khi khởi động lại.
- Gemini hết hạn mức ngày (retryDelay > 45s) không chờ nữa mà rơi sang dự phòng ngay; không thử "bỏ ảnh" khi lỗi là hạn mức.
- Cửa sổ tách giọng chỉ tính phần thực render (sau cắt quảng cáo); thông báo nói đúng lý do không tách.
- Mép khúc tách giọng cạnh khúc bỏ qua được vuốt về 0 (tránh tiếng tách). Đo với model thật, audio 20s:
  chỉ 1/4 khúc có lời 10.0s, tách cả bài 18.8s (cả hai gồm ~5s nạp model).
- Link video do tikwm trả về cũng bị chặn nếu trỏ vào IP nội bộ.

## Đợt 2 — phần còn lại của kế hoạch

| Hạng mục | Kết quả |
| --- | --- |
| Gemini thật | Gọi thật trên google-genai 2.34 và 3.0.0: viết kịch bản kèm ảnh, JSON theo schema, lỗi 404/400 đúng định dạng; nới ràng buộc lên `< 4` |
| Bóc lời bằng Gemini | `gemini-3.5-transcribe`, mốc từng từ, có dấu câu, tự viết số ("300g"); 11s audio trong ~1.5s; `ASR_ORDER` (mặc định groq,gemini,capcut) |
| Cờ tiếng gốc từng đoạn | Lời mới / giữ tiếng gốc / đọc lời mới lót tiếng gốc; chọn trên web, Telegram ("số: gốc"), hoặc AI đề xuất (`OST_AUTO=1`); đo: tiếng gốc chỉ vang trong cửa sổ đánh dấu (> 25 dB so với ngoài) |
| Timeline + SRT | Mỗi job lưu `timeline.json` và phụ đề `.srt`, tải trên web để dựng tay |
| Web cập nhật trực tiếp | Server-Sent Events thay cho hỏi lại mỗi 4 giây |
| Cắt phần không mã hoá | Điểm cắt dời về khung khoá, chép luồng: đoạn 70s 1080p 0.4s thay vì 17.7s |
| Tách `main.py` | state, storage, review, pipeline, jobs, admin, tg_bot; main.py ~80 dòng |
| Telegram mất mạng | Không còn làm chết tiến trình (và web); thử lại, chờ tăng dần tới 5 phút |
| Test Telegram | aiogram thật với phiên mạng giả: người lạ, luồng link → chế độ → thời lượng, duyệt "số: gốc" |

### Sửa sau review độc lập đợt 2

Review xác nhận việc tách `main.py` giữ nguyên hành vi; tìm và đã sửa (có test):
- Tín hiệu dừng (SIGTERM/Ctrl-C) bị bỏ qua trong lúc chờ thử lại Telegram → giờ thoát trong ~2s.
- Giọng Piper dự phòng bị cache dưới khoá giọng CapCut → chỉ cache giọng của provider chính.
- Spleeter cắt đầu ra xuống bội số 1024 mẫu (đuôi mỗi khúc còn giọng gốc, có tiếng tách) và lỗi với khúc < 4096 mẫu
  → đệm rồi cắt lại; mỗi khúc tách kèm 0.5s ngữ cảnh hai bên.
- Giọng mới lấn vào cửa sổ giữ tiếng gốc (mượn lùi 0.5s, lấn tới 0.35s, vuốt 0.2s ra ngoài) → không còn lấn.
- Bỏ cờ giữ tiếng gốc của đoạn AI để trống lời làm mất đoạn đó → báo cần nhập lời mới; trang/script không gửi cờ
  thì giữ nguyên cờ cũ; trả lời Telegram gõ dấu rời (NFD) vẫn nhận.
- Cắt phần chép luồng hay rơi về mã hoá lại với video 29.97fps (làm tròn mốc khung khoá) → giữ đủ chính xác.
- Tiến trình Piper không bị giết khi huỷ job/quá hạn.
- SSE: dừng khi đăng xuất/đổi mật khẩu, không vượt giới hạn kết nối, client không đọc thì đóng sau 10s; trình cài
  gói offline giải nén vào thư mục tạm rồi mới đổi tên; OCR lỗi ffmpeg thì dùng heuristic thay vì cache "không có chữ";
  kiểm có sherpa-onnx không còn nạp nó vào tiến trình chính.

### Giai đoạn 4 — thử nghiệm (đo trên 1 luồng CPU, `taskset -c 0`)

| Thành phần | Đĩa | RAM đỉnh | Tốc độ (giây xử lý / giây audio) | Chất lượng | Giấy phép | Quyết định |
| --- | --- | --- | --- | --- | --- | --- |
| Tách giọng nhanh Spleeter int8 (sherpa-onnx) | 43MB + 49MB | ~590MB (khúc 10s) | 0.06 (Kim_Vocal_2: 1.82) | Gemini không nghe ra từ nào trong tiếng nền (Kim: 1/36); tiếng nền kém tự nhiên hơn Kim | Apache-2.0 / MIT | **Đưa vào (tuỳ chọn)**: tự dùng khi người gốc nói > 45s, chỉ tách khúc có lời |
| Dò chữ PP-OCRv6 tiny chạy thẳng onnxruntime | 1.8MB | ~190MB | ~0.13s/khung, video 60s ≈ 7-9s | Bắt chữ vàng không viền trên nền sáng mà heuristic bỏ sót; không báo nhầm ở 4 video không chữ | Apache-2.0 | **Đưa vào (tuỳ chọn)**: `TEXT_DETECT=ocr`; không dùng rapidocr/OpenCV (+225MB) |
| Giọng offline Piper vi_VN vais1000 | 43MB + 82MB | ~200MB | 0.09 | Gemini nghe lại đúng 36/36 từ | Apache-2.0 / MIT, nhưng tinh chỉnh từ giọng lessac (dữ liệu chỉ cho nghiên cứu); espeak-ng GPL-3.0 | **Đưa vào, mặc định tắt** (`TTS_OFFLINE=1`): tầng dự phòng cuối, cân nhắc giấy phép nếu video kiếm tiền |
| ASR offline Zipformer vi int8 | 43MB + 75MB | 280-360MB (khúc ≤ 30s) | 0.03 | Đúng hết trên giọng TTS sạch; chữ HOA, không dấu câu, số viết bằng chữ, mốc theo mảnh BPE | Apache-2.0 | **Không đưa vào**: không dấu câu làm hỏng chia đoạn, số bằng chữ làm hỏng bước kiểm con số; đã có Groq → Gemini → CapCut |
| Nháp JianYing | — | — | — | — | — | **Thay bằng** `timeline.json` + `.srt` (mở được ở mọi phần mềm dựng, pyJianYingDraft không xuất được trên Linux) |
| Worker render trên VPS | — | — | — | — | — | **Chưa làm**: cần máy thứ hai để thử; khi cần thì tách `jobs.worker` ra tiến trình riêng nhận job qua HTTP |

Gói offline cài bằng lệnh console `setup_offline` (~185MB, kiểm SHA-256 từng file, sherpa-onnx ghim < 2 vì bản 2.0
bỏ espeak-ng), gỡ bằng `remove_offline`. Không cài thì bot chạy như trước.

## Bản 40: từ log chạy thật 15/11 (cánh gà chiên nước mắm, muối ớt xanh, lẩu đầu cá hồi)

Đã chạy đúng trên máy thật: video dài gửi Facebook từng khúc (bản 38) qua cả 3 lần, có gắn ảnh bìa, hết lỗi 413.

| Thấy trong log | Nguyên nhân | Sửa |
| --- | --- | --- |
| Job 1, 2 đứng ở «Bóc lời + phân tích hình trên Kaggle: gửi việc (GPU)» 17-20 phút không báo gì, rồi nhảy thẳng sang «đang làm (17 phút)»; việc thật chỉ mất ~3 phút | phiên Kaggle xếp hàng chờ máy. QUEUED là giá trị mặc định (0) của enum `KernelWorkerStatus` nên API bỏ hẳn trường `status` (đã kiểm trong kagglesdk 0.1.38); bot đọc ra chuỗi rỗng, coi như "không đổi" | `kaggle_api.status`: thiếu trạng thái = QUEUED, web hiện «đang chờ máy». `wait` báo lại số phút mỗi 5 phút dù trạng thái không đổi |
| (đọc mã) phiên Kaggle chờ quá giờ hoặc mất mạng chỉ bị bỏ ở phía bot | không gọi huỷ: phiên vẫn nằm trong hàng chờ Kaggle, lát sau tự chạy, tốn giờ GPU và có thể chặn lượt kế của cùng notebook | `kaggle_worker._wait_session`: chờ hỏng là nhờ Kaggle huỷ phiên (dùng cho cả phiên phân tích và phiên dựng) |
| Kaggle tải video nguồn 33-85s (trước khi cài lại: 4-14s) | có thể do chưa đặt `PUBLIC_URL` sau khi cài lại: Kaggle phải tự tải từ Facebook thay vì lấy bản trên Botkeep | cảnh báo một lần trong log khi Kaggle phải tự tải vì thiếu `PUBLIC_URL` |
| `thiếu ['dứa']` ở món cánh gà, ép AI viết lại 2 lần | "cho nó thơm lên", "rất là thơm luôn", "sau khi nó thơm" bị tính là trái thơm | thêm ngữ cảnh tính từ cho "thơm" (trước: nó, là, thơm, phi, rang; sau: luôn, nha, nè, dữ, thiệt, vậy). Vẫn tính dứa ở "cá kho thơm", "bò xào thơm", "200g thơm" (test) |
| `thiếu ['sữa']; bịa ['sả']`, `bịa ['hành phi', 'ớt sừng']` | máy nghe: "sữa mầm", "sữa cây" (sả), "đất sừng" (ớt sừng), "cỏ phi" | bảng sửa lỗi nghe nhầm thêm các cụm này |
| Lời đọc sai nội dung: "3 muỗng đường cùng 2 muỗng đường cà", "5 giá nước lẩu", "màu dừa điều" | AI chép nguyên lỗi nghe nhầm "đường ớt/đường cà" (tương ớt, tương cà), "nồi lỗ/nước lỗ" (lẩu), "dừa điều" | thêm vào bảng: đường ớt/cà → tương ớt/cà, nồi/nước/nấu lỗ → lẩu, đậu cá → đầu cá, gau → rau, nấm mặn → mắm mặn, muối điêu → muối tiêu, (mờ) dừa điều → (màu) dầu điều, sắc lát/khúc/hút → xắt lát/khúc. Không đụng "sữa đặc", "sữa tươi", "đường cát", "cá hồi" (test) |
| Job 2 «Khung đẹp nhất … 6 ứng viên» không có «AI chọn» và không có lỗi nào | AI trả số ảnh ngoài danh sách thì bị bỏ âm thầm | ghi cảnh báo kèm số AI trả, vẫn lấy ảnh điểm cao nhất |

Nhắc lại: chưa đặt `ALLOWED_USERS`/`ADMIN_CHAT_ID` nên các tin «Đã hẹn đăng» chỉ nằm trong log.

## Bản 39: mẫu .env đầy đủ; file .env chịu ghi chú cuối dòng

- `.env.example` viết lại: đủ mọi biến bot đọc (57 cài đặt + 17 biến chỉ đặt qua môi trường), chia 5 mục: bắt buộc,
  tài khoản & key (kèm link lấy key), tự động đăng, tuỳ chỉnh (để nguyên = mặc định), chỉ đặt ở đây. Ghi rõ thứ tự ưu
  tiên (Cài đặt web > panel Botkeep > .env > mặc định) và việc cookies nhiều dòng phải dán ở web. Test giữ mẫu luôn
  khớp mã: thiếu biến hoặc giá trị mặc định lệch là test đỏ.
- `config._load_dotenv`: ghi chú cuối dòng (`CACHE_MAX_MB=500   # trần`) trước đây thành một phần giá trị, `int()`
  lỗi và bot không khởi động được (mẫu cũ có đúng kiểu dòng này, chỉ cần bỏ dấu # đầu dòng là dính). Giờ bỏ ghi chú
  sau khoảng trắng + `#`, nhận giá trị trong `"..."`/`'...'` (mật khẩu có `#` hay dấu cách), `KEY=   # ghi chú` = trống.
- README: chỉ cách dùng `.env` thay cho panel Botkeep.

## Bản 38: từ log chạy thật 13/11 (cháo hàu, gà đốt Ô Thum): Facebook 413 với video dài, báo bịa nguyên liệu giả, lỗi nghe nhầm

| Thấy trong log | Nguyên nhân | Sửa |
| --- | --- | --- |
| `Tự động đăng lỗi: Facebook lỗi HTTP 413` lặp 5 lần trong 2 giờ rồi dừng (video #2 dài 246s) | video thường (> 90s, không phải Reels) gửi cả file trong MỘT request multipart; file lớn bị Facebook từ chối 413 | file nặng hơn `RESUMABLE_MIN` (40MB) đi giao thức gửi từng khúc của Graph API (`upload_phase` start → transfer… → finish, Facebook tự chọn cỡ khúc, mỗi khúc lỗi mạng gửi lại 3 lần); ảnh bìa gắn sau qua `/{video}/thumbnails` như Reels. 413 vào `PAUSE_CODES`: lỗi cố định thì dừng ngay và báo rõ, không thử lại 2 giờ. Test với máy chủ Graph giả (3 khúc 120KB) |
| `bịa/đổi ['sả', 'bột ngọt']`, `['hành phi']`, `['ớt', 'tiêu']` → bắt AI viết lại 2 lần (4 lượt Gemini thừa), cuối cùng "vẫn lệch" | lời gốc máy nghe sai ("hột ngọt", "sữa cay", "muối điêu", "hành trém phi"); AI sửa đúng nhưng bộ kiểm coi là bịa vì so khớp đòi đúng chữ cái đầu | `ingredients._near` so ở dạng lỏng theo từng âm tiết (Levenshtein ≤ 1, cặp phụ âm đầu hay đổi b/h, đ/t…), cụm 2 âm tiết được chen một âm tiết ở giữa ("hành tím phi" có "hành phi"); tên một âm tiết vẫn phải ≥ 4 chữ nên "cua" ≠ "cá", "tỏi" ≠ "tôm" (test) |
| Lời mới chép nguyên lỗi nghe nhầm: "Hành lá sắc nhiễn, ngò ghía sắc nhiễn Và hành trém phi", "đốt ghiêu ghiêu", "cháo hào" | từ điển sửa lỗi chỉ có cụm người dùng khai báo + tên nguyên liệu ≥ 3 âm tiết | `vocab.fix_common`: bảng cụm nghe nhầm hay gặp của giọng miền Tây (hột ngọt → bột ngọt, hạt niêm → hạt nêm, sắc nhiễn → xắt nhuyễn, giải nhiễn → giã nhuyễn, ngò ghía → ngò gai, hành trém → hành tím, ghiêu ghiêu → riu riu, sữa cay → sả cây, đậu hành → đầu hành, cháo/con/nước hào → hàu (giữ "dầu hào"), gà đốt câu thơm/u thông → gà đốt Ô Thum, "niêm" → "nêm" trừ "niêm yết/phong"); áp cho cả đường Kaggle (`from_whisper`) và Groq/Gemini trước khi AI viết, nên lời gốc đưa cho AI đã đúng chính tả |

Không đổi: Gemini 503 lúc chọn ảnh bìa đã tự lấy ảnh điểm cao nhất (đúng thiết kế). Nhắc: bạn chưa đặt
`ALLOWED_USERS`/`ADMIN_CHAT_ID` nên cảnh báo "đã dừng" chỉ nằm trong log, không tới Telegram.

## Bản 37: viết lại giao diện web theo chuẩn semantic / SEO / tiếp cận (skill ui-craft)

Hai trang `static/index.html`, `static/login.html` viết lại toàn bộ phần khung, giữ nguyên mọi id, API và luồng JS
(bộ test web 95 ca + toàn bộ 429 ca vẫn xanh):
- **SEO kỹ thuật**: `<title>` theo mẫu «Trang · Video Bot», `meta description`, `theme-color` sáng/tối, favicon SVG
  (hết lỗi 404 /favicon.ico mỗi lần mở), `lang="vi"`, một `<h1>` + `<h2>` cho từng mục, landmark `header/main/section/
  footer`. Bảng điều khiển là trang riêng nên đặt `meta robots noindex,nofollow` và header `X-Robots-Tag` cho mọi
  phản hồi: máy tìm kiếm không bao giờ lập chỉ mục trang đăng nhập hay bảng điều khiển.
- **Tiếp cận (WCAG AA)**: mọi ô nhập có `<label for>` (kể cả ô cài đặt sinh động), nút «Hủy / Duyệt / Copy / Đăng xuất»
  là `<button>` thật thay cho `<a href="#">`, nút Xoá có `aria-label` kèm tên mục, vùng thông báo `role=status
  aria-live`, skip link, `:focus-visible` rõ, bảng có `caption` + `th scope`, ảnh có `alt` + kích thước (không nhảy
  bố cục). Bảng màu đo lại: mọi cặp chữ/nền ≥ 4.5:1 ở cả sáng và tối (nút cam trước đây 3.6:1, nút xám 2.5:1).
- **Form**: `type/inputmode=url`, `autocomplete=off`, `spellcheck=false` cho link và key; `enterkeyhint` ở đăng nhập;
  cỡ chữ ô nhập 16px (iOS không tự phóng to); ô nhập và nút cao ≥ 44px; nhắc trước khi rời trang nếu còn cài đặt
  chưa lưu; hỏi xác nhận trước khi xoá kênh/link/nhạc hoặc hủy job; dấu «…» thay «...».
- **Điện thoại**: dưới 640px bảng hàng đợi đổi thành từng khối (trước đây 4 cột ép vào 390px làm vỡ chữ «Cop y con
  tent»); tôn trọng `prefers-reduced-motion` khi cuộn tới màn duyệt; `safe-area-inset`; không cuộn ngang.
- Kiểm chứng: bộ dò Impeccable 0 cảnh báo (bản cũ 8), kịch bản Playwright tự kiểm nhãn/heading/landmark/kích thước
  nút/tràn ngang ở 390·768·1440px, sáng và tối, kèm thao tác thật (đăng nhập, đổi chế độ, dán link, mở duyệt kịch bản).

## Bản 36: kênh nguồn + link dán tay ngay trong mục Tự động đăng; Cài đặt gom nhóm dễ hiểu

**🤖 Tự động đăng** trên web (`static/index.html`, `autopost.py`, `web_panel.py`):
- **Kênh nguồn**: danh sách kênh Facebook/TikTok đang dùng (nút Xoá từng kênh) + ô dán link kênh «Thêm kênh» — bao nhiêu
  kênh cũng được, bot tự nhận ra nền tảng theo link (`autopost.add_source/remove_source`, lưu vào `AUTO_SOURCES` /
  `AUTO_TIKTOK_SOURCES` như cũ). API: `POST /api/autopost/source {url[, delete]}`.
- **Link dán tay**: ô «Xếp vào lượt kế tiếp» nhận link MỘT video (TikTok / Facebook / YouTube): bot xem thử ngay (tiêu đề,
  độ dài ≤ 5 phút, không trùng video đã làm, không trùng trong hàng chờ) rồi xếp vào hàng chờ riêng (`autopost.json`
  mục `manual`, có nút Xoá). Lượt lấy video kế tiếp (tự động hoặc «Lấy video ngay») làm link này TRƯỚC video chọn ngẫu
  nhiên; xong thì hẹn đăng vào khung giờ vàng còn trống, hết khung thì đăng ngay. Có link dán thì không bắt buộc có kênh
  nguồn. Link hỏng lúc tới lượt: báo admin và bỏ qua, không kẹt. API: `POST /api/autopost/queue {url[, delete]}`;
  `GET /api/autopost` trả thêm `sources` và `manual`.
- Dòng trạng thái có thêm «Link bạn dán đang chờ (n)».

**⚙️ Cài đặt** (`config.META`, `config.GROUPS`, `describe_settings`, `/api/settings` trả `{fields, groups}`):
- Gom 7 nhóm: 🔑 Tài khoản & API (mở sẵn), 🗣️ Giọng đọc, 🎬 Video & lời, ☁️ Kaggle, 🤖 Tự động đăng, 💬 Telegram,
  🔧 Nâng cao (thu gọn, bấm để mở).
- Mỗi mục có nhãn tiếng Việt, một câu «dùng để làm gì», mức cần (bắt buộc / nên có / chỉ khi cần), trạng thái ✅ đã điền
  hay chưa, link «Lấy ở đây ↗» tới trang tạo key (AI Studio, Kaggle, Groq, OpenRouter, Graph API Explorer); nhóm API
  báo «thiếu n mục bắt buộc» khi chưa có key Gemini. Tên khoá kỹ thuật vẫn hiện nhỏ bên cạnh để tra tài liệu.

Kiểm tra: `ruff` sạch; 429 test xanh + 5 bỏ qua; giao diện chụp thật bằng Playwright (khổ điện thoại, nền tối).

## Bản 35: rà soát production (kiến trúc, bảo mật, độ tin cậy) + vá khẩn

Tài liệu đầy đủ 4 phần: `docs/DANH_GIA_KIEN_TRUC_PRODUCTION.md` (kiến trúc & luồng dữ liệu, bảo mật & độ tin cậy,
đề xuất tái cấu trúc Clean Architecture, kế hoạch + code mẫu). Code mẫu chuẩn production ở `samples/rmvideo/`
(tiến trình con có timeout + giết cả nhóm; cài đặt có kiểu; bộ định tuyến AI) có test riêng, chưa nối vào bot.

Vá ngay trong bản này (mỗi mục có test hồi quy trong `tests/test_review35.py`):

| Rủi ro | Sửa |
| --- | --- |
| Trần body web 305MB áp cho cả `/api/login` (đường công khai): vài request lớn song song là OOM máy 2GB | trần JSON 1MB; login từ chối body > 4KB trước khi đọc; upload vẫn đi multipart đọc từng khúc |
| `/api/logo`, `/api/voice_ref` gom cả phần vào RAM rồi mới kiểm cỡ | đọc từng khúc, vượt trần dừng ngay |
| Gửi 2 file video trong một request: file đầu bị bỏ rơi trong `uploads_in/` tới khi đầy ổ | phần thứ hai → 400, file đầu được xoá |
| Đăng xuất không thu hồi được phiên (cookie sao chép dùng được 30 ngày) | mốc thu hồi `.session_key.epoch`: đăng xuất là mọi cookie cũ hết hiệu lực |
| Huỷ vì quá hạn / bot tắt không dừng phiên Kaggle; cờ dừng dùng chung giữa các lượt; API huỷ chạy đồng bộ trong handler (treo web/Telegram tới 120s) | `abort()` ở cả 3 đường huỷ, cờ riêng từng lượt, gọi huỷ trong luồng riêng |
| Logo > ~530KB làm notebook Kaggle quá 1MB → mọi job Kaggle âm thầm đổ về Botkeep | trần 400KB khi tải lên (báo lý do); logo quá cỡ bị bỏ kèm cảnh báo thay vì hỏng cả việc |
| `stt_cache.json` / `speed.json` ghi không atomic, hỏng là tắt cache mãi / ETA kẹt 6.0 | `cache.read_json_file/write_json_file` tự chữa |
| `/tmp/tmpXXXX` sót sau OOM/SIGKILL không ai dọn; `clean` xoá thư mục job đang chạy; tiếng nền đã tách nằm 2 bản | tiền tố `rmv_` + dọn lúc khởi động; `clean` giữ `work/` và cache khi có job; move vào cache |
| `settings.json.tmp` 0644 trong khoảnh khắc trước chmod | tạo đã là 0600 + fsync |
| SSRF: NAT64 `64:ff9b::/96` coi là công cộng; link có tab/xuống dòng | bóc IPv4 bên trong; chặn ký tự điều khiển |
| Liệt kê kênh TikTok là lời gọi yt-dlp duy nhất không tắt extractor generic, không dùng proxy | đặt `allowed_extractors` + proxy |
| Lỗi 500 với id không phải số, body không phải JSON, POST không multipart; thiếu header bảo mật; bảng đếm sai mật khẩu phình | 404/400 rõ ràng; `X-Frame-Options`, `nosniff`, `Referrer-Policy`; dọn bảng; cảnh báo mật khẩu < 12 ký tự |

Chưa vá, cần bạn quyết (xem Phần 2/4 tài liệu): `YTDLP_PROXY`/`PUBLIC_URL`/token Kaggle sửa được từ web (đề xuất
chuyển sang biến môi trường); ghim phiên bản + hash cho gói cài qua console và trên Kaggle; dừng yt-dlp khi huỷ job.

Kiểm tra: `ruff` sạch; 423 test xanh + 5 bỏ qua (cần key/model).

## Bản 34: thử model offline thay Groq/Gemini trên Kaggle

**Bóc lời (thay Groq):** từ bản 27 đã bóc lời offline trên Kaggle bằng faster-whisper large-v3; Groq chỉ còn là dự phòng.
Thử thêm **PhoWhisper-large** (VinAI, Whisper tinh chỉnh tiếng Việt), cùng một file giọng đã tách của video cá kèo
(notebook `rmvideo-phowhisper`, chuyển sang CTranslate2 fp16 2.9GB, giữ ở đầu ra notebook):

| Model | Thời gian | Kết quả trên video cá kèo 220s |
| --- | --- | --- |
| Whisper large-v3 (hiện tại) | 52s | 64 câu, 842 chữ, phủ 152s, có dấu câu; sai vài chữ ("lắc xê", "gây mơ", "rau găm") |
| PhoWhisper-large | 27s | 7 đoạn khổng lồ (35-73s, 73-108s...) KHÔNG có dấu câu; **bỏ hẳn quãng 9.7-35.5s** (lắc xê, bỏ đá, cho nước, ngâm 30 phút); bịa câu "đó là lý do vì sao báo chí lại tồn tại" ở cuối; sai chữ khác ("cá kẹo chua giòn", "trái kẹo", "rọi dầu") |

Kết luận: giữ large-v3 mặc định. Tuỳ chọn `KAGGLE_ASR=phowhisper` vẫn có (chỉ video tiếng Việt; thiếu notebook
model thì tự về large-v3) để thử với kênh khác.

**Viết lời (thay Gemini):** notebook `rmvideo-lab-llm` chạy model VLM mở trên Kaggle (2×T4, nén 4-bit nf4) với ĐÚNG
prompt + ảnh từng đoạn của bot cho video cá kèo (44 đoạn, lô 15 đoạn vì lô 28 đoạn kèm ảnh hết VRAM), chấm bằng bộ
kiểm của bot và so với lời Gemini trong log bản 32:

| Model | Chép lời gốc TB / đoạn chép ≥45% | Đúng khoảng chữ | Bịa/thiếu nguyên liệu, số | Thời gian | Nhận xét khi đọc |
| --- | --- | --- | --- | --- | --- |
| Gemini 3.5 flash-lite | 9% / 1 | 19/44 (20 đoạn viết dư, bước rút gọn xử lý) | bịa "1 phút" | ~15s cả bài | tự nhiên, đúng việc đang làm |
| Qwen3-VL 8B | 25% / 9 | 12/44 | sạch | nạp 141s + 93s/lô ≈ 7 phút GPU | câu ngắn, hay nói việc của đoạn SAU ("Thả cá vào chảo" ở đoạn bắt chảo dầu), chép lỗi bóc lời ("giàn rồi", "dắt ra", "gãy da"), 1 đoạn trống |
| Qwen3-VL 4B | 43% / 22 | 15/44 | bịa "nước mắm", thiếu "10 giây" | nạp 58s + 88s/lô | gần như chép lời gốc kể cả lỗi ("gây mơ", "rất là miền", "quét cục cục") |
| Gemma 3 12B | — | — | — | — | kho model bị khoá (cần chấp nhận giấy phép + token HF) |

Kết luận: model mở ≤ 8B chạy được trên Kaggle nhưng kém Gemini rõ (chép lời, lệch đoạn, giữ lỗi bóc lời); lớp 30B
cần nhiều VRAM hơn 2×T4 với cách nén hiện tại. **Giữ Gemini (OpenRouter dự phòng)**, không nối model offline vào bot.
Sửa thêm: `kaggle_api.wait` chịu được mạng chập chờn lúc hỏi trạng thái (một lần "Connection reset" trước đây làm
cả job Kaggle bị coi là lỗi).

## Bản 33: dùng lại tiếng nền khi chạy lại video cũ; lượt tách 2 chỉ khi GPU; bớt báo động giả

Log bản 32 (chạy lại link cá kèo): phân tích lấy từ cache nên không có mã tiếng nền -> phiên dựng tự tách lại trên
CPU Kaggle, 2 lượt, 418s (job 15.6 phút thay vì 12). AI bị bắt viết lại 2 lượt vì "rau răm" bị coi là bịa (lời gốc
máy nghe ra "rau găm") rồi bỏ cuộc, chép luôn lỗi "rau găm" vào lời đọc.

| Sửa | Cách làm |
| --- | --- |
| Phân tích từ cache vẫn mang mã tiếng nền | Phiên dựng gắn đầu ra notebook phân tích, khớp mã thì dùng lại (đã bị video khác ghi đè thì mã lệch, tự tách) |
| Phiên dựng Kaggle thử GPU cho tách giọng | Không có CUDA thì tự về CPU như cũ |
| Lượt tách thứ 2 chỉ khi GPU thật sự chạy | Trên CPU tốn gấp đôi mà mặt nạ phổ đã lọc được phần lớn giọng sót |
| AI sửa lỗi bóc lời không phải bịa | Tên nguyên liệu trong lời mới gần giống cụm trong lời gốc (sai một chữ cái: "rau găm" = "rau răm", "nước mắn" = "nước mắm") -> không báo bịa; đổi thật ("thịt gà" -> "thịt bò") vẫn bắt |
| Số liệu | "nửa tiếng" = "30 phút"; "một lát là / sau / nữa" là thời gian ngắn, không phải "1 lát" nguyên liệu |

## Bản 32: giọng gốc còn sót nhỏ trong tiếng nền đã tách

Video cá kèo bản 31 (13/11/2026): nghe còn lẫn giọng gốc nhỏ ở vài đoạn. Đo (tách giọng của video ra, so từng cửa sổ
250ms với giọng gốc): giọng gốc còn trong tiếng nền ở mức ~-25 dB so với giọng gốc (71% số cửa sổ có lời); nghe rõ
nhất ở 206-212s vì ở đó giọng mới đang im (khe giữa hai câu) và tiếng nền rất nhỏ (-40 dB) nên tiếng thì thầm lộ ra.

| Thử | Giọng gốc còn trong nền (trung vị / p90) | Phần giọng lượt 1 bỏ sót hẳn còn lại | Tiếng xèo >3.5kHz lúc có lời |
| --- | --- | --- | --- |
| Hiện tại (Kim_Vocal_2 một lượt) | -25 / -24 dB | 100% | — |
| Tách thêm lượt 2 | -41 / -26 dB | 3% (p90 38%) | -0.4 dB |
| **Mặt nạ phổ** (mới) | **-52 / -46 dB** | **6% (p90 12%)** | -2.5 dB |
| Lượt 2 + mặt nạ | -64 / -45 dB | 1% (p90 6%) | -2.9 dB |

Sửa (`vocal_sep.suppress_residual`): sau khi tách, lọc tiếng nền bằng mặt nạ phổ Wiener dựa trên chính giọng đã tách
(ô tần số-thời gian nào giọng trội hơn nền thì hạ nền ở ô đó, tối đa 26 dB; ô không có giọng giữ nguyên), xử lý từng
khối 20s (Botkeep 2GB RAM). Áp cho mọi lần tách (Kaggle lẫn Botkeep). Trên Kaggle (GPU rẻ) tách thêm lượt 2
(`SEP_PASSES=2`). Tắt mặt nạ: `SEP_MASK=0`. Kiểm: test mặt nạ với nhiễu + giọng giả; chạy mã mới trên nguồn cá kèo
(số đo ở dòng "Mặt nạ phổ").

## Bản 31: rà toàn bộ repo (13/11/2026) — sửa 30 lỗi ảnh hưởng video ra

Rà 4 mảng song song (dựng hình/tiếng, luồng xử lý, lời thoại, Kaggle/ảnh bìa/tự đăng), mỗi lỗi đều tái hiện bằng
dữ liệu cụ thể trước khi sửa; đo thêm video thật bản 29 (cá kèo): không lọt giọng gốc (0/180 giây có lời), -14.1 LUFS,
H.264 High 4.1 / bt709 / moov đầu file / khung khoá 2s. Test mới: `tests/test_review31.py`.

| Phần | Lỗi (kịch bản tái hiện) | Sửa |
| --- | --- | --- |
| Tự động đăng | Mô tả bài Fanpage là NGUYÊN cả gói nội dung 3 nền tảng: có «📌», «▶ TikTok:», «▶ YouTube Shorts:», «#Shorts», caption lặp 3 lần | Chỉ lấy phần «▶ Facebook» |
| Dựng | Dải chữ gốc sát mép trên/dưới (vd phụ đề in ở y=0) -> dải che < 80px, boxblur từ chối bán kính -> **cả lần dựng lỗi** (QUALITY fast/balanced) | Trượt cửa sổ 96px vào trong khung; bán kính mờ ≤ h/4 |
| Cắt phần | ✂️ Cắt 90s không có Kaggle: phần dài 90-112s bị cắt còn 90s, 20s cuối mất không báo | Phần cắt là remake trọn phần (long_mode full) |
| Lời thoại | "cá này mình mua ở chợ về đó" + "chiên vàng là xong rồi" bị coi là quảng cáo cuối -> cắt mất bước cuối | Chỉ cắt khi có dấu hiệu MẠNH (giỏ hàng, link, mã giảm, shop...); "mua ở", "giá rất" là dấu hiệu yếu |
| Lời thoại | Câu gốc không dấu "các bạn nhớ đăng ký kênh ủng hộ mình nha rồi cho 2 muỗng đường vào" bị bỏ cả câu -> mất bước 2 muỗng đường | Tách theo từ nối (nha / rồi / và / sau đó), chỉ bỏ mẩu kêu gọi; lời gốc đưa AI đã bỏ sẵn vế kêu gọi |
| Lời thoại | Whisper: câu thật chỉ KẾT bằng "nhớ đăng ký kênh" bị coi là bịa -> bỏ cả câu (cả bước làm trong đó) | Chỉ coi là bịa khi lời kêu gọi chiếm gần hết câu |
| Lời thoại | "Theo dõi để thịt không bị cháy" bị coi là kêu gọi theo dõi | "theo dõi để" chỉ tính khi để xem/nhận/không bỏ lỡ... |
| Lời thoại | AI viết "Các bạn mua cá kèo về rửa sạch..." / "Bí quyết độc quyền giúp cá giòn" bị coi là bán hàng -> 2 lượt viết lại vô ích | "mua" phải đi với ngay/ủng hộ/sản phẩm/shop...; "độc quyền của em/shop" |
| Lời thoại | Khoảng số chữ tính theo chữ VIẾT: câu "100g đường, 50ml nước mắm, 2 muỗng... 15 phút" được giao 14-17 chữ nhưng đọc ra đã 21 chữ phải giữ nguyên | Tính theo chữ ĐỌC RA (spoken_words) |
| Lời thoại | Có đoạn mở đầu: câu hook được giao số chữ của 5.5s (12-15 chữ) dù chỉ có 3.4s -> tràn | Theo đúng khe 3.4s (7-9 chữ) |
| Lời thoại | "Tiến hành ướp" báo bịa hành lá; "hột gà" thay "trứng" báo bịa trứng gà; "cá nhân", "ăn với cơm nóng", "đợi một lát", "cắn một miếng", "1 phần 3" ↔ "1/3" đều báo thiếu/bịa -> viết lại 2 lượt với chỉ dẫn sai | Thêm ngữ cảnh loại trừ; "trứng" thuộc nhóm trứng gà; bỏ "một lát/miếng/xíu" sau đợi/chờ/cắn/ăn/thử; "1 phần 3" = "1/3" |
| Lời thoại | "Cho một muỗng đường" -> "Cho một muỗng đường nữa" (bước tiếp) bị bỏ như lặp vòng | Câu NỐI DÀI câu trước không phải lặp |
| Lời thoại | "chợ Bà Chiểu" (Groq, từng chữ): chữ hoa tên riêng bị coi là câu mới -> cắt giữa cụm | Chữ hoa mà chữ kế cũng hoa thì không phải câu mới |
| Nội dung đăng | TikTok: caption < 110 ký tự vẫn bị cắt mất chữ cuối khi hashtag dài; video 4-7s khoảng số chữ ngược (15-9) | Chỉ cắt caption khi > 110; kẹp khoảng |
| Kaggle | Mốc cắt lẻ (quảng cáo cuối 40.32s, đoạn mở đầu 74.35s) bị int() trên Kaggle -> video ngắn hơn tới 0.99s, chữ cuối cụt | Giữ số lẻ |
| Kaggle | Kết quả không có mã việc: có thể nhận nhầm video của phiên trước (phân tích đã có, dựng chưa) | Mỗi việc có mã riêng, Botkeep đối chiếu |
| Kaggle | Các phần video dài chạy chung một phiên: video về thư mục gốc, ảnh bìa nằm ở thư mục phần -> không giao ảnh bìa | Đưa video về thư mục phần trước khi giao |
| Đoạn mở đầu | Đẩy cận neo góc trái trên (crop x=y=0 tính một lần): hình TRÔI xuống góc phải thay vì zoom vào món | crop tâm theo từng khung |
| Đoạn mở đầu | So khung nới 1s lấn sang cảnh kế (cũng có trong phần mở đầu) -> báo "gốc đã chiếu trước" nhầm | So đúng khúc [a, a+3s]; phần mở đầu chỉ tới trước a |
| Đoạn mở đầu | Video full 299s + 3s = 302s > HARD_MAX 300: lời/phụ đề lên lịch tới 302 nhưng hình cắt ở 300 | Tổng ≤ giới hạn |
| Nguồn | Video KHÔNG có luồng tiếng (quay màn hình, xuất -an): ffmpeg lỗi ở bước bóc lời -> hỏng job | Không bóc lời, đi đường nhạc nền; extract_audio xuất im lặng; ✨ đoạn hay cũng vậy |
| Nguồn | Video 31-45 fps (quay màn hình VFR) bị chia đôi còn 15-22 fps giật | Giữ nguyên |
| Nguồn | Điện thoại quay dọc mã hoá 1920x1080 + thẻ xoay 90°: coi là 1080px cao -> ra 720p | Đọc thẻ xoay, đổi chiều |
| Video không lời | AI viết bài đăng lỗi (mẫu "Video hot hôm nay") vẫn tự đăng; ghi "🔊 Giọng đọc AI" dù không có giọng | Không tự đăng; không ghi |
| Dự phòng | Đường đọc một mạch (AI lỗi) dùng độ dài/lời TRƯỚC khi cắt quảng cáo / thêm mở đầu | Theo độ dài sẽ dựng |
| Worker | Lỗi mạng TimeoutError (tải file Telegram) bị báo nhầm là "Quá thời gian cho phép" | Lớp lỗi riêng cho ngân sách thời gian |

Đã kiểm tra đúng (không sửa): mọi mốc thời gian qua kéo chậm × đoạn mở đầu × cắt quảng cáo; bản duyệt dựng lại y hệt;
gom lô Kaggle; tách giọng (ranh giới khúc, độ dài); cache (khoá gồm mọi đầu vào); loudness 2 lượt; phụ đề karaoke;
tương thích ffmpeg 5.1.

## Bản 30: không chiếu trước cảnh món khi video gốc đã tự mở đầu bằng món

Video cá kèo bạn gửi (13/11/2026): đoạn mở đầu lặp y hệt đầu video. Video gốc tự mở đầu bằng ~10 giây "xem trước"
(chiên → trút ra dĩa → dĩa cá kèo với rau → chén mắm me), dùng lại đúng các cảnh cuối video; AI chỉ được xem MỘT khung ở
giây 0.5 (đang chiên trong chảo) nên trả lời "video gốc chưa mở đầu bằng món" -> bot chiếu thêm 3 giây dĩa cá (giây
209.5) ngay trước phần xem trước có sẵn.

| Sửa | Cách làm | Kiểm tra |
| --- | --- | --- |
| AI xem cả phần mở đầu | Gửi 4 khung trải trên phần mở đầu video gốc (15% độ dài, 3-12 giây; video 220s: giây 0.5 / 4.2 / 7.8 / 11.5) thay vì 1 khung; hỏi có khung nào đã cho thấy món xong chưa, kể cả thoáng qua hay khác góc máy | test |
| So khung, không cần AI | So cảnh sẽ chiếu trước với từng khung trong phần mở đầu (tương quan trên ảnh màu thu nhỏ): giống ≥ 0.9 = video gốc dùng lại đúng cảnh đó -> bỏ đoạn mở đầu | video thật: cá kèo giây 3.5 giống cảnh 209.5s **0.97**; mì xào bò giây 8 giống cảnh cuối **0.98** (cũng mở đầu bằng 9.5s xem trước món); cảnh giữa video chỉ 0.24-0.27. Chạy lại đúng câu trả lời sai của AI trong log: cả hai video đều bỏ đoạn mở đầu |
| Khung đẹp nhất nằm trong phần mở đầu | Bỏ đoạn mở đầu (trước đây chỉ xét 4 giây đầu) | test |

Video bị bỏ đoạn mở đầu vẫn có giọng ngay đầu: câu hook đọc ở giây 0 trên phần xem trước có sẵn của video gốc (như bản 27).

## Bản 29: mở đầu bằng món thành phẩm + câu hook có giọng, ảnh bìa AI chọn cho Reels

| Phần | Cách làm | Kiểm tra |
| --- | --- | --- |
| Chọn khoảnh khắc | Một lượt AI (Gemini) chọn ảnh bìa trong 6 khung đẹp nhất, gửi kèm khung mở đầu video gốc để AI cho biết thêm: ảnh chọn có phải MÓN ĐÃ XONG không, video gốc đã tự mở đầu bằng món chưa. Ảnh bìa và đoạn mở đầu dùng chung khoảnh khắc đó (ảnh trên bảng tin = cảnh đầu video), không tốn thêm lượt AI | test |
| Đoạn mở đầu 3 giây (`TEASER=1`) | Chỉ video nấu ăn có lời. Lấy 3 giây trong cùng cảnh với khung AI chọn (không vắt qua chuyển cảnh: dò chuyển cảnh riêng quanh khung đó, vài giây video); cảnh ngắn hơn 3 giây thì đứng hình. Cùng chuỗi màu với video, đẩy cận nhẹ, vào thẳng cảnh món (không mờ từ đen: khung đầu hay được lấy làm ảnh bìa). Dựng trong CÙNG lần mã hoá (đầu vào tua sẵn riêng, không thêm một lượt mã hoá cả video) | dựng thật bằng FFmpeg: khung đầu đúng cảnh chọn, sau 3 giây vào video từ đầu, dài thêm đúng 3 giây (cả khi kéo chậm video) |
| Tiếng | Dưới câu hook là tiếng xèo/giòn của chính cảnh món (bản đã tách giọng gốc); chưa tách giọng thì đoạn mở đầu chỉ có giọng hook (tiếng gốc cảnh đó có thể còn lời người gốc). Mọi mốc (giọng, phụ đề, cổng tiếng nền, đoạn giữ tiếng gốc) lùi 3 giây | đo: có tiếng xèo -38 dB / không tách giọng -64 dB; ra -14 LUFS |
| Câu hook | AI viết câu hook cho đúng cảnh món đang thấy (giòn, thơm, màu…), đọc vừa 3 giây ngay giây 0, không kể bước làm; chữ hook to hiện ngay khung đầu tới hết đoạn mở đầu; câu hook không hiện thêm phụ đề karaoke | test |
| Không làm khi | AI không thấy món đã xong rõ ràng, video gốc đã mở đầu bằng món (hoặc khung đẹp nhất nằm trong 4 giây đầu), không có key Gemini, video không lời, chế độ review / nhạc / ảnh, phần cắt từ video dài, `TEASER=0`. Có ghi chú lý do | test |
| Độ dài | Video ≤ 90s: thêm mở đầu cũng không vượt 90s (bớt phần cuối) để vẫn đăng Reels; chọn 30/60/90s thì giữ đúng độ dài | test |
| Duyệt kịch bản | Bản duyệt dùng lại đúng đoạn mở đầu đã chọn (không hỏi AI lại). Sửa luôn lỗi cũ: bản duyệt bị mất chữ hook (lấy tiêu đề bài thay vào) | test |
| Kaggle | Đoạn mở đầu đi kèm việc dựng trên Kaggle (cùng hàm dựng) | test |
| Ảnh bìa Reels | Trước đây Reels (≤ 90s, phần lớn video tự đăng) KHÔNG được gửi ảnh bìa — API đăng Reels không có chỗ gửi. Giờ đăng xong thử gắn ảnh bìa AI chọn (`/{video-id}/thumbnails`); Facebook không nhận thì bỏ qua, bài vẫn giữ | test với Graph API giả; Facebook thật có nhận với Reels không phải thử trên Page |

## Bản 28: video không lời chèn nhạc nền, thêm nguồn TikTok cho Tự động đăng

| Phần | Cách làm | Kiểm tra |
| --- | --- | --- |
| Video không lời | Bóc lời ra dưới 6 chữ (không ai nói, hoặc chỉ "ừm", "ngon quá") hoặc chỉ một câu (trước đây AI viết cả bài đọc một mạch từ đúng câu đó) -> **chèn nhạc nền, KHÔNG để AI xem hình rồi bịa lời** (dễ đoán sai món, sai nguyên liệu, bịa bước). Vẫn dựng như remake: tiêu đề + chữ hook, logo, che chữ gốc, ảnh bìa, màu. Bài đăng (tiêu đề, caption, hashtag, hook) chỉ viết từ tiêu đề + mô tả gốc, dặn AI không thêm chi tiết; không có tiêu đề lẫn mô tả thì dùng mẫu. Có người nói thì vẫn viết lại lời như cũ | test cả luồng: không gọi AI viết lời, không đọc giọng |
| Thư viện nhạc nền | Web → ô **🎵 Nhạc nền cho video không lời**: dán link (TikTok / YouTube / mp3) hoặc tải file lên (tối đa 40 bài, 15MB/bài, kiểm tra đọc được nhạc). Mỗi video chọn ngẫu nhiên một bài, không lặp 3 bài vừa dùng; lấy đoạn hay nhất (điệp khúc) của bài; nhạc vào 1s, ra 2s, ngắn thì lặp; bài tải lỗi thì lấy bài khác | test: xoay vòng 4 bài không lặp trong 4 lượt; file hỏng bị từ chối |
| Tiếng gốc (`BGM_ORIG`) | **off** (mặc định): chỉ nhạc mới — video không lời thường đã có nhạc riêng, giữ lại thì hai bài chồng nhau; **low**: giữ tiếng gốc nhỏ hơn ~12 dB (video ASMR: tiếng xèo, tiếng dao); **keep**: tiếng gốc là chính, nhạc nằm dưới và tự nhỏ lại khi tiếng gốc to | đo bằng ffmpeg: off còn tiếng gốc -72 dB, low thấp hơn keep 12 dB, ra đúng -14 LUFS |
| Thư viện trống | Giữ nguyên tiếng gốc, không thêm lời, ghi chú cách thêm nhạc; Tự động đăng KHÔNG đăng video đó (gần như đăng lại nguyên bản) | test |
| Kaggle | Việc mới `bed`: dựng video + nhạc nền trên Kaggle **CPU** (không đọc giọng, không tách giọng, không tốn giờ GPU); phần của video dài chạy chung một phiên với các phần khác; Kaggle lỗi thì dựng trên Botkeep. Không có `PUBLIC_URL` thì chỉ chọn nhạc dạng link (Kaggle tự tải được) | test với Kaggle giả + chạy thật việc `bed` cục bộ |
| Nguồn TikTok | `AUTO_TIKTOK_SOURCES` (@tên hoặc tiktok.com/@tên): yt-dlp đọc ~30 video mới nhất của kênh (đo thật: có id, link, chú thích, độ dài). Bỏ video không phải của chủ kênh, quá dài/ngắn như nguồn Facebook | test |
| Ô tick nguồn | Ô **🤖 Tự động đăng** trên web: ☑ Facebook ☑ TikTok (`AUTO_PLATFORMS`). Tick cả hai thì **xen kẽ**: nền tảng được lấy ít hơn trong 6 video gần nhất đi trước; nền tảng hết video mới thì lấy bên kia. Không cho bỏ tick cả hai | test |
| Chống trùng | Ngoài danh sách video đã dùng, video mới được so với MỌI video đã làm: độ dài lệch ≤ 1.5s và chú thích giống ≥ 60% (bỏ hashtag/emoji) = cùng một video người gốc đăng ở cả Facebook lẫn TikTok (hoặc đăng lại) -> bỏ qua. Thiếu chú thích thì không đoán | test |
| Cài đặt mới | `NO_SPEECH` (music / narrate = cách cũ), `BGM_LINKS`, `BGM_ORIG`, `AUTO_TIKTOK_SOURCES`, `AUTO_PLATFORMS` | |

## Bản 27: bóc lời + phân tích hình trên Kaggle, câu hook, bỏ lời kêu gọi đăng ký

Chạy thật trên Kaggle T4 với video cá kèo 220s (13/11/2026):

| Phần | Cách làm | Đo thật |
| --- | --- | --- |
| Bóc lời | Notebook riêng `rmvideo-analyze`: tách giọng gốc khỏi nhạc trên GPU (onnxruntime-gpu, nạp CUDA qua `preload_dlls`) -> **Whisper large-v3 nghe giọng đã tách** (faster-whisper; tự giải mã âm thanh vì PyAV trên Kaggle lệch phiên bản; nạp sẵn libcublas/libcudnn của các gói nvidia-*; GPU lỗi thì chạy CPU). Botkeep lọc câu bịa/chào kết/lặp vòng, chia đoạn THEO CÂU CỦA WHISPER (Whisper trên Kaggle hay bỏ dấu câu ở khung sau: ghép từng chữ thì 8 chỗ cắt giữa câu, theo câu còn 2), câu > 9s tách ở quãng nghỉ lớn nhất | 94 câu, 840 chữ; bóc được 0-29s ("mẹo làm cá kèo chiên giòn… không nhảy ra", "bỏ đá vào", "nghỉ ngơi 30 phút") và 52-83s mà Groq bỏ sót. Tách giọng GPU 34s (CPU 209s), Whisper 55s gồm tải model 3GB |
| Phân tích hình | Cùng phiên, CPU song song: điểm chuyển cảnh + chữ in cứng + ảnh 2 khung/giây ở mức Kỹ; ảnh gửi về Botkeep cho AI nhìn và chấm ảnh bìa | 59s (Botkeep: ~380s) |
| Dựng | Notebook `rmvideo-worker` gắn đầu ra của `rmvideo-analyze` (kernelDataSources, đo thật: gắn ở `/kaggle/input/notebooks/<tên>/<slug>/`) -> dùng lại tiếng nền đã tách nếu khớp mã job | 6.4 phút, tách giọng 0s |
| Cả phiên phân tích | Hàng chờ + cài thư viện + tải model + chạy | 3.6-3.9 phút. Ước tính cả job video này ~12 phút (bản 25: 19.3 phút) |
| Câu hook | Đoạn 0 luôn là câu hook (luật riêng, chỉ ở lô đầu); AI chào hỏi ("Xin chào cả nhà...") thì bị bắt viết lại; lời gốc bắt đầu sau 2s thì có câu hook riêng ở 0s; AI trả 3-7 chữ IN HOA in màn hình 3 giây đầu + ảnh bìa, ngắt dòng cân nhau | test |
| Không kêu gọi đăng ký | Câu gốc chỉ là lời kêu gọi đăng ký/theo dõi -> bỏ trước khi đưa AI; AI viết -> bắt viết lại; còn sót -> lọc từng vế trước khi đọc (cả lời video không lời, rút gọn, kịch bản liền); màn kết bỏ dòng "Theo dõi để xem thêm"; "theo dõi nhiệt độ/lửa" không bị tính | test |
| Ngắt câu | Câu kết bằng hư từ ("là", "đã", "thì"...) là câu chưa xong (cần khi không có dấu câu); chữ HOA giữa chừng = câu mới | test, chạy trên lời thật |
| Cài đặt mới | `KAGGLE_ANALYZE` (1 = bóc lời + phân tích trên Kaggle) | |

Phần cắt từ video dài (✂️ / ✨) vẫn bóc lời + phân tích trên Botkeep (các phần đã gom chung một phiên dựng Kaggle).

## Video thật bản 25 (bản 26): 30 giây đầu không có giọng, phân tích hình lâu

Đo trên video cá kèo 220s bạn gửi: tách giọng cả video gốc và video ra, so từng giây.

| Hiện tượng | Nguyên nhân (đo được) | Sửa |
| --- | --- | --- |
| 0-29s video ra không có giọng mới (giọng gốc đã tách sạch, còn -45 dB) | Video gốc 0-29s người gốc NÓI liên tục: âm tiết 60-140ms, ít nốt giữ (giống hệt phần có lời, không phải bài hát); giọng hơn nhạc 10-18 dB, không tệ hơn phần bóc được. Whisper turbo lượt đầu bịa "Các bạn hãy đăng ký kênh để ủng hộ kênh", lượt nghe lại CÙNG model chỉ ra "Cá vào gãy là". 52-82s cũng vậy (8 chữ cho 30s lời) | Nghe lại bằng `whisper-large-v3` (bản đầy đủ) + khoá ngôn ngữ; vẫn < 0.4 chữ/giây thì nhờ Gemini nghe quãng đó, lấy bản nhiều lời hơn. Lời gốc bắt đầu sau 3s thì AI viết câu mở đầu ở 0s |
| Whisper lặp "Các cá kèo sẽ giòn lâu từ trong ra ngoài." 4 lần (84-100s), "Các bạn có thể nhìn thấy cá kèo..." 2 lần | Whisper lặp vòng | Bỏ đoạn gần y hệt 1-2 câu trước (trong 30s) hoặc là câu đó lặp hai lần; câu nói TIẾP câu trước không tính |
| Giọng mới chỉ phủ 68/220s, lặng giữa câu trung vị 2.4s | 60s lời bị sót, và lỗi của bản 25: lời nhắc đổi thành "tối đa n từ" -> AI chỉ viết 67% số chữ (bản 24 "khoảng n từ": 100%), giọng phủ ~53% mỗi khe | Giao KHOẢNG "lo-hi từ" (lo = 80% hi) |
| "xuống." đọc một mình; "...thì Các cá" \| "kèo sẽ giòn..." (cắt giữa tên cá kèo) | Whisper kéo dài mốc chữ cuối; câu gộp sẽ > 6s | Mẩu kết câu đứng lẻ về câu trước (cách ≤ 3s); câu bị cắt ngang gộp tới 9s |
| AI bị bắt viết lại 2 lượt vì "thiếu cua" (bóc lời "Các c. cua, cá kèo"); các lần trước "thiếu mè, bịa dầu mè" (bóc lời "dòng mè"), "thiếu ớt" ("ớt" = ướp) | Báo động giả do lỗi bóc lời — lần chạy nào cũng có, tốn 2 lượt AI, có nguy cơ AI nhét cua vào món cá | Lúc quyết định viết lại: bỏ qua nguyên liệu chỉ nhắc 1 lần; "mè"/"dầu mè", "dừa"/"nước cốt dừa" coi là một. Vẫn bắt đổi sang thứ khác (gà -> trứng gà, hoa hồi -> cá hồi) |
| "Bóc lời + phân tích hình" 400s cho video 220s | Bóc lời (cả nghe lại) chỉ ~17s. Phân tích hình giải mã ĐỦ 30 khung/giây (6.600 khung 720p) trên Botkeep ~380s — 80% là giải mã | `SCENE_ANALYSIS` (Cài đặt): **Nhẹ** (mặc định) chỉ đọc khung chính — dò chữ in cứng, ảnh bìa, ảnh cho AI vẫn đủ; đo 1 luồng 26.2s -> 1.05s (~25 lần nhanh hơn, Botkeep ~15-20s); **Vừa** thêm dò chuyển cảnh, bỏ khung phụ khi giải mã (26.2 -> 14.2s, 11/12 điểm chuyển cảnh, lệch < 0.1s); **Kỹ** như cũ. Video không lời / cắt phần / đoạn hay tự dò chuyển cảnh mức Vừa; khung chính quá thưa thì tự làm mức Vừa |

| Job 2, 3 lỗi "Video dài 220s, vượt giới hạn 90s" (gửi lại link 3 lần mới chạy) | Ô thời lượng mặc định «Giữ nguyên» vẫn bị giới hạn 90s — giới hạn đặt ra vì Botkeep dựng chậm | «Giữ nguyên» + Kaggle bật: video ≤ 5 phút tự làm nguyên cả video (kèm ghi chú). Chọn 30/60/90 giây, cắt phần, đoạn hay: như cũ. Không có Kaggle: như cũ |

Gemini nghe lại mỗi quãng tối đa 45 giây (Gemini quá tải/treo không làm job chờ hàng chục phút).

Phân tích hình dùng để làm gì: (1) dò chữ in cứng — che phụ đề gốc và đặt phụ đề mới đúng chỗ (quan trọng nhất,
giữ ở mọi mức); (2) ảnh bìa — chấm điểm khung đẹp; (3) ảnh từng đoạn cho AI nhìn khi viết lời (2 lần gần đây Gemini
quá tải nên viết không kèm ảnh); (4) điểm chuyển cảnh — câu mới bắt đầu đúng lúc đổi cảnh (video cá kèo chỉ có 12
điểm, 150 giây giữa là một cảnh liền) và bắt buộc cho video không lời (mỗi cảnh một câu).

## Video thật bản 24 (bản 25): cuối video còn giọng gốc, giọng đọc chưa trơn

Đo trên video bạn gửi (mì xào bò 173s, dựng trên Kaggle) bằng cách tách giọng cả video gốc lẫn video ra rồi so từng giây.

| Hiện tượng | Nguyên nhân (đo được) | Sửa |
| --- | --- | --- |
| Giây 143.7-166.7 của video ra trùng y hệt âm thanh gốc (tương quan 1.00), có giọng người gốc, không có giọng mới | Whisper bịa "Đừng quên đăng ký kênh để nhận thêm những video mới nhé." ở 140.4s rồi nhảy qua ~27s lời thật (rắc tiêu, ngò rí, "nhân công thức lên", chốt món — các lần chạy trước bóc được), câu kế là tiêu đề VIẾT HOA chép lại từ prompt. Kaggle chỉ tách giọng các khúc 5.75s có chạm quãng bóc lời thấy người nói, nên 4 khúc đó giữ nguyên âm thanh gốc. Bản 23 chạy Botkeep (video > 120s không tách giọng, tắt tiếng nền theo lời bóc được, lần đó bóc đủ) nên không bị | Kaggle tách giọng CẢ BÀI (4 luồng, chạy song song với đọc giọng — không chậm hơn), kể cả khi bóc lời không thấy chữ nào. Bóc lời: bỏ câu chào kết kiểu YouTube, câu lặp vòng, câu VIẾT HOA; nghe lại riêng từng quãng ≥ 8s không có chữ (không kèm prompt, lọc chặt hơn); tiêu đề VIẾT HOA đổi thành chữ thường trước khi làm prompt; cache bóc lời cũ bị bỏ (có thể thiếu cả quãng lời) |
| Giọng mới: 50 câu, 22 chỗ lặng > 1s, 9 chỗ > 2s; "sau" đọc một mình rồi im 6.1s; "Thêm vào.", "Phi thơm lên." | Lời gốc bị máy nghe cắt vụn ("...70g." \| "cái thìa." \| "70g cải thảo.") — Whisper kéo dài mốc chữ cuối tới tận câu sau nên quy tắc gộp cũ (theo độ dài mẩu) không bắt được | Mẩu 1-2 chữ KẾT câu về với câu trước ("70g cải thìa" về đúng câu); mẩu cụt ≤ 4 chữ MỞ câu mới dời tới sát câu sau và đọc liền |
| 16/46 đoạn bị AI rút gọn ("AI nén lời") | Khe trung vị chỉ 2.8s: giọng Mỹ Duyên ~3.1 chữ/giây chỉ được 4-6 chữ/đoạn ở 21 đoạn; AI vượt số từ giao ở 19/46 đoạn (trung bình +2.6 từ) rồi bị nén | Khe < 3s mà người gốc nói liền thì gộp với câu sau (câu gộp ≤ 6s): trên đúng lời của video này 43 -> 31 đoạn, khe trung vị 2.8s -> 4.5s, đoạn chỉ được ≤ 6 chữ 21 -> 2. Xếp giọng: lấn sang khe dư của câu sau tới 1s (câu sau lùi theo, vẫn xong trong khe của nó) TRƯỚC khi nói nhanh hay bắt AI rút gọn. AI được giao "TỐI ĐA n từ" (số đọc thành chữ cũng tính: 100g = 3 từ); ước lượng độ dài đếm chữ đọc ra |

## Sửa lỗi từ log Botkeep (bản 24)

| Lỗi thật | Nguyên nhân | Sửa |
| --- | --- | --- |
| `Kaggle lỗi 409 ở SaveKernel: title "rmvideo worker" is already in use` -> mọi job lùi về Botkeep (giọng edge, chậm) | `KAGGLE_USERNAME` không khớp chủ token (dán cả link, có `@`, gõ sai): Kaggle tưởng là notebook mới trùng tên. Biến môi trường Botkeep không qua bước kiểm tra của web | Tên tài khoản lấy theo token (Kaggle `IntrospectToken`, hỏi 1 lần rồi nhớ); `KAGGLE_USERNAME` chỉ để dự phòng, nhận cả dạng link. `kaggle_test` in tên tài khoản thật và báo nếu điền khác. Đo thật: link, tên sai, để trống đều đẩy đúng notebook |
| Tải giọng mẫu: `ffprobe lỗi: voice_ref.mp3.tmp.mp3: No such file` (lỗi 500) | Bấm Lưu 2 lần -> 2 lần tải chạy song song dùng chung tên file tạm, lần này xoá file của lần kia | Tên tạm riêng mỗi lần; nút Lưu khoá trong lúc lưu; đọc độ dài lỗi thì báo 400 dễ hiểu |

`edge-tts ... NoAudioReceived` trong log là Microsoft chập chờn khi đã lùi về Botkeep: bot đã đọc tối đa 3 đoạn cùng lúc
và thử lại 6 lần nên job vẫn xong. Kaggle chạy được thì giọng VieNeu đọc trên Kaggle, không gặp lỗi này.

## Rà soát trước deploy (lần 2: Kaggle + trang đăng nhập)

Rà độc lập tìm 7 lỗi, đã sửa hết, mỗi lỗi có test:

| Lỗi | Sửa |
| --- | --- |
| Trang đăng nhập nhận `?next=/\\evil.com` -> trình duyệt chuyển sang trang lạ (open redirect) | Bỏ hẳn tham số `next`, luôn về `/` |
| `vi_text` gặp "12.5.2026", "1.000,5", "v1.2.3" thì ném lỗi -> cả phiên Kaggle đổ, tốn giờ GPU rồi mới lùi về Botkeep | Số lạ đọc từng cụm ("mười hai chấm năm chấm ..."), không bao giờ ném lỗi |
| "5-7kg" -> "năm đến bảykg"; ngày "12/11" thành phân số; giờ "10:30" đọc sai | Khoảng + đơn vị/% xử lý trước; ngày/giờ đọc đúng kiểu |
| Lô nhiều phần với giọng nhân bản: đoạn mẫu gói lặp theo từng phần -> notebook > 1MB, cả lô bị từ chối | Gói đoạn mẫu một lần cho cả lô |
| `produce_batch` chỉ bắt `KaggleError`: lỗi khác làm mất hết các phần đang chờ, không lùi về Botkeep | Bắt mọi lỗi, từng phần làm trên Botkeep |
| Phần Kaggle lỗi làm lại trên Botkeep thì bóc lời + gọi AI viết lại từ đầu (tốn tiền, lời khác) | Giữ lời đã chốt, đi đường "kịch bản đã duyệt" |
| Hạn thời gian job cộng 50 phút một lần dù lô nhiều phần chờ lâu hơn; huỷ job thì phiên Kaggle vẫn chạy tốn giờ GPU | Hạn theo số phần; huỷ job gọi huỷ phiên Kaggle, dừng chờ ngay |

## Trang đăng nhập riêng cho web

`/login` (static/login.html): `/` chưa có phiên hợp lệ thì chuyển hướng sang đó; đăng nhập xong về `/` (hoặc `next` nếu
là đường dẫn nội bộ); đã đăng nhập mà mở `/login` thì về `/`; đăng xuất về `/login`. Bỏ ô mật khẩu trên trang chính.
Kiểm: test đủ luồng; khởi động bot thật đi 9 bước bằng cookie (chuyển hướng, sai/đúng mật khẩu, API, đăng xuất).

## Mọi chế độ dựng trên Kaggle

| Việc | Cách làm | Kiểm |
| --- | --- | --- |
| 1. Video tải lên / gửi Telegram | Link tạm `/kin/<mã>` trên web panel (cần PUBLIC_URL; mã 32 byte, hết hạn 4 giờ, huỷ khi Kaggle xong); Kaggle ưu tiên bản Botkeep, không được thì link nền tảng | test; chạy thật: link nền tảng hỏng -> tải bản dự phòng qua http |
| 2. Giọng edge / CapCut | Đọc ngay trên Kaggle (dịch vụ mạng), chạy CPU không tốn giờ GPU; AI rút gọn trước theo tốc độ giọng | chạy thật: edge 7.4s, CapCut 4s, dựng 15s, cả lô 87s |
| 3. 🎵 nhạc/nhảy, 📸 ảnh + nhạc | Botkeep tính beat / chọn seed + lịch sử công thức hiệu ứng; Kaggle chỉ dựng (render_music / slideshow.render) | test với Kaggle giả chạy đúng hàm thật; chạy thật lô nhạc nền (dựng 36s) + ảnh (15s, công thức pastel_soft trả về Botkeep), 86s CPU |
| 4. Video dài nhiều phần | Mỗi phần chuẩn bị xong thì chờ (Deferred), mọi phần chạy chung MỘT phiên; có PUBLIC_URL thì Kaggle tải từng phần đã cắt, không có thì tự tải nguồn một lần rồi cắt y như Botkeep; phần lỗi làm lại trên Botkeep | test (cả trường hợp một phần lỗi); chạy thật lô 2 việc chung nguồn |

Notebook Kaggle tối đa 1MB (đo thật: 3MB bị từ chối) nên file lớn luôn đi qua link. Cài đặt mới `KAGGLE_USE` (tắt nhanh
Kaggle không cần xoá token).

## 🎙 Giọng miền Nam VieNeu (Mỹ Duyên / Adam) + phần nặng chạy trên Kaggle

Người dùng chọn: Kaggle cho mọi video nhưng chỉ phần nặng; giọng nữ Mỹ Duyên, nam Adam; chỉ model cho phép thương mại.

| Thay đổi | Kiểm |
| --- | --- |
| `TTS_PROVIDER=vieneu` (mặc định), `VIENEU_VOICE_FEMALE/MALE`, `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN` (che như các key khác) | test |
| `kaggle_worker.py` (Botkeep): khi lời đã chốt, gửi MỘT notebook riêng tư (code nén + kịch bản JSON, không key API) -> chờ -> tải video; hết giờ GPU thì chạy CPU; lỗi bất kỳ -> làm trên Botkeep bằng giọng edge đúng giới tính | máy chủ Kaggle giả + pipeline giả lập Kaggle lỗi |
| `kaggle_job.py` (trên Kaggle): VieNeu đọc giọng -> xếp giọng một tốc độ chung -> phụ đề -> tách giọng gốc -> dựng 1080p | chạy tại máy thử với giọng giả (dựng thật); chạy thật trên Kaggle |
| `kaggle_api.py`: API kagglesdk (`api.kaggle.com/v1/...`, Bearer token kiểu mới, User-Agent `kaggle-api/` — thiếu là 404) | đo thật |
| `vi_text.py`: số -> chữ kiểu miền Nam (100g, 1/3, 60%, 5-7, 1,5kg, 1.000.000đ) cho VieNeu | test |
| Tốc độ đọc theo giọng (Mỹ Duyên 3.08, Adam 4.15 chữ/giây, đo thật) -> AI viết lời vừa khung hình | test |
| Giọng nhân bản: tải đoạn mẫu 3-15 giây trong Cài đặt, chọn `clone` | test |
| Console `kaggle_test` (token + giờ GPU còn lại) | — |

Chạy thật trên Kaggle (video mì xào bò 64s, giọng Mỹ Duyên): tải video Facebook, nạp model ~12s, đọc 21 câu ~15s, tách
giọng gốc 68s, dựng 1080p 130s (Botkeep ~19 phút). Lỗi tìm được khi chạy thật: `/tmp` và `/kaggle/working` khác ổ ->
`os.replace` lỗi, đổi sang `shutil.move`.

## Rà soát trước deploy (tự động đăng, xếp giọng)

Rà độc lập phần mới tìm 9 lỗi, đã sửa hết, mỗi lỗi có test:

| Lỗi | Sửa |
| --- | --- |
| Hai lượt tự đăng chạy cùng lúc (vòng nền + nút web/console) đăng trùng một video | Khoá: mỗi lúc một lượt |
| Đăng xong mà ghi `autopost.json` lỗi (ổ đầy) thì 2 phút sau đăng lại | Ghi dấu "đang đăng" trước khi tải, ghi ngay sau khi đăng, nhớ trong bộ nhớ; dừng giữa chừng thì không tự đăng lại, báo kiểm tra |
| AI lỗi (kịch bản mẫu) / lời gần như chép lời gốc vẫn được đăng công khai | Job gắn cờ `no_autopost` (kịch bản mẫu, lời mới khác < 70%): không đăng, chọn video khác |
| Lỗi tạm (xem video lỗi, job lỗi, Facebook giới hạn) đốt sạch video nguồn trong vài phút | Video nghỉ 6 giờ rồi thử lại, lỗi 2 lần mới bỏ; đăng lỗi chờ 5 → 60 phút; 3 video liên tiếp lỗi / lỗi token, sai Page, bị chặn thì dừng + báo |
| Đọc kênh / lượt xem lỗi thì gọi lại Facebook mỗi 2 phút (~3.600 lần/ngày); hết 7 ngày ghi 0 lượt xem | Nhớ lần lỗi, thử lại sau 1-6 giờ; không ghi số 0 giả |
| `facebook.com/kenh` (không có https) thành kênh sai | Nhận đúng |
| Đổi `AUTO_TIMES` / `AUTO_PER_DAY` ~2 ngày sau mới áp | Áp ngay cho các ngày chưa có bài; `AUTO_PER_DAY` 1-4 |
| `autopost.json` hỏng thì lặng lẽ mất lịch sử (đăng lại video cũ) | Giữ bản cũ `.bad`, dừng chờ người kiểm tra |
| `plan_voice`: đoạn sau bị đẩy qua mốc thì có thể đọc chậm lại / atempo < 0.5 làm hỏng job; đoạn TTS rỗng chia cho 0 | Không bao giờ chậm hơn tốc độ chung; đoạn rỗng giữ chỗ |

Thêm: dòng ghi nguồn tắt mặc định (`SOURCE_CREDIT=0`). Khởi động thật (chỉ-web) 2 lần: tự kiểm tra ffmpeg, đo tốc độ
dựng, các API mới, chặn kênh không phải Facebook, SIGTERM thoát gọn. Giọng: đọc thật bằng Hoài My 64 giây đầu video mì xào
bò trong log: tốc độ từng đoạn cũ 0.92-1.35x (20/29 đoạn "quá dài"), mới 1.10-1.21x (3/21).

## 🤖 Tự động đăng Fanpage; giọng Hoài My, đều tốc độ, ngắt câu tự nhiên

Log Botkeep 12/11: video 173s ra 720p, cả job 36.9 phút (render 1674s). Người dùng: "voice lúc nhanh lúc chậm và
ngắt câu chưa hợp lý"; muốn Hoài My (edge) làm mặc định, lỗi thì CapCut; và chế độ tự lấy video từ kênh Facebook,
tự đăng Fanpage giờ vàng 2 video/ngày.

| Thay đổi | Kiểm |
| --- | --- |
| Cả video đọc một tốc độ chung (phân vị 80% tốc độ cần, 1.0-1.12x), từng đoạn lệch ≤5% (≤10% nếu vẫn tràn sau khi AI rút gọn), không bao giờ làm chậm. Trước: mỗi đoạn 0.92x-1.35x | test |
| Gộp mẩu lời cụt ("Sau khi mình" + "luộc ... vớt ra", "Rồi" + câu sau) thành câu trọn ≤6s trước khi AI viết lại; giữ riêng từng món trong danh sách nguyên liệu; nguồn bóc lời không có dấu câu thì chỉ gộp mẩu 1-2 chữ | test trên đúng các mẩu trong log |
| `TTS_PROVIDER=edge` mặc định, Hoài My cho mọi chế độ; edge lỗi hẳn -> đoạn đó đọc bằng giọng nữ CapCut trong Cài đặt. CapCut "Hoai My"/"Nam Minh" gọi thật trả `TTSInvalidSpeaker` nên bỏ khỏi danh sách giọng | gọi thật CapCut |
| `autopost.py`: đọc tab Reels + Video của kênh nguồn (không cần đăng nhập, ~10 video/tab), chọn ngẫu nhiên video chưa dùng, bỏ video > 5 phút / không phải của chủ kênh; chỉ thêm job khi hàng đợi trống; làm sẵn cho khung giờ trong 36 giờ tới; đăng Reels (≤90s) hoặc video, **hẹn giờ phía Facebook**; giờ vàng 11:30 + 20:30 rồi học theo lượt xem sau 24 giờ; token lỗi thì dừng + báo admin | đọc thật 2 kênh (12 và 10 video, chủ kênh khớp); 11 test với máy chủ Graph API giả |
| Web: ô 🤖 Tự động đăng (trạng thái, lấy video ngay, thử token, chạy lại); console: `auto`, `auto_now`, `auto_resume`, `fb_test` | test |

## Máy chậm: tự ra 720p khi bản 1080p dựng quá lâu

Log Botkeep 16:17-16:28: video đùi gà 88s (nguồn 1080p) mất gần 30 phút. Bước nặng nhất là dựng hình 1080p.
Đo trên chính video đó, 30s, 1 lõi:

| Phần | Thời gian |
| --- | --- |
| Giải mã (AV1) | 3.4s |
| Cắt + scale lanczos | +2.8s |
| hqdn3d (khử nhiễu) | +8.6s |
| vibrance (tăng màu, phải đổi sang RGB) | +23.5s |
| unsharp (làm nét) | +8.8s |
| x264 veryfast 1080p | ~28s |
| **Dựng 1080p / 720p** | **75.4s / 37.6s** (720p nhanh gấp 2) |

Công thức màu đã duyệt giữ nguyên (thử thay vibrance bằng bảng Hald CLUT: chậm hơn, 53s). Thay vào đó:

| Thay đổi | Kiểm |
| --- | --- |
| `QUALITY=balanced` (mặc định): ước tính thời gian dựng 1080p theo tốc độ đo được của chính máy; quá `RENDER_BUDGET` (mặc định 600s) thì ra 720p và báo người dùng | test; chạy thật với tốc độ giả lập 13.6 giây/giây: video 60s ra 720p kèm lời nhắn |
| Tốc độ máy: đo nhanh 3 giây 1080p lúc khởi động (lần đầu), sau mỗi lần dựng thì cập nhật bằng số thật (`render_speed.json`) | test |
| `QUALITY=fast` luôn 720p (như chế độ 📸); `high` luôn 1080p | test |
| Hết mỗi job ghi một dòng tổng thời gian từng giai đoạn (tải, bóc lời, AI, đọc giọng, render...) | — |
| Lỗi tạm của Gemini khi AI viết lại: một dòng cảnh báo thay vì cả trang traceback | — |

## Giọng edge-tts lỗi làm hỏng cả job; đoạn giọng bị coi là "quá dài" oan

Log Botkeep 15:44: link chia sẻ Facebook đã tải được, nhưng job hỏng ở bước đọc giọng (`NoAudioReceived` của
edge-tts, giọng Nam Minh); các đoạn khác vẫn chạy tiếp sau khi job hỏng và ghi vào thư mục tạm đã xoá.

| Lỗi | Sửa | Đo / kiểm |
| --- | --- | --- |
| edge-tts trả `NoAudioReceived` ngẫu nhiên phía Microsoft, thử 3 lần chưa đủ | Thử tối đa 6 lần, chờ tăng dần + ngẫu nhiên; vẫn lỗi thì đoạn đó đọc bằng giọng CapCut cùng giới tính (rồi Piper nếu bật) thay vì hỏng job | Gọi thật 14 câu của job: Nam Minh lỗi 0-35% số lần tuỳ lúc, cùng câu gọi lại là được, không do câu chữ / tốc độ; test |
| Một đoạn đọc giọng lỗi thì các đoạn khác vẫn chạy (gọi TTS vô ích, `No such file or directory`) | `_gather_all`: một việc lỗi thì huỷ các việc còn lại; lỗi thiếu thư mục không thử lại | test |
| "Rút gọn 13 đoạn quá dài" | Độ dài đoạn giọng tính tới hết tiếng nói, không tính ~0.78s lặng edge-tts để ở cuối mọi file | 28 câu: tốc độ thật 3.9 từ/giây, tính cả khoảng lặng chỉ còn 3.1; test |
| Video 110s ở chế độ thường: lời báo bảo cắt ngắn rồi upload | Chỉ đúng nút «🎞 Video dài» / «✂️ Cắt thành phần» / «✨ Đoạn hay nhất» | test |

## Link chia sẻ Facebook, Whisper chép nhầm prompt

Log Botkeep (bản cũ, khởi động 14:43): `No suitable extractor found for URL https://www.facebook.com/share/r/...`.
Link Facebook bấm "Chia sẻ" giờ có dạng `facebook.com/share/r|v|p/...` (hoặc `fb.watch/...`): yt-dlp không nhận
dạng được, chỉ extractor "generic" mới đi theo chuyển hướng — mà generic bị tắt để chặn SSRF. Lỗi `text_align`
trong cùng log đã sửa ở bản trước (89e513b): cập nhật code là hết.

| Thay đổi | Kiểm |
| --- | --- |
| `core_video.resolve_url`: link chia sẻ Facebook được mở ra link reel/video thật trước khi đưa yt-dlp (header chuẩn của yt-dlp, mỗi bước chuyển hướng kiểm lại nền tảng + IP nội bộ, dừng ngay khi có link thật, không tải trang). Áp ở cả 4 lối tải | 2 link trong log → `facebook.com/reel/<id>/`, tải trọn 28s/15MB; test với máy chủ chuyển hướng cục bộ |
| Video cần đăng nhập / link hỏng / link trang báo lỗi rõ ràng (cookies, sao chép link của chính video) | test |
| Tiêu đề Facebook bỏ phần "2.6M views · 29K reactions \|" trước khi đưa AI / Whisper | test |
| Prompt Groq bỏ nhãn "Từ khóa:" (Whisper chép nguyên nhãn thành phụ đề ở đoạn chỉ có nhạc: "Đoạn 0 0.4s \| gốc: Từ khóa") | test |
| Bỏ đoạn Whisper "nghe" ra ở chỗ im lặng/nhạc (quy tắc no_speech_prob > 0.6 và avg_logprob < -1 của Whisper) và câu bịa quen thuộc ("Ghiền Mì Gõ", "Vietsub by"); lọc cả kết quả đã lưu cache | test |

Ghi chú Botkeep: máy cho **1.5 lõi** (CPU 150%). Tách giọng Kim_Vocal_2 ~148s/khúc nên bị bỏ qua (vượt ngân sách
180s): gõ `setup_offline` để có Spleeter (nhanh hơn ~30 lần) nếu cần tách giọng.

## Botkeep thật: ffmpeg 5.1, hiệu ứng trend, dựng nhanh hơn

Bài tự kiểm tra ffmpeg trên Botkeep cho biết máy dùng **ffmpeg 5.1 của Debian 12** (không phải bản static 8.0) và
bắt được lỗi `text_align` (chỉ có từ 6.1) -> tiêu đề mở đầu và chữ ảnh bìa hỏng: thay bằng mỗi dòng một drawtext căn
giữa. Job 📸 trên Botkeep: 14s 1080p, -14.0 LUFS, đúng; nhưng dựng mất ~4 phút.

| Thay đổi | Đo |
| --- | --- |
| Công thức hiệu ứng trend (10 công thức, trendfx.py), chọn theo kiểu nhạc, ngẫu nhiên thật, tránh công thức vừa dùng | Dựng thử đủ 10 công thức không lỗi; hai lần làm cùng ảnh + nhạc ra hai công thức khác nhau (test) |
| Một ảnh: chuyển động mạnh hơn (×1.7) | Video thật trên Botkeep với 1 ảnh nhìn gần như ảnh tĩnh |
| Dựng 2 khúc song song khi máy ≥ 1.4 lõi và RAM đủ | 2 lõi: 16.0s → 14.6s (1.68 → 1.86 lõi); x264 1080p chiếm ~75% thời gian |
| `QUALITY=fast` → 📸 ra 720p | 14s video: 16.2s → 8.3s |
| Log "Dựng hình …: dùng X lõi" | Để biết Botkeep có bóp CPU không |

## Sửa lỗi cài trên Botkeep: `No space left on device`

Pip đổ lỗi đầy ổ khi đang tải deno (42MB tải, 96MB cài) — gói nặng nhất, chỉ cần cho YouTube.
- Bỏ deno khỏi `requirements.txt`; lệnh console `setup_youtube` / `remove_youtube` cài/gỡ deno vào `tools/deno`
  (pip `--no-cache-dir`), bot tự đưa vào PATH. Chưa cài thì lỗi YouTube nói rõ cách cài, kiểm tra định kỳ bỏ qua
  YouTube (không báo động giả).
- `.config/pip/pip.conf`: tắt bộ nhớ đệm pip (HOME = thư mục bot trên Botkeep nên bộ nhớ đệm tính vào ổ).
- Đo cài sạch: 346MB, không bộ nhớ đệm (trước: ~440MB + 150–250MB bộ nhớ đệm + file tạm lúc tải).
- Lệnh console `clean` (xoá bộ nhớ đệm pip + cache + work/), `status` báo thêm `.cache`, `.local`; khởi động mà ổ còn
  < 400MB thì cảnh báo kèm cách dọn.
- Không hạ static-ffmpeg dù bản 3.0 kéo theo `twine` (~40MB thừa): bản cuối không kéo `twine` (2.8) tải ffmpeg 5.0,
  quá cũ cho các bộ lọc đang dùng.

## Kiểm tra trước khi merge

Cài sạch từ `requirements.txt` trên Python 3.11 và 3.12 (như CI): 176 test xanh mỗi bản, ruff sạch. Quét lịch sử
git: không có key/token, không có file nhạy cảm. Chạy thật bot (web) với job 📸 và 🎵 nhạc nền qua API như người
dùng. Tìm và sửa thêm:

| Lỗi | Sửa / đo |
| --- | --- |
| Tắt bot (SIGTERM) giữa job: job bị ghi "Đã hủy theo yêu cầu" + xoá file tải lên (không chạy lại được), worker bốc tiếp job sau, tiến trình không thoát (luồng đọc console kẹt) — có từ trước | Phân biệt bot tắt / người dùng huỷ; giữ job + file; console đọc stdin bằng luồng daemon. Đo: thoát sau 0.4s, khởi động lại thì job chạy lại và xong |
| 📸 RAM tăng theo số cảnh (60s nhạc nhanh: 2GB ở 1080p) | Dựng theo khúc ≤ 8 cảnh + ghép chép luồng: 540–585MB ở 1080p bất kể độ dài |
| 📸 Video dài hơn phần nhạc còn lại (đuôi im lặng) | Giới hạn theo nhạc + báo |
| Telegram: nút cũ biến luồng ảnh thành job video không link | Chặn, giữ ảnh |
| Nhạc TikTok lỗi giữa chừng để lại file dở bị đường dự phòng nhặt nhầm | Tải ra .part rồi mới đổi tên |
| Link media bên thứ ba chỉ kiểm link đầu, không kiểm chuyển hướng | Kiểm từng lần chuyển hướng (cả tải video tikwm) |
| Dọn ổ đĩa xoá ảnh/nhạc của job đang chờ | Giữ lại |
| Nhạc nền tắt khi tiếng gốc ngắn hơn hình | Đệm tiếng gốc tới hết video |
| Web: file gửi kèm không dùng tới bị bỏ lại | Xoá ngay |

## Đợt 4 — 📸 Ảnh + nhạc, nhạc nền / nhạc TikTok

| Thay đổi | Đo / kiểm |
| --- | --- |
| Chế độ 📸: 1–4 ảnh + nhạc → video theo nhịp (slideshow.py) | BPM đúng 8/8 bản nhạc thật có BPM công bố (trước khi thêm thử tỉ lệ 3:4 và nhịp chậm: 6/8 tuyệt đối); nhạc thử có drop ở giây 15.0 → bắt được 15.0; dựng 12–15s/720p trong 8–14 giây trên 1 lõi |
| Link video TikTok làm nhạc: lấy bản nhạc gốc qua tikwm | Thử thật: tải được sound mp3 của video mẫu; lỗi thì yt-dlp |
| 🎵 làm nhạc nền (giữ tiếng gốc) | Test: thời lượng video không đổi, tiếng gốc vẫn còn trong bản trộn |
| Web + Telegram cho cả hai | Test luồng album 5 ảnh (lấy 4) → nhạc → thời lượng; nhạc → nhạc nền → thời lượng; upload web 2 ảnh, 5 ảnh (từ chối), thiếu nhạc (từ chối) |

Sửa sau rà soát đợt 3 (commit riêng): render bản đã duyệt nghe lại lời gốc; đoạn hay không bám khung khoá; golden.py
giải mã mọi khung (2.3GB RAM) và dùng chung jobs.db; tiêu đề có '%' làm mất chữ trên ảnh bìa/tiêu đề mở đầu.

## Đợt 3 — 7 nâng cấp chất lượng

| Nâng cấp | Đo / kiểm |
| --- | --- |
| Từ điển bóc lời `ASR_VOCAB` (vocab.py): prompt cho Groq Whisper, sửa chính tả sau khi nghe, đưa vào lời nhắc AI | Groq lỗi khi kèm prompt thì gọi lại không kèm. Gemini transcribe trả 400 nếu dùng `custom_vocabulary` cùng mốc từng từ nên không dùng ở đó. Sửa chính tả chỉ cụm ≥2 âm tiết (tránh "đâu đó"→"đậu đỏ") |
| Karaoke khớp giọng CapCut: rải chữ trên khoảng có tiếng của giọng đã tạo | 105 từ edge-tts có mốc thật: lệch trung bình 271ms → 69ms (qua compose_voice + atempo 1.1: 61ms), 90% số từ < 124ms; STT CapCut 85ms và 7–11 giây/lần |
| Chuẩn hoá âm lượng hai lượt | Giọng thưa: một lượt -14.5/-15.3 LUFS, nền giữa câu -17.9 nhưng cuối video -24; hai lượt -14.1, nền -21.0/-20.7. Render không chậm hơn |
| Bộ video mẫu `golden.py` + lệnh console `golden` | Chạy thật trên 2 video tự tạo: report.md, ảnh bìa, ảnh 4 khung; so chuẩn theo ngưỡng |
| Thời gian từng giai đoạn trên web | Gom bước thành giai đoạn cố định; thêm bước "Gửi" để không tính vào render |
| Ảnh bìa tự động (cover.py) | Chấm điểm khung (nét, màu, sáng, vị trí), AI chọn trong 6 ứng viên; ~1 giây CPU, chạy song song render; thumbnail Telegram 180x320 |
| ✨ Đoạn hay (highlights.py) | AI chấm điểm lời + ảnh, ≤3 đoạn 30–88.5s, ranh giới về khoảng lặng/chuyển cảnh; chạy thật 150s nguồn → 2 đoạn 45s + 42s kèm ảnh bìa |

Không đổi chất lượng hình: chuỗi lọc video được tách thành `core_video.look_chain` (dùng chung với ảnh bìa), so chuỗi
lọc trước/sau giống hệt từng ký tự.

## Tăng tốc không giảm chất lượng (đo trên 1 lõi, video 60s 1080p chế độ nấu ăn)

| Thay đổi | Kết quả đo |
| --- | --- |
| Làm ấm màu bằng bảng tra 1 chiều `lutrgb` thay cho `colortemperature` (công thức của FFmpeg với pl=0 chỉ là nhân hằng số từng kênh) | Ảnh trước mã hoá: SSIM 0.99987, PSNR 55 dB so với cách cũ. Render 20s: 57.0s → 45.9s (−19.5%); cả job 60s: 176s → 144s (−18%) |
| Tách giọng gốc bắt đầu ngay khi biết đoạn có lời, chạy trong lúc chờ AI viết kịch bản (CPU đang rảnh) | Tiết kiệm đúng bằng thời gian chờ AI (thường 5–20s) ở mỗi video nấu ăn có tách giọng |

Đã thử và không dùng: bảng màu 3D (Hald CLUT) cho cả công thức màu — chậm hơn 34%; lọc màu/khử nhiễu trước khi phóng
nhẹ 1036→1080 — nhanh hơn 5% nhưng ảnh khác thấy được (SSIM 0.9925, PSNR 43 dB); bỏ bộ lọc khử khối khi giải mã để
phân tích hình — chỉ nhanh 1s/phút video.

Đo cả job 60s 1080p (giả lập phần mạng): tải/nhận 0.2s, bóc lời + phân tích hình 8.6s, đọc giọng 1s, render 133.6s.
Render chiếm 93%: trong đó lọc hình ~70% (vibrance, hqdn3d, unsharp là nặng nhất), mã hoá x264 ~25%.

## Chưa làm (để lại, có lý do)

| Việc | Lý do |
| --- | --- |
| pydantic-settings | `config.py` đã kiểm kiểu mọi khoá có giá trị cố định; thêm thư viện không đem lại gì thêm |
| HTMX | Đã có Server-Sent Events; HTMX chỉ đổi cách viết giao diện, không thêm chức năng |
| Worker render trên VPS | Cần máy thứ hai để thử (xem bảng Giai đoạn 4) |
