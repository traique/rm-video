# Kế hoạch: kênh truyện thiếu nhi AI («kids») trong RM-VIDEO — MVP v1 — CHƯA CODE

**Mục tiêu (ý tưởng ngày 18/11/2026):** 2 video/ngày, 60-90 giây, 9:16, giọng Việt kể chuyện, một bài học, học 3-5 từ
tiếng Anh, tự động 100%, đăng Facebook Reels lúc 08:00 và 19:00, chạy trên GPU Kaggle, không dùng model sinh video.

**Trạng thái 18/11/2026: ĐÃ CODE bản MVP (1.1.0)** — `rmvideo/kids/`, `kids_auto.py`, `rmvideo/app/flow/kids.py`, cài đặt nhóm 🧸,
mục trên web, lệnh console; test không cần token (`tests/test_kids.py`). Chưa chạy thật trên Kaggle: bước kế là `kids_setup` rồi
«Làm 1 truyện ngay», soát vài video, chỉnh theo lần chạy đầu, rồi bật `KIDS_AUTO`.

**Quyết định sau bài đo Qwen offline của kênh bếp (17/11, `Bài đo Qwen offline.html`):**
- **Model chữ: Qwen3.6-35B-A3B** (GGUF 4-bit, llama.cpp đã biên dịch, 57 token/giây trên 2×T4, nạp 81–93 giây) thay cho
  Qwen3-30B-A3B: bài đo cho thấy 3.6-35B viết đúng đoạn hình, ít «nha», số liệu đúng; cặp 30B + VL-7B kém hơn. Không dùng Gemini.
- **Model ảnh mặc định: FLUX.1-schnell 4-bit** (Apache-2.0, chạy được T4 fp16 + NF4, có bản lượng tử sẵn qua `kids_setup`);
  Z-Image-Turbo giữ làm lựa chọn `KIDS_IMAGE_MODEL=zimage` để thử sau. Nhất quán nhân vật: khối mô tả nhân vật cố định đặt đầu prompt,
  1–2 nhân vật mỗi ảnh, cùng khối phong cách; LoRA nhân vật sau khi có 20–30 ảnh đẹp.
- **Giọng: Thục Đoan** (VieNeu, kể chuyện, miền Nam); lời model qua bộ đổi từ miền Nam của kênh bếp + từ trẻ em.
- **Tên kênh: Kể Bé Nghe**, thế giới **Đồi Cỏ Non** (bạn đã chốt).
- **Vận hành:** làm tay vài video đầu để soát (`kids_now` / nút web, không tự đăng), đạt rồi bật `KIDS_AUTO=1`.

**Quyết định đã chốt (18/11/2026, sau bản đầu):**
- **Model chữ: Qwen3-30B-A3B** (bản Instruct-2507, không có chế độ suy nghĩ) chạy trên Kaggle, KHÔNG dùng Gemini cho kênh
  này — hạn mức Gemini miễn phí không gánh nổi hai kênh. Lý do chọn MoE 30B-A3B thay 32B dense: mỗi bước chỉ 3B tham số
  hoạt động nên sinh chữ nhanh gấp 3-5 lần mà chất lượng gần 32B (đã so ở `KE_HOACH_AI_KAGGLE.md` mục 4).
- **Fanpage riêng** cho kênh trẻ em (mục 13).
- **Tên kênh:** đề xuất ở mục 12, chờ bạn chọn.

**Kết luận ngắn:** làm được, và làm được NHANH vì RM-VIDEO đã có sẵn 70% phần nền (gửi việc Kaggle, giọng VieNeu, xếp
giọng + phụ đề không cần mốc từ, hiệu ứng Ken Burns + chuyển cảnh, chuẩn loudness, đăng Reels hẹn giờ, hàng đợi, thông
báo). Phần phải viết mới là: chọn đề + chống trùng, lời nhắc cho model chữ, sinh ảnh, bộ dựng riêng cho truyện, vòng lịch
đăng riêng. Dưới đây là 8 điều chỉnh so với ý tưởng gốc để khả thi và ổn định hơn, rồi thiết kế, ước lượng, rủi ro, lộ trình.

## 1. Tận dụng được gì từ RM-VIDEO

| Bước trong ý tưởng | Mã đã có | Dùng lại hay viết mới |
| --- | --- | --- |
| Chạy trên Kaggle (gửi notebook, chờ, tải kết quả, hết giờ GPU, huỷ) | `kaggle_api.py`, `kaggle_worker.py` (`_run_sync`, `with_expiry`, `kernel_text`), `kaggle_job.main` | Dùng lại nguyên. Thêm loại việc `kind="kids"` |
| Bộ mã gửi Kaggle tự tính theo import | `rmvideo/services/kaggle_bundle.py` | Dùng lại: mã mới đặt trong `rmvideo/kids/` là tự được gói |
| Giọng VieNeu trên GPU | `kaggle_job.make_synth` (`vieneu`, `infer_batch`) | Dùng lại; chọn giọng kể chuyện |
| Đo độ dài tiếng nói, rải phụ đề theo khoảng có tiếng (VieNeu không trả mốc từ) | `voiceplan.speech_duration`, `voiced_intervals`, `align.spread_voiced` | Dùng lại nguyên |
| Phụ đề .ass to đậm viền đen, .srt | `subs.write_ass`, `subs.to_srt` | Dùng lại + thêm tô màu từ tiếng Anh |
| Ken Burns (zoom/lia), xfade, nền mờ cho ảnh lệch tỉ lệ, dựng theo khúc, NVENC | `slideshow.zoompan_expr`, `_prep`, `xfade_transitions`, `renderer.encode_args` | Dùng lại hàm; viết bộ lập kế hoạch cảnh mới (theo giọng, không theo beat) |
| Nhạc nền nhỏ dưới lời, tự hạ khi đang đọc | `rmvideo/media/voicebed.py` (sidechain), `bgm.py` (thư viện nhạc) | Dùng lại cách trộn; thư mục nhạc riêng cho trẻ em |
| Loudness -14 LUFS, true peak -1 | `renderer.two_pass_loudness`, `core_video.LOUDNORM` | Dùng lại |
| Đăng Reels hẹn giờ, gắn ảnh bìa, đọc lượt xem | `autopost.publish`, `_upload_reel`, `_set_cover`, `views_of` | Dùng lại hàm đăng; vòng lịch viết mới (xem 2.h) |
| Hàng đợi, worker, ngân sách giờ, chạy lại sau restart, thông báo Telegram/Zoom | `queue_manager`, `jobs.py`, `rmvideo/app/notifier.py` | Dùng lại; thêm `Mode.KIDS` |
| Cài đặt trên web, bí mật không gửi Kaggle | `rmvideo/app/settings.py` (`SNAPSHOT_EXCLUDE_PREFIXES`) | Dùng lại; thêm nhóm `kids` |
| Test không cần token (Kaggle giả, ffmpeg thật) | `tests/test_kaggle.py`, `tests/test_photo.py` | Theo mẫu đó |

