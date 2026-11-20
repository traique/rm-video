"""(Kaggle) Kiểm ảnh FLUX sau khi vẽ: đếm ĐẦU nhân vật và soát MÀU ÁO bằng OWLv2 (bộ dò vật thể mở, ~600MB, chạy sau FLUX trên T4) rồi so với
số bạn có mặt trong cảnh; thừa đầu = ảnh hai đầu / nhân vật nhân đôi (phản hồi 19/11, lỗi quen của FLUX với khung dọc 9:16) ->
images.generate vẽ lại cảnh đó với seed khác. Không cần key, không cần PUBLIC_URL.

Đo thật 19/11 (16 ảnh từ prompt thật): OWLv2 hay trả hai khung lồng nhau cho cùng một đầu (có tai / chỉ mặt), gọi nhầm mèo
thành thỏ ở điểm thấp, và đánh dấu thân áo ngay dưới đầu là «đầu» (0,35). Gộp khung lồng nhau / chồng nhau, bỏ khung nằm
ngay dưới một đầu khác, ngưỡng 0,3 -> 0/16 ảnh bị báo nhầm. Chỉ báo khi THỪA đầu (thiếu đầu không báo: bạn nhỏ ở xa hay
bị dò sót).

Đo lại 20/11 (video job 1, ảnh vẽ bằng LoRA, 85 khung hình): điểm đầu thấp hơn hẳn (đầu gấu 0,15-0,23) nên 0,3 cho qua cả hai
cảnh nhân đôi. Ngưỡng 0,18; khung bao nhiều đầu bị bỏ thay vì nuốt các đầu bên trong; khung dưới đầu chỉ là thân áo khi thẳng
hàng hoặc mang màu áo; đếm thêm theo loài -> bắt 13/13 khung nhân đôi, 0 báo nhầm."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

MODEL_ID = "google/owlv2-base-patch16-ensemble"
QUERIES = ("a rabbit head", "a cat head", "a bear head", "a squirrel head", "a cartoon animal face", "a t-shirt")
SHIRT_THR = 0.25
# màu áo theo sắc độ (độ, 0-360): đỏ / xanh lá / xanh dương / vàng; cam (lông mèo) và màu khác -> None, không tính
HUES = {"red": ((345, 360), (0, 15)), "yellow": ((46, 68),), "green": ((75, 165),), "blue": ((195, 255),)}
# 20/11: ảnh vẽ bằng LoRA (lông nỉ) OWLv2 chấm đầu thấp hơn hẳn ảnh FLUX trơn — đầu gấu 0,15-0,23, đầu thỏ thứ hai ở cảnh
# Gấu ôm hai Thỏ 0,297 — nên 0,3 (đo 19/11 trên ảnh không LoRA) bỏ sót gần hết. Đo 85 khung hình video job 1 (9 cảnh, 2 cảnh
# nhân đôi): 0,18 + luật gộp mới bắt 13/13 khung nhân đôi, 0 khung báo nhầm; 0,12 bắt đầu báo nhầm.
THRESHOLD = 0.18
SIZE_RATIO = 4.0                  # diện tích đầu to nhất / nhỏ nhất (2 bạn): quá mức là một bạn to gấp đôi bạn kia (đo thật mẫu 31)
RETRIES = 2                       # vẽ lại tối đa chừng này lần cho một cảnh (~35 giây / lần trên T4)
Box = tuple[str, float, list[int]]
_LOADED: dict[str, Any] = {}


def _inter(a: list[int], b: list[int]) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(a: list[int]) -> float:
    return max(1.0, float((a[2] - a[0]) * (a[3] - a[1])))


BODY_ALIGN = 0.25        # khung dưới đầu, tâm lệch ≤ 25% bề ngang của nó so với tâm đầu: thân của chính bạn đó


def _is_body(box: list[int], head: list[int], img: Any = None) -> bool:
    """Khung «đầu» nằm ngay DƯỚI đầu `head` (chồng ngang ≥ 50%, đỉnh thấp hơn giữa đầu, không to hơn 1,5 lần) là THÂN của
    chính bạn đó (đo 19/11: OWLv2 đánh dấu thân áo là đầu, 0,35) khi thẳng hàng dưới đầu hoặc vùng giữa mang màu áo. Đầu thứ
    hai lệch sang bên (job 1 ngày 20/11, cảnh 7: Gấu ôm HAI Thỏ, đầu thỏ thứ hai ló dưới cằm thỏ thứ nhất, tâm lệch 54%) là
    bản sao: giữ lại để đếm (luật cũ gộp mất nó)."""
    x_over = max(0, min(box[2], head[2]) - max(box[0], head[0])) / max(1, box[2] - box[0])
    if not (x_over >= 0.5 and box[1] >= (head[1] + head[3]) / 2 and _area(box) <= 1.5 * _area(head)):
        return False
    off = abs((box[0] + box[2]) / 2 - (head[0] + head[2]) / 2) / max(1, box[2] - box[0])
    if off <= BODY_ALIGN:
        return True
    return img is not None and box_color(img, box) is not None


def _inside(a: list[int], b: list[int]) -> bool:
    return _inter(a, b) / _area(a) >= 0.6


def _groups(cands: list[Box]) -> set[int]:
    """Chỉ số các khung «nhóm»: bao trọn ≥ 2 khung khác tách rời nhau (job 1 cảnh 7: khung «a rabbit head» 0,32 bao cả hai
    thỏ, điểm cao nhất nên hai đầu thật bị gộp vào nó -> đếm 1). Bỏ khung nhóm thì các đầu bên trong vẫn còn để đếm; đầu +
    thân của cùng một bạn bên trong thì thân bị `_is_body` bỏ sau, kết quả không đổi."""
    out = set()
    for i, (_q, _s, big) in enumerate(cands):
        inner = [c[2] for j, c in enumerate(cands) if j != i and _area(c[2]) < _area(big) and _inside(c[2], big)]
        if any(not (_inside(a, b) or _inside(b, a)) for k, a in enumerate(inner) for b in inner[k + 1:]):
            out.add(i)
    return out


def merge_heads(boxes: list[Box], thr: float = THRESHOLD, img: Any = None) -> list[Box]:
    """Khung «đầu» riêng biệt (không phân loài): bỏ khung dưới ngưỡng, khung nhóm bao nhiều đầu (`_groups`), khung lồng /
    chồng khung điểm cao hơn (IoU ≥ 0,5 hay ≥ 60% diện tích nằm trong nhau), và khung là thân của một đầu phía trên
    (`_is_body`; img: ảnh để soát màu áo)."""
    cands = sorted([b for b in boxes if ("head" in b[0] or "face" in b[0]) and b[1] >= thr], key=lambda b: -b[1])
    drop = _groups(cands)
    cands = [c for i, c in enumerate(cands) if i not in drop]
    kept: list[Box] = []
    for q, s, box in cands:
        dup = False
        for _q2, _s2, k in kept:
            inter = _inter(box, k)
            if inter / _area(box) >= 0.6 or inter / _area(k) >= 0.6 or inter / (_area(box) + _area(k) - inter) >= 0.5:
                dup = True
                break
            if _is_body(box, k, img):
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


def _under_head(box: list[int], heads: list[Box]) -> bool:
    """Khung áo nằm dưới một đầu (đỉnh áo không cao hơn giữa đầu, chồng ngang ≥ 30%): bỏ khung bụi hoa / cỏ vàng."""
    for _q, _s, h in heads:
        x_over = max(0, min(box[2], h[2]) - max(box[0], h[0])) / max(1, box[2] - box[0])
        if x_over >= 0.3 and box[1] >= (h[1] + h[3]) / 2 - 0.2 * (h[3] - h[1]):
            return True
    return False


def shirt_colors(img: Any, boxes: list[Box], thr: float = SHIRT_THR, heads: list[Box] | None = None) -> list[tuple[str, float, list[int]]]:
    """Áo thấy trong ảnh: (màu, điểm, khung) — khung «a t-shirt» ≥ thr, nằm dưới một đầu (nếu có danh sách đầu), bỏ khung
    chồng nhau, màu xác định được."""
    out: list[tuple[str, float, list[int]]] = []
    kept: list[list[int]] = []
    for q, sc, box in sorted(boxes, key=lambda b: -b[1]):
        if q != "a t-shirt" or sc < thr:
            continue
        if heads and not _under_head(box, heads):
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


def sizes_ok(heads: list[Box], expected: int, ratio: float = SIZE_RATIO) -> bool:
    """Cảnh 2 bạn: hai đầu phải cỡ nhau (tỉ lệ diện tích ≤ ratio); 1 bạn hay dò chưa đủ 2 đầu thì không xét."""
    if expected < 2 or len(heads) < 2:
        return True
    areas = sorted(_area(b) for _q, _s, b in heads)[-2:]
    return areas[1] / max(1.0, areas[0]) <= ratio


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


def detect(img: Any, loaded: tuple[Any, Any, str] | None = None, min_score: float = 0.1,
           extra: Sequence[str] = ()) -> list[Box]:
    """Mọi khung đầu / mặt OWLv2 thấy (điểm ≥ min_score), toạ độ pixel của ảnh gốc (OWLv2 đệm ảnh thành vuông theo cạnh dài).
    extra: câu hỏi thêm cho lượt dò này («a toy box»: đồ vật chính của truyện)."""
    import torch
    queries = list(QUERIES) + [q for q in extra if q]
    proc, model, device = loaded or load()
    inputs = proc(text=[queries], images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model(**inputs)
    side = max(img.size)
    r = proc.post_process_object_detection(out, threshold=min_score, target_sizes=torch.tensor([[side, side]]).to(device))[0]
    return [(queries[int(lab)], float(sc), [int(round(v)) for v in box.tolist()])
            for box, sc, lab in zip(r["boxes"], r["scores"], r["labels"])]


def shirt_owner(shirt: list[int], heads: list[Box]) -> str | None:
    """Loài của đầu ngay PHÍA TRÊN áo (chồng ngang ≥ 30%, tâm đầu không thấp hơn đỉnh áo quá 20%): «rabbit», «bear»...;
    không có / đầu không rõ loài («a cartoon animal face») thì None."""
    best: tuple[float, str] | None = None
    for q, _s, h in heads:
        x_over = max(0, min(shirt[2], h[2]) - max(shirt[0], h[0])) / max(1, shirt[2] - shirt[0])
        if x_over < 0.3 or (h[1] + h[3]) / 2 > shirt[1] + 0.2 * (shirt[3] - shirt[1]):
            continue
        gap = abs(shirt[1] - h[3])
        if best is None or gap < best[0]:
            best = (gap, q)
    m = re.match(r"a (\w+) head$", best[1]) if best else None
    return m.group(1) if m else None


def owners_ok(heads: list[Box], shirts: list[tuple[str, float, list[int]]], owners: dict[str, str]) -> bool:
    """Áo dưới đầu thỏ phải màu áo của Thỏ... (đo thật mẫu 32: Thỏ mặc áo xanh của Gấu, Gấu không áo — một áo duy nhất
    nên shirts_ok không bắt)."""
    for c, _s, b in shirts:
        sp = shirt_owner(b, heads)
        if sp and owners.get(sp) and owners[sp] != c:
            return False
    return True


CONTACT_GAP = 0.02      # hai áo tách rời nhau theo chiều ngang quá 2% bề ngang ảnh -> chưa ôm / chạm nhau


def _overlap(a: list[int], b: list[int]) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def contact_ok(shirts: list[tuple[str, float, list[int]]], heads: list[Box], width: int) -> bool:
    """Cảnh ôm / dắt tay / đưa đồ (từ dạy có «contact»): hai bạn phải chạm nhau. Hai đầu đè lên nhau, hoặc chỉ thấy một áo
    (áo kia bị che khi ôm) là có; thấy hai áo mà cách nhau theo chiều ngang quá CONTACT_GAP bề ngang ảnh là đứng tách rời
    (mẫu 32 cảnh 7: «Gấu ôm Thỏ» mà Gấu bưng bánh, Thỏ đứng cạnh)."""
    if len(heads) == 2 and _overlap(heads[0][2], heads[1][2]):
        return True
    if len(shirts) < 2:
        return True
    two = sorted(sorted(shirts, key=lambda s: -(s[2][2] - s[2][0]) * (s[2][3] - s[2][1]))[:2], key=lambda s: s[2][0])
    gap = two[1][2][0] - two[0][2][2]
    return gap <= CONTACT_GAP * max(1, width)


SPECIES_THR = 0.25      # hai đầu cùng loài đều chắc chừng này mà cảnh chỉ có một bạn loài đó -> nhân đôi


def species_twins(heads: list[Box], species: Sequence[str], thr: float = SPECIES_THR) -> tuple[str, int, int] | None:
    """(loài, số đầu thấy, số bạn loài đó trong cảnh) khi thấy NHIỀU đầu riêng biệt của một loài hơn số bạn loài đó có mặt,
    mỗi đầu ≥ thr (job 1 ngày 20/11, cảnh 7: Gấu ôm hai Thỏ, đầu gấu chỉ 0,19 nên đếm tổng không đủ để bắt). species: loài
    các bạn trong cảnh («rabbit», «bear»…); rỗng thì không xét."""
    if not species:
        return None
    for sp in set(species):
        n = sum(1 for q, s, _b in heads if q == f"a {sp} head" and s >= thr)
        if n > species.count(sp):
            return sp, n, species.count(sp)
    return None


PROP_THR = 0.15         # đồ vật chính (câu hỏi mở «a toy box»): điểm thấp hơn đầu nhân vật vẫn tính là có


def check(img: Any, expected: int, loaded: tuple[Any, Any, str] | None = None, thr: float = THRESHOLD,
          colors: list[str] | None = None, contact: bool = False, prop: str = "") -> tuple[bool, int, list[Box]]:
    """(đạt?, số đầu thấy, các khung): đạt khi số đầu riêng biệt ≤ số bạn có mặt (ít nhất 1), màu áo không sai (colors:
    màu áo các bạn trong cảnh, «red» hoặc «rabbit:red» — có loài thì soát cả áo nào dưới đầu nào), hai bạn không lệch cỡ,
    (contact) hai bạn chạm nhau và (prop) có đồ vật chính «prop» trong hình (mẫu 33: «toy box» ghi trong prompt mà 3/5 cảnh
    không vẽ). Khung trả thêm các áo thấy ("shirt:red", điểm, khung), đồ vật ("prop:toy box") và lý do hỏng ("why:...")."""
    pairs = [tuple(c.split(":", 1)) if ":" in c else ("", c) for c in colors or []]
    plain = [c for _sp, c in pairs]
    owners = {sp: c for sp, c in pairs if sp and c}
    pq = f"a {prop.strip()}" if prop and prop.strip() else ""
    boxes = detect(img, loaded, extra=[pq] if pq else ())
    props = [b for b in boxes if pq and b[0] == pq and b[1] >= PROP_THR]
    heads = merge_heads(boxes, thr, img)
    found = shirt_colors(img, boxes, heads=merge_heads(boxes, 0.2, img)) if (colors or contact) else []
    why: list[str] = []
    if len(heads) > max(1, int(expected)):
        why.append(f"thấy {len(heads)} đầu nhân vật, cảnh chỉ có {expected} bạn (ảnh hai đầu / nhân vật nhân đôi)")
    else:
        twin = species_twins(heads, [sp for sp, _c in pairs if sp])
        if twin:
            why.append(f"thấy {twin[1]} đầu {twin[0]}, cảnh chỉ có {twin[2]} (nhân vật nhân đôi)")
    if not shirts_ok(found, plain):
        why.append(f"áo thấy {[c for c, _s, _b in found]}, các bạn mặc {plain} (áo đổi màu)")
    elif owners and not owners_ok(heads, found, owners):
        why.append(f"áo mặc nhầm chủ ({', '.join(f'{c} dưới đầu {shirt_owner(b, heads)}' for c, _s, b in found)}; "
                   f"đúng: {owners})")
    if not sizes_ok(heads, int(expected)):
        why.append("hai bạn to nhỏ lệch nhau")
    if contact and int(expected) >= 2 and not contact_ok(found, heads, int(getattr(img, "width", 0) or 0)):
        why.append("hai bạn đứng tách rời, chưa ôm / chạm nhau")
    if pq and not props:
        why.append(f"không thấy đồ vật chính «{prop.strip()}» trong hình")
    return (not why, len(heads), heads + [("shirt:" + c, sc, b) for c, sc, b in found]
            + [("prop:" + prop.strip(), sc, b) for _q, sc, b in props]
            + [("why:" + w, 0.0, [0, 0, 1, 1]) for w in why])
