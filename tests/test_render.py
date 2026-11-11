"""Smoke test: video + voice giả -> render 2 chế độ, 2 kiểu thời lượng, bóc audio,
fallback transcribe, hàng đợi. Chạy: python tests/test_render.py (không cần token)."""
import asyncio
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_video  # noqa: E402  (tự nạp static-ffmpeg vào PATH)
import config  # noqa: E402
from queue_manager import Job, JobQueue, QueueFull  # noqa: E402
from transcribe import transcribe  # noqa: E402
import subs as subs_mod  # noqa: E402
import beat  # noqa: E402
import prompt_engine  # noqa: E402


def make_fake_inputs(tmp):
    inp = os.path.join(tmp, "input.mp4")
    voice = os.path.join(tmp, "voice.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", inp],
        check=True, capture_output=True)
    # 24kHz mono mp3: giống hệt định dạng edge-tts trả về
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
         "-ar", "24000", "-ac", "1", voice],
        check=True, capture_output=True)
    return inp, voice


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        inp, voice = make_fake_inputs(tmp)

        # render: 2 chế độ x (giữ nguyên 6s | cắt 4s)
        for mode in ("cook", "review"):
            for dur, expect in ((0, 6.0), (4, 4.0)):
                out = os.path.join(tmp, f"out_{mode}_{dur}.mp4")
                await core_video.render(inp, voice, out, mode, dur)
                got = await core_video.probe_duration(out)
                assert abs(got - expect) < 0.3, f"{mode}/{dur}: dài {got}s, muốn {expect}s"
                print(f"OK render {mode} dur={dur or 'gốc'}: {got:.1f}s, {os.path.getsize(out)} bytes")

        audio = os.path.join(tmp, "audio.mp3")
        await core_video.extract_audio(inp, audio)
        assert os.path.getsize(audio) > 1_000, "audio bóc ra rỗng"
        print(f"OK extract_audio: {os.path.getsize(audio)} bytes")

        if not config.GROQ_API_KEY:
            assert await transcribe(audio) == ""
            print("OK transcribe fallback (không có GROQ_API_KEY)")

        # Phụ đề: sinh ASS từ word timing giả rồi burn vào video
        words = [(0.1, 0.5, "Món"), (0.5, 0.9, "này"), (0.9, 1.4, "ngon"),
                 (1.4, 1.9, "lắm"), (1.9, 2.5, "luôn"), (2.5, 3.0, "nha")]
        ass = os.path.join(tmp, "subs.ass")
        n = subs_mod.write_ass(words, ass)
        assert n >= 2 and "Dialogue:" in open(ass).read()
        out = os.path.join(tmp, "out_subs.mp4")
        await core_video.render(inp, voice, out, "review", 4, ass)
        assert os.path.getsize(out) > 10_000
        print(f"OK render phụ đề burn-in: {n} dòng, {os.path.getsize(out)} bytes")

        # Video câm tiếng vẫn chạy được chế độ cook (tự bỏ lớp ambient)
        silent = os.path.join(tmp, "silent.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc=duration=4:size=640x360:rate=24",
                        "-c:v", "libx264", "-preset", "ultrafast", "-an", silent],
                       check=True, capture_output=True)
        out2 = os.path.join(tmp, "out_silent.mp4")
        await core_video.render(silent, voice, out2, "cook", 0)
        print(f"OK video câm -> cook fallback: {os.path.getsize(out2)} bytes")

        # edge-tts thật (cần mạng): giọng + mốc thời gian từng từ
        try:
            mp3 = os.path.join(tmp, "real_voice.mp3")
            ass2 = os.path.join(tmp, "real.ass")
            got = await asyncio.wait_for(
                core_video.tts("Xin chào, đây là bài kiểm tra giọng đọc.", mp3, config.VOICE_FEMALE, ass2), 30)
            assert got and os.path.getsize(mp3) > 5_000
            print(f"OK edge-tts thật + phụ đề: {os.path.getsize(mp3)} bytes, {os.path.getsize(ass2)} bytes ass")
        except Exception as e:
            print(f"SKIP edge-tts (mạng chặn?): {e}")

    # Dò beat trên click track chuẩn + render thay nhạc
    with tempfile.TemporaryDirectory() as tmp:
        inp, _ = make_fake_inputs(tmp)
        music = os.path.join(tmp, "music.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "aevalsrc=sin(2*PI*1000*t)*lt(mod(t\\,0.5)\\,0.06):d=15",
                        "-ar", "44100", music], check=True, capture_output=True)
        bpm, fb = await beat.analyze(music)
        assert abs(bpm - 120) < 1.5, f"dò {bpm} BPM, muốn 120"
        print(f"OK dò beat: {bpm:.1f} BPM, beat đầu {fb:.2f}s")
        sync = beat.compute_sync(110.0, 0.3, bpm, fb)
        assert 0.8 <= sync["speed"] <= 1.25
        out = os.path.join(tmp, "out_music.mp4")
        dur = await core_video.render_music(inp, music, out, 0, **sync)
        got = await core_video.probe_duration(out)
        assert abs(got - dur) < 0.3 and os.path.getsize(out) > 10_000
        print(f"OK render_music: speed x{sync['speed']:.2f}, {got:.1f}s")

    # Dò chữ in cứng: video có dòng chữ trắng viền đen cố định -> phải dò ra đúng dải, render che được
    import textzone
    with tempfile.TemporaryDirectory() as tmp:
        vid = os.path.join(tmp, "hard.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x6a8f5c:duration=6:size=360x640:rate=24",
                        "-f", "lavfi", "-i", "sine=d=6",
                        "-vf", "drawtext=fontfile=/home/claude/project/assets/DejaVuSans-Bold.ttf:text='PHU DE GOC':fontsize=28:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.15",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", vid], check=True, capture_output=True)
        bands = await textzone.detect_text_bands(vid)
        assert bands and abs((bands[0][0] + bands[0][1]) / 2 - 0.17) < 0.04, f"dò sai: {bands}"
        clean = os.path.join(tmp, "clean.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x6a8f5c:duration=6:size=360x640:rate=24",
                        "-c:v", "libx264", "-preset", "ultrafast", clean], check=True, capture_output=True)
        assert await textzone.detect_text_bands(clean) == [], "báo nhầm trên video không chữ"
        y, h = core_video.band_to_output(*bands[0])
        subs_mod.write_ass([(0.2, 1.0, "mới"), (1.0, 2.0, "đè lên")], os.path.join(tmp, "p.ass"), pos_y=y + h // 2)
        _, voice2 = make_fake_inputs(tmp)
        out = os.path.join(tmp, "covered.mp4")
        await core_video.render(vid, voice2, out, "review", 3, os.path.join(tmp, "p.ass"), bands)
        assert os.path.getsize(out) > 10_000
        print(f"OK dò chữ in cứng {bands[0][0]:.2f}-{bands[0][1]:.2f}, không báo nhầm, render che OK")

    # Kiểm nguyên liệu: bắt đổi trứng gà -> trứng vịt và bỏ nguyên liệu; chấp nhận từ miền Nam
    import ingredients
    o = "đập 2 quả trứng gà, xào cà rốt, mộc nhĩ, thêm mùi và dứa"
    miss, added = ingredients.check(o, "đập 2 trứng vịt, xào rau củ, thêm ngò và khóm")
    assert added == ["trứng vịt"] and set(miss) == {"trứng gà", "cà rốt", "mộc nhĩ"}, (miss, added)
    assert ingredients.check(o, "đập 2 hột gà, xào cà rốt, nấm mèo, thêm ngò và thơm") == ([], [])
    print("OK kiểm nguyên liệu")

    # Cắt phần video dài: cắt vào khoảng im lặng, không phần nào quá ngắn
    import scenes
    b = scenes.split_points(245, 60, [i * 7.3 for i in range(1, 34)], [(58.1, 58.9), (119.0, 119.6), (176.3, 177.4)])
    assert all(any(a - 0.1 <= x <= c + 0.1 for a, c in [(58.1, 58.9), (119.0, 119.6), (176.3, 177.4)]) for x in b[1:-1]), b
    assert min(y - x for x, y in zip(b, b[1:])) >= 20 and scenes.split_points(70, 60, [], []) == [0.0, 70]
    print(f"OK cắt phần: {b}")

    # parse_pack: JSON bọc trong fence vẫn bóc được, giữ fallback khi hỏng
    p = prompt_engine.parse_pack('```json\n{"script":"A 500 gam","title":"T","caption":"C","hashtags":"#x"}\n```', "fb")
    assert p["script"] == "A 500 gam" and p["hashtags"] == "#x"
    p2 = prompt_engine.parse_pack("văn bản trần không json", "fb")
    assert p2["script"].startswith("văn bản trần")
    print("OK parse_pack")

    q = JobQueue(db_path=os.path.join(tempfile.mkdtemp(), "q.db"))
    for i in range(config.MAX_QUEUE):
        assert q.put(Job(url="u", mode="cook", duration=0, voice="v")) == i + 1
    try:
        q.put(Job(url="u", mode="cook", duration=0, voice="v"))
        raise AssertionError("phải báo hàng đợi đầy")
    except QueueFull:
        print("OK queue đầy bị chặn")
    q.done()
    assert q.pending == config.MAX_QUEUE - 1
    print("OK queue position")


def test_web_js():
    """Kiểm cú pháp JS trên HTML THẬT máy chủ trả ra (sau khi Python xử lý chuỗi),
    vì lỗi dấu \\ trong chuỗi Python chỉ lộ ra ở đây, không lộ khi đọc file .py."""
    import shutil
    import subprocess
    import tempfile
    import web_panel
    with open(web_panel.INDEX_HTML, encoding="utf-8") as f:
        html = f.read()
    js = html[html.index("<script>") + 8:html.index("</script>")]
    if not shutil.which("node"):
        print("SKIP kiểm JS web (không có node)")
        return
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
    assert r.returncode == 0, "JS trang web lỗi cú pháp:\n" + r.stderr[:600]
    print("OK JS trang web đúng cú pháp")


def test_ingredient_false_positives():
    """Các báo nhầm thật từ log Botkeep (video gà rang muối)."""
    import ingredients as I
    import prompt_engine as P
    o = ("làm món gà răng muối thơm ngon Gà mua về Đem chừng qua với xả và gừng cho la lốt vào chiên "
         "Giá đang rất tốt cho vào 1 cái tô lớn Khoảng chừng 5 giây")
    n = "gà rang muối ngon. Trần sơ qua cùng sả và gừng. Thả lá lốt vô. Cho vào thau lớn, 5 giây"
    assert I.check(o, n) == ([], []), I.check(o, n)                  # thơm ngon≠dứa, giá tiền≠giá đỗ, xả=sả
    assert I.check_numbers(o, n) == ([], []), I.check_numbers(o, n)    # "1 cái tô" là mạo từ
    assert I.check_numbers(o, "vài giây")[0] == ["5 giây"]             # nhưng "vài giây" thay "5 giây" vẫn bị bắt
    assert I.check("trứng gà", "trứng vịt") == (["trứng gà"], ["trứng vịt"])
    assert I.check_numbers("2 muỗng", "hai muỗng") == ([], [])
    assert P._CTA_RE.search("Mua ở giỏ hàng góc trái màn hình nha") and not P._CTA_RE.search("Rắc 2 muỗng bột")
    S = P.same_sentence
    assert S("Xé tơi sợi sả rối tung vàng ươm như rơm khô.", "Xé tơi sợi sả rối tung, vàng ươm như rơm khô nha.") >= 0.85
    assert S("Hợp làm món gà.", "Hợp làm món cá tôm.") < 0.85
    assert S("Giờ mình chiên giòn nè, đoạn 27 nghen.", "Giờ mình cắt gà nè, đoạn 0 nghen.") < 0.85
    print("OK kiểm nguyên liệu không báo nhầm + chặn lặp câu giữa lô")


def test_promo_fps_json():
    import core_video
    import prompt_engine as P
    # đoạn quảng cáo cuối video gà rang muối (lời gốc thật từ log Botkeep)
    segs = [(0.0, 4.5, "Có nhiều bạn yêu cầu mình làm món gà răng muối"), (5.9, 8.2, "Gà mua về anh chị rửa sạch"),
            (73.3, 75.2, "xong Bước tiếp theo vô cùng là đơn giản Mua"), (75.2, 77.4, "cho em cái gói bột răng muối này nè"),
            (77.4, 80.3, "Cho vào khoảng 2 muỗng Lắc cho bột bám đều lên miếng gà là hoàn thành"),
            (80.3, 82.1, "Đây là bột răng muối độc quyền do chính tay em làm"), (82.1, 84.5, "Chuyên dùng để làm các món như gà,"),
            (84.5, 86.1, "cá, tôm,"), (86.1, 89.8, "ếch, ba ba Rất thơm ngon và tiện lợi Có mua bột răng muối và bột"),
            (89.8, 92.8, "chiên tổng hợp Em để trong chào hàng ở góc trái màn hình Giá đang rất tốt"), (92.8, 94.7, "ủng hộ em nhẹ")]
    assert 80 <= P.find_promo_tail(segs, 94.7) <= 80.3
    assert P.find_promo_tail([(i * 5.0, i * 5 + 4, t) for i, t in enumerate(
        ["Gà mua về rửa sạch", "chặt miếng", "ướp", "chiên vàng", "mua thêm ít hành", "bày ra đĩa"])], 30) is None
    # fps: chia đều, không ép 50->30
    assert core_video.out_fps(50) == 25 and core_video.out_fps(60) == 30 and core_video.out_fps(25) == 25
    # JSON hỏng như log thật vẫn cứu được
    d = P.loads_lenient('{"segments": [{"i": 0, "text": "Rửa gà."} {"i": 1, "text": "Chặt "nhỏ" ra."},]}')
    assert [s["text"] for s in d["segments"]] == ["Rửa gà.", "Chặt nhỏ ra."]
    # nguồn nhỏ: khử nhiễu + màu TRƯỚC khi phóng to; nguồn lớn: thu nhỏ trước
    up = core_video.build_filter("cook", 10, False, None, W=720, H=1280, src_w=360, src_h=640)
    down = core_video.build_filter("cook", 10, False, None, W=720, H=1280, src_w=1080, src_h=1920)
    assert up.index("vibrance") < up.index("scale=") and down.index("scale=") < down.index("vibrance")
    # cổng tiếng nền: gộp các lần nói cách nhau <0.8s (không chập chờn), không giữ lớp nền khi người gốc nói
    assert core_video._gate_windows([(1.0, 2.0), (2.5, 3.0), (6.0, 7.0)]) == [[0.8, 3.2], [5.8, 7.2]]
    assert core_video.GATE_FLOOR == 0.0, "lớp nền khi người gốc nói làm lọt lời gốc (máy nghe chép được nguyên câu)"
    print("OK cắt quảng cáo cuối, fps chia đều, JSON hỏng, thứ tự lọc, cổng tiếng nền")


def test_color_recipe():
    """Công thức màu người dùng đã duyệt (vibrance chừa vùng đã đậm). Bản eq saturation=1.2 làm món chiên
    cam loang, bị chê -> cấm quay lại. Mức nào cũng phải giữ vibrance + saturation 1.06 cho nấu ăn."""
    import config
    import core_video
    for q in ("fast", "balanced", "high"):
        config.QUALITY = q
        f = core_video.build_filter("cook", 10, False, None)
        assert "vibrance=intensity=0.2" in f and "saturation=1.06" in f and "lutrgb=g=" in f, q   # ấm màu (6000K)
        assert "saturation=1.2" not in f and "lutyuv" not in f, q
        assert f.index("fps=30") < f.index("scale="), "fps=30 phải đứng trước scale (nguồn 50-60fps)"
    config.QUALITY = "balanced"
    print("OK công thức màu giữ nguyên bản đã duyệt")


async def _loudness():
    """Đầu ra phải -14 LUFS ±1 và đỉnh ≤ -1 dBFS NGAY TỪ RENDER (lỗi cũ: alimiter tự khuếch đại lên 0 dBFS)."""
    import re
    import subprocess
    import tempfile
    import core_video
    td = tempfile.mkdtemp()
    src, voice, out = f"{td}/s.mp4", f"{td}/v.mp3", f"{td}/o.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:d=8", "-f", "lavfi",
                    "-i", "anoisesrc=d=8:a=0.05", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=6,volume=0.3", "-ar", "24000", voice],
                   check=True)
    await core_video.render(src, voice, out, "cook", 8, None, None, speech_gate=[(1.0, 2.0)])
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?[0-9.]+) LUFS", err)[-1])
    peak = float(re.findall(r"Peak:\s+(-?[0-9.]+) dBFS", err)[-1])
    assert abs(lufs + 14) <= 1.0, f"âm lượng {lufs} LUFS, phải -14±1"
    assert peak <= -1.0, f"đỉnh {peak} dBFS, phải ≤ -1 (méo tiếng)"
    print(f"OK âm lượng {lufs} LUFS, đỉnh {peak} dBFS")


