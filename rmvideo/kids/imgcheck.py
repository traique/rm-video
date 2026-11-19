"""(Kaggle) Kiểm ảnh FLUX sau khi vẽ: đếm ĐẦU nhân vật và soát MÀU ÁO bằng OWLv2 (bộ dò vật thể mở, ~600MB, chạy sau FLUX trên T4) rồi so với
số bạn có mặt trong cảnh; thừa đầu = ảnh hai đầu / nhân vật nhân đôi (phản hồi 19/11, lỗi quen của FLUX với khung dọc 9:16) ->
images.generate vẽ lại cảnh đó với seed khác. Không cần key, không cần PUBLIC_URL.

Đo thật 19/11 (16 ảnh từ prompt thật): OWLv2 hay trả hai khung lồng nhau cho cùng một đầu (có tai / chỉ mặt), gọi nhầm mèo
thành thỏ ở điểm thấp, và đánh dấu thân áo ngay dưới đầu là «đầu» (0,35). Gộp khung lồng nhau / chồng nhau, bỏ khung nằm
ngay dưới một đầu khác, ngưỡng 0,3 -> 0/16 ảnh bị báo nhầm. Chỉ báo khi THỪA đầu (thiếu đầu không báo: bạn nhỏ ở xa hay
bị dò sót)."""
from __future__ import annotations

from typing import Any

MODEL_ID = "google/owlv2-base-patch16-ensemble"
QUERIES = ("a rabbit head", "a cat head", "a bear head", "a squirrel head", "a cartoon animal face", "a t-shirt")
SHIRT_THR = 0.25
# màu áo theo sắc độ (độ, 0-360): đỏ / xanh lá / xanh dương / vàng; cam (lông mèo) và màu khác -> None, không tính
HUES = {"red": ((345, 360), (0, 15)), "yellow": ((42, 70),), "green": ((75, 165),), "blue": ((195, 255),)}
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


def hue_name(h: float) -> str | None:
    for name, ranges in HUES.items():
        if any(a <= h < b for a, b in ranges):
            return name
    return None


def box_color(img: Any, box: list[int]) -> str | None:
    """Màu áo trong khung (vùng giữa 50% của khung, chỉ điểm ảnh có độ bão hoà): red / green / blue / yellow / None."""
    import colorsys
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    crop = img.convert("RGB").crop((x0 + w // 4, y0 + h // 4, x1 - w // 4, y1 - h // 4))
    if crop.size[0] < 2 or crop.size[1] < 2:
        return None
    crop = crop.resize((24, 24))
    votes: dict[str, int] = {}
    raw = crop.tobytes()
    for r, g, b in (raw[i:i + 3] for i in range(0, len(raw), 3)):
        hh, ll, ss = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if ss < 0.35 or ll < 0.15 or ll > 0.9:
            continue
        name = hue_name(hh * 360)
        if name:
            votes[name] = votes.get(name, 0) + 1
    if not votes:
        return None
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] >= 0.4 * (24 * 24) else None      # ít nhất 40% điểm ảnh vùng giữa cùng một màu


def shirt_colors(img: Any, boxes: list[Box], thr: float = SHIRT_THR) -> list[tuple[str, float, list[int]]]:
    """Áo thấy trong ảnh: (màu, điểm, khung) — khung «a t-shirt» ≥ thr, bỏ khung chồng nhau, màu xác định được."""
    out: list[tuple[str, float, list[int]]] = []
    kept: list[list[int]] = []
    for q, sc, box in sorted(boxes, key=lambda b: -b[1]):
        if q != "a t-shirt" or sc < thr:
            continue
        if any(_inter(box, k) / min(_area(box), _area(k)) >= 0.6 for k in kept):
            continue
        kept.append(box)
        c = box_color(img, box)
        if c:
            out.append((c, sc, box))
    return out


def shirts_ok(found: list[tuple[str, float, list[int]]], expected: list[str]) -> bool:
    """Đạt khi không có áo màu NGOÀI bộ màu của các bạn trong cảnh, và không có hai áo cùng màu khi hai bạn khác màu
    (đo thật mẫu 29: Mèo Cam mặc áo đỏ của Thỏ -> hai áo đỏ). Thiếu áo (dò sót) không tính là lỗi."""
    colors = [c for c, _s, _b in found]
    exp = [c for c in expected if c]
    if not exp:
        return True
    if any(c not in exp for c in colors):
        return False
    if len(exp) >= 2 and len(set(exp)) == len(exp):
        return all(colors.count(c) <= 1 for c in colors)
    return True


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


def check(img: Any, expected: int, loaded: tuple[Any, Any, str] | None = None, thr: float = THRESHOLD,
          colors: list[str] | None = None) -> tuple[bool, int, list[Box]]:
    """(đạt?, số đầu thấy, các khung): đạt khi số đầu riêng biệt ≤ số bạn có mặt (ít nhất 1) và màu áo không sai
    (colors: màu áo của các bạn trong cảnh, xem shirts_ok). Khung trả thêm các áo thấy dưới dạng ("shirt:red", điểm, khung)."""
    boxes = detect(img, loaded)
    heads = merge_heads(boxes, thr)
    found = shirt_colors(img, boxes) if colors else []
    ok = len(heads) <= max(1, int(expected)) and shirts_ok(found, colors or [])
    return ok, len(heads), heads + [("shirt:" + c, sc, b) for c, sc, b in found]
