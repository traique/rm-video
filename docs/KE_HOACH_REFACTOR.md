# Kế hoạch refactor rm-video (từ bản 40) — phân tích ngược kiến trúc & lộ trình

> Trạng thái: **ĐÃ THỰC HIỆN (bản 41)** theo 5 quyết định của chủ dự án ở mục 8: gói `rmvideo/` + mặt tiền tương
> thích; sửa lỗi ghi chú bị đè ngay trong đợt; nhập lại mật khẩu khi đổi khoá nhạy cảm (A5); Python tối thiểu 3.11;
> làm đủ các phase. Mục "Trạng thái thực hiện" ngay dưới ghi kết quả; phần còn lại của tài liệu là bản phân tích và
> kế hoạch gốc (dẫn `tệp:dòng` tại commit `4f1b2f9`, Bản 40).

## Trạng thái thực hiện (bản 41)

Kiểm tra cuối: `ruff check .` sạch; `mypy` sạch cho 50 tệp của gói `rmvideo/` (mã chuyển nguyên văn còn nới lỏng, ghi
trong `pyproject.toml`); `pytest` 441 → 503 test xanh (+ 5 bỏ qua cần key / model offline); 25 snapshot pipeline và
snapshot prompt giữ nguyên qua mọi bước (chỉ đổi 2 chỗ có chủ đích, ghi trong commit: lỗi ghi chú bị đè; bước hiển thị
của job cha video dài giờ là bước thật mới nhất). Mỗi bước một commit, đã đẩy lên nhánh làm việc.

Chạy thật: (1) trên **Kaggle** với tài khoản của bạn — một việc remake 10 giây (video mẫu công khai, giọng edge, CPU,
không tốn giờ GPU) bằng bundle mới (278KB, 36 tệp): xong trong 45-86 giây, video 720x1280 đúng 10,000s, phụ đề khớp
từng từ. Lần chạy đầu bắt được một lỗi do dời file mà test chưa phủ (font tiêu đề / logo tìm sai thư mục -> chữ tiêu
đề mất dấu), đã sửa + thêm test; (2) khởi động `main.py` thật (chỉ web): `/health`, đăng nhập, đọc cài đặt, đổi
`PUBLIC_URL` bị hỏi lại mật khẩu, SIGTERM thoát gọn.

| Phase | Đã làm | Khác kế hoạch / ghi chú |
| --- | --- | --- |
| 0 Lưới an toàn | `tests/test_snapshot_pipeline.py` (25 kịch bản: Botkeep/Kaggle, phân tích Kaggle, duyệt, không lời, mở đầu, đường đọc một mạch, 🎵, 📸, tải về, cắt phần lô Kaggle có phần lỗi, đoạn hay), `test_snapshot_prompts.py`, `test_kaggle_bundle.py`; py311 + mypy + CI 3.11-3.13; sửa lỗi ghi chú bị đè | — |
| 1 Nền móng | schema cài đặt + A5 + C9 (cài đặt dựng chờ job xong), `rmvideo/media/proc.py` (theo job, giết cả nhóm), dừng yt-dlp (C8), SQLite WAL (C10), bundle Kaggle tự tính, cắt vòng import config↔autopost, core_video→kaggle_worker | mẫu `samples/` đã thành mã thật rồi xoá |
| 2 Kiểu & ngữ cảnh | enum `Mode/LongMode/Status/Source` (StrEnum, jobs.db cũ đọc y nguyên), `Job.note()`, bus sự kiện thay 3 vòng hỏi lại, dữ liệu theo phạm vi job, `kaggle_worker.is_enabled()`, `job_factory` | `JobContext` làm bằng contextvar (tiến trình theo job + kho dữ liệu theo job) thay vì một đối tượng truyền tay, để không phải đổi chữ ký hàng trăm hàm cũ |
| 3 Tích hợp ngoài | `services/llm.py` (6 vòng Gemini→OpenRouter), `prompt_engine.call_gemini` công khai, `backends.try_produce`, `fetch.ytdlp_base_options` | ASR/TTS vốn đã ở một chỗ mỗi loại (`transcribe.asr_order`, `tts_engine.synth_clip_ex`) nên giữ |
| 4 Điều phối | `pipeline.py` → `rmvideo/app/flow/` (10 module), `assemble()` chung Botkeep/Kaggle, lớp `Remake`, `dispatch` | `delivery` vẫn gửi Telegram qua `state.bot` (đã tách khỏi pipeline nhưng chưa là port riêng) |
| 5 Giao diện | `interfaces/web/` (auth, http, 5 nhóm route, app), `interfaces/telegram/` (access, keyboards, handlers, bot) + chặn nút trên tin nhắn cũ | handler Telegram vẫn là một hàm đăng ký (giữ đúng thứ tự khớp tin nhắn của aiogram) |
| 6 Chia core_video | `rmvideo/media/` ff, fetch, tts_engine, voiceplan, filtergraph, renderer (tách bằng AST, tham chiếu chéo `module.tên`) | — |
| 7 Hiệu năng | ảnh bìa + đoạn hay khỏi vòng lặp sự kiện; bớt ffprobe mỗi đoạn giọng (95→56ms, kết quả giống hệt); đo song song ≤ 4 | xem "Còn lại" |
| 8 Dọn dẹp | README (kiến trúc, bảo mật, WAL), CHANGELOG bản 41, tài liệu này | mặt tiền ở gốc **giữ lại** (xem dưới) |

**Mặt tiền tương thích** (`rmvideo/compat.py`): `pipeline.py`, `core_video.py`, `web_panel.py`, `tg_bot.py` còn ở gốc vì
(1) ~650 chỗ test và notebook Kaggle dùng tên cũ, (2) ghi một tên qua mặt tiền (kể cả `monkeypatch`) được chuyển tới
đúng module đang dùng tên đó, nên giữ lại không có rủi ro bản vá "âm thầm vô hiệu". Mã mới trong gói gọi thẳng module
thật. Bỏ hẳn mặt tiền = đổi import trong test, làm dần khi sửa từng test.

