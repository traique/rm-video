"""Chạy ffmpeg/ffprobe (huỷ theo job, giết cả nhóm) + thăm dò (độ dài, có tiếng, khung khoá, chỗ im lặng), cắt / đóng gói lại, tự kiểm bộ lọc lúc khởi động, số lõi / RAM của container, đường dẫn tài nguyên (font, logo).

Tách từ core_video.py (Phase 6); core_video.py còn là mặt tiền tương thích."""
import logging
import math
import os
import re
import shutil
import subprocess
import config
import subs as subs_mod
from rmvideo.media import proc
from rmvideo.media import ff, fetch, filtergraph, renderer, tts_engine, voiceplan  # noqa: F401


def _ensure_ffmpeg():
    """Ưu tiên ffmpeg/ffprobe có sẵn trên máy; thiếu mới nhờ static-ffmpeg tải (~70MB, một lần).
    Tải lỗi thì chỉ cảnh báo: bot vẫn khởi động được, job sẽ báo lỗi rõ ràng khi cần ffmpeg."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception as e:  # mạng chặn GitHub, ổ chỉ đọc...
        logging.error("Không có ffmpeg trên máy và không tải được static-ffmpeg: %s", e)


_ensure_ffmpeg()


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")


FONT_PATH = os.path.join(ASSETS_DIR, "BeVietnamPro-ExtraBold.ttf")


def kill_running():
    """Giết MỌI tiến trình con đang theo dõi (tắt bot). Huỷ một job: proc.kill_owner(job.id) — chỉ tiến trình của job đó."""
    proc.kill_all()


async def _run_ffmpeg_full(*args: str, tool: str = "ffmpeg", stderr_keep: int | None = None) -> tuple:
    """Chạy ffmpeg/ffprobe (huỷ job là giết được ngay, cả nhóm). Trả (stdout, stderr). stderr_keep=None: giữ trọn
    stderr cho caller phân tích log (vd ebur128)."""
    r = await proc.run([tool, *args], stdout=True, stderr_keep=stderr_keep)
    return r.stdout.decode(errors="ignore"), r.stderr


async def _run_ffmpeg(*args: str, tool: str = "ffmpeg"):
    """Chỉ cần stdout: stderr chỉ giữ phần cuối để báo lỗi (render in hàng MB log không làm phình RAM)."""
    return (await _run_ffmpeg_full(*args, tool=tool, stderr_keep=proc.STDERR_KEEP))[0]


# Botkeep không có ffmpeg hệ thống: static-ffmpeg tải bản của nó (3.0 -> FFmpeg 8.0), khác bản máy phát triển/CI
# (6.1). Lỗi tương thích (vd framelog của ebur128, chọn kênh aresample->apad) trước đây chỉ lộ ra khi đang làm video;
# giờ một lệnh ~1 giây lúc khởi động dùng đủ bộ lọc + bộ mã hoá thật và báo ngay bộ lọc nào hỏng.
SELFTEST_VIDEO = (
    "[0:v]split=2[a][b];"
    "[a]crop=in_w*0.96:in_h*0.96,scale=360:640:flags=lanczos,hqdn3d=1.2:1.2:3:3,"
    "eq=contrast=1.05:brightness=0.012:saturation=1.06,vibrance=intensity=0.2,"
    "lutrgb=g='clip(val*0.99+0.5\\,0\\,255)',cas=strength=0.5,unsharp=3:3:0.6:3:3:0,"
    "drawbox=x=0:y=0:w=20:h=20:color=black@0.45:t=fill,"
    "drawtext=fontfile={font}:textfile={txt}:expansion=none:fontsize=24:fontcolor=white:borderw=2:"
    "box=1:boxcolor=black@0.45:boxborderw=8:x=(w-text_w)/2:y=20:enable='between(t,0,1)':"
    "alpha='if(lt(t,0.4),t/0.4,1)',"
    "subtitles=filename={ass}:fontsdir={fonts},"
    "format=yuv420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)',setsar=1,"
    # hiệu ứng trend của 📸 (trendfx): màu, bật màu, glitch, lật gương, nháy, hạt phim, viền tối, phát sáng
    "curves=preset=vintage,colorbalance=rs=-0.04:bs=0.07,hue=s='min(1.2,1.2*exp(-t/0.4))',hue=h='10*sin(2*PI*t)',"
    "rgbashift=rh=-8:bh=8:gv=2:enable='between(t,0.2,0.3)',hflip=enable='between(t,0.5,0.8)',"
    "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,0.9,0.95)',noise=alls=9:allf=t,"
    "vignette=angle=PI/5,split=2[ga][gb];[gb]scale=90:160,gblur=sigma=6,scale=360:640[gc];"
    "[ga][gc]blend=all_mode=screen:all_opacity=0.3,format=yuv420p,setsar=1[a2];"
    "[b]zoompan=z='1.05+0.05*on/29':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=30:s=360x640:fps=30,"
    "trim=duration=1,setpts=PTS-STARTPTS,format=yuv420p,setsar=1[b2];"
    "[a2]trim=duration=1,setpts=PTS-STARTPTS[a3];"
    "[a3][b2]xfade=transition=slideleft:duration=0.3:offset=0.5,"
    "eq=brightness='0.2*exp(-(t-0.5)/0.08)*gte(t,0.5)':eval=frame,fade=t=in:st=0:d=0.1[v]")


SELFTEST_AUDIO = (
    "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,highpass=f=75,equalizer=f=230:t=q:w=1.1:g=1.8,"
    "deesser=i=0.35:m=0.5:f=0.5,acompressor=threshold=0.1:ratio=2.5:attack=12:release=140:makeup=1.3,"
    "aecho=0.9:0.55:13|22:0.10|0.07,asplit=2[vo][sc];"
    "[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume='1-0.5*gte(t,0.5)':eval=frame[amb];"
    "[amb][sc]sidechaincompress=threshold=0.02:ratio=6:attack=100:release=500[ambd];"
    "[vo][ambd]amix=inputs=2:duration=longest:weights='1 0.5',atempo=1.05,ebur128,"
    "{loudnorm},{aout},apad=whole_dur=1.5,afade=t=out:st=1:d=0.3,alimiter=limit=0.89:level=false,"
    "atrim=duration=1.2[a]")


def selftest_sync() -> tuple:
    """(True, "phiên bản") hoặc (False, đuôi lỗi ffmpeg). Chạy đồng bộ (~1 giây)."""
    import tempfile
    try:
        ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30).stdout.split("\n")[0]
    except Exception as e:
        return False, f"không chạy được ffmpeg: {e}"
    with tempfile.TemporaryDirectory() as td:
        txt, ass = os.path.join(td, "t.txt"), os.path.join(td, "s.ass")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("Thử 50% chữ")
        subs_mod.write_ass([(0.0, 0.5, "Xin"), (0.5, 1.0, "chào")], ass)
        fc = (SELFTEST_VIDEO.format(font=FONT_PATH, txt=txt, ass=ass, fonts=ASSETS_DIR) + ";"
              + SELFTEST_AUDIO.replace("{loudnorm}", filtergraph.LOUDNORM).replace("{aout}", filtergraph.AOUT))
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-f", "lavfi", "-i", "testsrc2=s=480x854:r=30:d=1",
                            "-f", "lavfi", "-i", "sine=f=300:d=1", "-f", "lavfi", "-i", "anoisesrc=d=1.2:a=0.05",
                            "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset",
                            "ultrafast", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        err = [ln for ln in r.stderr.splitlines() if ln.strip()]
        return False, f"{ver}: " + " | ".join(err[-6:])[-600:]
    return True, ver


async def probe_duration(path: str) -> float:
    out = await _run_ffmpeg(
        "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
        tool="ffprobe",
    )
    return float(out.strip())


async def has_audio(path: str) -> bool:
    out = await _run_ffmpeg(
        "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", path, tool="ffprobe",
    )
    return "audio" in out


async def extract_audio(input_mp4: str, out_mp3: str, max_dur: float | None = None):
    """Bóc audio 16kHz mono 32kbps cho Whisper: 90s ≈ 0.4MB, 10 phút ≈ 2.4MB (hạn của Groq 25MB).
    Nguồn không có luồng tiếng: xuất file im lặng (trước đây ffmpeg lỗi "does not contain any stream" làm hỏng job)."""
    if not await has_audio(input_mp4):
        dur = min(await probe_duration(input_mp4), max_dur or config.HARD_MAX)
        await _run_ffmpeg("-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", f"{dur:.2f}", "-b:a", "32k", out_mp3)
        return
    await _run_ffmpeg(
        "-y", "-i", input_mp4, "-t", f"{max_dur or config.HARD_MAX:.2f}", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        out_mp3
    )


async def keyframe_times(path: str) -> list:
    """Mốc các khung khoá (I-frame) của luồng hình, tính từ đầu file. Chỉ giải mã khung khoá nên nhanh."""
    try:
        out = await _run_ffmpeg("-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
                                "frame=pts_time", "-of", "csv=p=0", path, tool="ffprobe")
        st = await _run_ffmpeg("-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", path,
                               tool="ffprobe")
        t0 = float(st.strip() or 0)
    except (RuntimeError, ValueError):
        return []
    out_t = []
    for x in out.split():
        try:
            out_t.append(round(float(x.strip(",")) - t0, 6))    # đủ chính xác: làm tròn 3 số có thể rơi trước khung khoá
        except ValueError:
            continue
    return sorted(set(out_t))


def snap_to_keyframes(bounds: list, kfs: list, tol: float = 0.4) -> list:
    """Dời các điểm cắt (trừ đầu/cuối) về khung khoá gần nhất nếu lệch ≤ tol giây: cắt đúng khung khoá thì
    chép luồng được, không phải mã hoá lại. Điểm cắt chọn ở chỗ chuyển cảnh/ngưng nói, mà bộ mã hoá thường đặt
    khung khoá ngay chỗ chuyển cảnh, nên phần lớn điểm cắt dời rất ít."""
    if not kfs or len(bounds) < 3:
        return list(bounds)
    out = [bounds[0]]
    for b in bounds[1:-1]:
        k = min(kfs, key=lambda x: abs(x - b))
        out.append(k if abs(k - b) <= tol and out[-1] < k < bounds[-1] else b)
    out.append(bounds[-1])
    return out


def is_keyframe(t: float, kfs: list, tol: float = 0.0005) -> bool:
    return t <= tol or any(abs(t - k) <= tol for k in kfs)


async def cut(src: str, dst: str, start: float, end: float, copy: bool = False) -> str:
    """Cắt đoạn [start, end]. copy=True (start là khung khoá): chép luồng, không mã hoá lại — nhanh và không mất
    thêm một thế hệ nén; kiểm độ dài, lệch thì mã hoá lại như cũ. Trả "copy" hoặc "encode"."""
    if copy:
        try:
            # -ss nhích 0.5ms SAU khung khoá: ffmpeg lùi về đúng khung khoá đó, không lùi nhầm về khung khoá trước
            await _run_ffmpeg("-y", "-ss", f"{start + 0.0005:.4f}", "-i", src, "-t", f"{end - start:.4f}", "-map", "0:v:0",
                              "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero",
                              "-movflags", "+faststart", dst)
            if abs(await probe_duration(dst) - (end - start)) < 0.15:
                return "copy"
            logging.warning("Cắt chép luồng lệch độ dài, mã hoá lại đoạn %.1f-%.1f", start, end)
        except (RuntimeError, ValueError) as e:
            logging.warning("Cắt chép luồng lỗi (%s), mã hoá lại", str(e)[:80])
    await _run_ffmpeg("-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k",
                      "-movflags", "+faststart", dst)
    return "encode"


async def silences(path: str, noise_db: int = -35, min_len: float = 0.35) -> list:
    """Các khoảng im lặng [(start, end)] — chỗ người nói ngưng, hợp để cắt phần."""
    r = await proc.run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_len}",
                        "-f", "null", "-"], stderr_keep=None, check=False)
    st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    return list(zip(st, en))


async def remux(src: str, dst: str):
    """Chế độ chỉ tải về: không encode lại, chỉ đóng gói mp4 + faststart để xem/gửi được ngay."""
    await _run_ffmpeg("-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst)


def cgroup_cpus() -> float:
    """Số lõi CPU container thật sự được dùng (cgroup v2 cpu.max / v1 cfs_quota), không phải lõi máy chủ."""
    try:
        q, per = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
        if q != "max":
            return max(0.1, int(q) / int(per))
    except (OSError, ValueError):
        pass
    try:
        q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
        per = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if q > 0:
            return max(0.1, q / per)
    except (OSError, ValueError):
        pass
    return float(os.cpu_count() or 1)


def mem_available_mb() -> int | None:
    """RAM còn dùng được (MB): giới hạn cgroup trừ phần đang dùng, và MemAvailable của máy; lấy số nhỏ hơn.
    Không đọc được gì thì None."""
    vals = []
    for lim_p, use_p in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                         ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            lim = open(lim_p).read().strip()
            use = int(open(use_p).read().strip())
            if lim != "max" and int(lim) < (1 << 50):
                vals.append((int(lim) - use) >> 20)
                break
        except (OSError, ValueError):
            continue
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                vals.append(int(line.split()[1]) >> 10)
                break
    except (OSError, ValueError):
        pass
    return min(vals) if vals else None


def ffmpeg_threads() -> int:
    """FFMPEG_THREADS > 0 thì dùng đúng số đó; 0 = theo số lõi container, làm tròn lên (1.5 vCPU -> 2), tối đa 8.
    Trước đây viết cứng -threads 1: sang máy nhiều lõi vẫn chạy một lõi."""
    if config.FFMPEG_THREADS > 0:
        return config.FFMPEG_THREADS
    return max(1, min(8, math.ceil(cgroup_cpus() - 0.01)))


def thread_args() -> list:
    """Đặt TRƯỚC -i: số luồng bộ lọc (toàn cục) + luồng giải mã đầu vào chính."""
    n = str(ffmpeg_threads())
    return ["-filter_complex_threads", n, "-threads", n]
