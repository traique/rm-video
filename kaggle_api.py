"""Gọi API Kaggle bằng token kiểu mới (Settings -> API Tokens), không cần cài gói `kaggle`.

API của thư viện chính thức (kagglesdk): POST https://api.kaggle.com/v1/<service>/<method>, body JSON camelCase,
`Authorization: Bearer <token>`. Kaggle trả 404 cho MỌI request không xưng là client của họ (User-Agent phải bắt đầu
bằng `kaggle-api/`) — đo thật 12/11/2026."""
import json
import logging
import os
import re
import threading
import time

import requests

import config

API = os.environ.get("KAGGLE_API_URL", "https://api.kaggle.com/v1")
UA = "kaggle-api/v1.8.0 rm-video"
KERNELS = "kernels.KernelsApiService"


class KaggleError(RuntimeError):
    pass


class SessionLimit(KaggleError):
    """Kaggle từ chối nhận notebook: «Maximum batch GPU session count of 2 reached» (đo thật 24/11 job 4). Phiên bot đã
    bỏ (chờ máy quá lâu rồi dựng trên Botkeep) vẫn xếp hàng và chiếm chỗ tới khi được cấp máy; kaggle_worker xoá các
    notebook có phiên «ma» rồi gửi lại."""


class Parked(KaggleError):
    """Bot thôi chờ nhưng GIỮ phiên trong hàng chờ Kaggle (truyện bé: hạn bắt đầu dài, kids_auto theo dõi rồi thu kết quả).
    `info`: mô tả phiên để thu kết quả sau (kaggle_worker gắn)."""

    def __init__(self, msg: str, info: dict | None = None):
        super().__init__(msg)
        self.info = info


_sleep = time.sleep            # test thay bằng hàm không chờ
CALL_RETRIES = 2               # hỏi Get* / List* lỗi tạm: hỏi lại chừng này lần
PUSH_TRIES = 4                 # gửi notebook lỗi tạm: tối đa chừng này lần (kiểm Kaggle đã nhận chưa trước khi gửi lại)
PUSH_WAIT = (5.0, 15.0, 30.0)
_TRANSIENT_RE = re.compile(r"Kaggle lỗi (429|499|5\d\d) ")


def transient(e: Exception) -> bool:
    """Lỗi tạm của mạng / máy chủ Kaggle (mất kết nối, 429, 499, 5xx): thử lại được. 22/11 tối (job 12, 13): SaveKernel
    «Connection aborted» rồi «499», Google đang quá tải (Gemini cũng 503) — bot bỏ job ngay ở lần gửi đầu."""
    s = str(e)
    return "Không gọi được Kaggle" in s or bool(_TRANSIENT_RE.search(s))


def clean_user(v: str) -> str:
    """'https://www.kaggle.com/QueTrai/' / '@quetrai' / ' quetrai ' -> 'quetrai'."""
    v = (v or "").strip()
    v = re.sub(r"(?i)^(https?://)?(www\.)?kaggle\.com/", "", v).strip("/@ ")
    return v.split("/")[0].lower()


def token() -> str:
    return (config.KAGGLE_API_TOKEN or "").strip()


_OWNER: dict = {}          # token -> username chủ token (chỉ lưu khi hỏi Kaggle thành công)
_OWNER_LOCK = threading.Lock()


def owner() -> str:
    """Username thật của chủ token (Kaggle IntrospectToken). '' nếu không hỏi được."""
    tok = token()
    if not tok:
        return ""
    with _OWNER_LOCK:
        if tok in _OWNER:
            return _OWNER[tok]
    d = call("IntrospectToken", {"token": tok}, service="security.OAuthService", timeout=30)
    name = clean_user(str(d.get("username") or ""))
    if name:
        with _OWNER_LOCK:
            _OWNER[tok] = name
    return name


def user() -> str:
    """Username dùng trong slug notebook. Lấy theo token: điền sai KAGGLE_USERNAME (dán cả link, sai chính tả) thì
    Kaggle tưởng là notebook mới và báo 409 'title already in use' — đo thật 12/11/2026."""
    try:
        name = owner()
    except KaggleError:
        name = ""
    name = name or clean_user(config.KAGGLE_USERNAME)
    if not name:
        raise KaggleError("Không biết tên tài khoản Kaggle: điền KAGGLE_USERNAME trong Cài đặt")
    return name