**Còn lại (có lý do, chưa làm):**
1. `vocal_sep` float32 / overlap-add vector hoá: đổi số học của âm thanh ra — cần đo SNR so bản cũ trên video mẫu thật
   trước khi nhận, không làm "mù".
2. Giải mã âm thanh nguồn một lần dùng chung cho bóc lời / tách giọng / đo độ to: ba bước chạy ở ba nơi (một trong
   tiến trình con riêng để trả RAM), gộp cần đổi cách tách giọng; lợi ích nhỏ khi phần nặng đã lên Kaggle.
3. Kaggle Dataset chứa wheel + model để bỏ ~2 phút cài đặt mỗi phiên: cần tạo dataset trên tài khoản Kaggle của bạn.
4. Chú thích kiểu cho mã chuyển nguyên văn (`rmvideo/app/flow/{analysis,teaser,music,photo,long_video,delivery}`,
   `rmvideo/media/{ff,fetch,...}`) — mypy đang nới lỏng cho các module này.
5. Chuỗi cung ứng (mục 2.13): ghim phiên bản pip trong notebook Kaggle và gói PO token.

## 0. Bối cảnh và cách đọc

- Repo đã có `docs/DANH_GIA_KIEN_TRUC_PRODUCTION.md` (bản 35, 13/11/2026): rà bảo mật + đề xuất Clean Architecture.
  Từ đó tới nay có thêm 5 bản tính năng (36–40) nhưng **chưa bước nào của lộ trình tái cấu trúc được làm**: gói
  `rmvideo/` chưa có (chỉ có 3 mẫu trong `samples/`), các mục C8/C9/C10/A5/chuỗi cung ứng vẫn mở (kiểm lại ở mục 2.13).
- Plan này **kế thừa** tài liệu đó, không lặp lại phần bảo mật đã vá, mà tập trung vào: luồng dữ liệu thật, vì sao
  mã khó sửa, và một lộ trình refactor **có lưới an toàn đủ chặt để giữ 100% chức năng**.
- Quy mô: ~16.000 dòng Python chạy thật (42 module gốc + `capcut_tts_api/`), ~7.500 dòng test (368 hàm test),
  2 trang HTML. Một tiến trình asyncio chạy trên Botkeep (1.5 vCPU, 2GB đĩa) + mã lõi được nén gửi lên Kaggle chạy.
- Baseline trước refactor: `ruff check .` sạch; `pytest` 441 passed, 5 skipped (chi tiết mục 7).

---

## 1. Phân tích kiến trúc hiện tại

### 1.1 Topology tiến trình

```
                         ┌────────────── 1 tiến trình asyncio (main.py) ──────────────┐
 Trình duyệt ──HTTP/SSE──▶ web_panel.build_app (aiohttp, 30 route)                     │
 Telegram ──long poll────▶ tg_bot.setup (aiogram)                ──┐                    │
 Console stdin ──────────▶ admin.console                           │ tạo Job            │
 autopost.run_forever (120s) ─────────────────────────────────────┤ (11 chỗ khác nhau) │
                                                                   ▼                    │
                       queue_manager.JobQueue: asyncio.Queue + jobs.db (JSON blob)      │
                                                                   │ 1 worker           │
                       jobs.worker → _run_one (ngân sách giờ, huỷ, thư mục tạm)         │
                                                                   ▼                    │
                       pipeline.run_job → process_split | process_highlights | process  │
                                 │                       │                              │
              core_video / scenes / cover / transcribe / prompt_engine / vocal_sep ...  │
                                 │                       │                              │
                     Botkeep (ffmpeg, onnx CPU)   kaggle_worker ──zip mã + spec──▶ Kaggle notebook
                                 │                                          (kaggle_job.produce/analyze)
                       pipeline.deliver → outputs/ + state.bot (Telegram) ; autopost đọc queue.jobs → Facebook
 maintenance(), healthcheck, _ffmpeg_selftest chạy nền cùng vòng lặp                        │
                         └─────────────────────────────────────────────────────────────┘
```

### 1.2 Luồng dữ liệu end-to-end của một job remake (đường chính)

| # | Giai đoạn | Nơi xử lý | Dữ liệu vào → ra | Trạng thái nằm ở đâu |
|---|---|---|---|---|
| 1 | Nhận yêu cầu | `web_panel.add_job/upload`, `tg_bot.pick_duration`, `autopost._enqueue`, `review.approve_job` | form / callback / link → `Job` (dataclass 45 trường) | `JobQueue.jobs` (RAM) + `jobs.db` |
| 2 | Lập lịch | `jobs._run_one` | chọn thư mục tạm, ngân sách giờ, task + 1 vòng poll `job.step` mỗi 1s | `state.current` (1 khe toàn cục) |
| 3 | Lấy nguồn | `pipeline._process:700-729` → `core_video.download` (yt-dlp/tikwm) | link → `tmp/input.mp4`, `title`, `desc`, `source_meta` | biến cục bộ + `job.source_meta` |
| 4 | Phân tích | `kaggle_analysis` (Kaggle) **hoặc** `extract_audio` + `transcribe_segments` + `scenes.analyze_cached` | video → `text`, `raw_segs`, `cuts`, `bands`, ảnh thu nhỏ | ảnh thu nhỏ: `scenes._ANALYZED[path]` (dict toàn cục) ; cache `cache/` |
| 5 | Quyết định | `promo_cut`, `no_speech`→`_music_only`, `plan_teaser` | segs → cắt quảng cáo, nhánh không lời, đoạn mở đầu | `job.cut_at`, `job.teaser`, `job.error` (ghi chú) |
| 6 | AI viết lời | `prompt_engine.generate_timed_any` (Gemini→OpenRouter) | segs + ảnh → `timed.segments`, `post`, `hook` | `job.script/post/hook`, `prompt_engine._VOICE_RATE` (contextvar) |
| 7 | Cổng duyệt | `_process:1014-1026` | dừng, trả chuỗi `"REVIEW"`, chuyển nguồn sang `uploads_in/` | `job.approved`, `job.local` |
| 8 | Rút gọn + kế hoạch giọng | `_shorten`, `core_video.plan_voice` | lời → lời ngắn hơn, `plan` | biến cục bộ |
| 9a | Dựng trên Kaggle | `kaggle_worker.build_spec` → `produce` → `kaggle_job.produce` | spec JSON → `output.mp4`, `plan`, `srt` | `kaggle_worker._ABORT/_LINKS`; lô nhiều phần qua contextvar `_COLLECT` → `Deferred` |
| 9b | Dựng trên Botkeep | `synth_clip_cached` → `compose_voice` → `words_for_track`/`write_ass` → `_ambient` (tách giọng) → `core_video.render` | lời → `voice.wav`, `subs.ass`, `ambient.wav` → `output.mp4` | `core_video.RUNNING` (tiến trình con, toàn cục) |
| 10 | Ảnh bìa | `cover_mod.make_cover` (task song song) | khung hình + AI chấm → `cover.jpg` | `tasks` list trong `process` |
| 11 | Giao | `pipeline.deliver` | move → `outputs/`, Telegram gửi file/link, `queue.log_source` | `job.parts`, `jobs.db` |
| 12 | Tự đăng | `autopost._finish_pending` (poll 120s) | `job.parts` → Graph API (Reels/Video), hẹn giờ | `autopost.json` + `_POSTED` |

