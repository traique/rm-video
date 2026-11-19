"""Phía Botkeep: gửi phần nặng sang Kaggle rồi nhận video hoàn chỉnh về (xem kaggle_job.py).

Một lần chạy = một phiên notebook riêng tư `<user>/rmvideo-worker` (mỗi lần là một phiên bản mới). Notebook chứa code
cần thiết (nén + base64) và việc cần làm (JSON), KHÔNG chứa key API nào; file lớn (video tải lên, nhạc, ảnh) Kaggle tự
tải: link nền tảng gốc, hoặc link tạm có mã bí mật do web panel của Botkeep phát (cần PUBLIC_URL; notebook < 1MB nên
không nhúng file được). GPU chỉ khi có giọng VieNeu (còn giờ GPU trong tuần); việc khác chạy CPU 4 lõi, không tốn giờ GPU.
Lỗi ở bất kỳ bước nào -> ném KaggleError, pipeline làm tiếp trên Botkeep như cũ."""
import asyncio
import base64
import contextvars
import json
import logging
import os
import re
import secrets
import time
import urllib.parse

import config
import kaggle_api
from kaggle_api import KaggleError
from rmvideo.services import kaggle_bundle
from rmvideo.media import proc, voiceplan

BASE = os.path.dirname(os.path.abspath(__file__))
SLUG = "rmvideo-worker"
SLUG_ANALYZE = "rmvideo-analyze"   # notebook riêng: phiên dựng gắn đầu ra của nó (tiếng nền đã tách) làm dữ liệu vào
SLUG_PHOWHISPER = "rmvideo-phowhisper"   # notebook giữ model PhoWhisper-large đã chuyển CTranslate2 (chạy một lần)
SLUG_KIDS = "rmvideo-kids"               # 🧸 truyện bé: notebook riêng (không ghi đè phiên remake)
SLUG_KIDS_MODELS = "rmvideo-kids-models"  # chạy một lần (kids_setup): llama.cpp đã biên dịch + FLUX.1-schnell 4-bit
# mã gửi kèm notebook: tính tự động từ import thật của kaggle_job.py (rmvideo/services/kaggle_bundle.py)
BUNDLE = list(kaggle_bundle.files())
PIP = ("pip install -q --no-deps vieneu sea-g2p && pip install -q onnxruntime kaldi-native-fbank soxr "
       "'transformers==4.57.6' safetensors yt-dlp edge-tts")
PIP_LIGHT = "pip install -q yt-dlp edge-tts"         # không có giọng VieNeu: khỏi cài model giọng
# phiên phân tích: Whisper (faster-whisper) + tách giọng trên GPU (onnxruntime-gpu thay bản CPU mà faster-whisper kéo về)
PIP_ANALYZE = ("pip install -q faster-whisper yt-dlp edge-tts && (pip uninstall -y -q onnxruntime; "
               "pip install -q onnxruntime-gpu)")
# truyện bé: giọng VieNeu + diffusers (FLUX 4-bit) + huggingface_hub (tải GGUF); llama-server là file đã biên dịch gắn vào
PIP_KIDS = PIP + " && pip install -q 'diffusers>=0.36' accelerate bitsandbytes sentencepiece protobuf 'huggingface_hub<1.0'"  # hub<1.0: transformers 4.57.6 đòi (đo 18/11: hub 2.4 làm diffusers không import được)
KIDS_TIMEOUT = 125 * 60          # truyện bé: chờ máy (đo 18/11: 16-60 phút, giờ cao điểm) + cài + tải GGUF 20GB + 3 model + viết + vẽ + dựng
KIDS_RUN_RESERVE = 50 * 60       # giờ chạy cần cho một truyện (đo 18/11: ~20 phút, nạp FLUX từ ổ gắn 7-8 phút): chưa được cấp máy khi chỉ còn ngần này thì bỏ
GPU_MIN_LEFT = 20 * 60           # còn dưới 20 phút GPU trong tuần thì chạy CPU
TIMEOUT = 50 * 60                # cả hàng chờ máy + chạy (một việc)
TIMEOUT_PER_EXTRA = 12 * 60      # mỗi việc thêm trong lô
RUN_RESERVE = 15 * 60            # giờ chạy cần cho một việc: phiên chưa được cấp máy khi chỉ còn ngần này thì bỏ
VOICE_REF = os.path.join(BASE, "assets", "voice_ref.mp3")
LINK_TTL = 4 * 3600
_LOCK = asyncio.Lock()
_ABORT = {"run": None}     # phiên đang chờ: {"flag", "slug"} riêng từng lượt (lượt trước kết thúc không xoá cờ lượt sau)


def abort():
    """Huỷ job / quá hạn / bot tắt khi đang chờ Kaggle: dừng vòng chờ ngay. Phiên Kaggle chưa được cấp máy sẽ tự thoát
    khi được cấp (hạn `expires_at` trong việc gửi đi)."""
    run = _ABORT["run"]
    if not run:
        return
    run["flag"] = True
    _cancel_session(run["slug"])


def _cancel_session(slug: str):
    """API công khai của Kaggle chỉ huỷ được phiên theo MÃ PHIÊN mà không có lệnh nào trả mã đó (bản cũ gửi tên
    notebook nên luôn bị 403 và báo nhầm «token sai», bản 43). Thay vào đó mỗi việc gửi đi mang hạn bắt đầu
    (`expires_at`): bot đã bỏ chờ thì phiên vừa được cấp máy thoát ngay, không tốn giờ GPU, không giữ chỗ."""
    logging.info("Bỏ chờ phiên Kaggle %s: phiên chưa chạy sẽ tự thoát khi được cấp máy", slug)


