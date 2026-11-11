# Video Bot — Botkeep.cloud: Telegram + Web Panel

Nhận link video → tải (yt-dlp) → Groq Whisper bóc lời thoại → Gemini viết hook + kịch bản khớp thời lượng → edge-tts giọng Việt → FFmpeg render 720x1280 (ducking + loudnorm, nhìn/nghe như video thật). Điều khiển qua Telegram **hoặc** link web Botkeep cấp.

## Triển khai trên Botkeep

1. Upload toàn bộ thư mục này (Python Runtime **3.11 trở lên**; 3.12 khuyên dùng), lệnh chạy: `python main.py`.
   `pip install -r requirements.txt` cài đúng các phiên bản đã kiểm (google-genai < 3, yt-dlp kèm Deno qua pip).
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

## Giọng đọc CapCut/TikTok (mặc định)

24 giọng Việt của CapCut (Ban Mai, Review Phim, Giọng Nữ Phổ Thông, Nam Trầm...) qua gói `capcut_tts_api` (MIT, đóng kèm trong thư mục `capcut_tts_api/`, không cần đăng nhập). Giọng CapCut không trả mốc từng từ: phụ đề rải chữ theo âm tiết trong từng đoạn giọng (1–4 giây nên đủ khớp, không tốn thêm lượt mạng); `SUB_TIMING=stt` để nghe lại track bằng STT CapCut như bản cũ. Giọng edge-tts có mốc từng từ thật. Đây là API không chính thức, có thể đổi bất cứ lúc nào: lỗi gì cũng tự rơi về edge-tts, job không hỏng. Chọn giọng cụ thể trong dropdown web; giọng mặc định nam/nữ và provider (`TTS_PROVIDER=capcut|edge`) sửa được trong Cài đặt.

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

- **Timeout toàn cục**: 25 phút/job thường, 3 giờ/video dài; quá hạn tự hủy, giết FFmpeg, chạy job kế. **Hủy** bằng nút trên web hoặc `/cancel` Telegram.
- **Lịch sử job lưu SQLite** (`jobs.db`), sống qua restart; link tải vẫn tra được. Job đang chờ lúc restart được chạy tiếp; job đang chạy dở được chạy lại một lần (chết lần nữa thì dừng, tránh vòng lặp). Job chờ duyệt vẫn duyệt được sau restart, hết hạn sau 24 giờ.
- **Đo đạc**: bảng `steps` (thời gian từng bước mỗi job), `events` (dịch vụ nào được dùng, có rơi về dự phòng không), `sources` (link nguồn ↔ video ra, để xử lý khiếu nại).
- **Kiểm tra định kỳ** (mỗi `HEALTH_CHECK_HOURS` giờ): ffmpeg, ổ đĩa, YouTube, Gemini, Groq, CapCut TTS, edge-tts; khâu nào hỏng, hoặc tỉ lệ rơi dự phòng 24 giờ > 20%, thì nhắn `ADMIN_CHAT_ID` (chỉ khi trạng thái đổi).
- **Cache theo bước** (`cache/`, trần `CACHE_MAX_MB`): đoạn giọng, kết quả dò cảnh/dải chữ, tiếng nền đã tách giọng. Duyệt kịch bản xong render lại không tải lại video, không gọi AI, chỉ đọc lại đoạn đã sửa.
- **Ước tính thời gian** hiện trên web, tự học tốc độ máy sau mỗi job (`speed.json`).
- **Duyệt kịch bản**: tick "📝 Duyệt/sửa kịch bản trước khi render" (web) hoặc nút 📝 (Telegram): bot viết lời xong thì dừng, hiện từng đoạn cạnh lời gốc để sửa, bấm Render. Telegram: trả lời "ok" hoặc "số: lời mới". `REVIEW_SCRIPT=1` để luôn duyệt. Mỗi đoạn chọn được **lời mới / giữ tiếng gốc (không đọc) / đọc lời mới lót tiếng gốc** (web: ô chọn dưới mỗi đoạn; Telegram: "số: gốc", "số: lót gốc", "số: mới"); `OST_AUTO=1` cho AI tự đề xuất (tối đa 1/5 số đoạn, chỉ đoạn không có bước/nguyên liệu/con số). Hàng đợi đầy vẫn duyệt được (job đã duyệt được vượt giới hạn); đoạn quảng cáo cuối đã cắt lúc viết kịch bản vẫn được cắt khi render bản duyệt.
- **Ghi nguồn** (`SOURCE_CREDIT=1`, mặc định): nội dung đăng có dòng "🎬 Nguồn: <kênh gốc> · 🔊 Giọng đọc AI". `ALLOWED_SOURCES`: chỉ remake video của các kênh trong danh sách (kênh của bạn / đã được cấp phép).
- **Nhiều link một lúc**: mỗi link một dòng (web) hoặc nhiều link trong một tin (Telegram), tối đa 10.
- **Kiểm con số bằng máy**: số có đơn vị (2 quả, 200g, 15 phút, 35k) phải khớp lời gốc, như kiểm nguyên liệu (~270 nhóm). Vi phạm → AI viết lại.
- **Thương hiệu** (Cài đặt): `BRAND_NAME` màn kết 2.2s cuối, logo PNG upload trong Cài đặt (góc phải trên), `BRAND_INTRO` tiêu đề 3s đầu. `SUB_STYLE`: karaoke | box | outline | plain.
- **Caption 3 nền tảng** (TikTok / Facebook / YouTube Shorts) trong nội dung đăng.
- Tốc độ: bóc lời, dò chữ, tách cảnh chạy song song; tách giọng AI chỉ chạy các khúc có lời gốc (bật khi tổng thời gian có lời ≤ 45s và video ≤ 2 phút) và chạy song song với tạo giọng; cache nghe lời gốc (`stt_cache.json`). Kéo dài video (chọn thời lượng dài hơn gốc) làm ngay trong lần render cuối, không mã hoá trung gian. Số luồng FFmpeg/x264 theo số lõi container.
- CI: `.github/workflows/test.yml` chạy ruff + pytest (Python 3.11 và 3.12) mỗi lần push.

