"""Cảnh người gốc nói vào máy (1.1.2): lời Việt chạy trên môi người khác trông như lồng tiếng giả. Dò mặt bằng YuNet
(OpenCV Zoo, MIT, ~230KB ONNX, chạy bằng onnxruntime có sẵn — không cần OpenCV) trên ẢNH THU NHỎ bước phân tích đã trích
(scenes.analyzed: 2 ảnh/giây, hay khung chính ở mức nhẹ; không giải mã thêm). Quãng người gốc đang nói (lời bóc được) mà
mặt to ở phần trên khung -> khúc của kế hoạch dựng trong quãng đó đẩy cận NEO ĐÁY (edl.Piece.ay = 1): khung ra cắt mất
miệng, giữ tay / món. Mặt nằm giữa / thấp (cần cận quá ZOOM_MAX mới cắt được miệng) thì để nguyên, chỉ ghi chú.
Không làm khớp môi (làm giả lời người thật).

TẮT mặc định (TALK_ZOOM=0). Đo 20/11 trên 2 video nấu ăn Wikimedia Commons: dò mặt đúng (46-60 ms/ảnh 384px trên CPU; bỏ
được hai bức tranh chân dung treo tường nhờ lọc mặt tĩnh), nhưng với hai người dẫn đứng nói thì cận neo đáy chỉ còn thân
người cụt đầu; chuyển động phía dưới miệng không tách được «tay đang làm món» (0,04-0,13) với «đứng nói, vung tay» (0,20)."""
from __future__ import annotations

import hashlib
import logging
import os
import urllib.request
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_URLS = ("https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + MODEL_NAME,
              "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/" + MODEL_NAME)
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"   # 232.589 byte, tải 20/11/2026
SIDE = 640                 # YuNet 2023mar: đầu vào cố định 640x640
SCORE = 0.6
NMS_IOU = 0.3
FACE_MIN = 0.10            # mặt cao ≥ 10% khung mới tính là «nói vào máy» (người đứng xa, mặt nhỏ: lồng tiếng không lộ)
MOUTH_MAX = 0.42           # miệng thấp hơn chừng này (tỉ lệ chiều cao): đẩy cận bao nhiêu cũng không cắt được miệng
ZOOM_MAX = 1.6             # cận tối đa (nguồn 720p phóng thêm 1,6 lần còn chấp nhận được)
ZOOM_MIN = 1.15
GAP = 1.5                  # hai ảnh có mặt cách nhau ≤ chừng này giây: cùng một quãng
MIN_LEN = 2.0              # quãng ngắn hơn chừng này: bỏ (chớp qua)
OVERLAP = 0.5              # khúc nằm ≥ 50% trong quãng nói vào máy thì đẩy cận neo đáy
_SESSION: dict[str, Any] = {}


def _model_path() -> str:
    os.makedirs(_DIR, exist_ok=True)
    p = os.path.join(_DIR, MODEL_NAME)
    if not os.path.exists(p):
        last: Exception | None = None
        for url in MODEL_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "rm-video"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read(2 << 20)
                if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
                    raise RuntimeError("SHA-256 không khớp")
                with open(p + ".part", "wb") as f:
                    f.write(data)
                os.replace(p + ".part", p)
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(f"không tải được {MODEL_NAME}: {last}")
    return p


def _session() -> Any:
    if "s" not in _SESSION:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1                         # Botkeep 1,5 vCPU: một luồng, ảnh nhỏ
        _SESSION["s"] = ort.InferenceSession(_model_path(), opts, providers=["CPUExecutionProvider"])
    return _SESSION["s"]


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float = NMS_IOU) -> list[int]:
    order = list(np.argsort(-scores))
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        rest = []
        for j in order:
            x0, y0 = max(boxes[i, 0], boxes[j, 0]), max(boxes[i, 1], boxes[j, 1])
            x1, y1 = min(boxes[i, 2], boxes[j, 2]), min(boxes[i, 3], boxes[j, 3])
            inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            b = (boxes[j, 2] - boxes[j, 0]) * (boxes[j, 3] - boxes[j, 1])
            if inter / max(1e-6, a + b - inter) < thr:
                rest.append(j)
        order = rest
    return keep


