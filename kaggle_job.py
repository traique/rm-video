"""Chạy TRÊN Kaggle (bot gói file này cùng các module cần thiết vào notebook, xem kaggle_worker.py).

Không cần key API nào: lời đã viết xong ở Botkeep. Các loại việc (spec["kind"]):
- "remake": kịch bản ĐÃ CHỐT -> đọc giọng (VieNeu trên GPU, hoặc edge-tts / CapCut qua mạng) -> xếp giọng một tốc độ
  chung (core_video.plan_voice) -> phụ đề -> tách giọng gốc (Kim_Vocal_2) -> dựng (core_video.render).
- "music": 🎵 thay nhạc / nhạc nền (core_video.render_music) với thông số khớp beat Botkeep đã tính.
- "photo": 📸 ảnh + nhạc (slideshow.render) với seed + lịch sử công thức hiệu ứng của Botkeep.
- "batch": nhiều việc trong MỘT phiên (video dài cắt nhiều phần): nạp model + tải nguồn một lần.
Đầu vào (fetch): link nền tảng (Kaggle tự tải) -> không được thì link tạm của Botkeep (PUBLIC_URL); "cut" = cắt
một đoạn của nguồn khác. Kết quả trong /kaggle/working: output[_k].mp4 + result[_k].json."""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import traceback
import urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
_FETCHED: dict = {}                 # tải một lần dùng nhiều lần (các phần của cùng một video dài)


