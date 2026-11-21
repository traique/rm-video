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

from rmvideo.kids import dialogue, images as images_mod, llm, prompts, render, story as story_mod

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
    image_model = (spec.get("image") or {}).get("model") or "schnell"
    images_mod.prefetch(image_model, log)           # chép model ảnh (14GB) từ ổ gắn về đĩa cục bộ trong lúc viết truyện
    if spec.get("story"):                           # Botkeep đã viết sẵn (Gemini): bỏ bước model chữ
        st, attempts = spec["story"], list(spec.get("attempts") or [])
        log("Truyện đã viết sẵn trên Botkeep, bỏ bước model chữ")
    else:
        try:
            st, attempts = await asyncio.to_thread(llm.generate_story, spec, data, tmp, log)
        except llm.StoryRejected as e:              # các lượt (kể cả chữ model viết) vào result.json để soát lời nhắc
            return {"ok": False, "kind": "kids", "error": str(e), "attempts": e.attempts,
                    "timings": {"llm": round(time.time() - t0, 1)}}
    t["llm"] = round(time.time() - t0, 1)
    texts = [s["text"] for s in st["scenes"]]
    lora = str((spec.get("image") or {}).get("lora") or "")
    prop = (str(st.get("prop_vi") or ""), str(st.get("prop_en") or ""))
    plist = [prompts.image_prompt(s, brief, data, i, use_lora=bool(lora), prop=prop) for i, s in enumerate(st["scenes"])]
    seeds = [int(brief.get("seed") or 1) + i for i in range(len(plist))]
    t0 = time.time()
    check = (spec.get("image") or {}).get("check", True)
    expected = [len(sc.get("characters") or []) for sc in st["scenes"]] if check else None
    chars = data["characters"]
    colors = [[f"{chars.get(k, {}).get('name_en', '').split()[-1].lower()}:{prompts.shirt_color(chars.get(k, {}))}"
               for k in sc.get("characters") or [] if k in chars]
              for sc in st["scenes"]] if check else None       # «rabbit:red»: soát cả áo nào dưới đầu nào
    contact = [len(sc.get("characters") or []) >= 2 and any(w.get("contact") for w in story_mod.beat_words(sc["text"], brief, data))
               for sc in st["scenes"]] if check else None       # cảnh dạy «hug» / «help» / «share»: hai bạn phải chạm nhau
    props = [prop[1] if prompts.prop_required(i, sc, prop) else "" for i, sc in enumerate(st["scenes"])] if check else None
    istats: dict = {}
    imgs = await asyncio.to_thread(images_mod.generate, plist, seeds, os.path.join(tmp, "img"), image_model, log,
                                   expected=expected, stats=istats, colors=colors, lora=lora, contact=contact, props=props)
    t["image_checked"], t["image_redo"] = istats.get("checked", 0), istats.get("redo", 0)
    if lora:
        t["lora"] = istats.get("lora", False)
    t["images"] = round(time.time() - t0, 1)
    import kaggle_job
    t0 = time.time()
    notes: list[str] = []
    synth = kaggle_job.make_synth(spec, None)
    recap = story_mod.recap_text(brief)             # câu ôn từ (mã viết) đọc trong màn kết: mỗi từ chắc chắn được nghe kèm nghĩa
    hook = (st.get("hook") or "").strip()           # câu móc 3 giây đầu (đọc trước truyện)
    narrator = (spec.get("voice") or {}).get("name") or ""
    multi = bool((spec.get("voice") or {}).get("multi", True))
    en_voice = str((spec.get("voice") or {}).get("en", dialogue.EN_VOICE) or "")    # trống: VieNeu đọc cả từ tiếng Anh
    chars = data["characters"]
    # mỗi clip (cảnh 0-7, 90 = ôn từ, 91 = móc) là các đoạn (người nói, lời); từ tiếng Anh trong đoạn đọc bằng giọng Anh
    groups: dict[int, list[tuple[str, str]]] = {}
    for i, text in enumerate(texts):
        groups[i] = dialogue.split(text, chars, brief.get("main", "")) if multi else [("narrator", text)]
    if recap:
        groups[90] = [("narrator", recap)]
    if hook:
        groups[91] = [("narrator", hook)]
    en_words = [w["en"] for w in brief.get("words", [])] if en_voice else []
    en_texts = sorted({p for segs in groups.values() for _, part in segs
                       for is_en, p in dialogue.split_english(part, en_words) if is_en})
    en_clip: dict[str, str] = {}
    if en_texts:
        async def _en(j: int, w: str) -> None:
            try:
                en_clip[w] = await dialogue.english_clip(w, os.path.join(tmp, f"en_{j:02d}.wav"), en_voice)
            except Exception as e:
                notes.append(f"từ «{w}» đọc bằng VieNeu (giọng tiếng Anh lỗi: {str(e)[:100]})")
                logging.warning("Truyện bé: %s", notes[-1])
        await asyncio.gather(*[_en(j, w) for j, w in enumerate(en_texts)])
    items: list = []                                # (khoá, lời, giọng) cho VieNeu; khoá = clip*10000 + đoạn*100 + mẩu
    order: dict[int, list] = {}                     # clip -> các mẩu theo thứ tự đọc: khoá VieNeu hoặc đường dẫn giọng Anh
    for g, segs in groups.items():
        seq: list = []
        for j, (who, part) in enumerate(segs):
            voice = dialogue.voice_for(who, chars, narrator)
            pieces = dialogue.split_english(part, en_words)
            if any(is_en and p not in en_clip for is_en, p in pieces):
                pieces = [(False, part)]            # giọng Anh lỗi: đọc cả đoạn bằng VieNeu như trước
            for q, (is_en, p) in enumerate(pieces):
                if is_en:
                    seq.append(en_clip[p])
                else:
                    k = g * 10000 + j * 100 + q
                    items.append((k, p, voice))
                    seq.append(k)
        order[g] = seq
    voiced = await synth(items, tmp)
    from rmvideo.media import voice_qa
    qa, bad = voice_qa.report(synth)                # Whisper nghe lại mẩu VieNeu (kaggle_job.make_synth)
    t.update(qa)
    notes += bad

    async def _clip(g: int, name: str) -> dict:
        """Nối các mẩu của một clip thành một file (một mẩu thì dùng luôn)."""
        paths = [x if isinstance(x, str) else voiced.get(x, {}).get("path") for x in order.get(g, [])]
        paths = [p for p in paths if p]
        if len(paths) > 1:
            return {"path": await dialogue.merge(paths, os.path.join(tmp, name))}
        return {"path": paths[0] if paths else None}
    clips: list[dict] = [await _clip(i, f"scene_{i:02d}.wav") for i in range(len(texts))]   # phần dựng giữ nguyên
    recap_clip = await _clip(90, "recap.wav") if recap else {"path": None}
    hook_clip = await _clip(91, "hook.wav") if hook else {"path": None}
    if en_clip:
        t["en_voice"] = en_voice
    t["voices"] = sorted({v for _, _, v in items})
    t["tts"] = round(time.time() - t0, 1)
    music = None
    if spec.get("music"):
        try:
            music = await kaggle_job.fetch(spec["music"], tmp, "music")
        except Exception as e:
            notes.append(f"không tải được nhạc nền ({str(e)[:100]}): dựng không nhạc")
            logging.warning("Truyện bé: %s", notes[-1])
    t0 = time.time()
    out = os.path.join(tmp, "output.mp4")
    res = await render.render(images_mod.for_render(imgs), clips, texts, brief, out, tmp, music=music, title=st["title"],
                              channel=spec.get("channel") or {}, rnd=random.Random(int(brief.get("seed") or 0)),
                              recap=(recap_clip, recap) if recap_clip.get("path") else None,
                              fx=str(spec.get("fx") or "full"),
                              hook=(hook_clip, hook, st.get("hook_text") or "") if hook_clip.get("path") else None,
                              hook_scene=story_mod.hook_scene(st))
    t["render"] = round(time.time() - t0, 1)
    sj = os.path.join(tmp, "story.json")
    with open(sj, "w", encoding="utf-8") as f:
        json.dump({"story": st, "brief": brief, "prompts": plist, "attempts": attempts, "plan": res["plan"]}, f,
                  ensure_ascii=False, indent=1)
    files = {"story.json": sj}
    for i, p in enumerate(imgs, 1):
        files[f"scene_{i:02d}.png"] = p
    import glob
    for p in sorted(glob.glob(os.path.join(tmp, "img", "raw_*_bad*.png"))):     # ảnh bộ kiểm loại: giữ để soát bộ kiểm
        files[os.path.basename(p).replace("raw_", "scene_")] = p
    bad = await qa_video(out)
    if bad:                     # vẫn trả video + ảnh + kịch bản về /kaggle/working để xem được vì sao hỏng (Botkeep coi là lỗi)
        return {"ok": False, "kind": "kids", "error": "Video dựng ra không đạt: " + "; ".join(bad), "video": out,
                "story": st, "brief": brief, "duration": res["length"], "attempts": attempts, "timings": t, "files": files}
    cov, thumb = await render.cover(out, tmp)
    files.update({"cover.jpg": cov, "thumb.jpg": thumb})
    # brief trả về: Qwen viết trên Kaggle thì từ hôm nay (choose_words) chỉ được chốt ở đây -> Botkeep soạn bài đăng / ghi
    # bộ nhớ từ theo đúng từ đã dạy trong video
    return {"ok": True, "kind": "kids", "video": out, "story": st, "brief": brief, "srt": res["srt"],
            "duration": res["length"], "attempts": attempts, "music": bool(music), "music_gain_db": res.get("music_gain_db"),
            "notes": notes, "timings": t, "files": files}
