"""Tạo bộ nhận diện kênh bằng FFmpeg (không cần thư viện ảnh): logo tròn nền trong suốt (tải lên ở trang cài đặt),
ảnh đại diện 1080x1080 cho Fanpage / TikTok, âm hiệu màn kết 1,3 giây (nốt nhạc tự tổng hợp, không vướng bản quyền).

    python brand/brand_kit.py "Bếp Đong Đủ" "Đong đủ, nấu là ngon" "#F28C28" brand/bep_dong_du
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "assets", "BeVietnamPro-ExtraBold.ttf")


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=120)


def _text(tmp: str, name: str, s: str) -> str:
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    return p


def _split(name: str) -> tuple[str, str]:
    """"Bếp Đong Đủ" -> ("BẾP", "ĐONG ĐỦ"): chữ đầu nhỏ ở trên, phần còn lại to ở dưới."""
    w = name.upper().split()
    return (w[0], " ".join(w[1:])) if len(w) > 1 else ("", w[0])


def logo(name: str, color: str, out: str, size: int = 512) -> None:
    c = color.lstrip("#")
    top, main = _split(name)
    with tempfile.TemporaryDirectory() as tmp:
        t1, t2 = _text(tmp, "a.txt", top), _text(tmp, "b.txt", main)
        r = size // 2 - 6
        fc = (f"color=c=0x{c}:s={size}x{size},format=rgba,"
              f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(hypot(X-{size // 2},Y-{size // 2}),{r}),255,0)',"
              f"drawtext=fontfile={FONT}:textfile={t1}:fontsize={size // 9}:fontcolor=white:x=(w-text_w)/2:"
              f"y=h*0.33-text_h/2,"
              f"drawtext=fontfile={FONT}:textfile={t2}:fontsize={int(size / 7.6)}:fontcolor=white:x=(w-text_w)/2:"
              f"y=h*0.53-text_h/2,"
              f"drawbox=x={int(size * 0.30)}:y={int(size * 0.68)}:w={int(size * 0.40)}:h={max(4, size // 64)}:"
              "color=white@0.9:t=fill")
        _ff("-f", "lavfi", "-i", fc, "-frames:v", "1", "-pix_fmt", "rgba", out)


def avatar(name: str, tagline: str, color: str, out: str, size: int = 1080) -> None:
    c = color.lstrip("#")
    top, main = _split(name)
    with tempfile.TemporaryDirectory() as tmp:
        t1, t2, t3 = _text(tmp, "a.txt", top), _text(tmp, "b.txt", main), _text(tmp, "c.txt", tagline)
        fc = (f"color=c=0x{c}:s={size}x{size},"
              f"drawtext=fontfile={FONT}:textfile={t1}:fontsize={size // 8}:fontcolor=white:x=(w-text_w)/2:"
              f"y=h*0.30-text_h/2,"
              f"drawtext=fontfile={FONT}:textfile={t2}:fontsize={size // 6}:fontcolor=white:x=(w-text_w)/2:"
              f"y=h*0.50-text_h/2,"
              f"drawbox=x={int(size * 0.32)}:y={int(size * 0.64)}:w={int(size * 0.36)}:h={size // 90}:"
              "color=white@0.9:t=fill,"
              f"drawtext=fontfile={FONT}:textfile={t3}:fontsize={size // 22}:fontcolor=white@0.95:x=(w-text_w)/2:"
              f"y=h*0.74-text_h/2")
        _ff("-f", "lavfi", "-i", fc, "-frames:v", "1", "-q:v", "2", out)


def sting(out: str) -> None:
    """Hai nốt "đinh - đong" (La 5 rồi Mi 6), tắt dần, chút vang phòng: 1,3 giây."""
    _ff("-f", "lavfi", "-i", "sine=f=880:d=0.28:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=f=1318.5:d=1.0:sample_rate=44100",
        "-filter_complex",
        "[0]volume=0.5,afade=t=out:st=0.05:d=0.23[a];[1]volume=0.5,afade=t=out:st=0.1:d=0.9[b];"
        "[a][b]concat=n=2:v=0:a=1,aecho=0.8:0.6:60:0.25,atrim=0:1.3,afade=t=out:st=1.1:d=0.2",
        "-ac", "1", "-b:a", "96k", out)


def main(argv: list[str]) -> None:
    name, tagline, color, out_dir = argv
    os.makedirs(out_dir, exist_ok=True)
    logo(name, color, os.path.join(out_dir, "logo.png"))
    avatar(name, tagline, color, os.path.join(out_dir, "avatar.jpg"))
    sting(os.path.join(out_dir, "sting.mp3"))
    print("Đã tạo:", ", ".join(sorted(os.listdir(out_dir))))


if __name__ == "__main__":
    main(sys.argv[1:])
