"""Phía Botkeep: gửi phần nặng sang Kaggle rồi nhận video hoàn chỉnh về (xem kaggle_job.py).

Một lần chạy = một phiên notebook riêng tư `<user>/rmvideo-worker` (mỗi lần là một phiên bản mới). Notebook chứa code
cần thiết (nén + base64) và việc cần làm (JSON), KHÔNG chứa key API nào; file lớn (video tải lên, nhạc, ảnh) Kaggle tự
tải: link nền tảng gốc, hoặc link tạm có mã bí mật do web panel của Botkeep phát (cần PUBLIC_URL; notebook < 1MB nên
không nhúng file được). GPU chỉ khi có giọng VieNeu (còn giờ GPU trong tuần); việc khác chạy CPU 4 lõi, không tốn giờ GPU.
Lỗi ở bất kỳ bước nào -> ném KaggleError, pipeline làm tiếp trên Botkeep như cũ."""
import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse

from typing import Any

import config
import kaggle_api
from kaggle_api import KaggleError, Parked, SessionLimit
from rmvideo.services import kaggle_bundle
from rmvideo.media import proc, voiceplan

BASE = os.path.dirname(os.path.abspath(__file__))
SLUG = "rmvideo-worker"
SLUG_ANALYZE = "rmvideo-analyze"   # notebook riêng: phiên dựng gắn đầu ra của nó (tiếng nền đã tách) làm dữ liệu vào
SLUG_PHOWHISPER = "rmvideo-phowhisper"   # notebook giữ model PhoWhisper-large đã chuyển CTranslate2 (chạy một lần)
SLUG_KIDS = "rmvideo-kids"               # 🧸 truyện bé: notebook riêng (không ghi đè phiên remake)
SLUG_LORA_TRAIN = "rmvideo-lora-train"   # notebook train LoRA 4 nhân vật (đầu ra: output/<tên>/<tên>.safetensors)
SLUG_KIDS_LORA = "rmvideo-kids-lora"     # dataset riêng tư giữ bản LoRA đang dùng (train lại không xoá mất)
_LORA_OK: dict[str, tuple[bool, float]] = {}
SLUG_KIDS_MODELS = "rmvideo-kids-models"  # chạy một lần (kids_setup): llama.cpp đã biên dịch + FLUX.1-schnell 4-bit
# mã gửi kèm notebook: tính tự động từ import thật của kaggle_job.py (rmvideo/services/kaggle_bundle.py)
BUNDLE = list(kaggle_bundle.files())
PIP = ("pip install -q --no-deps vieneu sea-g2p && pip install -q onnxruntime kaldi-native-fbank soxr "
       "'transformers==4.57.6' safetensors yt-dlp edge-tts")
# faster-whisper: nghe lại giọng VieNeu trên GPU (rmvideo/media/voice_qa.py). Lệnh RIÊNG nối sau cùng: cài lỗi / xung đột
# phiên bản thì chỉ mất bước nghe lại (voice_qa tự bỏ qua khi thiếu gói), giọng VieNeu và diffusers không bị kéo theo
PIP_QA = "; pip install -q faster-whisper"
PIP_LIGHT = "pip install -q yt-dlp edge-tts"         # không có giọng VieNeu: khỏi cài model giọng
# phiên phân tích: Whisper (faster-whisper) + tách giọng trên GPU (onnxruntime-gpu thay bản CPU mà faster-whisper kéo về)
PIP_ANALYZE = ("pip install -q faster-whisper yt-dlp edge-tts && (pip uninstall -y -q onnxruntime; "
               "pip install -q onnxruntime-gpu)")
# tách giọng RoFormer (VOICE_SEP_MODEL=auto, vocal_sep.roformer_wanted): cài sau cùng, ghim bản (danh sách SHA-256 model
# đi kèm gói); cài lỗi thì chỉ mất RoFormer, tách bằng Kim_Vocal_2 như cũ. audioread: 0.48.0 import mà không khai báo
PIP_ROFORMER = "; pip install -q audio-separator==0.48.0 audioread"
# truyện bé: giọng VieNeu + diffusers (FLUX 4-bit) + huggingface_hub (tải GGUF); llama-server là file đã biên dịch gắn vào
PIP_KIDS = PIP + " && pip install -q 'diffusers>=0.36' accelerate bitsandbytes sentencepiece protobuf peft 'huggingface_hub<1.0'"  # hub<1.0: transformers 4.57.6 đòi (đo 18/11: hub 2.4 làm diffusers không import được)
KIDS_TIMEOUT = 125 * 60          # truyện bé: chờ máy (đo 18/11: 16-60 phút, giờ cao điểm) + cài + tải GGUF 20GB + 3 model + viết + vẽ + dựng
KIDS_RUN_RESERVE = 50 * 60       # giờ chạy cần cho một truyện (đo 18/11: ~20 phút, nạp FLUX từ ổ gắn 7-8 phút): chưa được cấp máy khi chỉ còn ngần này thì bỏ
# Giữ chỗ (1.4.6, job 3 ngày 24/11: chờ GPU 75 phút rồi bỏ -> mất truyện lẫn chỗ xếp hàng; gửi lại là xếp hàng từ đầu).
# Phiên truyện bé gửi với hạn bắt đầu KIDS_PARK_MAX: bot thôi chờ (hết queue_max, hoặc có job khác chờ mà đã chờ quá
# KIDS_PARK_MIN, hoặc bot tắt / job quá hạn) thì phiên VẪN nằm trong hàng chờ; kids_auto hỏi trạng thái mỗi 2 phút, có kết
# quả thì tạo job thu về (rmvideo/app/flow/kids.py: brief.collect).
KIDS_PARK_MAX = 4 * 3600
KIDS_PARK_MIN = 5 * 60
GPU_MIN_LEFT = 20 * 60           # còn dưới 20 phút GPU trong tuần thì chạy CPU
TIMEOUT = 50 * 60                # cả hàng chờ máy + chạy (một việc)
TIMEOUT_PER_EXTRA = 12 * 60      # mỗi việc thêm trong lô
RUN_RESERVE = 15 * 60            # giờ chạy cần cho một việc: phiên chưa được cấp máy khi chỉ còn ngần này thì bỏ
VOICE_REF = os.path.join(BASE, "assets", "voice_ref.mp3")
LINK_TTL = 4 * 3600
_LOCK = asyncio.Lock()
_ABORT = {"run": None}     # phiên đang chờ: {"flag", "slug"} riêng từng lượt (lượt trước kết thúc không xoá cờ lượt sau)


def abort(park: bool = False):
    """Huỷ job / quá hạn / bot tắt khi đang chờ Kaggle: dừng vòng chờ ngay. Phiên Kaggle chưa được cấp máy sẽ tự thoát
    khi được cấp (hạn `expires_at` trong việc gửi đi). park=True (bot tắt, job quá hạn — KHÔNG phải người dùng huỷ): phiên
    truyện bé giữ chỗ, kids_auto theo dõi và thu kết quả sau."""
    run = _ABORT["run"]
    if not run:
        return
    run["flag"] = True
    if park and run.get("park_info") and _park(run["park_info"]):
        run["parked"] = True
        return
    if run.get("park_info"):                            # người dùng huỷ: bỏ mục giữ chỗ (ghi lúc gửi), không thu kết quả nữa
        try:
            import kids_auto
            kids_auto.unpark(int(run["park_info"]["job"]))
        except Exception as e:
            logging.warning("Không bỏ được mục giữ chỗ Kaggle (%s)", str(e)[:120])
    if run.get("park_info"):
        _cancel_session(run["slug"], long_expiry=True)
    else:
        _cancel_session(run["slug"])


