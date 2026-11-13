"""Tách video thành các cảnh (điểm chuyển cảnh) bằng bộ dò scene của FFmpeg.
Dùng để: (1) căn câu thoại mới bắt đầu đúng lúc chuyển cảnh -> khớp hình hơn cả bản gốc;
(2) video không có lời gốc: mỗi cảnh là một đoạn, AI nhìn ảnh từng cảnh để viết lời."""
import asyncio
import re
import subprocess


_ANALYZED: dict = {}      # path -> {"dir": thư mục ảnh thu nhỏ, "n": số ảnh, "times": mốc từng ảnh (chế độ nhẹ)}
THUMB_FPS = 2


def mode() -> str:
    """SCENE_ANALYSIS: light | fast | full (xem config)."""
    import config
    m = (getattr(config, "SCENE_ANALYSIS", "") or "light").strip()
    return m if m in ("light", "fast", "full") else "light"


def cut_mode() -> str:
    """Việc CẦN điểm chuyển cảnh (video không lời, cắt phần, đoạn hay): chế độ nhẹ thì dò ở mức vừa."""
    return "full" if mode() == "full" else "fast"


def _decode_opts(m: str) -> list:
    """Mức "vừa": bỏ khung không tham chiếu (B) + bỏ lọc khử khối khi giải mã. Đo video 220s 720p, 1 luồng: 26.2s ->
    14.2s, vẫn dò được 11/12 điểm chuyển cảnh (lệch < 0.1s). Botkeep cùng video: ~380s."""
    return ["-skip_frame", "noref", "-skip_loop_filter", "all", "-flags2", "fast"] if m == "fast" else []


def _parse_cuts(err: str, min_gap: float = 0.8) -> list[float]:
    cuts = []
    for t in (float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)):
        if t < 0.3:
            continue
        if not cuts or t - cuts[-1] >= min_gap:
            cuts.append(round(t, 2))
    return cuts


def _light_sync(path: str, tmpdir: str, dur: float, want_gray: bool):
    """Mức "nhẹ": chỉ giải mã KHUNG CHÍNH (Facebook/TikTok ~1 khung mỗi 2-5 giây; video 220s: 44 khung, ~1/150 công
    giải mã) cho ảnh AI nhìn + ảnh bìa + dò chữ in cứng. Không dò điểm chuyển cảnh. Khung chính quá thưa (video mã hoá
    lạ) thì trả None để làm mức vừa."""
    import os
    import textzone
    tdir = os.path.join(tmpdir, "thumbs_kf")
    os.makedirs(tdir, exist_ok=True)
    gray = os.path.join(tmpdir, "gray_kf.raw")
    fc = ("[0:v]showinfo,split=2[t][g];[t]scale=384:-2[to];"
          f"[g]scale={textzone._W}:{textzone._H},format=gray[go]") if want_gray else "[0:v]showinfo,scale=384:-2[to]"
    outs = ["-map", "[to]", "-fps_mode", "passthrough", "-q:v", "5", os.path.join(tdir, "%05d.jpg")]
    if want_gray:
        outs += ["-map", "[go]", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-y", gray]
    import core_video
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-y", "-skip_frame", "nokey", "-i", path, "-an",
                             "-filter_complex", fc] + outs, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    core_video.RUNNING.add(proc)
    try:
        err = proc.communicate()[1]
    finally:
        core_video.RUNNING.discard(proc)
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]
    n = len(os.listdir(tdir))
    if n < max(4, min(12, dur / 10)) or len(times) < n:
        return None
    _ANALYZED[path] = {"dir": tdir, "n": n, "times": times[:n]}
    while len(_ANALYZED) > 4:
        _ANALYZED.pop(next(iter(_ANALYZED)))
    bands = []
    if want_gray and os.path.exists(gray):
        bands = textzone.bands_from_raw(open(gray, "rb").read())
        os.remove(gray)
    return [], bands