Nhánh khác dùng chung khung này: 🎵 music (`_process:742-785`), 📸 photo (`process_photo`), ⬇️ download,
✂️ split / ✨ highlight (`_remake_pieces` tạo **job con** gọi lại `process`), video không lời (`_music_only`),
đường cũ "đọc một mạch" (`_process:1177-1216`).

### 1.3 Mô hình dữ liệu

`queue_manager.Job` (`queue_manager.py:22-69`) gộp **bốn loại dữ liệu khác vòng đời** vào một dataclass 45 trường,
lưu nguyên khối JSON vào `jobs.data`:

| Nhóm | Trường | Ai ghi |
|---|---|---|
| Đầu vào (bất biến sau khi tạo) | `url, local, tg_file_id, music_*, images, tg_images, title_hint, mode, long_mode, duration, voice, subs, cover, quality, review, music_mix, source, chat_id` | giao diện |
| Trạng thái chạy | `status, step, timeline, started_at, finished_at, attempts, cancel, out_dur` | worker + pipeline |
| Kết quả | `script, post, hook, parts, output, srt, final_segments, error, no_autopost` | pipeline |
| Trung gian dùng lại khi chạy tiếp | `approved, teaser, cut_at, pieces, remote_src, post_keep, source_meta` | pipeline, review |

`mode`, `long_mode`, `status` là chuỗi tự do (chú thích `mode: "cook" | "review"` ở `queue_manager.py:24` đã sai —
còn `music/photo/download`). `job.error` vừa là **lỗi** vừa là **kênh ghi chú ℹ️** (24 chỗ nối chuỗi trong
`pipeline.py`). `job.step` là chữ hiển thị nhưng **bị regex phân loại** để tính thời gian từng giai đoạn
(`queue_manager.PHASES`, `queue_manager.py:74-112`).

### 1.4 Độ ghép nối (đo bằng AST)

| Chỉ số | Giá trị | Ý nghĩa |
|---|---|---|
| Module import `config` | 25/42 | config là trạng thái toàn cục sống, đổi được lúc chạy (`config._apply` ghi `globals()`) |
| Fan-out `pipeline` | 23 module nội bộ | "god module" điều phối |
| Vòng phụ thuộc (SCC, tính cả import trễ) | **1 vòng 11 module**: autopost, cache, config, core_video, kaggle_api, kaggle_worker, metrics, offline, queue_manager, state, vocab | không tách gói được nếu không cắt vòng |
| Import nội bộ đặt trong thân hàm | 87 | phần lớn để né vòng import / né gói nặng |
| Hàm dài nhất | `web_panel.build_app` 592 dòng, `pipeline._process` 519 dòng (15 hàm lồng), `tg_bot.setup` 316, `prompt_engine.generate_timed` 180 | |
| Truy cập tên riêng (`_x`) xuyên module | `core_video._run_ffmpeg` ×5, `prompt_engine._call_gemini` ×4, `scenes._ANALYZED` ×5, `scenes._keyframe_sync` ×2, … | API thật bị ẩn sau dấu gạch dưới |

`core_video.py` (1.802 dòng) là tầng "lõi" nhưng import `metrics`, `potoken_setup`, `url_guard`, `tts_capcut`, và
trễ `kaggle_worker`, `offline`: lõi dựng video biết cả về YouTube PO token lẫn Kaggle.

### 1.5 Hai môi trường chạy cùng một mã

`kaggle_worker.BUNDLE` (`kaggle_worker.py:30-33`) là **danh sách tệp viết tay** nén gửi lên Kaggle. Hệ quả:
- Thêm một import vào `core_video` mà quên thêm vào `BUNDLE` → chỉ hỏng **trên Kaggle, lúc chạy**, rồi job tự lùi về
  Botkeep (chậm hơn ~10 lần) chỉ với một dòng ℹ️. Ví dụ đang tồn tại: `core_video._vieneu_male` (`core_video.py:595`)
  import `kaggle_worker` — không có trong bundle; hiện an toàn chỉ vì Kaggle không đi vào nhánh đó.
- `metrics` import `queue_manager` chỉ để lấy `DB_PATH` → bundle phải mang theo `queue_manager` + sqlite.
- `kaggle_job.make_synth` ghi đè `config.TTS_PROVIDER` (`kaggle_job.py:131`) để chọn nhà cung cấp giọng.

---

## 2. Các vùng có vấn đề nghiêm trọng (xếp theo rủi ro × tần suất phải sửa)

### 2.1 [Rất cao] `pipeline._process` là một hàm 519 dòng điều khiển mọi thứ
- `pipeline.py:698-1216`; 15 hàm lồng chia sẻ trạng thái qua `nonlocal amb_task`, `cuts[:] = …` (`:865`),
  `sep.update(...)` (`:859`), dict `cover_src` bị sửa ở 3 chỗ; biến `segs/cuts/speech/out_dur/render_dur` bị gán lại
  sau mỗi quyết định (cắt quảng cáo, đoạn mở đầu) nên ý nghĩa của chúng phụ thuộc vị trí dòng.