## 2. Tám điều chỉnh so với ý tưởng gốc (và lý do)

**a. MỘT phiên Kaggle làm trọn: chữ → ảnh → giọng → dựng.** Ý tưởng gốc gọi Qwen ở 5 bước rời (1, 2, 4, 5, 11). Mỗi
bước thành một phiên Kaggle là thêm một lần chờ máy (đã đo 0-30 phút). Vì vậy: Botkeep chọn đề và gửi MỘT việc; notebook
nạp Qwen một lần, sinh toàn bộ chữ (kịch bản, 8 cảnh, 8 prompt ảnh, tiêu đề/mô tả/hashtag) trong một chuỗi, kiểm bằng mã,
viết lại tối đa 2 lần, rồi giải phóng VRAM, nạp model ảnh, sinh 8 ảnh, đọc giọng, dựng, trả `output.mp4` + `result.json`.
Botkeep nhận về, QA bằng mã, giao và đăng. Đúng kiến trúc hiện tại: «Botkeep điều khiển, Kaggle là thợ».

**b. Bước 1 (ý tưởng truyện) làm bằng MÃ, không gọi model.** Có 10 bài học × 8 bối cảnh × 10 xung đột = 800 bộ ba; 60
video/tháng. Botkeep chọn ngẫu nhiên một bộ ba CHƯA dùng trong 30 ngày (ưu tiên bài học ít dùng nhất) và 3-5 từ tiếng Anh
từ **kho từ có sẵn** (xem mục 5), tránh bộ từ vừa dùng. Model chỉ việc viết truyện theo đề đã chốt. Hết hẳn vòng «sinh ra
rồi thấy trùng thì bỏ», và chống trùng là chắc chắn 100% thay vì nhờ model «nhớ».

**c. Model ảnh: không nên dùng FLUX.1-dev cho kênh kiếm tiền.** Giấy phép FLUX.1 [dev] là phi thương mại cho MODEL:
điều khoản đầu ra cho dùng đầu ra vào mục đích thương mại, nhưng «dùng model cho hoạt động tạo doanh thu» bị xếp là mục
đích thương mại và cần mua giấy phép riêng của Black Forest Labs; cộng đồng còn tranh cãi, tức là rủi ro pháp lý thật
(bạn đã chốt ở `docs/KAGGLE_PLAN.md` mục 3: chỉ dùng model cho phép thương mại). Hai ứng viên thay thế, đều Apache-2.0:
- **FLUX.1-schnell**: cùng kiến trúc, 4 bước thay vì 28 → nhanh ~6 lần; chạy được 4-bit (NF4) trên T4.
- **Z-Image-Turbo** (Alibaba, 6B, 8 bước, không CFG): nhỏ hơn FLUX một nửa, tài liệu nói chạy trong 16GB VRAM, chữ trên ảnh
  tốt; cần kiểm fp16 trên T4 (T4 không có bf16) ở K0.
K0 (mục 9) sinh cùng 8 prompt bằng cả hai, bạn chấm mù. FLUX.1-dev chỉ dùng nếu bạn mua giấy phép dev tự host.

**d. Sinh ảnh ở 832×1472 rồi phóng lên 1080×1920.** Sinh thẳng 1080×1920 (2,1 MP) chậm ~2 lần và dễ hết VRAM trên T4;
các model này đẹp nhất quanh 1-1,5 MP. Phóng bằng lanczos (đủ cho hoạt hình 3D nét, màu phẳng); `slideshow` đã phóng
nền 2 lần trước zoompan nên chuyển động vẫn mượt.

**e. Độ dài cảnh theo GIỌNG, không cố định 7-10 giây.** Mỗi cảnh = thời gian đọc đoạn đó + 0,6 giây đệm, tối thiểu 5
giây. Kịch bản 190-230 chữ với Thục Đoan (~3,4 chữ/giây) hay Mỹ Duyên (3,08 chữ/giây, đã đo) cho 56-75 giây tiếng nói;
cộng đệm + màn mở/kết ~5 giây → 62-85 giây, rơi gọn trong 60-90 mà không phải ép. Lời và hình luôn khớp nhau (đổi cảnh
đúng lúc sang đoạn mới), không có cảnh «chờ» im lặng.

**f. Phụ đề: rải theo khoảng có tiếng (đã có), tô màu từ tiếng Anh.** `align.spread_voiced` đo lệch trung bình ~70 ms,
không cần Whisper, đúng như ý tưởng. Thêm tham số `highlight` cho `subs.write_ass`: từ tiếng Anh in màu khác (mã màu
ASS inline), đồng thời thẻ chữ nhỏ «sorry = xin lỗi» hiện 2 giây khi từ đó được đọc lần đầu (drawtext như thẻ MẸO hiện
có). `.srt` xuất kèm để sửa tay.

