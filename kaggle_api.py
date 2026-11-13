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


def push(slug: str, title: str, text: str, gpu: bool = True, kernel_sources: list | None = None) -> dict:
    """kernel_sources: slug các notebook khác của mình mà notebook này đọc đầu ra (gắn ở
    /kaggle/input/notebooks/<tên>/<slug>/, bản chạy xong gần nhất — đo thật 13/11/2026)."""
    u = user()
    body = {"slug": f"{u}/{slug}", "newTitle": title, "text": text, "language": "python", "kernelType": "script",
            "isPrivate": True, "enableGpu": gpu, "enableInternet": True, "datasetDataSources": [],
            "kernelDataSources": [f"{u}/{k}" for k in kernel_sources or []], "competitionDataSources": [],
            "modelDataSources": [], "categoryIds": []}
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
    d = call("GetKernelSessionStatus", {"userName": user(), "kernelSlug": slug})
    return str(d.get("status") or ""), d.get("failureMessage") or ""


def cancel(slug: str):
    call("CancelKernelSession", {"userName": user(), "kernelSlug": slug})


def outputs(slug: str) -> dict:
    return call("ListKernelSessionOutput", {"userName": user(), "kernelSlug": slug, "pageSize": 200})


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
DONE = ("COMPLETE",)
FAILED = ("ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED", "CANCELLED")


def wait(slug: str, timeout: float, every: float = 20, on_status=None, sleep=time.sleep, stop=None) -> str:
    """Chờ phiên chạy xong. Trả 'COMPLETE' hoặc ném KaggleError (lỗi / quá giờ)."""
    t0 = time.time()
    last = ""
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
        if on_status and st != last:
            on_status(st, time.time() - t0)
        last = st
        if any(x in up for x in DONE):
            return "COMPLETE"
        if any(x in up for x in FAILED):
            raise KaggleError(f"Notebook Kaggle lỗi ({st}): {why[:200]}")
        if time.time() - t0 > timeout:
            raise KaggleError(f"Kaggle chạy quá {timeout / 60:.0f} phút ({st})")
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
