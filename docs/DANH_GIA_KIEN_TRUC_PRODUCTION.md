# Đánh giá kiến trúc & kế hoạch đưa lên production (13/11/2026)

Vai trò: kỹ sư hệ thống tiếp nhận codebase để vận hành lâu dài. Mọi nhận định dưới đây đều có dẫn chiếu `tệp:dòng`
trên mã thật tại commit hiện hành; chỗ nào chưa kiểm chứng được sẽ ghi rõ.

## Phần 1 — Kiến trúc hiện tại và luồng dữ liệu

### 1.1 Bản đồ module (đo bằng phân tích import, 42 module, ~13.300 dòng)

| Tầng (thực tế, chưa được tách thư mục) | Module | Ghi chú |
| --- | --- | --- |
| Điểm chạy & tiến trình | `main.py` | 1 tiến trình asyncio: worker (1 job một lúc), web (aiohttp), Telegram (aiogram, long polling), console stdin, bảo trì, autopost, healthcheck |
| Trạng thái chung | `state.py`, `queue_manager.py`, `config.py` | `state.queue` (SQLite `jobs.db` + `asyncio.Queue`), `state.bot`, `state.current`, `spawn()` giữ tham chiếu task; `config` là **module toàn cục có biến đổi lúc chạy** (`_apply()` ghi đè `globals()`) |
| Giao diện | `web_panel.py` (819 dòng), `static/*.html`, `tg_bot.py`, `admin.py` (console) | |
| Điều phối việc | `jobs.py` (worker, ngân sách giờ, huỷ, dọn đầu vào), `pipeline.py` (1.200 dòng: mọi chế độ cook/review/music/photo/download/split/highlight/không lời) | `pipeline` phụ thuộc 23 module nội bộ — là "god module" |
| Lõi xử lý | `core_video.py` (1.783 dòng: tải, ffmpeg, filter graph, TTS, xếp giọng, loudness), `scenes.py`, `textzone.py`, `cover.py`, `subs.py`, `align.py`, `vocal_sep.py`, `beat.py`, `slideshow.py`, `trendfx.py`, `bgm.py`, `ingredients.py`, `vi_text.py`, `vocab.py` | thuần tính toán, nhưng nhiều hàm gọi thẳng `config` và `subprocess` |
| Tích hợp ngoài | `prompt_engine.py` (Gemini/OpenRouter), `transcribe.py` (Groq/Gemini/CapCut), `tts_capcut.py`, `kaggle_api.py`, `kaggle_worker.py`, `kaggle_job.py` (chạy BÊN TRONG Kaggle), `asr_local.py`, `autopost.py` (Facebook/TikTok), `potoken_setup.py`, `ytdlp_update.py`, `offline.py` | |
| Hạ tầng | `cache.py`, `storage.py`, `metrics.py`, `healthcheck.py`, `url_guard.py` | |

Phụ thuộc đáng chú ý (đo được):
- `config` được 25 module import; `core_video` 15; `queue_manager` 9; `state` 8.
- Vòng 2 chiều: `autopost` ↔ `config` (config.save_settings gọi `autopost.normalize_*` để kiểm giá trị:
  `config.py` dòng ~200) và `core_video` ↔ `offline`.
- Lõi gọi vào tầng giao diện/trạng thái: `pipeline` import `state` (gửi Telegram, `OUT_DIR`), `core_video` import
  `kaggle_worker` (chỉ cho tên giọng VieNeu), `cover`/`scenes` dùng `_ANALYZED` (biến riêng của `scenes`) từ `pipeline`.
- `kaggle_job.py` được gói kèm `core_video`, `scenes`, `vocal_sep`, `subs`… và chạy trên Kaggle: cùng mã lõi dùng ở
  hai môi trường, nhưng **hai bộ điều phối khác nhau** (`pipeline._process` trên Botkeep và `kaggle_job.produce`
  trên Kaggle) phải được giữ giống nhau bằng tay (test `test_kaggle*.py` kiểm phần lớn).

### 1.2 Luồng một job "nấu ăn" (đường chính, có Kaggle)

```
web /api/jobs|/api/upload  ──┐                             tg_bot (aiogram) ──┐
   (web_panel.add_job/upload)│                                                 │
                              ▼                                                 ▼
                   queue_manager.JobQueue.put() → jobs.db (INSERT OR REPLACE, json của dataclass Job)
                              │ asyncio.Queue
                              ▼
                   jobs.worker() → _run_one(job): chọn thư mục tạm (storage.pick_workdir), ngân sách giờ co giãn
                              │ TemporaryDirectory(tmp)
                              ▼
                   pipeline.process → _process
   1  tải nguồn (yt-dlp, url_guard) → tmp/input.mp4 | hoặc file upload trong uploads_in/
   2  phân tích: Kaggle `rmvideo-analyze` (tách giọng GPU → Whisper large-v3 → cảnh/chữ/ảnh thu nhỏ; ambient.flac
      giữ ở output notebook) → cache kaggle_an/kaggle_thumbs | không có Kaggle: extract_audio + Groq/Gemini + scenes
   3  quyết định: không lời → _music_only; đoạn mở đầu (plan_teaser: AI chọn khung + so khung)
   4  AI viết lời theo đoạn (prompt_engine.generate_timed_any, 1 lượt/28 đoạn, tối đa 3 lượt mỗi lô) → kiểm
      nguyên liệu/số/chép/kêu gọi → (duyệt kịch bản: dừng, trả "REVIEW")
   5  rút gọn trước theo tốc độ giọng → build_spec → Kaggle `rmvideo-worker` (TTS VieNeu/edge, tách giọng hoặc dùng
      lại ambient theo mã, xếp giọng, phụ đề, dựng) → tải output.mp4 về tmp
      | không Kaggle: TTS (cache tts/), tách giọng Botkeep (cache ambient/), compose_voice, render trên Botkeep
   6  ảnh bìa song song (cover.make_cover) → tmp/cover.jpg, thumb.jpg
   7  deliver: move tmp/output.mp4 → outputs/out_<id>_<k>.mp4 (+ .jpg); storage._cleanup_outputs; Telegram gửi
      file/ link; queue.save(job); autopost (nếu job.source == "auto") lấy file từ job.parts để đăng Fanpage
```

Tệp sinh ra và vòng đời (kiểm trên mã):
- `uploads_in/up_<hex>`: file người dùng tải lên; xoá khi job xong (`jobs._drop_inputs`), giữ khi chờ duyệt.
- `work/` hoặc `/tmp` (chọn theo chỗ trống, `storage.pick_workdir`): thư mục tạm mỗi job, xoá bởi
  `TemporaryDirectory`; `main.py:31` xoá cả `work/` lúc khởi động.
- `outputs/`: kết quả giữ cho web tải, trần `OUT_MAX_MB` (400MB): giữ file mới nhất, xoá file cũ khi vượt trần
  (`storage._cleanup_outputs`, theo tổng dung lượng, không theo tuổi).