**g. Nhân vật nhất quán: nói thẳng giới hạn.** Chỉ mô tả bằng chữ («thỏ trắng áo đỏ, mắt nâu») thì các model này giữ
được MÀU LÔNG / MÀU ÁO / phong cách Pixar khá ổn, nhưng khuôn mặt, tỉ lệ, kiểu áo đổi nhẹ giữa 8 ảnh. MVP chấp nhận mức
đó, giảm bằng: (1) mỗi ảnh tối đa 2 nhân vật (prompt generator chọn 1-2 nhân vật/cảnh, cả 4 vẫn xuất hiện trong truyện);
(2) khối mô tả nhân vật đặt ĐẦU prompt, nguyên văn từ character bible, không cho model viết lại; (3) cùng một khối
«phong cách» cố định cuối prompt; (4) seed khác nhau mỗi ảnh (seed cố định không giúp nhất quán giữa các cảnh khác nhau).
Sau MVP: huấn luyện LoRA nhân vật trên chính Kaggle từ 20-30 ảnh đẹp nhất đã sinh (schnell/Z-Image đều train LoRA được)
— đó mới là cách nhất quán thật sự.

**h. Đăng: dùng lại hàm đăng, nhưng vòng lịch riêng và Fanpage riêng.** `autopost.py` gắn chặt với «kênh nguồn → video
đã dùng → pending»; nhét truyện vào đó sẽ rối. Viết `kids_auto.py` nhỏ (~150 dòng) theo cùng mẫu: làm trước cho các khung
giờ trong 36 giờ tới, chỉ khi hàng đợi trống, xong thì `autopost.publish(..., when=khung giờ)`. Kênh trẻ em là Fanpage
KHÁC kênh Bếp Đong Đủ → cần `KIDS_FB_PAGE_ID` / `KIDS_FB_PAGE_TOKEN`, và `autopost.publish` nhận page/token làm tham số
(hiện đọc thẳng `config.FB_PAGE_ID`). Khung giờ cố định 08:00 và 19:00 (`KIDS_TIMES`), có thể học giờ vàng sau.

Ngoài ra: **không dùng bộ nhận diện Bếp Đong Đủ** (logo, âm hiệu, tông màu tự chọn, câu chốt) cho video truyện — bộ dựng
truyện là đường riêng, không đi qua `renderer.render` (vốn dựng video remake có thương hiệu bếp).

## 3. Kiến trúc và luồng

```
Botkeep (điều khiển, không GPU)                              Kaggle (một phiên GPU, không chứa key)
┌──────────────────────────────────────────────┐             ┌─────────────────────────────────────────────┐
│ kids_auto: còn khung giờ trống trong 36h?    │             │ rmvideo/kids/job.py (kind="kids")           │
│  └ chọn đề: bài học + bối cảnh + xung đột    │  spec JSON  │ 1. Qwen3-30B-A3B: truyện (190-230 chữ)      │
│    + 3-5 từ tiếng Anh (chưa dùng 30 ngày)    │ ──────────► │    → kiểm mã → 8 cảnh → 8 prompt → tiêu đề   │
│  └ tạo Job(mode="kids") vào hàng đợi         │             │    (viết lại ≤ 2 lần; giải phóng VRAM)       │
│ worker → flow/kids.process_kids              │             │ 2. schnell / Z-Image: 8 ảnh 832×1472 → 1080p │
│  └ kaggle_worker.kids_spec → produce()       │             │ 3. VieNeu: 8 đoạn giọng, đo độ dài           │
│  └ nhận output.mp4 + result.json + ảnh       │ ◄────────── │ 4. kế hoạch cảnh theo giọng, phụ đề (.ass)   │
│  └ QA mã: 60-88s, 1080×1920, có tiếng, srt   │  kết quả    │ 5. FFmpeg: zoompan + xfade + phụ đề + nhạc   │
│  └ ghi story_memory, giao (web/Telegram)     │             │    nền ducking + loudnorm + NVENC            │
│  └ đăng Reels hẹn giờ (autopost.publish)     │             │ → output.mp4, result.json, scene_01..08.png  │
└──────────────────────────────────────────────┘             └─────────────────────────────────────────────┘
```

- Model không chạy được trên Botkeep (1,5 vCPU, 2GB ổ) → **không có đường lùi về Botkeep** như remake. Kaggle lỗi / hết
  giờ GPU thì job báo lỗi, `kids_auto` bỏ khung giờ đó và báo admin; hết giờ GPU tuần thì tạm dừng tới thứ Bảy (đã có
  `kaggle_api.quota`).
- Trọng số model: HAI notebook chạy một lần giữ model, phiên dựng gắn cả hai bằng `kernel_sources` (đúng mẫu
  `rmvideo-phowhisper` đang dùng; đầu ra mỗi notebook tối đa 20GB):
  - `rmvideo-kids-llm`: Qwen3-30B-A3B-Instruct-2507 **lượng tử sẵn** — GGUF Q4_K_M (~18GB, của Unsloth/Qwen) cho
    llama.cpp, hoặc bản bnb-4bit sẵn của Unsloth (~17GB) cho transformers. KHÔNG lượng tử tại chỗ: phải nạp 60GB bf16,
    RAM Kaggle 31GB không đủ.
  - `rmvideo-kids-image`: FLUX.1-schnell NF4 hoặc Z-Image-Turbo (7-12GB).
  → không tải ~30GB mỗi phiên, không cần token Hugging Face trong notebook tự động (các model này không bị khoá tải).
