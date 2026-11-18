"""Render video ra: chọn độ phân giải theo tốc độ máy đo được, tham số mã hoá, logo / tiêu đề / màn kết, độ to hai lượt EBU R128; trộn nhạc nền (video không lời) và thay nhạc (🎵).

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import json
import logging
import os
import re
import subprocess
import time

import config
from rmvideo.media import autolook, edl, ff, filtergraph, textfit


async def measure_lufs(input_args: list, audio_fc: str, out_dur: float) -> float | None:
    """Độ to tích hợp (LUFS) của nhãn [a] trong audio_fc, chỉ xét out_dur giây đầu. Lỗi/im lặng: None."""
    try:
        _, err = await ff._run_ffmpeg_full("-hide_banner", "-nostats", *input_args, "-filter_complex", audio_fc,
                                         "-map", "[a]", "-t", f"{out_dur:.2f}", "-f", "null", "-")
    except Exception as e:
        logging.warning("Đo độ to lỗi (%s), dùng loudnorm một lượt", str(e)[:120])
        return None
    m = filtergraph._LUFS_RE.findall(err)
    if not m or m[-1] == "-inf" or float(m[-1]) < -60:
        return None
    return float(m[-1])


async def two_pass_loudness(fc: str, input_args: list, out_dur: float) -> str:
    """Thay loudnorm động trong filter graph bằng hệ số cố định đo từ lượt 1 (xem trên). Không đo được thì giữ
    nguyên fc (loudnorm một lượt như cũ). input_args: đúng các '-i' (và tuỳ chọn đầu vào) của lần render."""
    if config.LOUDNESS_2PASS == "0" or filtergraph.LOUDNORM not in fc:
        return fc
    # graph có hình: phần tiếng nằm sau "[v];"; graph chỉ có tiếng (ảnh + nhạc dựng hình riêng): cả graph
    # ebur128 KHÔNG kèm tuỳ chọn: FFmpeg 8 trên Botkeep báo "Error applying options to the filter" với
    # framelog=quiet. Dòng tổng kết "Integrated loudness" luôn in ở mức info nên vẫn đọc được.
    audio = (fc.split("[v];", 1)[1] if "[v];" in fc else fc).replace(filtergraph.LOUDNORM, "ebur128", 1)
    lufs = await measure_lufs(input_args, audio, out_dur)
    if lufs is None:
        return fc
    gain = max(-30.0, min(24.0, filtergraph.TARGET_LUFS - lufs))
    logging.info("Âm lượng: bản trộn %.1f LUFS -> nhân %+.1f dB (mục tiêu %.0f LUFS)", lufs, gain, filtergraph.TARGET_LUFS)
    return fc.replace(filtergraph.LOUDNORM, filtergraph._linear_gain_filter(gain), 1)


# Đo trên video thật 30s 1080p, 1 lõi: dựng 1080p 75s, 720p 38s (x2.0); trong đó vibrance (đổi sang RGB) ~23s,
# unsharp ~9s, hqdn3d ~9s, x264 ~28s. Botkeep (1.5 lõi chậm) dựng 1080p video 88s mất cỡ 20 phút -> QUALITY=balanced
# (mặc định) ước tính trước thời gian dựng 1080p theo tốc độ đo được của CHÍNH máy này; quá RENDER_BUDGET thì ra 720p.
DEFAULT_SPEED_FILE = os.path.join(ff.ROOT, "render_speed.json")
SPEED_FILE = os.environ.get("RENDER_SPEED_FILE") or DEFAULT_SPEED_FILE


# RAM trống tối thiểu để dựng 1080p (MB). Đo 17/11/2026, video mực 1080x1920, ffmpeg 2 luồng: bản ngắn dựng mạnh 86s
# (21 khúc, balanced) 1235 MB; bản đầy đủ dựng mạnh 59 khúc preset medium (high) 2465 MB; cùng bản đó 720p 571 MB.
# Botkeep 2 GB (bot chiếm một phần) bị giết từ ~970 MB (job 6) -> bản ngắn 1080p trên Botkeep chắc chắn chết.
HD_RAM_MB = {"balanced": 1800, "high": 3000}
RENDER_BUDGET = float(os.environ.get("RENDER_BUDGET", "600") or 0)     # giây; 0 = tắt (luôn 1080p khi nguồn đủ nét)


HD_COST = 2.0                  # 1080p tốn ~2 lần 720p


BENCH_FACTOR = 1.2             # bài đo testsrc2 nhanh hơn video thật ~1.2 lần (2.1 so với 2.5 giây/giây trên 1 lõi)


def render_speed() -> float | None:
    """Số giây máy này cần để dựng 1 giây video 1080p (chế độ nấu ăn, QUALITY=balanced); chưa đo thì None."""
    try:
        with open(SPEED_FILE, encoding="utf-8") as f:
            v = float(json.load(f)["sec_per_sec_1080"])
        return v if v > 0 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record_render_speed(elapsed: float, out_dur: float, H: int, source: str = "render") -> float | None:
    """Ghi tốc độ dựng thật (đổi về 1080p). Lần dựng thật đầu tiên thay hẳn số của bài đo; sau đó lấy trung bình."""
    if elapsed <= 0 or out_dur <= 0 or (out_dur < 3 and source != "bench"):    # video quá ngắn: số đo nhiễu
        return None
    s = elapsed / out_dur * (1.0 if H >= 1920 else HD_COST)
    try:
        with open(SPEED_FILE, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    old = render_speed()
    new = s if old is None or source == "bench" or prev.get("source") == "bench" else 0.5 * old + 0.5 * s
    try:
        with open(SPEED_FILE, "w", encoding="utf-8") as f:
            json.dump({"sec_per_sec_1080": round(new, 3), "source": source, "at": int(time.time())}, f)
    except OSError:
        pass
    return new


def render_benchmark_sync(seconds: int = 3) -> float | None:
    """Đo nhanh lúc khởi động (chỉ khi chưa có số đo): dựng `seconds` giây 1080p tổng hợp qua đúng chuỗi lọc nấu ăn +
    x264 veryfast. Trả giây máy / giây video (đã quy đổi ra video thật)."""
    t0 = time.time()
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", *ff.thread_args(), "-f", "lavfi", "-i",
                            f"testsrc2=s=1080x1920:r=30:d={seconds}", "-vf", filtergraph.look_chain("cook", 1080, 1920, 1080, 1920),
                            "-threads", str(ff.ffmpeg_threads()), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-an", "-f", "null", "-"], capture_output=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return record_render_speed((time.time() - t0) * BENCH_FACTOR, seconds, 1920, source="bench")


def output_size(src_h: int, allow_hd: bool, out_dur: float, notes: list | None = None) -> tuple:
    """(W, H) đầu ra. 1080p khi nguồn đủ nét (>=1600px), trừ khi: QUALITY=fast (luôn 720p, như chế độ 📸), hoặc
    QUALITY=balanced mà ước tính dựng 1080p trên máy này quá RENDER_BUDGET giây (máy yếu: 720p nhanh gấp đôi)."""
    if not (config.OUTPUT_HD and allow_hd and src_h >= 1600):
        return 720, 1280
    if config.QUALITY == "fast":
        return 720, 1280
    need, free = HD_RAM_MB.get(config.QUALITY, HD_RAM_MB["balanced"]), ff.mem_available_mb()
    if free is not None and free < need:
        logging.info("Còn %d MB RAM, 1080p cần ~%d MB -> xuất 720p", free, need)
        if notes is not None:
            notes.append(f"ℹ️ Máy dựng chỉ còn ~{free / 1024:.1f} GB RAM trống, bản 1080p cần ~{need / 1024:.1f} GB nên "
                         f"xuất 720p (dựng trên Kaggle thì được 1080p). ")
        return 720, 1280
    sp = render_speed()
    if config.QUALITY == "balanced" and RENDER_BUDGET > 0 and sp and sp * out_dur > RENDER_BUDGET:
        est = sp * out_dur
        logging.info("Máy chậm (%.1f giây máy / giây video 1080p): 1080p ước tính %.0f phút > %.0f phút -> xuất 720p",
                     sp, est / 60, RENDER_BUDGET / 60)
        if notes is not None:
            notes.append(f"ℹ️ Máy chủ chậm: bản 1080p ước tính mất ~{est / 60:.0f} phút dựng nên bot xuất 720p "
                         f"(~{est / HD_COST / 60:.0f} phút). Muốn luôn 1080p: Cài đặt → Chất lượng = Chuẩn TVC. ")
        return 720, 1280
    return 1080, 1920


_NVENC: dict = {}


def nvenc() -> list | None:
    """Tham số bộ mã hoá GPU NVIDIA (h264_nvenc) nếu máy có GPU + FFmpeg hỗ trợ (phiên GPU của Kaggle), không thì None
    (Botkeep, phiên CPU: x264 như cũ). Thử mã hoá thật một khung một lần mỗi tiến trình; RM_NVENC=0 để tắt."""
    if "args" in _NVENC:
        return _NVENC["args"]
    _NVENC["args"] = None
    if os.environ.get("RM_NVENC") == "0" or not (os.path.exists("/dev/nvidia0") or os.path.exists("/dev/nvidiactl")):
        return None
    for preset in (["-preset", "p5", "-tune", "hq"], ["-preset", "slow"]):     # FFmpeg mới / cũ đặt tên preset khác nhau
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=0.2",
                                "-c:v", "h264_nvenc", *preset, "-f", "null", "-"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            break
        if r.returncode == 0:
            _NVENC["args"] = preset
            logging.info("Mã hoá video bằng GPU (h264_nvenc %s)", " ".join(preset))
            break
    return _NVENC["args"]


def encode_args(H: int, fps: float = 30.0) -> list:
    """Thông số x264/AAC theo QUALITY và độ phân giải. high = chuẩn TVC: preset medium, crf 18,
    1080p ~10 Mbps; balanced = veryfast/crf 20; fast = superfast/crf 23. Luôn: High 4.1, bt709,
    GOP 2s (60 khung) cho phát trực tuyến, AAC 192k 48 kHz."""
    q = config.QUALITY
    preset, crf = {"fast": ("superfast", 22), "high": ("medium", 18)}.get(q, ("veryfast", 20))
    if H >= 1920:
        maxrate, buf = {"fast": ("5000k", "8000k"), "high": ("12000k", "20000k")}.get(q, ("9000k", "14000k"))
    else:
        maxrate, buf = {"fast": ("2500k", "4000k"), "high": ("6000k", "10000k")}.get(q, ("4500k", "7000k"))
    # -threads ở đây là luồng của bộ mã hoá x264 (tuỳ chọn đầu ra); để mặc định x264 tự lấy theo số lõi MÁY CHỦ
    # (container 1.5 vCPU vẫn thấy hàng chục lõi -> tốn RAM lookahead, tranh CPU)
    gpu = nvenc()
    # GPU (Kaggle): mã hoá nhanh gấp nhiều lần x264 trên CPU; cq hạ 1 bậc so với crf để giữ chất lượng tương đương
    codec = (["-c:v", "h264_nvenc", *gpu, "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "0", "-bf", "3",
              "-spatial-aq", "1"] if gpu else ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)])
    return ["-threads", str(ff.ffmpeg_threads()),
            *codec, "-maxrate", maxrate, "-bufsize", buf,
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-g", str(round(2 * fps)), "-keyint_min", str(round(2 * fps)),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]


async def source_info(path: str) -> tuple:
    """(rộng, cao, fps) của luồng hình; fps lấy avg_frame_rate (đúng cả với video tốc độ khung thay đổi)."""
    out = await ff._run_ffmpeg("-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,avg_frame_rate,r_frame_rate:stream_side_data=rotation",
                            "-of", "default=nw=1", path, tool="ffprobe")
    d = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    try:
        rot = int(round(float(d.get("rotation", 0) or 0)))   # ffmpeg tự xoay khi giải mã: kích thước thật đổi chiều
    except ValueError:
        rot = 0

    def _rate(s):
        try:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        except Exception:
            return 0.0
    fps = _rate(d.get("avg_frame_rate", "")) or _rate(d.get("r_frame_rate", ""))
    try:
        w, h = int(d.get("width", 0)), int(d.get("height", 0))
    except ValueError:
        return 0, 0, fps
    return (h, w, fps) if abs(rot) % 180 == 90 else (w, h, fps)


def write_lines(text: str, out_dir: str, prefix: str) -> list:
    """Mỗi dòng chữ một file (drawtext đọc textfile: khỏi phải thoát ký tự đặc biệt)."""
    files = []
    for i, line in enumerate([ln for ln in (text or "").split("\n") if ln.strip()]):
        p = os.path.join(out_dir, f"{prefix}_{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(line.strip())
        files.append(p)
    return files


def drawtext_lines(files: list, fontsize: int, y_top: str, line_h: int, style: str) -> str:
    """Khối chữ nhiều dòng, MỖI DÒNG căn giữa. Thay cho text_align=center (chỉ có từ FFmpeg 6.1; Botkeep dùng ffmpeg
    5.1 của Debian 12 nên cả lệnh render hỏng — bài tự kiểm tra lúc khởi động bắt được)."""
    return "".join(f",drawtext=fontfile={ff.FONT_PATH}:textfile={p}:expansion=none:fontsize={fontsize}:{style}:"
                   f"x=(w-text_w)/2:y={y_top}+{i * line_h}" for i, p in enumerate(files))


def brand_hex(alpha: float | None = None) -> str:
    """Màu kênh cho drawtext/drawbox ("0xRRGGBB[@a]"), "" nếu chưa đặt BRAND_COLOR."""
    c = config.brand_text("BRAND_COLOR").lstrip("#")
    return (f"0x{c.upper()}" + (f"@{alpha:g}" if alpha is not None else "")) if len(c) == 6 else ""


OUTRO_DUR = 2.2
LOGO_Y = 0.09                # mép trên của logo theo chiều cao khung (dưới vùng nút của TikTok / Shorts / Reels)
STING_PEAK = -4.0          # dBFS: đỉnh của âm hiệu trước khi trộn (bản 46: file mặc định đỉnh -29 dBFS, ra video còn
                           # ~-40 dB, gần như không nghe thấy; file tải lên to nhỏ đủ kiểu -> chuẩn hoá lúc trộn)
_STING_GAIN: dict = {}


async def sting_gain(path: str) -> float:
    """Số dB cần cộng để đỉnh âm hiệu bằng STING_PEAK (đo một lần mỗi file; đo lỗi thì 0)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return 0.0
    if key not in _STING_GAIN:
        from rmvideo.media import proc
        try:
            r = await proc.run(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                               timeout=60, stderr_keep=None)
            m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr or "")
            _STING_GAIN[key] = max(-12.0, min(30.0, STING_PEAK - float(m.group(1)))) if m else 0.0
        except Exception:
            _STING_GAIN[key] = 0.0
    return _STING_GAIN[key]