def with_expiry(spec: dict, start_within: float) -> dict:
    """Gắn hạn bắt đầu cho việc gửi Kaggle: phiên được cấp máy sau hạn này thì thoát ngay (xem kernel_text). Hạn đặt
    SỚM hơn lúc bot thôi chờ (start_within giây tính từ sau khi gửi) EXPIRY_MARGIN giây: phiên được cấp máy sát giờ bot
    bỏ chờ cũng thoát, không chạy song song với bản làm trên Botkeep. Phiên thoát lúc bot còn chờ thì bot thấy xong mà
    không có kết quả -> cũng làm trên Botkeep."""
    return {**spec, "expires_at": round(time.time() + start_within - EXPIRY_MARGIN)}


EXPIRY_MARGIN = 30         # giây: lệch đồng hồ Botkeep / Kaggle (NTP, thường < 1 giây)


def _watch(slug: str) -> dict:
    run = {"flag": False, "slug": slug}
    _ABORT["run"] = run
    return run


def _unwatch(run: dict):
    if _ABORT["run"] is run:
        _ABORT["run"] = None


def _wait_session(slug: str, timeout: float, on_status, every: float = 20, queue_max: float | None = None):
    """Chờ phiên Kaggle xong. Chờ hỏng (quá giờ, chờ máy quá queue_max, mất mạng, bị huỷ, notebook lỗi) thì bỏ phiên:
    phiên chưa chạy tự thoát nhờ hạn `expires_at` (trước đây phiên quá giờ vẫn nằm trong hàng chờ, lát sau tự chạy,
    tốn giờ GPU và chặn lượt kế)."""
    run = _watch(slug)
    try:
        kaggle_api.wait(slug, timeout, every=every, on_status=on_status, stop=lambda: run["flag"], queue_max=queue_max)
    except KaggleError:
        _cancel_session(slug)
        raise
    finally:
        _unwatch(run)

# danh mục giọng VieNeu + tốc độ đọc: rmvideo/domain/voices.py (giữ tên cũ ở đây cho mã/test đang dùng)
from rmvideo.domain.voices import (CLONE, RATE, RATE_CAPCUT, RATE_EDGE, SOUTH_FEMALE, SOUTH_MALE,  # noqa: E402,F401
                                   is_male, vieneu_rate, voice_name)


def voice_engine(voice: str) -> str:
    if voice_name(voice):
        return "vieneu"
    if config.TTS_PROVIDER == "capcut" and voice not in (config.VOICE_FEMALE, config.VOICE_MALE):
        return "capcut"
    return "edge"


def words_per_sec(voice: str) -> float:
    name = voice_name(voice)
    if name:
        return vieneu_rate(name)
    return RATE_CAPCUT if voice_engine(voice) == "capcut" else RATE_EDGE


# ---------------- link tạm cho file trên Botkeep ----------------

_LINKS: dict = {}                # mã -> (đường dẫn, hết hạn)
_NOTED = {"public_url": False}   # cảnh báo một lần mỗi lần chạy bot


def serve(path: str) -> str | None:
    """Link tải tạm (mã ngẫu nhiên 32 byte, hết hạn sau LINK_TTL) để Kaggle lấy file đang nằm trên Botkeep.
    Không có PUBLIC_URL thì None."""
    base = (config.PUBLIC_URL or "").strip().rstrip("/")
    if not base or not path or not os.path.isfile(path):
        return None
    now = time.time()
    for k in [k for k, (_, exp) in _LINKS.items() if exp < now]:
        _LINKS.pop(k, None)
    tok = secrets.token_urlsafe(32)
    _LINKS[tok] = (os.path.abspath(path), now + LINK_TTL)
    return f"{base}/kin/{tok}/{urllib.parse.quote(os.path.basename(path))}"


def served(token: str) -> str | None:
    hit = _LINKS.get(token)
    if not hit or hit[1] < time.time() or not os.path.isfile(hit[0]):
        return None
    return hit[0]


def release(spec: dict):
    """Huỷ các link tạm của một việc (Kaggle đã chạy xong)."""
    for tok in list(_LINKS):
        if tok in json.dumps(spec):
            _LINKS.pop(tok, None)


def ref(path: str | None = None, url: str = "", kind: str = "video", max_duration: int = 600) -> dict:
    """Đầu vào cho Kaggle: link nền tảng (Kaggle tự tải, không cần PUBLIC_URL) và/hoặc link tạm tới bản trên Botkeep.
    Ném KaggleError nếu Kaggle không có cách nào lấy được file."""
    r: dict = {}
    if url:
        r.update(url=url, kind=kind, max_duration=max_duration)
    link = serve(path) if path else None
    if link:
        r.update(http=link, name=os.path.basename(path))
    elif url and path and os.path.isfile(path) and not _NOTED["public_url"]:
        _NOTED["public_url"] = True
        logging.warning("Chưa đặt PUBLIC_URL: Kaggle phải tự tải video từ link gốc (chậm hơn, nền tảng có thể chặn máy "
                        "Kaggle). Điền link web của bot ở Cài đặt > Tài khoản & API.")
    if not r:
        raise KaggleError("file tải lên từ máy cần PUBLIC_URL (link web panel) để Kaggle tải về")
    return r


