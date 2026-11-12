"""Gọi API Kaggle bằng token kiểu mới (Settings -> API Tokens), không cần cài gói `kaggle`.

API của thư viện chính thức (kagglesdk): POST https://api.kaggle.com/v1/<service>/<method>, body JSON camelCase,
`Authorization: Bearer <token>`. Kaggle trả 404 cho MỌI request không xưng là client của họ (User-Agent phải bắt đầu
bằng `kaggle-api/`) — đo thật 12/11/2026."""
import json
import os
import time

import requests

import config

API = os.environ.get("KAGGLE_API_URL", "https://api.kaggle.com/v1")
UA = "kaggle-api/v1.8.0 rm-video"
KERNELS = "kernels.KernelsApiService"


class KaggleError(RuntimeError):
    pass


def creds() -> tuple:
    return (config.KAGGLE_USERNAME or "").strip(), (config.KAGGLE_API_TOKEN or "").strip()


def configured() -> bool:
    user, tok = creds()
    return bool(user and tok)


def call(method: str, body: dict, service: str = KERNELS, timeout: float = 120) -> dict:
    _, tok = creds()
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


def push(slug: str, title: str, text: str, gpu: bool = True) -> dict:
    user, _ = creds()
    body = {"slug": f"{user}/{slug}", "newTitle": title, "text": text, "language": "python", "kernelType": "script",
            "isPrivate": True, "enableGpu": gpu, "enableInternet": True, "datasetDataSources": [],
            "kernelDataSources": [], "competitionDataSources": [], "modelDataSources": [], "categoryIds": []}
    if gpu:
        body["machineShape"] = "NvidiaTeslaT4"
    d = call("SaveKernel", body)
    if d.get("error"):
        raise KaggleError(f"Kaggle không nhận notebook: {d['error']}")
    return d


def status(slug: str) -> tuple:
    user, _ = creds()
    d = call("GetKernelSessionStatus", {"userName": user, "kernelSlug": slug})
    return str(d.get("status") or ""), d.get("failureMessage") or ""


def outputs(slug: str) -> dict:
    user, _ = creds()
    return call("ListKernelSessionOutput", {"userName": user, "kernelSlug": slug, "pageSize": 200})


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


DONE = ("COMPLETE",)
FAILED = ("ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED", "CANCELLED")


def wait(slug: str, timeout: float, every: float = 20, on_status=None, sleep=time.sleep) -> str:
    """Chờ phiên chạy xong. Trả 'COMPLETE' hoặc ném KaggleError (lỗi / quá giờ)."""
    t0 = time.time()
    last = ""
    while True:
        st, why = status(slug)
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
