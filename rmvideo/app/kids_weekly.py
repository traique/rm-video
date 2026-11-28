"""(Botkeep) 📼 Video tổng hợp truyện bé hằng tuần cho YouTube (1.6.0).

Kênh thiếu nhi trên YouTube lớn lên nhờ video dài: bé xem liền nhiều truyện (TV, máy tính bảng, điện thoại của ba mẹ) và
YouTube tính thời gian xem. Shorts từng truyện vẫn đăng như cũ; mỗi tuần (KIDS_WEEKLY, mặc định «CN 08:00») bot ghép các
truyện đã lên YouTube trong tuần thành MỘT video dài trên kênh YouTube truyện bé:
- Nối thẳng (ffmpeg concat, -c copy), không mã hoá lại: video truyện bé cùng một khuôn (1080x1920, 30 fps, H.264 High 4.1,
  AAC 48 kHz — rmvideo/kids/render) nên ghép mất vài giây, không tốn CPU Botkeep hay giờ GPU Kaggle. Chỉ ghép các file có
  thông số VÀ extradata (SPS/PPS của H.264, cấu hình AAC) giống hệt nhau (ffprobe -show_data_hash): nối file khác khuôn
  (bản dựng cũ, x264 thay NVENC) bằng -c copy cho ra video lỗi hình. Khác khuôn thì để lại, tuần sau / hết hạn thì bỏ.
- Mục lục «0:00 Tên truyện» trong mô tả -> YouTube tự chia chương; từ tiếng Anh bé học; câu giới thiệu, CTA, từ khoá kênh.
- Ảnh bìa 1280x720: thẻ tiêu đề (giây 1,6, như rmvideo/kids/render.cover) của 3 truyện đầu + dòng chữ. YouTube chỉ nhận ảnh
  bìa riêng khi kênh đã xác minh số điện thoại: lỗi thì ghi chú, YouTube tự chọn khung.
- Khai «dành cho trẻ em» hay không như mọi video của kênh bé: theo KIDS_YT_MADE_FOR_KIDS (youtube.made_for_kids, 1.6.2).

Giữ file: bộ dọn ổ (storage._cleanup_outputs) chỉ giữ ~OUT_MAX_MB video mới nhất trong outputs/, truyện đầu tuần bị xoá trước
Chủ nhật. Mỗi truyện vừa xếp lên YouTube (autopost.other_platforms) được liên kết cứng (hard link: không tốn thêm chỗ khi file
gốc còn) vào kids_library/. Ghép xong thì xoá các truyện đã ghép; video tổng hợp xoá khi YouTube đã nhận hoặc đã bỏ nó.
Ghép trước giờ đăng BUILD_LEAD giây rồi xếp vào hàng chờ YouTube với publishAt = giờ đăng (YouTube tự công khai đúng giờ).
Trạng thái: kids_weekly.json (KIDS_WEEKLY_STATE)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from typing import Any

import config
from rmvideo.app import youtube
from rmvideo.media import ff

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = os.environ.get("KIDS_WEEKLY_STATE") or os.path.join(_ROOT, "kids_weekly.json")
LIB_DIR = os.environ.get("KIDS_LIBRARY_DIR") or os.path.join(_ROOT, "kids_library")
VN = youtube.VN
DAYS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")      # datetime.weekday(): thứ Hai = 0 … Chủ nhật = 6
BUILD_LEAD = 6 * 3600       # ghép trước giờ đăng chừng này: tải lên (vài trăm MB) + YouTube xử lý HD video ~15 phút
LATE = 6 * 3600             # bot tắt đúng giờ: còn trong chừng này sau giờ đăng thì vẫn ghép, đăng ngay
MIN_CLIPS = 3               # YouTube cần ≥ 3 mốc mục lục mới chia chương; ít hơn thì dồn sang tuần sau
CLIP_MAX_AGE = 21 * 86400   # truyện chưa ghép quá 3 tuần (tắt tính năng / khác khuôn / YouTube mất kết nối): xoá khỏi kho
MADE_KEEP = 7 * 86400       # video tổng hợp không còn trong hàng chờ YouTube: xoá sau chừng này
BACKFILL_DAYS = 8           # lần chạy đầu: truyện đã đăng trong chừng này ngày (file còn) vào kho luôn
SHORT_MAX = 15 * 60 - 10    # kênh chưa xác minh số điện thoại: YouTube từ chối video dài hơn 15 phút
THUMB_AT = 1.6              # giây thẻ tiêu đề đang hiện
THUMB_BG = "0xFF7A30"       # cam của thẻ truyện bé (rmvideo/kids/cards.ORANGE)
KEEP_MADE = 60
_LOCK = asyncio.Lock()


# ---------------- lịch ----------------

def spec(raw: str | None = None) -> tuple[int, int, int] | None:
    """KIDS_WEEKLY «CN 08:00» -> (thứ 0-6, giờ, phút); tắt / sai -> None."""
    s = " ".join(str(config.KIDS_WEEKLY if raw is None else raw).split()).upper()
    m = re.fullmatch(r"(T[2-7]|CN) ?([01]?\d|2[0-3]):([0-5]\d)", s)
    return (DAYS.index(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _slots(now: float, sp: tuple[int, int, int]) -> list[float]:
    """Giờ đăng tuần trước, tuần này, tuần sau (giờ Việt Nam) quanh `now`."""
    d = datetime.fromtimestamp(now, VN)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    t = monday + timedelta(days=sp[0], hours=sp[1], minutes=sp[2])
    return [(t + timedelta(weeks=k)).timestamp() for k in (-1, 0, 1)]


def due_slot(now: float, sp: tuple[int, int, int] | None = None) -> float | None:
    """Giờ đăng mà lúc này là lúc ghép (từ BUILD_LEAD trước tới LATE sau giờ đó), hoặc None."""
    sp = sp or spec()
    if sp is None:
        return None
    return next((t for t in _slots(now, sp) if t - BUILD_LEAD <= now <= t + LATE), None)


def next_slot(now: float, sp: tuple[int, int, int] | None = None) -> float | None:
    sp = sp or spec()
    if sp is None:
        return None
    return next((t for t in _slots(now, sp) if t + LATE >= now), None)


def week_key(t: float) -> str:
    y, w, _ = datetime.fromtimestamp(t, VN).isocalendar()
    return f"{y}-W{w:02d}"


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts, VN).strftime("%H:%M %d/%m")


def _mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def enabled() -> str:
    """Lý do KHÔNG chạy (rỗng = chạy): tắt trong Cài đặt, hoặc kênh YouTube truyện bé chưa kết nối."""
    if spec() is None:
        return "tắt (KIDS_WEEKLY = 0)"
    if youtube.missing("kids"):
        return "cần kết nối YouTube kênh truyện bé"
    return ""


def max_clips() -> int:
    try:
        return max(MIN_CLIPS, min(30, int(str(getattr(config, "KIDS_WEEKLY_MAX", "14") or "14"))))
    except ValueError:
        return 14


# ---------------- trạng thái ----------------

def load_state(path: str | None = None) -> dict:
    """{"clips": [truyện chờ ghép], "made": [video tổng hợp đã ghép], "weeks": {tuần: kết quả}, "backfilled": bool}."""
    path = path or STATE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("không phải object JSON")
    except FileNotFoundError:
        st = {}
    except (OSError, ValueError) as e:
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        logging.error("kids_weekly.json hỏng (%s), đã đổi tên thành .bad", e)
        st = {}
    for k in ("clips", "made"):
        st.setdefault(k, [])
    st.setdefault("weeks", {})
    st.setdefault("backfilled", False)
    return st


def save_state(st: dict, path: str | None = None) -> None:
    path = path or STATE_FILE
    st["made"] = st["made"][-KEEP_MADE:]
    st["weeks"] = dict(sorted(st["weeks"].items())[-KEEP_MADE:])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _remove(*paths: str) -> None:
    for p in paths:
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError as e:
                logging.warning("Tổng hợp tuần: không xoá được %s (%s)", p, e)


# ---------------- kho truyện ----------------

def add(job: Any, video: str, when: float | None = None, publish: float | None = None, st: dict | None = None) -> bool:
    """Giữ một truyện đã xếp lên YouTube cho video tổng hợp (một lần mỗi job): liên kết cứng file video vào kho (sao chép
    nếu không liên kết được), ghi tên truyện, từ tiếng Anh, giờ Shorts lên YouTube. False: tắt / chưa kết nối / đã có /
    không có file. Lỗi gì cũng không làm hỏng việc đăng bài."""
    try:
        if enabled() or not video or not os.path.isfile(video):
            return False
        own = st is None
        st = load_state() if st is None else st
        jid = int(job.id)
        if any(int(c.get("job") or 0) == jid for c in st["clips"]):
            return False
        os.makedirs(LIB_DIR, exist_ok=True)
        dst = os.path.join(LIB_DIR, f"{jid}.mp4")
        if not os.path.isfile(dst):
            try:
                os.link(video, dst)
            except OSError:
                shutil.copyfile(video, dst)
        title = str(getattr(job, "hook", "") or "").strip()
        if not title:
            post = str(getattr(job, "post", "") or "")
            title = post.split("\n", 1)[0].replace("📌", "").replace("#Shorts", "").strip() or f"Truyện #{jid}"
        brief = getattr(job, "brief", None) or {}
        words = [str(w.get("en") if isinstance(w, dict) else w) for w in brief.get("words") or []]
        if publish is None:
            publish = (youtube.slot_for("kids", when) if when else None) or when or time.time()
        st["clips"].append({"job": jid, "title": title[:120], "words": [w for w in words if w][:6], "path": dst,
                            "publish": round(float(publish), 2), "added": round(time.time(), 2)})
        if own:
            save_state(st)
        return True
    except Exception as e:                                     # không bao giờ làm hỏng việc đăng truyện
        logging.warning("Tổng hợp tuần: không giữ được truyện #%s (%s)", getattr(job, "id", "?"), str(e)[:150])
        return False


def backfill(st: dict, now: float) -> int:
    """Lần chạy đầu sau khi cập nhật: truyện đã đăng trong BACKFILL_DAYS ngày mà file còn trên máy vào kho luôn (khỏi chờ
    cả tuần mới có video tổng hợp đầu tiên)."""
    try:
        import autopost
        import kids_auto
        from state import queue
    except Exception:                                          # pragma: no cover - thiếu môi trường bot
        return 0
    n = 0
    for rec in kids_auto.load_state()["posts"]:
        t = float(rec.get("time") or 0)
        if t < now - BACKFILL_DAYS * 86400:
            continue
        job = queue.jobs.get(rec.get("job"))
        if job is None or job.status != "done":
            continue
        video, _thumb = autopost._output_files(job)
        if video and add(job, video, publish=youtube.slot_for("kids", t, now=0) or t, st=st):
            n += 1
    return n


def cleanup(st: dict, now: float) -> None:
    """Xoá video tổng hợp YouTube đã nhận / đã bỏ (hoặc không còn trong hàng chờ sau MADE_KEEP); truyện mất file hoặc chờ
    quá CLIP_MAX_AGE."""
    items = {it.get("key"): it for it in youtube.load_state()["items"]}
    for m in st["made"]:
        if not m.get("path") or m.get("gone"):
            continue
        it = items.get(m.get("key"))
        if (it is not None and it.get("status") in ("done", "failed")) or (it is None and now - float(m.get("at") or 0) > MADE_KEEP):
            _remove(m["path"], str(m.get("thumb") or ""))
            m["gone"] = True
    keep = []
    for c in st["clips"]:
        if not os.path.isfile(str(c.get("path") or "")):
            continue
        if now - float(c.get("added") or now) > CLIP_MAX_AGE:
            _remove(c["path"])
            continue
        keep.append(c)
    st["clips"] = keep


# ---------------- ghép ----------------

async def probe(path: str) -> dict | None:
    """{"dur", "sig"}: sig = thông số + extradata (hash) của luồng hình và tiếng — hai file ghép thẳng được khi sig bằng nhau.
    ffprobe không có -show_data_hash (bản rất cũ): đọc lại không có hash, so thông số thôi."""
    d: dict = {}
    for extra in (("-show_data_hash", "sha256"), ()):
        try:
            out = await ff._run_ffmpeg("-v", "error", "-show_streams", "-show_format", *extra, "-of", "json", path,
                                       tool="ffprobe")
            d = json.loads(out or "{}")
            break
        except Exception as e:
            if not extra:
                logging.warning("Tổng hợp tuần: không đọc được %s (%s)", os.path.basename(path), str(e)[:120])
                return None
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None) or {}
    if v is None:
        return None
    sig = tuple(str(v.get(k)) for k in ("codec_name", "profile", "level", "width", "height", "pix_fmt", "r_frame_rate",
                                         "extradata_hash"))
    sig += tuple(str(a.get(k)) for k in ("codec_name", "profile", "sample_rate", "channels", "extradata_hash"))
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    return {"dur": dur, "sig": sig}


def pick(infos: list[tuple[dict, dict]], cap: int) -> tuple[list[tuple[dict, dict]], int]:
    """Nhóm truyện cùng khuôn đông nhất (hoà thì nhóm có truyện mới nhất), lấy tối đa `cap` truyện cũ trước (không truyện
    nào bị bỏ quên). Trả (đã chọn, số truyện khác khuôn bị để lại)."""
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    for c, info in infos:
        groups.setdefault(info["sig"], []).append((c, info))
    if not groups:
        return [], 0
    best = max(groups.values(), key=lambda g: (len(g), max(float(c["publish"]) for c, _ in g)))
    return sorted(best, key=lambda x: float(x[0]["publish"]))[:cap], len(infos) - len(best)


def chapters(clips: list[dict], durs: list[float]) -> list[str]:
    out, t = [], 0.0
    for c, d in zip(clips, durs):
        out.append(f"{_mmss(t)} {c['title']}")
        t += d
    return out


def texts(clips: list[dict], durs: list[float], k: int) -> tuple[str, str]:
    """(tiêu đề, mô tả) YouTube: «Tuyển tập 3: 14 truyện hoạt hình cho bé | <đuôi từ khoá>» + mục lục, từ tiếng Anh, câu
    giới thiệu, CTA, từ khoá, hashtag của kênh."""
    import kids_auto
    ch = kids_auto.channel()
    n, total = len(clips), sum(durs)
    tag = str(ch.get("title_tag") or ch.get("name") or "").strip()
    title = f"Tuyển tập {k}: {n} truyện hoạt hình cho bé" + (f" | {tag}" if tag else "")
    if len(title) > youtube.TITLE_MAX:
        title = f"Tuyển tập {k}: {n} truyện hoạt hình cho bé | {ch.get('name') or ''}".rstrip(" |")
    words = list(dict.fromkeys(w for c in clips for w in c.get("words") or []))[:40]
    fixed = [t for t in re.split(r"[,\s]+", str(ch.get("hashtags") or "")) if t.startswith("#")][:12]
    lines = [f"🧸 {n} truyện hoạt hình cho bé ở {ch.get('world') or 'Đồi Cỏ Non'} trong một video dài {int(total // 60)} phút: "
             "bé nghe kể chuyện liền mạch và học từ tiếng Anh qua từng truyện.", "", "📖 Mục lục", *chapters(clips, durs)]
    if words:
        lines += ["", "🔤 Từ tiếng Anh trong video: " + ", ".join(words)]
    if ch.get("tagline"):
        lines += ["", str(ch["tagline"])]
    if ch.get("cta"):
        lines += [f"👉 {ch['cta']}"]
    if ch.get("keywords"):
        lines += [f"🔎 {', '.join(ch['keywords'])}"]
    if fixed:
        lines += ["", " ".join(fixed)]
    return youtube.clean_title(title), youtube.clean_desc("\n".join(lines))


async def _long_allowed() -> bool:
    """Kênh YouTube truyện bé tải được video dài hơn 15 phút chưa (đã xác minh số điện thoại). Lỗi: coi như chưa."""
    try:
        async with youtube._session() as s:
            return await youtube.long_uploads("kids", s) == "allowed"
    except Exception as e:
        logging.info("Tổng hợp tuần: không hỏi được YouTube giới hạn 15 phút (%s), coi như kênh chưa xác minh", str(e)[:120])
        return False


def _fq(p: str) -> str:
    """Đường dẫn trong bộ lọc ffmpeg (trong nháy đơn)."""
    return "'" + p.replace("\\", "/").replace("'", "'\\''") + "'"


async def thumbnail(frames: list[tuple[str, float]], out: str, text: str) -> str:
    """Ảnh bìa 1280x720: thẻ tiêu đề của tối đa 3 truyện (338x600) trên nền cam + dòng chữ. Lỗi: "" (YouTube tự chọn)."""
    txt = out + ".txt"
    try:
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        args: list[str] = ["-y", "-v", "error"]
        frames = frames[:3]
        for path, dur in frames:
            args += ["-ss", f"{min(THUMB_AT, max(0.0, dur / 2)):.2f}", "-i", path]
        args += ["-f", "lavfi", "-i", f"color=c={THUMB_BG}:s=1280x720:d=1"]
        n, w, h, gap, y = len(frames), 338, 600, 23, 106
        x0 = (1280 - n * w - (n - 1) * gap) // 2
        parts = [f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[f{i}]" for i in range(n)]
        cur = f"[{n}:v]"
        for i in range(n):
            parts.append(f"{cur}[f{i}]overlay={x0 + i * (w + gap)}:{y}[o{i}]")
            cur = f"[o{i}]"
        parts.append(f"{cur}drawtext=fontfile={_fq(ff.FONT_PATH)}:textfile={_fq(txt)}:expansion=none:fontsize=54:"
                     "fontcolor=white:borderw=5:bordercolor=0x2E263C:x=(w-text_w)/2:y=24[v]")
        await ff._run_ffmpeg(*args, "-filter_complex", ";".join(parts), "-map", "[v]", "-frames:v", "1", "-q:v", "3", out)
        return out if os.path.isfile(out) and os.path.getsize(out) > 0 else ""
    except Exception as e:
        logging.warning("Tổng hợp tuần: không làm được ảnh bìa (%s)", str(e)[:150])
        _remove(out)
        return ""
    finally:
        _remove(txt)


async def _build(now: float, when: float | None, wk: str, manual: bool = False) -> str:
    """Ghép các truyện chờ (Shorts lên YouTube trước giờ đăng) thành video tổng hợp, xếp lên YouTube. Trả câu báo."""
    from state import notify_admin
    st = load_state()
    cutoff = when if when is not None else now
    cands = [c for c in st["clips"] if float(c.get("publish") or 0) <= cutoff and os.path.isfile(str(c.get("path") or ""))]
    infos = [(c, info) for c in sorted(cands, key=lambda c: float(c["publish"])) for info in [await probe(c["path"])]
             if info and info["dur"] > 1.0]
    chosen, odd = pick(infos, max_clips())
    if len(chosen) < MIN_CLIPS:
        why = (f"mới có {len(chosen)} truyện" + (f" cùng khuôn ({odd} truyện khác khuôn)" if odd else "")
               + f", cần ít nhất {MIN_CLIPS}: dồn sang tuần sau")
        if not manual:
            st = load_state()
            st["weeks"][wk] = {"status": "skipped", "why": why, "at": round(now, 2)}
            save_state(st)
        return why
    capped = 0
    if sum(info["dur"] for _, info in chosen) > SHORT_MAX and not await _long_allowed():
        while len(chosen) > MIN_CLIPS and sum(info["dur"] for _, info in chosen) > SHORT_MAX:
            chosen.pop()                                       # truyện mới nhất dời sang tuần sau
            capped += 1
    clips, durs = [c for c, _ in chosen], [info["dur"] for _, info in chosen]
    k = max((int(m.get("k") or 0) for m in st["made"]), default=0) + 1    # «made» chỉ giữ KEEP_MADE mục: không đếm độ dài
    os.makedirs(LIB_DIR, exist_ok=True)
    out = os.path.join(LIB_DIR, f"tuyentap-{k}.mp4")
    lst = out + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines("file " + _fq(c["path"]) + "\n" for c in clips)
    try:
        await ff._run_ffmpeg("-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a:0?",
                             "-c", "copy", "-movflags", "+faststart", out)
        got = await probe(out)
        total = sum(durs)
        if got is None or abs(got["dur"] - total) > 1.0 + 0.1 * len(clips):
            raise RuntimeError(f"video ghép dài {got['dur'] if got else 0:.1f}s, tổng các truyện {total:.1f}s")
    except Exception as e:
        _remove(out)
        why = f"ghép lỗi: {str(e)[:200]}"
        logging.warning("Tổng hợp tuần: %s", why)
        if not manual:
            st = load_state()
            st["weeks"][wk] = {"status": "failed", "why": why, "at": round(now, 2)}
            save_state(st)
        await notify_admin(f"⚠️ Video tổng hợp tuần truyện bé: {why}")
        return why
    finally:
        _remove(lst)
    title, desc = texts(clips, durs, k)
    thumb = await thumbnail([(c["path"], d) for c, d in zip(clips, durs)], out[:-4] + ".jpg",
                            f"TUYỂN TẬP {k} · {len(clips)} TRUYỆN CHO BÉ")
    key = f"kids:tuyentap-{k}"
    label = f"video tổng hợp «Tuyển tập {k}»"
    queued = youtube.enqueue("kids", 0, out, title, desc, when, tags=youtube.video_tags("kids", desc), key=key, label=label,
                             thumb=thumb, long=True)
    used = {int(c["job"]) for c in clips}
    st = load_state()                                          # đọc lại: truyện mới có thể vừa vào kho trong lúc ghép
    st["clips"] = [c for c in st["clips"] if int(c.get("job") or 0) not in used]
    st["made"].append({"k": k, "key": key, "path": out, "thumb": thumb, "jobs": sorted(used), "n": len(clips),
                       "dur": round(sum(durs), 2), "title": title, "at": round(now, 2), "publish": when, "week": wk,
                       "queued": queued})
    if not manual:
        st["weeks"][wk] = {"status": "made", "k": k, "at": round(now, 2)}
    save_state(st)
    _remove(*(c["path"] for c in clips))                       # đã nằm trong video tổng hợp
    at = _at(when) if when and when > now + youtube.MIN_LEAD else "ngay khi tải lên xong"
    msg = (f"đã ghép {len(clips)} truyện thành «{title}» ({_mmss(sum(durs))})"
           + (f", hẹn YouTube kênh bé {at}" if queued else ", nhưng KHÔNG xếp được lên YouTube (kênh chưa kết nối?)")
           + (f"; {odd} truyện khác khuôn để lại" if odd else "")
           + (f"; {capped} truyện dời sang tuần sau vì kênh chưa xác minh số điện thoại (YouTube chỉ nhận video tối đa 15 "
              "phút — xác minh ở youtube.com/verify để ghép cả tuần và đặt ảnh bìa riêng)" if capped else ""))
    logging.info("Tổng hợp tuần: %s", msg)
    await notify_admin(f"📼 Truyện bé: {msg}.\nTruyện: " + ", ".join(f"#{c['job']}" for c in clips))
    return msg


# ---------------- vòng chạy ----------------

async def tick(now: float | None = None) -> str:
    """Một lượt (kids_auto.tick gọi mỗi 2 phút): lần đầu đưa truyện đã đăng gần đây vào kho; dọn; tới khung giờ ghép của
    tuần mà chưa làm thì ghép. Không có gì để làm: «»."""
    now = time.time() if now is None else now
    if enabled() or _LOCK.locked():
        return ""
    async with _LOCK:
        st = load_state()
        before = json.dumps(st, sort_keys=True)
        note = ""
        if not st["backfilled"]:
            n = backfill(st, now)
            st["backfilled"] = True
            if n:
                note = f"đưa {n} truyện đã đăng gần đây vào kho video tổng hợp tuần"
        cleanup(st, now)
        if json.dumps(st, sort_keys=True) != before:          # vòng 2 phút: chỉ ghi file khi có đổi
            save_state(st)
        t = due_slot(now)
        if t is None or week_key(t) in st["weeks"]:
            return note
        wk = week_key(t)
        try:
            msg = await _build(now, t, wk)
        except Exception as e:                    # lỗi lạ: ghi tuần lỗi (không thử lại mỗi 2 phút suốt khung ghép), báo admin
            msg = f"lỗi: {(str(e) or type(e).__name__)[:200]}"
            logging.error("Tổng hợp tuần %s lỗi", wk, exc_info=e)
            st = load_state()
            st["weeks"][wk] = {"status": "failed", "why": msg, "at": round(now, 2)}
            save_state(st)
            from state import notify_admin
            await notify_admin(f"⚠️ Video tổng hợp tuần truyện bé {msg}")
        return "; ".join(x for x in (note, msg) if x)


async def build_now(now: float | None = None) -> str:
    """Lệnh console kids_weekly_now: ghép ngay các truyện đã lên YouTube, đăng YouTube luôn (thử tính năng)."""
    now = time.time() if now is None else now
    why = enabled()
    if why:
        return f"Video tổng hợp tuần: {why}"
    if _LOCK.locked():
        return "Video tổng hợp tuần: đang ghép, chờ chút"
    async with _LOCK:
        st = load_state()
        cleanup(st, now)
        save_state(st)
        return "Video tổng hợp tuần: " + await _build(now, None, "", manual=True)


def status_lines(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    why = enabled()
    if why:
        return [f"📼 Video tổng hợp tuần: {why}"]
    st = load_state()
    waiting = [c for c in st["clips"] if os.path.isfile(str(c.get("path") or ""))]
    nxt = next_slot(now)
    line = f"📼 Video tổng hợp tuần: {len(waiting)} truyện chờ ghép" + (f", lần tới {_at(nxt)}" if nxt else "")
    if st["made"]:
        m = st["made"][-1]
        line += f" | gần nhất: tuyển tập {m['k']} ({m['n']} truyện, {_mmss(float(m['dur']))}) ghép lúc {_at(float(m['at']))}"
    return [line]
