"""Ảnh bìa tự động: chọn khung đẹp nhất của video, chỉnh màu như video, đặt tiêu đề lên trên.

1. Chấm điểm khung hình (ảnh thu nhỏ 2 khung/giây đã có sẵn từ bước phân tích cảnh; không có thì giải mã riêng
   các khung khoá): độ nét (phương sai Laplacian), độ rực màu (Hasler-Süsstrunk), phơi sáng (không tối/cháy),
   vị trí trong video (nấu ăn: món hoàn thành thường ở 55-95% cuối). Loại khung sát điểm chuyển cảnh (nhoè),
   1 giây đầu, màn kết cuối và đoạn quảng cáo đã cắt.
2. Có key Gemini: AI nhìn 6 ứng viên tốt nhất (khác cảnh nhau) và chọn ảnh hấp dẫn nhất (món rõ, không bị tay
   che...). Không có key / AI lỗi: lấy ứng viên điểm cao nhất.
3. Dựng ảnh: đúng chuỗi màu của video (core_video.look_chain), che dải chữ gốc, nền tối dần phía trên cho chữ
   nổi, tiêu đề vàng viền đen, logo kênh. Kèm ảnh nhỏ 180x320 làm thumbnail Telegram (≤ 320px, < 200KB).
Mọi lỗi đều chỉ ghi log: không có ảnh bìa không được làm hỏng video.
"""
import asyncio
import logging
import os
import re
import subprocess

import numpy as np

import config
import core_video
import scenes

SW, SH = 144, 256          # cỡ chấm điểm
N_CAND = 6
COVER_NAME, THUMB_NAME = "cover.jpg", "thumb.jpg"


# ---------- 1. lấy khung + chấm điểm ----------

def _raw_frames(args: list, pre_vf: str = "") -> np.ndarray:
    """Khung RGB SHxSW từ ffmpeg. pre_vf: bộ lọc chạy TRƯỚC bước thu nhỏ (vd fps=...) — chỉ được có một -vf."""
    raw = subprocess.run(["ffmpeg", "-v", "error", *args, "-vf",
                          (pre_vf + "," if pre_vf else "")
                          + f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=180).stdout
    n = len(raw) // (SW * SH * 3)
    return np.frombuffer(raw[: n * SW * SH * 3], np.uint8).reshape(n, SH, SW, 3)


def frames_sync(src: str, thumbs_dir: str | None) -> tuple:
    """(thời điểm trên video gốc, khung SHxSWx3). Ưu tiên ảnh thu nhỏ có sẵn; không có thì giải mã khung khoá."""
    if thumbs_dir and os.path.isdir(thumbs_dir) and os.listdir(thumbs_dir):
        fr = _raw_frames(["-framerate", str(scenes.THUMB_FPS), "-i", os.path.join(thumbs_dir, "%05d.jpg")])
        return np.arange(len(fr)) / scenes.THUMB_FPS, fr
    err = subprocess.run(["ffmpeg", "-hide_banner", "-skip_frame", "nokey", "-i", src, "-an", "-vf", "showinfo",
                          "-fps_mode", "passthrough", "-f", "null", "-"], capture_output=True, text=True, timeout=180).stderr
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]
    fr = _raw_frames(["-skip_frame", "nokey", "-i", src, "-an", "-fps_mode", "passthrough"])
    k = min(len(times), len(fr))
    return np.array(times[:k]), fr[:k]


def _z(v: np.ndarray) -> np.ndarray:
    sd = float(np.std(v))
    return (v - float(np.median(v))) / (sd if sd > 1e-9 else 1.0)


def frame_metrics(frames: np.ndarray) -> dict:
    """Số đo từng khung RGB uint8 (N x H x W x 3): sharp = log phương sai Laplacian (độ nét), color = độ rực màu
    Hasler-Süsstrunk, mean = độ sáng trung bình 0..1, expo = điểm phơi sáng (0 là đẹp nhất, âm là tối/cháy)."""
    if len(frames) > 64:                     # từng khối 64 khung: video 5 phút có ~600 khung, float32 cả loạt ~1GB RAM
        parts = [frame_metrics(frames[i:i + 64]) for i in range(0, len(frames), 64)]
        return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    f = frames.astype(np.float32) / 255.0
    n = len(f)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    lap = 4 * y[:, 1:-1, 1:-1] - y[:, :-2, 1:-1] - y[:, 2:, 1:-1] - y[:, 1:-1, :-2] - y[:, 1:-1, 2:]
    rg, yb = (r - g).reshape(n, -1), (0.5 * (r + g) - b).reshape(n, -1)
    m = y.reshape(n, -1).mean(axis=1)
    clipped = (y > 0.97).reshape(n, -1).mean(axis=1) + (y < 0.03).reshape(n, -1).mean(axis=1)
    return {"sharp": np.log(lap.reshape(n, -1).var(axis=1) + 1e-6),
            "color": (np.sqrt(rg.std(axis=1) ** 2 + yb.std(axis=1) ** 2)
                      + 0.3 * np.sqrt(rg.mean(axis=1) ** 2 + yb.mean(axis=1) ** 2)),
            "mean": m, "expo": -(np.abs(m - 0.5) * 2 + clipped * 3)}