- Kiểu trả về lẫn lộn: đường dẫn video | chuỗi `"REVIEW"` | `kaggle_worker.Deferred` (khi đang ở trong contextvar
  `collect`). Người gọi phải biết cả ba (`_remake_pieces:203`, `jobs._run_one:100`, `run_job:1228`).
- Ba đường dựng (Kaggle, Botkeep có mốc, "đọc một mạch") nằm chung một hàm, đường cũ tự lặp lại phần render.
- Hệ quả thực tế: lịch sử CHANGELOG có nhiều lỗi hồi quy đúng vùng này (bản 31 `int(render_dur)`, N1 bản duyệt
  render lại quảng cáo, mất chữ hook ở bản duyệt…). Mọi tính năng mới đều phải sửa hàm này.

### 2.2 [Rất cao] Hai bộ dựng phải giữ giống nhau bằng tay (Botkeep ↔ Kaggle)
- `pipeline._process:1123-1161` và `kaggle_job.produce` (`kaggle_job.py:147-214`): cùng chuỗi
  `synth → speech_duration → plan_voice → compose_voice → words_for_track/write_ass → ambient/gate → render`.
- Tương tự `pipeline._music_only` ↔ `kaggle_job.produce_bed`. Mỗi tham số mới (teaser, bed, stretch…) phải thêm
  vào: `build_spec`/`bed_spec` + `kaggle_job` + `pipeline` + test parity.

### 2.3 [Cao] Cấu hình là trạng thái toàn cục đổi được giữa job, ba nguồn sự thật
- `config._apply()` ghi đè `globals()` (`config.py:219-229`); web lưu cài đặt là áp dụng ngay **kể cả với job đang
  chạy** → một job có thể quyết 720p theo `QUALITY` cũ rồi render với preset mới, cache TTS lẫn nhà cung cấp (C9 cũ, còn mở).
- Kiểm hợp lệ: chuỗi `if k == …` 70 dòng (`config.save_settings:232-323`) + `META` + `web_panel._SETTING_OPTIONS`
  (`web_panel.py:211`) mô tả cùng một tập lựa chọn ở 3 nơi. Cờ bật/tắt là chuỗi `"0"/"1"` so sánh rải khắp nơi.
- `config.save_settings` import trễ `autopost` để kiểm link kênh → vòng `config ↔ autopost`.

### 2.4 [Cao] `Job` là god object, trạng thái bằng chuỗi, ghi chú đè mất
- Xem 1.3. Thêm một chế độ mới phải sửa ~10 tệp vì `mode/long_mode` không có kiểu.
- **Lỗi thật (nhỏ) tìm thấy khi đọc**: đường "đọc một mạch" gán đè `job.error` thay vì nối:
  `pipeline.py:1181` xoá các ghi chú trước (Kaggle lỗi, VieNeu không dùng được, đã cắt quảng cáo…), và
  `pipeline.py:1215` xoá luôn cảnh báo **"⚠️ AI viết kịch bản lỗi, đang dùng kịch bản mẫu"** khi video có dải chữ gốc.
  (`no_autopost` vẫn được đặt nên tự đăng không bị ảnh hưởng; chỉ người dùng mất cảnh báo.)
- Phân loại giai đoạn bằng regex trên chữ tiếng Việt hiển thị: đổi câu chữ một bước là thống kê thời gian sai mà
  không test nào báo.

### 2.5 [Cao] Registry toàn cục thay cho ngữ cảnh job
| Biến | Chỗ | Vấn đề |
|---|---|---|
| `scenes._ANALYZED` | `scenes.py:9`, đọc/ghi từ `pipeline` 4 chỗ + `kaggle_job` | khoá theo **đường dẫn file**, private, không dọn theo job |
| `core_video.RUNNING` + `kill_running()` | `core_video.py:926-935` | giết **mọi** tiến trình kể cả ffprobe của request web |
| `state.current` | `state.py:17` | 1 khe → cứng 1 worker |
| `kaggle_worker._ABORT`, `_LINKS`, `_COLLECT` | `kaggle_worker.py:46-47,128,496` | `_COLLECT` (contextvar) **đổi kiểu trả về** của `process()` |
| `prompt_engine._VOICE_RATE` | `prompt_engine.py:503` | tham số ẩn ảnh hưởng số chữ AI viết |
Hệ quả: không thể tăng worker, không thể test một bước đơn lẻ mà không dựng lại cả trạng thái toàn cục.

### 2.6 [Cao] Quản lý tiến trình con rải rác, không timeout, không giết cả nhóm
- ~41 chỗ tạo tiến trình con trong 17 tệp, ít nhất 4 bản "spawn + RUNNING + kill" (`core_video`, `vocal_sep`,
  `offline`, `beat`).
- `core_video._run_ffmpeg_full` (`:938-953`) dùng `communicate()` → giữ toàn bộ stderr trong RAM, **không timeout**,
  không `start_new_session`/`killpg` (grep: 0 chỗ) → tiến trình cháu sống sót khi huỷ.
- yt-dlp chạy trong `to_thread` không huỷ được (C8 cũ): chỉ `download_format` có `progress_hooks` (`core_video.py:373`)
  và hook đó chỉ báo phần trăm.

### 2.7 [Trung bình–Cao] Logic tích hợp ngoài bị nhân bản
| Mảng | Số bản | Vị trí |
|---|---|---|
| Vòng "Gemini → OpenRouter → thử lại → bóc JSON" | 10 khai báo provider + 4 lời gọi trực tiếp `_call_gemini` | `prompt_engine` ×8, `highlights` ×2, `cover:207`, `healthcheck:58`, `transcribe:371` (client Gemini riêng) |
| Cấu hình yt-dlp | 5 `YoutubeDL(...)` | `core_video:294,322,383,486` dùng `_add_cookies`; `autopost:332` tự dựng lại |
| Vỏ chạy Kaggle (try → produce → metrics → ghi chú ℹ️) | 4 biến thể | `_kaggle_render`, `_music_only`, nhánh chính `_process`, `_remake_pieces` |
| Tạo `Job` | 11 chỗ / 6 tệp | `web_panel` ×3, `tg_bot` ×4, `autopost`, `review`, `pipeline`, `golden` |
| `post_title = job.hook or …` | 4 | `pipeline.py:598,1077,1151,1205` |
| Danh sách đoạn đã duyệt `{"i","start","slot","span",…}` | 3 | `pipeline.py:1015,1110`, `review.py:548` |