def configured() -> bool:
    """Chỉ cần token: tên tài khoản tự lấy theo token (KAGGLE_USERNAME chỉ để dự phòng)."""
    return bool(token())


def call(method: str, body: dict, service: str = KERNELS, timeout: float = 120) -> dict:
    """Một lời gọi API. Lời gọi chỉ đọc (Get* / List*) lỗi tạm thì hỏi lại CALL_RETRIES lần; lời gọi ghi (SaveKernel...)
    không tự gọi lại ở đây (push tự kiểm Kaggle đã nhận chưa rồi mới gửi lại)."""
    tries = 1 + (CALL_RETRIES if method.startswith(("Get", "List")) else 0)
    for k in range(tries):
        try:
            return _call(method, body, service, timeout)
        except KaggleError as e:
            if k == tries - 1 or not transient(e):
                raise
            _sleep(2.0 * (k + 1))
    raise KaggleError(f"Không gọi được Kaggle ({method})")          # pragma: no cover


def _call(method: str, body: dict, service: str = KERNELS, timeout: float = 120) -> dict:
    tok = token()
    try:
        r = requests.post(f"{API}/{service}/{method}", json=body, timeout=timeout,
                          headers={"Authorization": f"Bearer {tok}", "User-Agent": UA,
                                   "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise KaggleError(f"Không gọi được Kaggle ({method}): {str(e)[:150]}") from None
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code in (401, 403):
        raise KaggleError(f"Token Kaggle sai hoặc hết hạn ({r.status_code}). Tạo token mới trong Kaggle -> Settings "
                          "-> API Tokens rồi dán vào KAGGLE_API_TOKEN.")
    if r.status_code >= 400:
        raise KaggleError(f"Kaggle lỗi {r.status_code} ở {method}: {str(d)[:200] or r.text[:200]}")
    return d


DATASETS = "datasets.DatasetApiService"
BLOBS = "blobs.BlobApiService"


def upload_blob(path: str, kind: str = "DATASET", timeout: float = 900) -> str:
    """Tải một file lên kho blob của Kaggle (StartBlobUpload -> PUT lên link GCS đã ký, như gói kaggle); trả token để gắn
    vào dataset."""
    import mimetypes
    st = os.stat(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    d = call("StartBlobUpload", {"type": kind, "name": os.path.basename(path), "contentType": mime,
                                 "contentLength": st.st_size, "lastModifiedEpochSeconds": int(st.st_mtime)}, service=BLOBS)
    url, tok = d.get("createUrl"), d.get("token")
    if not url or not tok:
        raise KaggleError(f"Kaggle không cấp chỗ tải file {os.path.basename(path)}: {str(d)[:200]}")
    try:
        with open(path, "rb") as f:
            r = requests.put(url, data=f, timeout=timeout, headers={"Content-Length": str(st.st_size)})
    except requests.RequestException as e:
        raise KaggleError(f"Không tải được {os.path.basename(path)} lên Kaggle: {str(e)[:150]}") from None
    if r.status_code >= 300:
        raise KaggleError(f"Kaggle từ chối file {os.path.basename(path)} ({r.status_code}): {r.text[:200]}")
    return str(tok)


def dataset_status(slug: str) -> dict:
    return call("GetDatasetStatus", {"ownerSlug": user(), "datasetSlug": slug}, service=DATASETS, timeout=60)


def dataset_exists(slug: str) -> bool:
    """Dataset của tài khoản có chưa. Đo thật 19/11/2026: slug chưa có thì Kaggle trả 403 «Permission 'datasets.get' was
    denied» (không phải 404) -> coi như chưa có; token hỏng thật thì CreateDataset ngay sau sẽ báo rõ."""
    try:
        dataset_status(slug)
        return True
    except KaggleError as e:
        if any(k in str(e) for k in ("404", "403", "NOT_FOUND", "not found", "denied")):
            return False
        raise


def dataset_push(slug: str, title: str, files: list, notes: str = "") -> dict:
    """Dataset riêng tư của tài khoản: tạo mới (lần đầu) hoặc thêm phiên bản mới gồm ĐÚNG các file này (xoá bản cũ).
    Dùng cho kho nhạc truyện bé (rmvideo/kids/bgm.py): Kaggle gắn vào /kaggle/input, không cần PUBLIC_URL."""
    tokens = [{"token": upload_blob(p)} for p in files]
    u = user()
    if dataset_exists(slug):
        d = call("CreateDatasetVersion", {"ownerSlug": u, "datasetSlug": slug,
                                          "body": {"versionNotes": notes or "update", "files": tokens,
                                                   "deleteOldVersions": True}}, service=DATASETS, timeout=300)
    else:
        d = call("CreateDataset", {"ownerSlug": u, "slug": slug, "title": title, "licenseName": "other", "isPrivate": True,
                                   "files": tokens}, service=DATASETS, timeout=300)
    if d.get("error"):
        raise KaggleError(f"Kaggle không nhận dataset {slug}: {str(d['error'])[:200]}")
    return d


def dataset_wait(slug: str, timeout: float = 900, every: float = 15, sleep=time.sleep) -> str:
    """Chờ phiên bản mới nhất của dataset xử lý xong (READY); FAILED / quá giờ thì KaggleError. Đo thật 19/11/2026: ngay sau
    CreateDataset, GetDatasetStatus trả 403 «datasets.get denied» chừng một phút rồi mới READY -> 403/404 coi là đang xử lý."""
    t0 = time.time()
    while True:
        try:
            st = str(dataset_status(slug).get("status") or "")
        except KaggleError as e:
            if not any(k in str(e) for k in ("403", "404", "denied", "NOT_FOUND", "not found")):
                raise
            st = "đang xử lý"
        if st == "READY":
            return st
        if st in ("FAILED", "DELETED"):
            raise KaggleError(f"dataset {slug}: {st}")
        if time.time() - t0 > timeout:
            raise KaggleError(f"dataset {slug} chưa sẵn sàng sau {timeout:.0f} giây ({st or 'chưa rõ'})")
        sleep(every)


def quota() -> dict:
    """{'gpu_left': giây GPU còn trong tuần, 'refresh': thời điểm làm mới}"""
    d = call("GetAcceleratorQuotaStatistics", {})
    g = d.get("gpuQuota") or {}

    def secs(v):
        try:
            return float(str(v or "0").rstrip("s"))
        except ValueError:
            return 0.0
    return {"gpu_left": secs(g.get("totalTimeAllowed")) - secs(g.get("timeUsed")),
            "gpu_used": secs(g.get("timeUsed")), "refresh": d.get("quotaRefreshTime", "")}


VN_OFFSET = 7 * 3600          # giờ Việt Nam (UTC+7, không đổi giờ mùa hè)
QUOTA_TTL = 300               # web hỏi liên tục: đọc lại Kaggle tối đa mỗi 5 phút
_QCACHE: dict = {}


def quota_cached(max_age: float = QUOTA_TTL) -> dict:
    """quota() nhớ trong max_age giây (max_age=0: đọc mới, vd ngay sau một phiên GPU)."""
    hit = _QCACHE.get("q")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    q = quota()
    _QCACHE["q"] = (time.time(), q)
    return q


def vn_time(iso: str) -> str:
    """'2026-11-22T00:00:00Z' -> '07:00 22/11' (giờ Việt Nam); không đọc được thì trả nguyên."""
    from datetime import datetime, timedelta, timezone
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone(timedelta(seconds=VN_OFFSET))).strftime("%H:%M %d/%m")
    except (TypeError, ValueError):
        return str(iso or "?")


def quota_text(q: dict) -> str:
    """'còn 21,5 / 30 giờ GPU tuần này, làm mới 07:00 22/11'."""
    left, used = max(0.0, q["gpu_left"]) / 3600, q.get("gpu_used", 0.0) / 3600
    total = left + used
    s = f"còn {left:.1f}".replace(".", ",") + (f" / {total:.0f}" if total else "") + " giờ GPU tuần này"
    return s + (f", làm mới {vn_time(q['refresh'])}" if q.get("refresh") else "")


# slug người gọi dùng -> slug thật Kaggle trả về sau khi lưu (Kaggle đặt slug theo TIÊU ĐỀ notebook, xem push)
_ALIAS: dict[str, str] = {}


def title_for(slug: str) -> str:
    """Tiêu đề notebook mà Kaggle đổi ra ĐÚNG slug này ("rmvideo-worker" -> "rmvideo worker")."""
    return slug.replace("-", " ")


def real_slug(slug: str) -> str:
    return _ALIAS.get(slug, slug)


def _ref_slug(d: dict) -> str:
    """Slug trong ref / url SaveKernel trả về ("/code/quetrai/rmvideo-worker" -> "rmvideo-worker"); không đọc được: ""."""
    for key in ("ref", "url"):
        parts = [p for p in re.split(r"[/?#]", str(d.get(key) or "").split("://")[-1]) if p]
        if len(parts) >= 2 and re.fullmatch(r"[a-z0-9][a-z0-9-]*", parts[-1]):
            return parts[-1]
    return ""


def push(slug: str, title: str, text: str, gpu: bool = True, kernel_sources: list | None = None,
         dataset_sources: list | None = None) -> dict:
    """kernel_sources: slug các notebook khác mà notebook này đọc đầu ra (gắn ở /kaggle/input/notebooks/<tên>/<slug>/,
    bản chạy xong gần nhất — đo thật 13/11/2026); dataset_sources: dataset gắn vào /kaggle/input/datasets/... Mục có
    dấu «/» là của người khác (user/slug), không có thì là của chính tài khoản.

    Tiêu đề notebook LUÔN lấy theo slug (`title` chỉ để ghi log): Kaggle đặt lại slug theo tiêu đề. Bản cũ gửi tiêu đề
    video («ĐỒ CHUA GIÒN SẦN SẬT») làm tiêu đề notebook dựng -> Kaggle đổi slug rmvideo-worker thành chua-gi-n-s-n-s-t,
    hỏi trạng thái bằng slug cũ bị 404 «Not found» nên bot bỏ Kaggle, dựng trên Botkeep bằng giọng edge-tts trong khi
    phiên Kaggle vẫn chạy xong (tốn giờ GPU vô ích) — đúng lỗi job 3 ngày 21/11 (kiểm bằng API 21/11/2026)."""
    u = user()
    body = {"slug": f"{u}/{slug}", "newTitle": title_for(slug), "text": text, "language": "python", "kernelType": "script",
            "isPrivate": True, "enableGpu": gpu, "enableInternet": True,
            "datasetDataSources": [d if "/" in d else f"{u}/{d}" for d in dataset_sources or []],
            "kernelDataSources": [k if "/" in k else f"{u}/{k}" for k in kernel_sources or []],
            "competitionDataSources": [], "modelDataSources": [], "categoryIds": []}
    if gpu:
        body["machineShape"] = "NvidiaTeslaT4"
    d: dict = {}
    for k in range(PUSH_TRIES):
        try:
            d = call("SaveKernel", body)
            break
        except KaggleError as e:
            if "409" in str(e) and "title" in str(e).lower():
                raise KaggleError(f"Kaggle báo trùng tên notebook: tài khoản '{body['slug'].split('/')[0]}' không phải "
                                  "chủ token. Sửa KAGGLE_USERNAME trong Cài đặt cho đúng tên ở kaggle.com/<tên>") from None
            if not transient(e) or k == PUSH_TRIES - 1:
                raise
            logging.warning("Kaggle: gửi notebook %s lỗi tạm (%s), thử lại lần %d", slug, str(e)[:120], k + 2)
            _sleep(PUSH_WAIT[min(k, len(PUSH_WAIT) - 1)])
            # kết nối đứt lúc Kaggle đã nhận notebook: phiên mới đang chờ máy / đang chạy -> không gửi lại (gửi lại là
            # thêm một phiên, tốn gấp đôi giờ GPU). Phiên trước của slug này luôn đã xong / bị huỷ khi bắt đầu việc mới.
            try:
                st, _why = status(slug)
            except KaggleError:
                st = ""
            if st.upper() in ("QUEUED", "RUNNING"):
                logging.warning("Kaggle đã nhận notebook %s dù kết nối lỗi (%s): không gửi lại", slug, st)
                return {}
    if d.get("error"):
        if "session count" in str(d["error"]).lower():
            raise SessionLimit(f"Kaggle không nhận notebook: {d['error']}")
        raise KaggleError(f"Kaggle không nhận notebook: {d['error']}")
    got = _ref_slug(d)
    if got and got != slug:
        logging.warning("Kaggle lưu notebook %s dưới slug %s (%s): hỏi trạng thái theo slug Kaggle trả về", slug, got,
                        title[:60])
        _ALIAS[slug] = got
    else:
        _ALIAS.pop(slug, None)
    return d


def delete_kernel(slug: str) -> dict:
    """Xoá notebook (và phiên đang xếp hàng / đang chạy của nó): cách duy nhất qua API công khai để bỏ một phiên đã gửi
    (CancelKernelSession cần mã phiên mà không lệnh nào trả về). Notebook bot tạo lại ở lần gửi sau."""
    d = call("DeleteKernel", {"userName": user(), "kernelSlug": real_slug(slug)})   # kagglesdk 0.1.38: ApiDeleteKernelRequest
    if d.get("errorMessage"):
        raise KaggleError(f"Kaggle không xoá được notebook {slug}: {d['errorMessage']}")
    _ALIAS.pop(slug, None)
    return d


def status(slug: str) -> tuple:
    """(trạng thái, lý do lỗi). QUEUED là giá trị mặc định (0) của enum KernelWorkerStatus nên API bỏ hẳn trường
    status khi phiên đang xếp hàng chờ máy: trước đây đọc ra "" và bot đứng ở «gửi việc» 17-20 phút không báo gì."""
    d = call("GetKernelSessionStatus", {"userName": user(), "kernelSlug": real_slug(slug)})
    return str(d.get("status") or "QUEUED"), d.get("failureMessage") or ""


def outputs(slug: str) -> dict:
    """Danh sách file đầu ra của phiên chạy xong gần nhất (gộp mọi trang; đo thật 19/11: pageSize=200 cắt mất result.json khi
    notebook ghi gần 300 file)."""
    slug = real_slug(slug)
    d = call("ListKernelSessionOutput", {"userName": user(), "kernelSlug": slug, "pageSize": 500})
    files = list(d.get("files") or [])
    tok = d.get("nextPageToken") or ""
    for _ in range(20):
        if not tok:
            break
        more = call("ListKernelSessionOutput", {"userName": user(), "kernelSlug": slug, "pageSize": 500, "pageToken": tok})
        files += list(more.get("files") or [])
        tok = more.get("nextPageToken") or ""
    d["files"] = files
    return d


DL_STALLS = 6              # tải output: số lần đứt liên tiếp KHÔNG tải thêm được byte nào thì bỏ cuộc
DL_TRIES = 60              # trần tổng số lần nối lại (mỗi lần có tiến triển thì không tính vào DL_STALLS)
DL_WAIT = (3.0, 10.0, 20.0, 30.0)
DL_REPORT = 60.0           # giây: báo tiến độ tải (mỗi lần báo = một dòng log) tối đa mỗi chừng này
_EXPIRED = (400, 401, 403, 404, 410)


def _total(r, start: int) -> int | None:
    """Tổng dung lượng file từ Content-Range (206) hoặc Content-Length (200)."""
    cr = r.headers.get("Content-Range") or ""
    if "/" in cr and cr.rsplit("/", 1)[1].strip().isdigit():
        return int(cr.rsplit("/", 1)[1])
    cl = r.headers.get("Content-Length") or ""
    return start + int(cl) if cl.isdigit() else None


def download(url: str, dst: str, max_bytes: int = 1 << 31, refresh=None, on_progress=None) -> int:
    """Link output là link ký sẵn của Google Storage (không cần token). Mạng Botkeep chậm (~80-100 KB/s) và hay đứt
    giữa chừng (job 1 ngày 24/11: video 80 MB đứt ở 7,7 MB sau 96 giây, mất trắng 1 giờ Kaggle): đứt thì nối lại, tải
    TIẾP phần còn thiếu bằng Range; link hết hạn (400/403/404) thì gọi refresh() lấy link mới. on_progress(n, tổng)."""
    tmp = dst + ".part"
    n, total, stalls, last_rep = 0, None, 0, 0.0
    try:
        open(tmp, "wb").close()
        for k in range(DL_TRIES):
            before = n
            try:
                hdr = {"User-Agent": UA}
                if n:
                    hdr["Range"] = f"bytes={n}-"
                with requests.get(url, stream=True, timeout=(30, 120), headers=hdr) as r:
                    if n and r.status_code == 416 and total is not None and n >= total:
                        break
                    r.raise_for_status()
                    zipped = (r.headers.get("Content-Encoding") or "identity").lower() not in ("identity", "")
                    if n and (r.status_code != 206 or zipped):     # máy chủ không cho tải tiếp: tải lại từ đầu
                        n = 0
                    total = _total(r, n) if not zipped else None
                    if total is not None and total > max_bytes:
                        raise KaggleError("file output Kaggle quá lớn")
                    with open(tmp, "r+b") as f:
                        f.seek(n)
                        f.truncate()
                        for chunk in r.iter_content(1 << 16):     # khúc nhỏ: đứt giữa khúc thì mất ít
                            n += len(chunk)
                            if n > max_bytes:
                                raise KaggleError("file output Kaggle quá lớn")
                            f.write(chunk)
                            if on_progress and time.time() - last_rep >= DL_REPORT:
                                last_rep = time.time()
                                on_progress(n, total)
                if total is None or n >= total:
                    break
                raise requests.ConnectionError(f"kết nối đóng sớm ({n}/{total} byte)")
            except KaggleError:
                raise
            except (requests.RequestException, OSError) as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                stalls = 0 if n > before else stalls + 1
                if stalls >= DL_STALLS or k == DL_TRIES - 1:
                    got = f"{n / 1e6:.1f}/{total / 1e6:.1f} MB" if total else f"{n / 1e6:.1f} MB"
                    raise KaggleError(f"tải output Kaggle đứt sau {k + 1} lần thử ({got}): {str(e)[:200]}") from e
                if code in _EXPIRED and refresh is not None:
                    try:
                        url = refresh() or url
                    except Exception as e2:                       # hỏi link mới lỗi: thử lại link cũ
                        logging.warning("Kaggle: không lấy được link tải mới (%s)", str(e2)[:120])
                logging.warning("Kaggle: tải output đứt ở %.1f MB (%s), nối lại", n / 1e6, str(e)[:120])
                _sleep(DL_WAIT[min(stalls - 1, len(DL_WAIT) - 1)] if stalls else 1.0)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return n


WAIT_NET_RETRIES = 6       # ~2 phút mạng hỏng liên tục (every=20s) mới coi là lỗi
NOT_FOUND_GRACE = 180      # vừa SaveKernel xong, GetKernelSessionStatus có thể 404 (phiên chưa tạo): coi là đang chờ
REFRESH = 300              # giây: báo lại "đang chờ máy / đang làm (N phút)" dù trạng thái không đổi
# Hỏi trạng thái thưa dần (25/11): trước đây hỏi mỗi 20 giây suốt cả lúc chờ máy — job 7 chờ 42 phút là ~130 lần hỏi.
# Chờ máy lâu thì vài phút mới có thay đổi; đang chạy thì kết quả cần sớm hơn một chút.
POLL_QUEUED = ((120, 60.0), (600, 120.0))   # (đã chờ quá ngần này giây, hỏi cách ngần này giây) khi QUEUED
POLL_RUNNING = 30.0
STOP_CHECK = 5.0           # giây: lúc nghỉ giữa hai lần hỏi (tới 120 giây) vẫn xem job có bị huỷ / bot tắt không


def poll_every(every: float, state: str, waited: float) -> float:
    """Khoảng nghỉ giữa hai lần hỏi trạng thái. every <= 0 (test) giữ nguyên."""
    if every <= 0:
        return every
    if "QUEUED" in state.upper():
        pause = every
        for after, gap in POLL_QUEUED:
            if waited > after:
                pause = max(every, gap)
        return pause
    return max(every, POLL_RUNNING) if waited > 120 else every
DONE = ("COMPLETE",)
FAILED = ("ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED", "CANCELLED")


def wait(slug: str, timeout: float, every: float = 20, on_status=None, sleep=None, stop=None,
         queue_max: float | None = None, park=None) -> str:
    """Chờ phiên chạy xong. Trả 'COMPLETE' hoặc ném KaggleError (lỗi / quá giờ / chờ máy quá queue_max giây).
    park(giây đã chờ) -> True: thôi chờ nhưng giữ phiên trong hàng chờ (ném Parked; truyện bé, 1.4.6)."""
    if sleep is None:
        def sleep(secs: float) -> None:          # nghỉ theo khúc STOP_CHECK: Hủy / tắt bot được thấy trong vài giây
            done = 0.0
            while done < secs:
                if stop is not None and stop():
                    return
                step = min(secs - done, STOP_CHECK)
                _sleep(step)
                done += step
    t0 = time.time()
    last = ""
    reported = 0.0
    net_fails = 0
    while True:
        if stop is not None and stop():
            raise KaggleError("đã huỷ")
        try:
            st, why = status(slug)
            net_fails = 0
        except KaggleError as e:
            # mạng chập chờn lúc hỏi trạng thái (đo thật 13/11/2026: "Connection reset by peer" sau 4.7 phút) không
            # phải notebook lỗi: hỏi lại tối đa WAIT_NET_RETRIES lần rồi mới bỏ cuộc
            # vừa gửi notebook: Kaggle chưa tạo phiên nên hỏi trạng thái trả 404 «Not found» trong ít giây (job 3 ngày
            # 21/11: 404 ngay 4 giây sau khi gửi, bot bỏ Kaggle, đọc giọng edge-tts trên Botkeep -> «100 dê muối»)
            if any(k in str(e) for k in ("404", "NOT_FOUND")) and time.time() - t0 < NOT_FOUND_GRACE:
                sleep(min(every, 10))
                continue
            net_fails += 1
            if "Không gọi được Kaggle" not in str(e) or net_fails > WAIT_NET_RETRIES:
                raise
            sleep(every)
            continue
        up = st.upper()
        now = time.time()
        if on_status and (st != last or now - reported >= REFRESH):
            on_status(st, now - t0)
            reported = now
        last = st
        if any(x in up for x in DONE):
            return "COMPLETE"
        if any(x in up for x in FAILED):
            raise KaggleError(f"Notebook Kaggle lỗi ({st}): {why[:200]}")
        if time.time() - t0 > timeout:
            if park is not None:                      # phiên vẫn chạy / chờ: giữ chỗ, kids_auto thu kết quả khi xong
                raise Parked(f"Kaggle chưa xong sau {timeout / 60:.0f} phút ({st})")
            raise KaggleError(f"Kaggle chạy quá {timeout / 60:.0f} phút ({st})")
        if queue_max is not None and "QUEUED" in up and time.time() - t0 > queue_max:
            raise KaggleError(f"Kaggle chờ máy quá {queue_max / 60:.0f} phút")
        if park is not None and "QUEUED" in up and park(time.time() - t0):
            raise Parked(f"Kaggle chưa cấp máy sau {(time.time() - t0) / 60:.0f} phút")
        sleep(poll_every(every, up, time.time() - t0))


def log_tail(d: dict, n: int = 1500) -> str:
    """Đuôi log của phiên (log là chuỗi JSON lines của Kaggle) để báo lỗi dễ đọc."""
    raw = d.get("log") or ""
    try:
        lines = json.loads("[" + raw.strip().rstrip(",").lstrip("[").rstrip("]") + "]") if raw else []
        txt = "".join(x.get("data", "") for x in lines if isinstance(x, dict))
    except ValueError:
        txt = raw
    return txt[-n:]