STING_LEN = 1.4            # âm hiệu màn kết (cắt ở 1,8 giây; bộ Bếp Đong Đủ dài 1,3 giây)


TITLE_FS = 50          # cỡ chữ hook (khung 1280 cao); dài quá thì xuống dòng, vẫn không vừa thì thu nhỏ
TITLE_PAD = 16
SIDE = 0.05            # chừa mỗi bên mép 5% bề ngang cho mọi chữ in lên hình


def text_room(W: int, pad: int) -> float:
    """Bề ngang dành cho chữ: trừ lề hai bên và khung nền (boxborderw) của drawtext."""
    return W * (1 - 2 * SIDE) - 2 * pad


def title_layout(title: str, W: int, H: int) -> tuple[list, int]:
    """(các dòng, cỡ chữ) của chữ hook cho vừa khung: đo theo font thật (textfit), tối đa 3 dòng."""
    k = H / 1280
    return textfit.fit(title.strip(), text_room(W, int(TITLE_PAD * k)), int(TITLE_FS * k), max_lines=3)


def hook_title_end(T: float, plan) -> float | None:
    """Lúc tắt chữ hook (None = mặc định 0,3-3,3s). Có đoạn mở đầu: hết đoạn đó. Điểm danh nguyên liệu bắt đầu sớm hơn
    thì tắt đúng lúc đó (bản 46: chữ hook đè thẻ «Mực» nửa giây)."""
    end = (T + 0.3) if T else None
    first = min((float(x["t"]) for x in (plan.texts if plan else []) if x.get("kind") == "montage"), default=None)
    if first is not None and first + T < (end or 3.3):
        end = round(first + T, 2)
    return end