def can_ref(path: str | None, url: str = "") -> bool:
    return bool(url) or bool(path and (config.PUBLIC_URL or "").strip())


def enabled() -> str:
    """LÝ DO Kaggle chưa dùng được ("" = dùng được). Hỏi có/không thì gọi is_enabled() cho rõ nghĩa."""
    if config.KAGGLE_USE == "0":
        return "KAGGLE_USE=0"
    if not kaggle_api.configured():
        return "chưa điền KAGGLE_API_TOKEN trong Cài đặt"
    return ""


def is_enabled() -> bool:
    """Kaggle đã bật và đã có token (enabled() trả lý do khi không)."""
    return not enabled()


def usable(job, local_path: str | None = None) -> str:
    """'' nếu job remake này chạy được trên Kaggle; không thì lý do (để ghi chú cho người dùng)."""
    why = enabled()
    if why:
        return why
    if not job.remote_src and not can_ref(local_path or job.local, job.url):
        return "video tải lên từ máy: đặt PUBLIC_URL (link web panel) để Kaggle tải được"
    if voice_name(job.voice) == CLONE and not os.path.exists(VOICE_REF):
        return "chưa tải lên đoạn giọng mẫu"
    return ""


# ---------------- notebook ----------------

def bundle_b64() -> str:
    return kaggle_bundle.build_b64()


def _jobs(spec: dict) -> list:
    return spec.get("jobs") if spec.get("kind") == "batch" else [spec]


def needs_vieneu(spec: dict) -> bool:
    return any((j.get("voice") or {}).get("engine") == "vieneu" for j in _jobs(spec)
               if (j.get("kind") or "remake") == "remake")


def is_kids(spec: dict) -> bool:
    return any(j.get("kind") == "kids" for j in _jobs(spec))


def needs_gpu(spec: dict) -> bool:
    return needs_vieneu(spec) or is_kids(spec) or any(j.get("kind") == "analyze" for j in _jobs(spec))


def pip_for(spec: dict) -> str:
    if is_kids(spec):
        return PIP_KIDS
    if any(j.get("kind") == "analyze" for j in _jobs(spec)):
        return PIP_ANALYZE
    return PIP if needs_vieneu(spec) else PIP_LIGHT


def kernel_text(spec: dict) -> str:
    spec_b64 = base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode()
    pip = pip_for(spec)
    text = f'''# rm-video: job tự động (bot tạo, đừng sửa tay). Không chứa key API.
import base64, io, json, subprocess, sys, time, zipfile
if time.time() > {float(spec.get("expires_at") or 0)!r} > 0:
    print("rm-video: bot đã thôi chờ việc này (chờ máy quá lâu / đã huỷ), thoát ngay để khỏi tốn giờ GPU")
    sys.exit(0)
APP = "/tmp/rm_app"
zipfile.ZipFile(io.BytesIO(base64.b64decode("{bundle_b64()}"))).extractall(APP)
r = subprocess.run({pip!r}, shell=True, capture_output=True, text=True)
print(r.stdout[-1500:], r.stderr[-1500:])
sys.path.insert(0, APP)
import kaggle_job
kaggle_job.main(json.loads(base64.b64decode("{spec_b64}").decode()))
'''
    if len(text.encode()) >= 990_000:
        raise KaggleError(f"việc quá lớn cho một notebook Kaggle ({len(text) // 1024}KB, tối đa 1MB)")
    return text


def settings_snapshot() -> dict:
    """Cài đặt gửi kèm notebook: không bí mật, không tài khoản (rmvideo/app/settings.py quyết khoá nào được đi)."""
    from rmvideo.app import settings as schema
    return schema.snapshot({k: getattr(config, k) for k in config.EDITABLE})


def _common(spec: dict) -> dict:
    from rmvideo.media import ff
    logo = ff.logo_path()                       # logo tải lên, không có thì logo kênh mặc định (bản 43)
    if config.BRAND_LOGO != "0" and logo:
        if os.path.getsize(logo) <= config.LOGO_MAX_BYTES:
            spec["logo_b64"] = base64.b64encode(open(logo, "rb").read()).decode()
        else:                                   # trước đây: notebook quá 1MB -> MỌI việc Kaggle lỗi, âm thầm đổ về Botkeep
            logging.warning("Logo %dKB quá cỡ cho notebook Kaggle (tối đa %dKB): việc này dựng không logo, hãy tải logo nhỏ hơn",
                            os.path.getsize(logo) >> 10, config.LOGO_MAX_BYTES >> 10)
    sting = ff.sting_path()
    if config.BRAND_STING != "0" and sting and os.path.getsize(sting) <= 100 * 1024:
        spec["sting_b64"] = base64.b64encode(open(sting, "rb").read()).decode()
    spec["settings"] = settings_snapshot()
    from rmvideo.media import filtergraph
    if filtergraph.AUTO_LOOK.get():                    # BRAND_LOOK=auto: tông Botkeep đã chọn cho job này (bản 1.0.0)
        spec.setdefault("look", filtergraph.AUTO_LOOK.get())
    spec.setdefault("token", secrets.token_hex(8))     # mã riêng từng việc: không nhận nhầm kết quả phiên trước
    return spec


