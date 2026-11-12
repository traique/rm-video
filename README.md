# Video Bot — Botkeep.cloud: Telegram + Web Panel

Nhận link video → tải (yt-dlp) → Groq Whisper bóc lời thoại → Gemini viết hook + kịch bản khớp thời lượng → edge-tts giọng Việt → FFmpeg render 720x1280 (ducking + loudnorm, nhìn/nghe như video thật). Điều khiển qua Telegram **hoặc** link web Botkeep cấp.

## Triển khai trên Botkeep

1. Upload toàn bộ thư mục này (Python Runtime **3.11 trở lên**; 3.12 khuyên dùng), lệnh chạy: `python main.py`.
   `pip install -r requirements.txt` cài đúng các phiên bản đã kiểm (~350MB). **YouTube** cần thêm bộ giải JavaScript:
   gõ `setup_youtube` vào console (cài deno ~100MB vào `tools/`, gỡ bằng `remove_youtube`); TikTok/Facebook không cần.
   File `.config/pip/pip.conf` tắt bộ nhớ đệm của pip (Botkeep chạy với HOME = thư mục bot, nên bộ nhớ đệm ~150–250MB
   tính vào ổ 2GB).
2. Biến môi trường **bắt buộc cho bảo mật**:
   - `WEB_PASSWORD` — **bắt buộc**. Thiếu thì web panel KHOÁ mọi chức năng (link web Botkeep là công khai).
   - `BOT_TOKEN` — token @BotFather (bỏ trống = chạy chỉ-web).
   - `ALLOWED_USERS` — chat_id Telegram được dùng bot, cách nhau dấu phẩy (đặt được cả trong Cài đặt web).
     Chưa đặt thì bot từ chối mọi người; nhắn bot một tin là nó báo chat id của bạn.
   - `ADMIN_CHAT_ID` — nơi nhận cảnh báo dịch vụ ngoài hỏng (trống = người đầu tiên trong `ALLOWED_USERS`).
3. Biến môi trường khác (key AI nhập sau từ web cũng được):
   - `GEMINI_API_KEY` — viết kịch bản (thiếu thì dùng kịch bản mẫu)
   - `GROQ_API_KEY` — Whisper bóc lời thoại gốc. Không có thì bóc bằng Gemini `gemini-3.5-transcribe` (dùng chung
     `GEMINI_API_KEY`, mốc từng từ, ~1.5s cho 11s audio), cuối cùng mới tới STT CapCut. Thứ tự đổi bằng `ASR_ORDER`.
   - `OPENROUTER_API_KEY` — provider AI thứ 2, tự dùng khi Gemini thiếu key hoặc lỗi; `OPENROUTER_MODEL` (mặc định auto)
   - `OPENROUTER_BASE_URL`, `POT_SERVER` — **chỉ đặt qua biến môi trường** (web không sửa được: lộ mật khẩu web
     không kéo theo lộ key bằng cách trỏ base URL sang máy lạ)
   - `YTDLP_COOKIES`, `YTDLP_PROXY` — vượt chặn YouTube (xem dưới)
   - `ALLOWED_DOMAINS` — thêm tên miền được phép gửi link (mặc định: TikTok, Douyin, YouTube, Facebook, Instagram, SoundCloud)
   - `FFMPEG_THREADS` (0 = theo số lõi container), `CACHE_MAX_MB` (mặc định 500), `HEALTH_CHECK_HOURS` (mặc định 6, 0 = tắt)
   - `PORT` — Botkeep thường tự cấp; mặc định 8080
4. ffmpeg: dùng bản có sẵn trên máy; không có thì static-ffmpeg tự tải ~70MB (1 lần).

### Lỗi `No space left on device` khi cài thư viện

Ổ Botkeep 2GB đầy nên pip không cài được và bot không khởi động (lệnh console chưa dùng được). Dọn bằng **File
Manager** của Botkeep, theo thứ tự (an toàn trước):
1. `.cache/` — bộ nhớ đệm pip, xoá thoải mái (thường 150–300MB).
2. `cache/`, `work/` — tạo lại được.
3. `outputs/` — video kết quả cũ (tải về trước nếu cần).
4. Gói tuỳ chọn không dùng tới: `models/` (gói offline ~185MB, cài lại bằng `setup_offline`), `tools/` (Node + PO token
   ~300MB và deno ~100MB, cài lại bằng `setup_potoken` / `setup_youtube`), `potoken/`.
Đừng xoá `jobs.db`, `settings.json`, `.session_key`, `assets/logo.png`. Bot chạy lại được thì gõ `status` xem thư mục
nào chiếm chỗ, `clean` để xoá bộ nhớ đệm pip + cache; lúc khởi động bot tự cảnh báo khi ổ còn < 400MB.

## Gói offline (tuỳ chọn)

Gõ `setup_offline` vào console (~185MB, kiểm SHA-256; gỡ bằng `remove_offline`), khởi động lại bot:
- **Tách giọng nhanh Spleeter**: tự dùng khi người gốc nói > 45s (trước đây phải tắt tiếng nền lúc người gốc nói).
  Nhanh hơn Kim_Vocal_2 ~30 lần trên 1 lõi; tiếng nền kém tự nhiên hơn chút nên video ngắn vẫn dùng Kim.
- **Dò chữ in cứng bằng OCR** (`TEXT_DETECT=ocr`): bắt cả chữ màu không viền; ~8s cho 60s video.
- **Giọng offline Piper** (`TTS_OFFLINE=1`, mặc định tắt): tầng cuối khi CapCut và edge-tts đều lỗi. Giọng được tinh
  chỉnh từ dữ liệu chỉ cho nghiên cứu: cân nhắc trước khi dùng cho video kiếm tiền.

## Bảo mật