### 2.8 [Trung bình] Điểm nghẽn hiệu năng trên máy 1.5 vCPU
1. **SQLite đồng bộ trên vòng lặp sự kiện**: `queue.save` (9 chỗ gọi) `commit` ngay trong coroutine; `metrics` mở
   kết nối thứ hai từ luồng khác, không WAL, không `busy_timeout` (C10 cũ, còn mở).
2. **SSE tính lại toàn bộ payload mỗi giây cho mỗi client** (`web_panel.py:433-460`, tối đa 8 client): mỗi lượt
   `os.path.exists` cho mọi phần của mọi job + `phase_times` + 2 lần `json.dumps`.
3. **Ba vòng poll để phát hiện `job.step` đổi**: `_timeline` 1s (`jobs.py:84`), `_mirror` 2s (`pipeline.py:190`),
   SSE 1s — vì `step` là thuộc tính trần, không phát sự kiện.
4. **Đọc file đồng bộ trong coroutine**: `pipeline._highlight_images:290` (`open().read()` từng ảnh).
5. Số lượt gọi ffmpeg/ffprobe (đã đo ở tài liệu cũ, mục 1.3): nguồn giải mã audio 3 lần, mỗi đoạn giọng 1 ffprobe
   + 1 ffmpeg (`speech_duration`), tách giọng numpy complex128.
6. Mỗi phiên Kaggle cài lại thư viện + tải model (~2 phút/phiên, 2 phiên/job).

### 2.9 [Trung bình] Tầng giao diện là closure khổng lồ
- `web_panel.build_app` 592 dòng chứa 30 handler lồng; `tg_bot.setup` 316 dòng. Không test được từng handler mà
  không dựng cả app; logic dựng `Job` từ input (kiểm thời lượng, giọng, chế độ dài) lặp giữa web và Telegram.

### 2.10 [Trung bình] API boolean ngược nghĩa
- `kaggle_worker.enabled()` / `usable()` trả **chuỗi rỗng khi DÙNG ĐƯỢC**, chuỗi lý do khi không
  (`kaggle_worker.py:182-200`). Đọc `if kaggle_worker.enabled(): return None` (`pipeline.py:535`) dễ hiểu ngược;
  `not kaggle_worker.enabled()` nghĩa là "đang bật" (`pipeline.py:109,147,580`, `jobs.py:172`).

### 2.11 [Trung bình] Rủi ro khi mở rộng
- **Thêm worker**: chặn bởi 2.5 (registry toàn cục) + SQLite trên vòng lặp.
- **Thêm chế độ / nền tảng nguồn / provider AI**: phải sửa `_process`, `Job`, `config` (3 nguồn), web + Telegram
  (2 bộ dựng Job), Kaggle (spec + bundle + produce).
- **Kaggle notebook ≤ 1MB**: bundle hiện ~260KB mã + logo ≤ 400KB; gom mã vào gói lớn hơn phải đo kích thước.
- **Dữ liệu lưu**: `Job` JSON không có số phiên bản; đổi tên/kiểu trường là hỏng job đang chờ trong `jobs.db`.

### 2.12 [Trung bình] Khó bảo trì: test & công cụ
- Test xếp theo **đợt rà soát** (`test_review31/35/38/40.py`, `test_fixes.py`, `test_panel36.py`) thay vì theo
  module → khó biết module nào được phủ. Nhiều test import tên riêng (`_process`, `_ANALYZED`, `_call_gemini`…) nên
  refactor nào cũng vỡ test dù hành vi không đổi.
- `ruff` chỉ bật `F, E9, B`; không kiểm kiểu; `target-version = py310` trong khi README/CI yêu cầu 3.11+.

### 2.13 Đối chiếu các mục còn mở từ bản 35
| Mục | Trạng thái hiện tại (đã kiểm mã) |
|---|---|
| C8 huỷ không dừng yt-dlp | **mở** — không có hook `DownloadCancelled` |
| C9 `kill_running` toàn cục, `_ANALYZED` theo path, config đổi giữa job | **mở** |
| C10 SQLite không WAL, 2 kết nối | **mở** — không có `journal_mode` |
| A5 `YTDLP_PROXY`/`PUBLIC_URL`/token Kaggle sửa từ web | **mở** — chờ chủ quyết định |
| 2.5 chuỗi cung ứng (pip/npm không ghim trên Kaggle/potoken) | **mở** (`kaggle_worker.py:35-40`) |
| Lộ trình tái cấu trúc bước 0–7 | **chưa bắt đầu** |

---

## 3. Kiến trúc đích

### 3.1 Nguyên tắc
1. **Một hướng phụ thuộc**: `interfaces → app → domain` ; `app → ports ← adapters`. Lõi tính toán (`domain`,
   `media/graph`) không import `config`, `state`, aiohttp, aiogram, không gọi mạng.
2. **Mọi trạng thái của một job nằm trong `JobContext`**, không trong biến module.
3. **Một bộ dựng duy nhất** chạy được cả trên Botkeep lẫn Kaggle.
4. **Strangler fig + shim**: tên module cũ ở gốc vẫn còn (re-export) tới khi test đã đổi import → mỗi bước deploy
   được độc lập, lùi được bằng `git revert` một commit.

### 3.2 Cấu trúc thư mục đích

