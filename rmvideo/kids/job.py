"""(Kaggle) Một việc «kids»: đề (Botkeep chọn) -> model chữ viết truyện -> 8 ảnh -> giọng VieNeu -> dựng -> result.json.

Chạy trong kaggle_job.run_one khi spec["kind"] == "kids". Không cần key API. Thứ tự nạp model để VRAM không tràn:
model chữ (giải phóng) -> model ảnh (giải phóng) -> VieNeu (nhỏ). qa_video() dùng cả ở Botkeep sau khi tải video về."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Callable

from rmvideo.kids import images as images_mod, llm, prompts, render, story as story_mod

DUR_MIN, DUR_MAX = 60.0, 88.0          # Reels nhận 3-90 giây; chừa 2 giây (render.plan tự co vào MAX_LEN = 87s)
Log = Callable[[str], None]


async def qa_video(path: str) -> list[str]:
    """Kiểm video bằng mã: tồn tại, dài 60-88s, 1080x1920, có tiếng. Trả danh sách lỗi (rỗng = đạt)."""
    from rmvideo.media import ff, renderer
    problems = []
    if not path or not os.path.exists(path) or os.path.getsize(path) < 100_000:
        return ["không có file video"]
    dur = await ff.probe_duration(path)
    if not DUR_MIN <= dur <= DUR_MAX:
        problems.append(f"dài {dur:.1f}s, cần {DUR_MIN:.0f}-{DUR_MAX:.0f}s")
    w, h, _ = await renderer.source_info(path)
    if (w, h) != (images_mod.OUT_W, images_mod.OUT_H):
        problems.append(f"khung {w}x{h}, cần {images_mod.OUT_W}x{images_mod.OUT_H}")
    if not await ff.has_audio(path):
        problems.append("không có tiếng")
    return problems


async def run(spec: dict, tmp: str, log: Log | None = None) -> dict:
    log = log or (lambda s: logging.info("Truyện bé: %s", s))
    data = spec.get("data") or story_mod.load()
    brief = spec["brief"]
    t: dict = {}
    os.makedirs(tmp, exist_ok=True)
    t0 = time.time()
    try:
        st, attempts = await asyncio.to_thread(llm.generate_story, spec, data, tmp, log)
    except llm.StoryRejected as e:                  # các lượt (kể cả chữ model viết) vào result.json để soát lời nhắc
        return {"ok": False, "kind": "kids", "error": str(e), "attempts": e.attempts, "timings": {"llm": round(time.time() - t0, 1)}}
    t["llm"] = round(time.time() - t0, 1)
    texts = [s["text"] for s in st["scenes"]]
    plist = [prompts.image_prompt(s, brief, data) for s in st["scenes"]]
    seeds = [int(brief.get("seed") or 1) + i for i in range(len(plist))]
    t0 = time.time()
    imgs = await asyncio.to_thread(images_mod.generate, plist, seeds, os.path.join(tmp, "img"),
                                   (spec.get("image") or {}).get("model") or "schnell", log)
    t["images"] = round(time.time() - t0, 1)
    import kaggle_job
    t0 = time.time()
    synth = kaggle_job.make_synth(spec, None)
    recap = story_mod.recap_text(brief)             # câu ôn từ (mã viết) đọc trong màn kết: mỗi từ chắc chắn được nghe kèm nghĩa
    voiced = await synth(list(enumerate(texts + [recap])), tmp)
    clips = [voiced.get(k) or {"path": None} for k in range(len(texts))]
    recap_clip = voiced.get(len(texts)) or {"path": None}
    t["tts"] = round(time.time() - t0, 1)
    notes: list[str] = []
    music = None
    if spec.get("music"):
        try:
            music = await kaggle_job.fetch(spec["music"], tmp, "music")
        except Exception as e:
            notes.append(f"không tải được nhạc nền ({str(e)[:100]}): dựng không nhạc")
            logging.warning("Truyện bé: %s", notes[-1])
    t0 = time.time()
    out = os.path.join(tmp, "output.mp4")
    res = await render.render(imgs, clips, texts, brief, out, tmp, music=music, title=st["title"],
                              channel=spec.get("channel") or {}, rnd=random.Random(int(brief.get("seed") or 0)),
                              recap=(recap_clip, recap) if recap_clip.get("path") else None)
    t["render"] = round(time.time() - t0, 1)
    sj = os.path.join(tmp, "story.json")
    with open(sj, "w", encoding="utf-8") as f:
        json.dump({"story": st, "brief": brief, "prompts": plist, "attempts": attempts, "plan": res["plan"]}, f,
                  ensure_ascii=False, indent=1)
    files = {"story.json": sj}
    for i, p in enumerate(imgs, 1):
        files[f"scene_{i:02d}.png"] = p
    bad = await qa_video(out)
    if bad:                     # vẫn trả video + ảnh + kịch bản về /kaggle/working để xem được vì sao hỏng (Botkeep coi là lỗi)
        return {"ok": False, "kind": "kids", "error": "Video dựng ra không đạt: " + "; ".join(bad), "video": out,
                "story": st, "duration": res["length"], "attempts": attempts, "timings": t, "files": files}
    cov, thumb = await render.cover(out, tmp)
    files.update({"cover.jpg": cov, "thumb.jpg": thumb})
    return {"ok": True, "kind": "kids", "video": out, "story": st, "srt": res["srt"], "duration": res["length"],
            "attempts": attempts, "music": bool(music), "music_gain_db": res.get("music_gain_db"),
            "notes": notes, "timings": t, "files": files}