def decode(out: dict[str, np.ndarray], score: float = SCORE) -> list[tuple[float, ...]]:
    """Đầu ra YuNet -> [(x0, y0, x1, y1, điểm, y miệng)] pixel trên ảnh 640x640 (miệng = trung bình hai khoé)."""
    boxes, scores, mouths = [], [], []
    for s in (8, 16, 32):
        n = SIDE // s
        cls, obj = out[f"cls_{s}"][0, :, 0], out[f"obj_{s}"][0, :, 0]
        sc = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        idx = np.where(sc >= score)[0]
        if not len(idx):
            continue
        bb, kp = out[f"bbox_{s}"][0, idx], out[f"kps_{s}"][0, idx]
        col, row = (idx % n).astype(np.float32), (idx // n).astype(np.float32)
        cx, cy = (col + bb[:, 0]) * s, (row + bb[:, 1]) * s
        w, h = np.exp(bb[:, 2]) * s, np.exp(bb[:, 3]) * s
        boxes.append(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1))
        scores.append(sc[idx])
        mouths.append(((row + kp[:, 7]) * s + (row + kp[:, 9]) * s) / 2)     # khoé miệng phải / trái (điểm 4, 5)
    if not boxes:
        return []
    B, S, M = np.concatenate(boxes), np.concatenate(scores), np.concatenate(mouths)
    return [(*map(float, B[i]), float(S[i]), float(M[i])) for i in _nms(B, S)]


def faces(img: np.ndarray) -> list[tuple[float, ...]]:
    """Mặt trong ảnh RGB uint8 (h, w, 3): [(x0, y0, x1, y1, điểm, y miệng)] theo TỈ LỆ 0..1 của ảnh."""
    h, w = img.shape[:2]
    k = SIDE / max(h, w)
    nh, nw = max(1, int(round(h * k))), max(1, int(round(w * k)))
    ys = (np.arange(nh) / k).astype(int).clip(0, h - 1)
    xs = (np.arange(nw) / k).astype(int).clip(0, w - 1)
    small = img[ys][:, xs]
    pad = np.zeros((SIDE, SIDE, 3), np.float32)
    pad[:nh, :nw] = small[..., ::-1]                             # YuNet học trên ảnh BGR, thang 0..255
    sess = _session()
    names = [o.name for o in sess.get_outputs()]
    out = dict(zip(names, sess.run(names, {sess.get_inputs()[0].name: pad.transpose(2, 0, 1)[None]})))
    return [(x0 / nw, y0 / nh, x1 / nw, y1 / nh, sc, m / nh) for x0, y0, x1, y1, sc, m in decode(out)]


def _speaking(t: float, speech: Sequence[tuple[float, float]]) -> bool:
    return any(a - 0.3 <= t <= b + 0.3 for a, b in speech)