async def _loudness_sparse():
    """Giọng THƯA (2 câu, còn lại chỉ tiếng nền): chuẩn hoá hai lượt giữ tiếng nền đều nhau giữa các câu (loudnorm
    một lượt kéo nền lên lúc không có giọng: đo được nền chênh 6 dB giữa hai khoảng lặng) và đúng -14 LUFS ±0.3."""
    import re
    import subprocess
    import tempfile
    import core_video
    td = tempfile.mkdtemp()
    src, voice, out = f"{td}/s.mp4", f"{td}/v.wav", f"{td}/o.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=16", "-f", "lavfi",
                    "-i", "anoisesrc=color=pink:d=16:a=0.05", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", src],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=f=200:d=16,volume='0.4*(between(t,1,4)+between(t,9,12))':eval=frame", "-ar", "24000", voice],
                   check=True)
    await core_video.render(src, voice, out, "cook", 16, None, None)

    def lufs(a, d):
        err = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(a), "-t", str(d), "-i", out, "-af",
                              "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
        return float(re.findall(r"I:\s+(-?[0-9.]+) LUFS", err)[-1])
    whole, gap1, gap2 = lufs(0, 16), lufs(5, 3.5), lufs(12.5, 3)
    assert abs(whole + 14) <= 0.3, f"âm lượng {whole} LUFS, phải -14±0.3"
    assert abs(gap1 - gap2) <= 2.0, f"tiếng nền giữa các câu {gap1} vs {gap2}: bị kéo lên xuống"


