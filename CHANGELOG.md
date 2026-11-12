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