- `cache/<bước>/`: tts, ambient, scenes, kaggle_an/thumbs, transcribe (`.transcribe_cache.json` riêng), trần
  `CACHE_MAX_MB` 500MB, ghi tạm rồi `os.replace` (`cache.py:57-84`, an toàn khi ngắt giữa chừng).
- `jobs.db` (SQLite, chế độ mặc định — không WAL, `synchronous` mặc định), `settings.json` (0600), `autopost.json`,
  `metrics` trong jobs.db, `.session_key` (0600).
- Trên Kaggle: notebook < 1MB chứa mã bot (zip base64) + spec; output `result.json`, `output.mp4`, `ambient.flac`.

### 1.3 Điểm nghẽn hiệu năng (đo được)

| Nơi | Bằng chứng | Hệ quả |
| --- | --- | --- |
| Bóc lời + phân tích hình trên Botkeep (khi không Kaggle) | log cũ: 380s/220s video ở mức "full"; mức "light" ~20s | đã giảm bằng `SCENE_ANALYSIS=light` và chuyển lên Kaggle |
| Tách giọng CPU Botkeep | ~1 giây xử lý/giây audio, ~1.3GB RAM đỉnh (`vocal_sep.py`: `NEED_MB=1450`, chạy tiến trình con) | chỉ bật cho ≤120s video / ≤45s lời; Kaggle làm cả bài |
| Render | Botkeep 1.5 vCPU: 14-24 giây máy/giây video 1080p (đo lúc khởi động) → tự hạ 720p khi dài > ~25-40s | đúng hướng; Kaggle 4 lõi ~1.1s/giây video |
| Vòng lặp sự kiện bị chặn | SQLite đồng bộ trong coroutine (`queue_manager.save/_rows`), đọc/ghi settings.json, `cache.file_hash` đã đưa vào `to_thread`; `scenes._analyze_sync` qua `to_thread` | chấp nhận được với 1 worker; không nên tăng worker khi chưa tách |
| Kaggle | mỗi job 2 phiên (phân tích ~3.6-3.9 phút gồm hàng chờ + cài thư viện + tải model 3GB; dựng 6-8 phút) | phần "cài thư viện + tải model" lặp lại mỗi phiên: ứng viên lớn nhất để rút ngắn (Kaggle Dataset chứa wheel + model) |
| Polling trạng thái Kaggle 20s/lần, web polling `/api/jobs` | nhẹ | — |
| Số lần gọi ffmpeg/ffprobe mỗi job (đo trên clip 20s, 8 đoạn, mức light) | 25 ffmpeg + 15 ffprobe; nguồn bị giải mã audio **3 lần** (`extract_audio`, `vocal_sep._decode`, `measure_lufs`) và 7 ffprobe; mỗi đoạn giọng 1 ffprobe + 1 ffmpeg (`speech_duration` → `voiced_intervals`) | khoá cache STT theo `src_hash` đã có để bỏ `extract_audio` khi trúng; bỏ ffprobe mỗi đoạn (voiced_intervals đã biết độ dài); gộp `silences` vào lượt `scenes` |
| Numpy trên vòng lặp sự kiện | `cover.score_frames` chạy trên vòng lặp: 623ms cho 600 khung (video 300s @2fps) trên máy 4 lõi; `storage.disk_report()` duyệt cả `.cache/.local` mỗi lần bắt đầu job (58ms/13k tệp) | đưa `score_frames` vào `to_thread` cùng `frames_sync` |
| Tách giọng thuần numpy | 24s/300s audio ngay cả với model giả (STFT complex128, ISTFT vòng Python, 5-6 bản sao toàn bài → 1.1GB RAM cho 300s) | `scipy.fft` float32/complex64, overlap-add vector hoá, khối mask 20s → 5s |

### 1.4 Logic trùng lặp (vị trí)

1. **Hai bộ điều phối dựng**: `pipeline._process` (đoạn từ "Đọc giọng" tới `core_video.render`, ~dòng 1030-1075)
   và `kaggle_job.produce` (dòng ~147-215): cùng chuỗi compose_voice → words_for_track → write_ass → ost_windows →
   render. Mọi tham số mới (teaser, bed, float render_dur) phải sửa hai nơi — đã từng lệch (bản 31: `int(render_dur)`).
2. `post_title = job.hook or (job.post.split("\n",1)[0]...)` lặp 4 lần trong `pipeline.py`.
3. Kiểm giá trị cài đặt: chuỗi `if k == ... raise ValueError` dài trong `config.save_settings` (~70 dòng) tách rời
   với danh sách lựa chọn hiển thị `web_panel._SETTING_OPTIONS` — hai nguồn sự thật cho cùng một thứ.
4. Tải video: `core_video._download_sync`, `download_format`, `_download_audio_sync`, `_probe_formats_sync` lặp
   phần cấu hình yt-dlp (cookies, proxy, giới hạn).
5. Gọi AI: `prompt_engine.generate`, `generate_timed`, `post_only`, `shorten`, `cover.ai_judge`, `highlights`,
   `transcribe._recheck_gemini` mỗi nơi tự dựng danh sách provider + thử lại + bóc JSON.
6. Bốn "vỏ chạy Kaggle" trong `pipeline` (`_kaggle_render`, `_music_only`+`_finish_remote`, nhánh chính, `_remake_pieces`)
   lặp try/`produce`/`metrics.record("kaggle")`/ghi chú ℹ️ (9 chỗ); bản ghi đoạn đã duyệt dựng 3 kiểu (`pipeline` 2 chỗ,
   `review.py`); 5 biến thể yt-dlp + 4 hàm tải HTTP; `_write_wav`, sha256, đo RAM cgroup, "spawn + RUNNING + kill" mỗi
   thứ 2-4 bản (`core_video`, `vocal_sep`, `offline`, `beat`).

### 1.5 Rủi ro chạy đồng thời (đã kiểm trên mã)

- Thiết kế **một worker** (`jobs.worker`, `MAX_QUEUE=5`) là đúng cho máy 1.5 vCPU/2GB; nhưng web, autopost (mỗi
  120s), healthcheck, console và Telegram cùng chạy trong một vòng lặp, chạm vào cùng đối tượng `Job` và `jobs.db`:
  - `review.approve_job` (web/Telegram) tạo job mới và đổi trạng thái job cũ trong lúc worker vẫn đang ở bước
    gửi tin — đã có cờ `keep_source` xử lý (`jobs.py:~90`), đúng.
  - `scenes._ANALYZED` là dict toàn cục **khoá theo đường dẫn file** (`scenes.py:9`): hai job dùng cùng đường dẫn
    (file giữ lại để duyệt rồi làm lại) có thể đọc ảnh thu nhỏ của lần trước; với 1 worker thì tuần tự nên an toàn,
    nhưng sẽ hỏng ngay khi có 2 worker.
  - `kaggle_worker._LINKS` (link tạm cho Kaggle) sống trong bộ nhớ: bot khởi động lại giữa phiên Kaggle → link chết,
    Kaggle tải lỗi → job làm lại trên Botkeep (có dự phòng, chấp nhận được).
  - `autopost._POSTED` trong bộ nhớ + `autopost.json` ghi atomically; khoá `_LOCK` chống hai lượt chạy chồng — ổn.
  - `core_video.RUNNING` giữ tiến trình con để huỷ; `TemporaryDirectory` dọn khi huỷ. Nhưng tiến trình tách giọng
    chạy bằng `create_subprocess_exec` không được đặt vào nhóm tiến trình riêng: huỷ job giết đúng tiến trình đó,
    còn tiến trình cháu (ffmpeg do python con gọi) phụ thuộc vào việc con thoát (không có `start_new_session`/
    `killpg` ở bất kỳ đâu trong mã — đã grep).