## Video dài

- **🎞 Remake nguyên (≤5 phút):** AI viết lời theo lô ~28 đoạn, lô sau nối mạch lô trước (không mở hook lại). Video >100s tự xuất 720p và bỏ tách giọng AI (quá chậm trên Botkeep), dùng cổng tắt tiếng nền lúc người gốc nói.
- **✂️ Cắt phần 60s/90s (nguồn ≤10 phút):** chọn điểm cắt ưu tiên chỗ vừa ngưng nói vừa chuyển cảnh → chỗ ngưng nói → chuyển cảnh; phần cuối <20s gộp vào phần trước. Điểm cắt dời về khung khoá gần nhất (≤ 0.4s) để chép luồng, không mã hoá lại (đoạn 70s 1080p: 0.4s thay vì 17.7s). Mỗi phần remake riêng, xong phần nào gửi phần đó (Telegram + nút tải riêng trên web); bot khởi động lại giữa chừng thì không giao lại phần đã giao. Chế độ "Chỉ tải về" + cắt phần = chỉ cắt, không remake.
- Tải/upload tới 300MB cho chế độ video dài (video thường vẫn 90s/50MB). Web giữ ~700MB video gần nhất.
- File >48MB không gửi được qua Telegram: bot gửi link tải nếu đã đặt `PUBLIC_URL` trong Cài đặt.
- Thời gian ước tính trên Botkeep: ~15–20 lần thời lượng video (video 5 phút ≈ 1–1.5 giờ); cắt phần nhận được phần đầu sau ~10–15 phút.

## Chế độ ⬇️ Chỉ tải về

Dán link → nhận file gốc, không chỉnh sửa, tối đa 10 phút / 50MB (giới hạn gửi Telegram). TikTok: bản HD không logo qua tikwm. Facebook Reels và YouTube qua yt-dlp; **YouTube từ IP máy chủ đòi đăng nhập** → dán cookies YouTube vào `YTDLP_COOKIES` trong Cài đặt là tải được.