- **Telegram**: chỉ người trong `ALLOWED_USERS` tạo được job; người lạ nhận câu từ chối kèm chat id của họ.
- **Web**: đăng nhập một lần bằng mật khẩu → cookie phiên ký HMAC (HttpOnly, SameSite=Strict, 30 ngày, sống qua
  restart; đổi `WEB_PASSWORD` là mọi phiên cũ hết hiệu lực). Sai 5 lần/phút bị khoá tạm. POST từ trang khác bị chặn
  (CSRF). Không còn nhận mật khẩu qua `?auth=` hay lưu trong trình duyệt. Script/curl dùng header `X-Auth`.
- **Link**: chỉ nhận link của các nền tảng trên; link trỏ vào IP nội bộ (127.0.0.1, 10.x, 169.254.169.254...) bị chặn;
  yt-dlp tắt extractor "generic" (tải được mọi URL bất kỳ).
- **Model và mã tải về**: model tách giọng kiểm SHA-256; server PO token build đúng tag phiên bản plugin, `npm ci`.
- `settings.json` (chứa key) ghi quyền 600; `/health` công khai chỉ trả `ok` và số job chờ.

## Web panel (link web của Botkeep)

- Đăng nhập, rồi dán link video **hoặc tải file video lên trực tiếp** để remake, chọn chế độ / thời lượng / giọng.
- **⚙️ Cài đặt ngay trên web**: key, model, cookies YouTube, giọng đọc, `ALLOWED_USERS`, `ALLOWED_SOURCES`... lưu vào
  `settings.json`, áp dụng ngay. Bỏ trống = không đổi, nhập `-` = xóa về biến môi trường. Key hiển thị dạng che bớt.
- Theo dõi hàng đợi theo thời gian thực (Server-Sent Events `/api/events`, mất kết nối thì tự quay về hỏi lại);
  tải video kết quả, phụ đề `.srt` và `timeline.json` (từng đoạn: thời điểm, cờ tiếng gốc, lời gốc, lời mới, chỗ
  giọng thật sự nằm) để dựng tay trong CapCut/Premiere.
- `/health` cho uptime-check.

## Telegram

`/start` → gửi link **hoặc gửi thẳng file video đính kèm (≤ 20MB, giới hạn Bot API)** → chọn chế độ (🍲/📦) → chọn thời lượng → nhận output.mp4, caption là content đăng luôn.

## Giọng đọc (mặc định: Hoài My của edge-tts)

24 giọng Việt của CapCut (Ban Mai, Review Phim, Giọng Nữ Phổ Thông, Nam Trầm...) qua gói `capcut_tts_api` (MIT, đóng kèm trong thư mục `capcut_tts_api/`, không cần đăng nhập). Giọng CapCut không trả mốc từng từ: phụ đề rải chữ theo các khoảng có tiếng của chính giọng đã tạo (lệch ~70ms, không gọi mạng); `SUB_TIMING=stt` để nghe lại track bằng STT CapCut như bản cũ (lệch ~85ms, chậm hơn). Giọng edge-tts có mốc từng từ thật. Đây là API không chính thức, có thể đổi bất cứ lúc nào: lỗi gì cũng tự rơi về edge-tts, job không hỏng. Chọn giọng cụ thể trong dropdown web; giọng mặc định nam/nữ và provider (`TTS_PROVIDER=capcut|edge`) sửa được trong Cài đặt.

Mặc định `TTS_PROVIDER=edge`, giọng Hoài My (`vi-VN-HoaiMyNeural`) cho mọi chế độ. edge-tts lỗi (thử 6 lần) thì
riêng đoạn đó đọc bằng giọng nữ CapCut trong Cài đặt (`CAPCUT_VOICE_FEMALE`, mặc định Ban Mai). CapCut có liệt kê
"Hoai My" / "Nam Minh" nhưng gọi thì lỗi `TTSInvalidSpeaker` (thử 11/2026) nên không dùng được làm dự phòng.
Cả video đọc cùng một tốc độ (tối đa 1.12x), từng đoạn chỉ lệch ≤5%; lời gốc bị máy nghe cắt vụn được gộp thành câu
trọn trước khi AI viết lại.

## 🤖 Tự động đăng Fanpage (kênh nguồn → làm lại → giờ vàng)

Bật trong Cài đặt: `AUTO_POST=1`, `AUTO_SOURCES` (link/tên kênh Facebook nguồn, cách nhau dấu phẩy),
`FB_PAGE_ID`, `FB_PAGE_TOKEN`; tuỳ chọn `AUTO_PER_DAY` (mặc định 2), `AUTO_TIMES` (trống = tự chọn giờ vàng),
`AUTO_MODE` (cook | review). Theo dõi ở ô **🤖 Tự động đăng Facebook** trên web hoặc lệnh console `auto`.

- Mỗi lần lấy NGẪU NHIÊN một video mới của kênh nguồn (đọc tab Reels + Video của kênh, không cần đăng nhập: ~10 video
  mới nhất mỗi tab). Video đã dùng không bao giờ dùng lại; video quá 5 phút / không phải của chủ kênh bị bỏ qua.
- Chỉ thêm job khi hàng đợi trống (video bạn gửi luôn làm trước), làm sẵn cho các khung giờ trong 36 giờ tới rồi
  **hẹn giờ đăng phía Facebook**, nên máy chậm hay bot khởi động lại không làm lỡ giờ.
- ≤ 90 giây đăng thành Reels, dài hơn đăng thành video. Nội dung bài = tiêu đề + caption + hashtag (không ghi nguồn).
- Giờ vàng: ban đầu 11:30 và 20:30 (giờ Việt Nam). Mỗi bài sau 24 giờ được đọc lượt xem; khung nào trung bình cao hơn
  được chọn nhiều hơn, thỉnh thoảng thử khung khác. Hai khung trong ngày cách nhau ≥ 3 giờ.
- Lỗi token (hết hạn, thiếu quyền) thì dừng và báo Telegram admin; sửa token rồi gõ `auto_resume` (hoặc bấm nút trên web).

