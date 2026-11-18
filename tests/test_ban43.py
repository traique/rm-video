"""Bản 43: lỗi bắt được khi chạy thật video «mực hấp gừng» (16/11/2026) + bộ nhận diện «Bếp Đong Đủ» làm mặc định.

- chữ hook "MỰC HẤP GÙNG GIÒN NGỌT" tràn hai mép video và sai dấu (GỪNG -> GÙNG);
- Gemini quá tải (503) bị hiểu nhầm là "không nhận ảnh": AI viết lời không nhìn hình, lời lệch cảnh;
- Kaggle chờ máy 30 phút ở bước bóc lời; lệnh huỷ phiên luôn 403 (gửi tên notebook thay vì mã phiên);
- Whisper kéo dài một chữ phủ cả quãng người gốc đang nói: không nghe lại, video im 14-16 giây;
- màn kết đè 2,2 giây cuối (che món thành phẩm, âm hiệu chen câu cuối)."""
import asyncio
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import config
import cover
import kaggle_api
import kaggle_worker
import prompt_engine
from rmvideo.app import brand
from rmvideo.app import settings as schema
from rmvideo.media import ff, renderer, textfit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_LOGO = os.path.join(ROOT, "brand", "bep_dong_du", "logo.png")


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True, timeout=120)


# ---------------- chữ vừa khung ----------------

def test_textfit_matches_real_font_widths():
    pil = pytest.importorskip("PIL.ImageFont")
    f = pil.truetype(ff.FONT_PATH, 1000)
    for s in ("MỰC HẤP GỪNG GIÒN NGỌT", "Cá kèo giòn không văng dầu", "ƯỚP 500G THỊT, 2 MUỖNG NƯỚC MẮM", "Bếp Đong Đủ"):
        assert textfit.em(s) == pytest.approx(f.getlength(s) / 1000, rel=0.02)