- Bộ chạy model chữ trên T4 (SM 7.5): **llama-cpp-python** (wheel CUDA dựng sẵn, chia model lên 2 GPU bằng
  `tensor_split`, MoE chạy tốt trên GPU cũ, ước 15-25 token/giây) là lựa chọn chính; dự phòng transformers + bitsandbytes
  (bnb chạy được SM 7.5, nhưng MoE trong transformers chậm hơn). vLLM/GPTQ-Marlin KHÔNG dùng: Marlin cần SM 8.0, cài vLLM
  trên Kaggle hay vỡ phiên bản torch. Nạp xong sinh chữ, rồi giải phóng hẳn VRAM trước khi nạp model ảnh.
- Bộ nạp này viết chung (`rmvideo/kids/llm.py` → sau tách thành `rmvideo/services/local_llm.py`) để kênh bếp dùng lại
  khi làm K1 của `KE_HOACH_AI_KAGGLE.md`: bỏ hẳn Gemini cho cả hai kênh.

## 4. Thiết kế từng bước (đối chiếu 12 bước của ý tưởng)

| # | Ý tưởng | Làm ở đâu | Cách làm |
| --- | --- | --- | --- |
| 1 | Story generator (Qwen) | **Botkeep, mã** | `rmvideo/kids/pick.py`: chọn bộ ba + từ, tránh 30 ngày (mục 2.b) |
| 2 | Script generator | Kaggle, Qwen3-30B-A3B | Lời nhắc tiếng Việt: cấu trúc HOOK → VẤN ĐỀ → XUNG ĐỘT → GIẢI QUYẾT → BÀI HỌC → KẾT VUI, câu ngắn, 4 nhân vật đúng tên trong bible, mỗi từ tiếng Anh ≥ 3 lần, tuổi 3-8, không bạo lực/sợ hãi. bản Instruct-2507 không có chế độ suy nghĩ; ép JSON bằng grammar theo schema của llama.cpp (không còn JSON hỏng), kiểm lại bằng mã `{script, scenes[8]{text, characters[], action}}` — kịch bản và 8 cảnh trong MỘT lượt (bước 4 gộp vào) |
| 3 | Kiểm kịch bản bằng mã | Kaggle (ngay sau sinh) | 190-230 chữ; đủ 8 cảnh, mỗi cảnh 15-40 chữ; mỗi từ tiếng Anh xuất hiện ≥ 3 lần (không phân biệt hoa thường); đủ 4 tên nhân vật; không có từ cấm (danh sách ngắn); cảnh 1 có câu hỏi/câu cảm (hook); cảnh 7 chứa bài học; cảnh 8 kết vui. Lỗi → báo lý do cho model viết lại (tối đa 2 lần) → vẫn lỗi thì việc thất bại, Botkeep chọn đề khác |
| 4 | Scene splitter | gộp vào bước 2 | Mỗi cảnh = một đoạn giọng + một ảnh; không cần lượt riêng |
| 5 | Image prompt generator | Kaggle, Qwen (lượt 2, ngắn) | Model chỉ viết **phần hành động** bằng tiếng Anh cho mỗi cảnh (≤ 25 từ, 1-2 nhân vật, bối cảnh). Mã ghép: `[mô tả nhân vật từ bible] + [hành động] + [khối phong cách cố định: Pixar style, cute 3D cartoon, children storybook, bright colors, soft lighting, vertical composition]`. Không cho model đụng khối nhân vật và khối phong cách |
| 6 | Sinh ảnh | Kaggle, GPU | diffusers; schnell NF4 4 bước hoặc Z-Image-Turbo 8 bước; 832×1472; seed ngẫu nhiên ghi vào result; phóng lên 1080×1920 bằng ffmpeg lanczos; ảnh hỏng (đen/NaN) sinh lại 1 lần |
| 7 | TTS | Kaggle, VieNeu | Giọng `KIDS_VOICE` (mặc định Thục Đoan «kể chuyện»; Mỹ Duyên chậm hơn ~10%); `vi_text.normalize` cho số; **từ tiếng Anh để nguyên** vì VieNeu v3 Turbo là model song ngữ Anh-Việt (code-switching) — K0 nghe thử «sorry / thank you / friend / help / share»; từ nào đọc hỏng thì kho từ ghi kèm cách phiên âm (`"tts": "so-ri"`) |
| 8 | Phụ đề | Kaggle | `spread_voiced` theo khoảng có tiếng của từng đoạn; `.ass` cỡ chữ to hơn (trẻ em), tô màu từ tiếng Anh; `.srt` trả về Botkeep |
| 9 | Dựng | Kaggle, FFmpeg + NVENC | `rmvideo/kids/render.py`: mỗi cảnh zoompan (zoom_in/zoom_out/pan_left/pan_right ngẫu nhiên, không lặp kiểu 2 cảnh liền), xfade 0,5s (fade/dissolve/smoothleft…), độ dài cảnh theo giọng (mục 2.e); màn mở 1,5s (tên kênh + tiêu đề) và màn kết 2,5s (3 từ hôm nay + nghĩa); nhạc nền từ `assets/kids_bgm/` qua link tạm PUBLIC_URL, -18 dB dưới giọng + sidechain hạ thêm khi đang đọc; loudnorm 2 lượt; 1080×1920, 30 fps, H.264 High 4.1 |
| 10 | QA video bằng mã | Botkeep (sau tải về) + Kaggle (trước gửi) | 8 ảnh có thật, `result.json` đủ trường, ffprobe: 60 ≤ dài ≤ 88, 1080×1920, có luồng tiếng, srt không rỗng, loudness trong khoảng. Lỗi → dựng lại một lần (cùng truyện, cùng ảnh, seed hiệu ứng khác) → vẫn lỗi thì job lỗi |
| 11 | Metadata | Kaggle, Qwen (cùng lượt 2) | `{title, description, hashtags}`; mã kiểm: tiêu đề ≤ 90 ký tự có tên nhân vật chính + một từ tiếng Anh, mô tả có 3-5 từ + nghĩa + bài học, hashtag = 5 cố định + 2 theo bài học; `prompt_engine.platform_post` dựng bài đăng |
| 12 | Đăng | Botkeep | `kids_auto` + `autopost.publish(page, token, when)`; ảnh bìa = `scene_01.png` (thử `_set_cover`, Reels có thể không nhận) |

