"""Bộ video mẫu (golden set): chạy lại bot trên 5-10 video THẬT của bạn và so với lần chuẩn, để mỗi lần sửa code
(hoặc đổi model, đổi giọng, cập nhật yt-dlp...) biết ngay chất lượng có tụt không.

Cách dùng:
  1. Chép 5-10 video nguồn vào golden/videos/ (mp4, mov...). Tuỳ chọn golden/manifest.json để ghi chế độ, tiêu đề:
       {"thit_kho.mp4": {"mode": "cook", "title": "Thịt kho tàu"}, "quan_oc.mp4": {"mode": "review"}}
     (mặc định: chế độ nấu ăn, tiêu đề = tên file).
  2. python golden.py run          chạy cả bộ -> golden/results/<thời gian>.json + golden/report.md
     python golden.py baseline     lấy lần chạy gần nhất làm CHUẨN (golden/baseline.json)
     python golden.py compare      so lần chạy gần nhất với chuẩn (in bảng + ghi golden/report.md)
     Tuỳ chọn: --only tên1,tên2 (chạy vài video), --keep (giữ video ra trong golden/out/).
  Trên Botkeep: gõ `golden` (hoặc `golden baseline`) vào console: chạy nền, ưu tiên CPU thấp, chỉ khi hàng đợi trống.

Đo gì (mỗi video): chạy có lỗi không; thời gian từng giai đoạn; âm lượng (LUFS, đỉnh, LRA); độ nét, độ rực màu, khung
đen của video ra; kịch bản: nguyên liệu/con số bị mất hoặc bịa, mức chép lời gốc, đoạn phải nói nhanh, đoạn vẫn dài;
phụ đề: số dòng, dòng dài nhất. So với chuẩn theo ngưỡng (xem RULES); có ngưỡng tuyệt đối (vd âm lượng -14±0.7 LUFS)
áp dụng cả khi chưa có chuẩn. AI và giọng đọc dùng dịch vụ thật nên mỗi lần chạy hơi khác nhau: ngưỡng đã tính tới.
"""
import argparse
import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.environ.get("GOLDEN_DIR") or os.path.join(BASE, "golden")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")

# Ngưỡng từng chỉ số. abs/abs_warn: tuyệt đối (không cần chuẩn); warn/fail: chênh so với chuẩn (âm = giảm);
# warn_ratio / warn_ratio_up: tỉ lệ so với chuẩn; warn_abs: lệch tuyệt đối; fail_more: tăng so với chuẩn là lỗi
RULES = {
    "lufs": {"abs": lambda v: abs(v + 14) <= 0.7, "why": "âm lượng phải -14±0.7 LUFS"},
    "true_peak": {"abs": lambda v: v <= -0.8, "why": "đỉnh phải ≤ -0.8 dBTP (méo tiếng)"},
    "black_ratio": {"abs": lambda v: v <= 0.1, "why": "quá 10% khung đen"},
    "sharp": {"warn": -0.15, "fail": -0.35, "why": "độ nét giảm (log phương sai Laplacian)"},
    "color": {"warn_ratio": 0.9, "why": "độ rực màu giảm > 10%"},
    "duration": {"warn_abs": 1.5, "why": "thời lượng lệch > 1.5s"},
    "ingredient_errors": {"fail_more": True, "why": "nguyên liệu/con số mất hoặc bịa nhiều hơn chuẩn"},
    "copy_ratio": {"warn": 0.1, "why": "lời mới chép lời gốc nhiều hơn"},
    "max_speed": {"abs_warn": lambda v: v <= 1.3, "why": "có đoạn phải nói nhanh > 1.3x"},
    "too_long": {"fail_more": True, "why": "nhiều đoạn giọng vẫn quá dài hơn chuẩn"},
    "seconds": {"warn_ratio_up": 1.25, "why": "chậm hơn chuẩn > 25%"},
}


def _load(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def list_videos(only: str = "") -> list:
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(GOLDEN, "videos", "*"))
                   if p.lower().endswith(VIDEO_EXT))
    want = {x.strip() for x in only.split(",") if x.strip()}
    return [n for n in names if not want or n in want or os.path.splitext(n)[0] in want]


