"""1.1.2: cảnh người gốc nói vào máy (rmvideo/media/talking.py): giải mã YuNet, bỏ mặt tĩnh (tranh treo tường), quãng
có lời gốc + mặt to ở trên -> khúc kế hoạch dựng cận neo đáy; khung cận neo đáy trong chuỗi lọc, dải chữ, dựng thật."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import config
from rmvideo.media import edl, filtergraph, framing, talking


def _yunet_out(dets):
    """Đầu ra YuNet giả: dets [(stride, chỉ số ô, (dx, dy, log w, log h), y khoé miệng (tương đối ô), điểm)]."""
    out = {}
    for s in (8, 16, 32):
        n = (640 // s) ** 2
        out[f"cls_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{s}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{s}"] = np.zeros((1, n, 10), np.float32)
    for s, i, bb, my, sc in dets:
        out[f"cls_{s}"][0, i, 0] = sc
        out[f"obj_{s}"][0, i, 0] = sc
        out[f"bbox_{s}"][0, i] = bb
        out[f"kps_{s}"][0, i, 7] = my
        out[f"kps_{s}"][0, i, 9] = my
    return out


def test_decode_and_nms():
    # ô (hàng 10, cột 20) bước 16: tâm (20.5*16, 10.5*16), rộng / cao e^2 * 16 ≈ 118
    i = 10 * 40 + 20
    out = _yunet_out([(16, i, (0.5, 0.5, 2.0, 2.0), 1.0, 0.95), (16, i + 1, (-0.5, 0.5, 2.0, 2.0), 1.0, 0.9),
                      (32, 5, (0, 0, 1, 1), 0.5, 0.3)])
    got = talking.decode(out)
    assert len(got) == 1                                     # hai ô sát nhau = một mặt; điểm thấp bị loại
    x0, y0, x1, y1, sc, m = got[0]
    assert (x0 + x1) / 2 == pytest.approx(20.5 * 16) and (y1 - y0) == pytest.approx(np.exp(2) * 16, rel=1e-4)
    assert sc == pytest.approx(0.95) and m == pytest.approx(11 * 16)


def test_faces_scales_to_image(monkeypatch):
    i = 10 * 40 + 20
    fake = SimpleNamespace(get_outputs=lambda: [SimpleNamespace(name=k) for k in sorted(_yunet_out([]))],
                           get_inputs=lambda: [SimpleNamespace(name="input")])
    outs = _yunet_out([(16, i, (0.5, 0.5, 2.0, 2.0), 1.0, 0.95)])
    seen = {}

    def run(names, feed):
        seen["shape"] = feed["input"].shape
        return [outs[n] for n in names]
    fake.run = run
    monkeypatch.setattr(talking, "_session", lambda: fake)
    f = talking.faces(np.zeros((640, 360, 3), np.uint8))     # ảnh dọc: k = 1, đệm bên phải
    assert seen["shape"] == (1, 3, 640, 640) and len(f) == 1
    x0, y0, x1, y1, sc, m = f[0]
    assert (x0 + x1) / 2 == pytest.approx(20.5 * 16 / 360) and m == pytest.approx(11 * 16 / 640)


def _face(cx, cy, h=0.15, mouth=None):
    return (cx - h / 3, cy - h / 2, cx + h / 3, cy + h / 2, 0.9, mouth if mouth is not None else cy + h * 0.3)


def test_static_faces_and_windows():
    rng = np.random.default_rng(3)
    # người thật lắc lư quanh (0.5, 0.25), bức tranh đứng yên ở (0.15, 0.3)
    samples = [(t * 0.5, [_face(0.5 + rng.normal(0, 0.02), 0.25 + rng.normal(0, 0.015)), _face(0.15, 0.3, 0.12)])
               for t in range(20)]
    kept = talking.drop_static(samples)
    assert all(len(fs) == 1 and fs[0][0] > 0.3 for _t, fs in kept)
    # chỉ khi có lời gốc; quãng ngắn hơn MIN_LEN bỏ
    w = talking.windows(samples, [(2.0, 6.0)])
    assert len(w) == 1 and w[0]["a"] == pytest.approx(1.75) and w[0]["b"] == pytest.approx(6.25) and w[0]["ok"]
    assert 1.3 < w[0]["zoom"] <= talking.ZOOM_MAX
    assert talking.windows(samples, [(2.0, 2.6)]) == []
    assert talking.windows(samples, []) == []
    # miệng thấp (mặt giữa khung) -> không cận được, ok=False; mặt nhỏ (người ở xa) -> không tính
    low = [(t * 0.5, [_face(0.5, 0.5 + rng.normal(0, 0.01))]) for t in range(12)]
    w = talking.windows(low, [(0, 99)])
    assert w and not w[0]["ok"]
    tiny = [(t * 0.5, [_face(0.5 + rng.normal(0, 0.01), 0.2, h=0.05)]) for t in range(12)]
    assert talking.windows(tiny, [(0, 99)]) == []
    # khung dọc theo nội dung (bỏ dải đen): miệng tính theo vùng khung ra
    w = talking.windows(samples, [(0, 99)], box=(0.2, 0.5))
    assert w and w[0]["mouth"] == pytest.approx((0.295 - 0.2) / 0.5, abs=0.06)


def test_apply_to_plan_and_roundtrip():
    plan = edl.EditPlan([edl.Piece(0, 4), edl.Piece(4, 10, zoom=1.12), edl.Piece(10, 14, role="side", tag="recap")])
    new, n = talking.apply(plan, [{"a": 3.5, "b": 11.0, "zoom": 1.4, "ok": True}, {"a": 0, "b": 4, "zoom": 1.5, "ok": False}])
    assert n == 1 and new.pieces[1].zoom == 1.4 and new.pieces[1].ay == 1.0
    assert new.pieces[0] == plan.pieces[0] and new.pieces[2] == plan.pieces[2]          # khúc phụ / quãng không ok: giữ
    d = new.to_dict()
    assert "ay" not in d["pieces"][0] and d["pieces"][1]["ay"] == 1.0                    # bản lưu cũ không đổi
    assert edl.EditPlan.from_dict(d).pieces == new.pieces
    assert talking.apply(None, [])[1] == 0 and talking.apply(plan, [])[0] is plan


def test_anchor_crop_and_bands():
    c = filtergraph.look_chain("cook", 720, 1280, 720, 1280, zoom=1.5, anchor_y=1.0)
    assert "crop=in_w*0.6400:in_h*0.6400:(in_w-out_w)/2:(in_h-out_h)*1.000," in c
    assert "(in_h-out_h)" not in filtergraph.look_chain("cook", 720, 1280, 720, 1280, zoom=1.5)
    # dải chữ ở đáy (0.85-0.9) trên khúc cận 1.5 neo đáy: nằm trong khung ra, dời lên so với cận giữa
    (a, b), = edl.zoomed_bands([(0.85, 0.9)], {(1.5, 1.0)})
    assert a == pytest.approx(0.02 + 1.5 * (0.85 - (1 - 0.96 / 1.5)), abs=1e-6)
    assert edl.zoomed_bands([(0.4, 0.5)], {1.12}) == edl.zoomed_bands([(0.4, 0.5)], {(1.12, 0.5)})   # cách gọi cũ


def test_remake_talking_zoom(monkeypatch):
    from rmvideo.app.flow import remake
    monkeypatch.setattr(config, "TALK_ZOOM", "1")
    monkeypatch.setattr(talking, "thumbs_of", lambda a, fps=2.0: [(k * 0.5, f"{k}.jpg") for k in range(40)])
    seen = {}

    def fake_analyze(th, speech, box):
        seen.update(n=len(th), speech=speech, box=box)
        return [{"a": 4.0, "b": 12.0, "zoom": 1.45, "ok": True}]
    monkeypatch.setattr(talking, "analyze", fake_analyze)
    notes = []
    me = SimpleNamespace(inp="x.mp4", job=SimpleNamespace(note=notes.append))
    plan = edl.EditPlan([edl.Piece(0, 4), edl.Piece(4, 12), edl.Piece(12, 20)])
    tok = framing.FRAMING.set({"box": [0, 320, 720, 640], "pad": False, "src": [720, 1280]})
    try:
        new = asyncio.run(remake.Remake.talking_zoom(me, plan, [(0.0, 15.0, "lời gốc")], 20.0))
    finally:
        framing.FRAMING.reset(tok)
    assert new.pieces[1].ay == 1.0 and new.pieces[1].zoom == 1.45 and new.pieces[0].ay == 0.5
    assert seen["n"] == 20 and seen["speech"] == [(0.0, 15.0)] and seen["box"] == (0.25, 0.5)   # ≤ ~1 ảnh/giây
    assert "nói vào máy" in notes[0] and "40%" in notes[0]
    monkeypatch.setattr(config, "TALK_ZOOM", "0")
    assert asyncio.run(remake.Remake.talking_zoom(me, plan, [], 20.0)) is plan


def test_render_anchored_piece(tmp_path, monkeypatch):
    from tests import test_edl as te
    from rmvideo.media import renderer
    for k, d in config.EDITABLE.items():
        monkeypatch.setattr(config, k, d)
    monkeypatch.setattr(config, "QUALITY", "fast")
    monkeypatch.setattr(config, "BRAND_LOGO", "0")
    src, voice = str(tmp_path / "s.mp4"), str(tmp_path / "v.wav")
    te._ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:d=12:r=25", "-f", "lavfi", "-i", "sine=f=300:d=12", "-shortest", src)
    te._ffmpeg("-f", "lavfi", "-i", "sine=f=500:d=6", voice)
    plan = edl.EditPlan([edl.Piece(0, 4), edl.Piece(4, 8, zoom=1.5, ay=1.0), edl.Piece(8, 12),
                         edl.Piece(9, 10, zoom=1.12, ay=1.0, role="side", tag="montage")])
    assert not plan.validate()
    out = str(tmp_path / "o.mp4")
    got = asyncio.run(renderer.render(src, voice, out, "cook", 0, None, [(0.80, 0.86)], None, None, allow_hd=False,
                                      tmp=str(tmp_path), edit=plan.to_dict()))
    assert got == pytest.approx(plan.out_dur(), abs=0.05) and te._dur(out) == pytest.approx(got, abs=0.15)