## 5. Dữ liệu và bộ nhớ

Đặt trong `rmvideo/kids/data/` (theo mã, gửi kèm bundle) và tệp trạng thái cạnh `autopost.json` (không vào git):

- `character_bible.json` — 4 nhân vật như ý tưởng, thêm trường `prompt_en` (mô tả nguyên văn dùng trong prompt ảnh) và
  `name_vi` («Thỏ Trắng», «Gấu Nâu», «Mèo Cam», «Sóc Nhỏ») để kịch bản gọi tên tiếng Việt nhất quán.
- `lessons.json`, `settings.json`, `conflicts.json` — danh sách như ý tưởng, mỗi mục có `vi` (dùng trong lời nhắc) và
  `en` (prompt ảnh). Có thể thêm mục dần trên web (sau MVP).
- `words.json` — **kho từ tiếng Anh** 80-120 từ cho tuổi 3-8, theo nhóm (cảm xúc, hành động, gia đình, đồ vật, màu, số…),
  mỗi từ: `{"en": "sorry", "vi": "xin lỗi", "group": "feeling", "lessons": ["apologizing"], "tts": ""}`. Chọn 3-5 từ
  theo bài học (ưu tiên nhóm khớp) + tránh bộ từ 30 ngày. Kho từ do bạn duyệt một lần → model không bịa từ khó.
- `kids_memory.json` (trạng thái, giống `autopost.json`): `stories[]` {`job`, `created_at`, `title`, `lesson`, `setting`,
  `conflict`, `english_words`, `fb_id`, `slot`, `views`} + `pending`, `paused`, `fails`. Chống trùng: bộ ba chưa dùng 30
  ngày; bộ từ không trùng ≥ 2/3 từ trong 30 ngày; tiêu đề không gần giống (`autopost._norm_title`).

## 6. Mã mới và chỗ sửa (theo quy ước repo, không theo cây thư mục `pipelines/` trong ý tưởng)

| Tệp | Vai trò |
| --- | --- |
| `rmvideo/domain/job.py` | `Mode.KIDS = "kids"` |
| `rmvideo/app/job_factory.py` | `kids(*, source, chat_id, brief)` — `brief` = đề đã chọn (bộ ba + từ), ghi vào Job (trường mới `brief: dict`) |
| `queue_manager.inputs_available` | mode kids không cần file → `True` (restart không báo «file không còn») |
| `jobs.job_budget` | kids: `KIDS_TIMEOUT` 125 phút + 10 phút (chờ máy tới 75 phút + chạy ~20 phút + dự phòng) |
| `rmvideo/app/flow/dispatch.py` | `Mode.KIDS → rmvideo/app/flow/kids.py` |
| `rmvideo/app/flow/kids.py` | Botkeep: gửi Kaggle, nhận, QA, ghi memory, `job.post`, `job.parts` (Video, Phụ đề, Ảnh bìa) |
| `rmvideo/kids/pick.py` | chọn đề + từ, chống trùng (thuần, test được) |
| `rmvideo/kids/validate.py` | kiểm kịch bản / cảnh / metadata bằng mã (thuần, chạy cả hai bên) |
| `rmvideo/kids/prompts.py` | lời nhắc Qwen + ghép prompt ảnh (chụp snapshot như `tests/test_snapshot_prompts.py`) |
| `rmvideo/kids/llm.py` | Kaggle: nạp Qwen3-30B-A3B-Instruct-2507 Q4_K_M bằng llama-cpp-python chia 2 GPU (dự phòng bnb-4bit + transformers), sinh JSON theo grammar, giải phóng VRAM |
| `rmvideo/kids/images.py` | Kaggle: diffusers schnell / Z-Image, phóng ảnh |
| `rmvideo/kids/render.py` | Kaggle: kế hoạch cảnh theo giọng + FFmpeg (dùng `slideshow.zoompan_expr`, `_prep`, `encode_args`, `two_pass_loudness`) |
| `rmvideo/kids/job.py` | Kaggle: chuỗi 1→5, trả result.json; `kaggle_job.run_one` gọi khi `kind == "kids"` |
| `kaggle_worker.kids_spec` + `PIP_KIDS` | spec (bible, đề, từ, giọng, link nhạc, cài đặt) + `pip install diffusers bitsandbytes accelerate` |
| `subs.write_ass(highlight=...)` | tô màu từ tiếng Anh |
| `autopost.publish(..., page=None, token=None)` | tham số hoá Page (mặc định như cũ) |
| `kids_auto.py` | vòng lịch 2 khung/ngày, làm trước 36 giờ, đăng hẹn giờ, báo admin, lệnh `kids_now` / `kids_status` / `kids_resume` |
| `rmvideo/app/settings.py` | nhóm `kids`: `KIDS_AUTO`, `KIDS_TIMES`, `KIDS_VOICE`, `KIDS_IMAGE_MODEL`, `KIDS_LLM_PATTERN`, `KIDS_LLM_HF`, `KIDS_LLM_THINK`, `KIDS_KAGGLE_SOURCES`, `KIDS_CHANNEL_NAME`, `KIDS_WORLD_NAME`, `KIDS_TAGLINE`, `KIDS_HASHTAGS`, `KIDS_BGM_LINKS`; nhóm `api`: `KIDS_FB_PAGE_ID` (reauth), `KIDS_FB_PAGE_TOKEN` (secret); tiền tố `KIDS_` vào `SNAPSHOT_EXCLUDE_PREFIXES` |
| `main.py`, `admin.py` | `spawn(kids_auto.run_forever())`, lệnh console |
| `static/index.html` | nút «🧸 Truyện bé: làm ngay» + bảng truyện đã làm (sau MVP cũng được; MVP dùng console + tự động) |
| `tests/test_kids.py` | chọn đề/chống trùng, kiểm kịch bản (mẫu đúng/sai), ghép prompt, kế hoạch cảnh theo giọng, dựng thật bằng ảnh `testsrc2` + giọng giả (ffmpeg), QA, Kaggle giả (theo `test_kaggle.py`) |