def _park(info: dict) -> bool:
    """Ghi phiên truyện bé đang giữ chỗ vào kids_auto (theo dõi, thu kết quả). Trả False nếu không ghi được."""
    try:
        import kids_auto
        kids_auto.park_record(info)
        logging.info("Giữ chỗ phiên Kaggle %s cho job %s: bot theo dõi, có kết quả thì thu về (tối đa %.0f giờ)",
                     info.get("slug"), info.get("job"), KIDS_PARK_MAX / 3600)
        return True
    except Exception as e:
        logging.warning("Không ghi được phiên Kaggle giữ chỗ (%s)", str(e)[:160])
        return False


def _parked_other(slug: str, job: Any) -> Any:
    """Mã job truyện bé KHÁC đang giữ chỗ trên cùng notebook (None nếu không có)."""
    try:
        import kids_auto
        return next((p.get("job") for p in kids_auto.load_state()["parked"] if p.get("slug") == slug and p.get("job") != job),
                    None)
    except Exception:
        return None


def _others_waiting() -> bool:
    """Có job khác đang chờ trong hàng đợi Botkeep (job truyện bé đang giữ worker thì nên nhường)."""
    try:
        from state import queue
        return int(getattr(queue, "pending", 0) or 0) > 1
    except Exception:
        return False


GPU_SESSIONS = 2           # Kaggle: tối đa chừng này phiên GPU «batch» xếp hàng / chạy cùng lúc (đo thật 24/11 job 4)
SLOT_WAIT = 120.0          # giây giữa hai lần gửi lại khi Kaggle kín phiên GPU
GPU_SLUGS = (SLUG, SLUG_KIDS, SLUG_ANALYZE)


def _cancel_session(slug: str, long_expiry: bool = False):
    """Bỏ phiên đã gửi: xoá notebook (kaggle_api.delete_kernel) — API công khai chỉ huỷ phiên theo MÃ PHIÊN mà không lệnh
    nào trả mã đó (bản 43 bị 403). Trước 1.4.8 chỉ dựa vào hạn bắt đầu `expires_at` (phiên được cấp máy sau hạn tự thoát),
    nhưng phiên chưa được cấp máy vẫn xếp hàng và CHIẾM một trong GPU_SESSIONS chỗ: job 4 ngày 24/11 bị từ chối «Maximum
    batch GPU session count of 2 reached» vì hai phiên bot đã bỏ còn xếp hàng. Chỉ xoá khi phiên CÒN xếp hàng / chạy (phiên
    đã xong / lỗi không chiếm chỗ; xoá là mất đầu ra — tiếng nền đã tách mà lần dựng sau dùng lại — và log để dò lỗi);
    notebook đang có phiên truyện bé giữ chỗ thì không xoá. Chạy trong luồng nền: nơi gọi (Hủy, quá hạn, tắt bot) ở vòng sự
    kiện, không được chờ mạng."""
    if _slug_parked(slug):
        logging.info("Bỏ chờ phiên Kaggle %s nhưng notebook còn phiên truyện bé giữ chỗ: không xoá", slug)
        return
    if not kaggle_api.configured():
        return

    def run() -> None:
        try:
            st, _why = kaggle_api.status(slug)
            if st.upper() not in ("QUEUED", "RUNNING") or _slug_parked(slug):
                logging.info("Bỏ chờ phiên Kaggle %s (%s): không cần xoá notebook", slug, st)
                return
            kaggle_api.delete_kernel(slug)
            logging.info("Bỏ phiên Kaggle %s (%s): đã xoá notebook, trả chỗ GPU", slug, st)
        except Exception as e:
            logging.info("Bỏ chờ phiên Kaggle %s (không xoá được notebook: %s): phiên chưa chạy sẽ tự thoát khi được cấp "
                         "máy%s", slug, str(e)[:100], " sau hạn %.0f giờ" % (KIDS_PARK_MAX / 3600) if long_expiry else "")
    threading.Thread(target=run, name=f"kaggle-cancel-{slug}", daemon=True).start()


def _slug_parked(slug: str) -> bool:
    try:
        import kids_auto
        return any(p.get("slug") == slug for p in kids_auto.load_state()["parked"])
    except Exception:
        return False


def _free_slots(current: str) -> list:
    """Kaggle kín GPU_SESSIONS phiên GPU: xoá notebook của bot còn phiên xếp hàng / chạy (phiên «ma»: bot đã bỏ chờ mà
    Kaggle chưa cấp máy; bot chỉ chạy một phiên một lúc nên phiên nào còn đó đều là ma) — trừ notebook có phiên truyện bé
    giữ chỗ. Trả danh sách slug đã xoá."""
    freed = []
    for slug in GPU_SLUGS:              # chỉ notebook công việc (không bao giờ notebook model / nhạc / PhoWhisper)
        if _slug_parked(slug):
            continue
        try:
            st, _why = kaggle_api.status(slug)
        except KaggleError:
            continue
        if st.upper() in ("QUEUED", "RUNNING"):
            try:
                kaggle_api.delete_kernel(slug)
                freed.append(slug)
                logging.warning("Kaggle kín %d phiên GPU: đã xoá notebook %s còn phiên %s (bot đã bỏ)", GPU_SESSIONS, slug, st)
            except Exception as e:
                logging.warning("Kaggle: không xoá được notebook %s (%s)", slug, str(e)[:120])
    return freed


FREE_SETTLE = 10.0         # giây chờ Kaggle nhả chỗ sau khi xoá phiên ma rồi mới gửi lại
# 25/11: tài khoản quetrai kẹt hàng chờ chạy nền GPU hơn 20 giờ sau một ngày bot gửi rồi bỏ nhiều phiên (chờ quá lâu -> bỏ ->
# job sau gửi tiếp). Để không «dồn» phiên lên Kaggle: (1) trước mỗi lần gửi phiên GPU, đóng hết phiên công việc còn xếp
# hàng / chạy (bot chạy một phiên một lúc: phiên nào còn là ma); (2) một phiên phải bỏ vì chờ máy quá lâu thì NGHỈ gửi phiên
# GPU mới QUEUE_COOL giây (bếp làm trên Botkeep, truyện bé chờ); (3) Kaggle không cấp GPU (tài khoản chưa xác minh số điện
# thoại: phiên chạy ngay nhưng bằng CPU, không internet — đo thật 25/11 tài khoản traique002) thì nghỉ NOGPU_COOL giây.
QUEUE_COOL = 60 * 60
NOGPU_COOL = 6 * 3600
NO_GPU_MARK = "Kaggle không cấp GPU"
_COOL: dict[str, Any] = {"until": 0.0, "why": ""}


_ACCT: dict[str, Any] = {"tok": None}


def _sync_account() -> None:
    """Đổi tài khoản Kaggle trong Cài đặt lúc bot đang chạy (25/11: quetrai kẹt hàng chờ -> traique): thời gian nghỉ gửi
    GPU và các kết quả «model sẵn sàng» là của tài khoản cũ — bỏ hết, tài khoản mới được xét lại từ đầu."""
    tok = kaggle_api.token()
    if _ACCT["tok"] != tok:
        if _ACCT["tok"] is not None:
            _COOL.update(until=0.0, why="")
            _READY.clear()
            _OUT_OK.clear()
            logging.info("Kaggle: đã đổi tài khoản, xét lại giờ nghỉ gửi GPU và model truyện bé từ đầu")
        _ACCT["tok"] = tok


