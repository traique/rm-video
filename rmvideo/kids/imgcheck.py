"""(Kaggle) Kiểm ảnh FLUX sau khi vẽ: đếm ĐẦU nhân vật bằng OWLv2 (bộ dò vật thể mở, ~600MB, chạy sau FLUX trên T4) rồi so với
số bạn có mặt trong cảnh; thừa đầu = ảnh hai đầu / nhân vật nhân đôi (phản hồi 19/11, lỗi quen của FLUX với khung dọc 9:16) ->
images.generate vẽ lại cảnh đó với seed khác. Không cần key, không cần PUBLIC_URL.

Đo thật 19/11 (16 ảnh từ prompt thật): OWLv2 hay trả hai khung lồng nhau cho cùng một đầu (có tai / chỉ mặt), gọi nhầm mèo
thành thỏ ở điểm thấp, và đánh dấu thân áo ngay dưới đầu là «đầu» (0,35). Gộp khung lồng nhau / chồng nhau, bỏ khung nằm
ngay dưới một đầu khác, ngưỡng 0,3 -> 0/16 ảnh bị báo nhầm. Chỉ báo khi THỪA đầu (thiếu đầu không báo: bạn nhỏ ở xa hay
bị dò sót)."""
from __future__ import annotations

from typing import Any

MODEL_ID = "google/owlv2-base-patch16-ensemble"
QUERIES = ("a rabbit head", "a cat head", "a bear head", "a squirrel head", "a cartoon animal face")
THRESHOLD = 0.3
RETRIES = 2                       # vẽ lại tối đa chừng này lần cho một cảnh (~35 giây / lần trên T4)
Box = tuple[str, float, list[int]]
_LOADED: dict[str, Any] = {}


def _inter(a: list[int], b: list[int]) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(a: list[int]) -> float:
    return max(1.0, float((a[2] - a[0]) * (a[3] - a[1])))


def merge_heads(boxes: list[Box], thr: float = THRESHOLD) -> list[Box]:
    """Khung «đầu» riêng biệt (không phân loài): bỏ khung dưới ngưỡng, khung lồng / chồng khung điểm cao hơn (IoU ≥ 0,5 hay
    ≥ 60% diện tích nằm trong nhau), và khung nằm ngay DƯỚI một đầu khác, chồng ngang ≥ 50% (thân áo bị nhận là đầu)."""
    cands = sorted([b for b in boxes if ("head" in b[0] or "face" in b[0]) and b[1] >= thr], key=lambda b: -b[1])
    kept: list[Box] = []
    for q, s, box in cands:
        dup = False
        for _q2, _s2, k in kept:
            inter = _inter(box, k)
            if inter / _area(box) >= 0.6 or inter / _area(k) >= 0.6 or inter / (_area(box) + _area(k) - inter) >= 0.5:
                dup = True
                break
            x_over = max(0, min(box[2], k[2]) - max(box[0], k[0])) / max(1, box[2] - box[0])
            if x_over >= 0.5 and box[1] >= (k[1] + k[3]) / 2 and _area(box) <= 1.5 * _area(k):
                dup = True
                break
        if not dup:
            kept.append((q, s, box))
    return kept


def load(device: str = "cuda") -> tuple[Any, Any, str]:
    """Nạp OWLv2 một lần (processor, model, device)."""
    if "m" not in _LOADED:
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        proc = Owlv2Processor.from_pretrained(MODEL_ID)
        model = Owlv2ForObjectDetection.from_pretrained(MODEL_ID).to(device).eval()
        _LOADED["m"] = (proc, model, device)
    return _LOADED["m"]


def unload() -> None:
    _LOADED.clear()


def detect(img: Any, loaded: tuple[Any, Any, str] | None = None, min_score: float = 0.1) -> list[Box]:
    """Mọi khung đầu / mặt OWLv2 thấy (điểm ≥ min_score), toạ độ pixel của ảnh gốc (OWLv2 đệm ảnh thành vuông theo cạnh dài)."""
    import torch
    proc, model, device = loaded or load()
    inputs = proc(text=[list(QUERIES)], images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model(**inputs)
    side = max(img.size)
    r = proc.post_process_object_detection(out, threshold=min_score, target_sizes=torch.tensor([[side, side]]).to(device))[0]
    return [(QUERIES[int(lab)], float(sc), [int(round(v)) for v in box.tolist()])
            for box, sc, lab in zip(r["boxes"], r["scores"], r["labels"])]


def check(img: Any, expected: int, loaded: tuple[Any, Any, str] | None = None, thr: float = THRESHOLD) -> tuple[bool, int, list[Box]]:
    """(đạt?, số đầu thấy, các khung): đạt khi số đầu riêng biệt ≤ số bạn có mặt (ít nhất 1)."""
    heads = merge_heads(detect(img, loaded), thr)
    return len(heads) <= max(1, int(expected)), len(heads), heads