- SQLite: kết nối duy nhất, không WAL; mọi ghi là `INSERT OR REPLACE` + `commit` ngay (bền khi mất điện ở mức
  "job cuối có thể thiếu"), đủ cho quy mô này.
- Bổ sung từ lượt rà sâu (đã kiểm trên mã, chi tiết và trạng thái vá ở Phần 2.4): huỷ vì quá hạn/tắt bot không
  dừng phiên Kaggle và cờ dừng dùng chung giữa các lượt (C1); API huỷ Kaggle gọi đồng bộ trong handler (C2); tệp JSON
  trạng thái không atomic và tự tắt khi hỏng (C4); `clean` xoá thư mục job đang chạy (C6); `config._apply()` đổi
  cài đặt giữa job (C9); hai kết nối SQLite từ hai luồng (C10).

### 1.6 Điểm mạnh nên giữ

- Mọi lỗi dịch vụ ngoài đều có dự phòng và ghi chú cho người dùng (Kaggle → Botkeep, Gemini → OpenRouter → mẫu,
  CapCut ↔ edge, tách giọng → cổng tiếng), có `metrics` để xem tỉ lệ rơi dự phòng.
- Hàng đợi sống qua khởi động lại; job chờ duyệt giữ nguồn; dọn ổ đĩa theo trần.
- Bộ test 423 ca (bản 35) chạy không cần mạng/token, dựng thật bằng ffmpeg; bộ video mẫu (`golden.py`).
- Bảo mật cơ bản đã làm: cookie phiên HMAC + muối riêng, chống CSRF theo Origin, khoá dò mật khẩu theo IP và toàn cục,
  link tải có mã riêng, `url_guard` chống SSRF (allowlist nền tảng + chặn IP nội bộ sau DNS), settings.json 0600,
  key API bị che trên giao diện, notebook Kaggle không chứa key (`settings_snapshot` loại `SECRET_KEYS`).

## Phần 2 — Rà soát bảo mật & độ tin cậy production

Cách làm: đọc toàn bộ module có mặt ngoài (web_panel, url_guard, config, tg_bot, kaggle_*, autopost, admin,
potoken_setup, ytdlp_update, storage, jobs, main) rồi **chạy thử trên mã thật**: aiohttp `TestClient` đánh vào
`build_app` (CSRF, phiên, trần body, upload), hàm `url_guard` với DNS giả, dựng notebook Kaggle với key giả để tìm
rò rỉ, đo RAM bằng `tracemalloc`. Mỗi mục ghi mức độ, bằng chứng, và trạng thái: **[đã vá bản 35]** hoặc **[đề xuất]**.

### 2.1 Xác thực và phân quyền

| # | Mức | Phát hiện (bằng chứng) | Trạng thái |
| --- | --- | --- | --- |
| A1 | **Cao** | `web.Application(client_max_size=MAX_UPLOAD+5MB)` = 305MB áp cho **mọi** `request.json()`, kể cả `/api/login` (đường công khai) đọc body xong mới so mật khẩu. Đo: body JSON 40MB gửi `/api/login` → 401 sau khi đã đọc hết, đỉnh RAM ~120MB cho một request; 3-6 request song song đủ OOM máy 2GB, job đang render chết theo. Khoá dò mật khẩu không giúp vì chỉ đếm sau khi đọc xong body. | [đã vá] trần body JSON 1MB (`web_panel.JSON_MAX`), `/api/login` từ chối `Content-Length > 4KB` trước khi đọc; upload vẫn đi `request.multipart()` đọc từng khúc 1MB với trần riêng (đã test file 3MB đi qua) |
| A2 | Trung bình | `/api/logo`, `/api/voice_ref` gọi `part.read()` gom cả phần vào RAM rồi mới kiểm cỡ (trần thực tế = 305MB mỗi phần); trường chữ của `/api/upload` cũng đọc hết rồi mới cắt 4000 ký tự. | [đã vá] `_read_capped()` đọc từng khúc, vượt trần là dừng ngay |
| A3 | Trung bình | `/api/upload` nhận nhiều phần `file`/`music` trong một request: phần sau đè biến `saved`, file trước **bị bỏ rơi** trong `uploads_in/` (chỉ được dọn sau 1 giờ và chỉ khi job khác thiếu chỗ). Bấm đúp "Tải lên" với 2 file 300MB là đầy ổ 2GB. | [đã vá] phần thứ hai → 400 và file thứ nhất được xoá (test `test_logo_capped_for_kaggle_and_single_video_part`) |
| A4 | Trung bình | Đăng xuất chỉ xoá cookie phía trình duyệt (`resp.del_cookie`); token = HMAC(hạn) nên **không thu hồi được** trong 30 ngày — cookie bị sao chép (máy dùng chung, log proxy) vẫn dùng được sau khi chủ đã đăng xuất. Đo: replay cookie cũ sau `POST /api/logout` → `GET /api/jobs` 200. | [đã vá] mốc thu hồi `.session_key.epoch` trộn vào khoá ký; đăng xuất (từ phiên hợp lệ) đổi mốc → mọi cookie cũ hết hiệu lực; request đăng xuất không có phiên thì không được thu hồi (chống phá rối) |
| A5 | Trung bình | `YTDLP_PROXY`, `KAGGLE_API_TOKEN`/`KAGGLE_USERNAME`, `PUBLIC_URL` sửa được từ web, trong khi chính mã đã đặt `OPENROUTER_BASE_URL`/`POT_SERVER` chỉ-env với lý do "lộ mật khẩu web không kéo theo lộ key" (`config.py` ~dòng 100). Kẻ có mật khẩu web: đặt proxy → **toàn bộ cookie YouTube/TikTok** (`YTDLP_COOKIES`) đi qua máy của hắn; đổi token Kaggle → video + mẫu giọng nhân bản + link `/kin/` của bạn được đẩy lên tài khoản Kaggle của hắn; đổi `PUBLIC_URL` → tên miền của hắn lọt vào danh sách Origin hợp lệ (`_allowed_hosts`) và link tải gửi qua Telegram trỏ về máy hắn. | [đề xuất] chuyển `YTDLP_PROXY` và `PUBLIC_URL` sang chỉ-env (như `OPENROUTER_BASE_URL`), hoặc bắt nhập lại mật khẩu khi đổi khoá trong `SECRET_KEYS`; `_allowed_hosts` chỉ đọc `os.environ["PUBLIC_URL"]`. Không vá tự động vì đổi cách cấu hình đang chạy của bạn — cần quyết định |
| A6 | Thấp | `Origin: null` được cho qua; `X-Forwarded-Host` tin vô điều kiện; không có `X-Frame-Options`/`nosniff`. Chưa khai thác được vì cookie `SameSite=Strict`, nhưng Origin là tuyến phòng thủ duy nhất khi hai dịch vụ cùng host khác cổng (cookie không phân biệt cổng — kiểu node Pterodactyl nhiều tenant). | [đã vá một phần] middleware thêm `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`; `Origin: null` giữ nguyên (ghi nhận) |
| A7 | Thấp | Đầu vào hỏng gây 500 kèm dấu vết trong log: `/api/jobs/abc/cancel` (`int()`), body không phải JSON hoặc là list (`.get`), `approve` với `i` không phải số, POST không multipart vào `/api/upload` (`AssertionError` của aiohttp). | [đã vá] `_int_id` → 404, `_json_dict` → 400, `_multipart` → 400 |
| A8 | Thấp | Bảng đếm sai mật khẩu `_fails` khoá theo `X-Forwarded-For` đầu tiên (giả được khi đánh thẳng), chỉ dọn theo từng khoá → XFF ngẫu nhiên làm dict phình tới khi khởi động lại; không cảnh báo mật khẩu ngắn (30 lần/phút ≈ 1,3 triệu lần/tháng đủ dò mật khẩu ngắn). | [đã vá] dọn khoá cũ khi > 2000; cảnh báo lúc khởi động nếu `WEB_PASSWORD` < 12 ký tự |
| A9 | — | Telegram: `ALLOWED_USERS` đóng mặc định (trống = từ chối tất cả), nhận cả chat id nhóm → mọi thành viên nhóm được phép tạo job (đúng thiết kế, cần nhớ khi chia sẻ nhóm). | ghi nhận |