def test_two_pass_passthrough(monkeypatch):
    import config
    import core_video
    fc = "[0:v]null[v];[1:a]" + core_video.LOUDNORM + ",aresample=48000[a]"
    monkeypatch.setattr(config, "LOUDNESS_2PASS", "0")
    assert asyncio.run(core_video.two_pass_loudness(fc, [], 5)) == fc
    monkeypatch.setattr(config, "LOUDNESS_2PASS", "1")

    async def silent(*a):
        return None
    monkeypatch.setattr(core_video, "measure_lufs", silent)          # đo không được (im lặng/lỗi): giữ một lượt
    assert asyncio.run(core_video.two_pass_loudness(fc, [], 5)) == fc

    async def loud(*a):
        return -20.5
    monkeypatch.setattr(core_video, "measure_lufs", loud)
    got = asyncio.run(core_video.two_pass_loudness(fc, [], 5))
    assert "loudnorm" not in got and "volume=6.50dB" in got and "alimiter" in got


def test_loudness_sparse():
    asyncio.run(_loudness_sparse())


def test_smoke_render():
    asyncio.run(run())


def test_loudness():
    asyncio.run(_loudness())


if __name__ == "__main__":
    asyncio.run(run())
    asyncio.run(_loudness())
    test_color_recipe()
    test_promo_fps_json()
    test_ingredient_false_positives()
    test_web_js()
    print("ALL TESTS PASSED")


