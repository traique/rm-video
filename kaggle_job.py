"""Chạy TRÊN Kaggle (bot gói file này cùng các module cần thiết vào notebook, xem kaggle_worker.py).

Không cần key API nào: lời đã viết xong ở Botkeep. Các loại việc (spec["kind"]):
- "remake": kịch bản ĐÃ CHỐT -> đọc giọng (VieNeu trên GPU, hoặc edge-tts / CapCut qua mạng) -> xếp giọng một tốc độ
  chung (core_video.plan_voice) -> phụ đề -> tách giọng gốc (Kim_Vocal_2) -> dựng (core_video.render).
- "music": 🎵 thay nhạc / nhạc nền (core_video.render_music) với thông số khớp beat Botkeep đã tính.
- "photo": 📸 ảnh + nhạc (slideshow.render) với seed + lịch sử công thức hiệu ứng của Botkeep.
- "batch": nhiều việc trong MỘT phiên (video dài cắt nhiều phần): nạp model + tải nguồn một lần.
- "kids": 🧸 truyện bé (rmvideo/kids/job.py): model chữ viết truyện theo đề -> 8 ảnh -> giọng VieNeu -> dựng.
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
    import base64
    for key, name in (("logo_b64", "logo.png"), ("sting_b64", "sting.mp3")):     # logo, âm hiệu màn kết
        if spec.get(key):
            os.makedirs(os.path.join(app, "assets"), exist_ok=True)
            with open(os.path.join(app, "assets", name), "wb") as f:
                f.write(base64.b64decode(spec[key]))


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
    if ref.get("mounted"):               # file trong dataset / notebook đã gắn vào phiên (kho nhạc bé): không cần tải
        path = mounted_file(str(ref["mounted"]), str(ref.get("name") or ""), INPUT_ROOT)
        if path:
            _FETCHED[key] = path
            return path
        errs.append(f"dataset {ref['mounted']} không có {ref.get('name')}")
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
    """async synth(items, tmp) -> {k: {"path", "words"}}. items: [(k, lời)] hoặc [(k, lời, tên giọng VieNeu)] — truyện bé
    đọc lời nhân vật bằng giọng riêng (rmvideo/kids/dialogue.py); giọng lạ / lỗi thì rơi về giọng chung."""
    import core_video
    import vi_text
    voice = spec.get("voice") or {}
    engine = voice.get("engine") or "vieneu"
    if engine == "vieneu":
        tts = _vieneu()
        name = voice.get("name") or "Mỹ Duyên"

        def run(items, tmp):
            items = [(it[0], it[1], (it[2] if len(it) > 2 and it[2] else name)) for it in items]
            out = {}
            groups: dict[str, list] = {}
            for k, t, v in items:
                groups.setdefault(v, []).append((k, vi_text.normalize(t)))    # VieNeu không tự đọc số/ký hiệu
            for v, grp in groups.items():
                texts = [t for _, t in grp]
                if ref_path:
                    audios = [tts.infer(t, ref_audio=ref_path) for t in texts]
                else:
                    try:
                        audios = tts.infer_batch(texts, voice=v)
                    except Exception:
                        try:
                            audios = [tts.infer(t, voice=v) for t in texts]
                        except Exception:                                  # giọng không có trong bộ preset
                            logging.warning("VieNeu: giọng «%s» không dùng được, đọc bằng «%s»", v, name)
                            audios = tts.infer_batch(texts, voice=name)
                for (k, _), a in zip(grp, audios):
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

# nguồn -> tiếng nền đã tách trong PHIÊN Kaggle này (bản đầy đủ + bản ngắn cùng video: tách MỘT lần). None = không
# ở trong một phiên (gọi produce trực tiếp, vd trong test): không dùng lại, tránh lẫn giữa các lần gọi không liên quan
_SEP_DONE: dict | None = None


async def produce(spec: dict, inp: str, tmp: str, synth) -> dict:
    """Remake: CÙNG hàm dựng với đường Botkeep (rmvideo/app/assemble.py), trừ bước AI rút gọn (không có key ở đây;
    Botkeep đã rút gọn trước theo tốc độ của giọng): đoạn còn dài thì nói nhanh hơn trong giới hạn của plan_voice."""
    import config
    import vocal_sep
    from rmvideo.app.assemble import assemble
    t = {}
    segs = spec["segments"]
    out_dur = float(spec["out_dur"])
    sep_task = None
    reused = mounted_ambient(spec.get("amb_token", "")) if spec.get("mode") == "cook" else None
    src_key = json.dumps(spec.get("src") or {}, sort_keys=True)
    done = (_SEP_DONE or {}).get(src_key)
    if not reused and spec.get("mode") == "cook" and done and os.path.exists(done):
        reused = done                                   # việc trước trong cùng phiên đã tách giọng video này
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
            if ok and _SEP_DONE is not None:
                _SEP_DONE[src_key] = p
            return p if ok else None
        sep_task = asyncio.create_task(_sep())

    voice_bed = None
    if spec.get("voice_bed"):                         # nhạc lót dưới lời (VOICE_BED): tải lỗi thì dựng không nhạc lót
        try:
            vb = spec["voice_bed"]
            voice_bed = {"path": await fetch(vb["music"], tmp, "bed"), "trim": float(vb.get("trim") or 0.0),
                         "name": vb.get("name") or ""}
        except Exception as e:
            logging.warning("Không tải được nhạc lót: %s", str(e)[:200])

    async def _ambient():
        amb = await sep_task if sep_task else None
        gate = None
        if spec.get("mode") == "cook" and not amb:
            gate = [tuple(x) for x in spec.get("speech") or []] or [(0.0, 99999.0)]
        return amb, gate
    try:
        res = await assemble(inp, tmp, mode=spec["mode"], segs=segs, out_dur=out_dur,
                             render_dur=float(spec.get("render_dur") or 0), synth=synth, ambient=_ambient,
                             subs=bool(spec.get("subs")), pos_y=spec.get("pos_y"), sub_style=config.SUB_STYLE,
                             bands=spec.get("bands") or None, allow_hd=bool(spec.get("allow_hd", True)),
                             title=spec.get("title") or "", stretch=float(spec.get("stretch") or 1.0),
                             teaser=spec.get("teaser"), edit=spec.get("edit"), overlays=spec.get("overlays"),
                             tail=float(spec.get("tail") or 0.0), voice_bed=voice_bed)
    finally:
        if sep_task and not sep_task.done():          # lỗi giữa chừng: không để tiến trình tách giọng chạy mồ côi
            sep_task.cancel()
            await asyncio.gather(sep_task, return_exceptions=True)
    t.update(res.timings)
    return {"ok": True, "video": res.video, "plan": res.plan, "too_long": res.too_long, "srt": res.srt,
            "amb": bool(res.ambient), "notes": res.notes, "timings": t, "fast": res.fast}


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
        if spec.get("asr") == "phowhisper":              # model tiếng Việt (VinAI) gắn từ notebook rmvideo-phowhisper
            ct2 = mounted_dir("phowhisper-large-ct2")
            if ct2:
                asr_local.MODEL = ct2
                t["asr_model"] = "phowhisper-large"
            else:
                logging.warning("Không thấy model PhoWhisper trong /kaggle/input, bóc lời bằng large-v3")
        d = await asyncio.to_thread(asr_local.transcribe, vocals, spec.get("language") or "", spec.get("prompt") or "")
        t["asr"] = round(time.time() - t1, 1)
        cuts, bands = await scene_task
    finally:
        if not scene_task.done():
            scene_task.cancel()
            await asyncio.gather(scene_task, return_exceptions=True)
    files = {}
    inpainted: list = []
    if spec.get("inpaint") and bands:                   # TEXT_INPAINT=1: LaMa vẽ lại nền ở dải chữ (GPU), thay cho che mờ
        from rmvideo.media import inpaint
        t1 = time.time()
        try:
            cp = os.path.join(tmp, "clean.mp4")
            info = await asyncio.to_thread(inpaint.clean_video, inp, [tuple(b) for b in bands], cp, logging.info,
                                           mask=spec.get("inpaint_mask"))
            files["clean.mp4"] = cp
            inpainted = [list(b) for b in bands]
            t["inpaint"] = round(time.time() - t1, 1)
            logging.info("LaMa xoá chữ: %s khung, %ss", info.get("frames"), info.get("secs"))
        except Exception as e:                          # lỗi thì Botkeep che mờ như cũ
            t["inpaint_error"] = str(e)[:200]
            logging.warning("LaMa xoá chữ lỗi (%s): Botkeep sẽ che mờ như cũ", str(e)[:200])
    a = scenes.analyzed(inp)
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
            "bands": [list(b) for b in bands], "inpainted": inpainted, "dur": dur, "sep": sep_ok,
            "thumb_fps": scenes.THUMB_FPS, "timings": t, "files": files}


INPUT_ROOT = "/kaggle/input"


def mounted_file(slug: str, name: str, root: str = INPUT_ROOT) -> str | None:
    """Đường dẫn file `name` trong dataset / notebook `slug` đã gắn vào phiên (/kaggle/input/datasets/<user>/<slug>/...);
    không có thư mục đúng slug thì lấy file cùng tên ở bất kỳ đâu trong root."""
    if not name or not os.path.isdir(root):
        return None
    loose = None
    for r, _dirs, files in os.walk(root):
        if name in files:
            p = os.path.join(r, name)
            if slug and slug in r.split(os.sep):
                return p
            loose = loose or p
    return loose


def mounted_dir(name: str, root: str = "/kaggle/input") -> str | None:
    """Thư mục `name` (vd model đã chuyển đổi) trong đầu ra notebook đã gắn vào phiên này."""
    if not os.path.isdir(root):
        return None
    for r, dirs, files in os.walk(root):
        if name in dirs and "model.bin" in os.listdir(os.path.join(r, name)):
            return os.path.join(r, name)
    return None


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
    from rmvideo.media import filtergraph
    os.makedirs(tmp, exist_ok=True)
    # tông màu Botkeep đã tự chọn cho job này (BRAND_LOOK=auto): video Kaggle dựng cùng tông với ảnh bìa Botkeep làm.
    # Mỗi việc chạy trong asyncio.run riêng (ngữ cảnh riêng): việc không mang sẵn thì renderer tự đo trên nguồn.
    if spec.get("look") in filtergraph.LOOKS or spec.get("look") == "goc":
        filtergraph.AUTO_LOOK.set(spec["look"])
    from rmvideo.media import framing
    framing.apply_spec(spec)                            # khung dọc theo nội dung Botkeep đã chọn (1.1.2)
    t0 = time.time()
    kind = spec.get("kind") or "remake"
    if kind == "analyze":
        return await analyze(spec, tmp)
    if kind == "kids":                                  # 🧸 truyện bé: chữ -> ảnh -> giọng -> dựng (rmvideo/kids/job.py)
        from rmvideo.kids import job as kids_job
        return await kids_job.run(spec, tmp)
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
    global _SEP_DONE
    _SEP_DONE = {}                                       # một phiên Kaggle: các việc cùng video dùng chung tiếng nền
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
