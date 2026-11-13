# Thay đổi so với bản botkeep_7

Làm theo "Kế hoạch nâng cấp Video Bot (botkeep_7) — Bản hoàn thiện". Mỗi giai đoạn là một commit riêng trên nhánh.
Kiểm tra: `ruff check .` sạch, `pytest -q` 131 test xanh + 5 test chỉ chạy khi có key Gemini / model offline (có model: 133 xanh)
(Python 3.13 cục bộ; CI chạy 3.11 và 3.12). Đã chạy trọn một job với dịch vụ thật (Gemini bóc lời + viết kịch bản,
edge-tts, tách giọng Kim_Vocal_2, render) và các model offline thật.

## Cần làm khi cập nhật lên Botkeep

1. Python runtime **≥ 3.11** (yt-dlp và google-genai bản mới không còn hỗ trợ 3.10).
2. `pip install -r requirements.txt` (google-genai < 4: đã kiểm với key thật trên 2.34 và 3.0.0).
3. Đặt `WEB_PASSWORD` (thiếu là web khoá), `ALLOWED_USERS` (thiếu là bot Telegram từ chối mọi người; nhắn bot một tin
   để biết chat id). Nếu trước đây đã sửa `OPENROUTER_BASE_URL` / `POT_SERVER` trên web: đặt lại bằng biến môi trường.
4. Mở web, đăng nhập lại (mật khẩu cũ lưu trong trình duyệt được xoá).

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