def build_spec(job, segments: list, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str,
               bands, pos_y, sep: dict, speech: list, max_duration: int, local_path: str | None = None,
               src: dict | None = None, amb_token: str = "", teaser: dict | None = None,
               edit: dict | None = None, overlays: list | None = None, tail: float = 0.0,
               voice_bed: dict | None = None) -> dict:
    """Việc remake. src: đầu vào đã dựng sẵn (vd. một phần cắt từ video dài); không có thì từ job.url / file trên Botkeep.
    voice_bed: nhạc lót dưới lời đã chọn trên Botkeep {path, trim, name, url} (Kaggle tải theo link / PUBLIC_URL)."""
    engine = voice_engine(job.voice)
    spec = {"kind": "remake", "job": job.id, "mode": job.mode,
            "src": src or ref(local_path or job.local or None, job.url, "video", max_duration),
            # orig chỉ gửi cho khúc điểm danh / tóm tắt: xếp giọng không cho câu trước lấn vào khúc đó (bản 49)
            "segments": [{**{k: s.get(k) for k in ("i", "start", "slot", "span", "text", "ost")},
                          **({"orig": s["orig"]} if voiceplan.side_orig(s.get("orig")) else {})} for s in segments],
            "out_dur": out_dur, "render_dur": render_dur, "stretch": stretch, "allow_hd": allow_hd, "title": title,
            "bands": bands or [], "pos_y": pos_y, "subs": bool(job.subs),
            "sep": {"ok": bool(sep.get("ok_remote")), "windows": sep.get("windows")},
            "speech": [list(x) for x in speech or []],
            "voice": {"engine": engine, "name": voice_name(job.voice), "id": job.voice}}
    if amb_token:
        spec["amb_token"] = amb_token
    if teaser:                                   # đoạn mở đầu: cảnh món đã xong chiếu trước (mốc đã lùi theo)
        spec["teaser"] = {k: teaser[k] for k in ("a", "dur", "still")}
    if edit:                                     # kế hoạch dựng (REMAKE_STYLE): Kaggle dựng y hệt Botkeep
        spec["edit"] = edit
    if overlays:                                 # thẻ chữ ngoài kế hoạch dựng (mẹo trên video, TIPS=video)
        spec["overlays"] = overlays
    if tail:                                     # màn kết nối thêm sau video (giây, đã tính trong out_dur)
        spec["tail"] = tail
    if voice_bed:                                # nhạc lót dưới lời (VOICE_BED): Kaggle không lấy được thì dựng không nhạc lót
        try:
            spec["voice_bed"] = {"music": ref(voice_bed.get("path"), voice_bed.get("url", ""), "audio"),
                                 "trim": voice_bed.get("trim", 0.0), "name": voice_bed.get("name", "")}
        except KaggleError as e:
            logging.info("Nhạc lót không gửi kèm Kaggle được (%s): dựng không nhạc lót", e)
    if voice_name(job.voice) == CLONE:
        spec["ref_b64"] = base64.b64encode(open(VOICE_REF, "rb").read()).decode()
        spec["ref_ext"] = ".mp3"
    return _common(spec)


def bed_spec(job, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str, bands,
             max_duration: int, music: str | None, music_url: str, trim: float, orig: str,
             local_path: str | None = None, src: dict | None = None) -> dict:
    """Video KHÔNG LỜI (NO_SPEECH=music): dựng như remake (tiêu đề, logo, che chữ, màu) nhưng tiếng là nhạc nền (+ tiếng
    gốc theo BGM_ORIG) thay cho giọng đọc. Không đọc giọng, không tách giọng: chạy CPU, không tốn giờ GPU.
    music: file nhạc trên Botkeep (Kaggle lấy qua PUBLIC_URL); music_url: link gốc (Kaggle tự tải). Không có cả hai:
    chỉ tiếng gốc."""
    return _common({"kind": "bed", "job": job.id, "mode": job.mode,
                    "src": src or ref(local_path or job.local or None, job.url, "video", max_duration),
                    "music": ref(music, music_url, "audio") if (music or music_url) else None,
                    "trim": round(float(trim), 3), "orig": orig, "out_dur": out_dur, "render_dur": render_dur,
                    "stretch": stretch, "allow_hd": allow_hd, "title": title, "bands": bands or []})


def analyze_spec(job, local_path: str | None, title: str, desc: str, want_gray: bool, max_duration: int) -> dict:
    """Việc bóc lời + phân tích hình (chạy TRƯỚC khi AI viết lời). Khoá ngôn ngữ tiếng Việt khi tiêu đề/mô tả có dấu
    tiếng Việt (Whisper đoán ngôn ngữ trên quãng nhạc dạo hay sai); không thì để Whisper tự đoán (nguồn nước ngoài)."""
    import vocab
    vi = bool(re.search(r"[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", f"{title} {desc}".lower()))
    return _common({"kind": "analyze", "job": job.id, "src": ref(local_path or job.local or None, job.url, "video",
                                                                  max_duration),
                    "want_gray": bool(want_gray), "language": "vi" if vi else "", "token": secrets.token_hex(8),
                    "prompt": vocab.groq_prompt(title, desc), "keep_ambient": job.mode == "cook",
                    "inpaint": config.TEXT_INPAINT == "1",             # LaMa xoá chữ in cứng (GPU) thay cho che mờ
                    "asr": config.KAGGLE_ASR if vi else "large-v3"})   # PhoWhisper chỉ tiếng Việt