## Chế độ 🎵 Nhảy/Nhạc — thay nhạc mới KHỚP BEAT

Dành cho video nhảy/trend: không voiceover, không phụ đề. Gửi video + link nhạc mới (hoặc file mp3/m4a), bot sẽ:
1. Dò BPM + pha beat của điệu nhảy gốc và của nhạc mới (numpy thuần, autocorrelation + nội suy parabol, tự sửa lỗi bắt nhầm bội số BPM).
2. Chỉnh tốc độ video theo tỉ lệ BPM (kẹp 0.8–1.25 cho tự nhiên, tự thử x2/÷2).
3. Cắt/đẩy nhạc để beat đầu tiên rơi đúng nhịp chân, loudnorm -14 LUFS, fade out cuối.
Kết quả: bước nhảy rơi đúng beat nhạc mới. Telegram: chọn 🎵 → bot hỏi nhạc → gửi link hoặc file nhạc. Web: chọn chế độ 🎵 là hiện ô nhạc.

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

## AI viết kịch bản

Gemini (mặc định `gemini-3.5-flash-lite`, trả JSON theo schema cố định; quá tải 503 hoặc hết hạn mức 429 thì chờ theo `retryDelay` rồi thử lại tới 3 lần; video dài chạy tối đa 2 lô song song, lô lỗi được gọi lại riêng) → OpenRouter (`OPENROUTER_MODEL=auto`: tự liệt kê model free đang có, thử lần lượt 4 model lớn nhất, mỗi model timeout 45s để không treo) → kịch bản mẫu.

## Che phụ đề in cứng của video gốc

Bật mặc định (tắt được bằng checkbox trên web). Bot lấy 16 khung hình, tìm dải ngang có "pixel sáng kề pixel tối" xuất hiện ở ≥50% khung (đặc trưng của chữ phụ đề có viền/bóng, cố định vị trí), rồi làm mờ + phủ tối dải đó và đặt phụ đề mới đè lên. Chữ chỉ hiện vài giây (tiêu đề mở đầu) không bị coi là phụ đề nên vẫn còn.

## Chất lượng video

- 720x1280, fps 30, crf 23, unsharp nhẹ cho nét, `+faststart` xem ngay trên web.
- Âm thanh: loudnorm -16 LUFS chuẩn mạng xã hội; chế độ Nấu ăn giữ tiếng xèo xèo (highpass 3kHz) và **tự hạ tiếng nền khi giọng đọc vang lên** (sidechain ducking) như video dựng tay.
- Kịch bản AI diễn đạt lại bằng lời riêng (không chép câu gốc), giọng nói chuyện câu ngắn có ngắt hơi, nhưng giữ đủ bước, đúng thứ tự, nguyên văn định lượng. Số từ bám theo thời lượng thật (~2.8 từ/giây).
- Hạt phim rất nhẹ (noise) để hình bớt vẻ render sạch, giống quay máy hơn.
- **Phụ đề tự động burn-in**: khớp chính xác từng từ theo mốc thời gian edge-tts trả về, chữ to đậm viền đen kiểu video ngắn, font DejaVu đóng kèm (assets/) nên không cần font hệ thống. Tắt được bằng checkbox trên web.
- **Content đăng luôn**: AI trả trọn gói tiêu đề + caption + hashtag; Telegram gắn thẳng vào caption video, web có nút "Copy content".
- **Luật cứng trong prompt**: định lượng công thức, giá, địa chỉ, tên quán/thương hiệu trong lời thoại gốc phải giữ nguyên văn, cấm bịa/làm tròn.

## Test không cần token

```
pip install pytest ruff
ruff check .
pytest -q            # ~125 test: đơn vị, bảo mật, hàng đợi, Telegram (phiên mạng giả), pipeline với provider giả, render thật
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
| `pipeline.py` | Xử lý một job từ tải tới giao; cắt phần video dài |
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
