"""Câu hook (1.4.0): viết NHIỀU câu mở đầu theo nhiều kiểu, AI chấm điểm, chọn câu mạnh nhất; ghi kiểu hook đã dùng để
so với lượt xem về sau.

Video 10 nghìn view và 1 triệu view nhiều khi khác nhau chỉ ở câu đầu, nên câu đó đáng một lượt riêng:
  1. Một lượt AI viết N_HOOKS câu theo các kiểu trong KINDS (vấn đề / mẹo bất ngờ / kết quả / tò mò / con số), mỗi câu
     kèm chữ in màn hình.
  2. MÃ loại câu sai luật: cụt (< MIN_WORDS chữ) hay dài quá, không nêu tên món, chào hỏi / kêu gọi, «mình thử» (người
     đọc không phải người nấu), nguyên liệu hay con số không có trong lời gốc.
  3. Một lượt AI khác chấm 1-10 từng câu (hiểu ngay món gì và được gì, lời hứa có thật, đúng ý lời gốc, nối trơn sang
     câu sau) và chọn.
1.4.1: có đoạn mở đầu thì câu hook chọn TRƯỚC (choose) và đoạn mở đầu dài vừa đọc hết câu (2-4 giây). Bản 1.4.0 chọn sau
khi viết kịch bản, ép câu vào 2 giây -> tối đa 5 chữ, job 6 ra «Đồ chua bị hăng?»: cụt, không hứa gì, lạc với câu sau.
Không có đoạn mở đầu: chọn sau kịch bản như cũ (improve, câu cũ chấm cùng). Lỗi gì cũng giữ câu cũ.

Kiểu hook ghi vào job.hook_kind và hooks.json (HOOK_STATE); status_lines ghép với lượt xem YouTube sau 24h
(rmvideo/app/youtube.py) để thấy kênh hợp kiểu nào."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import config
import ingredients
import prompt_engine
from rmvideo.domain import dialect

KINDS = {"van_de": "vấn đề", "meo": "mẹo bất ngờ", "ket_qua": "kết quả", "to_mo": "tò mò", "con_so": "con số",
         "goc": "viết cùng kịch bản"}
N_HOOKS = 8
MIN_WORDS = 7                   # câu hook ngắn hơn là câu cụt («Đồ chua bị hăng?» — job 6, 21/11)
TEASER_MAX = 4.0                # giây: đoạn mở đầu dài tối đa chừng này để đọc hết câu hook
TIMEOUT = 45.0
KEEP = 400                      # số job gần nhất giữ trong hooks.json
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STATE_FILE = os.environ.get("HOOK_STATE") or os.path.join(_ROOT, "hooks.json")
# người đọc lời không phải người nấu: «mình thử công thức 5 triệu view» là nói sai (và con số thường là bịa)
_FAKE_RE = re.compile(r"\b(mình|tui|tôi|tụi mình|em)\s+(đã\s+|vừa\s+|mới\s+)?(thử|làm thử|nấu thử|ăn thử)\b|"
                      r"lần đầu (mình |tui |tôi )?(làm|nấu)|triệu (view|lượt)|nghìn (view|lượt)|ngàn (view|lượt)",
                      re.IGNORECASE)

GEN_PROMPT = """Bạn là biên kịch video nấu ăn ngắn (Reels / Shorts / TikTok) tiếng Việt, giọng miền Nam. Viết {n} CÂU HOOK khác nhau: câu đọc lên ngay giây 0, lúc màn hình đang chiếu {scene}. Câu hook quyết định người đang lướt có dừng lại không.
Món: {dish}
Tiêu đề video gốc: «{title}»
Mẹo chính người làm video nói (đã kiểm trong lời gốc): {tip}
Lời gốc cả video (để biết video thật sự có gì): «{orig}»
Câu ngay SAU câu hook (phần làm món bắt đầu): «{after}»
{seg0}
Người lướt CHƯA biết video về món gì và chưa nghe gì trước đó, nên MỖI câu hook phải:
- NÊU TÊN MÓN (hoặc nguyên liệu chính) để người xem biết ngay đây là món gì;
- là MỘT câu trọn ý {mn}-{mx} chữ khi đọc ra: nêu một vấn đề / mẹo / kết quả CỤ THỂ kèm LỜI HỨA video sẽ chỉ. Câu hỏi thì phải kèm lời hứa ngay trong câu, KHÔNG hỏi trống không;
- nói ĐÚNG ý lời gốc, không đảo nghĩa (gốc nói «không cần thêm nước» thì không được viết «thêm nước»).
Ví dụ TỐT (món khác, chỉ để học cách viết): «Bò lúc lắc hay bị dai là do bỏ qua bước ướp này nè.» · «Cá kho tộ hổng tanh mà thơm lừng nhờ mẹo nhỏ này.» · «Trứng chiên kiểu này phồng gấp đôi, giòn rụm luôn.»
Ví dụ DỞ: «Cá bị tanh?» (cụt, chưa hứa gì) · «Dùng một trăm gam muối.» (kể bước, không gây tò mò) · «Ngon lắm luôn nè!» (không nêu món, chung chung).
Viết đủ các kiểu, mỗi kiểu 1-2 câu, ghi kiểu vào "kind":
- "van_de": lỗi / nỗi khổ hay gặp mà video giải quyết. CHỈ viết khi mẹo chính hay lời gốc thật sự có cách khắc phục đó.
- "meo": mẹo bất ngờ, ngược thói quen.
- "ket_qua": kết quả CỤ THỂ người xem sẽ có (giòn rụm, mềm tan, để được cả tháng...), không khen chung chung.
- "to_mo": gợi tò mò về cách làm (kèm lời hứa).
- "con_so": dựa trên con số CÓ THẬT trong lời gốc (số phút, số ngày giữ được, định lượng); không có số thì bỏ kiểu này.
Luật: nói chuyện tự nhiên, có dấu câu; KHÔNG chào hỏi, không giới thiệu kênh, không kêu gọi like / đăng ký / theo dõi; KHÔNG nói «mình thử», «mình làm thử», «lần đầu mình nấu» (người đọc không phải người nấu); KHÔNG bịa nguyên liệu, con số, lượt xem hay lời khen của người khác; không emoji.
Mỗi câu kèm "screen": 3-7 chữ in lên màn hình cùng lúc (có tên món), viết chữ thường có dấu đầy đủ, không dấu chấm.
Trả về DUY NHẤT JSON: {{"hooks": [{{"kind": "van_de", "text": "...", "screen": "..."}}]}}"""

JUDGE_PROMPT = """Bạn là biên tập viên một kênh nấu ăn video ngắn hàng triệu lượt xem. Dưới đây là các câu hook ứng viên đọc ngay giây 0 của video «{title}» (món: {dish}; màn hình lúc đó đang chiếu {scene}). Mẹo chính của video: {tip}. Câu ngay sau hook: «{after}».
Chấm mỗi câu 1-10 theo, quan trọng nhất trước:
(1) người đang lướt nghe xong có hiểu ngay video về MÓN GÌ và sẽ được gì không — câu cụt, hỏi trống không, không nêu món: tối đa 4 điểm;
(2) có vấn đề / lời hứa / điều bất ngờ CỤ THỂ khiến muốn xem tiếp, không khen chung chung;
(3) đúng với lời gốc, lời hứa có thật trong video (đảo nghĩa lời gốc: 1 điểm);
(4) tự nhiên như người miền Nam nói, nối trơn sang câu ngay sau, khớp với hình đang chiếu.
{items}
Trả về DUY NHẤT JSON: {{"scores": [{{"i": số câu, "score": điểm}}], "best": số câu tốt nhất}}"""

try:
    from pydantic import BaseModel

    class _Hook(BaseModel):
        kind: str
        text: str
        screen: str = ""

    class HooksOut(BaseModel):
        hooks: list[_Hook]

    class _Score(BaseModel):
        i: int
        score: float

    class JudgeOut(BaseModel):
        scores: list[_Score]
        best: int
except Exception:                                        # pragma: no cover
    HooksOut = JudgeOut = None  # type: ignore[assignment,misc]


def wanted() -> bool:
    return str(getattr(config, "HOOK_PICK", "1")) != "0" and bool(config.GEMINI_API_KEY) and prompt_engine.genai is not None


def budget(seg0: dict) -> int:
    """Số từ tối đa của câu hook: đúng công thức generate_timed dùng cho đoạn 0 (khe trống / có lời gốc)."""
    rate = prompt_engine._rate()
    slot = float(seg0.get("slot") or 3.0)
    if not (seg0.get("orig") or "").strip():
        span = float(seg0.get("span") or slot)
        return max(5, min(int(min(slot, span, prompt_engine.INTRO_SPEAK) * rate * 0.92), 16))
    return max(3, int(slot * rate * 0.92))


def teaser_words(rate: float) -> int:
    """Số chữ tối đa của câu hook đọc trên đoạn mở đầu dài nhất (TEASER_MAX giây)."""
    return max(MIN_WORDS + 2, int(TEASER_MAX * rate * 0.92))


def teaser_secs(text: str, rate: float) -> float:
    """Độ dài đoạn mở đầu cho vừa câu hook: đọc hết câu (+ chút thở), trong khoảng TEASER_DUR..TEASER_MAX giây."""
    from rmvideo.media import filtergraph
    need = prompt_engine.spoken_words(text) / max(rate, 1.0) + 0.35
    return round(min(TEASER_MAX, max(filtergraph.TEASER_DUR, need)), 1)


def _norm(s: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", (s or "").lower()).strip() + " "


def names_dish(text: str, dish: str, title: str = "") -> bool:
    """Câu hook có nêu tên món (hoặc nguyên liệu chính của món / tiêu đề) không. Không biết món: coi như có."""
    t = _norm(text)
    if dish and _norm(dish) in t:
        return True
    want = ingredients.find(f"{dish} {title}")
    if not want and not dish:
        return True
    return bool(want & ingredients.find(text))


def problems(text: str, mx: int, orig_all: str, orig0: str, dish: str = "", title: str = "",
             mn: int = 2) -> list[str]:
    """Lý do câu hook không dùng được (rỗng = dùng được)."""
    why = []
    n = prompt_engine.spoken_words(text)
    if n < mn or n > mx + 1:
        why.append(f"{n} từ (cần {mn}-{mx})")
    if prompt_engine._GREET_RE.search(text) or prompt_engine._CTA_RE.search(text) or prompt_engine.has_sub_cta(text):
        why.append("chào hỏi / kêu gọi")
    if _FAKE_RE.search(text):
        why.append("nói như người nấu / lượt xem bịa")
    if (dish or title) and not names_dish(text, dish, title):
        why.append("không nêu tên món")
    added_n = ingredients.check_numbers(orig_all, text)[1]
    added_i = prompt_engine._not_in_glossary(ingredients.check_rewrite(orig_all, text)[1])
    if added_n or added_i:
        why.append("bịa " + ", ".join(added_n + added_i))
    if orig0:
        miss = ingredients.check_rewrite(orig0, text)[0] + ingredients.check_numbers(orig0, text)[0]
        if miss:
            why.append("mất " + ", ".join(miss) + " của lời gốc đoạn 0")
        if prompt_engine.copy_ratio(text, orig0) >= 0.45:
            why.append("chép lời gốc")
    return why


def _parse_hooks(raw: str) -> list[dict]:
    d = prompt_engine.loads_lenient(raw)
    out = []
    for h in d.get("hooks") or []:
        if not isinstance(h, dict):
            continue
        text = dialect.south(str(h.get("text") or "").strip())
        kind = str(h.get("kind") or "").strip()
        if text:
            out.append({"kind": kind if kind in KINDS else "to_mo", "text": text,
                        "screen": dialect.south(str(h.get("screen") or "").strip())})
    return out


async def _pick(*, title: str, tip: str, dish: str, orig_all: str, after: str, teaser: bool, mx: int, mn: int,
                orig0: str = "", old: dict | None = None) -> dict | None:
    """Một lượt viết N_HOOKS câu + mã loại câu sai luật + một lượt chấm điểm. Trả câu thắng {"kind", "text", "screen",
    "score", "n"} hoặc None (AI lỗi / không đủ câu hợp lệ)."""
    scene = "cảnh món thành phẩm hấp dẫn nhất (chiếu trước, rồi video mới làm từ đầu)" if teaser else "cảnh đầu video"
    tip_txt = f"«{tip.strip().rstrip('.')}»" if tip.strip() else "(không có mẹo rõ ràng: đừng viết kiểu vấn đề)"
    dish_txt = dish or "(xem tiêu đề)"
    seg0_rule = (f"Đoạn 0 có lời gốc «{orig0}»: câu hook phải nhắc ĐỦ và ĐÚNG mọi nguyên liệu, con số trong câu đó, "
                 "bằng từ ngữ khác." if orig0 else "")
    try:
        raw = await prompt_engine.call_gemini(
            GEN_PROMPT.format(n=N_HOOKS, scene=scene, title=title or "(không có)", tip=tip_txt, dish=dish_txt,
                              orig=orig_all[:1500], after=after[:200] or "(không có)", seg0=seg0_rule, mx=mx, mn=mn),
            schema=HooksOut, timeout=TIMEOUT)
        pool = _parse_hooks(raw)
    except Exception as e:
        logging.warning("Hook: AI viết câu lỗi (%s)", str(e)[:120])
        return None
    if old and old.get("text"):
        pool.insert(0, old)
    ok, seen = [], set()
    for h in pool:
        key = re.sub(r"\W+", " ", h["text"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        # câu viết cùng kịch bản đã qua bộ kiểm của kịch bản, vừa khe của chính nó: không loại, chỉ chấm cùng
        why = [] if h["kind"] == "goc" else problems(h["text"], mx, orig_all, orig0, dish, title, mn)
        logging.info("Hook ứng viên (%s)%s: %s", KINDS[h["kind"]], f" LOẠI: {'; '.join(why)}" if why else "", h["text"])
        if not why:
            ok.append(h)
    if not ok:
        return None
    scores: dict[int, float] = {}
    best = 0
    if len(ok) >= 2:
        items = "\n".join(f"{k} | {KINDS[h['kind']]} | {h['text']}" for k, h in enumerate(ok))
        try:
            raw = await prompt_engine.call_gemini(
                JUDGE_PROMPT.format(title=title or "(không có)", dish=dish_txt, scene=scene, tip=tip_txt,
                                    after=after[:200] or "(không có)", items=items), schema=JudgeOut, timeout=TIMEOUT)
            d = prompt_engine.loads_lenient(raw)
        except Exception as e:
            logging.warning("Hook: AI chấm điểm lỗi (%s)", str(e)[:120])
            return None
        for x in d.get("scores") or []:
            try:
                scores[int(x["i"])] = float(x["score"])
            except (TypeError, ValueError, KeyError):
                continue
        try:
            best = int(d.get("best", -1))
        except (TypeError, ValueError):
            best = -1
        if scores and 0 <= best < len(ok) and scores.get(best, 0) < max(scores.values()):
            best = max(scores, key=lambda k: scores[k])     # «best» lệch điểm chấm: theo điểm
        if not 0 <= best < len(ok):
            best = max(scores, key=lambda k: scores[k], default=-1) if scores else -1
            if not 0 <= best < len(ok):
                return None
    h = ok[best]
    logging.info("Hook chọn %d/%d (%s, %s điểm): %s", best, len(ok), KINDS[h["kind"]], scores.get(best, "?"), h["text"])
    ref = " ".join([title, dish, orig_all])
    return {"kind": h["kind"], "text": h["text"],
            "screen": prompt_engine.clean_hook(h["screen"], ref) if h.get("screen") else "",
            "score": scores.get(best), "n": len(ok)}


async def choose(title: str, tip: str, dish: str, orig_all: str, after: str, rate: float) -> dict | None:
    """Chọn câu hook TRƯỚC khi quyết đoạn mở đầu (1.4.1): đoạn mở đầu dài theo câu hook (teaser_secs), không ép câu hook
    vào 2 giây (1.4.0 job 6: ép còn 5 chữ -> «Đồ chua bị hăng?», cụt và lạc). Trả thêm "secs" (độ dài đoạn mở đầu)."""
    if not wanted():
        return None
    got = await _pick(title=title, tip=tip, dish=dish, orig_all=orig_all, after=after, teaser=True,
                      mx=teaser_words(rate), mn=MIN_WORDS)
    if got:
        got["secs"] = teaser_secs(got["text"], rate)
    return got


def apply(timed: dict, hook: dict) -> bool:
    """Đặt câu hook đã chọn vào đoạn 0 (khe trống của đoạn mở đầu) của kịch bản. False: đoạn 0 không phải khe hook."""
    segs = timed.get("segments") or []
    if not segs or int(segs[0].get("i", 0)) != 0 or (segs[0].get("orig") or "").strip() or segs[0].get("ost") == "orig":
        return False
    segs[0]["text"] = hook["text"]
    segs[0]["copy"] = 0.0
    timed["script"] = " ".join(s["text"] for s in segs if s.get("text"))
    if hook.get("screen"):
        timed["hook"] = hook["screen"]
    return True


async def improve(timed: dict, title: str, tip: str = "", teaser: bool = False, dish: str = "") -> dict | None:
    """Không có đoạn mở đầu: thay câu hook (đoạn 0) của kịch bản bằng câu mạnh nhất trong N_HOOKS câu AI viết thêm
    (câu cũ chấm cùng). Sửa tại chỗ `timed` (segments[0].text, script, hook). Trả {"kind", "text", "screen", "score",
    "n"} hoặc None (giữ nguyên)."""
    segs = timed.get("segments") or []
    if not segs or not wanted():
        return None
    seg0 = segs[0]
    if int(seg0.get("i", 0)) != 0 or seg0.get("ost") == "orig":
        return None
    old = (seg0.get("text") or "").strip()
    orig0 = (seg0.get("orig") or "").strip()
    orig_all = " ".join((s.get("orig") or "") for s in segs)
    after = next(((s.get("orig") or s.get("text") or "") for s in segs[1:] if (s.get("orig") or s.get("text"))), "")
    mx = budget(seg0)
    got = await _pick(title=title, tip=tip, dish=dish, orig_all=orig_all, after=after, teaser=teaser, mx=mx,
                      mn=min(MIN_WORDS, max(2, mx - 2)), orig0=orig0,
                      old={"kind": "goc", "text": old, "screen": timed.get("hook") or ""} if old else None)
    if not got:
        return None
    if got["kind"] != "goc":
        seg0["text"] = got["text"]
        seg0["copy"] = round(prompt_engine.copy_ratio(got["text"], orig0), 2) if orig0 else 0.0
        timed["script"] = " ".join(s["text"] for s in segs if s.get("text"))
        if got["screen"]:
            timed["hook"] = got["screen"]
    got["screen"] = timed.get("hook") or ""
    return got


# ---------------- ghi lại để so với lượt xem ----------------

def load_state(path: str | None = None) -> dict:
    try:
        with open(path or STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("items"), list) else {"items": []}
    except Exception:
        return {"items": []}


def record(job_id: int, kind: str, text: str, title: str = "", path: str | None = None) -> None:
    """Ghi kiểu hook của job (job duyệt kịch bản chạy lại bằng id mới: ghi thêm id đó)."""
    if kind not in KINDS:
        return
    p = path or STATE_FILE
    st = load_state(p)
    items = [i for i in st["items"] if i.get("job") != job_id]
    items.append({"job": job_id, "kind": kind, "text": text[:200], "title": title[:120], "at": round(time.time())})
    st["items"] = items[-KEEP:]
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        logging.warning("Không ghi được hooks.json: %s", str(e)[:120])


def by_kind(views: dict[int, int], path: str | None = None) -> list[tuple[str, float, int]]:
    """[(kiểu, lượt xem trung bình, số video)] — chỉ các job đã có lượt xem; nhiều xem trước."""
    acc: dict[str, list[int]] = {}
    for i in load_state(path)["items"]:
        v = views.get(i.get("job"))
        if v is not None and i.get("kind") in KINDS:
            acc.setdefault(i["kind"], []).append(int(v))
    return sorted(((k, sum(v) / len(v), len(v)) for k, v in acc.items()), key=lambda x: -x[1])


def status_lines(ch: str = "cook") -> list[str]:
    """Một dòng «Hook theo lượt xem YouTube 24h: ...» khi đã có số liệu."""
    try:
        from rmvideo.app import youtube
        views: dict[int, Any] = {int(i["job"]): i["views"] for i in youtube.load_state()["items"]
                                 if i.get("ch") == ch and i.get("views") is not None and str(i.get("job", "")).isdigit()}
    except Exception:
        return []
    rows = by_kind(views)
    if not rows:
        return []
    return ["Hook theo lượt xem YouTube 24h: " + " · ".join(f"{KINDS[k]} {avg:,.0f} ({n} video)".replace(",", ".")
                                                             for k, avg, n in rows)]
