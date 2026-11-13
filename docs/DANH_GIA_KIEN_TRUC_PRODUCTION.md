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

### 1.6 Điểm mạnh nên giữ

- Mọi lỗi dịch vụ ngoài đều có dự phòng và ghi chú cho người dùng (Kaggle → Botkeep, Gemini → OpenRouter → mẫu,
  CapCut ↔ edge, tách giọng → cổng tiếng), có `metrics` để xem tỉ lệ rơi dự phòng.
- Hàng đợi sống qua khởi động lại; job chờ duyệt giữ nguồn; dọn ổ đĩa theo trần.
- Bộ test 407 ca chạy không cần mạng/token, dựng thật bằng ffmpeg; bộ video mẫu (`golden.py`).
- Bảo mật cơ bản đã làm: cookie phiên HMAC + muối riêng, chống CSRF theo Origin, khoá dò mật khẩu theo IP và toàn cục,
  link tải có mã riêng, `url_guard` chống SSRF (allowlist nền tảng + chặn IP nội bộ sau DNS), settings.json 0600,
  key API bị che trên giao diện, notebook Kaggle không chứa key (`settings_snapshot` loại `SECRET_KEYS`).

*(Phần 2 — bảo mật & độ tin cậy, Phần 3 — tái cấu trúc, Phần 4 — kế hoạch & code mẫu: xem tiếp bên dưới.)*

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