```
rmvideo/
  domain/            # kiểu dữ liệu thuần, không I/O
    job.py           #   Mode, LongMode, Status (str-Enum, JSON tương thích ngược); JobSpec (đầu vào, frozen)
    notes.py         #   Notes: info()/warn() -> render ra chuỗi job.error như cũ
    progress.py      #   Phase (enum) + nhãn hiển thị; thay regex PHASES
    script.py        #   Segment, ApprovedSegment (1 định nghĩa thay 3), Teaser, VoicePlan
    sources.py       #   normalize_source/tiktok (cắt vòng config<->autopost)
  app/
    settings.py      #   schema có kiểu (nâng từ samples/), Settings.snapshot() chụp lúc job bắt đầu
    context.py       #   JobContext: spec, settings snapshot, notes, progress, proc registry, cancel token,
                     #   workdir, artifacts (thư mục ảnh thu nhỏ thay scenes._ANALYZED), clock
    jobs/            #   repository (SQLite WAL, 1 kết nối, ghi ngoài vòng lặp), queue, worker, factory (1 chỗ tạo Job)
    remake/          #   các stage: acquire, analyze, decide, write, review_gate, finalize
    assemble.py      #   assemble()/assemble_bed() — DÙNG CHUNG Botkeep & Kaggle
    backends.py      #   RenderBackend: LocalBackend | KaggleBackend | BatchKaggleBackend (thay contextvar collect)
    long_video.py, music.py, photo.py, download.py
    deliver.py       #   Delivery port: web (outputs/), Telegram, autopost — pipeline không import state/aiogram
    events.py        #   bus sự kiện tiến độ/đổi job (asyncio.Condition + số phiên bản) cho SSE, timeline, mirror
  media/
    proc.py          #   (nâng từ samples/) run(cmd, timeout, ctx) giết cả nhóm, stderr có trần
    ffmpeg.py        #   probe, cut, extract_audio, render (chỉ chạy lệnh)
    graph.py         #   build_filter, look_chain, gate/ost expressions (thuần chuỗi -> test không cần ffmpeg)
    voice.py         #   plan_voice, compose_voice, words_for_track
    scenes.py, cover.py, textzone.py, subs.py, align.py, vocal_sep.py, beat.py, slideshow.py, trendfx.py
  services/
    llm/             #   router (nâng từ samples/), gemini, openrouter
    asr/             #   groq, gemini, capcut, local; router + cache
    tts/             #   edge, capcut, vieneu(spec), offline; router với dự phòng
    download/        #   ytdlp_options() một chỗ (cookies, proxy, PO token, allowed_extractors, hook huỷ), tikwm, http
    kaggle/          #   api, specs, runner (push/wait/outputs), bundle (tự tính phụ thuộc), notebook_entry
    social/          #   facebook (graph), channel listing (fb, tiktok)
  interfaces/
    web/             #   app.py (middleware, routes table), auth.py, jobs.py, uploads.py, settings.py,
                     #   autopost.py, bgm.py, sse.py
    telegram/        #   handlers theo luồng: link, file, photo, music, review
    console.py
  main.py            #   composition root: dựng repo, settings, backends, delivery; tín hiệu dừng
# gốc repo: core_video.py, pipeline.py, config.py, … thành shim re-export tới khi xoá ở Phase 8
```

### 3.3 Quyết định thiết kế chính

| # | Quyết định | Thay cho | Lợi ích |
|---|---|---|---|
| D1 | `JobContext` truyền tường minh qua mọi stage | `_ANALYZED`, `RUNNING`, `current`, `_VOICE_RATE`, `_COLLECT`, `_ABORT` | test từng stage độc lập; mở đường >1 worker; huỷ chỉ giết tiến trình của job đó |
| D2 | `_process` thành chuỗi stage có kiểu vào/ra (`SourceInfo → Analysis → Decisions → Script → RenderResult`) | 1 hàm 519 dòng, 15 closure | mỗi stage ≤ 100 dòng, sửa một quyết định không chạm phần khác |
| D3 | `RenderBackend` (Local / Kaggle / BatchKaggle) | 3 nhánh if + contextvar đổi kiểu trả về | Kaggle lỗi → lùi Local ở **một** chỗ; lô nhiều phần là backend riêng, không ma thuật |
| D4 | `assemble()` dùng chung, Kaggle gọi đúng hàm đó | 2 bộ dựng song song | hết lệch parity |
| D5 | `Notes` + `Progress(Phase, text)` | `job.error +=` 24 chỗ, regex PHASES | ghi chú không bị đè; đổi chữ hiển thị không hỏng thống kê; UI nhận **cùng chuỗi** như cũ |
| D6 | Settings schema + snapshot bất biến mỗi job; `config.py` thành shim dùng `__getattr__` (PEP 562) đọc kho cài đặt | `globals()` + 3 nguồn kiểm hợp lệ | `config.X` cũ vẫn chạy; job không bị đổi cấu hình giữa chừng |
| D7 | Repository SQLite: WAL + `busy_timeout`, một kết nối, ghi qua `to_thread`/luồng ghi riêng; thêm cột `status` có chỉ mục, giữ cột JSON | 2 kết nối, commit trên vòng lặp | vòng lặp không chặn; không cần chuyển dữ liệu cũ |
| D8 | Bus sự kiện (số phiên bản + `asyncio.Condition`) | 3 vòng poll 1–2s | SSE chỉ gửi khi đổi, payload dựng **một lần** chia cho mọi client |
| D9 | `Availability(ok, reason)` cho Kaggle | `enabled() -> ""` ngược nghĩa | đọc đúng nghĩa, kiểu kiểm được |
| D10 | Bundle Kaggle tính phụ thuộc tự động từ entrypoint (AST) + test import trong tiến trình sạch | danh sách `BUNDLE` viết tay | không còn hỏng-chỉ-trên-Kaggle |

---

## 4. Chiến lược refactor theo phase

Quy tắc chung (giữ từ tài liệu cũ, siết thêm):
- Mỗi bước là **một PR nhỏ, chạy được, lùi được**; không đổi hành vi người dùng thấy (chữ trên UI/Telegram, file ra,
  nội dung prompt giữ **từng byte**).
- Trước khi chạm một vùng: có **test đặc tả (characterization)** chốt hành vi hiện tại của vùng đó (Phase 0).
- Sau mỗi bước: `ruff` + toàn bộ `pytest` + test snapshot + (với Phase 4, 6, 7) chạy `golden.py` trên video mẫu.
- Deploy từng phase; phase có rủi ro trung bình trở lên chạy ổn ≥ 3 ngày (có autopost) mới sang phase sau.

