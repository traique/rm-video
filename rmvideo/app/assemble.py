"""Dựng phần "giọng mới + phụ đề + tiếng nền + render" từ lời đã chốt — MỘT bản dùng chung cho Botkeep và Kaggle.

Trước đây có hai bản phải giữ giống nhau bằng tay: rmvideo/app/flow/remake.py (đường Botkeep) và kaggle_job.produce
(chạy trong notebook). Mỗi tham số mới (đoạn mở đầu, kéo chậm, cửa sổ tiếng gốc...) phải sửa hai nơi, đã từng lệch
(bản 31: `int(render_dur)` chỉ sửa một bên).

Chuỗi: đọc giọng (synth do caller cấp: edge/CapCut trên Botkeep, VieNeu/edge trên Kaggle) -> đo độ dài tiếng nói
-> xếp giọng vào khe (plan_voice) -> [AI rút gọn đoạn còn dài rồi đọc lại — chỉ Botkeep có key] -> ghép track giọng ->
phụ đề khớp từng từ -> chờ tiếng nền (tách giọng / dùng lại) -> render.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import subs as subs_mod
from rmvideo.media import filtergraph, renderer, voiceplan

# items [(chỉ số đoạn, lời)] + thư mục tạm -> {chỉ số: {"path": file giọng, "words": mốc từng từ | None}}
Synth = Callable[[list[tuple[int, str]], str], Awaitable[dict[int, dict]]]
# (đoạn quá dài, độ dài đo được) -> chỉ số đoạn đã được viết ngắn lại (lời mới đã ghi vào segs)
Shorten = Callable[[list[int], list[float]], Awaitable[list[int]]]
# -> (tiếng nền đã bỏ giọng gốc | None, các quãng tắt tiếng nền lúc người gốc nói | None)
Ambient = Callable[[], Awaitable[tuple[str | None, list | None]]]


@dataclass
class Assembled:
    video: str
    plan: list
    too_long: list
    srt: str
    ambient: str | None
    clips: list
    notes: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    @property
    def fast(self) -> int:
        """Số đoạn phải đọc nhanh hơn tốc độ chung tối đa."""
        return sum(1 for p in self.plan if p["factor"] > 1.12)

    def drift(self, segs: Sequence[dict]) -> float:
        """Lệch lớn nhất giữa chỗ giọng mới bắt đầu và mốc của đoạn (giây)."""
        return max((abs(p["start"] - s["start"]) for p, s in zip(self.plan, segs)), default=0.0)


def spoken_items(segs: Sequence[dict], idx: Sequence[int] | None = None) -> list[tuple[int, str]]:
    """Đoạn cần đọc: có lời và không giữ nguyên tiếng gốc."""
    ks = range(len(segs)) if idx is None else idx
    return [(k, segs[k]["text"]) for k in ks
            if segs[k].get("ost") != "orig" and (segs[k].get("text") or "").strip()]


def _edit_kw(edit: dict | None) -> dict[str, Any]:
    """Chỉ truyền kế hoạch dựng khi có (giữ nguyên lời gọi render cũ khi REMAKE_STYLE tắt)."""
    return {"edit": edit} if edit else {}


MEASURE_PARALLEL = 4     # tối đa chừng này ffmpeg/ffprobe đo cùng lúc (trước đây 30-45 tiến trình một lượt: máy 2GB RAM)


async def _durations(clips: Sequence[dict]) -> list[float]:
    sem = asyncio.Semaphore(MEASURE_PARALLEL)

    async def one(c: dict) -> float:
        if not c.get("path"):
            return 0.0
        async with sem:
            return await voiceplan.speech_duration(c["path"], c.get("words"))
    return list(await asyncio.gather(*[one(c) for c in clips]))


async def assemble(inp: str, tmp: str, *, mode: str, segs: list, out_dur: float, render_dur: float, synth: Synth,
                   ambient: Ambient, subs: bool, pos_y: int | None, sub_style: str, bands: Any, allow_hd: bool,
                   title: str, stretch: float, teaser: dict | None, shorten: Shorten | None = None,
                   on_step: Callable[[str], None] = lambda s: None, tts_step: str = "", wait_step: str = "",
                   before_render: Callable[[], Any] | None = None, edit: dict | None = None) -> Assembled:
    """Đọc giọng + xếp + phụ đề + tiếng nền + render. segs: [{i, start, slot, span, text, ost, ...}] trên trục thời
    gian video ra (đã cộng đoạn mở đầu, đã kéo chậm). Ném lỗi của synth / ffmpeg cho caller."""
    t: dict = {}
    if tts_step:
        on_step(tts_step)
    t0 = time.time()
    voiced = await synth(spoken_items(segs), tmp)
    t["tts"] = round(time.time() - t0, 1)
    clips = [voiced.get(k) or {"path": None, "words": []} for k in range(len(segs))]
    durs = await _durations(clips)
    plan, too_long = voiceplan.plan_voice(durs, segs, out_dur)
    if too_long and shorten is not None:                    # đoạn nào vẫn quá dài -> AI viết ngắn lại 1 lần
        on_step(f"Rút gọn {len(too_long)} đoạn quá dài")
        changed = await shorten(too_long, durs)
        if changed:
            redo = await synth(spoken_items(segs, changed), tmp)
            for k in changed:
                clips[k] = redo.get(k) or {"path": None, "words": []}
            for k, d in zip(changed, await _durations([clips[k] for k in changed])):
                durs[k] = d
        plan, too_long = voiceplan.plan_voice(durs, segs, out_dur)
    voice_track = os.path.join(tmp, "voice.wav")
    await voiceplan.compose_voice(clips, plan, voice_track, out_dur)
    srt, ass = "", None
    if subs:
        words = await voiceplan.words_for_track(clips, plan, voice_track, filtergraph.subtitle_texts(segs, teaser))
        if words:
            srt = subs_mod.to_srt(words)
            ass = os.path.join(tmp, "subs.ass")
            subs_mod.write_ass(words, ass, pos_y, sub_style)
    if wait_step:
        on_step(wait_step)
    amb, gate = await ambient()
    on_step("Render (FFmpeg)")
    if before_render is not None:
        before_render()
    out = os.path.join(tmp, "output.mp4")
    notes: list = []
    t0 = time.time()
    await renderer.render(inp, voice_track, out, mode, render_dur, ass, bands, amb, gate, allow_hd=allow_hd,
                            title=title, tmp=tmp, stretch=stretch, ost=filtergraph.ost_windows(segs, out_dur),
                            notes=notes, teaser=teaser, **_edit_kw(edit))
    t["render"] = round(time.time() - t0, 1)
    return Assembled(out, plan, too_long, srt, amb, clips, notes, t)