## 7. Thời gian đo thật (18/11/2026, 2 × T4, lần chạy thứ 7)

| Bước | Đo thật | Ghi chú |
| --- | --- | --- |
| Chờ máy Kaggle | 0-60 phút (giờ cao điểm) | ngoài tầm; `KIDS_TIMEOUT` 125 phút, chừa 50 phút chạy -> chờ tối đa 75 phút |
| Khởi động: giải nén bundle, pip (diffusers, bitsandbytes, vieneu…) | ~35 giây | llama-server lấy từ notebook `rmvideo-kids-models` (không build lại) |
| Tải Qwen3.6-35B-A3B Q4_K_M (20,2 GB) từ Hugging Face | 97 giây | ~200 MB/s, nhanh hơn đọc từ ổ Kaggle gắn vào |
| llama-server nạp model lên 2 GPU | 96 giây | |
| Sinh chữ | 14-16 giây/lượt, thường 2 lượt | lượt 1 hay quá dài/ngắn, lượt 2 đạt |
| Nạp FLUX.1-schnell NF4 (14 GB) từ `/kaggle/input` | 420-512 giây | chậm nhất; ổ gắn vào chỉ ~30 MB/s |
| Vẽ 8 ảnh 768×1344, 4 bước | 31 giây/ảnh = 4,2 phút | T5 đã bỏ khỏi GPU sau khi mã hoá prompt |
| VieNeu: nạp + đọc 8 đoạn + câu ôn từ | ~33 giây | |
| Dựng FFmpeg 89 giây 1080×1920 (NVENC) | 94 giây | |
| **Một video** | **~19,5 phút GPU** (+ chờ máy) | |
| 2 video/ngày | ~4,5 giờ GPU/tuần | Kaggle cho 30 giờ; kênh nấu ăn đang dùng ~3-5 giờ |

Bài học đo được: 236 chữ -> video 88,9 giây (trượt QA 60-88 nửa giây) -> `render.plan` tự co đệm rồi đọc nhanh hơn
(≤ ×1,15) để không quá 87 giây; lời nhắc nhắm 185-225 chữ.

## 8. Rủi ro

| Rủi ro | Mức | Cách giảm |
| --- | --- | --- |
| Qwen3-30B-A3B viết tiếng Việt cho trẻ em chưa hay (câu lủng củng, từ Bắc, lặp ý) | Trung bình | K0 chấm mù 3 truyện (bản Gemini chỉ làm MỐC so sánh, không dùng vận hành); bộ kiểm mã + sửa từ miền Bắc bằng mã (đã có cách ở `KE_HOACH_AI_KAGGLE.md`); sinh 2-3 bản chọn bản điểm cao nhất (best-of-N, MoE sinh nhanh nên rẻ); 5-10 truyện mẫu bạn duyệt đưa vào lời nhắc (few-shot) |
| Model 30B trên T4: Q4_K_M 18GB phải chia 2 GPU, wheel llama-cpp-python CUDA không khớp CUDA của Kaggle, hoặc tốc độ < 8 token/giây | Trung bình | K0 đo thật cả llama.cpp lẫn bnb-4bit; ghim phiên bản wheel; chậm quá thì Q3_K_M (~14GB) hoặc tắt best-of-N; đường cuối Qwen3-14B dense 4-bit (~9GB, một GPU) |
| Nhân vật đổi dáng giữa các cảnh | Cao | Mục 2.g; LoRA nhân vật sau MVP |
| Giấy phép model ảnh | Trung bình | schnell / Z-Image (Apache-2.0); không dùng FLUX.1-dev nếu chưa mua giấy phép |
| VieNeu đọc sai từ tiếng Anh | Trung bình | Model song ngữ; K0 nghe thử; kho từ có ô phiên âm dự phòng |
| Hết giờ GPU tuần / Kaggle chờ máy lâu / Kaggle đổi môi trường | Trung bình | Không có đường lùi Botkeep → tạm dừng và báo; làm trước 36 giờ; ghim phiên bản thư viện; notebook giữ model không phụ thuộc Hugging Face |
| T4 không bf16: Z-Image / FLUX chạy fp16 có thể ra ảnh NaN | Trung bình | K0 kiểm; NF4 + fp16 cho FLUX đã có người chạy trên Colab T4; ảnh đen thì sinh lại, 2 lần hỏng thì đổi model |
| Facebook: nội dung AI, hashtag, kênh mới ít tiếp cận | Thấp | Ghi «truyện do AI tạo» trong mô tả; không cam kết lượt xem ở MVP |
| Kaggle xem việc chạy kênh nội dung là lạm dụng | Đã ghi ở `KAGGLE_PLAN.md` | Giữ 2 video/ngày, không chạy liên tục |
| Notebook > 1MB khi bundle lớn lên | Thấp | `kernel_text` đã kiểm; data json nhỏ (< 30KB) |

## 9. K0 — thử trước khi code (điểm dừng, ~1,5 ngày công, ~3 giờ GPU)