Lấy `FB_PAGE_TOKEN` (token Page không hết hạn):
1. developers.facebook.com → My Apps → tạo app (loại Business).
2. Tools → Graph API Explorer: chọn app, **Get User Access Token** với quyền `pages_show_list`,
   `pages_read_engagement`, `pages_manage_posts` → Generate.
3. Tools → Access Token Debugger: dán token → **Extend Access Token** (token người dùng 60 ngày).
4. Trong Graph API Explorer dùng token 60 ngày đó gọi `me/accounts`: lấy `id` (= `FB_PAGE_ID`) và `access_token`
   (= `FB_PAGE_TOKEN`) của Fanpage. Token Page lấy từ token người dùng dài hạn không hết hạn.
5. Dán vào Cài đặt, bấm **🔑 Thử token Fanpage** (hoặc gõ `fb_test`).

Lưu ý: Facebook giảm hiển thị (và có thể tắt kiếm tiền) với nội dung đăng lại không phải của mình. Chỉ dùng kênh nguồn
bạn có quyền dùng lại; bot đã viết lại lời, đổi giọng, đổi hình nhưng không thay được quyền tác giả.

## Chuẩn kỹ thuật đầu ra (TVC / nền tảng)

H.264 High 4.1, yuv420p, bt709, 30 fps CFR, GOP 2s, faststart; AAC 192 kbps **48 kHz**; loudness **−14 LUFS, true peak −1 dBTP** (alimiter); fade in 0.3s / out 0.4s hình + tiếng; phụ đề trong vùng an toàn (cách đáy ~26%, không bị thanh công cụ TikTok/Reels che). `QUALITY` trong Cài đặt: `fast` (superfast, crf 23), `balanced` (mặc định: veryfast, crf 20, 1080p ~9 Mbps), `high` (chuẩn TVC: medium, crf 18, làm nét thích ứng `cas`, chậm ~2x). Âm lượng ra đúng −14 LUFS, đỉnh ≤ −1 dBFS ngay từ render (alimiter `level=false`; có test chặn tái phát).

**Hiệu năng** (video 87s 1080p 50fps, 1 lõi CPU, AI giả lập): 512s → 334s. Bộ lọc chạy sau `fps=30` (nguồn TikTok 50-60fps); giữ NGUYÊN công thức màu đã duyệt (eq + vibrance + colortemperature: vibrance chừa vùng đã đậm, hợp món chiên), chỉ thay `cas` bằng `unsharp` ở balanced/fast (SSIM 0.984-0.988 so với bản cũ, nhanh hơn bản cũ 24-28%); dò cảnh + ảnh cho AI + dò chữ gộp một lượt giải mã; các lô AI chạy song song. Đã thử và LOẠI: màu tính trên YUV (eq saturation 1.2, làm món chiên cam loang) và LUT 3D (chậm hơn bản gốc).

## Trình tải video (kiểu vidssave)

Card "⬇️ Trình tải video" trên web: dán link YouTube/TikTok/Facebook → **Xem định dạng** hiện ảnh bìa, tiêu đề, thời lượng và các nút MP4 2160p→360p kèm dung lượng ước tính + MP3; bấm là tải vào hàng đợi, xong hiện link. Telegram: gửi link → "⬇️ Chỉ tải về" → bàn phím chọn chất lượng/MP3; file ≤48MB gửi thẳng (MP3 gửi dạng audio), lớn hơn gửi link. Giới hạn 60 phút / 450MB; % tải hiện trong bước của job. Nhãn theo cạnh ngắn (video dọc 1080×1920 = 1080p). **Lệnh console Botkeep**: gõ thẳng vào ô console của server (bot đọc stdin): `help`, `status` (kèm tỉ lệ rơi dự phòng 24 giờ), `health` (kiểm tra dịch vụ ngoài ngay), `update_ytdlp` (cập nhật yt-dlp, thử YouTube trong tiến trình riêng, hỏng thì tự quay về bản cũ; xong thì khởi động lại bot), `setup_potoken`, `remove_potoken`, `yt_test <link>` (xem + tải thật), `cancel <số job>`.

**YouTube bị chặn (403 / đòi đăng nhập)?** Ba cách theo thứ tự nên thử: (1) **không cần cookies**: gõ `setup_potoken` vào console Botkeep — bot tự tải Node qua pip nếu máy không có, cài bộ sinh PO token bgutil (~270MB, ~1 phút), dùng ngay không cần restart; rồi `yt_test <link>` để thử tải thật. Không ăn thì `remove_potoken` để lấy lại đĩa; (2) điền `YTDLP_PROXY` (proxy dân dụng, `http://user:pass@ip:port` hoặc `socks5://...`) trong Cài đặt, hoặc `POT_SERVER` trỏ tới server token chạy ở máy khác; (3) dán cookies YouTube vào `YTDLP_COOKIES`. Nếu IP máy chủ bị YouTube gắn cờ quá nặng thì PO token cũng không đủ, khi đó chỉ còn cookies. TikTok dùng tikwm nên không bị ảnh hưởng.

## Vận hành

- **Cắt quảng cáo của người gốc** (`CUT_PROMO=1`): đoạn cuối video giới thiệu sản phẩm / giỏ hàng / kêu gọi mua (dò trên lời gốc, chịu lỗi máy nghe như "chào hàng"=giỏ hàng) bị cắt bỏ cả hình lẫn tiếng. Chỉ cắt khi đoạn đó nằm ở nửa sau và kéo tới hết video, ≥40% câu có dấu hiệu bán hàng.
- **Tốc độ khung**: nguồn ≤30fps giữ nguyên; >30 chia đều (50→25, 60→30) — ép 50→30 hoặc 25→30 gây giật.
- **Nguồn nhỏ** (vd 360×640): khử nhiễu + chỉnh màu ở kích thước gốc rồi mới phóng to (render nhanh ~1.7x).
- **Tiếng nền** khi không tách giọng AI: tắt/bật dần 0.15s, gộp các lần người gốc nói cách nhau <0.8s (hết chập chờn); không giữ lớp nền lúc người gốc nói vì đo được vẫn lọt lời.
- Gemini dùng chế độ JSON; JSON hỏng vẫn cứu từng đoạn thay vì bỏ cả lượt viết lại.

