"""Bản 42 (S2): bộ nhận diện kênh — bộ màu, hạt phim, màu phụ đề, khung hook + màn kết + tagline, âm hiệu, mẫu ảnh
bìa có tên loạt + số tập, câu chốt cuối video. Mặc định (khoá trống / "goc") phải ra y như cũ."""
import asyncio
import os
import subprocess

import pytest

import config
import cover
import subs
from rmvideo.app import brand
from rmvideo.media import ff, filtergraph, renderer


@pytest.fixture
def cfg(monkeypatch):
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    for k, d in brand.OLD_DEFAULTS.items():      # như khi CHƯA đặt nhận diện kênh (mặc định bản 43: test_ban43.py)
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    return monkeypatch


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=120)


def test_default_look_unchanged_and_presets_valid(cfg, tmp_path):
    base = filtergraph.look_chain("cook", 720, 1280, 360, 640)
    assert "colorbalance" not in base and "noise" not in base
    src = str(tmp_path / "src.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:d=0.2", src)
    for look in filtergraph.LOOKS:
        cfg.setattr(config, "BRAND_LOOK", look)
        chain = filtergraph.look_chain("cook", 720, 1280, 360, 640)
        assert chain != base
        _ffmpeg("-i", src, "-vf", chain, "-frames:v", "1", str(tmp_path / f"{look}.png"))   # cú pháp chạy được thật
    cfg.setattr(config, "BRAND_GRAIN", "1")
    assert filtergraph.GRAIN in filtergraph.look_chain("cook", 1080, 1920)
    assert filtergraph.GRAIN not in filtergraph.look_chain("cook", 720, 1280)            # 720p: không hạt
    assert filtergraph.GRAIN not in filtergraph.look_chain("cook", 1080, 1920, grain=False)   # ảnh bìa: không hạt


def test_subtitle_brand_color():
    assert subs.ass_color("#FF8A00") == "&H00008AFF"
    assert subs.style_line("karaoke") == subs._STYLES["karaoke"]
    assert "&H00008AFF" in subs.style_line("karaoke", "#FF8A00") and "&H0000E1FF" not in subs.style_line("karaoke", "#FF8A00")
    assert subs.style_line("box", "#FF8A00") == subs._STYLES["box"]                   # chữ trắng hộp đen: giữ


def test_brand_filter_defaults_and_brand(cfg):
    old = renderer._brand_filter(720, 1280, 20, ["t.txt"], "o.txt", None)
    assert "boxcolor=black@0.45" in old and "tagline" not in old
    cfg.setattr(config, "BRAND_COLOR", "#2BB673")
    new = renderer._brand_filter(720, 1280, 20, ["t.txt"], "o.txt", None, tagline_file="tagline.txt")
    assert "boxcolor=0x2BB673@0.88" in new and "textfile=tagline.txt" in new and "fontcolor=0x2BB673" in new


def test_render_with_brand_kit_and_sting(cfg, tmp_path):
    """Dựng thật: bộ màu điện ảnh + hạt (720p nên bỏ hạt) + khung hook màu kênh + màn kết có tagline + âm hiệu."""
    for k, v in (("BRAND_NAME", "Bếp Nhà Quế"), ("BRAND_TAGLINE", "Nấu nhanh, ăn là ghiền"), ("BRAND_COLOR", "#FF8A00"),
                 ("BRAND_LOOK", "dien_anh"), ("BRAND_GRAIN", "1"), ("BRAND_LOGO", "0"), ("BRAND_STING", "1")):
        cfg.setattr(config, k, v)
    sting = tmp_path / "sting.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=f=880:d=1.2", str(sting))
    cfg.setattr(ff, "STING_PATH", str(sting))
    src, voice, out = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav"), str(tmp_path / "o.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:d=8:r=25", "-f", "lavfi", "-i", "sine=f=300:d=8", "-shortest", src)
    _ffmpeg("-f", "lavfi", "-i", "sine=f=500:d=6", voice)
    dur = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, [(0.0, 8.0)], allow_hd=False,
                                      title="CÁNH GÀ CHIÊN", tmp=str(tmp_path)))
    assert os.path.exists(out) and abs(dur - 8.0) < 0.3
    assert asyncio.run(ff.has_audio(out))


def test_cover_template_with_series(cfg, tmp_path):
    for k, v in (("COVER_TEMPLATE", "kenh"), ("BRAND_COLOR", "#FF8A00"), ("BRAND_LOGO", "0")):
        cfg.setattr(config, k, v)
    src = str(tmp_path / "s.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:d=2:r=25", src)
    fc = cover.cover_filter("cook", 720, 1280, 360, 640, [], ["a.txt"], 0.05, False, "series.txt")
    assert "drawbox=x=0:y=ih-108" in fc and "textfile=series.txt" in fc and "fontcolor=0xFF8A00" in fc
    got = asyncio.run(cover.render_cover(src, 0.5, str(tmp_path), "cook", "Cánh gà chiên", series="CÔNG THỨC CHUẨN #3"))
    assert got and os.path.exists(got)


def test_series_numbers_follow_source_video(cfg, tmp_path):
    p = str(tmp_path / "b.json")
    assert brand.series_label("h1", p) == ""                                             # chưa đặt tên loạt
    cfg.setattr(config, "BRAND_SERIES", "Công thức chuẩn")
    cfg.setattr(config, "COVER_TEMPLATE", "kenh")
    assert brand.series_label("h1", p) == "CÔNG THỨC CHUẨN #1"
    assert brand.series_label("h2", p) == "CÔNG THỨC CHUẨN #2"
    assert brand.series_label("h1", p) == "CÔNG THỨC CHUẨN #1"                           # dựng lại: giữ số cũ


def test_close_line(cfg):
    segs = [{"text": "Cho gà vào chảo.", "ost": "new"}, {"text": "", "ost": "orig"}]
    assert not brand.add_close(segs)
    cfg.setattr(config, "BRAND_CLOSE", "Chúc cả nhà nấu ngon nha")
    assert brand.add_close(segs) and segs[0]["text"] == "Cho gà vào chảo. Chúc cả nhà nấu ngon nha."
    assert brand.add_close(segs) and segs[0]["text"].count("Chúc cả nhà") == 1              # không gắn hai lần
    cfg.setattr(config, "BRAND_CLOSE", "Nhớ đăng ký kênh nha")
    assert brand.close_line() == ""                                                       # kêu gọi đăng ký: bỏ