def _analyze_sync(path: str, tmpdir: str, dur: float, want_gray: bool, threshold: float = 0.3, m: str = ""):
    """MỘT lượt giải mã cho cả 3 việc (trước đây là 2 lượt giải mã + 1 lần tua cho mỗi ảnh):
    điểm chuyển cảnh, ảnh thu nhỏ 2 khung/giây cho AI nhìn, khung xám cho dò chữ in cứng.
    m (SCENE_ANALYSIS): light = chỉ khung chính, không dò chuyển cảnh; fast = bỏ khung phụ khi giải mã; full = mọi khung."""
    import os
    import textzone
    m = m or mode()
    if m == "light":
        got = _light_sync(path, tmpdir, dur, want_gray)
        if got is not None:
            return got
        m = "fast"
    tdir = os.path.join(tmpdir, "thumbs")
    os.makedirs(tdir, exist_ok=True)
    gray = os.path.join(tmpdir, "gray.raw")
    fc = (f"[0:v]split={3 if want_gray else 2}[s][t]{'[g]' if want_gray else ''};"
          f"[s]scale=320:-2,select='gt(scene,{threshold})',showinfo[so];"     # y hệt detect_cuts cũ
          f"[t]fps={THUMB_FPS},scale=384:-2[to]")                              # bớt khung TRƯỚC rồi mới thu nhỏ
    outs = ["-map", "[so]", "-f", "null", "-",
            "-map", "[to]", "-q:v", "5", os.path.join(tdir, "%05d.jpg")]
    if want_gray:
        fc += f";[g]fps={textzone._N / max(dur, 1):.5f},scale={textzone._W}:{textzone._H},format=gray[go]"  # như cũ
        outs += ["-map", "[go]", "-f", "rawvideo", "-pix_fmt", "gray", "-y", gray]
    import core_video
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-y", *_decode_opts(m), "-i", path, "-an", "-filter_complex", fc]
                            + outs, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    core_video.RUNNING.add(proc)                  # bấm Hủy / quá hạn là giết được ngay
    try:
        err = proc.communicate()[1]
    finally:
        core_video.RUNNING.discard(proc)
    _ANALYZED[path] = {"dir": tdir, "n": len(os.listdir(tdir))}
    while len(_ANALYZED) > 4:
        _ANALYZED.pop(next(iter(_ANALYZED)))
    bands = []
    if want_gray and os.path.exists(gray):
        bands = textzone.bands_from_raw(open(gray, "rb").read())
        os.remove(gray)
    return _parse_cuts(err), bands


async def analyze(path: str, tmpdir: str, dur: float, want_gray: bool, m: str = ""):
    """Trả (cuts, bands). Lỗi thì quay về cách cũ từng việc riêng."""
    try:
        return await asyncio.to_thread(_analyze_sync, path, tmpdir, dur, want_gray, 0.3, m)
    except Exception:
        import textzone
        return await detect_cuts(path), (await textzone.detect_text_bands(path) if want_gray else [])


async def analyze_cached(path: str, tmpdir: str, dur: float, want_gray: bool, src_hash: str,
                         need_thumbs: bool = True):
    """analyze() có cache theo hash nguồn. Lần render sau khi duyệt không cần ảnh cho AI nên dùng thẳng
    kết quả cũ (điểm cảnh + dải chữ), không giải mã lại cả video."""
    import cache
    k = cache.key("scenes", src_hash, want_gray, mode())
    if not need_thumbs:
        d = cache.get_json("scenes", k)
        if d:
            return d["cuts"], [tuple(b) for b in d["bands"]]
    cuts, bands = await analyze(path, tmpdir, dur, want_gray)
    try:
        cache.put_json("scenes", k, {"cuts": cuts, "bands": [list(b) for b in bands]})
    except OSError:
        pass
    return cuts, bands


def _cuts_sync(path: str, threshold: float = 0.3, min_gap: float = 0.8) -> list[float]:
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", *_decode_opts(cut_mode()), "-i", path, "-an", "-vf",
         f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    cuts = []
    for t in (float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)):
        if t < 0.3:
            continue
        if not cuts or t - cuts[-1] >= min_gap:
            cuts.append(round(t, 2))
    return cuts


