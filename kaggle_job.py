"""Chạy TRÊN Kaggle (bot gói file này cùng các module cần thiết vào notebook, xem kaggle_worker.py).

Nhận kịch bản ĐÃ CHỐT (lời mới từng đoạn + mốc thời gian, do Botkeep bóc lời + AI viết) rồi làm phần nặng:
đọc giọng VieNeu (GPU, giọng miền Nam / giọng nhân bản) -> xếp giọng một tốc độ chung (core_video.plan_voice) ->
phụ đề -> tách giọng gốc (Kim_Vocal_2, 4 lõi) -> dựng video (core_video.render, 1080p). Không cần key API nào:
lời đã viết xong ở Botkeep. Kết quả: output.mp4 + result.json trong /kaggle/working."""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import traceback

APP = os.path.dirname(os.path.abspath(__file__))


def prepare_env(spec: dict, app: str = APP):
    """Cài đặt (không có bí mật) + logo cho config/core_video, các file trạng thái để ngoài thư mục output."""
    with open(os.path.join(app, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(spec.get("settings") or {}, f, ensure_ascii=False)
    for k, v in (("JOBS_DB", "/tmp/rm_jobs.db"), ("RENDER_SPEED_FILE", "/tmp/rm_speed.json"),
                 ("RECENT_LOOKS", "/tmp/rm_looks.json"), ("SEP_BUDGET", "1500")):
        os.environ.setdefault(k, v)
    if spec.get("logo_b64"):
        import base64
        os.makedirs(os.path.join(app, "assets"), exist_ok=True)
        with open(os.path.join(app, "assets", "logo.png"), "wb") as f:
            f.write(base64.b64decode(spec["logo_b64"]))


def vieneu_synth_factory(spec: dict, ref_path: str | None):
    """Trả hàm synth(items, tmp) -> {k: đường dẫn wav}. items: [(k, chữ đã chuẩn hoá)]."""
    from vieneu import Vieneu
    tts = Vieneu()                       # có CUDA thì chạy GPU, không thì ONNX trên CPU
    name = (spec.get("voice") or {}).get("name") or "Mỹ Duyên"

    def synth(items, tmp):
        out = {}
        texts = [t for _, t in items]
        if ref_path:
            audios = [tts.infer(t, ref_audio=ref_path) for t in texts]
        else:
            try:
                audios = tts.infer_batch(texts, voice=name)
            except Exception:
                audios = [tts.infer(t, voice=name) for t in texts]
        for (k, _), a in zip(items, audios):
            p = os.path.join(tmp, f"v_{k:03d}.wav")
            tts.save(a, p)
            out[k] = p
        return out
    return synth


async def produce(spec: dict, inp: str, tmp: str, synth) -> dict:
    """Phần nặng, giống hệt đường chạy trên Botkeep (pipeline._process) từ sau khi lời đã chốt, trừ bước AI rút gọn
    (không có key ở đây): đoạn còn dài sau khi đọc thì nói nhanh hơn trong giới hạn của plan_voice."""
    import config
    import core_video
    import subs as subs_mod
    import vi_text
    import vocal_sep
    t = {}
    segs = spec["segments"]
    out_dur = float(spec["out_dur"])
    items = [(k, vi_text.normalize(s["text"])) for k, s in enumerate(segs)
             if s.get("ost") != "orig" and (s.get("text") or "").strip()]
    t0 = time.time()
    paths = await asyncio.to_thread(synth, items, tmp)
    t["tts"] = round(time.time() - t0, 1)
    clips = [{"path": paths.get(k), "words": None} for k in range(len(segs))]
    durs = [await core_video.speech_duration(c["path"]) if c["path"] else 0.0 for c in clips]
    plan, too_long = core_video.plan_voice(durs, segs, out_dur)
    voice_track = os.path.join(tmp, "voice.wav")
    await core_video.compose_voice(clips, plan, voice_track, out_dur)
    srt, ass = "", None
    if spec.get("subs"):
        words = await core_video.words_for_track(clips, plan, voice_track,
                                                 ["" if s.get("ost") == "orig" else s["text"] for s in segs])
        if words:
            srt = subs_mod.to_srt(words)
            ass = os.path.join(tmp, "subs.ass")
            subs_mod.write_ass(words, ass, spec.get("pos_y"), config.SUB_STYLE)
    amb, gate = None, None
    if spec.get("mode") == "cook":
        sep = spec.get("sep") or {}
        if sep.get("ok"):
            t0 = time.time()
            p = os.path.join(tmp, "ambient.wav")
            if await vocal_sep.remove_voice(inp, p, windows=sep.get("windows")):
                amb = p
            t["sep"] = round(time.time() - t0, 1)
        if not amb:
            gate = [tuple(x) for x in spec.get("speech") or []] or [(0.0, 99999.0)]
    t0 = time.time()
    out = os.path.join(tmp, "output.mp4")
    notes: list = []
    await core_video.render(inp, voice_track, out, spec["mode"], int(spec.get("render_dur") or 0), ass,
                            spec.get("bands") or None, amb, gate, allow_hd=bool(spec.get("allow_hd", True)),
                            title=spec.get("title") or "", tmp=tmp, stretch=float(spec.get("stretch") or 1.0),
                            ost=core_video.ost_windows(segs, out_dur), notes=notes)
    t["render"] = round(time.time() - t0, 1)
    return {"ok": True, "video": out, "plan": plan, "too_long": too_long, "srt": srt, "amb": bool(amb),
            "notes": notes, "timings": t, "fast": sum(1 for p in plan if p["factor"] > 1.12)}


def main(spec: dict, out_dir: str = "/kaggle/working"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    prepare_env(spec)
    sys.path.insert(0, APP)
    result = {"ok": False, "timings": {}}
    tmp = "/tmp/rm_job"
    os.makedirs(tmp, exist_ok=True)
    try:
        import core_video
        t0 = time.time()
        inp, _, _, _ = core_video._download_sync(spec["url"], tmp, int(spec.get("max_duration") or 300))
        result["timings"]["download"] = round(time.time() - t0, 1)
        ref = None
        if spec.get("ref_b64"):
            import base64
            ref = os.path.join(tmp, "ref" + (spec.get("ref_ext") or ".mp3"))
            with open(ref, "wb") as f:
                f.write(base64.b64decode(spec["ref_b64"]))
        t0 = time.time()
        synth = vieneu_synth_factory(spec, ref)
        result["timings"]["load_tts"] = round(time.time() - t0, 1)
        res = asyncio.run(produce(spec, inp, tmp, synth))
        # /tmp và /kaggle/working là hai ổ khác nhau trên Kaggle: os.replace báo "Invalid cross-device link" (chạy thật)
        shutil.move(res.pop("video"), os.path.join(out_dir, "output.mp4"))
        res["timings"] = {**result["timings"], **res["timings"]}
        result = res
    except Exception:
        result["error"] = traceback.format_exc()[-2500:]
        logging.error("Job Kaggle lỗi:\n%s", result["error"])
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return result