- **Dung lượng**: khi khởi động và lệnh `status` in ổ bot + `/tmp` còn trống và thư mục nào chiếm chỗ. Mỗi job ước lượng chỗ cần (file nguồn ×3 + 300MB), tự chọn `/tmp` hay `work/` trong ổ bot (chỗ nào đủ), thiếu thì xóa video kết quả cũ nhất vừa đủ; không đủ thì báo lỗi kèm số liệu. `OUT_MAX_MB` mặc định 400.

- **Tắt/khởi động lại bot giữa chừng** (Botkeep dừng server, cập nhật mã): job đang làm dừng ngay (giết FFmpeg),
  giữ trạng thái "đang chạy" và file đầu vào, lần khởi động sau tự chạy lại (tối đa 2 lần); bot thoát trong < 1 giây.
- **Timeout toàn cục**: 25 phút/job thường, 3 giờ/video dài; quá hạn tự hủy, giết FFmpeg, chạy job kế. **Hủy** bằng nút trên web hoặc `/cancel` Telegram.
- **Lịch sử job lưu SQLite** (`jobs.db`), sống qua restart; link tải vẫn tra được. Job đang chờ lúc restart được chạy tiếp; job đang chạy dở được chạy lại một lần (chết lần nữa thì dừng, tránh vòng lặp). Job chờ duyệt vẫn duyệt được sau restart, hết hạn sau 24 giờ.
- **Đo đạc**: bảng `steps` (thời gian từng bước mỗi job), `events` (dịch vụ nào được dùng, có rơi về dự phòng không), `sources` (link nguồn ↔ video ra, để xử lý khiếu nại).
- **Kiểm tra định kỳ** (mỗi `HEALTH_CHECK_HOURS` giờ): ffmpeg, ổ đĩa, YouTube, Gemini, Groq, CapCut TTS, edge-tts; khâu nào hỏng, hoặc tỉ lệ rơi dự phòng 24 giờ > 20%, thì nhắn `ADMIN_CHAT_ID` (chỉ khi trạng thái đổi).
- **Cache theo bước** (`cache/`, trần `CACHE_MAX_MB`): đoạn giọng, kết quả dò cảnh/dải chữ, tiếng nền đã tách giọng. Duyệt kịch bản xong render lại không tải lại video, không gọi AI, chỉ đọc lại đoạn đã sửa.
- **Ước tính thời gian** hiện trên web, tự học tốc độ máy sau mỗi job (`speed.json`).
- **Duyệt kịch bản**: tick "📝 Duyệt/sửa kịch bản trước khi render" (web) hoặc nút 📝 (Telegram): bot viết lời xong thì dừng, hiện từng đoạn cạnh lời gốc để sửa, bấm Render. Telegram: trả lời "ok" hoặc "số: lời mới". `REVIEW_SCRIPT=1` để luôn duyệt. Mỗi đoạn chọn được **lời mới / giữ tiếng gốc (không đọc) / đọc lời mới lót tiếng gốc** (web: ô chọn dưới mỗi đoạn; Telegram: "số: gốc", "số: lót gốc", "số: mới"); `OST_AUTO=1` cho AI tự đề xuất (tối đa 1/5 số đoạn, chỉ đoạn không có bước/nguyên liệu/con số). Hàng đợi đầy vẫn duyệt được (job đã duyệt được vượt giới hạn); đoạn quảng cáo cuối đã cắt lúc viết kịch bản vẫn được cắt khi render bản duyệt.
- **Ghi nguồn** (`SOURCE_CREDIT=1`, mặc định TẮT): bật thì nội dung đăng có dòng "🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI". `ALLOWED_SOURCES`: chỉ remake video của các kênh trong danh sách (kênh của bạn / đã được cấp phép).
- **Nhiều link một lúc**: mỗi link một dòng (web) hoặc nhiều link trong một tin (Telegram), tối đa 10.
- **Kiểm con số bằng máy**: số có đơn vị (2 quả, 200g, 15 phút, 35k) phải khớp lời gốc, như kiểm nguyên liệu (~270 nhóm). Vi phạm → AI viết lại.
- **Thương hiệu** (Cài đặt): `BRAND_NAME` màn kết 2.2s cuối, logo PNG upload trong Cài đặt (góc phải trên), `BRAND_INTRO` tiêu đề 3s đầu. `SUB_STYLE`: karaoke | box | outline | plain.
- **Caption 3 nền tảng** (TikTok / Facebook / YouTube Shorts) trong nội dung đăng.
- Tốc độ: bóc lời, dò chữ, tách cảnh chạy song song; tách giọng AI chỉ chạy các khúc có lời gốc (bật khi tổng thời gian có lời ≤ 45s và video ≤ 2 phút) và chạy song song với tạo giọng; cache nghe lời gốc (`stt_cache.json`). Kéo dài video (chọn thời lượng dài hơn gốc) làm ngay trong lần render cuối, không mã hoá trung gian. Số luồng FFmpeg/x264 theo số lõi container.
- CI: `.github/workflows/test.yml` chạy ruff + pytest (Python 3.11 và 3.12) mỗi lần push.

## Video dài