def music_spec(job, inp: str, music: str, music_args: dict) -> dict:
    return _common({"kind": "music", "job": job.id, "duration": job.duration, "music_args": music_args,
                    "src": ref(inp, job.url, "video", 600),
                    "music": ref(music, job.music_url if not job.music_local and not job.music_tg_file_id else "",
                                 "audio")})


def photo_spec(job, images: list, music: str, seed: int, recent: list) -> dict:
    return _common({"kind": "photo", "job": job.id, "duration": job.duration, "seed": seed, "recent": recent,
                    "images": [ref(p) for p in images],
                    "music": ref(music, job.music_url if not job.music_local and not job.music_tg_file_id else "",
                                 "audio")})


# ---------------- 🧸 truyện bé ----------------

def kids_sources() -> tuple[list, list]:
    """(notebook, dataset) gắn vào phiên truyện bé: KIDS_KAGGLE_SOURCES («notebook:user/slug», «dataset:user/slug»,
    hoặc chỉ «slug» = notebook của mình); trống = notebook rmvideo-kids-models do lệnh kids_setup tạo."""
    nbs, dss = [], []
    for item in re.split(r"[,\s]+", config.KIDS_KAGGLE_SOURCES or ""):
        if not item:
            continue
        kind, _, slug = item.partition(":")
        if not slug:
            kind, slug = "notebook", kind
        (dss if kind.lower() == "dataset" else nbs).append(slug.strip("/ "))
    return (nbs or [SLUG_KIDS_MODELS]), dss


def kids_spec(job, brief: dict, music: dict | None = None, story: dict | None = None,
              attempts: list | None = None) -> dict:
    """Việc truyện bé: đề đã chọn + dữ liệu (nhân vật, bài học, kho từ) + giọng + model + nhận diện kênh. Không gửi logo /
    âm hiệu kênh bếp. Botkeep tải về thêm cover.jpg / thumb.jpg / story.json (fetch_files). story: truyện đã viết sẵn trên
    Botkeep (Gemini) -> Kaggle bỏ hẳn bước model chữ (không tải GGUF 20GB)."""
    from rmvideo.kids import story as story_mod
    data = json.loads(json.dumps(story_mod.load()))
    if (config.KIDS_WORLD_NAME or "").strip():
        data["world"]["name"] = config.KIDS_WORLD_NAME.strip()
    nbs, dss = kids_sources()
    if music and music.get("dataset") and music["dataset"] not in dss:    # kho nhạc Lyria (rmvideo/kids/bgm.py)
        dss = dss + [music["dataset"]]
    spec = {"kind": "kids", "slug": SLUG_KIDS, "title": "rmvideo kids", "job": job.id, "brief": brief, "data": data,
            "voice": {"engine": "vieneu", "name": config.KIDS_VOICE, "id": "vieneu:" + config.KIDS_VOICE,
                      "multi": config.KIDS_MULTI_VOICE != "0"},
            "image": {"model": config.KIDS_IMAGE_MODEL},
            "llm": {"pattern": config.KIDS_LLM_PATTERN, "hf_repo": config.KIDS_LLM_HF,
                    "think": int(config.KIDS_LLM_THINK or 0)},
            "channel": {"name": config.KIDS_CHANNEL_NAME, "world": data["world"]["name"], "tagline": config.KIDS_TAGLINE,
                        "hashtags": config.KIDS_HASHTAGS},
            "music": music, "fx": config.KIDS_FX, "kernel_sources": nbs, "dataset_sources": dss,
            "fetch_files": ["cover.jpg", "thumb.jpg", "story.json"]}
    if story:
        spec["story"] = story
        spec["attempts"] = list(attempts or [])
    spec = _common(spec)
    spec.pop("logo_b64", None)
    spec.pop("sting_b64", None)
    return spec