### Phase 0 — Lưới an toàn (bắt buộc trước mọi refactor) · ~2 ngày · rủi ro: không
| Việc | Chi tiết | Xong khi |
|---|---|---|
| 0.1 Test snapshot cho pipeline | Chạy `pipeline.process` với provider giả (LLM/ASR/TTS/Kaggle giả, ffmpeg **ghi lệnh thay vì chạy**) qua ~14 kịch bản: cook có lời (Botkeep / Kaggle), cook không lời → nhạc nền, review, teaser, cắt quảng cáo, bản đã duyệt, split, highlight, music thay nhạc, music nền, photo, download, đường "đọc một mạch", Kaggle lỗi → lùi Botkeep. Chốt: chuỗi lệnh ffmpeg, spec JSON gửi Kaggle (bỏ token ngẫu nhiên), các trường `Job` cuối (`error/script/post/hook/final_segments/srt/parts`) | snapshot ổn định qua 3 lần chạy |
| 0.2 Test bundle Kaggle | Dựng bundle, giải nén vào thư mục tạm, `import` từng module trong tiến trình con **không có** aiohttp/aiogram | phát hiện được ca `core_video → kaggle_worker` (mục 1.5) |
| 0.3 Test "prompt byte-identical" | Băm SHA của mọi prompt dựng ra cho bộ đầu vào cố định | dùng cho Phase 3 |
| 0.4 Công cụ | ruff thêm `I, UP, SIM, RUF` **chỉ cho `rmvideo/`**; pyright/mypy strict cho `rmvideo/`, mã cũ loại trừ; sửa `target-version` = py311; CI chạy thêm 3.13 | CI xanh |
| 0.5 Đo baseline hiệu năng | thời gian job 20s/60s trên máy 1 vCPU giả lập, số lượt ffmpeg/ffprobe, CPU nhàn rỗi khi 8 client SSE | số liệu ghi vào tài liệu |

### Phase 1 — Nền móng, không đổi hành vi · ~4 ngày · rủi ro: thấp
1. Gói `rmvideo/` rỗng + shim; cập nhật bundle builder (D10) để mang theo gói.
2. **Settings** (D6): đưa `samples/rmvideo/app/settings.py` vào; `config.py` thành shim; `web_panel._SETTING_OPTIONS`,
   `META`, `save_settings` đọc từ schema; chuyển `normalize_source/tiktok` sang `domain/sources.py` → cắt vòng
   `config ↔ autopost`.
3. **Tiến trình con** (nâng `samples/rmvideo/media/proc.py`): `core_video._run_ffmpeg_full` gọi sang trước, rồi lần
   lượt `vocal_sep`, `offline`, `beat`, `scenes`, `cover`… (mỗi module một commit). Registry theo job id; giữ
   `kill_running()` như shim gọi `kill_all(current_job)`.
4. **Huỷ yt-dlp** (C8): hook `DownloadCancelled` trong builder tuỳ chọn yt-dlp.
5. **SQLite** (D7): WAL + `busy_timeout`, `metrics` dùng chung repository (bỏ import `queue_manager` khỏi bundle).

### Phase 2 — Kiểu dữ liệu & ngữ cảnh job · ~4 ngày · rủi ro: thấp–trung bình
1. Enum `Mode/LongMode/Status` dạng `str` (JSON lưu y nguyên chuỗi cũ → `jobs.db` cũ đọc được); thêm `schema_v` vào JSON.
2. `Notes`, `Progress/Phase` (D5); giữ `PHASES` regex làm đường đọc dữ liệu cũ trong `steps`.
3. `JobContext` (D1): bước đầu chỉ bọc các registry hiện có (đọc/ghi qua ctx, biến module vẫn còn phía sau), sau đó
   chuyển hẳn; `Availability` cho Kaggle (D9).
4. `JobFactory`: một chỗ dựng `Job` cho web/Telegram/autopost/review/phần con/golden; kiểm hợp lệ đầu vào một lần.

### Phase 3 — Tích hợp ngoài sau cổng · ~5 ngày · rủi ro: trung bình
1. **LLM router** (nâng `samples/rmvideo/services/router.py`): 10 vòng provider + 4 lời gọi `_call_gemini` → `llm.complete()`.
   Prompt giữ nguyên từng byte (test 0.3), số lần thử/thời gian chờ giữ nguyên.
2. **ASR/TTS router**: thứ tự `ASR_ORDER`, dự phòng edge ↔ CapCut ↔ Piper ở một nơi; `metrics` ghi trong router.
3. **Download service**: `ytdlp_options(purpose)` duy nhất cho 5 chỗ (kể cả `autopost.list_tiktok_sync`).
4. **Kaggle runner**: `run_remote(ctx, spec, label)` thay 4 vỏ try/metrics/ghi chú.

### Phase 4 — Tách bộ điều phối · ~6 ngày · rủi ro: trung bình–cao (vùng lõi)
1. `assemble()` + `assemble_bed()` dùng chung (D4); `kaggle_job.produce/produce_bed` gọi chúng. Test parity: cùng
   spec chạy hai đường → so lệnh ffmpeg + `plan` + SRT giống hệt.
2. `_process` → stage (D2) + `RenderBackend` (D3); `Deferred/collect` → `BatchKaggleBackend`. Snapshot 0.1 phải
   **giống hệt**.
3. `deliver` → `app/deliver.py` (Delivery port); `pipeline` hết import `state`/aiogram.
4. Tách `long_video` (split/highlight), `music`, `photo`, `download` ra module riêng.

### Phase 5 — Tầng giao diện & sự kiện · ~4 ngày · rủi ro: thấp–trung bình
1. `web_panel` → `interfaces/web/*` với bảng route; middleware bảo mật/giới hạn body ở một chỗ (giữ nguyên hành vi
   đã kiểm trong `test_security`, `test_review35`).
2. Bus sự kiện (D8): SSE gửi khi số phiên bản đổi + nhịp 10s cho đồng hồ; payload dựng một lần/lượt; bỏ `_timeline`
   poll 1s và `_mirror` poll 2s.