Đã làm tốt, **không đổi**: cookie phiên HMAC có muối riêng 0600, `HttpOnly + SameSite=Strict + Secure` khi HTTPS,
`hmac.compare_digest` cho mật khẩu/phiên/mã tải, khoá dò theo IP **và** toàn cục dùng chung cho `/api/login` và
`X-Auth`, mật khẩu không bao giờ nhận qua URL, `/api/me` không dùng làm máy dò, `/health` chỉ lộ `ok/pending`,
thiếu `WEB_PASSWORD` là khoá toàn bộ; Origin kiểm trên mọi route đổi trạng thái kể cả login/logout; link tải mã
128-bit riêng từng file; link `/kin/` 256-bit, một file, 4 giờ, thu hồi sau phiên Kaggle.

### 2.2 Injection, SSRF, dữ liệu từ bên ngoài

| # | Mức | Phát hiện | Trạng thái |
| --- | --- | --- | --- |
| B1 | Thấp | `url_guard._ip_public` coi `64:ff9b::/96` (NAT64) là công cộng vì Python theo IANA; trên máy có NAT64, `64:ff9b::7f00:1` dẫn về 127.0.0.1 (cần DNS do kẻ tấn công kiểm soát trỏ về địa chỉ này — lý thuyết). Link chứa `\t`/`\n` được `urlsplit` bỏ qua khi kiểm host nhưng chuỗi gốc vẫn trả về. | [đã vá] bóc IPv4 trong NAT64 rồi kiểm như IPv4; link có ký tự điều khiển bị chặn |
| B2 | Thấp | `autopost.list_tiktok_sync` là lời gọi yt-dlp **duy nhất** không đặt `allowed_extractors=["default","-generic"]` và không dùng proxy/cookies như `core_video._add_cookies` (đầu vào đã chuẩn hoá về `tiktok.com/@tên` nên không khai thác được; nhưng liệt kê kênh bỏ qua proxy trong khi tải video dùng proxy → có thể liệt kê lỗi nơi tải được). | [đã vá] đặt `allowed_extractors` + proxy |
| B3 | Thấp | `config.save_settings` kiểm ~20 khoá nhưng nhận chuỗi bất kỳ cho `GEMINI_MODEL`, `GROQ_WHISPER_MODEL`, `BRAND_NAME` (vào `drawtext` qua tệp chữ — an toàn, nhưng không có trần độ dài), các khoá bật/tắt (giao diện chỉ cho chọn, API nhận chữ tự do), `BGM_LINKS` nhận host mà allowlist sẽ từ chối lúc dùng. Tệp tạm `settings.json.tmp` tạo bằng `open(..., "w")` → 0644 trong khoảnh khắc trước `chmod`. | [đã vá] tạo tệp tạm bằng `os.open(..., 0o600)` + `fsync`. [đề xuất] một schema cài đặt (mẫu `samples/rmvideo/app/settings.py`) để lựa chọn hợp lệ và kiểm tra ở cùng một chỗ |
| B4 | — | Mọi chỗ còn lại đã đúng: allowlist nền tảng an toàn với tên miền con/`%2e`/punycode/`#@`; mọi bản ghi A/AAAA đều kiểm (private, link-local, CGNAT, ULA, 6to4, Teredo, doc, multicast…); IP viết tắt (`127.1`, `0x7f000001`, số thập phân) bị bắt ở bước phân giải; redirect kiểm từng bước ở cả 3 opener; tikwm kiểm với `any_domain=True` vẫn chặn IP nội bộ; XSS: `esc()` áp cho mọi trường job kể cả tên file tải lên và tiêu đề kênh ngoài; `thumbs.zip` chỉ bung tên `\d{5}\.jpg`; `result.json` gắn mã riêng từng việc. | giữ |

### 2.3 Bí mật và rò rỉ

- Kiểm bằng cách gieo dấu hiệu vào **mọi** khoá bí mật + `WEB_PASSWORD` + `BOT_TOKEN` rồi dựng notebook/bundle Kaggle:
  **không** dấu hiệu nào xuất hiện; bundle không chứa `.env`, `settings.json`, `jobs.db`, `.session_key`.
  `settings_snapshot` loại `SECRET_KEYS` và mọi khoá `KAGGLE_/FB_/AUTO_/ALLOWED_/ADMIN_`. Log và tin Telegram không
  mang key; API che giá trị (`AIza•••ijk`); token Kaggle đi trong header `Authorization`.
- Ghi nhận (thấp): `access_token` Facebook Graph đi trong query string ở GET (chuẩn của Graph, nhưng lọt vào log
  proxy — nên dùng `Authorization: Bearer`); `jobs.db` 0644 chứa `dl_token`; `cookies.txt` ghi 0644 trong thư mục
  tạm của job (container một người dùng → chấp nhận; `os.open(0o600)` là một dòng).

