"""Gọi API Kaggle bằng token kiểu mới (Settings -> API Tokens), không cần cài gói `kaggle`.

API của thư viện chính thức (kagglesdk): POST https://api.kaggle.com/v1/<service>/<method>, body JSON camelCase,
`Authorization: Bearer <token>`. Kaggle trả 404 cho MỌI request không xưng là client của họ (User-Agent phải bắt đầu
bằng `kaggle-api/`) — đo thật 12/11/2026."""
import json
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


def push(slug: str, title: str, text: str, gpu: bool = True, kernel_sources: list | None = None,
         dataset_sources: list | None = None) -> dict:
    """kernel_sources: slug các notebook khác mà notebook này đọc đầu ra (gắn ở /kaggle/input/notebooks/<tên>/<slug>/,
    bản chạy xong gần nhất — đo thật 13/11/2026); dataset_sources: dataset gắn vào /kaggle/input/datasets/... Mục có
    dấu «/» là của người khác (user/slug), không có thì là của chính tài khoản."""
    u = user()
    body = {"slug": f"{u}/{slug}", "newTitle": title, "text": text, "language": "python", "kernelType": "script",
            "isPrivate": True, "enableGpu": gpu, "enableInternet": True,
            "datasetDataSources": [d if "/" in d else f"{u}/{d}" for d in dataset_sources or []],
            "kernelDataSources": [k if "/" in k else f"{u}/{k}" for k in kernel_sources or []],
            "competitionDataSources": [], "modelDataSources": [], "categoryIds": []}
    if gpu:
        body["machineShape"] = "NvidiaTeslaT4"
    try:
        d = call("SaveKernel", body)
    except KaggleError as e:
        if "409" in str(e) and "title" in str(e).lower():
            raise KaggleError(f"Kaggle báo trùng tên notebook: tài khoản '{body['slug'].split('/')[0]}' không phải chủ "
                              "token. Sửa KAGGLE_USERNAME trong Cài đặt cho đúng tên ở kaggle.com/<tên>") from None
        raise
    if d.get("error"):
        raise KaggleError(f"Kaggle không nhận notebook: {d['error']}")
    return d


def status(slug: str) -> tuple:
    """(trạng thái, lý do lỗi). QUEUED là giá trị mặc định (0) của enum KernelWorkerStatus nên API bỏ hẳn trường
    status khi phiên đang xếp hàng chờ máy: trước đây đọc ra "" và bot đứng ở «gửi việc» 17-20 phút không báo gì."""
    d = call("GetKernelSessionStatus", {"userName": user(), "kernelSlug": slug})
    return str(d.get("status") or "QUEUED"), d.get("failureMessage") or ""


def outputs(slug: str) -> dict:
    """Danh sách file đầu ra của phiên chạy xong gần nhất (gộp mọi trang; đo thật 19/11: pageSize=200 cắt mất result.json khi
    notebook ghi gần 300 file)."""
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


def download(url: str, dst: str, max_bytes: int = 1 << 31):
    """Link output là link ký sẵn của Google Storage (không cần token)."""
    tmp = dst + ".part"
    n = 0
    try:
        with requests.get(url, stream=True, timeout=600, headers={"User-Agent": UA}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    n += len(chunk)
                    if n > max_bytes:
                        raise KaggleError("file output Kaggle quá lớn")
                    f.write(chunk)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return n


WAIT_NET_RETRIES = 6       # ~2 phút mạng hỏng liên tục (every=20s) mới coi là lỗi
REFRESH = 300              # giây: báo lại "đang chờ máy / đang làm (N phút)" dù trạng thái không đổi
DONE = ("COMPLETE",)
FAILED = ("ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED", "CANCELLED")


def wait(slug: str, timeout: float, every: float = 20, on_status=None, sleep=time.sleep, stop=None,
         queue_max: float | None = None) -> str:
    """Chờ phiên chạy xong. Trả 'COMPLETE' hoặc ném KaggleError (lỗi / quá giờ / chờ máy quá queue_max giây)."""
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
            raise KaggleError(f"Kaggle chạy quá {timeout / 60:.0f} phút ({st})")
        if queue_max is not None and "QUEUED" in up and time.time() - t0 > queue_max:
            raise KaggleError(f"Kaggle chờ máy quá {queue_max / 60:.0f} phút")
        sleep(every)


def log_tail(d: dict, n: int = 1500) -> str:
    """Đuôi log của phiên (log là chuỗi JSON lines của Kaggle) để báo lỗi dễ đọc."""
    raw = d.get("log") or ""
    try:
        lines = json.loads("[" + raw.strip().rstrip(",").lstrip("[").rstrip("]") + "]") if raw else []
        txt = "".join(x.get("data", "") for x in lines if isinstance(x, dict))
    except ValueError:
        txt = raw
    return txt[-n:]