# ---------- đo ----------

def audio_metrics(path: str) -> dict:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af",
                          "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], capture_output=True, text=True).stderr

    def last(rx):
        m = re.findall(rx, err)
        return float(m[-1]) if m and m[-1] not in ("-inf", "inf") else None
    return {"lufs": last(r"I:\s*(-?[\d.]+|-inf) LUFS"), "lra": last(r"LRA:\s*([\d.]+) LU"),
            "true_peak": last(r"Peak:\s*(-?[\d.]+|-inf) dBFS")}


def video_metrics(path: str, n: int = 12) -> dict:
    import numpy as np

    import cover
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate:format=duration,size", "-of", "json", path],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    st, fmt = (d.get("streams") or [{}])[0], d.get("format") or {}
    dur = float(fmt.get("duration") or 0)
    a, b = (st.get("avg_frame_rate") or "0/1").split("/")
    fps = float(a) / float(b) if float(b or 0) else 0.0
    frames = cover._raw_frames(["-i", path, "-vf", f"fps={n / max(dur, 1):.4f}"]) if dur else np.zeros((0,))
    m = cover.frame_metrics(frames) if len(frames) else {"sharp": np.zeros(1), "color": np.zeros(1), "mean": np.ones(1)}
    return {"duration": round(dur, 2), "width": st.get("width"), "height": st.get("height"), "fps": round(fps, 2),
            "mbps": round(float(fmt.get("size") or 0) * 8 / max(dur, 1) / 1e6, 2),
            "sharp": round(float(np.mean(m["sharp"])), 3), "color": round(float(np.mean(m["color"])), 4),
            "black_ratio": round(float(np.mean(m["mean"] < 0.06)), 3)}


def script_metrics(job) -> dict:
    """Từ timeline cuối của job: lỗi nguyên liệu/con số, mức chép lời gốc, tốc độ đọc, phụ đề."""
    import ingredients
    import prompt_engine
    segs = job.final_segments or []
    spoken = [s for s in segs if s.get("ost") != "orig" and s.get("text")]
    orig_all = " ".join(s.get("orig", "") for s in segs)
    new_all = " ".join((s.get("orig", "") if s.get("ost") == "orig" else s.get("text", "")) for s in segs)
    miss, added = ingredients.check(orig_all, new_all)
    nmiss, nadded = ingredients.check_numbers(orig_all, new_all)
    speeds = [s["voice"]["speed"] for s in segs if s.get("voice")]
    srt_lines = [ln for ln in (job.srt or "").splitlines() if ln and not ln.isdigit() and "-->" not in ln]
    m = re.search(r"(\d+) đoạn vẫn hơi dài", job.error or "")
    return {"segments": len(segs), "spoken": len(spoken),
            "ingredient_errors": len(miss) + len(added) + len(nmiss) + len(nadded),
            "ingredient_detail": {"thiếu": miss + nmiss, "bịa": added + nadded},
            "copy_ratio": round(sum(prompt_engine.copy_ratio(s["text"], s.get("orig", "")) for s in spoken)
                                / max(1, len(spoken)), 3),
            "max_speed": round(max(speeds), 3) if speeds else 1.0,
            "fast_segments": sum(1 for v in speeds if v > 1.12),
            "too_long": int(m.group(1)) if m else 0,
            "sub_lines": len(srt_lines), "sub_max_chars": max((len(x) for x in srt_lines), default=0)}