def _cuts_near_sync(path: str, t0: float, t1: float, threshold: float = 0.3) -> list[float]:
    """Điểm chuyển cảnh trong [t0, t1] (giây video gốc): chỉ giải mã đúng khúc đó (vài giây), không cả video."""
    if t1 - t0 < 0.5:
        return []
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{max(0.0, t0):.2f}", "-t", f"{t1 - t0:.2f}", "-i", path, "-an", "-vf",
         f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60).stderr
    return [round(max(0.0, t0) + float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", err) if float(x) > 0.05]


async def cuts_near(path: str, t0: float, t1: float) -> list[float]:
    try:
        return await asyncio.to_thread(_cuts_near_sync, path, t0, t1)
    except Exception:
        return []


async def detect_cuts(path: str) -> list[float]:
    try:
        return await asyncio.to_thread(_cuts_sync, path)
    except Exception:
        return []


def snap_to_cuts(segs: list, cuts: list, before: float = 1.0, after: float = 0.9, lead: float = 0.1):
    """Dời mốc bắt đầu mỗi đoạn lời về đúng điểm chuyển cảnh gần nhất (nếu trong khoảng cho phép),
    để câu mới vang lên ngay khi cảnh mới xuất hiện. Mỗi cảnh chỉ dùng cho 1 đoạn; giữ thứ tự tăng dần."""
    out, used, prev = [], set(), -1.0
    for st, en, tx in segs:
        best = None
        for c in cuts:
            if c in used or not (st - before <= c <= st + after):
                continue
            if best is None or abs(c - st) < abs(best - st):
                best = c
        new_st = round(best + lead, 2) if best is not None else st
        if new_st <= prev + 0.5:          # không được đè lên đoạn trước
            new_st = st if st > prev + 0.5 else prev + 0.5
        else:
            if best is not None:
                used.add(best)
        out.append((new_st, max(en, new_st + 0.5), tx))
        prev = new_st
    return out


def scene_segments(cuts: list, out_dur: float, min_len: float = 1.5) -> list:
    """Video không lời: mỗi cảnh (đủ dài) là một đoạn [(start, end, "")]."""
    pts = [0.0] + [c for c in cuts if c < out_dur - 0.5] + [out_dur]
    segs = []
    for a, b in zip(pts, pts[1:]):
        if segs and b - a < min_len:
            segs[-1] = (segs[-1][0], b, "")
        else:
            segs.append((a, b, ""))
    return segs


def _keyframe_sync(path: str, t: float) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
         "-vf", "scale=384:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"],
        capture_output=True).stdout


async def keyframes(path: str, segs: list) -> list[bytes]:
    """Ảnh giữa mỗi đoạn (JPEG ~20KB) để AI nhìn cảnh. Có ảnh thu nhỏ sẵn từ analyze() thì lấy luôn
    (tức thì); không thì tua video lấy từng ảnh như cũ."""
    import os
    a_ = _ANALYZED.get(path)
    out = []
    for a, b, _ in segs:
        t = (a + b) / 2
        if a_ and a_["n"]:
            if a_.get("times"):                            # mức nhẹ: ảnh ở mốc khung chính, lấy ảnh gần nhất
                k = 1 + min(range(len(a_["times"])), key=lambda i: abs(a_["times"][i] - t))
            else:
                k = min(a_["n"], max(1, int(round(t * THUMB_FPS)) + 1))
            p = os.path.join(a_["dir"], f"{k:05d}.jpg")
            if os.path.exists(p):
                out.append(open(p, "rb").read())
                continue
        out.append(await asyncio.to_thread(_keyframe_sync, path, t))
    return out


def split_points(dur: float, part_len: float, cuts: list, silences: list,
                 before: float = 20.0, after: float = 10.0, min_last: float = 20.0) -> list:
    """Chọn ranh giới cắt video dài thành nhiều phần ~part_len giây, không cắt ngang câu nói.
    Ưu tiên (điểm càng cao càng tốt): im lặng TRÙNG chuyển cảnh > giữa khoảng im lặng > chuyển cảnh
    > mốc cứng. Chỉ xét trong [mốc - before, mốc + after]. Trả về [0, b1, ..., dur]."""
    if dur <= part_len * 1.25:
        return [0.0, dur]
    mids = [((a + b) / 2, b - a) for a, b in silences]
    bounds, prev = [0.0], 0.0
    while dur - prev > part_len * 1.25:
        target = prev + part_len
        lo, hi = max(prev + part_len * 0.6, target - before), min(dur - min_last, target + after)
        best, best_score = target, -1.0
        for c in cuts:                                   # chuyển cảnh, cộng điểm nếu nằm trong khoảng im lặng
            if lo <= c <= hi:
                in_sil = any(a - 0.1 <= c <= b + 0.1 for a, b in silences)
                score = (3.0 if in_sil else 1.0) - abs(c - target) / 60
                if score > best_score:
                    best, best_score = c, score
        for m, length in mids:                           # giữa khoảng im lặng (dài hơn càng tốt)
            if lo <= m <= hi:
                score = 2.0 + min(length, 1.5) / 3 - abs(m - target) / 60
                if score > best_score:
                    best, best_score = m, score
        bounds.append(round(best, 2))
        prev = best
    if dur - bounds[-1] < min_last and len(bounds) > 1:   # phần cuối quá ngắn -> gộp vào phần trước
        bounds.pop()
    bounds.append(dur)
    return bounds
