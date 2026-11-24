"""(Botkeep) Tạo kho nhạc Lyria trên Kaggle (CPU) thay vì trên Botkeep.

Đo thật 24/11/2026 (job 2 truyện bé): trên Botkeep mỗi phiên Lyria chỉ nhận ~4 giây nhạc trong 51 giây rồi bị cắt
(«1006 abnormal closure»). Luồng Lyria là PCM 48 kHz stereo (~190 KB/s, gửi dạng base64 còn nặng hơn) mà mạng Botkeep chỉ
~80-100 KB/s. Mạng Kaggle nhanh: bot gửi notebook riêng `rmvideo-music` (CPU, không tốn giờ GPU) tạo các bài còn thiếu
của kho bé + kho bếp, tải m4a về kho trên Botkeep (assets/kids_bgm/lyria, assets/bgm_lyria), ghi library.json rồi đẩy
dataset như cũ (rmvideo-kids-bgm, rmvideo-cook-bgm).

KHÁC các notebook khác: notebook này CHỨA KEY GEMINI (trong mã, dạng base64 — ai mở được notebook là đọc được). Notebook
riêng tư; người dùng chọn cách này ngày 24/11 (cách 1). Chạy khi kho thiếu bài mà Lyria trên Botkeep quá chậm (tự động,
tối đa một lần mỗi RETRY_AFTER giây) hoặc bằng lệnh console bgm_kaggle."""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from typing import Any

import config
from rmvideo.media import lyria