def test_output_size_adapts_to_machine_speed(monkeypatch, tmp_path):
    """Botkeep 1.5 lõi chậm: video 88s 1080p dựng ~20 phút. balanced: ước tính theo tốc độ đo được, quá
    RENDER_BUDGET thì ra 720p (kèm lời nhắn); fast luôn 720p; high luôn 1080p; nguồn nhỏ luôn 720p."""
    import config
    import core_video
    monkeypatch.setattr(core_video, "SPEED_FILE", str(tmp_path / "rs.json"))
    monkeypatch.setattr(core_video, "RENDER_BUDGET", 600)
    monkeypatch.setattr(config, "OUTPUT_HD", True)
    monkeypatch.setattr(config, "QUALITY", "balanced")
    assert core_video.output_size(1920, True, 88) == (1080, 1920)            # chưa đo: giữ 1080p như trước
    assert core_video.record_render_speed(40.0, 10, 1920, source="bench") == 4.0
    # lần dựng thật đầu tiên thay hẳn số bài đo; 720p quy đổi x2 ra 1080p
    assert core_video.record_render_speed(600.0, 88, 1280) == pytest.approx(13.64, abs=0.01)
    assert core_video.record_render_speed(1200.0, 88, 1920) == pytest.approx(13.64, abs=0.01)     # trung bình
    notes = []
    assert core_video.output_size(1920, True, 88, notes) == (720, 1280)
    assert notes and "720p" in notes[0] and "~20 phút" in notes[0]
    assert core_video.output_size(1920, True, 30) == (1080, 1920)            # 30s x 13.6 = 409s < 600s
    monkeypatch.setattr(config, "QUALITY", "high")
    assert core_video.output_size(1920, True, 88) == (1080, 1920)
    monkeypatch.setattr(config, "QUALITY", "fast")
    assert core_video.output_size(1920, True, 10) == (720, 1280)
    monkeypatch.setattr(config, "QUALITY", "balanced")
    monkeypatch.setattr(core_video, "RENDER_BUDGET", 0)
    assert core_video.output_size(1920, True, 88) == (1080, 1920)
    assert core_video.output_size(1280, True, 10) == (720, 1280)
    assert core_video.output_size(1920, False, 10) == (720, 1280)


def test_render_benchmark_records_speed(monkeypatch, tmp_path):
    import core_video
    monkeypatch.setattr(core_video, "SPEED_FILE", str(tmp_path / "rs.json"))
    sp = core_video.render_benchmark_sync(seconds=1)
    assert sp and sp > 0 and core_video.render_speed() == pytest.approx(sp, abs=0.001)