### 2.4 Chịu lỗi, dọn tài nguyên khi sự cố / tắt đột ngột

| # | Mức | Phát hiện (bằng chứng) | Trạng thái |
| --- | --- | --- | --- |
| C1 | **Cao** | Huỷ vì **quá hạn** (`jobs._OverBudget`) hoặc **bot tắt** chỉ `task.cancel()`; chỉ nút Huỷ mới gọi `kaggle_worker.abort()`. Vòng chờ Kaggle chạy trong `to_thread` không huỷ được → tiếp tục hỏi trạng thái tới 50 phút rồi tải kết quả vào thư mục tạm đã xoá; `finally: _ABORT["running"]=False` của lượt cũ có thể **xoá cờ của lượt sau** → `abort()` lần tới về sớm, giờ GPU cháy không. | [đã vá] `abort()` gọi ở cả 3 đường huỷ; cờ dừng là đối tượng riêng từng lượt (`_watch/_unwatch`), lượt cũ không chạm lượt mới (test) |
| C2 | **Cao** | `kaggle_worker.abort()` → `kaggle_api.cancel()` → `requests.post(timeout=120)` chạy **ngay trong handler web/Telegram** (`jobs.cancel_job`): một cú bấm Huỷ có thể đóng băng web, SSE và Telegram tới 120 giây (240 giây nếu phải hỏi tên tài khoản). | [đã vá] gọi API huỷ trong luồng riêng (`run_in_executor`), vòng lặp không chặn |
| C3 | Trung bình | Logo > ~530KB làm notebook Kaggle vượt 990.000 byte (`kernel_text` ném "việc quá lớn") vì `logo_b64` gắn vào **mọi** spec; upload lại cho tới 2MB → mọi job Kaggle âm thầm đổ về Botkeep (chỉ một dòng ℹ️). | [đã vá] trần `config.LOGO_MAX_BYTES` = 400KB khi tải lên (báo lý do), và `_common` bỏ logo quá cỡ kèm cảnh báo thay vì làm hỏng cả việc |
| C4 | Trung bình | `stt_cache.json` ghi không atomic (`json.dump(open(...,"w"))`); hỏng giữa chừng → `except: key=""` → cache **tắt hẳn** cho tới khi xoá tay (không bao giờ ghi lại). `speed.json` cùng kiểu: hỏng là ETA kẹt 6.0 mãi. | [đã vá] `cache.read_json_file/write_json_file` (tạm + `os.replace`, hỏng → coi như trống rồi ghi đè); test giả lập tệp hỏng |
| C5 | Trung bình | Thư mục tạm `TemporaryDirectory(dir=/tmp)` không có tiền tố, OOM/SIGKILL để lại `/tmp/tmpXXXX` tới ~1GB mỗi lần; `main.py` chỉ dọn `work/` → /tmp đầy dần, `pick_workdir` lặng lẽ đẩy job sang ổ bot 2GB. | [đã vá] tiền tố `rmv_` + `storage.sweep_tmp()` lúc khởi động |
| C6 | Trung bình | Lệnh console `clean` xoá `work/` và toàn bộ cache **kể cả khi job đang chạy** (job dùng `work/` khi /tmp chật; `_ambient()` trả đường dẫn trong cache cho render dùng vài phút sau). | [đã vá] đang có job thì `clean` giữ `work/` và cache, chỉ dọn pip |
| C7 | Trung bình | Tiếng nền đã tách được **chép** vào cache (`cache.put` mặc định copy): 53MB/5 phút nằm hai chỗ tới hết job, trên ổ 2GB. | [đã vá] `move=True` (render dùng bản trong cache; `cache.prune` LRU không chạm tệp mới nhất) |
| C8 | Thấp-TB | Huỷ job / quá hạn **không dừng được yt-dlp** đang tải trong `to_thread` (`core_video.download`): job ghi "đã huỷ", thư mục tạm bị xoá, luồng vẫn tải/ghép 300MB; huỷ nhiều lần chồng nhiều luồng trên 1.5 vCPU. | [đề xuất] `progress_hooks` ném `DownloadCancelled` khi cờ huỷ bật (mẫu ở 4.3), hoặc chạy yt-dlp như tiến trình con qua `proc.run` |
| C9 | Thấp | `core_video.kill_running()` giết **mọi** tiến trình đang theo dõi, kể cả ffprobe của web (`bgm_upload`, `voice_ref`) đang chạy cùng lúc; `scenes._ANALYZED` khoá theo đường dẫn, > 4 mảnh video dài bị đẩy khỏi dict trước khi dựng ảnh bìa; `config._apply()` đổi `QUALITY`/`TTS_PROVIDER` giữa job → quyết định 720p với preset "high", cache TTS lẫn nhà cung cấp. | [đề xuất] gắn id job vào registry tiến trình; ảnh phân tích đi theo ngữ cảnh job; chụp cài đặt vào Job lúc bắt đầu (Kaggle đã làm vậy qua `settings_snapshot`) |
| C10 | Thấp | Hai kết nối SQLite tới `jobs.db` (`queue_manager` trên vòng lặp, `metrics` từ luồng worker với `check_same_thread=False`), không WAL: ghi chồng là `queue.save` có thể đợi tới 5 giây (chỉ log "database is locked"). Đo 0,9ms/commit nên hiếm. | [đề xuất] `PRAGMA journal_mode=WAL` hoặc đẩy `metrics.record` về vòng lặp qua `call_soon_threadsafe` |
| C11 | — | Đã tốt: SIGTERM giữ job "processing" + file đầu vào để chạy lại (tối đa 2 lần), huỷ lan đúng để tiến trình thoát (test `test_shutdown`); `notify()` không bao giờ ném; polling Telegram lỗi chỉ thử lại, web vẫn sống; autopost ghi atomically, tệp hỏng cách ly `.bad`, cờ `publishing` chống đăng đôi; chờ Kaggle chịu được 6 lần lỗi mạng; kiểm RAM cgroup trước khi tách giọng; chọn thư mục tạm theo chỗ trống. | giữ |

### 2.5 Chuỗi cung ứng (thấp-trung bình, chưa vá — cần quyết định)

`potoken_setup` cài `nodejs-wheel-binaries` không ghim phiên bản/hash (~200MB mã máy), tải tarball bgutil theo **tag**
(tag đổi được) không kiểm SHA-256, `npm install` khi thiếu lockfile; `requirements.txt` ghim khoảng, không hash;
notebook Kaggle `pip install vieneu sea-g2p faster-whisper edge-tts yt-dlp` không ghim trong phiên có Internet đang
giữ video + mẫu giọng của bạn. Chỉ Kim_Vocal_2, các gói sherpa và model OCR được kiểm SHA-256. Đề xuất: ghim
`==` + `--require-hashes` cho bộ công cụ; ghi SHA của tarball bgutil; bỏ `npm install` không lockfile; ghim
`PIP` trong `kaggle_worker`. `update_ytdlp` giữ nguyên (đã thử trong tiến trình con và tự lùi).