def test_title_layout_wraps_wide_uppercase():
    lines, fs = renderer.title_layout("MỰC HẤP GỪNG GIÒN NGỌT", 720, 1280)
    assert lines == ["MỰC HẤP GỪNG", "GIÒN NGỌT"] and fs == 50          # bản 42: một dòng 50px, rộng 752px > 720
    lines, fs = renderer.title_layout("THỊT KHO", 720, 1280)
    assert lines == ["THỊT KHO"] and fs == 50
    for W, H in ((720, 1280), (1080, 1920)):
        for t in ("BÍ QUYẾT LÀM MỰC HẤP GỪNG GIÒN NGỌT KHÔNG DAI", "WWWWWWWWWWWWWWWWWWWWWWWW"):   # một từ dài hơn khung
            lines, fs = renderer.title_layout(t, W, H)
            assert len(lines) <= 3 and max(textfit.em(x) for x in lines) * fs <= renderer.text_room(W, 16 * H // 1280)


@pytest.mark.parametrize("W,H", [(720, 1280), (1080, 1920)])
def test_title_pixels_stay_inside_frame(tmp_path, monkeypatch, W, H):
    """Dựng thật bằng FFmpeg: chữ hook không chạm 3% mép trái / phải."""
    monkeypatch.setattr(config, "BRAND_COLOR", "0")
    lines, fs = renderer.title_layout("MỰC HẤP GỪNG GIÒN NGỌT", W, H)
    files = renderer.write_lines("\n".join(lines), str(tmp_path), "title")
    vf = "format=rgb24" + renderer._brand_filter(W, H, 3.0, files, None, None, title_end=2.0, title_fs=fs)
    out = str(tmp_path / "f.rgb")
    _ff("-f", "lavfi", "-i", f"color=black:s={W}x{H}:d=1", "-vf", vf, "-frames:v", "1", "-f", "rawvideo", out)
    img = np.fromfile(out, np.uint8).reshape(H, W, 3)
    edge = int(W * 0.03)
    assert img[:, :, :].max() > 200                                           # có chữ
    assert img[:, :edge].max() < 60 and img[:, W - edge:].max() < 60          # không chạm mép


def test_cards_and_cover_fit():
    assert renderer.fit_one("Bếp Đong Đủ", 720, 62) == 62
    long_name = "Bếp Nhà Mình Đong Đủ Nấu Ngon Mỗi Ngày"
    fs = renderer.fit_one(long_name, 720, 62)
    assert fs < 62 and textfit.em(long_name) * fs <= renderer.text_room(720, 0)
    txt, size = cover.layout_title("MỰC HẤP GỪNG GIÒN NGỌT")
    assert all(textfit.em(x) * size * 1280 <= 720 * 0.9 for x in txt.split("\n"))


# ---------------- hook sai dấu ----------------

def test_hook_marks_repaired_from_title_and_script():
    ref = "Bí quyết làm mực hấp gừng thơm ngon đơn giản tại nhà"
    assert prompt_engine.clean_hook("MỰC HẤP GÙNG GIÒN NGỌT", ref) == "MỰC HẤP GỪNG GIÒN NGỌT"
    assert prompt_engine.clean_hook("mực hấp gừng giòn ngọt") == "MỰC HẤP GỪNG GIÒN NGỌT"      # AI viết chữ thường
    # khác dấu thanh thì KHÔNG đổi ("văng" ≠ "vàng"); chữ có thật trong lời giữ nguyên
    assert prompt_engine.clean_hook("cá kèo không văng dầu", "chiên vàng đều") == "CÁ KÈO KHÔNG VĂNG DẦU"
    assert prompt_engine.clean_hook("CÁ KHÔNG VANG DẦU", "chiên cá không văng dầu") == "CÁ KHÔNG VĂNG DẦU"


# ---------------- Gemini quá tải ----------------

class _Resp:
    def __init__(self, text):
        self.text = text


def _fake_genai(monkeypatch, behaviour, calls):
    class Models:
        async def generate_content(self, model, contents, config):
            calls.append((model, isinstance(contents, list)))
            return behaviour(model)

    class Client:
        def __init__(self, api_key):
            self.aio = type("A", (), {"models": Models()})()

    monkeypatch.setattr(prompt_engine.genai, "Client", Client)

    async def no_sleep(s):
        return None
    monkeypatch.setattr(prompt_engine.asyncio, "sleep", no_sleep)


@pytest.mark.skipif(prompt_engine.genai is None, reason="cần google-genai")
def test_overloaded_model_falls_back_to_backup_model(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "GEMINI_MODEL", "main-lite")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", "backup-1, backup-2")
    calls: list = []

    def behaviour(model):
        if model != "backup-2":
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return _Resp("ok")
    _fake_genai(monkeypatch, behaviour, calls)
    assert asyncio.run(prompt_engine.call_gemini("viết", [b"jpg"])) == "ok"
    assert [m for m, _ in calls] == ["main-lite"] * 3 + ["backup-1"] * 3 + ["backup-2"]
    assert all(img for _, img in calls)                                        # ảnh vẫn gửi kèm ở mọi lượt


@pytest.mark.skipif(prompt_engine.genai is None, reason="cần google-genai")
def test_non_overload_error_does_not_try_backups(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "GEMINI_MODEL", "main")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", "backup")
    calls: list = []

    def behaviour(model):
        raise RuntimeError("401 UNAUTHENTICATED API key not valid")
    _fake_genai(monkeypatch, behaviour, calls)
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(prompt_engine.call_gemini("viết"))
    assert [m for m, _ in calls] == ["main"]


def test_overload_is_not_an_image_problem():
    assert not prompt_engine._image_rejected("503 UNAVAILABLE {'message': 'high demand'}")
    assert not prompt_engine._image_rejected("429 RESOURCE_EXHAUSTED")
    assert not prompt_engine._image_rejected("")                               # quá giờ
    assert prompt_engine._image_rejected("400 INVALID_ARGUMENT: Unable to process input image")
    assert prompt_engine.fallback_models() == ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"] or \
        config.GEMINI_FALLBACK_MODELS != schema.BY_KEY["GEMINI_FALLBACK_MODELS"].default


# ---------------- Kaggle: chờ máy, phiên tự thoát ----------------

def test_wait_gives_up_when_queued_too_long(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(kaggle_api.time, "time", lambda: now[0])
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("QUEUED", ""))

    def tick(s):
        now[0] += 60
    with pytest.raises(kaggle_api.KaggleError, match="chờ máy quá 8 phút"):
        kaggle_api.wait("x", 1800, every=60, sleep=tick, queue_max=8 * 60)
    assert now[0] < 10 * 60
    states = iter([("QUEUED", "")] * 3 + [("RUNNING", "")] * 20 + [("COMPLETE", "")])
    now[0] = 0.0
    monkeypatch.setattr(kaggle_api, "status", lambda slug: next(states))
    assert kaggle_api.wait("x", 1800, every=60, sleep=tick, queue_max=8 * 60) == "COMPLETE"   # đang chạy: chờ tiếp


def _header(text: str) -> str:
    return text.split('APP = "/tmp/rm_app"')[0]


def test_expired_kernel_exits_before_installing_anything():
    import time
    queue_max = 8 * 60
    exp = kaggle_worker.with_expiry({}, queue_max)["expires_at"]
    assert exp <= time.time() + queue_max - kaggle_worker.EXPIRY_MARGIN + 1    # hết hạn TRƯỚC lúc bot thôi chờ
    old = kaggle_worker.with_expiry({"kind": "analyze"}, 0)
    r = subprocess.run([sys.executable, "-c", _header(kaggle_worker.kernel_text(old)) + "print('CHẠY TIẾP')"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "thoát ngay" in r.stdout and "CHẠY TIẾP" not in r.stdout
    fresh = kaggle_worker.with_expiry({"kind": "analyze"}, 600)
    r = subprocess.run([sys.executable, "-c", _header(kaggle_worker.kernel_text(fresh)) + "print('CHẠY TIẾP')"],
                       capture_output=True, text=True, timeout=60)
    assert "CHẠY TIẾP" in r.stdout
    r = subprocess.run([sys.executable, "-c", _header(kaggle_worker.kernel_text({})) + "print('CHẠY TIẾP')"],
                       capture_output=True, text=True, timeout=60)
    assert "CHẠY TIẾP" in r.stdout                                              # việc cũ không có hạn: chạy


def test_no_fake_cancel_call():
    assert not hasattr(kaggle_api, "cancel")         # API công khai chỉ huỷ theo mã phiên (không lấy được): bỏ hẳn


# ---------------- bộ nhận diện mặc định ----------------

def test_schema_defaults_are_the_kit():
    d = {k: schema.BY_KEY[k].default for k in brand.OLD_DEFAULTS}
    assert d == {"BRAND_NAME": "Bếp Đong Đủ", "BRAND_TAGLINE": "Đong đủ, nấu là ngon",
                 "BRAND_CLOSE": "Đong đủ là ngon, cả nhà nấu thử nha", "BRAND_COLOR": "#F28C28",
                 "BRAND_LOOK": "auto", "BRAND_SERIES": "Hôm nay nấu gì", "COVER_TEMPLATE": "kenh",   # 1.0.0: tự chọn tông
                 "POST_RECIPE": "1", "TIPS": "video", "REMAKE_STYLE": "strong", "SHORT_VERSION": "on"}
    assert os.path.exists(KIT_LOGO) and os.path.getsize(KIT_LOGO) <= config.LOGO_MAX_BYTES


def test_zero_turns_brand_text_off(monkeypatch):
    monkeypatch.setattr(config, "BRAND_NAME", "0")
    monkeypatch.setattr(config, "BRAND_COLOR", "0")
    assert config.brand_text("BRAND_NAME") == "" and renderer.brand_hex() == ""
    assert schema.BY_KEY["BRAND_COLOR"].check("0") == "0"
    monkeypatch.setattr(config, "BRAND_NAME", "Bếp Đong Đủ")
    assert brand.end_tail(30, 90) == renderer.OUTRO_DUR and brand.end_tail(89, 90) == 0.0
    assert brand.end_tail(5, 90) == 0.0                                          # video quá ngắn: không màn kết


def test_apply_default_kit_once_keeps_user_choices(tmp_path, monkeypatch):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"REMAKE_STYLE": "off", "BRAND_LOOK": "am_moc", "TIPS": "off", "QUALITY": "fast"}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(sf))
    for k in ("REMAKE_STYLE", "BRAND_LOOK", "TIPS", "QUALITY", *brand.OLD_DEFAULTS):
        monkeypatch.setattr(config, k, getattr(config, k))                   # save_settings áp vào config: trả lại
    state = str(tmp_path / "brand_state.json")
    assert sorted(brand.apply_default_kit(state)) == ["REMAKE_STYLE", "TIPS"]
    over = json.loads(sf.read_text())
    assert over == {"BRAND_LOOK": "am_moc", "QUALITY": "fast"}                 # đã chọn khác mặc định cũ: giữ
    sf.write_text(json.dumps({"REMAKE_STYLE": "off"}))                         # sau đó tự chọn lại «Tắt»: tôn trọng
    assert brand.apply_default_kit(state) == [] and json.loads(sf.read_text()) == {"REMAKE_STYLE": "off"}


def test_default_logo_and_sting_used_when_nothing_uploaded(tmp_path, monkeypatch):
    kit = os.path.join(ROOT, "brand", "bep_dong_du")
    monkeypatch.setattr(ff, "LOGO_PATH", str(tmp_path / "none.png"))
    monkeypatch.setattr(ff, "STING_PATH", str(tmp_path / "none.mp3"))
    monkeypatch.setattr(ff, "DEFAULT_LOGO", os.path.join(kit, "logo.png"))
    monkeypatch.setattr(ff, "DEFAULT_STING", os.path.join(kit, "sting.mp3"))
    monkeypatch.setattr(config, "BRAND_LOGO", "1")
    monkeypatch.setattr(config, "BRAND_STING", "1")
    assert ff.logo_path().endswith("bep_dong_du/logo.png")
    spec = kaggle_worker._common({})
    assert spec["logo_b64"] and spec["sting_b64"]
    (tmp_path / "none.png").write_bytes(b"x")                                 # đã tải logo riêng: dùng logo đó
    assert ff.logo_path() == str(tmp_path / "none.png")


# ---------------- màn kết nối thêm ----------------

def test_render_appends_end_card_after_content(tmp_path, monkeypatch):
    for k, v in (("BRAND_NAME", "Bếp Đong Đủ"), ("BRAND_TAGLINE", "Đong đủ, nấu là ngon"), ("BRAND_LOGO", "0"),
                 ("BRAND_STING", "0"), ("BRAND_INTRO", "0"), ("QUALITY", "fast")):
        monkeypatch.setattr(config, k, v)
    src, voice, out = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav"), str(tmp_path / "o.mp4")
    _ff("-f", "lavfi", "-i", "testsrc2=s=360x640:d=8:r=25", "-f", "lavfi", "-i", "sine=d=8", "-shortest", src)
    _ff("-f", "lavfi", "-i", "sine=f=500:d=10.2", voice)
    dur = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, None, None, None, tmp=str(tmp_path),
                                      tail=2.2, sting_after=9.0))
    assert dur == pytest.approx(10.2, abs=0.05)
    assert asyncio.run(ff.probe_duration(out)) == pytest.approx(10.2, abs=0.15)
    # khung cuối của video (giây 7,9) vẫn hiện nguyên trước màn kết; màn kết là khung đó giữ lại. Bản 50: màn kết
    # SÁNG (video tự quay vòng trên TikTok / Shorts): chỉ dải giữa khung (40-60% chiều cao) tối đi sau tên kênh
    a = str(tmp_path / "a.rgb")
    _ff("-ss", "7.6", "-i", out, "-frames:v", "1", "-vf", "scale=36:64,format=gray", "-f", "rawvideo", a)
    b = str(tmp_path / "b.rgb")
    _ff("-ss", "9.6", "-i", out, "-frames:v", "1", "-vf", "scale=36:64,format=gray", "-f", "rawvideo", b)
    fa, fb = (np.fromfile(x, np.uint8).astype(float).reshape(64, 36) for x in (a, b))
    assert fb[:20].mean() > fa[:20].mean() * 0.9 and fb[46:].mean() > fa[46:].mean() * 0.9   # trên / dưới dải: còn sáng
    assert fb[27:37, :3].mean() < fa[27:37, :3].mean() * 0.8                    # mép dải (không có chữ): tối đi
