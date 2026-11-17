"""Sổ giọng của kênh: cặp «lời gốc -> lời kênh» đưa vào lệnh viết lời để AI học CÁCH VIẾT theo ví dụ (mô hình học theo
ví dụ tốt hơn theo luật: câu mẫu trong lệnh bản 44 đều kết bằng "nha" là AI kết "nha" 8/26 câu).

- Mẫu do người duyệt: voice_examples.json trong thư mục bộ nhận diện (brand/bep_dong_du/).
- Kho tự lớn: mỗi lần duyệt kịch bản, các đoạn có lời mới được lưu lại (đoạn người dùng SỬA tay được ưu tiên) —
  voice_bank.json cạnh brand_state.json.
Mỗi lần viết lời lấy vài cặp: luôn có câu mở đầu + thao tác có định lượng, phần còn lại xoay vòng theo video (video
khác nhau thấy mẫu khác nhau, đỡ na ná), cộng tối đa 2 cặp đã sửa tay hợp nguyên liệu của video."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time

import ingredients
from rmvideo.media import ff

KINDS = ("hook", "action", "wait", "tip", "plate", "react", "recap", "close")
KIND_VI = {"hook": "câu mở đầu", "action": "thao tác", "wait": "lúc chờ", "tip": "mẹo", "plate": "bày món",
           "react": "cảm thán", "recap": "tóm tắt", "close": "chào kết", "bank": "đã duyệt"}
PICK = 6
BANK_PICK = 2
BANK_MAX = 300
_lock = threading.Lock()


def bank_path() -> str:
    return os.environ.get("VOICE_BANK") or os.path.join(ff.ROOT, "voice_bank.json")


def curated() -> list[dict]:
    """Mẫu trong bộ nhận diện (thiếu file / hỏng: không có mẫu, lệnh viết lời như cũ)."""
    path = os.path.join(ff.BRAND_DIR, "voice_examples.json")
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f).get("examples") or []
    except FileNotFoundError:
        return []
    except Exception as e:
        logging.warning("Sổ giọng hỏng (%s): bỏ qua", str(e)[:120])
        return []
    return [{"kind": str(x.get("kind") or ""), "orig": str(x["orig"]).strip(), "new": str(x["new"]).strip()}
            for x in items if isinstance(x, dict) and x.get("orig") and x.get("new")]


def bank() -> list[dict]:
    try:
        with open(bank_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, dict) and x.get("orig") and x.get("new")]
    except FileNotFoundError:
        return []
    except Exception as e:
        logging.warning("Kho lời đã duyệt hỏng (%s): bỏ qua", str(e)[:120])
        return []


def remember(before: list, after: list) -> int:
    """Lưu các đoạn đã duyệt (before: kịch bản AI viết, after: bản người dùng chốt) vào kho. Đoạn giữ tiếng gốc / quá
    ngắn / quá dài bỏ qua. Trả số cặp mới lưu."""
    ai = {int(s["i"]): (s.get("text") or "").strip() for s in before if "i" in s}
    rows = []
    for s in after:
        new, orig = (s.get("text") or "").strip(), (s.get("orig") or "").strip()
        if s.get("ost") == "orig" or not orig or not 6 <= len(new.split()) <= 40:
            continue
        rows.append({"orig": orig, "new": new, "edited": new != ai.get(int(s.get("i", -1)), new), "t": int(time.time())})
    if not rows:
        return 0
    with _lock:
        have = bank()
        seen = {x["new"] for x in have}
        rows = [r for r in rows if r["new"] not in seen]
        if not rows:
            return 0
        keep = (have + rows)[-BANK_MAX:]
        path = bank_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
    return len(rows)


def _rank(seed: str, text: str) -> str:
    return hashlib.sha1(f"{seed}|{text}".encode("utf-8")).hexdigest()


def pick(context: str, seed: str, n: int = PICK) -> list[dict]:
    """Các cặp mẫu cho một video. context: lời gốc (chọn mẫu đã sửa tay có chung nguyên liệu); seed: mã video."""
    cur = curated()
    if not cur:
        return []
    by: dict = {}
    for x in cur:
        by.setdefault(x["kind"] if x["kind"] in KINDS else "", []).append(x)
    for k in by:
        by[k].sort(key=lambda x: _rank(seed, x["new"]))
    out: list[dict] = []
    for k in ("hook", "action"):                                  # luôn có: câu hook + câu có định lượng
        if by.get(k):
            out.append(by[k].pop(0))
    rest = [x for k in sorted(by, key=lambda k: _rank(seed, k)) for x in by[k][:1]]
    rest += [x for k in by for x in by[k][1:]]
    bank_n = min(BANK_PICK, max(0, n - len(out) - 1))
    mine = [x for x in bank() if x.get("edited")]
    if mine and bank_n:
        groups = ingredients.find(context)
        mine.sort(key=lambda x: (len(groups & ingredients.find(x["orig"] + " " + x["new"])), x.get("t", 0)),
                  reverse=True)
        out += [{"kind": "bank", "orig": x["orig"], "new": x["new"]} for x in mine[:bank_n]]
    out += rest[:max(0, n - len(out))]
    return out[:n]