## Phần 3 — Đề xuất tái cấu trúc (Clean Architecture, giữ 100% chức năng và bộ test)

### 3.1 Nguyên tắc

1. **Lõi không biết gì về I/O bên ngoài**: lõi (xếp giọng, filter graph, gộp câu, kiểm nguyên liệu…) nhận dữ liệu,
   trả dữ liệu; không import `state`, `config`, `aiohttp`, `aiogram`, không gọi mạng. Cấu hình được truyền vào.
2. **Tích hợp ngoài nằm sau cổng (port)**: mỗi dịch vụ (Gemini, Groq, CapCut, edge-tts, Kaggle, Facebook, TikTok,
   yt-dlp) là một adapter có cùng giao diện nhỏ; dự phòng/thử lại nằm ở một nơi.
3. **Một bộ điều phối dựng duy nhất** dùng chung cho Botkeep và Kaggle (`kaggle_job` chỉ còn là "vỏ" chạy bộ
   điều phối đó với adapter của Kaggle).
4. **Tương thích**: giữ tên module cũ ở gốc dưới dạng *shim* re-export trong giai đoạn chuyển, để 407 test và
   mọi `import core_video` hiện có vẫn chạy; chuyển từng gói, chạy test sau mỗi bước.

### 3.2 Cấu trúc thư mục đề xuất

```
rmvideo/                      # gói chính (src layout), cài bằng `pip install -e .`
  core/                       # thuần tính toán, không I/O mạng, không trạng thái toàn cục
    timeline.py               #   merge_sentences, plan_voice, ost_windows, teaser_window  (từ prompt_engine/core_video)
    text/                     #   ingredients, vi_text, vocab, cta (strip_sub_cta…), hook
    audio/                    #   music_bed graph, gate/duck expressions, loudness maths
    video/                    #   build_filter, look_chain, band_to_output, brand overlay
    transcript.py             #   from_whisper, chunk_units, hallucination filters
  media/                      # bao ffmpeg/ffprobe (I/O cục bộ), subprocess có timeout + kill nhóm
    ffmpeg.py                 #   run(), probe(), render(), extract_audio()…  (từ core_video)
    scenes.py, cover.py, textzone.py, subs.py, align.py, vocal_sep.py, beat.py, slideshow.py, trendfx.py
  services/                   # adapter dịch vụ ngoài, mỗi tệp một dịch vụ, cùng kiểu lỗi ServiceError
    llm/gemini.py, llm/openrouter.py, llm/router.py   (router: thứ tự, thử lại, bóc JSON, schema)
    asr/groq.py, asr/gemini.py, asr/capcut.py, asr/local_whisper.py, asr/router.py
    tts/edge.py, tts/capcut.py, tts/vieneu.py, tts/router.py
    download/ytdlp.py (một chỗ cấu hình cookies/proxy/giới hạn), download/potoken.py
    kaggle/api.py, kaggle/specs.py, kaggle/runner.py (push/wait/outputs), kaggle/job.py (chạy trong notebook)
    social/facebook.py, social/tiktok_listing.py
  app/                        # điều phối (use-cases), không biết web hay Telegram
    settings.py               #   schema cài đặt có kiểu (một nguồn sự thật: validate + options + secret)
    jobs/models.py            #   Job, trạng thái
    jobs/queue.py             #   SQLite + asyncio.Queue
    jobs/worker.py            #   ngân sách giờ, huỷ, dọn
    remake/pipeline.py        #   process(): tải → phân tích → viết → render → giao (gọi orchestrator)
    remake/orchestrate.py     #   assemble(): compose_voice → subs → ambient → render — DÙNG CHUNG với Kaggle
    remake/analysis.py        #   bóc lời + phân tích hình (Kaggle hoặc cục bộ) + cache
    remake/teaser.py, remake/music_only.py, remake/long_video.py, remake/photo.py
    autopost/                 #   chọn video, giờ vàng, đăng
    storage.py, cache.py, metrics.py, health.py
  interfaces/                 # giao diện điều khiển, chỉ gọi app/
    web/ (routes, auth, sse, static/), telegram/ (handlers), console.py
  main.py                     # ghép mọi thứ (composition root), tín hiệu dừng
tests/                        # giữ nguyên; thêm tests/contract/ cho adapter (ghi/đọc bản ghi mẫu)
core_video.py, pipeline.py, … # shim: `from rmvideo.media.ffmpeg import *` (xoá dần sau khi test đã chuyển import)
```

### 3.3 Hợp đồng nhỏ cho các cổng (ví dụ)

```python
# rmvideo/services/base.py
class ServiceError(Exception):
    """Lỗi dịch vụ ngoài; retryable=True: thử lại/đổi dịch vụ có ích (503, 429 ngắn, mạng)."""
    def __init__(self, msg: str, retryable: bool = False, code: int = 0):
        super().__init__(msg); self.retryable, self.code = retryable, code

class LLM(Protocol):
    async def complete(self, prompt: str, images: list[bytes] | None, schema: type | None) -> str: ...

class ASR(Protocol):
    async def transcribe(self, audio_path: str, language: str, prompt: str) -> Transcript: ...

class TTS(Protocol):
    async def synth(self, text: str, voice: str, out_path: str) -> list[tuple[float, float, str]] | None: ...
```
Router (`services/llm/router.py`) giữ thứ tự dịch vụ, thử lại theo `ServiceError.retryable`, ghi `metrics`, bóc
JSON một lần — thay cho 7 chỗ đang tự làm.

### 3.4 Lộ trình chuyển đổi an toàn (mỗi bước xanh test mới sang bước kế)

| Bước | Việc | Rủi ro | Cách kiểm |
| --- | --- | --- | --- |
| 0 | Thêm `pyproject` src-layout, gói `rmvideo/` rỗng, CI chạy `ruff` + `pytest` | không | test hiện có |
| 1 | `app/settings.py`: schema có kiểu; `config.py` thành shim đọc/ghi qua schema; `web_panel._SETTING_OPTIONS` lấy từ schema | thấp | `test_security`, `test_autopost` (validate), test mới cho schema |
| 2 | `media/ffmpeg.py`: một hàm chạy ffmpeg/ffprobe có timeout, `start_new_session`, giết cả nhóm khi huỷ; `core_video._run_ffmpeg` gọi sang | thấp | `test_render`, `test_teaser` |
| 3 | `services/llm/router.py` + adapter Gemini/OpenRouter; `prompt_engine` gọi router (giữ nguyên prompt) | trung bình | `test_flow` (fake provider), `test_live_gemini` khi có key |
| 4 | `app/remake/orchestrate.assemble()` dùng chung; `pipeline` đường cục bộ và `kaggle_job.produce` gọi cùng hàm | trung bình (parity) | `test_kaggle*`, so output hai đường trên video mẫu (`golden`) |
| 5 | Tách `pipeline.py` thành `analysis / teaser / music_only / long_video / photo` | thấp (chỉ di chuyển) | toàn bộ test |
| 6 | Tách `web_panel.py` thành `interfaces/web/{auth,routes_jobs,routes_settings,routes_autopost,sse}.py` | thấp | `test_security`, test web |
| 7 | Xoá shim khi mọi import trong test đã đổi | thấp | toàn bộ test |