KIDS_MODELS_KERNEL = r'''# rm-video: chuẩn bị model cho truyện bé (bot tạo, chạy MỘT lần, ~45-60 phút trên GPU). Không chứa key.
import os, shutil, subprocess, sys, time, glob
W = "/kaggle/working"
def run(cmd, env=None, check=True):
    print(">>", cmd, flush=True); t0 = time.time()
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
    print(r.stdout[-3000:], r.stderr[-3000:], f"({time.time() - t0:.0f}s)", flush=True)
    if check and r.returncode: raise RuntimeError(f"lệnh lỗi: {cmd}")
    return r
def used_gb(path=W):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs) / 2**30
done = {}
run("nvidia-smi -L; df -h / /kaggle/working /tmp | tail -3; python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'; "
    "ls /usr/local/cuda/lib64/stubs /usr/local/cuda/targets/x86_64-linux/lib/stubs 2>/dev/null | head; find / -name 'libcuda.so*' 2>/dev/null | head",
    check=False)
# 1. llama.cpp (CUDA, kiến trúc T4 = 75) -> /kaggle/working/llama/llama-server (+ thư viện .so cạnh nó)
# GGML_CUDA_NO_VMM=ON: không cần libcuda.so lúc link (Kaggle không có symlink libcuda.so -> cmake báo thiếu CUDA::cuda_driver,
# lỗi thật 18/11/2026); lúc chạy vẫn dùng driver libcuda.so.1 của phiên GPU.
try:
    run("git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp")
    run("cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DGGML_CUDA=ON -DGGML_CUDA_NO_VMM=ON -DCMAKE_CUDA_ARCHITECTURES=75 "
        "-DLLAMA_CURL=OFF -DGGML_NATIVE=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_BUILD_TYPE=Release "
        "-DCUDAToolkit_ROOT=/usr/local/cuda")
    run("cmake --build /tmp/llama.cpp/build --config Release -j4 --target llama-server")
    os.makedirs(f"{W}/llama", exist_ok=True)
    for p in glob.glob("/tmp/llama.cpp/build/bin/*"):
        shutil.copy(p, f"{W}/llama/")
    done["llama"] = os.listdir(f"{W}/llama")
    print("llama-server:", done["llama"], flush=True)
except Exception as e:
    print("!! llama.cpp lỗi:", e, flush=True)
# 2. model ảnh 4-bit (bitsandbytes NF4), lưu để phiên dựng nạp ~1 phút thay vì tải 20-30GB mỗi lần
run("pip install -q 'diffusers>=0.36' accelerate bitsandbytes sentencepiece protobuf 'transformers==4.57.6'", check=False)
import torch
from diffusers import BitsAndBytesConfig as DiffBnb
from transformers import BitsAndBytesConfig as TfBnb
q4 = DiffBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
# 2a. FLUX.1-schnell: repo gốc black-forest-labs đã khoá (gated) -> dùng bản sao công khai nguyên vẹn (unsloth, ...)
for repo in ("unsloth/FLUX.1-schnell", "Niansuh/FLUX.1-schnell", "cocktailpeanut/xulf-s", "ParkSol/flux-schnell"):
    try:
        from diffusers import FluxPipeline, FluxTransformer2DModel
        from transformers import T5EncoderModel
        print("FLUX.1-schnell từ", repo, flush=True)
        tr = FluxTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4, torch_dtype=torch.float16)
        t5 = T5EncoderModel.from_pretrained(repo, subfolder="text_encoder_2", quantization_config=TfBnb(load_in_8bit=True), torch_dtype=torch.float16)
        pipe = FluxPipeline.from_pretrained(repo, transformer=tr, text_encoder_2=t5, torch_dtype=torch.float16)
        pipe.save_pretrained(f"{W}/flux-schnell-nf4")
        del pipe, tr, t5
        done["flux"] = os.listdir(f"{W}/flux-schnell-nf4")
        print("đã lưu flux-schnell-nf4 (%.1f GB):" % used_gb(f"{W}/flux-schnell-nf4"), done["flux"], flush=True)
        break
    except Exception as e:
        print("!! FLUX", repo, "lỗi:", str(e)[:400], flush=True)
        shutil.rmtree(f"{W}/flux-schnell-nf4", ignore_errors=True)
    finally:
        shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
        torch.cuda.empty_cache()
# 2b. Z-Image-Turbo (Apache-2.0, không khoá): transformer NF4 + text encoder Qwen3 4-bit; chỉ khi còn chỗ (đầu ra tối đa 20GB)
try:
    if used_gb() > 13.0:
        raise RuntimeError("đầu ra đã %.1f GB, bỏ Z-Image để không vượt 20GB" % used_gb())
    from diffusers import ZImagePipeline, ZImageTransformer2DModel
    from transformers import AutoModel
    repo = "Tongyi-MAI/Z-Image-Turbo"
    tr = ZImageTransformer2DModel.from_pretrained(repo, subfolder="transformer", quantization_config=q4, torch_dtype=torch.float16)
    te = AutoModel.from_pretrained(repo, subfolder="text_encoder", torch_dtype=torch.float16,
                                   quantization_config=TfBnb(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16))
    pipe = ZImagePipeline.from_pretrained(repo, transformer=tr, text_encoder=te, torch_dtype=torch.float16)
    pipe.save_pretrained(f"{W}/zimage-turbo-nf4")
    done["zimage"] = os.listdir(f"{W}/zimage-turbo-nf4")
    print("đã lưu zimage-turbo-nf4 (%.1f GB):" % used_gb(f"{W}/zimage-turbo-nf4"), done["zimage"], flush=True)
except Exception as e:
    print("!! Z-Image lỗi:", str(e)[:400], flush=True)
    shutil.rmtree(f"{W}/zimage-turbo-nf4", ignore_errors=True)
finally:
    shutil.rmtree(os.path.expanduser("~/.cache/huggingface"), ignore_errors=True)
# (model chữ GGUF ~20GB vượt giới hạn đầu ra notebook: phiên dựng tải từ Hugging Face, ~3 phút)
print("KẾT QUẢ:", {k: len(v) for k, v in done.items()}, "| đầu ra %.1f GB" % used_gb(), flush=True)
if "llama" not in done or not ({"flux", "zimage"} & set(done)): raise SystemExit("thiếu: " + ", ".join(k for k in ("llama", "flux/zimage") if k not in done and not ({"flux", "zimage"} & set(done))))
'''


def kids_setup() -> str:
    """Đẩy notebook chuẩn bị model (chạy một lần). Trả câu báo; lỗi thì ném KaggleError."""
    if not kaggle_api.configured():
        raise KaggleError("chưa điền KAGGLE_API_TOKEN trong Cài đặt")
    kaggle_api.push(SLUG_KIDS_MODELS, "rmvideo kids models", KIDS_MODELS_KERNEL, gpu=True)
    return (f"Đã gửi notebook {SLUG_KIDS_MODELS} (biên dịch llama.cpp + lượng tử FLUX.1-schnell, ~45-60 phút GPU). "
            "Xem tiến độ bằng lệnh kids_setup_status hoặc trên kaggle.com; xong thì làm truyện được.")