- **🎞 Remake nguyên (≤5 phút):** AI viết lời theo lô ~28 đoạn, lô sau nối mạch lô trước (không mở hook lại). Video >100s tự xuất 720p và bỏ tách giọng AI (quá chậm trên Botkeep), dùng cổng tắt tiếng nền lúc người gốc nói.
- **✂️ Cắt phần 60s/90s (nguồn ≤10 phút):** chọn điểm cắt ưu tiên chỗ vừa ngưng nói vừa chuyển cảnh → chỗ ngưng nói → chuyển cảnh; phần cuối <20s gộp vào phần trước. Điểm cắt dời về khung khoá gần nhất (≤ 0.4s) để chép luồng, không mã hoá lại (đoạn 70s 1080p: 0.4s thay vì 17.7s). Mỗi phần remake riêng, xong phần nào gửi phần đó (Telegram + nút tải riêng trên web); bot khởi động lại giữa chừng thì không giao lại phần đã giao. Chế độ "Chỉ tải về" + cắt phần = chỉ cắt, không remake.
- **✨ Đoạn hay nhất (nguồn ≤10 phút):** bot bóc lời cả video, AI đọc lời (kèm ~1 ảnh/10 giây nếu có key Gemini) và chấm điểm, chọn tối đa 3 đoạn 30–90s: trọn một ý, có điểm nhấn (món chín, cắt ra, nếm thử, mẹo hay), mở đầu cuốn, không phải quảng cáo/chào hỏi. Ranh giới dời về chuyển cảnh trong khoảng lặng → giữa khoảng lặng → chuyển cảnh (±3s) để không cắt ngang câu. Đoạn điểm cao giao trước, caption có "⭐ điểm · tiêu đề — lý do". Video ít lời hoặc AI lỗi thì tự cắt đều 60s như ✂️.
- Tải/upload tới 300MB cho chế độ video dài (video thường vẫn 90s/50MB). Web giữ ~700MB video gần nhất.
- File >48MB không gửi được qua Telegram: bot gửi link tải nếu đã đặt `PUBLIC_URL` trong Cài đặt.
- Thời gian ước tính trên Botkeep: ~15–20 lần thời lượng video (video 5 phút ≈ 1–1.5 giờ); cắt phần nhận được phần đầu sau ~10–15 phút.

## Chế độ 📸 Ảnh + nhạc (1–4 ảnh)

Gửi 1–4 ảnh + một bản nhạc (link TikTok/YouTube hoặc file), bot dựng video dọc theo nhịp, kiểu mẫu "beat sync" của
CapCut/TikTok. Web: chọn chế độ 📸, chọn ảnh (có xem trước), thời lượng *Tự động theo nhạc* (~12–16s) / 10 / 15 / 30 /
60 giây, chú thích tuỳ chọn (để AI viết caption). Telegram: gửi ảnh (một album hoặc từng ảnh), gửi nhạc, chọn độ dài.

1. **Phân tích nhạc** (numpy, ~1 giây cho bài 3 phút): tempo, lưới beat, phách mạnh đầu ô nhịp, năng lượng từng beat,
   mật độ nhịp, điểm **drop** (nhạc bật lên). Đo trên 8 bản nhạc có BPM công bố (70–155 BPM): 8/8 đúng.
2. **Chọn đoạn nhạc**: đoạn năng lượng cao nhất (thường là điệp khúc/hook), bắt đầu ở phách mạnh; có drop ngay đầu
   đoạn thì lùi 2 ô nhịp để video có đà rồi bùng đúng drop.
3. **Chọn hiệu ứng theo kiểu nhạc**:

   | Kiểu | Khi nào | Mỗi ảnh | Chuyển động | Chuyển cảnh |
   | --- | --- | --- | --- | --- |
   | Êm | < 92 BPM hoặc thưa nhịp | 2 ô nhịp (tối đa ~5s) | Ken Burns chậm, có làm mềm | Hoà/dissolve/lướt mềm, căn giữa beat |
   | Nhún | Vừa | 1 ô nhịp | Lia, zoom vào/ra, nảy nhẹ ở phách mạnh | Trượt, quét, mở tròn... kết thúc đúng beat |
   | Sôi | ≥ 125 BPM và dày nhịp | 2 phách (trước drop 4) | Nảy zoom mỗi beat, rung + loé ở phách mạnh sau drop | Zoom, loé trắng, lia nhoè, ép... ¼–½ phách |

   Đúng điểm drop luôn là chuyển cảnh "hit" (zoom hoặc loé trắng). Ảnh lặp vòng để đủ độ dài, mỗi lần một chuyển
   động khác. Một ảnh thì không dùng kiểu hoà (nhìn như bóng đôi) và chuyển động mạnh hơn.
   **Công thức hiệu ứng trend** (`trendfx.py`), mỗi lần tạo một công thức khác, hợp kiểu nhạc, tránh các công thức
   vừa dùng (`recent_looks.json`), thứ tự chuyển cảnh/chuyển động ngẫu nhiên thật — làm lại cùng ảnh + nhạc vẫn ra
   video khác: Flash Pop (loé trắng + nảy zoom), Glitch RGB (lệch kênh màu theo beat), Strobe (nháy đen nửa phách),
   Đen trắng bật màu (bật màu ở phách mạnh), Lật gương theo ô nhịp, Y2K Neon (rực màu, trôi sắc, phát sáng), Dreamy
   Glow (phát sáng mềm), Film Vintage (màu phim + hạt + viền tối), Cinematic (khung điện ảnh + màu teal/cam), Pastel Soft.
   Tất cả chỉ dùng bộ lọc có trong ffmpeg 5.1 (Botkeep) và nằm trong bài tự kiểm tra lúc khởi động.
4. **Ảnh**: khác tỉ lệ 9:16 (ảnh ngang, vuông, 3:4) thì lót nền mờ của chính ảnh; ảnh điện thoại có cờ xoay EXIF
   được xoay đúng; nguồn đủ lớn (≥1600px) ra 1080x1920, không thì 720x1280. Âm lượng chuẩn hoá hai lượt về -14 LUFS.