def prepare_env(spec: dict, app: str = APP):
    """Cài đặt (không có bí mật) + logo cho config/core_video, các file trạng thái để ngoài thư mục output."""
    with open(os.path.join(app, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(spec.get("settings") or {}, f, ensure_ascii=False)
    for k, v in (("JOBS_DB", "/tmp/rm_jobs.db"), ("RENDER_SPEED_FILE", "/tmp/rm_speed.json"),
                 ("RECENT_LOOKS", "/tmp/rm_looks.json"), ("SEP_BUDGET", "1500"), ("SEP_PASSES", "2"),
                 ("SEP_THREADS", str(os.cpu_count() or 4))):      # mặc định 2 luồng là cho Botkeep 1.5 vCPU
        os.environ.setdefault(k, v)
    if spec.get("logo_b64"):
        import base64
        os.makedirs(os.path.join(app, "assets"), exist_ok=True)
        with open(os.path.join(app, "assets", "logo.png"), "wb") as f:
            f.write(base64.b64decode(spec["logo_b64"]))


def _http_get(url: str, dst: str):
    req = urllib.request.Request(url, headers={"User-Agent": "rm-video-kaggle"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dst + ".part", "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(dst + ".part", dst)
    return dst


async def fetch(ref: dict, tmp: str, name: str) -> str:
    """ref: {"url", "kind": video|audio, "max_duration"} và/hoặc {"http", "name"}; hoặc {"cut": [a, b, copy], "of": ref}."""
    import core_video
    key = json.dumps(ref, sort_keys=True)
    if key in _FETCHED and os.path.exists(_FETCHED[key]):
        return _FETCHED[key]
    if "cut" in ref:
        src = await fetch(ref["of"], tmp, name + "_src")
        a, b, copy = ref["cut"]
        out = os.path.join(tmp, f"{name}.mp4")
        await core_video.cut(src, out, a, b, copy=copy)
        _FETCHED[key] = out
        return out
    errs = []
    if ref.get("http"):                  # bản Botkeep đã dùng để bóc lời / đo beat: đúng từng khung, ưu tiên
        ext = os.path.splitext(ref.get("name") or "")[1] or ".bin"
        try:
            path = await asyncio.to_thread(_http_get, ref["http"], os.path.join(tmp, f"{name}{ext}"))
            _FETCHED[key] = path
            return path
        except Exception as e:
            errs.append(f"link Botkeep: {str(e)[:200]}")
            logging.warning("Không tải được từ Botkeep (%s), thử link gốc", e)
    if ref.get("url"):
        d = os.path.join(tmp, f"dl_{name}")
        os.makedirs(d, exist_ok=True)
        try:
            if ref.get("kind") == "audio":
                path = await asyncio.to_thread(core_video._download_audio_sync, ref["url"], d)
            else:
                path = (await asyncio.to_thread(core_video._download_sync, ref["url"], d,
                                                int(ref.get("max_duration") or 600)))[0]
            _FETCHED[key] = path
            return path
        except Exception as e:
            errs.append(f"tải link: {str(e)[:200]}")
    raise RuntimeError("Không lấy được đầu vào: " + "; ".join(errs or ["không có link"]))


# ---------------- giọng ----------------

_VIENEU = {}


def _vieneu():
    if "tts" not in _VIENEU:
        from vieneu import Vieneu
        _VIENEU["tts"] = Vieneu()           # có CUDA thì chạy GPU, không thì ONNX trên CPU
    return _VIENEU["tts"]


def make_synth(spec: dict, ref_path: str | None):
    """async synth(items, tmp) -> {k: {"path", "words"}}. items: [(k, lời)]."""
    import core_video
    import vi_text
    voice = spec.get("voice") or {}
    engine = voice.get("engine") or "vieneu"
    if engine == "vieneu":
        tts = _vieneu()
        name = voice.get("name") or "Mỹ Duyên"

        def run(items, tmp):
            texts = [vi_text.normalize(t) for _, t in items]       # VieNeu không tự đọc số/ký hiệu
            if ref_path:
                audios = [tts.infer(t, ref_audio=ref_path) for t in texts]
            else:
                try:
                    audios = tts.infer_batch(texts, voice=name)
                except Exception:
                    audios = [tts.infer(t, voice=name) for t in texts]
            out = {}
            for (k, _), a in zip(items, audios):
                p = os.path.join(tmp, f"v_{k:03d}.wav")
                tts.save(a, p)
                out[k] = {"path": p, "words": None}
            return out

        async def synth(items, tmp):
            return await asyncio.to_thread(run, items, tmp)
        return synth

    voice_id = voice.get("id") or ""
    import config
    config.TTS_PROVIDER = engine          # synth_clip_ex chọn CapCut / edge theo cài đặt này (lô có thể trộn hai loại)

    async def synth_net(items, tmp):                 # edge-tts / CapCut: dịch vụ mạng, đọc ngay trên Kaggle
        sem = asyncio.Semaphore(3)

        async def one(k, t):
            p = os.path.join(tmp, f"v_{k:03d}.mp3")
            async with sem:
                words, _ = await core_video.synth_clip_ex(t, voice_id, p)
            return k, {"path": p, "words": words}
        return dict(await asyncio.gather(*[one(k, t) for k, t in items]))
    return synth_net


# ---------------- các loại việc ----------------

async def produce(spec: dict, inp: str, tmp: str, synth) -> dict:
    """Remake: phần nặng giống hệt đường chạy trên Botkeep (pipeline._process) từ sau khi lời đã chốt, trừ bước AI rút
    gọn (không có key ở đây; Botkeep đã rút gọn trước theo tốc độ của giọng): đoạn còn dài thì nói nhanh hơn trong giới
    hạn của plan_voice."""
    import config
    import core_video
    import subs as subs_mod
    import vocal_sep
    t = {}
    segs = spec["segments"]
    out_dur = float(spec["out_dur"])
    items = [(k, s["text"]) for k, s in enumerate(segs) if s.get("ost") != "orig" and (s.get("text") or "").strip()]
    sep_task = None
    reused = mounted_ambient(spec.get("amb_token", "")) if spec.get("mode") == "cook" else None
    if reused:
        t["sep"] = 0.0
        logging.info("Dùng lại tiếng nền đã tách ở phiên phân tích: %s", reused)

        async def _reuse():
            return reused
        sep_task = asyncio.create_task(_reuse())
    elif spec.get("mode") == "cook" and (spec.get("sep") or {}).get("ok"):
        async def _sep():
            t0 = time.time()
            p = os.path.join(tmp, "ambient.wav")
            # CẢ BÀI, không chỉ các quãng bóc lời thấy người gốc nói: bóc lời sót một quãng (Whisper bịa câu "Đừng
            # quên đăng ký kênh" rồi bỏ qua ~27s lời thật, lỗi thật 12/11/2026) thì quãng đó vẫn còn nguyên giọng gốc.
            # Botkeep chậm mới phải tiết kiệm; Kaggle dư CPU (cả bài 173s ~2 phút, chạy song song với đọc giọng).
            os.environ.setdefault("SEP_DEVICE", "cuda")   # có GPU thì tách bằng GPU; không có CUDA thì tự về CPU
            ok = await vocal_sep.remove_voice(inp, p, windows=None)
            t["sep"] = round(time.time() - t0, 1)
            return p if ok else None
        sep_task = asyncio.create_task(_sep())
    try:
        t0 = time.time()
        voiced = await synth(items, tmp)
        t["tts"] = round(time.time() - t0, 1)
        clips = [voiced.get(k) or {"path": None, "words": []} for k in range(len(segs))]
        durs = [await core_video.speech_duration(c["path"], c.get("words")) if c["path"] else 0.0 for c in clips]
        plan, too_long = core_video.plan_voice(durs, segs, out_dur)
        voice_track = os.path.join(tmp, "voice.wav")
        await core_video.compose_voice(clips, plan, voice_track, out_dur)
        srt, ass = "", None
        if spec.get("subs"):
            words = await core_video.words_for_track(clips, plan, voice_track,
                                                     core_video.subtitle_texts(segs, spec.get("teaser")))
            if words:
                srt = subs_mod.to_srt(words)
                ass = os.path.join(tmp, "subs.ass")
                subs_mod.write_ass(words, ass, spec.get("pos_y"), config.SUB_STYLE)
        amb = await sep_task if sep_task else None
    finally:
        if sep_task and not sep_task.done():          # lỗi giữa chừng: không để tiến trình tách giọng chạy mồ côi
            sep_task.cancel()
            await asyncio.gather(sep_task, return_exceptions=True)
    gate = None
    if spec.get("mode") == "cook" and not amb:
        gate = [tuple(x) for x in spec.get("speech") or []] or [(0.0, 99999.0)]
    t0 = time.time()
    out = os.path.join(tmp, "output.mp4")
    notes: list = []
    await core_video.render(inp, voice_track, out, spec["mode"], float(spec.get("render_dur") or 0), ass,
                            spec.get("bands") or None, amb, gate, allow_hd=bool(spec.get("allow_hd", True)),
                            title=spec.get("title") or "", tmp=tmp, stretch=float(spec.get("stretch") or 1.0),
                            ost=core_video.ost_windows(segs, out_dur), notes=notes, teaser=spec.get("teaser"))
    t["render"] = round(time.time() - t0, 1)
    return {"ok": True, "video": out, "plan": plan, "too_long": too_long, "srt": srt, "amb": bool(amb),
            "notes": notes, "timings": t, "fast": sum(1 for p in plan if p["factor"] > 1.12)}


async def produce_bed(spec: dict, inp: str, tmp: str) -> dict:
    """Video KHÔNG LỜI: nhạc nền (+ tiếng gốc theo BGM_ORIG) thay cho giọng đọc, dựng như remake. Không tải được nhạc
    thì báo lỗi (Botkeep đã có bài nhạc, tự dựng lại) chứ không ra video thiếu nhạc."""
    import core_video
    t = {}
    music = None
    if spec.get("music"):
        t0 = time.time()
        music = await fetch(spec["music"], tmp, "music")
        t["music"] = round(time.time() - t0, 1)
    out_dur = float(spec["out_dur"])
    stretch = float(spec.get("stretch") or 1.0)
    bed = os.path.join(tmp, "bed.wav")
    await core_video.music_bed(inp, music, bed, out_dur, float(spec.get("trim") or 0.0), spec.get("orig") or "off",
                               stretch)
    t0 = time.time()
    out = os.path.join(tmp, "output.mp4")
    notes: list = []
    await core_video.render(inp, bed, out, spec["mode"], float(spec.get("render_dur") or 0), None,
                            spec.get("bands") or None, bed, None, allow_hd=bool(spec.get("allow_hd", True)),
                            title=spec.get("title") or "", tmp=tmp, stretch=stretch, notes=notes, music_bed=True)
    t["render"] = round(time.time() - t0, 1)
    return {"ok": True, "video": out, "music": bool(music), "notes": notes, "timings": t}


async def analyze(spec: dict, tmp: str) -> dict:
    """Bóc lời + phân tích hình (thay cho Botkeep, máy 1.5 lõi mất ~400s cho video 220s): tách giọng gốc khỏi nhạc
    (GPU) -> Whisper large-v3 nghe GIỌNG SẠCH (asr_local); song song trên CPU: điểm chuyển cảnh, dải chữ in cứng, ảnh
    thu nhỏ 2 khung/giây (scenes, mức Kỹ). Kết quả thô; Botkeep lọc câu bịa/lặp + chia đoạn (transcribe.py)."""
    import zipfile

    import asr_local
    import core_video
    import scenes
    import vocal_sep
    os.makedirs(tmp, exist_ok=True)
    t0 = time.time()
    inp = await fetch(spec["src"], tmp, "src")
    t = {"download": round(time.time() - t0, 1)}
    dur = await core_video.probe_duration(inp)

    async def _scenes():
        t1 = time.time()
        r = await asyncio.to_thread(scenes._analyze_sync, inp, tmp, dur, bool(spec.get("want_gray", True)), 0.3,
                                    "full")
        t["scenes"] = round(time.time() - t1, 1)
        return r
    scene_task = asyncio.create_task(_scenes())
    try:
        t1 = time.time()
        vocals, amb = os.path.join(tmp, "vocals16k.wav"), os.path.join(tmp, "ambient.wav")
        os.environ.setdefault("SEP_DEVICE", "cuda")
        sep_ok = await vocal_sep.remove_voice(inp, amb, windows=None, vocals_wav=vocals) and os.path.exists(vocals)
        t["sep"] = round(time.time() - t1, 1)
        if not sep_ok:                                  # không tách được: Whisper nghe thẳng âm thanh gốc
            vocals = os.path.join(tmp, "audio16k.wav")
            await core_video._run_ffmpeg("-y", "-i", inp, "-vn", "-ac", "1", "-ar", "16000", vocals)
        t1 = time.time()
        d = await asyncio.to_thread(asr_local.transcribe, vocals, spec.get("language") or "", spec.get("prompt") or "")
        t["asr"] = round(time.time() - t1, 1)
        cuts, bands = await scene_task
    finally:
        if not scene_task.done():
            scene_task.cancel()
            await asyncio.gather(scene_task, return_exceptions=True)
    files = {}
    a = scenes._ANALYZED.get(inp)
    if a and a.get("n"):
        zp = os.path.join(tmp, "thumbs.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:     # JPEG đã nén: chỉ gói lại
            for f in sorted(os.listdir(a["dir"])):
                z.write(os.path.join(a["dir"], f), f)
        files["thumbs.zip"] = zp
    if sep_ok and spec.get("keep_ambient"):
        fl = os.path.join(tmp, "ambient.flac")          # phiên dựng dùng lại, khỏi tách giọng lần nữa
        await core_video._run_ffmpeg("-y", "-i", amb, "-c:a", "flac", "-compression_level", "5", fl)
        files["ambient.flac"] = fl
    return {"ok": True, "kind": "analyze", "token": spec.get("token", ""), "whisper": d, "cuts": cuts,
            "bands": [list(b) for b in bands], "dur": dur, "sep": sep_ok, "thumb_fps": scenes.THUMB_FPS,
            "timings": t, "files": files}


def mounted_ambient(token: str, root: str = "/kaggle/input") -> str | None:
    """Tiếng nền đã tách ở phiên phân tích của CHÍNH job này (notebook phân tích gắn vào làm dữ liệu vào, xem
    kaggle_worker._run_sync): đúng mã job thì dùng lại, khỏi tách giọng lần nữa."""
    if not token or not os.path.isdir(root):
        return None
    for r, _, files in os.walk(root):
        if "result.json" in files and "ambient.flac" in files:
            try:
                if json.load(open(os.path.join(r, "result.json"), encoding="utf-8")).get("token") == token:
                    return os.path.join(r, "ambient.flac")
            except Exception:
                continue
    return None


async def run_one(spec: dict, tmp: str) -> dict:
    import core_video
    os.makedirs(tmp, exist_ok=True)
    t0 = time.time()
    kind = spec.get("kind") or "remake"
    if kind == "analyze":
        return await analyze(spec, tmp)
    if kind == "photo":
        import slideshow
        images = [await fetch(r, tmp, f"img{k}") for k, r in enumerate(spec["images"])]
        music = await fetch(spec["music"], tmp, "music")
        dl = round(time.time() - t0, 1)
        out = os.path.join(tmp, "output.mp4")
        t1 = time.time()
        plan = await slideshow.render(images, music, out, spec.get("duration") or 0, seed=spec.get("seed"),
                                      recent=spec.get("recent") or [])
        return {"ok": True, "video": out, "plan": plan,
                "timings": {"download": dl, "render": round(time.time() - t1, 1)}}
    inp = await fetch(spec["src"], tmp, "src")
    dl = round(time.time() - t0, 1)
    if kind == "bed":
        res = await produce_bed(spec, inp, tmp)
        res["timings"] = {"download": dl, **res["timings"]}
        return res
    if kind == "music":
        music = await fetch(spec["music"], tmp, "music")
        out = os.path.join(tmp, "output.mp4")
        t1 = time.time()
        await core_video.render_music(inp, music, out, int(spec.get("duration") or 0), **(spec.get("music_args") or {}))
        return {"ok": True, "video": out, "timings": {"download": dl, "render": round(time.time() - t1, 1)}}
    ref = None
    if spec.get("ref_b64"):
        import base64
        ref = os.path.join(tmp, "ref" + (spec.get("ref_ext") or ".mp3"))
        with open(ref, "wb") as f:
            f.write(base64.b64decode(spec["ref_b64"]))
    t1 = time.time()
    synth = make_synth(spec, ref)
    load = round(time.time() - t1, 1)
    res = await produce(spec, inp, tmp, synth)
    res["timings"] = {"download": dl, "load_tts": load, **res["timings"]}
    return res


def _jsonable(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
    except ImportError:
        pass
    return str(o)


def main(spec: dict, out_dir: str = "/kaggle/working"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    prepare_env(spec)
    sys.path.insert(0, APP)
    jobs = spec["jobs"] if spec.get("kind") == "batch" else [spec]
    results = []
    for k, sp in enumerate(jobs):
        sp = {**{x: spec[x] for x in ("settings", "ref_b64", "ref_ext") if x in spec}, **sp}
        tag = "" if spec.get("kind") != "batch" else f"_{k}"
        try:
            res = asyncio.run(run_one(sp, f"/tmp/rm_job{tag or '_0'}"))
            res.setdefault("token", sp.get("token", ""))     # Botkeep đối chiếu: kết quả đúng của việc này
            if res.get("video"):
                shutil.move(res.pop("video"), os.path.join(out_dir, f"output{tag}.mp4"))   # /tmp và /kaggle/working khác ổ
            for name, path in (res.pop("files", None) or {}).items():
                base, ext = os.path.splitext(name)
                shutil.move(path, os.path.join(out_dir, f"{base}{tag}{ext}"))
        except Exception:
            res = {"ok": False, "error": traceback.format_exc()[-2500:]}
            logging.error("Việc %s lỗi:\n%s", k, res["error"])
        with open(os.path.join(out_dir, f"result{tag}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, default=_jsonable)
        results.append(res)
    return results