Một notebook tay trên Kaggle (2 × T4), chưa đụng mã bot:
1. **Chữ:** Qwen3-30B-A3B-Instruct-2507 Q4_K_M qua llama.cpp (chia 2 T4) và bản bnb-4bit qua transformers: đo thời gian
   nạp, VRAM, token/giây; viết 3 truyện theo 3 đề cho sẵn, đúng lời nhắc dự kiến; chấm bằng bộ kiểm mã; bạn chấm mù 1-5
   cùng bản Gemini làm mốc (chỉ để so, không dùng vận hành).
1b. **Nhận diện kênh:** sinh thử avatar + ảnh bìa Page (4 bạn trên đồi cỏ) và màn mở/kết bằng model ảnh đã chọn, với tên
   kênh bạn chốt ở mục 12.
2. **Ảnh:** cùng 8 prompt (1 truyện) sinh bằng FLUX.1-schnell NF4 và Z-Image-Turbo; đo giây/ảnh, VRAM; bạn chấm mù: đẹp,
   đúng phong cách Pixar, nhân vật có giống nhau giữa các cảnh không.
3. **Giọng:** Thục Đoan + Mỹ Duyên đọc 1 kịch bản có 4 từ tiếng Anh; nghe từ tiếng Anh có rõ không; đo chữ/giây.
4. **Dựng tay** 1 video từ 8 ảnh + giọng bằng FFmpeg để xem tổng thể (có thể dùng ngay `slideshow` với nhạc giả).
Kết quả K0 chốt: model ảnh, model chữ (8B hay lớn hơn / Gemini), giọng, số chữ mục tiêu.

## 10. Lộ trình sau K0 (~6-7 ngày công)

| Bước | Nội dung | Công |
| --- | --- | --- |
| K1 | Phần Kaggle: `rmvideo/kids/` (llm, images, render, job), notebook giữ model, `kids_spec`, test dựng thật bằng ảnh giả | 2 ngày |
| K2 | Phần Botkeep: `Mode.KIDS`, `pick` + memory + `validate`, flow, QA, cài đặt, lệnh console, thông báo; chạy 2 video thật end-to-end | 1,5 ngày |
| K3 | `kids_auto` + Fanpage riêng + ảnh bìa + bài đăng; chạy thử 3 ngày, soát video như đã làm với kênh bếp | 1,5 ngày |
| K4 | Nút trên web, bảng truyện đã làm, sửa theo soát; tài liệu README | 1 ngày |
| (sau) | LoRA nhân vật; học giờ vàng từ lượt xem; YouTube Shorts (chỉ thêm uploader, phần còn lại dùng chung) | 1-2 ngày |

## 11. Cần bạn quyết (còn lại)

Đã chốt: model chữ Qwen3-30B-A3B trên Kaggle (không Gemini), Fanpage riêng, 4-6 giờ GPU/tuần.

1. **Model ảnh:** đồng ý thay FLUX.1-dev bằng FLUX.1-schnell / Z-Image-Turbo (Apache-2.0) và chọn theo K0? Hay bạn sẽ mua
   giấy phép dev?
2. **Tên kênh:** chọn một trong mục 12 (hoặc sửa). Tên thế giới truyện «Đồi Cỏ Non» dùng chung cho mọi phương án.
3. **Giọng kể:** Thục Đoan (kể chuyện, nhanh hơn ~10%) hay Mỹ Duyên (đọc truyện, chậm, ấm); hay nhân bản giọng.
4. **Nhạc nền:** 5-10 bài nhạc vui trẻ em có giấy phép dùng thương mại (Pixabay, Mixkit…) tải lên thư mục nhạc trẻ em.
5. **Duyệt hay tự động hoàn toàn:** MVP tự động 100% như ý tưởng; hay tuần đầu dừng ở bước «chờ duyệt kịch bản» trên
   web (đã có cơ chế `REVIEW_SCRIPT`) để bạn soát lời trước khi tốn GPU sinh ảnh?

## 12. Tên kênh và thế giới truyện (đề xuất)

Tiêu chí: 2-4 âm tiết, trẻ 3 tuổi đọc theo được, nói lên «kể chuyện + bài học + tiếng Anh», làm hashtag không dấu không
trùng, và tìm trên web ngày 18/11/2026 chưa thấy kênh / sách nào mang đúng tên (chưa kiểm được trên Facebook: khi tạo
Page hãy gõ thử tên vào ô tìm kiếm Facebook trước).

| Tên | Hashtag | Ưu | Nhược |
| --- | --- | --- | --- |
| **Kể Bé Nghe** (đề xuất chính) | #kebenghe | 3 âm tiết, đúng lời ba mẹ nói mỗi tối; vừa là tên vừa là lời mời («Kể Bé Nghe: Thỏ Trắng làm vỡ bình hoa»); đọc rõ, nhớ lâu | Cần tagline để nói phần tiếng Anh |
| **Đồi Cỏ Non** | #doiconon | Gợi hình, thơ, dễ vẽ logo (ngọn đồi xanh + 4 bạn); dùng luôn tên thế giới truyện | Chưa nói «truyện / tiếng Anh» |
| **Bốn Bạn Nhỏ** | #bonbannho | Nhấn 4 nhân vật, dễ nhớ | Chung chung, khó tìm kiếm |
| **Chuyện Nhỏ Lớn Khôn** | #chuyennholonkhon | Nêu rõ bài học, có vần | 4 âm tiết, hashtag dài |
| **Thỏ Trắng Kể Chuyện** | #thotrangkechuyen | Nhân vật chính dẫn chuyện (kiểu Peppa Pig) | Nhiều kênh «thỏ» / «kể chuyện» na ná; đổi nhân vật chính là kẹt tên |