5. **Tốc độ và RAM**: mã hoá x264 chiếm ~75% thời gian (đo: khúc 4.6s 1080p, lọc 2.0s / mã hoá 6.2s). Máy ≥ 1.4 lõi
   dựng 2 khúc song song (dùng hết lõi rảnh, nếu RAM đủ); log ghi "Dựng hình …: dùng X lõi (máy cho Y lõi)" — X thấp
   hơn hẳn Y là CPU bị máy chủ bóp. `QUALITY=fast` thì ra 720p, nhanh ~2 lần (đo 16.2s → 8.3s cho 14s video). Dựng theo khúc ≤ 8
   cảnh rồi ghép bằng chép luồng (không mã hoá lại), nên RAM không tăng theo độ dài: đo ~300MB ở 720p, ~550–600MB
   ở 1080p (trước khi chia khúc: video 60s nhạc nhanh ăn 2GB ở 1080p). Chỗ nối hai khúc: nhạc êm tối dần rồi sáng
   lại, nhạc nhún/sôi cắt thẳng đúng beat kèm loé. Tối đa 20 cảnh/video (video dài thì mỗi cảnh dài hơn). Video
   không dài quá phần nhạc còn lại (nhạc ngắn hơn độ dài đã chọn thì bot báo).

Cơ sở kỹ thuật (đã tìm hiểu trước khi làm): mẫu beat-sync của CapCut (cắt cảnh rơi đúng beat, chuyển "hit" ở drop);
nối ảnh tĩnh bằng `xfade` (bài của advancedweb.hu); Ken Burns bằng `zoompan` trên ảnh phóng to trước cho mượt (như
still-motion, kburns-slideshow — `zoompan` để nguyên thì rung, ticket FFmpeg #4298). editly / gl-transitions có
chuyển cảnh đẹp hơn nhưng cần headless-GL hoặc bản FFmpeg tự build, quá nặng cho máy 1 lõi, nên chỉ dùng bộ `xfade`
có sẵn (bản FFmpeg thiếu kiểu nào thì tự thay bằng `fade`).

## Chế độ ⬇️ Chỉ tải về

Dán link → nhận file gốc, không chỉnh sửa, tối đa 10 phút / 50MB (giới hạn gửi Telegram). TikTok: bản HD không logo qua tikwm. Facebook Reels và YouTube qua yt-dlp; **YouTube từ IP máy chủ đòi đăng nhập** → dán cookies YouTube vào `YTDLP_COOKIES` trong Cài đặt là tải được.

## Chế độ 🎵 Nhảy/Nhạc — thay nhạc mới KHỚP BEAT

Dành cho video nhảy/trend: không voiceover, không phụ đề. Gửi video + link nhạc mới (hoặc file mp3/m4a), bot sẽ:
1. Dò BPM + pha beat của điệu nhảy gốc và của nhạc mới (numpy thuần, autocorrelation + nội suy parabol, tự sửa lỗi bắt nhầm bội số BPM).
2. Chỉnh tốc độ video theo tỉ lệ BPM (kẹp 0.8–1.25 cho tự nhiên, tự thử x2/÷2).
3. Cắt/đẩy nhạc để beat đầu tiên rơi đúng nhịp chân, loudnorm -14 LUFS, fade out cuối.
Kết quả: bước nhảy rơi đúng beat nhạc mới. Telegram: chọn 🎵 → bot hỏi nhạc → gửi link hoặc file nhạc. Web: chọn chế độ 🎵 là hiện ô nhạc.


**Nguồn nhạc:** link (TikTok, YouTube...) hoặc file tải lên (web ≤ 30MB, Telegram ≤ 20MB). Link **video TikTok**: bot lấy
bản nhạc gốc (sound) mà video dùng qua tikwm — là file nhạc sạch, không lẫn giọng người quay; lỗi thì lấy tiếng của
video bằng yt-dlp. Link trang nhạc `tiktok.com/music/...` bị Cloudflare chặn: gửi link một video có dùng nhạc đó.

**Cách dùng nhạc** (web: ô "Cách dùng nhạc"; Telegram: hỏi sau khi gửi nhạc):
- 🎯 Thay hẳn nhạc + khớp beat điệu nhảy (như trước).
- 🎶 Làm nhạc nền: giữ tiếng gốc, nhạc nằm dưới (~-10 dB) và tự nhỏ lại khi tiếng gốc to; video không đổi tốc độ;
  lấy đoạn hay nhất (điệp khúc) của bài; nhạc ngắn hơn video thì lặp.
## Giọng KHỚP hình (mặc định)

1. **Bóc lời gốc kèm mốc từng từ** (Groq Whisper → Gemini gemini-3.5-transcribe → STT CapCut, theo `ASR_ORDER`) và chia đoạn theo **thao tác**: ngắt trước các từ mở đầu việc mới ("cho", "lật", "đảo", "sau đó"…).
2. **Tách cảnh** (bộ dò scene của FFmpeg) và dời đầu mỗi đoạn về đúng điểm chuyển cảnh gần nhất: câu mới vang lên ngay khi cảnh mới xuất hiện, khớp hơn cả người nói gốc (thường lệch cảnh 0.4–0.9s).
3. **AI xem ảnh từng đoạn** (Gemini) khi viết lời, để nói đúng thứ đang thấy; người gốc đọc lệch hình thì AI chuyển bước đó về đúng cảnh nhưng vẫn giữ đủ bước và số liệu. Model không nhận ảnh thì tự viết không kèm ảnh.
4. **Xếp lịch giọng không bao giờ cắt cụt chữ**: đoạn dài thì lần lượt mượn khoảng lặng phía trước (≤0.5s), nói nhanh (≤1.25x), lấn sang sau (≤0.35s); vẫn dài thì AI viết ngắn lại rồi đọc lại.
5. Video không có lời gốc: mỗi cảnh là một đoạn, AI nhìn ảnh cảnh để viết lời.
6. Chọn thời lượng dài hơn video gốc: làm chậm hình + tiếng nền tối đa 1.4x.

## Giọng văn miền Tây + giọng bớt chất AI

- `SPEECH_STYLE` (Cài đặt): `mien_tay` (mặc định: xưng tui, gọi bà con, từ miền Nam chén/muỗng/heo/trái, tiếng đệm nè/nha/hen vừa phải), `mien_nam`, `pho_thong`. Đổi từ địa phương không bao giờ đổi số liệu.
- `VOICE_HUMANIZE=1` (mặc định): xử lý giọng TTS cho giống thu micro thật trong bếp: ấm hơn, bớt chói, khử xì, nén nhẹ, phản xạ phòng ngắn.
- Chất giọng vùng miền phụ thuộc giọng CapCut bạn chọn; nghe thử rồi đặt `CAPCUT_VOICE_FEMALE`/`MALE` trong Cài đặt.

## Bỏ hẳn giọng người nói gốc (chế độ nấu ăn)

Tiếng xèo xèo được giữ, giọng người gốc bị **tách bỏ bằng AI**: model MDX-Net Kim_Vocal_2 (của dự án UVR) chạy ONNX trên CPU, không cần PyTorch. Model ~66MB tự tải lần đầu vào `models/` và được kiểm SHA-256. Chỉ các khúc ~5.7 giây có lời gốc mới chạy qua model. Chạy trong tiến trình riêng (~1.3GB RAM đỉnh, trả lại ngay khi xong), mất ~30–60s mỗi video. Kiểm thử: giọng gốc giảm ~21 dB, máy nhận dạng không còn nghe ra chữ nào.
Dự phòng khi RAM trống < 1.45GB hoặc lỗi: tắt hẳn tiếng nền đúng những lúc người gốc nói (theo mốc từng từ), giữ tiếng xèo ở khoảng nghỉ. Tắt tách AI bằng `VOICE_SEPARATION=0` trong Cài đặt.

## Hình đẹp hơn bản gốc

Khử nhiễu nén (hqdn3d) → scale lanczos → tương phản/sáng nhẹ → vibrance (tăng màu nhạt, không cháy màu da) → ấm nhẹ cho món ăn → làm nét thích ứng (CAS). Nguồn ≥1600px (ví dụ TikTok HD qua tikwm) xuất **1080x1920**, còn lại 720x1280; `OUTPUT_HD=0` để luôn 720p nếu thiếu RAM (1080p dùng ~370MB RAM, render lâu gấp đôi).

## Phụ đề kiểu CapCut

Font **Be Vietnam Pro ExtraBold** (OFL, thiết kế cho tiếng Việt, đóng kèm assets/), chữ đang đọc sáng dần sang vàng đúng nhịp giọng (karaoke `\kf`), dòng chia cân đối không trơ một chữ, chữ luôn đúng nguyên văn kịch bản.

Giọng CapCut không trả mốc từng từ: bot đo các khoảng **có tiếng** trong chính giọng đã tạo, mỗi cụm chữ giữa hai dấu câu vào đúng khúc giọng giữa hai chỗ ngắt hơi, âm cuối cụm được dành thêm thời gian. Đo trên 105 từ có mốc thật: chữ sáng lệch giọng trung bình 61–69ms (cách rải đều cũ 271ms; nghe lại bằng STT CapCut 85ms và mất 7–11 giây). Không gọi mạng.

## Ảnh bìa tự động

Mỗi video có thêm **ảnh bìa** (`COVER=1`): bot chấm điểm khung hình (độ nét, độ rực màu, độ sáng, vị trí — nấu ăn ưu tiên món đã xong ở cuối video; bỏ khung sát chuyển cảnh, khung tối, màn kết, đoạn quảng cáo đã cắt), có key Gemini thì AI chọn trong 6 ứng viên. Ảnh chỉnh màu y như video, che dải chữ gốc, tiêu đề vàng viền đen, logo kênh. Telegram: video có ảnh xem trước + ảnh bìa gửi riêng để đăng; web: link "Ảnh bìa". Chạy song song với render (~1 giây CPU).

## Từ điển bóc lời

`ASR_VOCAB` (Cài đặt): tên món, nguyên liệu, tên riêng của kênh, cách nhau dấu phẩy. Bot gửi kèm tiêu đề video + từ điển + nguyên liệu thấy trong tiêu đề/mô tả cho Groq Whisper (tham số `prompt`, ≤224 token) để viết đúng chính tả; sau khi nghe, cụm nghe gần đúng (sai dấu, nhầm s/x, d/gi/r, ch/tr) được sửa theo từ điển (chỉ cụm ≥2 âm tiết, khác không quá nửa số âm tiết). Từ điển cũng được đưa vào lời nhắc AI viết kịch bản. (Gemini transcribe chưa nhận từ điển cùng lúc với mốc từng từ.)

## AI viết kịch bản

Gemini (mặc định `gemini-3.5-flash-lite`, trả JSON theo schema cố định; quá tải 503 hoặc hết hạn mức 429 thì chờ theo `retryDelay` rồi thử lại tới 3 lần; video dài chạy tối đa 2 lô song song, lô lỗi được gọi lại riêng) → OpenRouter (`OPENROUTER_MODEL=auto`: tự liệt kê model free đang có, thử lần lượt 4 model lớn nhất, mỗi model timeout 45s để không treo) → kịch bản mẫu.

## Che phụ đề in cứng của video gốc

Bật mặc định (tắt được bằng checkbox trên web). Bot lấy 16 khung hình, tìm dải ngang có "pixel sáng kề pixel tối" xuất hiện ở ≥50% khung (đặc trưng của chữ phụ đề có viền/bóng, cố định vị trí), rồi làm mờ + phủ tối dải đó và đặt phụ đề mới đè lên. Chữ chỉ hiện vài giây (tiêu đề mở đầu) không bị coi là phụ đề nên vẫn còn.

## Chất lượng video

- 720x1280, fps 30, crf 23, unsharp nhẹ cho nét, `+faststart` xem ngay trên web.
- Âm thanh: **chuẩn hoá hai lượt** về đúng -14 LUFS (lượt 1 đo độ to cả bản trộn, lượt 2 nhân một hệ số cố định + chặn đỉnh -1.4 dBFS; `LOUDNESS_2PASS=0` để về cách cũ). Khác loudnorm một lượt: tiếng nền không bị kéo lên lúc không có giọng (đo: nền giữa hai câu -17.9 / cuối video -24 dB → -21.0 / -20.7), cả bài đúng -14.0–14.1 LUFS; chế độ Nấu ăn giữ tiếng xèo xèo (highpass 3kHz) và **tự hạ tiếng nền khi giọng đọc vang lên** (sidechain ducking) như video dựng tay.
- Kịch bản AI diễn đạt lại bằng lời riêng (không chép câu gốc), giọng nói chuyện câu ngắn có ngắt hơi, nhưng giữ đủ bước, đúng thứ tự, nguyên văn định lượng. Số từ bám theo thời lượng thật (~2.8 từ/giây).
- Hạt phim rất nhẹ (noise) để hình bớt vẻ render sạch, giống quay máy hơn.
- **Phụ đề tự động burn-in**: khớp chính xác từng từ theo mốc thời gian edge-tts trả về, chữ to đậm viền đen kiểu video ngắn, font DejaVu đóng kèm (assets/) nên không cần font hệ thống. Tắt được bằng checkbox trên web.
- **Content đăng luôn**: AI trả trọn gói tiêu đề + caption + hashtag; Telegram gắn thẳng vào caption video, web có nút "Copy content".
- **Luật cứng trong prompt**: định lượng công thức, giá, địa chỉ, tên quán/thương hiệu trong lời thoại gốc phải giữ nguyên văn, cấm bịa/làm tròn.

## Bộ video mẫu (kiểm tra chất lượng sau mỗi lần sửa)

Chép 5–10 video thật vào `golden/videos/` rồi `python golden.py run` (hoặc gõ `golden` vào console Botkeep). Bot làm lại từng video và đo: lỗi chạy, thời gian từng bước, âm lượng, độ nét/màu, nguyên liệu và con số bị mất/bịa, mức chép lời gốc, tốc độ đọc, phụ đề. `golden.py baseline` lấy lần chạy ưng ý làm chuẩn; các lần sau `golden/report.md` đánh ❌/⚠️ chỉ số tụt. Xem `golden/README.md`.

## Thời gian từng bước

Mỗi job trên web có thanh thời gian theo giai đoạn (tải, bóc lời + phân tích hình, AI viết, đọc giọng, chờ tách giọng, render, ảnh bìa, gửi), giai đoạn lâu nhất in đậm: biết ngay video nào chậm và chậm ở đâu.

## Test không cần token

```
pip install pytest ruff
ruff check .
pytest -q            # ~160 test: đơn vị, bảo mật, hàng đợi, Telegram (phiên mạng giả), pipeline với provider giả, render thật
LIVE_GEMINI=1 GEMINI_API_KEY=... pytest -q tests/test_live_gemini.py   # thử với Gemini thật (tốn vài token)
```

## Cấu trúc mã

| Tệp | Vai trò |
| --- | --- |
| `main.py` | Điểm chạy: nối worker, web, Telegram, console; Telegram lỗi mạng thì thử lại, web vẫn chạy |
| `state.py` | Hàng đợi, bot, job đang chạy; gửi tin không bao giờ ném lỗi |
| `tg_bot.py` | Handler Telegram (`setup(bot)`), chặn người ngoài `ALLOWED_USERS` |
| `web_panel.py`, `static/index.html` | Web: đăng nhập cookie, CSRF, SSE, duyệt kịch bản, cài đặt |
| `jobs.py` | Worker, hạn giờ, huỷ, dọn file đầu vào |
| `pipeline.py` | Xử lý một job từ tải tới giao; cắt phần / đoạn hay của video dài |
| `highlights.py`, `cover.py`, `vocab.py` | AI chọn đoạn hay, ảnh bìa tự động, từ điển bóc lời |
| `slideshow.py` | 📸 Ảnh + nhạc: phân tích nhạc, chọn hiệu ứng/chuyển cảnh theo nhịp, dựng FFmpeg |
| `golden.py`, `golden/` | Bộ video mẫu: chạy lại và so chất lượng với lần chuẩn |
| `review.py` | Duyệt kịch bản, cờ tiếng gốc, cú pháp trả lời Telegram |
| `core_video.py`, `scenes.py`, `textzone.py`, `beat.py`, `vocal_sep.py`, `subs.py`, `align.py` | FFmpeg, dò cảnh/chữ/beat, tách giọng, phụ đề, căn chữ |
| `transcribe.py`, `prompt_engine.py`, `tts_capcut.py` | Bóc lời, viết kịch bản (Gemini/OpenRouter), giọng CapCut |
| `queue_manager.py`, `metrics.py`, `cache.py`, `storage.py` | SQLite (jobs, steps, events, sources), cache theo bước, ổ đĩa |
| `healthcheck.py`, `admin.py`, `ytdlp_update.py`, `potoken_setup.py` | Kiểm tra định kỳ, console, cập nhật yt-dlp, PO token |
| `offline.py`, `ocr_text.py` | Gói offline: tách giọng Spleeter, giọng Piper, dò chữ OCR |
| `url_guard.py`, `config.py` | Chặn link lạ/IP nội bộ, cài đặt |

## Lưu ý

- Có ffmpeg trên máy thì bot dùng luôn; static-ffmpeg chỉ tải khi thiếu (tải lỗi bot vẫn khởi động, báo lỗi rõ khi cần).
- YouTube trên IP datacenter có thể đòi cookies; TikTok thường ổn. Cần thì thêm `cookiefile` vào ydl_opts trong core_video.py.
- Video nguồn không có tiếng: chế độ Nấu ăn tự bỏ lớp tiếng nền, vẫn chạy bình thường.
- Hàng đợi tối đa 5 video (chống tràn RAM/disk), 1 worker duy nhất.