3. `tg_bot` → handlers theo luồng, dùng `JobFactory`.

### Phase 6 — Chia `core_video.py` (1.802 dòng) · ~3 ngày · rủi ro: thấp (chỉ di chuyển)
`download/*`, `tts/*`, `media/voice.py` (thuần), `media/graph.py` (thuần), `media/ffmpeg.py`, loudness. Bỏ import
`kaggle_worker`/`offline`/`potoken_setup` khỏi tầng media.

### Phase 7 — Hiệu năng (mỗi mục đo trước/sau trên golden) · ~4 ngày · rủi ro: trung bình
1. Bỏ ffprobe mỗi đoạn giọng (độ dài lấy từ kết quả synth/`voiced_intervals`).
2. Giải mã audio nguồn **một lần** dùng cho bóc lời, tách giọng, đo LUFS.
3. Đọc file/numpy trên vòng lặp → `to_thread` (`_highlight_images`, `cover.score_frames`).
4. `vocal_sep`: `scipy.fft` float32, overlap-add vector hoá (từ tài liệu cũ, mục 1.3) — chỉ nhận nếu khác biệt âm thanh
   dưới ngưỡng (SNR so bản cũ) trên video mẫu.
5. Kaggle: Dataset chứa wheel + model để bỏ ~2 phút cài đặt mỗi phiên (cần tài khoản Kaggle của chủ — tuỳ chọn).

### Phase 8 — Dọn dẹp · ~2 ngày
Xoá shim khi test đã đổi import; đổi tên API riêng thành công khai; xếp lại test theo module
(`tests/unit/<module>`, `tests/contract/`, `tests/snapshot/`); cập nhật README + tài liệu kiến trúc.

**Tổng ước lượng**: ~34 ngày công cho một người, chia ~25 PR. Điểm dừng an toàn sau **mỗi** phase.

---

## 5. "Code cải tiến đạt chuẩn production" sẽ giao những gì

Không viết ở bước plan. Khi bắt đầu, mỗi PR mang theo:
- Mã mới trong `rmvideo/` có type hint đầy đủ, docstring nêu hợp đồng (vào/ra/lỗi ném ra), không biến toàn cục có
  thể đổi, I/O chặn đi qua `to_thread`, tiến trình con qua `proc.run` có timeout.
- Lỗi có kiểu (`ServiceError(retryable)`, `ProcError`, `JobCancelled`, `OverBudget`) thay cho `RuntimeError` chuỗi.
- Test đơn vị cho phần mới + snapshot cũ giữ nguyên; shim ở gốc để mã/test cũ chạy y hệt.
- Ghi chú CHANGELOG theo phong cách hiện có ("Bản 41: …").

Mẫu đã có sẵn và đã có test (`tests/test_samples.py`) làm điểm xuất phát: `samples/rmvideo/media/proc.py`,
`samples/rmvideo/app/settings.py`, `samples/rmvideo/services/router.py`.

---

## 6. Tiêu chí "giữ nguyên mọi chức năng" và cách kiểm

| Lớp kiểm | Công cụ | Ngưỡng |
|---|---|---|
| Hành vi đơn vị | toàn bộ `pytest` hiện có | 100% xanh mỗi commit |
| Quyết định pipeline | snapshot Phase 0.1 (lệnh ffmpeg, spec Kaggle, trường Job) | giống hệt; đổi có chủ đích phải duyệt diff snapshot |
| Prompt AI | SHA prompt (0.3) | giống hệt |
| Kaggle | test bundle (0.2) + test parity `assemble` hai đường | import sạch; lệnh giống hệt |
| Video ra | `golden.py` trên bộ video mẫu: ffprobe (độ dài, luồng, độ phân giải), LUFS ±0.1, SRT + `timeline.json` giống hệt | trước mỗi deploy Phase 4/6/7 |
| Dữ liệu cũ | mở `jobs.db`, `settings.json`, `autopost.json` thật (bản sao) bằng mã mới | job chờ/duyệt chạy tiếp được |
| Vận hành | log 10 phút đầu sau deploy, `metrics` tỉ lệ rơi dự phòng Kaggle→Botkeep | không tăng |

---

## 7. Baseline đã đo trong phiên này (trước refactor)

- `ruff check .`: sạch.
- `pytest -q` (Python 3.13, ffmpeg hệ thống, không mạng/token): **441 passed, 5 skipped** trong 7 phút 02 giây
  (5 ca bỏ qua là test cần key Gemini thật / gói offline). Mọi phase phải giữ đúng con số này hoặc hơn.
- Bundle mã gửi Kaggle hiện tại: 252KB (base64), trần notebook ~1MB.

---

## 8. Quyết định cần chủ dự án chốt trước khi code

1. **Bố cục gói**: tạo gói `rmvideo/` + shim (đề xuất) hay giữ cấu trúc phẳng, chỉ tách file? (Ảnh hưởng kích thước
   bundle Kaggle và đường import trong test.)
2. **Lỗi ghi chú bị đè** (`pipeline.py:1181,1215`): sửa luôn ở Phase 0 như một commit riêng (đề xuất — người dùng thấy
   thêm ghi chú bị mất trước đây), hay giữ nguyên tuyệt đối tới Phase 2?
3. **A5** (còn mở từ bản 35): chuyển `YTDLP_PROXY`, `PUBLIC_URL`, token Kaggle sang chỉ-env hoặc bắt nhập lại mật khẩu
   khi đổi — đây là thay đổi cách cấu hình nên cần bạn quyết.
4. **Phiên bản Python tối thiểu**: 3.11 (theo README/CI) — đồng ý nâng `target-version` và dùng `asyncio.TaskGroup`/
   `asyncio.timeout`?
5. **Phạm vi lần đầu**: đề xuất làm Phase 0 + Phase 1 trước (≈6 ngày, rủi ro thấp, giải quyết C8/C9/C10 và cấu hình
   đổi giữa job), rồi đánh giá lại.

Ngoài phạm vi (cố ý không làm): tăng số worker, đổi SQLite sang DB khác, đổi aiohttp/aiogram, đổi giao diện web,
đổi prompt hay thuật toán dựng.