**Đề xuất chốt:** tên kênh **Kể Bé Nghe**, thế giới truyện **Đồi Cỏ Non** (nơi Thỏ Trắng, Gấu Nâu, Mèo Cam, Sóc Nhỏ sống).
- Tên Page: «Kể Bé Nghe – Truyện & tiếng Anh cho bé»; username `@kebenghe`.
- Tagline (mô tả Page + màn kết): «Mỗi ngày một chuyện nhỏ ở Đồi Cỏ Non, học vài từ tiếng Anh».
- Hashtag cố định: `#kebenghe,#doiconon,#truyenchobe,#hoctienganhchobe,#kidsstory` (+2 theo bài học).
- Tên thế giới đi vào mã: lời nhắc («mọi chuyện xảy ra ở Đồi Cỏ Non»), khối phong cách prompt ảnh («green grassy hill
  village»), màn mở đầu 1,5 giây («Đồi Cỏ Non · Kể Bé Nghe»), màn kết (3 từ hôm nay + tagline). Thế giới có tên làm
  mọi truyện cùng một bối cảnh → thương hiệu nhất quán, và ảnh các cảnh cùng «chất» hơn.
- Cài đặt: `KIDS_CHANNEL_NAME`, `KIDS_WORLD_NAME`, `KIDS_TAGLINE`, `KIDS_HASHTAGS` (đổi được trên web, không sửa mã).

## 13. Fanpage riêng: việc cần làm

1. Tạo Page mới (facebook.com/pages/create), hạng mục Giáo dục / Giải trí, tên theo mục 12, đặt username; tải avatar + ảnh bìa
   sinh ở K0 (1b).
2. Lấy token y như hướng dẫn «Lấy `FB_PAGE_TOKEN`» trong README (cùng app Facebook, cùng token người dùng dài hạn): gọi
   `me/accounts` sẽ liệt kê CẢ HAI Page → lấy `id` + `access_token` của Page mới. Quyền: `pages_show_list`,
   `pages_read_engagement`, `pages_manage_posts` (đủ cho Reels API như kênh bếp đang dùng).
3. Điền `KIDS_FB_PAGE_ID` / `KIDS_FB_PAGE_TOKEN` ở Cài đặt → 🔑 Tài khoản & API (nhập lại mật khẩu web như `FB_PAGE_ID`);
   nút «Thử token» riêng (`kids_fb_test`) in tên Page để không nhầm với Page bếp.
4. Trong mã: `autopost.publish(..., page=, token=)` tham số hoá (mặc định như cũ cho kênh bếp); `kids_auto` luôn truyền Page
   trẻ em; token Page trẻ em không bao giờ đi vào notebook Kaggle (tiền tố `KIDS_FB_` vào `SNAPSHOT_EXCLUDE_PREFIXES`).
5. Lượt xem đọc riêng theo Page (`views_of` dùng token của Page đó) để sau này học giờ vàng cho kênh trẻ em.

## Nguồn đã kiểm (18/11/2026)

Bổ sung 19/11 (kể cho cuốn, học từ sách tranh): Rachel Bachman, «What Children Actually Want From Picture Books»
(bachmanrachel.substack.com) — trẻ muốn tham gia, lặp lại có nhịp, hài; Writer's Digest, bài về điệp khúc (refrain) trong sách
tranh — điệp khúc lặp rồi đảo ở cuối; Annie Parsons, «5 ways to make a picture book interactive» (annieparsonsart.substack.com) —
hỏi trẻ, cho trẻ đoán trước khi lật trang; neolemon.com, bài về «page turn» — treo câu hỏi ở cuối trang, trang sau trả lời.
Mẫu đối chiếu: The Gruffalo (nhịp + lặp), We're Going on a Bear Hunt (điệp khúc hát theo, tiếng động), truyện mầm non Việt Nam
(Chú Dê Đen, Ai đáng khen nhiều hơn).

- Giấy phép FLUX.1 [dev] (điều khoản đầu ra và «hoạt động tạo doanh thu»): https://scancode-licensedb.aboutcode.org/flux-1-nc.html ;
  thảo luận trên Hugging Face: https://huggingface.co/black-forest-labs/FLUX.1-dev/discussions/7 ;
  giấy phép dev thương mại tự host của BFL: https://help.bfl.ai/articles/9272590838-self-serve-dev-license-overview-pricing
- Z-Image-Turbo (6B, Apache-2.0, 8 bước, ~16GB VRAM, diffusers): https://comfyui-wiki.com/en/models/z-image/z-image-turbo ;
  https://huggingface.co/mrfakename/Z-Image-Turbo/blob/main/README.md
- FLUX.1-dev 4-bit (bitsandbytes/diffusers, chạy được Colab miễn phí): https://huggingface.co/diffusers/FLUX.1-dev-bnb-4bit
- VieNeu-TTS v3 Turbo song ngữ Anh-Việt (code-switching), sea-g2p: https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo ;
  https://pypi.org/project/vieneu/
- Qwen3-30B-A3B: ~17GB ở INT4, bảng tốc độ chính chủ (GPTQ-Int4 trên SGLang/Marlin 120 token/giây, không phải T4):
  https://qwen.readthedocs.io/en/latest/getting_started/speed_benchmark.html ; ước VRAM của bên thứ ba:
  https://dev.co/ai/llms/qwen3-30b-a3b-gptq-int4 — chưa có số đo trên 2 × T4, K0 phải đo.
- Tên kênh: tìm «Kể Bé Nghe», «Đồi Cỏ Non», «Rừng Nhỏ Kể Chuyện», «Chuyện Nhỏ Lớn Khôn», «Bốn Bạn Nhỏ» ngày 18/11/2026
  không thấy kênh / sách trùng tên (gần nhất: podcast «Kể Chuyện Bé Nghe» của Hello Bacsi, khác tên).