def score_frames(frames: np.ndarray, times: np.ndarray, dur: float, mode: str = "cook",
                 cuts: list | None = None, end: float | None = None) -> np.ndarray:
    """Điểm từng khung (càng cao càng hợp làm ảnh bìa); khung bị loại có điểm -inf.
    times, cuts, end: cùng trục thời gian (giây)."""
    if len(frames) == 0:
        return np.zeros(0)
    fm = frame_metrics(frames)
    sharp, color, m, expo = fm["sharp"], fm["color"], fm["mean"], fm["expo"]
    x = times / max(dur, 1e-6)
    prior = np.exp(-((x - 0.78) / 0.22) ** 2) if mode == "cook" else np.exp(-((x - 0.5) / 0.35) ** 2)
    score = 1.0 * _z(sharp) + 0.8 * _z(color) + 0.6 * _z(expo) + 1.0 * prior
    end = dur if end is None else min(end, dur)
    bad = (times < min(0.8, dur * 0.1)) | (times > end - 1.5)
    for c in cuts or []:
        bad |= np.abs(times - c) < 0.35
    bad |= m < 0.08                                       # khung đen (chuyển cảnh, mở đầu)
    if bad.all():                                         # video quá ngắn: bỏ ràng buộc vị trí
        bad = m < 0.08
    return np.where(bad, -np.inf, score)


def pick_candidates(times: np.ndarray, scores: np.ndarray, k: int = N_CAND, min_gap: float = 1.5) -> list:
    """Chỉ số k khung điểm cao nhất, cách nhau ≥ min_gap giây (ứng viên khác cảnh nhau)."""
    out = []
    for i in np.argsort(-scores):
        if not np.isfinite(scores[i]):
            break
        if all(abs(times[i] - times[j]) >= min_gap for j in out):
            out.append(int(i))
            if len(out) >= k:
                break
    return out


# ---------- 2. AI chọn ----------

COVER_PROMPT = """Dưới đây là {n} ảnh (Ảnh 0 đến Ảnh {last}) cắt từ một {kind} có tiêu đề «{title}». Chọn MỘT ảnh làm ẢNH BÌA hấp dẫn nhất để người lướt mạng dừng lại xem: món ăn/sản phẩm chính rõ nét, đầy đủ, màu đẹp, trông ngon; không bị tay, dụng cụ hay hơi nước che; không mờ nhoè vì chuyển động; không phải cảnh chuyển tiếp hay khung tối. Với video nấu ăn, ưu tiên món đã hoàn thành.
Trả về DUY NHẤT JSON: {{"best": số thứ tự ảnh, "reason": "lý do ngắn"}}"""

try:
    from pydantic import BaseModel

    class CoverOut(BaseModel):
        best: int
        reason: str
except Exception:
    CoverOut = None


async def ai_pick(images: list, title: str, mode: str) -> int | None:
    """Gemini chọn ảnh bìa trong các ứng viên; lỗi/không có key -> None."""
    if not config.GEMINI_API_KEY or len(images) < 2:
        return None
    import prompt_engine
    if prompt_engine.genai is None:
        return None
    kind = {"cook": "video nấu ăn", "review": "video review"}.get(mode, "video ngắn")
    prompt = COVER_PROMPT.format(n=len(images), last=len(images) - 1, kind=kind, title=title or "(không có)")
    try:
        raw = await prompt_engine._call_gemini(prompt, images, schema=CoverOut, label="Ảnh", timeout=40)
        d = prompt_engine.loads_lenient(raw)
        best = int(d.get("best"))
        if 0 <= best < len(images):
            logging.info("AI chọn ảnh bìa %d/%d: %s", best, len(images), str(d.get("reason", ""))[:120])
            return best
    except Exception as e:
        logging.warning("AI chọn ảnh bìa lỗi (%s), lấy ảnh điểm cao nhất", str(e)[:100])
    return None


# ---------- 3. dựng ảnh ----------

def cover_title(text: str) -> str:
    """Tiêu đề cho ảnh bìa: bỏ emoji/hashtag (font không có), tối đa ~3 dòng."""
    t = re.sub(r"#\S+|https?://\S+", " ", text or "")
    t = "".join(ch for ch in t if ch.isalnum() or ch in " ,.!?-:'&%/()")
    return " ".join(t.split())[:70].strip(" -:,")


def _wrap_lines(text: str, width: int) -> list:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def layout_title(text: str) -> tuple:
    """(chữ đã xuống dòng, cỡ chữ theo tỉ lệ chiều cao khung). Ngắn: chữ to; dài: chữ nhỏ hơn, tối đa 3 dòng."""
    for width, size in ((12, 0.068), (15, 0.056), (19, 0.046)):
        lines = _wrap_lines(text, width)
        if len(lines) <= 3:
            return "\n".join(lines), size
    lines = _wrap_lines(text, 19)[:3]
    lines[-1] = lines[-1].rstrip(" ,.") + "…"
    return "\n".join(lines), 0.046