def gpu_cool() -> str:
    """Lý do đang nghỉ gửi phiên GPU mới ("" = gửi được)."""
    _sync_account()
    left = _COOL["until"] - time.time()
    if left <= 0:
        return ""
    return f"tạm nghỉ gửi việc GPU lên Kaggle thêm {left / 60:.0f} phút ({_COOL['why']})"


def _cool(secs: float, why: str) -> None:
    _sync_account()
    if time.time() + secs > _COOL["until"]:
        _COOL["until"], _COOL["why"] = time.time() + secs, why
        logging.warning("Kaggle: %s", gpu_cool())


class Busy(KaggleError):
    """Không gửi phiên GPU lúc này (đang nghỉ / hàng chờ đang đông): việc làm trên Botkeep, không phải lỗi."""


# Nhớ thời gian chờ máy (25/11, chuyển sang tài khoản traique): hàng chờ chạy nền GPU lúc đông phải 15-42 phút, việc bếp
# chỉ chờ được 8 phút -> gần như lần nào cũng gửi, chờ, rồi xoá notebook. Tạo-rồi-xoá notebook liên tục là kiểu dùng không
# giống người dùng bình thường (nhiều khả năng là lý do tài khoản quetrai bị kẹt hàng chờ). Phiên GPU gần nhất (trong
# QUEUE_MEMORY) phải chờ máy lâu hơn hạn của việc sắp gửi thì không gửi nữa, làm trên Botkeep; truyện bé (chờ được lâu,
# giữ chỗ) vẫn gửi và cập nhật số đo. Phải bỏ phiên nhiều lần trong ngày thì nghỉ dài dần: 1, 2, 4 rồi 6 giờ.
QUEUE_FILE = os.path.join(BASE, "kaggle_queue.json")
QUEUE_MEMORY = 3 * 3600
QUEUE_COOL_MAX = 6 * 3600
_QLOCK = threading.Lock()


def _acct() -> str:
    return hashlib.sha1(kaggle_api.token().encode()).hexdigest()[:12]


def _qstate() -> dict:
    """Số đo của tài khoản đang dùng (đổi tài khoản: bắt đầu lại)."""
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict) or d.get("acct") != _acct():
        return {"acct": _acct(), "waits": [], "gave_up": []}
    return d


def note_queue(waited: float, how: str) -> None:
    """how: «got» (được cấp máy sau `waited` giây), «gave_up» (bỏ vì chờ quá lâu), «parked» (truyện bé giữ chỗ khi
    chưa được cấp máy)."""
    try:
        with _QLOCK:
            d, now = _qstate(), time.time()
            d["waits"] = [w for w in d.get("waits", []) if now - w.get("t", 0) < 86400][-9:] + \
                [{"t": round(now), "wait": round(waited), "how": how}]
            if how == "gave_up":
                d["gave_up"] = [t for t in d.get("gave_up", []) if now - t < 86400] + [round(now)]
            tmp = f"{QUEUE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, QUEUE_FILE)
    except Exception as e:                               # số đo hỏng không được làm hỏng job
        logging.warning("Kaggle: không ghi được thời gian chờ máy (%s)", str(e)[:120])


def queue_busy(limit: float) -> str:
    """Lý do không gửi việc chỉ chờ máy được `limit` giây ("" = gửi được). Xét số đo MỚI NHẤT còn nói được điều gì: phiên
    được cấp máy («got») cho biết đúng thời gian chờ; phiên bỏ / giữ chỗ chỉ cho biết hàng chờ ≥ ngần ấy — ngắn hơn hạn
    thì không kết luận được, bỏ qua (không che mất số đo dài hơn trước đó)."""
    now = time.time()
    for w in reversed(_qstate().get("waits") or []):
        if now - float(w.get("t", 0)) > QUEUE_MEMORY:
            break
        wait, how = float(w.get("wait", 0)), w.get("how")
        if how != "got" and wait <= limit:
            continue
        if wait <= limit:
            return ""
        return (f"Kaggle đang đông: phiên GPU gần nhất {'chờ' if how == 'got' else 'chờ mà chưa được cấp'} máy "
                f"{wait / 60:.0f} phút, việc này chỉ chờ được {limit / 60:.0f} phút — làm trên Botkeep, không gửi rồi bỏ "
                "phiên")
    return ""


def queue_cool() -> float:
    """Thời gian nghỉ sau khi phải bỏ phiên: 1 giờ, lần thứ hai trong 24 giờ 2 giờ, rồi 4, tối đa 6 giờ."""
    n = len([t for t in _qstate().get("gave_up", []) if time.time() - t < 86400])
    return float(min(QUEUE_COOL_MAX, QUEUE_COOL * 2 ** max(0, n - 1)))