Mỗi bước là một PR nhỏ, có thể dừng ở bất kỳ bước nào mà hệ thống vẫn chạy.

## Phần 4 — Kế hoạch hành động và code mẫu

### 4.1 Rủi ro nghiêm trọng nhất — xử lý ngay

Thứ tự theo (xác suất × thiệt hại) trên máy Botkeep 2GB chạy một mình ngoài Internet:

| Ưu tiên | Rủi ro | Hậu quả nếu xảy ra | Việc làm | Trạng thái |
| --- | --- | --- | --- | --- |
| 1 | A1/A2 trần body 305MB trên đường công khai | OOM → mất job đang render, bot khởi động lại | trần JSON 1MB, login không đọc body lớn, multipart đọc từng khúc | **bản 35** |
| 2 | C1/C2 huỷ không dừng Kaggle + API huỷ chặn vòng lặp | cháy giờ GPU (30 giờ/tuần), web/Telegram treo 2 phút | abort ở mọi đường huỷ, cờ riêng từng lượt, huỷ trong luồng riêng | **bản 35** |
| 3 | C3 logo > 530KB | mọi job Kaggle âm thầm chạy trên Botkeep (40 phút CPU/video) | trần 400KB + bỏ logo quá cỡ có cảnh báo | **bản 35** |
| 4 | A3/C5/C7 ổ đĩa 2GB đầy (upload đôi, /tmp sót, tiếng nền hai bản) | mọi job sau lỗi "không đủ chỗ" | một file/lần, dọn `rmv_*`, move vào cache | **bản 35** |
| 5 | C4 cache STT/speed tự tắt khi tệp hỏng | tốn thêm gọi Groq/Gemini mỗi lần làm lại; ETA sai | đọc/ghi JSON atomic tự chữa | **bản 35** |
| 6 | A4 phiên không thu hồi được | cookie rò rỉ dùng được 30 ngày | mốc thu hồi khi đăng xuất | **bản 35** |
| 7 | A5 proxy/token/PUBLIC_URL sửa từ web | mất cookie YouTube/TikTok, video lên Kaggle người khác | chuyển sang chỉ-env hoặc hỏi lại mật khẩu | **cần bạn quyết** (đổi cách cấu hình) |
| 8 | C8 yt-dlp không dừng khi huỷ | CPU/băng thông tốn sau khi huỷ, chồng nhiều lần | hook huỷ (mẫu 4.3) | tuần tới |
| 9 | 2.5 chuỗi cung ứng | gói độc chạy với quyền bot / trong phiên Kaggle có video của bạn | ghim + hash | tuần tới |
| 10 | C9/C10 registry theo job, chụp cài đặt vào Job, WAL | lỗi hiếm, khó tái hiện | đi cùng tái cấu trúc bước 2-4 | lộ trình |

Bản 35 kèm 13 test hồi quy mới (`tests/test_review35.py`) + 3 test cho code mẫu; toàn bộ 423 test xanh, `ruff` sạch.

### 4.2 Lộ trình tái cấu trúc an toàn, không gián đoạn

Quy tắc chung: mỗi bước là **một commit nhỏ, chạy được, có thể lùi**; không đổi hành vi người dùng thấy; sau mỗi
bước chạy `ruff check . && pytest -q` (423 test) và một job thật trên video mẫu (`golden.py`) trước khi deploy;
deploy bước kế chỉ sau khi bước trước chạy ổn ≥ 3 ngày với autopost.

| Tuần | Bước | Việc cụ thể | Điều kiện xanh | Lùi lại |
| --- | --- | --- | --- | --- |
| 0 | Vá khẩn | bản 35 (đã xong) + quyết định A5 + ghim chuỗi cung ứng | 423 test, job thật | `git revert` một commit |
| 1 | Nền | `pyproject` src-layout, gói `rmvideo/` rỗng + shim; CI chạy ruff + pytest trên 3.11/3.13; sao lưu `jobs.db`/`settings.json` hằng ngày (cron trên Botkeep hoặc `maintenance()`) | test như cũ | xoá gói rỗng |
| 1 | Cài đặt có kiểu | đưa `samples/rmvideo/app/settings.py` vào `rmvideo/app/settings.py`; `config.save_settings`/`describe_settings`/`_SETTING_OPTIONS` gọi qua schema (giữ tên hàm cũ) | `test_security`, `test_autopost`, `test_settings*` | shim gọi lại code cũ |
| 2 | Tiến trình con | `rmvideo/media/proc.py` (mẫu đã test): `core_video._run_ffmpeg_full`, `vocal_sep.remove_voice`, `offline.*`, ffprobe của web gọi `proc.run(...)`; `kill_running()` → `proc.kill_all(job_id)` | `test_render`, `test_teaser`, `test_shutdown` | từng hàm một, mỗi hàm một commit |
| 2 | Huỷ yt-dlp | hook `DownloadCancelled` theo cờ huỷ của job (4.3) | test giả lập hook | bỏ hook |
| 3 | Router AI | `rmvideo/services/router.py` (mẫu); `prompt_engine.generate/generate_timed/post_only/shorten`, `cover.ai_judge`, `highlights.choose` gọi `complete()`; prompt giữ nguyên từng chữ | `test_flow`, `test_live_gemini` khi có key; so JSON ra trên 3 video mẫu | từng hàm một |
| 4 | Một bộ dựng | `app/remake/orchestrate.assemble(spec, synth, tmp)` dùng chung; `pipeline` đường cục bộ và `kaggle_job.produce` cùng gọi; `produce_bed` ↔ `_music_only` tương tự | `test_kaggle*`; so **byte-level** `ffprobe`/LUFS output hai đường trên cùng spec | giữ hai đường song song tới khi bằng nhau |
| 5 | Tách `pipeline.py` | `analysis / teaser / music_only / long_video / photo / deliver`; `pipeline` trả kết quả, `jobs._run_one` giao (Telegram/web/auto) → pipeline không import `state` | toàn bộ test | chỉ di chuyển mã, không đổi logic |
| 6 | Tách `web_panel.py` | `interfaces/web/{auth, jobs, settings, media, autopost, sse}.py`; middleware bảo mật, giới hạn body ở một chỗ | `test_security`, `test_review35` | — |
| 7 | Dọn | xoá shim khi import trong test đã đổi; `metrics` thành hook để bundle Kaggle bỏ `queue_manager`/sqlite; đổi tên `_call_gemini`, `_run_ffmpeg`, `_ANALYZED` thành API công khai | toàn bộ test | — |