def cover_filter(mode: str, W: int, H: int, src_w: int, src_h: int, bands, title_file: str | None,
                 title_size: float, logo: bool) -> str:
    """filter_complex cho MỘT khung: màu như video -> che dải chữ gốc -> tối dần phía trên -> tiêu đề -> logo,
    ra 2 nhãn [big] (ảnh bìa) và [small] (thumbnail Telegram 180x320)."""
    v = "[0:v]" + core_video.look_chain(mode, W, H, src_w, src_h) + core_video._cover_filter(bands or [], W, H)
    if title_file:
        v += (",format=yuv420p,geq=lum='lum(X,Y)*(0.58+0.42*min(1,Y/(H*0.46)))':cb='cb(X,Y)':cr='cr(X,Y)'"
              f",drawtext=fontfile={core_video.FONT_PATH}:textfile={title_file}:fontsize={int(H * title_size)}:"
              f"expansion=none:fontcolor=0xFFE100:borderw={max(3, int(H * 0.0065))}:bordercolor=black:shadowcolor=black@0.6:"
              f"shadowx={int(H * 0.003)}:shadowy={int(H * 0.004)}:x=(w-text_w)/2:y=h*0.085:text_align=center:"
              f"line_spacing={int(H * 0.012)}")
    if logo:
        k = H / 1280
        v += (f"[vb];[1:v]scale={int(W * 0.16)}:-1,format=rgba,colorchannelmixer=aa=0.85[lg];"
              f"[vb][lg]overlay=W-w-{int(24 * k)}:H-h-{int(40 * k)}")
    return v + ",split=2[big][s0];[s0]scale=-2:320:flags=lanczos[small]"


async def render_cover(src: str, t_src: float, out_dir: str, mode: str, title: str, bands=None) -> str | None:
    """Dựng cover.jpg + thumb.jpg trong out_dir từ khung tại t_src (giây trên video gốc). Trả đường dẫn ảnh bìa."""
    src_w, src_h, _ = await core_video.source_info(src)
    W, H = (1080, 1920) if (config.OUTPUT_HD and src_h >= 1600) else (720, 1280)
    title_file, size = None, 0.0
    text = cover_title(title)
    if text:
        wrapped, size = layout_title(text)
        title_file = os.path.join(out_dir, "cover_title.txt")
        with open(title_file, "w", encoding="utf-8") as f:
            f.write(wrapped)
    logo = config.BRAND_LOGO != "0" and os.path.exists(core_video.LOGO_PATH)
    big, small = os.path.join(out_dir, COVER_NAME), os.path.join(out_dir, THUMB_NAME)
    fc = cover_filter(mode, W, H, src_w, src_h, bands, title_file, size, logo)
    await core_video._run_ffmpeg("-y", "-ss", f"{max(0.0, t_src):.3f}", "-i", src,
                                 *(["-i", core_video.LOGO_PATH] if logo else []),
                                 "-filter_complex", fc,
                                 "-map", "[big]", "-frames:v", "1", "-q:v", "2", big,
                                 "-map", "[small]", "-frames:v", "1", "-q:v", "4", small)
    return big if os.path.exists(big) else None


async def make_cover(src: str, out_dir: str, mode: str, title: str, src_dur: float, cuts: list | None = None,
                     end: float | None = None, bands=None, thumbs_dir: str | None = None) -> str | None:
    """Toàn bộ quy trình (thời gian theo video GỐC). Không bao giờ ném lỗi: trả None nếu không làm được."""
    if config.COVER == "0":
        return None
    try:
        times, frames = await asyncio.to_thread(frames_sync, src, thumbs_dir)
        if not len(frames):
            return None
        sc = score_frames(frames, times, src_dur, mode, cuts, end)
        cand = pick_candidates(times, sc)
        if not cand:
            return None
        best = cand[0]
        if len(cand) >= 2 and config.GEMINI_API_KEY:
            images = []
            for i in cand:
                p = os.path.join(thumbs_dir or "", f"{i + 1:05d}.jpg")
                images.append(open(p, "rb").read() if thumbs_dir and os.path.exists(p)
                              else await asyncio.to_thread(scenes._keyframe_sync, src, float(times[i])))
            k = await ai_pick(images, title, mode)
            if k is not None:
                best = cand[k]
        t = float(times[best])
        logging.info("Ảnh bìa: khung %.1fs (điểm %.2f, %d ứng viên)", t, float(sc[best]), len(cand))
        return await render_cover(src, t, out_dir, mode, title, bands)
    except Exception as e:
        logging.warning("Không tạo được ảnh bìa: %s", str(e)[:200])
        return None