def fit_one(text: str, W: int, fs_max: int) -> int:
    """Cỡ chữ cho MỘT dòng (tên kênh, câu dưới tên) vừa khung."""
    return textfit.fit(text, text_room(W, 0), fs_max, max_lines=1)[1]


def _brand_filter(W: int, H: int, out_dur: float, title_files: list | None, outro_file: str | None,
                  logo_idx: int | None, title_end: float | None = None, tagline_file: str | None = None,
                  title_fs: int | None = None, outro_fs: int | None = None, tagline_fs: int | None = None) -> str:
    """Chuỗi filter gắn lên [v]: logo góc phải trên, tiêu đề (câu hook) 3s đầu, màn kết 2.2s cuối (chỉ tên kênh).
    title_end: có đoạn mở đầu thì chữ hook hiện NGAY khung đầu (khung đầu thường thành ảnh bìa) tới hết đoạn đó.
    title_fs / outro_fs / tagline_fs: cỡ chữ đã tính cho vừa khung (title_layout / fit_one); None = cỡ mặc định."""
    k = H / 1280
    f = ""
    if title_files:
        fs, pad = title_fs or int(TITLE_FS * k), int(TITLE_PAD * k)
        lh = fs + 2 * pad + int(6 * k)
        if title_end:
            e = f"{title_end:.2f}"
            when = f"enable='lte(t,{e})':alpha='if(gt(t,{e}-0.4),({e}-t)/0.4,1)'"
        else:
            when = "enable='between(t,0.3,3.3)':alpha='if(lt(t,0.7),(t-0.3)/0.4,if(gt(t,2.9),(3.3-t)/0.4,1))'"
        box = brand_hex(0.88) or "black@0.45"          # màu kênh: khung chữ hook theo màu kênh
        f += drawtext_lines(
            title_files, fs, f"h*0.36-{len(title_files) * lh // 2}", lh,
            f"fontcolor=white:borderw={int(3 * k)}:bordercolor=black@0.9:box=1:boxcolor={box}:boxborderw={pad}:"
            + when)
    if outro_file and out_dur > 6:
        # màn kết SÁNG (bản 50): giữ cảnh món, chỉ một dải mờ sau tên kênh. Trước đây cả khung tối 72% rồi mờ đen:
        # dấu hiệu "hết rồi" khiến người xem TikTok / Shorts lướt đi trước khi video tự quay vòng (lượt xem lại tính
        # vào thời lượng xem) — khung cuối sáng nối vòng về khung đầu mượt hơn
        st = out_dur - OUTRO_DUR
        f += (f",drawbox=x=0:y=ih*0.40:w=iw:h=ih*0.20:color=black@0.42:t=fill:enable='gte(t,{st:.2f})'"
              f",drawtext=fontfile={ff.FONT_PATH}:textfile={outro_file}:expansion=none:fontsize={outro_fs or int(62 * k)}:fontcolor=white:"
              f"borderw={int(3 * k)}:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-{int(30 * k)}:"
              f"enable='gte(t,{st:.2f})':alpha='min(1,(t-{st:.2f})/0.4)'")
        if tagline_file:                               # câu dưới tên kênh (BRAND_TAGLINE), màu kênh
            f += (f",drawtext=fontfile={ff.FONT_PATH}:textfile={tagline_file}:expansion=none:fontsize={tagline_fs or int(34 * k)}:"
                  f"fontcolor={brand_hex() or '0xFFE100'}:borderw={int(2 * k)}:bordercolor=black:x=(w-text_w)/2:"
                  f"y=(h-text_h)/2+{int(48 * k)}:enable='gte(t,{st + 0.25:.2f})':alpha='min(1,(t-{st + 0.25:.2f})/0.4)'")
        # bỏ dòng "Theo dõi để xem thêm": không kêu gọi đăng ký/theo dõi kênh ở cuối video (yêu cầu 13/11/2026)
    if logo_idx is not None:
        # logo góc phải, hạ xuống ~9% chiều cao (bản 50): góc trên cùng là chỗ nút tìm kiếm / tab của TikTok, Shorts,
        # Reels — logo sát mép trên bị che
        f += (f"[vb];[{logo_idx}:v]scale={int(W * 0.16)}:-1,format=rgba,colorchannelmixer=aa=0.85[lg];"
              f"[vb][lg]overlay=W-w-{int(24 * k)}:{int(H * LOGO_Y)}")
    return f