def _push(slug: str, title: str, text: str, gpu: bool, sources: list | None, dss: list | None, on_step,
          wait_max: float = 0.0, stop=None, queue_limit: float | None = None) -> dict:
    """kaggle_api.push + xử lý «kín phiên GPU»: xoá phiên ma rồi gửi lại; vẫn kín thì chờ (wait_max giây, truyện bé) mỗi
    SLOT_WAIT gửi lại, hoặc ném SessionLimit (bếp: dựng trên Botkeep). stop() đúng (Hủy / tắt bot / quá hạn) thì thôi
    chờ ngay (ném KaggleError «đã huỷ»). Chỉ gọi khi giữ _LOCK (bot chạy một phiên một lúc: phiên nào còn đều là ma)."""
    if gpu:
        why = gpu_cool() or (queue_busy(queue_limit) if queue_limit else "")
        if why:
            raise Busy(why)
        _free_slots(slug)                     # đóng hết phiên công việc còn xếp hàng / chạy rồi mới gửi phiên mới
    t0 = time.time()
    freed = False
    while True:
        if stop is not None and stop():
            raise KaggleError("đã huỷ")
        kw: dict = {}
        if sources:
            kw["kernel_sources"] = sources
        if dss:
            kw["dataset_sources"] = dss
        try:
            return kaggle_api.push(slug, title, text, gpu=gpu, **kw)
        except SessionLimit:
            if gpu and not freed:              # vừa đóng ở trên mà Kaggle chưa kịp nhả chỗ: đóng lại lần nữa, chờ rồi gửi
                freed = True
                _free_slots(slug)
                kaggle_api._sleep(FREE_SETTLE)
                continue
            if time.time() - t0 >= wait_max or (wait_max and _others_waiting()):
                raise
            on_step(f"Kaggle: kín {GPU_SESSIONS} phiên GPU, chờ chỗ ({(time.time() - t0) / 60:.0f} phút)")
            for _ in range(int(SLOT_WAIT // 5)):            # ngủ từng đoạn ngắn để Hủy / tắt bot có tác dụng ngay
                if stop is not None and stop():
                    raise KaggleError("đã huỷ") from None
                kaggle_api._sleep(5)


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


def _wait_session(slug: str, timeout: float, on_status, every: float = 20, queue_max: float | None = None,
                  park_info: dict | None = None, measure: bool = True):
    """Chờ phiên Kaggle xong. Chờ hỏng (quá giờ, chờ máy quá queue_max, mất mạng, bị huỷ, notebook lỗi) thì bỏ phiên:
    phiên chưa chạy tự thoát nhờ hạn `expires_at` (trước đây phiên quá giờ vẫn nằm trong hàng chờ, lát sau tự chạy,
    tốn giờ GPU và chặn lượt kế). park_info (truyện bé): thay vì bỏ, giữ chỗ khi chờ máy quá queue_max hoặc có job khác
    chờ (ném Parked, kids_auto thu kết quả sau). measure=False: gọi lâu sau khi gửi (thu kết quả phiên giữ chỗ) — không đo
    thời gian chờ máy, không nghỉ gửi GPU (đã đo / đã nghỉ lúc giữ chỗ)."""
    run = _watch(slug)
    run["park_info"] = park_info
    t_push, seen = time.time(), {"got": False, "queue": False}

    def _status(s, secs):                              # lần đầu thấy hết QUEUED: ghi thời gian chờ máy
        if measure and not seen["got"] and "QUEUED" not in str(s).upper():
            seen["got"] = True
            note_queue(time.time() - t_push, "got")
        if on_status:
            on_status(s, secs)
    park = None
    if park_info is not None:
        qmax = queue_max

        def park(secs: float) -> bool:
            if qmax is not None and secs > qmax:       # hàng chờ GPU đông thật
                seen["queue"] = True
                return True
            return secs > KIDS_PARK_MIN and _others_waiting()   # nhường Botkeep cho job khác: Kaggle không có lỗi gì
        queue_max = None
    try:
        kaggle_api.wait(slug, timeout, every=every, on_status=_status, stop=lambda: run["flag"], queue_max=queue_max,
                        park=park)
    except Parked as e:
        e.info = park_info
        if park_info is not None:
            _park(park_info)
        if seen["queue"] and measure:                  # hàng chờ GPU đông: không gửi thêm phiên một lúc
            note_queue(time.time() - t_push, "parked")
            _cool(queue_cool(), "Kaggle lâu không cấp máy GPU")
        raise
    except KaggleError as e:
        if "chờ máy quá" in str(e) and measure:
            note_queue(time.time() - t_push, "gave_up")
            _cool(queue_cool(), "Kaggle lâu không cấp máy GPU")
        if run.get("parked"):                          # abort(park=True) đã ghi giữ chỗ: không báo «phiên sẽ tự thoát»
            pass
        elif park_info is not None:
            _cancel_session(slug, long_expiry=True)
        else:
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
    why = enabled() or gpu_cool()
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
    qa = PIP_QA if config.VOICE_QA != "0" else ""
    if is_kids(spec):
        return PIP_KIDS + qa
    if any(j.get("kind") == "analyze" for j in _jobs(spec)):
        return PIP_ANALYZE + (PIP_ROFORMER if config.VOICE_SEP_MODEL == "auto" else "")
    return PIP + qa if needs_vieneu(spec) else PIP_LIGHT


def kernel_text(spec: dict) -> str:
    spec_b64 = base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode()
    pip = pip_for(spec)
    text = f'''# rm-video: job tự động (bot tạo, đừng sửa tay). Không chứa key API.
import base64, io, json, subprocess, sys, time, zipfile
if time.time() > {float(spec.get("expires_at") or 0)!r} > 0:
    print("rm-video: bot đã thôi chờ việc này (chờ máy quá lâu / đã huỷ), thoát ngay để khỏi tốn giờ GPU")
    sys.exit(0)
if {bool(spec.get("need_gpu"))!r} and subprocess.run("nvidia-smi -L", shell=True, capture_output=True).returncode:
    _e = "{NO_GPU_MARK} cho phiên này (chạy bằng CPU, thường do tài khoản chưa xác minh số điện thoại)"
    for _t in {[f"_{k}" for k in range(len(spec["jobs"]))] if spec.get("kind") == "batch" else [""]!r}:
        json.dump({{"ok": False, "error": _e, "token": None}}, open(f"/kaggle/working/result{{_t}}.json", "w"))
    print("rm-video:", _e)
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
    from rmvideo.media import framing
    if framing.spec_value():                           # khung dọc theo nội dung Botkeep đã chọn (1.1.2)
        spec.setdefault("framing", framing.spec_value())
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
            import bgm
            music = bgm.kaggle_ref({k: voice_bed.get(k) for k in ("path", "url", "lyria") if voice_bed.get(k)})
            spec["voice_bed"] = {"music": music, "trim": voice_bed.get("trim", 0.0), "name": voice_bed.get("name", "")}
            if music.get("dataset"):             # kho nhạc Lyria của bếp: gắn dataset vào phiên
                spec["dataset_sources"] = [music["dataset"]]
        except KaggleError as e:
            logging.info("Nhạc lót không gửi kèm Kaggle được (%s): dựng không nhạc lót", e)
    if voice_name(job.voice) == CLONE:
        spec["ref_b64"] = base64.b64encode(open(VOICE_REF, "rb").read()).decode()
        spec["ref_ext"] = ".mp3"
    return _common(spec)


def bed_spec(job, out_dur: float, render_dur: float, stretch: float, allow_hd: bool, title: str, bands,
             max_duration: int, music: str | None, music_url: str, trim: float, orig: str,
             local_path: str | None = None, src: dict | None = None, music_ref: dict | None = None) -> dict:
    """Video KHÔNG LỜI (NO_SPEECH=music): dựng như remake (tiêu đề, logo, che chữ, màu) nhưng tiếng là nhạc nền (+ tiếng
    gốc theo BGM_ORIG) thay cho giọng đọc. Không đọc giọng, không tách giọng: chạy CPU, không tốn giờ GPU.
    music: file nhạc trên Botkeep (Kaggle lấy qua PUBLIC_URL); music_url: link gốc (Kaggle tự tải). Không có cả hai:
    chỉ tiếng gốc. music_ref: đầu vào đã dựng sẵn (bài Lyria: file trong dataset `rmvideo-cook-bgm`, gắn vào phiên)."""
    spec = {"kind": "bed", "job": job.id, "mode": job.mode,
            "src": src or ref(local_path or job.local or None, job.url, "video", max_duration),
            "music": music_ref or (ref(music, music_url, "audio") if (music or music_url) else None),
            "trim": round(float(trim), 3), "orig": orig, "out_dur": out_dur, "render_dur": render_dur,
            "stretch": stretch, "allow_hd": allow_hd, "title": title, "bands": bands or []}
    if music_ref and music_ref.get("dataset"):
        spec["dataset_sources"] = [music_ref["dataset"]]
    return _common(spec)


def analyze_spec(job, local_path: str | None, title: str, desc: str, want_gray: bool, max_duration: int) -> dict:
    """Việc bóc lời + phân tích hình (chạy TRƯỚC khi AI viết lời). Khoá ngôn ngữ tiếng Việt khi tiêu đề/mô tả có dấu
    tiếng Việt (Whisper đoán ngôn ngữ trên quãng nhạc dạo hay sai); không thì để Whisper tự đoán (nguồn nước ngoài)."""
    import vocab
    vi = bool(re.search(r"[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", f"{title} {desc}".lower()))
    return _common({"kind": "analyze", "job": job.id, "src": ref(local_path or job.local or None, job.url, "video",
                                                                  max_duration),
                    "want_gray": bool(want_gray), "language": "vi" if vi else "", "token": secrets.token_hex(8),
                    "prompt": vocab.groq_prompt(title, desc), "keep_ambient": job.mode == "cook",
                    "inpaint": config.TEXT_INPAINT in ("1", "band"),   # LaMa xoá chữ in cứng (GPU) thay cho che mờ
                    "inpaint_mask": "band" if config.TEXT_INPAINT == "band" else "text",
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
    nbs = nbs or [SLUG_KIDS_MODELS]
    src = lora_source()
    if src and not (src[0] == "notebook" and src[1] == SLUG_KIDS_MODELS):   # LoRA nhân vật: gắn dataset / notebook chứa file
        lst = dss if src[0] == "dataset" else nbs                          # (bản trong notebook model: đã gắn sẵn nếu dùng)
        if src[1] not in lst:
            lst.append(src[1])
    return nbs, dss


def lora_source() -> tuple[str, str, str] | None:
    """KIDS_LORA -> (dataset|notebook, slug, tên file) hoặc None (tắt / tài khoản không có dataset LoRA).
    «dcn_lora_v1» = dataset rmvideo-kids-lora; «dataset:slug/tên», «notebook:slug/tên» (vd notebook:rmvideo-lora-train/
    dcn_lora_v1 = đầu ra notebook train). Dataset được hỏi Kaggle có tồn tại không (nhớ 1 giờ): không có thì vẽ không LoRA
    thay vì để Kaggle từ chối cả phiên."""
    v = (config.KIDS_LORA or "").strip()
    if not v or v.lower() in ("0", "off", "none"):
        return None
    kind, _, rest = v.partition(":")
    if not rest:
        kind, rest = "dataset", v
    kind = "notebook" if kind.strip().lower() == "notebook" else "dataset"
    slug, _, name = rest.strip("/ ").rpartition("/")
    slug = slug or (SLUG_KIDS_LORA if kind == "dataset" else SLUG_LORA_TRAIN)
    if not name:
        return None
    if kind == "dataset" and kaggle_api.configured():
        now = time.time()
        hit = _LORA_OK.get(slug)
        if not hit or now - hit[1] > 3600:
            try:
                ok = kaggle_api.dataset_exists(slug)
            except Exception as e:                       # không hỏi được: cứ gắn, Kaggle sẽ báo nếu sai
                logging.warning("Không kiểm được dataset LoRA %s (%s)", slug, str(e)[:120])
                ok = True
            _LORA_OK[slug] = (ok, now)
            if not ok:
                logging.info("Truyện bé: không có dataset LoRA %s, dùng bản trong notebook model (%s)", slug,
                             ", ".join(kids_sources_nb()))
        if not _LORA_OK[slug][0]:                        # 1.4.9: kids_setup tải LoRA vào đầu ra notebook model
            return "notebook", SLUG_KIDS_MODELS, name
    return kind, slug, name


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
                      "multi": config.KIDS_MULTI_VOICE != "0", "en": "" if config.KIDS_EN_VOICE in ("", "off") else config.KIDS_EN_VOICE},
            "image": {"model": config.KIDS_IMAGE_MODEL, "check": config.KIDS_IMAGE_CHECK != "0",
                      "lora": (lora_source() or ("", "", ""))[2]},
            "llm": {"pattern": config.KIDS_LLM_PATTERN, "hf_repo": config.KIDS_LLM_HF,
                    "think": int(config.KIDS_LLM_THINK or 0)},
            "channel": {"name": config.KIDS_CHANNEL_NAME, "world": data["world"]["name"], "tagline": config.KIDS_TAGLINE,
                        "hashtags": config.KIDS_HASHTAGS},
            "music": music, "fx": config.KIDS_FX, "subs": str(getattr(config, "KIDS_SUBS", "1")) != "0",
            "kernel_sources": nbs, "dataset_sources": dss,
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
if subprocess.run("nvidia-smi -L", shell=True, capture_output=True).returncode:
    raise SystemExit("Kaggle không cấp GPU cho phiên này (chạy bằng CPU, thường do tài khoản chưa xác minh số điện thoại)")
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
# 3. LoRA 4 nhân vật (1.4.9): bản lưu trong repo GitHub công khai (Git LFS, 164 MB) -> đầu ra notebook này, phiên dựng tìm
#    theo tên file trong mọi thứ đã gắn (images.find_lora). Mất dataset rmvideo-kids-lora (lỡ xoá 24/11) vẫn có LoRA.
try:
    import hashlib, socket, urllib.request
    socket.setdefaulttimeout(120)        # kết nối đứng im không giữ phiên GPU tới hạn của Kaggle
    os.makedirs(f"{W}/lora", exist_ok=True)
    dst = f"{W}/lora/dcn_lora_v1.safetensors"
    for k in range(3):
        try:
            urllib.request.urlretrieve(__LORA_URL__, dst + ".part")
            h = hashlib.sha256()
            with open(dst + ".part", "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != __LORA_SHA__:
                raise RuntimeError("sai mã sha256")
            os.replace(dst + ".part", dst)
            done["lora"] = [os.path.basename(dst)]
            print("đã tải LoRA (%.0f MB)" % (os.path.getsize(dst) / 2**20), flush=True)
            break
        except Exception as e:
            print("!! LoRA lần", k + 1, "lỗi:", str(e)[:300], flush=True)
            time.sleep(10)
finally:
    if os.path.exists(f"{W}/lora/dcn_lora_v1.safetensors.part"):
        os.remove(f"{W}/lora/dcn_lora_v1.safetensors.part")
# (model chữ GGUF ~20GB vượt giới hạn đầu ra notebook: phiên dựng tải từ Hugging Face, ~3 phút)
print("KẾT QUẢ:", {k: len(v) for k, v in done.items()}, "| đầu ra %.1f GB" % used_gb(), flush=True)
if "llama" not in done or not ({"flux", "zimage"} & set(done)): raise SystemExit("thiếu: " + ", ".join(k for k in ("llama", "flux/zimage") if k not in done and not ({"flux", "zimage"} & set(done))))
'''


LORA_FILE = "dcn_lora_v1.safetensors"
LORA_SHA = "e5e1d8a5b924c2b2c566dd3f466c8e04ac6c77c8b024c928e96333b3f524a44b"     # = scripts/push_kids_lora.py
LORA_URL = ("https://media.githubusercontent.com/media/traique/rm-video/95be355cad019a3cc37f648a97e6ce13d1d2d615/lora/"
            + LORA_FILE)            # ghim theo commit (không theo nhánh: đổi / xoá nhánh vẫn tải được)


def kids_models_kernel() -> str:
    return KIDS_MODELS_KERNEL.replace("__LORA_URL__", repr(LORA_URL)).replace("__LORA_SHA__", repr(LORA_SHA))


def kids_setup() -> str:
    """Đẩy notebook chuẩn bị model (chạy một lần). Trả câu báo; lỗi thì ném KaggleError."""
    if not kaggle_api.configured():
        raise KaggleError("chưa điền KAGGLE_API_TOKEN trong Cài đặt")
    try:                                 # KHÔNG dọn phiên ma (_free_slots): kids_setup chạy ngoài _LOCK, phiên công việc còn đó
        kaggle_api.push(SLUG_KIDS_MODELS, "rmvideo kids models", kids_models_kernel(), gpu=True)   # có thể đang có job chờ
    except SessionLimit:
        raise KaggleError(f"Kaggle đang kín {GPU_SESSIONS} phiên GPU: chạy kids_setup lại khi các phiên kia xong") from None
    _READY.clear()
    return (f"Đã gửi notebook {SLUG_KIDS_MODELS} (biên dịch llama.cpp + lượng tử FLUX.1-schnell + tải LoRA nhân vật, ~45-60 "
            "phút GPU). Xem tiến độ bằng lệnh kids_setup_status hoặc trên kaggle.com; xong thì làm truyện được.")


SETUP_GPU_MIN = 75 * 60     # giây GPU còn trong tuần tối thiểu để tự chạy kids_setup (~45-60 phút)


def auto_setup() -> str:
    """Lúc khởi động: notebook model truyện bé không còn trên Kaggle (404) thì TỰ chạy kids_setup — người dùng yêu cầu
    24/11 sau khi lỡ xoá hết «Your Work». Chỉ khi thật sự mất (không chạy lại khi đang chạy / lỗi, khỏi đốt giờ GPU lặp
    lại), còn đủ giờ GPU và đang dùng notebook mặc định. Trả câu báo ("" khi không làm gì)."""
    if SLUG_KIDS_MODELS not in kids_sources_nb() or not kaggle_api.configured():
        return ""
    try:
        kaggle_api.status(SLUG_KIDS_MODELS)
        return ""
    except KaggleError as e:
        if not _missing(e):
            return ""
    try:
        left = float(kaggle_api.quota()["gpu_left"])
    except Exception:
        left = SETUP_GPU_MIN
    if left < SETUP_GPU_MIN:
        return (f"thiếu notebook {SLUG_KIDS_MODELS} nhưng chỉ còn {left / 3600:.1f} giờ GPU tuần này: gõ kids_setup khi "
                "Kaggle làm mới giờ GPU")
    return "tự chạy kids_setup — " + kids_setup()


_READY: dict[str, float] = {}       # {"ok": lúc kiểm thấy model sẵn sàng}
READY_TTL = 30 * 60


def _missing(e: Exception) -> bool:
    return any(k in str(e) for k in ("404", "403", "NOT_FOUND", "not found", "Not found", "denied"))


_OUT_OK: dict[str, float] = {}


def _custom_ready(slug: str) -> str:
    """Notebook tự đặt trong KIDS_KAGGLE_SOURCES (vd notebook chạy tay kids_setup, 25/11): có đầu ra chưa. "" = có / không
    hỏi được. Notebook chạy tay rồi «Quick Save» thì không có phiên chạy nền (hỏi trạng thái báo 404) nhưng vẫn có đầu ra."""
    _sync_account()
    if time.time() - _OUT_OK.get(slug, 0.0) < READY_TTL:
        return ""
    try:
        files = kaggle_api.outputs(slug).get("files") or []
    except KaggleError as e:
        return f"không thấy notebook {slug} trên Kaggle" if _missing(e) else ""
    if not files:
        return (f"notebook {slug} chưa có đầu ra — chạy xong thì Save Version → Quick Save, mở Advanced Settings chọn "
                "lưu output («Always save outputs»)")
    _OUT_OK[slug] = time.time()
    return ""


def kids_ready() -> str:
    """"" khi notebook model truyện bé (rmvideo-kids-models) đã chạy xong; không thì lý do + cách sửa. Chỉ xét khi dùng
    notebook mặc định (KIDS_KAGGLE_SOURCES trống hoặc có nó). 24/11: người dùng lỡ xoá hết trên Kaggle -> mọi phiên truyện
    bé gắn một notebook không còn: thay vì để Kaggle từ chối / phiên lỗi giữa chừng, báo ngay «gõ kids_setup»."""
    if not kaggle_api.configured():
        return ""
    cool = gpu_cool()
    if cool:
        return cool
    if SLUG_KIDS_MODELS not in kids_sources_nb():          # nguồn tự đặt: notebook của mình phải có đầu ra
        for slug in kids_sources_nb():
            if "/" not in slug:
                why = _custom_ready(slug)
                if why:
                    return why
        return ""
    if time.time() - _READY.get("ok", 0.0) < READY_TTL:
        return ""
    try:
        st, why = kaggle_api.status(SLUG_KIDS_MODELS)
    except KaggleError as e:
        if _missing(e):
            return (f"chưa có notebook {SLUG_KIDS_MODELS} (model vẽ + viết truyện) trên Kaggle — gõ kids_setup "
                    "(~45-60 phút GPU, một lần) rồi làm truyện lại")
        return ""                                       # không hỏi được (mạng): cứ làm, Kaggle sẽ báo nếu sai
    up = st.upper()
    if "COMPLETE" in up:
        _READY["ok"] = time.time()
        return ""
    if up in ("QUEUED", "RUNNING"):
        return f"đang chuẩn bị model truyện bé trên Kaggle ({st}, kids_setup ~45-60 phút): xong mới làm truyện được"
    return f"lần chuẩn bị model truyện bé trên Kaggle lỗi ({st}: {why[:120]}) — gõ kids_setup lại"


def kids_sources_nb() -> list:
    nbs: list = []
    for item in re.split(r"[,\s]+", config.KIDS_KAGGLE_SOURCES or ""):
        kind, _, slug = item.partition(":")
        if item and (not slug or kind.lower() != "dataset"):
            nbs.append((slug or kind).strip("/ "))
    return nbs or [SLUG_KIDS_MODELS]


def resources_report(heal: bool = True) -> list[str]:
    """Kiểm những thứ bot cần trên Kaggle (lệnh console kaggle_check, lúc khởi động): notebook model truyện bé, dataset LoRA,
    kho nhạc (mất thì tự đẩy lại khi heal), notebook PhoWhisper (nếu chọn). Trả các dòng báo."""
    from rmvideo.media import lyria
    out = []
    why = kids_ready()
    out.append(("✅" if not why else "⚠️") + f" Model truyện bé ({SLUG_KIDS_MODELS}): " + (why or "sẵn sàng"))
    src = lora_source()
    custom = [x for x in kids_sources_nb() if "/" not in x and x != SLUG_KIDS_MODELS]
    if src and src[0] != "dataset" and custom:          # notebook tự đặt (vd chạy tay kids_setup): LoRA nằm trong đó
        try:
            have = any(str(f.get("fileName") or "").endswith(LORA_FILE)
                       for x in custom for f in (kaggle_api.outputs(x).get("files") or []))
        except Exception:
            have = None
        out.append(f"✅ LoRA nhân vật: trong notebook {', '.join(custom)}" if have else
                   (f"⚠️ LoRA nhân vật: notebook {', '.join(custom)} không có file LoRA — truyện vẽ không LoRA" if have is False
                    else f"ℹ️ LoRA nhân vật: dùng bản trong notebook {', '.join(custom)} (không kiểm được)"))
    elif src and src[0] == "dataset":
        out.append(f"✅ LoRA nhân vật: dataset {src[1]}")
    elif src and why:
        out.append(f"ℹ️ LoRA nhân vật: không thấy dataset {SLUG_KIDS_LORA}, sẽ dùng bản kids_setup tải vào notebook {src[1]}")
    elif src:
        try:
            have = any(str(f.get("fileName") or "").endswith(LORA_FILE) for f in (kaggle_api.outputs(src[1]).get("files") or []))
        except Exception:
            have = None
        out.append("✅ LoRA nhân vật: trong notebook " + src[1] if have else
                   (f"⚠️ LoRA nhân vật: notebook {src[1]} không có file LoRA — gõ kids_setup lại (truyện đang vẽ không LoRA)"
                    if have is False else f"ℹ️ LoRA nhân vật: dùng bản trong notebook {src[1]} (không kiểm được)"))
    from rmvideo.kids import bgm as kids_bgm
    import bgm
    for lib in (kids_bgm._lib(), bgm.lyria_lib()):
        n = len(lyria.load_library(lib.dir)["tracks"])
        if not n:
            out.append(f"ℹ️ Kho nhạc {lib.dataset}: chưa có bài (bgm_kaggle để tạo)")
            continue
        ok = lyria.remote_ok(lib, force=True)
        if ok:
            out.append(f"✅ Kho nhạc {lib.dataset}: {n} bài")
        else:
            out.append(f"⚠️ Kho nhạc {lib.dataset}: dataset không còn trên Kaggle, " + ("đang đẩy lại {n} bài".format(n=n) if heal
                                                                                       else "lần dựng sau tự đẩy lại"))
            if heal:
                try:
                    lyria.push_dataset_sync(lib, lambda m: logging.info("🎵 %s", m))
                    out[-1] = f"✅ Kho nhạc {lib.dataset}: đã đẩy lại {n} bài"
                except Exception as e:
                    out[-1] = f"⚠️ Kho nhạc {lib.dataset}: đẩy lại lỗi ({str(e)[:120]}), lần dựng sau thử lại"
    for item in re.split(r"[,\s]+", config.KIDS_KAGGLE_SOURCES or ""):           # nguồn tự đặt (vd dataset llama-server)
        kind, _, slug = item.partition(":")
        if not item or (not slug and kind == SLUG_KIDS_MODELS) or slug == SLUG_KIDS_MODELS or "/" in (slug or kind):
            continue                                     # «user/slug» của người khác: API hỏi theo tài khoản mình, không kiểm
        if kind.lower() == "dataset":
            try:
                ok = kaggle_api.dataset_exists(slug)
            except KaggleError as e:
                ok = not _missing(e)
            if not ok:
                out.append(f"⚠️ KIDS_KAGGLE_SOURCES: không còn «{item}» trên Kaggle — xoá khỏi Cài đặt để dùng {SLUG_KIDS_MODELS}")
            continue
        why2 = _custom_ready(slug or kind)                # notebook: xét đầu ra (notebook chạy tay không có phiên chạy nền)
        out.append(f"⚠️ KIDS_KAGGLE_SOURCES «{item}»: {why2}" if why2 else f"✅ KIDS_KAGGLE_SOURCES «{item}»: có đầu ra")
    if config.KAGGLE_ASR == "phowhisper":
        try:
            kaggle_api.status(SLUG_PHOWHISPER)
            out.append(f"✅ PhoWhisper ({SLUG_PHOWHISPER})")
        except KaggleError as e:
            if _missing(e):
                out.append(f"ℹ️ Không có notebook {SLUG_PHOWHISPER}: bóc lời tự dùng Whisper large-v3")
    return out


def kids_setup_status() -> str:
    st, why = kaggle_api.status(SLUG_KIDS_MODELS)
    return f"notebook {SLUG_KIDS_MODELS}: {st}" + (f" — {why[:200]}" if why else "")


# ---------------- chạy ----------------

def fresh_url(slug: str, name: str) -> str:
    """Link tải mới của một file output (link ký sẵn của Google Storage có hạn; tải chậm trên Botkeep có thể quá hạn)."""
    for f in (kaggle_api.outputs(slug).get("files") or []):
        if f.get("fileName") == name and f.get("url"):
            return f["url"]
    raise KaggleError(f"Kaggle không còn file {name}")


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
    park_info = None
    if kids and spec.get("park"):                        # truyện bé: giữ chỗ trong hàng chờ GPU (xem KIDS_PARK_MAX)
        other = _parked_other(slug, spec["park"].get("job"))
        if other:                                        # mọi phiên truyện bé cùng notebook: gửi đè là mất phiên đang giữ chỗ
            raise KaggleError(f"truyện #{other} đang giữ chỗ trên Kaggle (cùng notebook {slug}): chờ nó xong rồi làm truyện mới")
        park_info = {"slug": slug, "token": spec.get("token"), "fetch_files": list(spec.get("fetch_files") or []),
                     "gpu": gpu, "since": time.time(), "expires_at": time.time() + KIDS_PARK_MAX - EXPIRY_MARGIN,
                     "brief": spec.get("brief") or {}, **spec["park"]}
    sent = {**{k: v for k, v in spec.items() if k != "park"}, "need_gpu": gpu}
    text = kernel_text(with_expiry(sent, KIDS_PARK_MAX if park_info else queue_max))
    dss = list(spec.get("dataset_sources") or []) or None
    run = _watch(slug)                                   # Hủy / tắt bot lúc đang chờ chỗ GPU: dừng ngay (abort đặt cờ)
    try:
        try:
            _push(slug, spec.get("title") or "rmvideo worker", text, gpu, sources or None, dss, on_step,
                  wait_max=queue_max if park_info else 0.0, stop=lambda: run["flag"],
                  queue_limit=None if park_info else queue_max)
        except (SessionLimit, Busy):
            raise
        except KaggleError as e:
            if not reuse or SLUG_ANALYZE not in sources or run["flag"]:
                raise
            # đầu ra phiên phân tích (tiếng nền đã tách) không còn (notebook đã xoá / ghi đè): gửi lại không gắn, phiên
            # dựng tự tách lại (kaggle_job.mounted_ambient không thấy đúng mã)
            logging.warning("Kaggle không nhận notebook kèm %s (%s): gửi lại không dùng lại tiếng nền", SLUG_ANALYZE, str(e)[:120])
            sources = [x for x in sources if x != SLUG_ANALYZE]
            _push(slug, spec.get("title") or "rmvideo worker", text, gpu, sources or None, dss, on_step,
                  wait_max=queue_max if park_info else 0.0, stop=lambda: run["flag"],
                  queue_limit=None if park_info else queue_max)
        if run["flag"]:                                  # bị huỷ đúng lúc vừa gửi xong: bỏ phiên vừa gửi
            _cancel_session(slug)
            raise KaggleError("đã huỷ")
    finally:
        _unwatch(run)
    if park_info:                                        # ghi ngay từ lúc gửi: bot tắt lúc đang tải kết quả vẫn còn mục để
        _park(park_info)                                 # chạy lại thu về (rà soát 24/11); xong / lỗi thật thì flow/kids.py bỏ mục

    def st(s, secs):
        on_step(f"Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} ({secs / 60:.0f} phút)")
    _wait_session(slug, total, st, queue_max=queue_max, park_info=park_info)
    return _fetch(slug, jobs, spec.get("kind") == "batch", tmp, on_step, gpu)


def _fetch(slug: str, jobs: list, batch: bool, tmp: str, on_step, gpu: bool) -> list:
    """Tải kết quả của phiên đã chạy xong về `tmp`: mỗi việc trong `jobs` ({token, fetch_files}) một kết quả / KaggleError."""
    n = len(jobs)
    d = kaggle_api.outputs(slug)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}

    def get(name: str, dst: str, max_bytes: int = 1 << 31, on_progress=None) -> int:
        return kaggle_api.download(files[name], dst, max_bytes, refresh=lambda: fresh_url(slug, name),
                                   on_progress=on_progress)
    out = []
    for k in range(n):
        tag = f"_{k}" if batch else ""
        try:
            if f"result{tag}.json" not in files:
                raise KaggleError("Kaggle không trả kết quả. Log: " + kaggle_api.log_tail(d, 600))
            rj = os.path.join(tmp, f"kaggle_result{tag}.json")
            get(f"result{tag}.json", rj, 5 << 20)
            res = json.load(open(rj, encoding="utf-8"))
            if not res.get("ok"):
                if NO_GPU_MARK in str(res.get("error") or ""):
                    _cool(NOGPU_COOL, "Kaggle không cấp GPU — tài khoản đã xác minh số điện thoại chưa?")
                raise KaggleError("Việc trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
            if "token" in res and jobs[k].get("token") and res["token"] != jobs[k]["token"]:
                raise KaggleError("kết quả trên Kaggle không phải của việc này (notebook bị việc khác ghi đè)")
            if f"output{tag}.mp4" not in files:
                raise KaggleError("Kaggle không trả video")
            part = f" {k + 1}/{n}" if n > 1 else ""
            on_step(f"Kaggle: tải video{part} về")
            vid = os.path.join(tmp, f"output{tag}.mp4")
            get(f"output{tag}.mp4", vid, on_progress=lambda got, size, part=part: on_step(
                f"Kaggle: tải video{part} về ({got / 1e6:.0f}" + (f"/{size / 1e6:.0f}" if size else "") + " MB)"))
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
                        get(remote, p, 50 << 20)
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
        _push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(with_expiry({**spec, "need_gpu": True}, QUEUE_ANALYZE)), True, src,
              None, on_step, queue_limit=QUEUE_ANALYZE)
    except (SessionLimit, Busy):
        raise
    except KaggleError as e:
        if not src:
            raise
        on_step("Kaggle: chưa có notebook PhoWhisper, dùng large-v3")   # tài khoản chưa chạy rmvideo-phowhisper
        logging.warning("Không gắn được notebook %s (%s): bóc lời bằng large-v3", SLUG_PHOWHISPER, str(e)[:120])
        spec = {**spec, "asr": "large-v3"}
        _push(SLUG_ANALYZE, "rmvideo analyze", kernel_text(with_expiry({**spec, "need_gpu": True}, QUEUE_ANALYZE)), True,
              None, None, on_step, queue_limit=QUEUE_ANALYZE)

    def st(s, secs):
        on_step(f"Bóc lời + phân tích hình trên Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} "
                f"({secs / 60:.0f} phút)")
    _wait_session(SLUG_ANALYZE, TIMEOUT_ANALYZE, st, every=15, queue_max=QUEUE_ANALYZE)
    d = kaggle_api.outputs(SLUG_ANALYZE)
    files = {f.get("fileName"): f.get("url") for f in d.get("files", []) if f.get("fileName")}
    if "result.json" not in files:
        raise KaggleError("Kaggle không trả kết quả phân tích. Log: " + kaggle_api.log_tail(d, 600))
    rj = os.path.join(tmp, "kaggle_analysis.json")
    kaggle_api.download(files["result.json"], rj, 20 << 20, refresh=lambda: fresh_url(SLUG_ANALYZE, "result.json"))
    res = json.load(open(rj, encoding="utf-8"))
    if not res.get("ok"):
        if NO_GPU_MARK in str(res.get("error") or ""):
            _cool(NOGPU_COOL, "Kaggle không cấp GPU — tài khoản đã xác minh số điện thoại chưa?")
        raise KaggleError("Phân tích trên Kaggle lỗi: " + (res.get("error") or "?")[-600:])
    if res.get("token") != spec.get("token"):
        raise KaggleError("kết quả phân tích không phải của job này (notebook bị việc khác ghi đè)")
    on_step("Bóc lời + phân tích hình trên Kaggle: tải kết quả về")
    for name, key in (("thumbs.zip", "thumbs_zip"), ("ambient.flac", "ambient"), ("clean.mp4", "clean")):
        if name in files:
            p = os.path.join(tmp, f"kaggle_{name}")
            try:
                kaggle_api.download(files[name], p, 400 << 20, refresh=lambda name=name: fresh_url(SLUG_ANALYZE, name))
                res[key] = p
            except Exception as e:                  # thiếu ảnh / tiếng nền: vẫn dùng được lời đã bóc
                logging.warning("Không tải được %s từ Kaggle: %s", name, e)
    return res


def _log_quota_sync():
    if not kaggle_api.configured():
        return
    try:
        logging.info("Kaggle: %s", kaggle_api.quota_text(kaggle_api.quota_cached(0)))
    except Exception as e:                          # đọc hạn mức lỗi không được làm hỏng việc vừa xong
        logging.info("Kaggle: không đọc được giờ GPU còn lại (%s)", str(e)[:120])


async def log_quota():
    """Sau mỗi phiên Kaggle: ghi log số giờ GPU còn lại trong tuần (đọc mới, không dùng bộ nhớ đệm)."""
    await asyncio.to_thread(_log_quota_sync)


async def analyze(spec: dict, tmp: str, on_step=None) -> dict:
    """Bóc lời + phân tích hình trên Kaggle. Trả kết quả thô (whisper, cuts, bands, ...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = await asyncio.to_thread(_run_analysis_sync, spec, tmp, on_step)
    finally:
        release(spec)
        await log_quota()
    logging.info("Kaggle phân tích xong: %s", res.get("timings"))
    return res


def _collect_sync(park: dict, tmp: str, on_step) -> list:
    """Thu kết quả phiên truyện bé đang giữ chỗ: chưa xong thì chờ tiếp (vẫn giữ chỗ được), xong thì tải về."""
    slug = park["slug"]
    st0, _why = kaggle_api.status(slug)
    if not any(x in st0.upper() for x in kaggle_api.DONE):
        left = max(120.0, float(park.get("expires_at") or 0) + KIDS_RUN_RESERVE - time.time())

        def st(s, secs):
            on_step(f"Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang làm'} ({secs / 60:.0f} phút, giữ chỗ)")
        _wait_session(slug, left, st, queue_max=KIDS_TIMEOUT - KIDS_RUN_RESERVE, park_info=park, measure=False)
    on_step("Kaggle: tải kết quả về")
    return _fetch(slug, [{"token": park.get("token"), "fetch_files": list(park.get("fetch_files") or [])}], False, tmp,
                  on_step, bool(park.get("gpu", True)))


async def collect_parked(park: dict, tmp: str, on_step=None) -> dict:
    """Thu kết quả của phiên truyện bé đã giữ chỗ (kids_auto.park_record). Trả kết quả như produce hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = (await asyncio.to_thread(_collect_sync, park, tmp, on_step))[0]
    finally:
        await log_quota()
    if isinstance(res, Exception):
        raise res
    logging.info("Kaggle (giữ chỗ) xong: %s", res.get("timings"))
    return res


async def produce(spec: dict, tmp: str, on_step=None) -> dict:
    """Một việc trên Kaggle (mỗi lúc một phiên). Trả kết quả (video, plan, srt, notes...) hoặc ném KaggleError."""
    on_step = on_step or (lambda s: None)
    try:
        async with _LOCK:
            res = (await asyncio.to_thread(_run_sync, spec, tmp, on_step))[0]
    finally:
        release(spec)
        await log_quota()
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
    dss = sorted({d for s in specs for d in s.get("dataset_sources") or []})     # kho nhạc Lyria: gắn một lần cho cả lô
    if dss:
        spec["dataset_sources"] = dss
    try:
        async with _LOCK:
            return await asyncio.to_thread(_run_sync, spec, tmp, on_step)
    except Exception as e:                       # mọi lỗi (kể cả lỗi gói notebook) -> từng phần làm trên Botkeep
        return [e if isinstance(e, KaggleError) else KaggleError(str(e)[:300])] * len(specs)
    finally:
        release(spec)
        await log_quota()


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