SLUG = "rmvideo-music"
BUDGET = 90 * 60              # giờ chạy tối đa của notebook (bé 9 x ~110 giây + bếp 9 x ~160 giây ≈ 41 phút + cài thư viện)
QUEUE_MAX = 45 * 60           # CPU hiếm khi phải chờ máy; chờ quá chừng này thì thôi, lần sau thử lại
TIMEOUT = QUEUE_MAX + BUDGET + 15 * 60     # bot chờ cả phiên: chờ máy + chạy + dự phòng (notebook tự dừng ở BUDGET)
TRACK_EXTRA = 120             # giây dự phòng mỗi bài ngoài độ dài bài (bắt tay, nối phiên)
RETRY_AFTER = 3 * 3600        # lần chạy tự động trước lỗi / thiếu: chừng này giây sau mới tự chạy lại
PROVEN_DAYS = 14              # Kaggle đã tạo được nhạc trong chừng này ngày: kho thiếu thì nhờ Kaggle, khỏi thử Lyria trên Botkeep
STATE_FILE = os.environ.get("BGM_KAGGLE_STATE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "bgm_kaggle.json")
PIP = "pip install -q 'google-genai>=3.0,<4'"     # cùng đời với Botkeep (3.6.1 nối được Lyria)
_RUN: dict[str, Any] = {"task": None}
_LAST: dict[str, int] = {"ok": 0}
Log = Callable[[str], Any]


def libs() -> dict[str, lyria.Lib]:
    """Các kho cần nhạc, theo thứ tự ưu tiên (bé trước: video bé hiện không có nhạc)."""
    import bgm
    from rmvideo.kids import bgm as kids_bgm
    return {"kids": kids_bgm._lib(), "cook": bgm.lyria_lib()}


def missing(all_libs: dict[str, lyria.Lib] | None = None) -> list[dict]:
    """Các bài còn thiếu: [{lib, mood, file, remote, prompt, bpm, density, brightness, secs, fade_out}]. Xen kẽ các màu
    (playful_1, curious_1, tender_1, playful_2...) để notebook hết giờ giữa chừng thì màu nào cũng đã có bài."""
    out: list[dict] = []
    for key, lib in (all_libs or libs()).items():
        per = {mood: lyria.next_names(lib.dir, mood, max(0, lib.per_mood - len(lyria.tracks(lyria.load_library(lib.dir), mood))))
               for mood in lib.moods}
        for i in range(max((len(v) for v in per.values()), default=0)):
            for mood, names in per.items():
                if i < len(names):
                    m = lib.moods[mood]
                    out.append({"lib": key, "mood": mood, "file": names[i], "remote": f"{key}__{names[i]}",
                                "prompt": m["prompt"], "bpm": m["bpm"], "density": m["density"],
                                "brightness": m["brightness"], "vi": m.get("vi", mood), "secs": float(lib.track_secs),
                                "fade_out": float(lib.fade_out)})
    return out


KERNEL = r'''# rm-video: tạo kho nhạc nền bằng Lyria RealTime (bot tạo, chạy trên CPU, đừng sửa tay).
# CHỨA KEY GEMINI (trong SPEC). Giữ notebook này ở chế độ riêng tư.
import asyncio, base64, json, os, subprocess, sys, time, types, wave
SPEC = json.loads(base64.b64decode("__SPEC__").decode())
OUT = os.environ.get("RMV_MUSIC_OUT") or "/kaggle/working"
if __name__ == "__main__":
    if time.time() > float(SPEC.get("expires_at") or 0) > 0:
        print("rm-video: bot đã thôi chờ việc này, thoát")
        sys.exit(0)
    r = subprocess.run(__PIP__, shell=True, capture_output=True, text=True)
    print(r.stdout[-800:], r.stderr[-800:])
cfg = types.ModuleType("config")
cfg.GEMINI_API_KEY = SPEC.pop("key")
sys.modules["config"] = cfg
ly = types.ModuleType("lyria")
sys.modules["lyria"] = ly
exec(compile(base64.b64decode("__LYRIA__").decode(), "lyria.py", "exec"), ly.__dict__)
ly.SLOW_RATE = 0.0            # trên Kaggle không có đường lùi: phiên chậm / đứt thì cứ nối phiên mới, không «nghỉ»
res = {"token": SPEC["token"], "tracks": [], "errors": []}


def save():
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


async def main(cl):
    end = time.time() + SPEC["budget"]
    for t in SPEC["tracks"]:
        if time.time() + t["secs"] + 30 > end:
            res["errors"].append(t["remote"] + ": hết giờ của phiên")
            continue
        t0 = time.time()
        try:
            pcm = await ly.stream(t, t["secs"] + 2, cl, print, timeout=t["secs"] + __EXTRA__, stall=30, tag=t["lib"])
            secs = min(t["secs"], len(pcm) / (ly.RATE * ly.CH * 2))
            raw = os.path.join(OUT, t["remote"] + ".wav")
            with wave.open(raw, "wb") as w:
                w.setnchannels(ly.CH)
                w.setsampwidth(2)
                w.setframerate(ly.RATE)
                w.writeframes(pcm)
            p = subprocess.run(["ffmpeg", "-v", "error"] + ly.encode_args(raw, os.path.join(OUT, t["remote"]), secs,
                                                                           t["fade_out"]), capture_output=True, text=True)
            os.remove(raw)
            if p.returncode:
                raise RuntimeError("ffmpeg: " + p.stderr[-300:])
            res["tracks"].append({"remote": t["remote"], "secs": round(secs, 1), "took": round(time.time() - t0)})
            print(f"xong {t['remote']}: {secs:.0f} giây nhạc trong {time.time() - t0:.0f} giây", flush=True)
        except Exception as e:
            res["errors"].append(f"{t['remote']}: {type(e).__name__} {str(e)[:200]}")
            print("lỗi", res["errors"][-1], flush=True)
        save()


if __name__ == "__main__":
    save()
    asyncio.run(main(ly.client()))
    save()
    print("rm-video: xong", len(res["tracks"]), "bài,", len(res["errors"]), "lỗi")
'''


def kernel_text(spec: dict) -> str:
    """Mã notebook: SPEC (gồm key) + mã nguồn lyria.py (dùng lại đúng hàm stream / encode_args đã kiểm ở Botkeep)."""
    src = inspect.getsource(lyria)
    return (KERNEL.replace("__SPEC__", base64.b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode())
            .replace("__LYRIA__", base64.b64encode(src.encode()).decode())
            .replace("__PIP__", repr(PIP)).replace("__EXTRA__", str(TRACK_EXTRA)))


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def run_sync(log: Log = logging.info) -> str:
    """Gửi notebook tạo các bài còn thiếu, chờ xong, tải về kho, đẩy dataset. Trả tóm tắt."""
    import kaggle_api
    import kaggle_worker
    if not kaggle_api.configured():
        raise RuntimeError("chưa cài token Kaggle")
    if not config.GEMINI_API_KEY:
        raise RuntimeError("chưa có GEMINI_API_KEY (Lyria cần key Gemini)")
    all_libs = libs()
    todo = missing(all_libs)
    if not todo:
        return "kho nhạc bé và bếp đã đủ bài"
    spec = {"key": config.GEMINI_API_KEY, "token": secrets.token_hex(8), "budget": BUDGET,
            "expires_at": round(time.time() + QUEUE_MAX - 30),
            "tracks": [{k: v for k, v in t.items() if k not in ("file",)} for t in todo]}
    log(f"Nhạc Kaggle: gửi notebook {SLUG} (CPU) tạo {len(todo)} bài "
        f"(~{sum(t['secs'] * 1.8 + 30 for t in todo) / 60:.0f} phút)")      # đo 24/11: 17 bài 100-150 giây hết 68 phút
    kaggle_api.push(SLUG, "rmvideo music", kernel_text(spec), gpu=False)

    def st(s: str, secs: float) -> None:
        log(f"Nhạc Kaggle: {'đang chờ máy' if 'QUEUED' in s.upper() else 'đang tạo nhạc'} ({secs / 60:.0f} phút)")
    kaggle_api.wait(SLUG, TIMEOUT, every=30, on_status=st, queue_max=QUEUE_MAX)
    files = {f.get("fileName"): f.get("url") for f in kaggle_api.outputs(SLUG).get("files", []) if f.get("fileName")}
    if "result.json" not in files:
        raise RuntimeError("notebook nhạc không trả result.json")
    with tempfile.TemporaryDirectory() as work:
        rj = os.path.join(work, "music_result.json")
        kaggle_api.download(files["result.json"], rj, 1 << 20,
                            refresh=lambda: kaggle_worker.fresh_url(SLUG, "result.json"))
        res = json.load(open(rj, encoding="utf-8"))
    if res.get("token") != spec["token"]:
        raise RuntimeError("kết quả notebook nhạc không phải của lần gửi này")
    by_remote = {t["remote"]: t for t in todo}
    got: dict[str, int] = {}
    for r in res.get("tracks") or []:
        t = by_remote.get(r.get("remote"))
        if not t or r["remote"] not in files:
            continue
        lib = all_libs[t["lib"]]
        os.makedirs(lib.dir, exist_ok=True)
        dst = os.path.join(lib.dir, t["file"])
        try:
            kaggle_api.download(files[r["remote"]], dst, 30 << 20,
                                refresh=lambda name=r["remote"]: kaggle_worker.fresh_url(SLUG, name))
        except Exception as e:
            log(f"Nhạc Kaggle: không tải được {r['remote']} ({str(e)[:120]})")
            continue
        lyria.add_track(lib.dir, t["file"], t["mood"], float(r.get("secs") or t["secs"]), t["prompt"], via="kaggle")
        got[t["lib"]] = got.get(t["lib"], 0) + 1
    errs = res.get("errors") or []
    for msg in errs[:5]:
        log(f"Nhạc Kaggle: lỗi {msg[:160]}")
    for key in got:
        lib = all_libs[key]
        if lyria.changed_since_push(lyria.load_library(lib.dir)):
            try:
                lyria.push_dataset_sync(lib, log)
            except Exception as e:
                log(f"Nhạc Kaggle: không đẩy được kho {key} lên Kaggle ({str(e)[:120]}), lần dựng sau thử lại")
    left = len(missing(all_libs))
    _LAST["ok"] = sum(got.values())
    return (f"Nhạc Kaggle: thêm {sum(got.values())}/{len(todo)} bài (bé {got.get('kids', 0)}, bếp {got.get('cook', 0)})"
            + (f", còn thiếu {left} bài" if left else ", kho đã đủ") + (f", {len(errs)} lỗi" if errs else ""))


def proven() -> bool:
    """Kaggle đã tạo được ít nhất một bài trong PROVEN_DAYS ngày: kho thiếu thì nhờ Kaggle luôn (start_later), khỏi mất
    ~1 phút thử Lyria trên Botkeep mỗi job (mạng Botkeep không theo kịp, đo 24/11)."""
    return time.time() - float(load_state().get("proven_at") or 0) < PROVEN_DAYS * 86400


def running() -> bool:
    t = _RUN.get("task")
    return t is not None and not t.done()


def start_later(log: Log | None = None, force: bool = False, why: str = "") -> str:
    """Chạy run_sync trong nền (không chặn job). Tự động (force=False): chỉ khi kho thiếu bài, Kaggle + key có sẵn và
    lần tự chạy trước đã qua RETRY_AFTER giây. Trả câu báo ("" khi không chạy)."""
    import kaggle_api
    log = log or (lambda m: logging.info("🎵 %s", m))
    if running():
        return "notebook nhạc trên Kaggle đang chạy, chờ xong đã"
    if not kaggle_api.configured() or not config.GEMINI_API_KEY:
        return "" if not force else "cần token Kaggle và GEMINI_API_KEY"
    if not missing():
        return "" if not force else "kho nhạc bé và bếp đã đủ bài"
    st = load_state()
    if not force and time.time() - float(st.get("at") or 0) < RETRY_AFTER:
        return ""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return ""
    save_state({"at": time.time(), "status": "running", "why": why, "proven_at": load_state().get("proven_at")})

    async def run() -> None:
        try:
            msg = await asyncio.to_thread(run_sync, log)
            save_state({"at": time.time(), "status": "done", "msg": msg, "ok": _LAST["ok"],
                        "proven_at": time.time() if _LAST["ok"] else load_state().get("proven_at")})
            log(msg)
        except Exception as e:
            save_state({"at": time.time(), "status": "error", "msg": str(e)[:300], "proven_at": load_state().get("proven_at")})
            log(f"❌ Nhạc Kaggle lỗi: {str(e)[:200]}")
    _RUN["task"] = loop.create_task(run())
    return (f"gửi notebook nhạc lên Kaggle (CPU) tạo {len(missing())} bài còn thiếu" + (f" — {why}" if why else "")
            + "; xong (~45-60 phút) tự tải về kho, các video sau có nhạc")