def overlay_filter(overlays, W: int, H: int, offset: float, tmp: str) -> str:
    """Thẻ chữ của kế hoạch dựng (⏩ tua nhanh, nguyên liệu kèm định lượng, mẹo, giá) theo màu kênh, hiện mờ dần
    vào/ra. Mốc của kế hoạch chưa tính đoạn mở đầu: cộng offset."""
    k = H / 1280
    out = ""
    pad = int(14 * k)
    for i, o in enumerate(overlays):
        big = o.kind in ("skip", "ingredient")
        # đo theo font thật: mẹo dài xuống tối đa 4 dòng, thẻ khác 2 dòng; vẫn dài thì thu chữ (không tràn mép)
        lines, fs = textfit.fit(o.text.strip(), text_room(W, pad), int((44 if big else 34) * k),
                                max_lines=4 if o.kind == "tip" else 2)
        p = os.path.join(tmp, f"ov_{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        t0, t1 = o.t + offset, o.t + offset + o.dur
        box = brand_hex(0.9) or "0xFFE100@0.9"
        y = {"skip": "h*0.16", "ingredient": "h*0.62", "tip": "h*0.12", "price": "h*0.62"}.get(o.kind, "h*0.16")
        alpha = f"alpha='min(1,min((t-{t0:.2f})/0.25,({t1:.2f}-t)/0.25))'"
        out += (f",drawtext=fontfile={ff.FONT_PATH}:textfile={p}:expansion=none:fontsize={fs}:fontcolor=white:"
                f"borderw={int(2 * k)}:bordercolor=black@0.8:box=1:boxcolor={box}:boxborderw={pad}:"
                f"line_spacing={int(8 * k)}:x=(w-text_w)/2:y={y}:enable='between(t,{t0:.2f},{t1:.2f})':{alpha}")
    return out


async def side_clip(src: str, pieces: list, out: str, mode: str, W: int, H: int, fps: float, src_w: int, src_h: int,
                    ambient_wav: str | None, stretch: float = 1.0, color: bool = True) -> bool:
    """Dãy khúc lặp / đảo chỗ -> một clip ngắn đã chỉnh màu, đúng cỡ khung (mỗi khúc một đầu vào tua sẵn bằng -ss:
    chỉ giải mã vài giây quanh khúc, không giải mã cả video). Kèm tiếng nền đã tách giọng nếu có. Trả có tiếng không."""
    ins, vparts, aparts = [], [], []
    n = len(pieces)
    for i, p in enumerate(pieces):
        ins += ["-ss", f"{p.a:.3f}", "-t", f"{p.b - p.a + 0.2:.3f}", "-i", src]
    if ambient_wav:
        for p in pieces:
            ins += ["-ss", f"{p.a:.3f}", "-t", f"{p.b - p.a + 0.2:.3f}", "-i", ambient_wav]
    for i, p in enumerate(pieces):
        L = p.length * stretch
        ch = (f"[{i}:v]trim=duration={p.b - p.a:.3f},setpts=(PTS-STARTPTS)*{stretch / p.speed:.5f},fps={fps:g},"
              + filtergraph.look_chain(mode, W, H, src_w, src_h, zoom=p.zoom, part="all" if color else "geo"))
        if p.push > 0:
            ch += "," + edl.push_filter(W, H, p.push, L).rstrip(",")
        if p.fx == "flash":
            ch += f",fade=t=in:st=0:d={edl.FLASH}:color=white"
        vparts.append(ch + f",setsar=1,trim=duration={L:.3f}[v{i}]")
        if ambient_wav:
            aparts.append(f"[{n + i}:a]atrim=duration={p.b - p.a:.3f},asetpts=PTS-STARTPTS,"
                          f"{edl._atempo(p.speed / stretch)}aformat=sample_rates=44100:channel_layouts=stereo,"
                          f"apad=whole_dur={L:.3f},atrim=0:{L:.3f}[a{i}]")
    fc = ";".join(vparts + aparts) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[sv]"
    if ambient_wav:
        fc += ";" + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[sa]"
    await ff._run_ffmpeg("-y", *ff.thread_args(), *ins, "-filter_complex", fc, "-map", "[sv]",
                         *(["-map", "[sa]", "-c:a", "pcm_s16le"] if ambient_wav else ["-an"]),
                         *(["-c:v", "h264_nvenc", *gpu, "-rc", "vbr", "-cq", "16", "-b:v", "0"] if (gpu := nvenc()) else
                           ["-c:v", "libx264", "-preset", "veryfast", "-crf", "15"]), "-pix_fmt", "yuv420p", out)
    return bool(ambient_wav)


async def render(input_mp4: str, voice_mp3: str, output_mp4: str, mode: str,
                 duration: float = 0, ass_path: str | None = None, cover_bands=None,
                 ambient_wav: str | None = None, speech_gate=None, allow_hd: bool = True,
                 title: str = "", tmp: str | None = None, stretch: float = 1.0, ost=None, notes: list | None = None,
                 music_bed: bool = False, teaser: dict | None = None, edit: dict | None = None,
                 overlays: list | None = None, tail: float = 0.0, sting_after: float | None = None,
                 voice_bed: dict | None = None):
    """Chế độ cook: ambient_wav = tiếng nền đã tách giọng AI; không có thì speech_gate =
    [(start, end)] lúc người gốc nói để tắt tiếng nền khi đó (không bao giờ để lọt giọng gốc).
    duration: 0 = giữ nguyên thời lượng gốc; 30/60/90 = cắt đúng ngần đó giây.
    stretch: làm chậm hình + tiếng nền (1.4 = dài thêm 40%) NGAY TRONG lần mã hoá này; mọi mốc thời gian
    (phụ đề, cổng tiếng nền, giọng) là theo trục thời gian ĐÃ làm chậm.
    Video nguồn câm tiếng vẫn chạy được chế độ cook (tự bỏ lớp tiếng nền).
    notes: danh sách nhận lời nhắn cho người dùng (vd. máy chậm nên ra 720p).
    teaser: đoạn mở đầu {"a": giây bắt đầu trên video gốc, "dur", "still"} chiếu trước video chính (bản 29).
    tail: giây màn kết NỐI THÊM sau video (giữ khung cuối; bản 43), 0 = màn kết đè lên cuối video như cũ.
    sting_after: giọng đọc dứt lúc nào: âm hiệu màn kết vang sau đó (trong phạm vi màn kết).
    voice_bed: nhạc lót dưới lời {"path", "trim", "name"} (VOICE_BED, bản 1.0.0); video gốc đã có nhạc thì bỏ."""
    t_start = time.time()
    # BRAND_LOOK=auto: Botkeep đã chọn tông lúc đầu job (hoặc Kaggle nhận sẵn trong việc) thì không đo lại
    await autolook.ensure(input_mp4, note=notes.append if notes is not None else None)
    T = float(teaser["dur"]) if teaser and teaser.get("dur") else 0.0
    plan = edl.EditPlan.from_dict(edit) if edit else None   # kế hoạch dựng (REMAKE_STYLE): khúc, tốc độ, cỡ khung
    vid_dur = (plan.out_dur() if plan else await ff.probe_duration(input_mp4) * stretch) + T + max(0.0, tail)
    out_dur = vid_dur if duration <= 0 else min(duration, vid_dur)
    out_dur = min(out_dur, config.HARD_MAX)       # trần tuyệt đối; giới hạn theo chế độ do caller truyền vào
    ambient = await ff.has_audio(input_mp4)
    # nguồn đủ nét (>=1600px) thì xuất 1080x1920, không thì 720x1280 (không phóng to giả nét)
    src_w, src_h, src_fps = await source_info(input_mp4)
    W, H = output_size(src_h, allow_hd, out_dur, notes)
    fps = filtergraph.out_fps(src_fps)
    logging.info("Render %dx%d %gfps | nguồn %dx%d %.2ffps%s | chất lượng %s", W, H, fps, src_w, src_h, src_fps,
                 " (nguồn <1600px nên ra 720p; gửi link TikTok để bot lấy bản HD)" if src_h < 1600 else "", config.QUALITY)
    # thương hiệu: tiêu đề mở đầu, màn kết, logo (tất cả tùy chọn trong Cài đặt)
    tmp = tmp or os.path.dirname(output_mp4)
    title_files, outro_file, title_fs = [], None, None
    if title and config.BRAND_INTRO != "0":
        lines, title_fs = title_layout(title, W, H)
        title_files = write_lines("\n".join(lines), tmp, "title")
    tagline_file = outro_fs = tagline_fs = None
    name, tagline = config.brand_text("BRAND_NAME"), config.brand_text("BRAND_TAGLINE")
    if name:
        outro_file = os.path.join(tmp, "outro.txt")
        open(outro_file, "w", encoding="utf-8").write(name)
        outro_fs = fit_one(name, W, int(62 * H / 1280))
        if tagline:
            tagline_file = os.path.join(tmp, "tagline.txt")
            open(tagline_file, "w", encoding="utf-8").write(tagline)
            tagline_fs = fit_one(tagline, W, int(34 * H / 1280))
    logo_file = ff.logo_path()
    use_logo = config.BRAND_LOGO != "0" and bool(logo_file)
    # đầu vào: 0 video, 1 giọng, [tiếng nền], [âm hiệu], [tiếng nền đoạn mở đầu], [logo], [hình đoạn mở đầu]. Các đầu
    # vào TIẾNG đứng trước để lượt đo âm lượng (two_pass_loudness, chỉ có các đầu vào tiếng) đánh số giống hệt.
    audio_in = ["-i", input_mp4, "-i", voice_mp3, *(["-i", ambient_wav] if ambient_wav else [])]
    sting_idx = None
    sting_file = ff.sting_path()
    if config.BRAND_STING != "0" and sting_file and out_dur > 6 and not music_bed:
        sting_idx = audio_in.count("-i")
        audio_in += ["-i", sting_file]
    vb = None
    if voice_bed and not music_bed and mode in ("cook", "review"):
        vb = await _bed_level(voice_bed, voice_mp3, ambient_wav or (input_mp4 if ambient else None),
                              None if ambient_wav else speech_gate, out_dur, notes)
        if vb is not None:
            vb["idx"] = audio_in.count("-i")
            audio_in += ["-stream_loop", "-1", "-ss", f"{float(voice_bed.get('trim') or 0.0):.3f}",
                         "-i", voice_bed["path"]]
    side_inputs: dict = {}
    side_audio: dict = {}
    if plan:
        # khúc lặp / đảo chỗ: dựng trước thành clip phụ ngắn (mỗi dãy một clip), bản dựng chính nhận như một đầu vào
        for r, ks in enumerate(plan.side_runs()):
            clip = os.path.join(tmp, f"side_{r}.mkv")      # mkv: chứa được tiếng PCM
            has_a = await side_clip(input_mp4, [plan.pieces[k] for k in ks], clip, mode, W, H, fps, src_w, src_h,
                                    ambient_wav, plan.stretch,
                                    color=not filtergraph.shared_look(plan, W, H, src_w, src_h))
            side_inputs[r] = audio_in.count("-i")
            if has_a:
                side_audio[r] = f"[{side_inputs[r]}:a]"
            audio_in += ["-i", clip]
    tz, extra_in = None, []
    if T:
        a0 = max(0.0, float(teaser.get("a") or 0.0))
        tz = {"dur": T, "still": bool(teaser.get("still")), "ia": None}
        if ambient_wav:
            tz["ia"] = audio_in.count("-i")
            audio_in += ["-ss", f"{a0:.3f}", "-t", f"{T:.3f}", "-i", ambient_wav]
        tz["iv"] = audio_in.count("-i") + (1 if use_logo else 0)
        extra_in = ["-ss", f"{a0:.3f}", "-t", "1" if tz["still"] else f"{T + 0.5:.3f}", "-i", input_mp4]
    logo_idx = audio_in.count("-i") if use_logo else None
    fc = filtergraph.build_filter(mode, out_dur, ambient or bool(ambient_wav), ass_path, cover_bands, W, H,
                      "[2:a]" if ambient_wav else "[0:a]", None if ambient_wav else speech_gate,
                      fps=fps, src_w=src_w, src_h=src_h, stretch=stretch, ost=ost, orig_audio=ambient,
                      bed=bool(music_bed and ambient_wav), teaser=tz, edit=plan, side_inputs=side_inputs,
                      side_audio=side_audio, tail=max(0.0, tail), voice_bed=vb)
    title_end = hook_title_end(T, plan)
    brand = _brand_filter(W, H, out_dur, title_files, outro_file, logo_idx, title_end=title_end,
                          tagline_file=tagline_file, title_fs=title_fs, outro_fs=outro_fs, tagline_fs=tagline_fs)
    cards = list(plan.overlays if plan else []) + [edl.Overlay(**o) for o in overlays or []]
    if cards:
        brand = overlay_filter(cards, W, H, T, tmp) + brand
    # vào thẳng hình, KHÔNG mờ từ đen (bản 50): khung đầu là thứ người xem TikTok / Reels thấy đầu tiên khi lướt tới
    # (và hay được lấy làm ảnh bìa); job 7: 0,33 giây đầu tối đen -> mất cú chạm đầu tiên. Cuối video cũng không mờ
    # về đen: TikTok / Shorts tự quay vòng, khung cuối sáng nối thẳng về khung đầu (tiếng vẫn nhỏ dần 0,4 giây)
    fades = ""
    fc = fc.replace("[v];", brand + fades + "[v];", 1)
    if sting_idx is not None:
        # âm hiệu kênh vang lúc màn kết hiện (hoặc 1,6 giây cuối nếu không có màn kết), trộn trên giọng + tiếng nền
        at = max(0.0, out_dur - (OUTRO_DUR if outro_file else 1.6))
        if sting_after is not None:                 # câu đọc cuối chưa dứt: lùi âm hiệu tới sau câu đó (còn kịp vang hết)
            at = max(at, min(sting_after + 0.15, out_dur - STING_LEN))
        ms = int(at * 1000)
        fc = fc.replace("[a]", "[a0]", 1)
        gain = await sting_gain(sting_file)
        fc += (f";[{sting_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:1.8,volume={gain:.1f}dB,"
               f"adelay={ms}|{ms},volume=0.7[sting];[a0][sting]amix=inputs=2:duration=first:normalize=0[a]")
    fc = fc.replace("[a]", f",afade=t=in:st=0:d=0.15,afade=t=out:st={max(0, out_dur - 0.4):.2f}:d=0.4,alimiter=limit=0.89:level=false[a]", 1)
    fc = await two_pass_loudness(fc, audio_in, out_dur)
    await ff._run_ffmpeg(
        "-y", *ff.thread_args(),
        *audio_in,
        *(["-i", logo_file] if use_logo else []),
        *extra_in,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        *encode_args(H, fps),
        "-t", f"{out_dur:.2f}",
        output_mp4,
    )
    if nvenc() and notes is not None:
        notes.append(f"ℹ️ Mã hoá video bằng GPU (NVENC), dựng {time.time() - t_start:.0f}s. ")
    if mode == "cook" and config.QUALITY == "balanced":      # số đo cho lần quyết định 1080p/720p sau
        sp = record_render_speed(time.time() - t_start, out_dur, H)
        logging.info("Dựng %dp %.0fs video mất %.0fs (máy: %.1f giây / giây video 1080p)", H * 9 // 16, out_dur,
                     time.time() - t_start, sp or 0)
    return out_dur


MUSIC_BG_LEVEL = 0.32          # nhạc nền ≈ -10 dB so với tiếng gốc, tự hạ thêm khi tiếng gốc to (sidechain)


BED_BELOW = 18.0               # nhạc lót: trung bình (đã bỏ bass) thấp hơn trung bình giọng 18 dB -> quãng lặng ~13 dB dưới lời


async def _mean_db(args: list, pre: str = "") -> float | None:
    """Âm lượng trung bình (dBFS, volumedetect) của một đầu vào ffmpeg (pre: bộ lọc chạy trước khi đo); lỗi -> None."""
    from rmvideo.media import proc
    try:
        r = await proc.run(["ffmpeg", "-v", "info", "-nostats", *args, "-vn", "-af", pre + "volumedetect", "-f", "null",
                            "-"],
                           timeout=120, stderr_keep=None)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
        return float(m.group(1)) if m else None
    except Exception as e:
        logging.info("Không đo được âm lượng: %s", str(e)[:120])
        return None


async def _bed_level(bed: dict, voice: str, orig: str | None, speech, out_dur: float, notes: list | None) -> dict | None:
    """Nhạc lót dưới lời: None = không lót (không có file, video gốc đã có nhạc); không thì {"gain_db"} để nhạc thấp hơn
    giọng đọc BED_BELOW dB, bất kể bài gốc được master to hay nhỏ."""
    import asyncio

    from rmvideo.media import voicebed
    path = bed.get("path") or ""
    if not os.path.exists(path):
        return None
    if orig and await asyncio.to_thread(voicebed.source_has_music, orig, speech):
        if notes is not None:
            notes.append("🎵 Video gốc đã có nhạc: không lót thêm nhạc dưới lời. ")
        return None
    music = await _mean_db(["-ss", f"{float(bed.get('trim') or 0.0):.3f}", "-t", f"{out_dur:.2f}", "-i", path],
                           "highpass=f=150,lowpass=f=9000,")              # đo đúng phần nhạc sẽ nghe (đã bỏ bass)
    vo = await _mean_db(["-i", voice])
    if music is None or music < -70:
        return None
    gain = max(-45.0, min(6.0, ((vo if vo is not None and vo > -70 else -20.0) - BED_BELOW) - music))
    if notes is not None:
        notes.append(f"🎵 Nhạc lót dưới lời: «{bed.get('name') or 'thư viện nhạc'}» (nhỏ, tự hạ khi đang đọc). ")
    return {"gain_db": round(gain, 1)}


BED_ORIG_LOW = 0.25             # BGM_ORIG=low: tiếng gốc (xèo xèo, tiếng dao...) ≈ -12 dB dưới nhạc nền


async def music_bed(input_mp4: str, music_path: str | None, out_wav: str, out_dur: float, trim: float = 0.0,
                    orig: str = "low", stretch: float = 1.0) -> str:
    """Video KHÔNG LỜI: lớp tiếng = nhạc nền mới (từ giây `trim`, lặp nếu ngắn, vào 1s / ra 2s) + tiếng gốc theo
    BGM_ORIG: low = nhạc là chính, tiếng gốc nhỏ (≈ -12 dB, vẫn nghe tiếng xèo xèo); keep = tiếng gốc là chính, nhạc
    nằm dưới và tự hạ khi tiếng gốc to; off = chỉ nhạc mới. Không có nhạc thì chỉ tiếng gốc. Tiếng gốc chậm theo
    hình khi kéo dài video; nhạc giữ nguyên nhịp."""
    has_orig = orig != "off" and await ff.has_audio(input_mp4)
    slow = f"atempo={1 / stretch:.4f}," if abs(stretch - 1) > 1e-3 else ""
    og = f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo,{slow}apad=whole_dur={out_dur:.2f}"
    ins = ["-i", input_mp4]
    if music_path:
        ins += ["-stream_loop", "-1", "-ss", f"{max(0.0, trim):.3f}", "-i", music_path]
        mus = (f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{out_dur:.2f},asetpts=PTS-STARTPTS,"
               f"afade=t=in:st=0:d=1.0,afade=t=out:st={max(0.0, out_dur - 2.0):.2f}:d=2.0")
        if not has_orig:
            fc = f"{mus},apad=whole_dur={out_dur:.2f}[a]"
        elif orig == "keep":
            fc = (f"{mus},volume={MUSIC_BG_LEVEL}[mus];{og},asplit=2[og][key];"
                  "[mus][key]sidechaincompress=threshold=0.05:ratio=4:attack=40:release=350[musd];"
                  "[og][musd]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            fc = f"{mus}[mus];{og},volume={BED_ORIG_LOW}[og];[og][mus]amix=inputs=2:duration=first:normalize=0[a]"
    elif has_orig or await ff.has_audio(input_mp4):
        fc = f"{og}[a]"
    else:
        fc = f"anullsrc=r=44100:cl=stereo,atrim=0:{out_dur:.2f}[a]"
    await ff._run_ffmpeg("-y", *ins, "-filter_complex", fc, "-map", "[a]", "-t", f"{out_dur:.2f}",
                      "-ar", "44100", "-ac", "2", out_wav)
    return out_wav


async def render_music(input_mp4: str, music_path: str, output_mp4: str,
                       duration: int = 0, speed: float = 1.0,
                       trim: float = 0.0, delay: float = 0.0, keep_orig: bool = False):
    """Chế độ nhảy/nhạc: bỏ tiếng gốc, lồng nhạc mới, video đổi tốc độ nhẹ cho khớp beat.
    keep_orig=True (nhạc nền): GIỮ tiếng gốc, nhạc mới nằm dưới (tự hạ khi tiếng gốc to), không đổi tốc độ video;
    nhạc ngắn hơn video thì lặp lại."""
    vid_dur = await ff.probe_duration(input_mp4)
    orig = keep_orig and await ff.has_audio(input_mp4)
    music_len = 1e9 if keep_orig else max(0.0, await ff.probe_duration(music_path) - trim) + delay
    out_dur = min(vid_dur / speed, music_len, config.HARD_MAX)
    if duration > 0:
        out_dur = min(out_dur, duration)
    a = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
    if delay > 0.01:
        ms = int(delay * 1000)
        a += f"adelay={ms}|{ms},"
    if orig:
        # tiếng gốc đệm im lặng tới hết video: amix=duration=first lấy độ dài tiếng gốc, video có tiếng gốc ngắn hơn
        # hình thì trước đây nhạc nền tắt theo
        a += (f"volume={MUSIC_BG_LEVEL}[mus];[0:a]aformat=sample_rates=44100:channel_layouts=stereo,"
              f"apad=whole_dur={out_dur:.2f},asplit=2[og][key];"
              "[mus][key]sidechaincompress=threshold=0.05:ratio=4:attack=40:release=350[musd];"
              "[og][musd]amix=inputs=2:duration=first:normalize=0,")
    a += (f"{filtergraph.LOUDNORM},{filtergraph.AOUT},"                            # nhạc chuẩn -14 LUFS như nền tảng nhạc
          f"afade=t=in:st=0:d={0.4 if keep_orig else 0.01},afade=t=out:st={max(0.0, out_dur - 0.8):.2f}:d=0.8,"
          f"apad=whole_dur={out_dur:.2f}[a]")
    v = ("[0:v]crop=in_w*0.96:in_h*0.96,"
         "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
         f"setpts=PTS/{speed:.4f},fps=30,hqdn3d=1.2:1.2:3:3,eq=contrast=1.05:brightness=0.012:saturation=1.04,vibrance=intensity=0.12,cas=strength=0.5[v];")
    audio_in = ["-i", input_mp4, *(["-stream_loop", "-1"] if keep_orig else []), "-ss", f"{trim:.3f}", "-i", music_path]
    fc = await two_pass_loudness(v + a, audio_in, out_dur)
    await ff._run_ffmpeg(
        "-y", *ff.thread_args(),
        *audio_in,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        *encode_args(1280),
        "-t", f"{out_dur:.2f}",
        output_mp4,
    )
    return out_dur