STATIC_STD = 0.004         # mặt đứng yên tuyệt đối (lệch chuẩn tâm < 0,4% khung qua ≥ STATIC_N ảnh): tranh / ảnh dán / poster
STATIC_N = 6
MAIN_FRAC = 0.6            # mặt cao ≥ 60% mặt lớn nhất của ảnh mới tính (người đứng cạnh, mặt nhỏ ở xa: bỏ)


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return inter / max(1e-9, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def drop_static(samples: Sequence[tuple[float, list]]) -> list[tuple[float, list]]:
    """Bỏ mặt đứng yên tuyệt đối qua nhiều ảnh (đo 20/11 trên chương trình nấu ăn: hai bức tranh chân dung treo tường được
    YuNet nhận là mặt, 0,88-0,90 điểm). Gom mặt theo vị trí (IoU ≥ 0,5 với mặt cùng nhóm ở ảnh trước), nhóm có ≥ STATIC_N
    ảnh mà tâm gần như không nhúc nhích là vật tĩnh. Người thật đứng nói vẫn lắc lư hơn ngưỡng này."""
    groups: list[list[tuple[int, int]]] = []          # [(chỉ số ảnh, chỉ số mặt)]
    for i, (_t, fs) in enumerate(samples):
        for j, f in enumerate(fs):
            for g in groups:
                pi, pj = g[-1]
                if pi < i and _iou(samples[pi][1][pj], f) >= 0.5:
                    g.append((i, j))
                    break
            else:
                groups.append([(i, j)])
    static: set[tuple[int, int]] = set()
    for g in groups:
        if len(g) < STATIC_N:
            continue
        cx = np.array([(samples[i][1][j][0] + samples[i][1][j][2]) / 2 for i, j in g])
        cy = np.array([(samples[i][1][j][1] + samples[i][1][j][3]) / 2 for i, j in g])
        if max(float(cx.std()), float(cy.std())) < STATIC_STD:
            static.update(g)
    return [(t, [f for j, f in enumerate(fs) if (i, j) not in static]) for i, (t, fs) in enumerate(samples)]


def windows(samples: Sequence[tuple[float, list]], speech: Sequence[tuple[float, float]],
            box: tuple[float, float] | None = None) -> list[dict]:
    """Quãng người gốc nói vào máy: [{"a", "b", "zoom", "ok"}] (giây video gốc). samples: [(giây, mặt của ảnh đó)];
    speech: quãng có lời gốc; box: (đỉnh, cao) của vùng khung ra theo tỉ lệ ảnh (khung dọc theo nội dung) hoặc None.
    ok=False: mặt quá giữa / quá thấp, đẩy cận ZOOM_MAX vẫn không cắt được miệng."""
    top, height = box or (0.0, 1.0)
    hits = []
    for t, fs in drop_static(samples):
        fs = [f for f in fs if top <= f[5] <= top + height]   # miệng nằm trong vùng khung ra
        if not fs or not _speaking(t, speech):
            continue
        tallest = max(f[3] - f[1] for f in fs)
        big = [f for f in fs if (f[3] - f[1]) >= MAIN_FRAC * tallest and (f[3] - f[1]) / height >= FACE_MIN]
        if big:
            hits.append((t, max((f[5] - top) / height for f in big)))    # hai người cùng nói: cắt cả hai miệng
    out: list[dict] = []
    for t, mouth in hits:
        if out and t - out[-1]["b"] <= GAP:
            out[-1]["b"] = t
            out[-1]["mouths"].append(mouth)
        else:
            out.append({"a": t, "b": t, "mouths": [mouth]})
    res = []
    for w in out:
        a, b = w["a"] - 0.25, w["b"] + 0.25
        if b - a < MIN_LEN:
            continue
        mouth = float(np.percentile(w["mouths"], 80))          # vài ảnh cúi người / dò nhầm không kéo cả quãng
        # khung cắt neo đáy (sau cắt 4% viền): đỉnh = 1 - 0.96 / z, phải thấp hơn miệng 3%
        need = 0.96 / max(1e-3, 1 - min(0.99, mouth + 0.03))
        z = round(max(ZOOM_MIN, need), 2)
        res.append({"a": round(max(0.0, a), 2), "b": round(b, 2), "zoom": min(z, ZOOM_MAX),
                    "ok": mouth <= MOUTH_MAX and z <= ZOOM_MAX, "mouth": round(mouth, 3)})
    return res


def analyze(thumbs: Sequence[tuple[float, str]], speech: Sequence[tuple[float, float]],
            box: tuple[float, float] | None = None) -> list[dict]:
    """Đọc ảnh thu nhỏ [(giây, đường dẫn jpg)] -> windows(). Ảnh hỏng bỏ qua; model không tải / nạp được thì ném lỗi MỘT
    lần (trước đây lỗi tải bị nuốt trong vòng từng ảnh: mỗi ảnh lại thử tải 2 link, mỗi link chờ tới 60 giây)."""
    from PIL import Image
    if thumbs:
        _session()
    samples = []
    for t, p in thumbs:
        try:
            with Image.open(p) as im:
                samples.append((t, faces(np.asarray(im.convert("RGB")))))
        except Exception as e:
            logging.debug("Không đọc được ảnh %s: %s", p, e)
    return windows(samples, speech, box)


def thumbs_of(a: dict | None, fps: float = 2.0) -> list[tuple[float, str]]:
    """Ảnh thu nhỏ của scenes.analyzed(): [(giây, đường dẫn)] (mức nhẹ có «times» của khung chính, không thì fps đều)."""
    if not a or not a.get("dir") or not os.path.isdir(a["dir"]):
        return []
    files = sorted(f for f in os.listdir(a["dir"]) if f.endswith(".jpg"))
    times = a.get("times") or [k / fps for k in range(len(files))]            # bộ lọc fps: ảnh đầu ở giây 0
    return [(float(t), os.path.join(a["dir"], f)) for t, f in zip(times, files)]


def apply(plan: Any, wins: Sequence[dict]) -> tuple[Any, int]:
    """Khúc chính nằm ≥ OVERLAP trong một quãng «ok": cận ≥ zoom của quãng, neo đáy (ay=1). Trả (kế hoạch mới, số khúc)."""
    good = [w for w in wins if w.get("ok")]
    if plan is None or not good:
        return plan, 0
    n = 0
    pieces = []
    for p in plan.pieces:
        hit = None
        if p.role == "main" and p.b > p.a:
            for w in good:
                if min(p.b, w["b"]) - max(p.a, w["a"]) >= OVERLAP * (p.b - p.a):
                    hit = w
                    break
        if hit is not None:
            pieces.append(replace(p, zoom=round(max(p.zoom, hit["zoom"]), 3), ay=1.0))
            n += 1
        else:
            pieces.append(p)
    return replace(plan, pieces=pieces), n