def kids_setup_status() -> str:
    st, why = kaggle_api.status(SLUG_KIDS_MODELS)
    return f"notebook {SLUG_KIDS_MODELS}: {st}" + (f" — {why[:200]}" if why else "")


# ---------------- chạy ----------------

def _run_sync(spec: dict, tmp: str, on_step) -> list:
    """Chạy một notebook cho spec (một việc hoặc một lô). Trả danh sách kết quả theo thứ tự việc."""
    jobs = spec["jobs"] if spec.get("kind") == "batch" else [spec]
    gpu = False
    if needs_gpu(spec):
        try:
            gpu = kaggle_api.quota()["gpu_left"] >= GPU_MIN_LEFT
        except KaggleError:
            raise
        except Exception:
            gpu = True
    n = len(jobs)
    kids = is_kids(spec)
    if kids and not gpu:                                 # model chữ + model ảnh trên CPU: vô vọng, không có đường lùi
        raise KaggleError("hết giờ GPU tuần này: truyện bé chờ tới khi Kaggle làm mới giờ GPU")
    on_step(f"Kaggle: gửi {'lô ' + str(n) + ' việc' if n > 1 else 'job'} ({'GPU' if gpu else 'CPU'})")
    reuse = any(j.get("amb_token") for j in jobs)        # tiếng nền đã tách ở phiên phân tích: gắn vào, khỏi tách lại
    total = (KIDS_TIMEOUT if kids else TIMEOUT) + TIMEOUT_PER_EXTRA * (n - 1)
    queue_max = total - (KIDS_RUN_RESERVE if kids else RUN_RESERVE) - TIMEOUT_PER_EXTRA * (n - 1)
    slug = spec.get("slug") or SLUG
    sources = list(spec.get("kernel_sources") or []) + ([SLUG_ANALYZE] if reuse else [])
    kaggle_api.push(slug, spec.get("title") or "rmvideo worker", kernel_text(with_expiry(spec, queue_max)), gpu=gpu,
                    kernel_sources=sources or None, dataset_sources=list(spec.get("dataset_sources") or []) or None)

    def st(s, secs):
        on_step(f"Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} ({secs / 60:.0f} phút)")
    _wait_session(slug, total, st, queue_max=queue_max)
    d = kaggle_api.outputs(slug)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    out = []
    for k in range(n):
        tag = f"_{k}" if spec.get("kind") == "batch" else ""
        try:
            if f"result{tag}.json" not in files:
                raise KaggleError("Kaggle không trả kết quả. Log: " + kaggle_api.log_tail(d, 600))
            rj = os.path.join(tmp, f"kaggle_result{tag}.json")
            kaggle_api.download(files[f"result{tag}.json"], rj, 5 << 20)
            res = json.load(open(rj, encoding="utf-8"))
            if not res.get("ok"):
                raise KaggleError("Việc trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
            if "token" in res and jobs[k].get("token") and res["token"] != jobs[k]["token"]:
                raise KaggleError("kết quả trên Kaggle không phải của việc này (notebook bị việc khác ghi đè)")
            if f"output{tag}.mp4" not in files:
                raise KaggleError("Kaggle không trả video")
            on_step(f"Kaggle: tải video về{f' ({k + 1}/{n})' if n > 1 else ''}")
            vid = os.path.join(tmp, f"output{tag}.mp4")
            kaggle_api.download(files[f"output{tag}.mp4"], vid)
            p = proc.run_sync(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", vid],
                              capture_output=True, text=True, timeout=60)
            try:
                dur = float(p.stdout.strip())
            except ValueError:
                dur = 0.0
            if dur < 1:
                raise KaggleError("video Kaggle trả về hỏng")
            res["video"], res["gpu"] = vid, gpu
            got: dict = {}
            for name in jobs[k].get("fetch_files") or []:         # file phụ (ảnh bìa, story.json của truyện bé)
                base, ext = os.path.splitext(name)
                remote = f"{base}{tag}{ext}"
                if remote in files:
                    try:
                        p = os.path.join(tmp, f"kaggle_{remote}")
                        kaggle_api.download(files[remote], p, 50 << 20)
                        got[name] = p
                    except Exception as e:
                        logging.warning("Không tải được %s từ Kaggle: %s", remote, e)
            if got:
                res["files"] = got
            out.append(res)
        except Exception as e:
            out.append(e if isinstance(e, KaggleError) else KaggleError(str(e)))
    return out


TIMEOUT_ANALYZE = 30 * 60        # phân tích: hàng chờ máy + cài thư viện + tải model Whisper + chạy
# chờ máy tối đa cho phân tích: Botkeep bóc lời được trong ~15 giây (kém hơn: chưa tách giọng), chờ Kaggle 30 phút
# rồi mới làm trên Botkeep là phí (log 16/11: 30 phút QUEUED, sau đó phiên dựng lại được cấp máy ngay)
QUEUE_ANALYZE = 8 * 60


def _run_analysis_sync(spec: dict, tmp: str, on_step) -> dict:
    """Phiên phân tích: trả kết quả (đã đọc result.json) + đường dẫn gói ảnh / tiếng nền đã tách nếu có."""
    gpu = True
    try:
        gpu = kaggle_api.quota()["gpu_left"] >= GPU_MIN_LEFT
    except KaggleError:
        raise
    except Exception:
        pass
    if not gpu:
        raise KaggleError("hết giờ GPU tuần này (Whisper trên CPU quá chậm): bóc lời trên Botkeep")
    on_step("Bóc lời + phân tích hình trên Kaggle: gửi việc (GPU)")
    src = [SLUG_PHOWHISPER] if spec.get("asr") == "phowhisper" else None
    try:
        kaggle_api.push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(with_expiry(spec, QUEUE_ANALYZE)), gpu=True,
                        kernel_sources=src)
    except KaggleError as e:
        if not src:
            raise
        on_step("Kaggle: chưa có notebook PhoWhisper, dùng large-v3")   # tài khoản chưa chạy rmvideo-phowhisper
        logging.warning("Không gắn được notebook %s (%s): bóc lời bằng large-v3", SLUG_PHOWHISPER, str(e)[:120])
        spec = {**spec, "asr": "large-v3"}
        kaggle_api.push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(with_expiry(spec, QUEUE_ANALYZE)), gpu=True)

    def st(s, secs):
        on_step(f"Bóc lời + phân tích hình trên Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} "
                f"({secs / 60:.0f} phút)")
    _wait_session(SLUG_ANALYZE, TIMEOUT_ANALYZE, st, every=15, queue_max=QUEUE_ANALYZE)
    d = kaggle_api.outputs(SLUG_ANALYZE)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    if "result.json" not in files:
        raise KaggleError("Kaggle không trả kết quả phân tích. Log: " + kaggle_api.log_tail(d, 600))
    rj = os.path.join(tmp, "kaggle_analysis.json")
    kaggle_api.download(files["result.json"], rj, 20 << 20)
    res = json.load(open(rj, encoding="utf-8"))
    if not res.get("ok"):
        raise KaggleError("Phân tích trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
    if res.get("token") != spec.get("token"):
        raise KaggleError("kết quả phân tích không phải của job này (notebook bị việc khác ghi đè)")
    on_step("Bóc lời + phân tích hình trên Kaggle: tải kết quả về")
    for name, key in (("thumbs.zip", "thumbs_zip"), ("ambient.flac", "ambient"), ("clean.mp4", "clean")):
        if name in files:
            p = os.path.join(tmp, f"kaggle_{name}")
            try:
                kaggle_api.download(files[name], p, 400 << 20)
                res[key] = p
            except Exception as e:                  # thiếu ảnh / tiếng nền: vẫn dùng được lời đã bóc
                logging.warning("Không tải được %s từ Kaggle: %s", name, e)
    return res


async def analyze(spec: dict, tmp: str, on_step=None) -> dict:
    """Bóc lời + phân tích hình trên Kaggle. Trả kết quả thô (whisper, cuts, bands, ...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = await asyncio.to_thread(_run_analysis_sync, spec, tmp, on_step)
    finally:
        release(spec)
    logging.info("Kaggle phân tích xong: %s", res.get("timings"))
    return res


async def produce(spec: dict, tmp: str, on_step=None) -> dict:
    """Một việc trên Kaggle (mỗi lúc một phiên). Trả kết quả (video, plan, srt, notes...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = (await asyncio.to_thread(_run_sync, spec, tmp, on_step))[0]
    finally:
        release(spec)
    if isinstance(res, Exception):
        raise res
    logging.info("Kaggle xong: %s", res.get("timings"))
    return res


async def produce_batch(specs: list, tmp: str, on_step=None) -> list:
    """Nhiều việc trong MỘT phiên (nạp model + tải nguồn một lần). Trả danh sách: kết quả hoặc KaggleError từng việc."""
    on_step = on_step or (lambda s: None)
    shared = ("settings", "logo_b64", "sting_b64", "ref_b64", "ref_ext")       # giống nhau ở mọi việc: gói một lần (notebook < 1MB)
    spec = {"kind": "batch", "jobs": [{k: v for k, v in s.items() if k not in shared} for s in specs],
            **{k: specs[0].get(k) for k in shared if specs[0].get(k)}}
    try:
        async with _LOCK:
            return await asyncio.to_thread(_run_sync, spec, tmp, on_step)
    except Exception as e:                       # mọi lỗi (kể cả lỗi gói notebook) -> từng phần làm trên Botkeep
        return [e if isinstance(e, KaggleError) else KaggleError(str(e)[:300])] * len(specs)
    finally:
        release(spec)


# ---------------- gom lô (video dài cắt nhiều phần) ----------------

class Deferred:
    """Phần đã chuẩn bị xong (lời đã chốt), chờ chạy chung một phiên Kaggle với các phần khác."""
    def __init__(self, spec: dict, finish, approved: list | None = None):
        self.spec, self.finish, self.approved = spec, finish, approved or []


_COLLECT = contextvars.ContextVar("kaggle_collect", default=None)


def collecting() -> bool:
    return _COLLECT.get() is not None


class collect:
    """with collect(): process() (cùng ngữ cảnh asyncio) trả Deferred thay vì chạy Kaggle ngay."""
    def __enter__(self):
        self._tok = _COLLECT.set([])
        return self

    def __exit__(self, *a):
        _COLLECT.reset(self._tok)
        return False