Điểm dừng an toàn ở bất kỳ tuần nào: hệ thống vẫn chạy nguyên chức năng. Việc **không** làm trong lộ trình này:
tăng số worker (thiết kế 1 worker là đúng với 1.5 vCPU; mọi registry toàn cục phải theo job trước đã), đổi SQLite
sang DB khác, đổi framework web.

### 4.3 Code mẫu chuẩn production cho các vùng trọng yếu

Ba module trong `samples/rmvideo/` đã chạy và có test (`tests/test_samples.py`); đây là hình mẫu cho bước 1-3:

**(a) Tiến trình con có timeout, giết cả nhóm, stderr có trần** — `samples/rmvideo/media/proc.py`

```python
async def run(cmd: list[str], *, timeout: float, stdout_bytes=False, nice=0) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=DEVNULL, stdout=PIPE if stdout_bytes else DEVNULL,
                                                stderr=PIPE, start_new_session=True,
                                                preexec_fn=(lambda: os.nice(nice)) if nice else None)
    RUNNING.add(proc)
    try:
        async with asyncio.timeout(timeout):
            err = await _drain(proc.stderr, STDERR_KEEP)       # chỉ giữ 16KB cuối, ffmpeg in hàng MB cũng không phình RAM
            out = await proc.stdout.read() if stdout_bytes else b""
            code = await proc.wait()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _kill_group(proc)                                    # os.killpg: ffmpeg do tiến trình con gọi cũng chết
        await proc.wait()
        raise
    finally:
        RUNNING.discard(proc)
    if code != 0:
        raise ProcError(cmd, code, err.decode("utf-8", "replace"))   # lỗi có kiểu: mã thoát + đuôi stderr
    return ProcResult(out, err.decode("utf-8", "replace"), seconds)
```
Test chứng minh: `sh -c "sleep 30 & wait"` quá hạn 0,5s → cả `sleep` cháu bị giết; lỗi ffmpeg trả `ProcError`
có tên tệp trong stderr.

**(b) Cài đặt có kiểu, một nguồn sự thật** — `samples/rmvideo/app/settings.py`

```python
@dataclass(frozen=True)
class Setting:
    key: str; default: str; help: str
    options: tuple[tuple[str, str], ...] = ()     # có options -> web hiện <select>, validate tự động
    secret: bool = False                          # che trên web, KHÔNG vào snapshot gửi Kaggle
    validate: Callable[[str], str] | None = None  # chuẩn hoá hoặc ném ValueError
    group: str = "chung"

class Settings:
    def save(self, updates: dict):                 # kiểm từng khoá theo schema, ghi atomically, tạo file đã 0600
    def describe(self) -> dict:                    # cho web: giá trị (che bí mật), options, nhóm, chú thích
    def snapshot(self, exclude_prefixes=()) -> dict:  # không bao giờ có secret, dù ai thêm khoá mới sau này
```
Thay cho chuỗi `if k == ...` 70 dòng trong `config.save_settings` và bảng `_SETTING_OPTIONS` tách rời trong
`web_panel` (B3, D5).

**(c) Bộ định tuyến dịch vụ AI** — `samples/rmvideo/services/router.py`

```python
class ServiceError(Exception):           # retryable=True: 503/429 ngắn/mạng; wait: giây nên chờ (hết hạn mức ngày -> đổi dịch vụ)
@dataclass
class Provider: name: str; call: Callable[..., Awaitable[str]]; tries: int = 3; max_wait: float = 45.0

async def complete(providers, prompt, *, images=None, schema=None, record=None) -> tuple[str, dict]:
    """Thứ tự dịch vụ -> thử lại lỗi tạm -> đổi dịch vụ -> bóc JSON (kể cả ```json và dấu phẩy thừa) -> ghi metrics."""
```
Thay cho 7 vòng Gemini → OpenRouter tự viết (D7).

**(d) Các đoạn đã đưa thẳng vào bot ở bản 35** (xem diff commit "Bản 35"):

```python
# web_panel.py — trần body chỉ cho JSON; đường công khai không đọc body lớn
app = web.Application(client_max_size=JSON_MAX, middlewares=[_secure_headers])
if (request.content_length or 0) > LOGIN_BODY_MAX:
    raise web.HTTPRequestEntityTooLarge(max_size=LOGIN_BODY_MAX, actual_size=request.content_length)

async def _read_capped(part, limit: int, too_big: str) -> bytes:     # multipart đọc từng khúc, vượt trần dừng ngay
    data = bytearray()
    while chunk := await part.read_chunk(1 << 20):
        data += chunk
        if len(data) > limit:
            raise web.HTTPBadRequest(text=too_big)
    return bytes(data)

# kaggle_worker.py — cờ dừng riêng từng lượt; huỷ phiên trong luồng riêng
def abort():
    run = _ABORT["run"]
    if not run:
        return
    run["flag"] = True
    asyncio.get_running_loop().run_in_executor(None, _cancel_session, run["slug"])

# cache.py — tệp trạng thái JSON tự chữa
def read_json_file(path, default):   # hỏng -> default (lần ghi sau đè lại)
def write_json_file(path, data):     # tạm + os.replace
```

**(e) Mẫu còn lại cần làm (C8) — dừng yt-dlp khi huỷ job**

```python
# core_video.py
CANCEL = threading.Event()                     # kill_running() set; jobs._run_one clear() lúc bắt đầu job

def _add_cookies(opts: dict, tmpdir: str):
    ...
    def _stop(d):
        if CANCEL.is_set():
            raise yt_dlp.utils.DownloadCancelled("job đã huỷ")
    opts.setdefault("progress_hooks", []).append(_stop)      # yt-dlp gọi hook mỗi khúc tải -> thoát trong < 1 giây
```

### 4.4 Checklist vận hành production (ngoài mã)

1. **Biến môi trường**: `WEB_PASSWORD` ≥ 12 ký tự, `BOT_TOKEN`, `ALLOWED_USERS`; sau khi phiên làm việc này kết thúc
   đổi token Kaggle và key Gemini như bạn đã định. `PUBLIC_URL` nên đặt qua env (A5).
2. **Sao lưu**: `jobs.db`, `settings.json`, `autopost.json`, `assets/` mỗi ngày (vài MB); `cache/`, `outputs/`,
   `work/` không cần.
3. **Giới hạn tài nguyên**: Botkeep 2GB — giữ `MAX_QUEUE=5`, một worker; theo dõi `/health` (chỉ `ok/pending`) từ
   ngoài (UptimeRobot 5 phút); log xoay vòng bởi Botkeep.
4. **Cập nhật**: `yt-dlp` qua lệnh console `update_ytdlp` (đã có thử + lùi); các gói còn lại ghim `==` sau 2.5.
5. **Sau mỗi deploy**: xem log 10 phút đầu (ffmpeg selftest, đo tốc độ dựng, cảnh báo mật khẩu/ALLOWED_USERS),
   chạy một job 60s thật, kiểm `metrics` tỉ lệ rơi dự phòng Kaggle → Botkeep.