def strip_image(video: str, dst: str):
    """4 khung trải từ 20% tới cuối video, ghép ngang, cao 320px: xem nhanh bằng mắt."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                         capture_output=True, text=True).stdout.strip()
    d = float(out or 0)
    if d <= 0:
        return
    fps = 4 / (d * 0.8)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{d * 0.2:.2f}", "-i", video, "-vf",
                    f"fps={fps:.4f},scale=-2:320,tile=4x1", "-frames:v", "1", "-q:v", "4", dst], check=False)


# ---------- chạy ----------

async def run_one(name: str, meta: dict, keep: bool, out_dir: str) -> dict:
    import config
    import cover
    import pipeline
    from queue_manager import Job, phase_times
    src = os.path.join(GOLDEN, "videos", name)
    job = Job(url="", mode=meta.get("mode", "cook"), duration=int(meta.get("duration", 0)),
              voice=meta.get("voice") or (config.CAPCUT_VOICE_FEMALE if config.TTS_PROVIDER == "capcut"
                                         else config.VOICE_FEMALE),
              local=src, title_hint=meta.get("title") or os.path.splitext(name)[0], source="golden")
    timeline, t0 = [], time.time()

    async def watch():
        last = None
        while True:
            if job.step != last:
                last = job.step
                timeline.append((job.step, round(time.time() - t0, 1)))
            await asyncio.sleep(0.5)
    w = asyncio.create_task(watch())
    res = {"name": name, "mode": job.mode, "ok": False}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = await pipeline.process(job, tmp, allow_review=False)
            res["ok"] = True
        except Exception as e:
            res["error"] = str(e)[:300]
            out = None
        finally:
            w.cancel()
        res["seconds"] = round(time.time() - t0, 1)
        res["phases"] = phase_times(timeline, res["seconds"])
        if out and os.path.exists(out):
            res.update(await asyncio.to_thread(audio_metrics, out))
            res.update(await asyncio.to_thread(video_metrics, out))
            res.update(script_metrics(job))
            res["notes"] = (job.error or "")[:400]
            stem = os.path.splitext(name)[0]
            await asyncio.to_thread(strip_image, out, os.path.join(out_dir, f"{stem}_khung.jpg"))
            cov = os.path.join(tmp, cover.COVER_NAME)
            if os.path.exists(cov):
                shutil.copyfile(cov, os.path.join(out_dir, f"{stem}_bia.jpg"))
            if keep:
                shutil.copyfile(out, os.path.join(out_dir, f"{stem}.mp4"))
    return res


async def run_all(only: str = "", keep: bool = False, log=print) -> str:
    vids = list_videos(only)
    if not vids:
        raise SystemExit(f"Chưa có video mẫu: chép 5-10 video vào {os.path.join(GOLDEN, 'videos')}")
    manifest = _load(os.path.join(GOLDEN, "manifest.json"), {}) or {}
    out_dir = os.path.join(GOLDEN, "out")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(GOLDEN, "results"), exist_ok=True)
    results = []
    for k, name in enumerate(vids, 1):
        log(f"[golden] {k}/{len(vids)} {name} ...")
        r = await run_one(name, manifest.get(name) or manifest.get(os.path.splitext(name)[0]) or {}, keep, out_dir)
        log(f"[golden]   {'OK' if r['ok'] else 'LỖI'} {r['seconds']:.0f}s"
            + (f" | {r.get('lufs')} LUFS | nét {r.get('sharp')} | lỗi nguyên liệu {r.get('ingredient_errors')}"
               if r["ok"] else f" | {r.get('error')}"))
        results.append(r)
    path = os.path.join(GOLDEN, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"at": time.time(), "results": results}, f, ensure_ascii=False, indent=1)
    return path


# ---------- so sánh ----------

def compare(cur: list, base: list | None) -> list:
    """[(video, chỉ số, chuẩn, hiện tại, 'ok'|'warn'|'fail', lý do)]. Chỉ liệt kê dòng có vấn đề + dòng ok tóm tắt."""
    bmap = {r["name"]: r for r in (base or [])}
    rows = []
    for r in cur:
        b = bmap.get(r["name"])
        if not r.get("ok"):
            rows.append((r["name"], "chạy", "OK" if (b or {}).get("ok") else "-", "LỖI", "fail", r.get("error", "")))
            continue
        bad = []
        for key, rule in RULES.items():
            v = r.get(key)
            if v is None:
                continue
            bv = (b or {}).get(key)
            if "abs" in rule and not rule["abs"](v):
                bad.append((key, bv, v, "fail", rule["why"]))
            elif "abs_warn" in rule and not rule["abs_warn"](v):
                bad.append((key, bv, v, "warn", rule["why"]))
            if bv is None or not b.get("ok"):
                continue
            if "fail" in rule and v - bv < rule["fail"]:
                bad.append((key, bv, v, "fail", rule["why"]))
            elif "warn" in rule and (v - bv < rule["warn"] if rule["warn"] < 0 else v - bv > rule["warn"]):
                bad.append((key, bv, v, "warn", rule["why"]))
            if "warn_ratio" in rule and bv > 0 and v < bv * rule["warn_ratio"]:
                bad.append((key, bv, v, "warn", rule["why"]))
            if "warn_ratio_up" in rule and bv > 0 and v > bv * rule["warn_ratio_up"]:
                bad.append((key, bv, v, "warn", rule["why"]))
            if "warn_abs" in rule and abs(v - bv) > rule["warn_abs"]:
                bad.append((key, bv, v, "warn", rule["why"]))
            if rule.get("fail_more") and v > bv:
                bad.append((key, bv, v, "fail", rule["why"]))
        if bad:
            rows += [(r["name"], *x) for x in bad]
        else:
            rows.append((r["name"], "tất cả", "", "", "ok", "không tụt so với chuẩn" if b else "đạt ngưỡng tuyệt đối"))
    return rows


def report(res_path: str, base_path: str) -> tuple:
    """Ghi golden/report.md. Trả (nội dung, số dòng lỗi)."""
    cur = (_load(res_path, {}) or {}).get("results", [])
    base = (_load(base_path, {}) or {}).get("results") if os.path.exists(base_path) else None
    rows = compare(cur, base)
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    lines = [f"# Bộ video mẫu — {os.path.basename(res_path)}",
             "", f"So với chuẩn: {os.path.basename(base_path) if base else '(chưa có — chạy `golden.py baseline`)'}", "",
             "| Video | Chạy | Thời gian | LUFS | Đỉnh | Nét | Màu | Lỗi nguyên liệu | Chép lời | Nói nhanh nhất |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in cur:
        lines.append(f"| {r['name']} | {'OK' if r.get('ok') else 'LỖI'} | {r.get('seconds', 0):.0f}s | {r.get('lufs', '-')} | "
                     f"{r.get('true_peak', '-')} | {r.get('sharp', '-')} | {r.get('color', '-')} | "
                     f"{r.get('ingredient_errors', '-')} | {r.get('copy_ratio', '-')} | {r.get('max_speed', '-')} |")
    lines += ["", "| Video | Chỉ số | Chuẩn | Lần này | Kết quả | Ghi chú |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {n} | {k} | {bv if bv is not None else ''} | {v} | {icon[st]} | {why} |" for n, k, bv, v, st, why in rows]
    lines += ["", "Ảnh bìa và 4 khung của mỗi video ra: thư mục golden/out/ (`<tên>_bia.jpg`, `<tên>_khung.jpg`)."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(GOLDEN, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text, sum(1 for x in rows if x[4] == "fail")


def latest_result() -> str | None:
    files = sorted(glob.glob(os.path.join(GOLDEN, "results", "*.json")))
    return files[-1] if files else None


def main(argv=None):
    sys.path.insert(0, BASE)
    import core_video  # noqa: F401  (nạp ffmpeg đóng gói vào PATH như khi bot chạy)
    ap = argparse.ArgumentParser(description="Bộ video mẫu: chạy lại và so chất lượng với lần chuẩn")
    ap.add_argument("cmd", choices=["run", "baseline", "compare"], nargs="?", default="run")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    base_path = os.path.join(GOLDEN, "baseline.json")
    if a.cmd == "baseline":
        src = latest_result()
        if not src:
            raise SystemExit("Chưa có lần chạy nào: chạy `python golden.py run` trước")
        shutil.copyfile(src, base_path)
        print(f"Đã lấy {os.path.basename(src)} làm chuẩn")
        return 0
    res = asyncio.run(run_all(a.only, a.keep)) if a.cmd == "run" else latest_result()
    if not res:
        raise SystemExit("Chưa có lần chạy nào")
    text, fails = report(res, base_path)
    print(text)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
