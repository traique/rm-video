"""Làm lại video CÓ LỜI (nấu ăn / review) — các bước của một lần làm, theo thứ tự:

    measure        độ dài nguồn, kéo chậm, độ dài ra, cắt bớt khi quá giới hạn
    analyze        bóc lời + dò cảnh + dải chữ in cứng (Kaggle nếu được, không thì Botkeep), OCR nếu bật
    place          đưa lời / điểm cắt cảnh lên trục thời gian video ra (đã kéo chậm)
    cut_promo      bỏ đoạn quảng cáo sản phẩm của người gốc ở cuối
    plan_separation  tách giọng gốc bằng AI được không (theo số giây có lời, RAM, gói offline)
    (không lời)    -> music.music_only: chèn nhạc nền, không để AI bịa lời
    decide_teaser  đoạn mở đầu cảnh món đã xong (chỉ nấu ăn có lời)
    choose_backend Kaggle hay Botkeep; tốc độ giọng cho AI ước số chữ; bắt đầu tách giọng sớm
    write_script   AI viết lời theo mốc (hoặc theo cảnh; hoặc dùng bản đã duyệt)
    review gate    dừng chờ duyệt (giữ nguồn)
    pre_shorten    rút gọn trước đoạn chắc chắn quá dài
    render_remote / render_local / render_oneshot

Trước đây toàn bộ là MỘT hàm 519 dòng với 15 hàm lồng sửa biến của nhau (nonlocal, cuts[:] = ..., sep.update);
giờ trạng thái là thuộc tính có kiểu của Remake, mỗi bước là một phương thức. Thứ tự, câu chữ hiển thị và quyết định
giữ nguyên (tests/test_snapshot_pipeline.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

import cache
import config
import cover as cover_mod
import ingredients
import kaggle_worker
import metrics
import ocr_text
import offline
import prompt_engine
import scenes
import transcribe
import vocal_sep
from queue_manager import Job
from review import spoken_script
from rmvideo.app import brand, job_factory
from rmvideo.app.assemble import _edit_kw, assemble
from rmvideo.app.flow import analysis, backends, common, dish, style, teaser
from rmvideo.app.flow import music as music_flow
from rmvideo.domain import recipe
from rmvideo.domain.job import LongMode, Mode, Source, Status
from rmvideo.media import edl, ff, filtergraph, renderer, tts_engine, voiceplan

AUTO_MIN_FRESH = 0.7     # 🤖 tự đăng: lời mới phải khác lời gốc ít nhất 70% (không đăng bản gần như chép lời)
SEP_MAX_SPEECH = 45      # giây có lời tối đa để tách giọng AI (máy 1 lõi ~1 giây xử lý / giây audio)
SEP_MAX_VIDEO = 120      # và video không dài quá 2 phút
REEL_MAX = 90            # Facebook Reels <= 90 giây: đoạn mở đầu không được đẩy video ra khỏi Reels
NO_SPEECH_WORDS = 6      # bóc lời ra dưới 6 chữ (không ai nói, hoặc chỉ "ừm", "ngon quá") = video KHÔNG LỜI
FULL_GATE = [(0.0, 99999.0)]   # không có mốc lời: tắt tiếng nền cả bài


def no_speech(segs: list, text: str, timed: bool = True) -> bool:
    """Video không lời: quá ít lời để viết lại mà không bịa, chỉ chèn nhạc nền (NO_SPEECH=music). segs: các đoạn lời
    trong phần sẽ render. Dưới 6 chữ, hoặc chỉ MỘT câu (trước đây AI viết cả bài đọc một mạch từ đúng một câu đó, phần
    còn lại là bịa). timed=False (nguồn bóc lời không có mốc, đọc một mạch theo lời gốc) thì chỉ đếm chữ trên text."""
    if segs or timed:
        return len(segs) < 2 or sum(len(t.split()) for *_, t in segs) < NO_SPEECH_WORDS
    return len((text or "").split()) < NO_SPEECH_WORDS


def promo_cut(job: Job, segs: list, out_dur: float) -> float | None:
    """Mốc cắt đoạn quảng cáo cuối của người gốc. Bản đã duyệt dùng lại ĐÚNG mốc đã quyết lúc viết kịch bản
    (kịch bản duyệt không có đoạn đó; trước đây bản duyệt render lại nguyên đoạn quảng cáo — lỗi N1)."""
    if job.approved:
        return job.cut_at or None
    if job.mode in (Mode.COOK, Mode.REVIEW) and config.CUT_PROMO != "0":
        return prompt_engine.find_promo_tail(segs, out_dur)
    return None


@dataclass
class Separation:
    """Kế hoạch tách giọng gốc trên Botkeep (Kaggle tách cả bài, không cần giới hạn này)."""
    ok: bool = False
    windows: list | None = None          # quãng có lời (trục video gốc) sẽ tách; None = cả bài
    secs: float = 0.0                    # tổng giây có lời trong phần sẽ dựng
    why: str = ""                        # lý do KHÔNG tách (ghi chú cho người dùng)
    engines: list = field(default_factory=list)   # "kim" (Kim_Vocal_2, sạch) rồi "fast" (Spleeter, nhanh)


class Remake:
    """Một lần làm lại video có lời. Gọi `await Remake(...).run()`: trả đường dẫn video, "REVIEW", hoặc
    kaggle_worker.Deferred (phần của video dài đang gom lô Kaggle)."""

    def __init__(self, job: Job, tmp: str, inp: str, title: str, desc: str, *, allow_review: bool, tasks: list,
                 local_only: bool, teaser_ok: bool):
        self.job, self.tmp, self.inp, self.title, self.desc = job, tmp, inp, title, desc
        self.allow_review, self.tasks, self.local_only, self.teaser_ok = allow_review, tasks, local_only, teaser_ok
        # đo
        self.src_dur = self.out_dur = self.render_dur = 0.0
        self.stretch = 1.0
        self.limit = config.MAX_DURATION
        self.is_long = False
        # phân tích
        self.src_hash = ""
        self.an: dict | None = None
        self.text = ""
        self.raw_segs: list = []            # lời gốc trên trục video GỐC
        self.segs: list = []                # lời gốc trên trục video RA (kéo chậm, cộng đoạn mở đầu)
        self.cuts: list = []
        self.bands: list = []
        self.speech: list = []              # lúc người gốc đang nói (trục video ra)
        self.pos_y: int | None = None
        self.sep = Separation()
        # ảnh bìa: thời gian theo video GỐC (trước khi lùi mốc cho đoạn mở đầu); cover_at = khung AI đã chọn sẵn
        self.cover_cuts: list = []
        self.cover_end = 0.0
        self.cover_at: float | None = None
        self.tz: dict | None = None         # đoạn mở đầu {"a", "dur", "still", "t"}
        self.remote = False
        self.amb_task: asyncio.Task | None = None
        self.pre_cover: asyncio.Task | None = None       # ảnh bìa bắt đầu trong lúc chờ Kaggle
        self.target = 0
        self.ass = os.path.join(tmp, "subs.ass")
        self.facts: recipe.Facts | None = None   # món + định lượng đã đối chiếu (bản 42)
        self.edit: edl.EditPlan | None = None    # kế hoạch dựng (REMAKE_STYLE, bản 42)

    # ---------------- các bước ----------------

    async def run(self):
        job = self.job
        await self.measure()
        await self.analyze()
        self.place()
        self.cut_promo()
        self.plan_separation()
        self.cover_cuts = [c / self.stretch for c in self.cuts]
        self.cover_end = self.out_dur / self.stretch
        silent = no_speech([s for s in self.segs if s[0] < self.out_dur - 0.5], self.text, bool(self.raw_segs))
        if not job.approved and config.NO_SPEECH != "narrate" and silent:
            return await music_flow._music_only(job, self.inp, self.tmp, self.title, self.desc, self.out_dur,
                                                self.render_dur, self.stretch, self.is_long, self.bands,
                                                self.local_only, self.start_cover, self.finish_cover)
        await self.understand()
        await self.restyle()
        await self.decide_teaser(silent)
        self.apply_teaser()
        self.choose_backend()
        self.target = int(self.out_dur)               # đường dự phòng (đọc một mạch): theo độ dài SẼ dựng
        if self.raw_segs:                             # (sau khi cắt quảng cáo / thêm đoạn mở đầu)
            self.text = " ".join(t for a, _, t in self.segs if a < self.out_dur - 0.5 and t)
        self.segs = [s for s in self.segs if s[0] < self.out_dur - 0.5]
        timed = await self.write_script()
        if not timed:
            return await self.render_oneshot()
        self.apply_script(timed)
        if self.allow_review and job.source != Source.AUTO and (job.review or config.REVIEW_SCRIPT == "1") \
                and not job.approved:
            return await self.stop_for_review(timed)
        segs_t = timed["segments"]
        await self.pre_shorten(segs_t)
        if self.remote:
            out = await self.render_remote(segs_t)
            if out is not None:
                return out
        return await self.render_local(segs_t, timed)

    async def measure(self):
        job = self.job
        self.src_dur = src_dur = await ff.probe_duration(self.inp)
        if common.auto_full(job) and config.MAX_DURATION + 0.5 < src_dur:
            job.long_mode = LongMode.FULL.value
            job.note(f"ℹ️ Video {src_dur:.0f}s, chọn «Giữ nguyên» nên làm nguyên cả video "
                     f"(≤{config.LONG_MAX // 60} phút, dựng trên Kaggle). ")
        if job.duration and job.duration > src_dur + 1:
            # làm chậm NGAY TRONG lần render cuối thay vì mã hoá riêng cả video một lần (lỗi #7: thêm một thế hệ nén
            # và cả một lượt mã hoá); bóc lời, dò cảnh, tách giọng chạy trên bản gốc rồi nhân mốc thời gian lên
            self.stretch = min(job.duration / src_dur, 1.4)
            job.step = f"Kéo dài video gốc {src_dur:.0f}s -> {src_dur * self.stretch:.0f}s (chậm {1 / self.stretch:.2f}x)"
            if self.stretch < job.duration / src_dur:
                job.note(f"ℹ️ Video gốc chỉ {src_dur:.0f}s; kéo chậm tối đa 1.4x được {src_dur * self.stretch:.0f}s "
                         f"(chậm hơn sẽ lộ). ")
        vid_dur = src_dur * self.stretch
        self.limit = config.LONG_MAX if job.long_mode == LongMode.FULL else config.MAX_DURATION
        self.out_dur = job.out_dur = min(vid_dur, job.duration or vid_dur, self.limit)
        self.render_dur = job.duration or (int(self.limit) if vid_dur > self.limit + 0.5 else 0)
        self.is_long = self.out_dur > 100                 # video dài: 720p
        if vid_dur > self.limit + 0.5 and not job.duration:
            job.note(f"ℹ️ Video {vid_dur:.0f}s, chỉ làm {self.limit}s đầu (chọn chế độ video dài để làm hết). ")

    async def analyze(self):
        job, inp = self.job, self.inp
        self.src_hash = await asyncio.to_thread(cache.file_hash, inp)
        want_gray = bool(job.subs and job.cover)
        has_aud = await ff.has_audio(inp)
        why_an = analysis.analyze_remote(job, inp, self.local_only) if has_aud else "nguồn không có tiếng"
        if not why_an or job.approved:                   # bản đã duyệt: lấy kết quả lần đầu trong cache (nếu có)
            job.step = "Bóc lời + phân tích hình trên Kaggle"
            self.an = await analysis.kaggle_analysis(job, inp, self.tmp, self.title, self.desc, self.src_hash,
                                                     want_gray, run=not why_an)
        if self.an:
            an = self.an
            self.text, self.raw_segs, self.cuts, self.bands = an["text"], an["segs"], an["cuts"], an["bands"]
            tm = an["timings"]
            if tm:
                job.note(f"ℹ️ Bóc lời + phân tích hình trên Kaggle (tách giọng {tm.get('sep', 0):.0f}s, Whisper "
                         f"{tm.get('asr', 0):.0f}s, dò cảnh {tm.get('scenes', 0):.0f}s). ")
        else:
            job.step = ("Bóc lời thoại + dò chữ in cứng (song song)" if scenes.mode() == "light"
                        else "Bóc lời thoại + dò chữ in cứng + tách cảnh (song song)")
            audio_mp3 = os.path.join(self.tmp, "audio.mp3")

            async def _words():
                if not has_aud:                          # clip câm tiếng (quay màn hình, xuất -an): không có lời để bóc
                    return "", []
                await ff.extract_audio(inp, audio_mp3)
                return await transcribe.transcribe_segments(audio_mp3, self.title, self.desc)   # ("", []) nếu thiếu STT
            (self.text, self.raw_segs), (self.cuts, self.bands) = await asyncio.gather(
                _words(), scenes.analyze_cached(inp, self.tmp, self.src_dur, want_gray, self.src_hash,
                                                need_thumbs=not job.approved))   # 1 lượt giải mã: cảnh + ảnh + chữ
        if config.TEXT_DETECT == "ocr" and job.subs and job.cover and ocr_text.available():
            self.bands = await analysis.ocr_bands(inp, self.src_dur, self.src_hash, self.bands)

    def place(self):
        """Lời gốc / điểm chuyển cảnh lên trục video ra (đã làm chậm); vị trí phụ đề theo dải chữ gốc đầu tiên."""
        st = self.stretch
        self.segs = [(round(a * st, 2), round(b * st, 2), t) for a, b, t in self.raw_segs]
        self.cuts = [round(c * st, 2) for c in self.cuts]
        self.speech = [(a, b) for a, b, _ in self.segs]
        if self.bands:
            y, h = filtergraph.band_to_output(*self.bands[0])
            self.pos_y = y + h // 2

    def cut_promo(self):
        """Bỏ đoạn quảng cáo sản phẩm của người gốc ở cuối video (cả hình lẫn tiếng)."""
        cut = promo_cut(self.job, self.segs, self.out_dur)
        if cut and cut < self.out_dur:
            logging.info("Cắt bỏ đoạn quảng cáo của người gốc: %.1fs -> %.1fs", cut, self.out_dur)
            self.job.cut_at = cut
            self.job.note(f"ℹ️ Đã cắt bỏ {self.out_dur - cut:.0f}s quảng cáo sản phẩm của người gốc ở cuối. ")
            self.out_dur = self.render_dur = self.job.out_dur = cut

    def plan_separation(self):
        """Gọi SAU khi đã biết đoạn thực render (đã cắt quảng cáo): tách giọng AI chỉ các đoạn người gốc nói trong
        phần sẽ render; giới hạn theo SỐ GIÂY CÓ LỜI, không theo độ dài video."""
        out_dur = self.out_dur
        end = out_dur / self.stretch                       # trục thời gian của video gốc
        win = (voiceplan.speech_windows([(a, min(b, end)) for a, b, _ in self.raw_segs if a < end], end)
               if self.raw_segs else None)
        secs = sum(b - a for a, b in win) if win is not None else end
        kim = out_dur <= SEP_MAX_VIDEO and secs <= SEP_MAX_SPEECH
        fast = offline.spleeter_available()             # gói offline: Spleeter nhanh ~30 lần, dùng cho video nhiều lời
        why = ("VOICE_SEPARATION=0" if config.VOICE_SEPARATION == "0" else "" if kim or fast else
               f"video {out_dur:.0f}s > {SEP_MAX_VIDEO}s" if out_dur > SEP_MAX_VIDEO else
               f"người gốc nói {secs:.0f}s > {SEP_MAX_SPEECH}s; cài gói offline để tách nhanh")
        self.sep = Separation(ok=self.job.mode == Mode.COOK and not why, windows=win, secs=secs, why=why,
                              engines=(["kim"] if kim else []) + (["fast"] if fast else []))

    async def understand(self):
        """Hiểu món + đối chiếu định lượng với lời gốc (POST_RECIPE / TIPS / REMAKE_STYLE=strong / tự đăng lọc quảng cáo)."""
        job = self.job
        if not self.raw_segs or not dish.wanted(job):
            return
        self.facts = await dish.understand(job, self.inp, self.title, self.desc, self.raw_segs,
                                           self.out_dur / self.stretch, self.src_hash)
        if not job.approved:
            dish.apply(job, self.facts)
        if self.facts is not None and not self.facts.is_recipe and job.source == Source.AUTO \
                and config.AUTO_SKIP_ADS == "1":
            raise RuntimeError(f"{recipe.NOT_RECIPE}: dừng sớm, tự đăng chọn video khác")   # khỏi tốn giọng + dựng

    async def restyle(self):
        """Kế hoạch dựng (REMAKE_STYLE): khúc / tốc độ / cỡ khung trên video gốc, chốt TRƯỚC khi AI viết lời; lời gốc,
        điểm chuyển cảnh, quãng người gốc nói đổi sang trục mới. Bản đã duyệt dùng lại đúng kế hoạch đã lưu."""
        job = self.job
        lv = style.level()
        if not lv or job.mode not in (Mode.COOK, Mode.REVIEW) or not self.raw_segs:
            return
        end = self.out_dur / self.stretch
        segs_src = [(a, min(b, end), t) for a, b, t in self.raw_segs if a < end]
        if job.approved:
            plan = edl.EditPlan.from_dict(job.edit)
            if plan is None:
                return
        else:
            job.step = "Lập kế hoạch dựng (nhịp, khung hình)"
            a = scenes.analyzed(self.inp)
            mot = await style.motion(self.inp, end, a)
            cuts_src = [c / self.stretch for c in self.cuts]
            plan = style.plan_light(end, segs_src, cuts_src, mot, self.stretch, config.TIME_SKIP_LABEL, lv)
            errs = plan.validate()
            if errs:
                job.note(f"ℹ️ Không áp được kế hoạch dựng ({errs[0]}): dựng như bản gốc. ")
                return
            job.edit = plan.to_dict()
            job.note(f"ℹ️ {plan.summary} ")
        self.edit = plan
        cuts_src = [c / self.stretch for c in self.cuts]
        self.segs = plan.map_segments(segs_src)
        self.cuts = sorted(set(plan.cut_points()) | {round(plan.to_out(c), 2) for c in cuts_src if c < end})
        self.speech = plan.map_windows([(a, b) for a, b, _ in segs_src])
        out = round(plan.out_dur(), 2)
        if self.render_dur:
            self.render_dur = min(self.render_dur, out)
        self.out_dur = job.out_dur = out

    async def decide_teaser(self, silent: bool):
        """Đoạn mở đầu: cảnh MÓN ĐÃ XONG chiếu trước 3 giây kèm câu hook (chỉ video nấu ăn có lời)."""
        job = self.job
        if job.approved:
            self.tz = dict(job.teaser) if job.teaser else None   # bản đã duyệt: đúng đoạn mở đầu đã quyết lúc viết lời
        elif self.teaser_ok and job.mode == Mode.COOK and config.TEASER != "0" and not silent:
            job.step = "AI chọn cảnh món đã xong (ảnh bìa + đoạn mở đầu)"
            self.tz, moment, why_tz = await teaser.plan_teaser(self.inp, job.mode, self.title, self.src_dur,
                                                               self.cover_cuts, self.cover_end)
            if moment:
                self.cover_at = moment["t"]               # ảnh bìa = đúng khung đó, khỏi hỏi AI lần nữa
            job.teaser = self.tz or {}
            if why_tz:
                job.note(f"ℹ️ Không chiếu trước cảnh món ({why_tz}). ")

    def apply_teaser(self):
        """Có đoạn mở đầu: mọi mốc lùi T giây; độ dài ra không vượt lựa chọn 30/60/90 hay giới hạn Reels."""
        tz, job = self.tz, self.job
        if not tz:
            return
        self.cover_at = tz["t"]
        T = float(tz["dur"])
        full = self.out_dur + T
        # chọn 30/60/90 giây thì giữ đúng độ dài đó; "giữ nguyên" mà video ≤ 90s thì không để vượt 90s (hết là Reels)
        total = round(min(full, self.limit, job.duration or (REEL_MAX if self.out_dur <= REEL_MAX + 0.01 else full)), 2)
        self.segs = [(round(a + T, 2), round(b + T, 2), t) for a, b, t in self.segs]
        self.cuts = [T] + [round(c + T, 2) for c in self.cuts]   # đoạn mở đầu -> video chính cũng là một lần chuyển cảnh
        self.speech = [(a + T, b + T) for a, b in self.speech]
        self.render_dur = total if (self.render_dur or total < full - 0.01) else 0
        self.out_dur = job.out_dur = total
        job.note(f"ℹ️ Mở đầu bằng {T:.0f} giây cảnh món đã xong (giây {tz['t']:.0f} video gốc"
                 + (", cảnh ngắn nên đứng hình" if tz.get("still") else "") + ") kèm câu hook"
                 + (f"; bớt {full - total:.0f}s cuối cho vừa {total:.0f}s" if total < full - 0.5 else "") + ". ")

    def choose_backend(self):
        """Kaggle hay Botkeep. Tách giọng gốc (nặng CPU) bắt đầu NGAY trên Botkeep, chạy trong lúc chờ AI viết kịch
        bản và đọc giọng (việc mạng, CPU rảnh). Chạy trên Kaggle thì Kaggle tách."""
        job = self.job
        why_local = "chạy trên Botkeep theo yêu cầu" if self.local_only else kaggle_worker.usable(job, self.inp)
        self.remote = not why_local
        prompt_engine.set_voice_rate(kaggle_worker.words_per_sec(job.voice) if self.remote else None)
        if kaggle_worker.voice_name(job.voice) and why_local:
            job.note(f"ℹ️ Giọng VieNeu chưa dùng được ({why_local}): đọc bằng giọng edge. ")
        if not self.remote:
            self.local_ambient()

    async def write_script(self) -> dict | None:
        """Lời mới: bản đã duyệt / AI viết theo mốc lời (nhìn ảnh từng đoạn) / video không lời thì theo cảnh."""
        job = self.job
        if job.approved:                                  # kịch bản đã duyệt: bỏ qua bước AI
            return {"segments": job.approved, "script": spoken_script(job.approved),
                    "title": "", "caption": "", "hashtags": "", "fresh": 1.0}
        if len(self.segs) >= 2:
            self.segs = scenes.snap_to_cuts(self.segs, self.cuts)     # câu mới vang lên đúng lúc chuyển cảnh
            job.step = (f"Viết lại {len(self.segs)} đoạn theo mốc lời"
                        + (f" + {len(self.cuts)} điểm chuyển cảnh" if self.cuts else "") + " (AI)")
            return await prompt_engine.generate_timed_any(
                self.title, self.desc, job.mode, self.segs, self.out_dur,
                images_for=self.keyframes_for,            # AI nhìn ảnh từng đoạn để lời khớp hình
                **({"teaser": float(self.tz["dur"])} if self.tz else {}))
        if len(self.cuts) >= 2 or (not self.cuts and scenes.mode() == "light" and await self.late_cuts()):
            sc = scenes.scene_segments(self.cuts, self.out_dur)   # video không lời: AI nhìn ảnh từng cảnh
            job.step = f"AI xem {len(sc)} cảnh và viết lời cho từng cảnh"
            return await prompt_engine.generate_scenes(self.title, self.desc, job.mode, sc,
                                                       await self.keyframes_for(sc), self.out_dur)
        return None

    def apply_script(self, timed: dict):
        job = self.job
        if not job.approved and brand.add_close(timed["segments"]):   # câu chốt của kênh (BRAND_CLOSE)
            timed["script"] = spoken_script(timed["segments"])
        job.script = spoken_script(timed["segments"]) or timed["script"]
        if timed.get("hook") and not job.approved:
            job.hook = timed["hook"]                      # chữ hook in màn hình 3 giây đầu + ảnh bìa
        if timed.get("fresh", 1.0) < AUTO_MIN_FRESH:     # AI lỗi một phần -> nhiều đoạn giữ nguyên lời gốc
            job.no_autopost = f"lời mới chỉ {timed['fresh'] * 100:.0f}% khác lời gốc"
        if not job.approved:
            block, line = dish.post_parts(job)              # công thức + mẹo đã đối chiếu (POST_RECIPE / TIPS)
            job.post = prompt_engine.platform_post(timed["title"], timed["caption"], timed["hashtags"],
                                                   common.credit_line(job), block, line)

    async def stop_for_review(self, timed: dict) -> str:
        """Dừng chờ duyệt: tách giọng làm sau (lúc render bản đã duyệt); nguồn từ link/Telegram giữ lại để render
        sau khi duyệt KHÔNG phải tải lại (#8)."""
        job = self.job
        job.approved = job_factory.approved_segments(timed["segments"])
        if self.amb_task:
            self.amb_task.cancel()
            await asyncio.gather(self.amb_task, return_exceptions=True)
        if not job.local:
            keep = os.path.join(config.UPLOAD_DIR, f"review_{job.id}{os.path.splitext(self.inp)[1] or '.mp4'}")
            await asyncio.to_thread(shutil.move, self.inp, keep)
            job.local = keep
        job.status, job.step = Status.REVIEW.value, "Chờ bạn duyệt/sửa kịch bản rồi bấm Render"
        return "REVIEW"

    async def pre_shorten(self, segs_t: list):
        """Ước lượng độ dài TRƯỚC khi đọc (kiểu VideoLingo): đoạn chắc chắn tràn thì rút gọn luôn, đỡ một lượt gọi TTS
        rồi lại phải đọc lại. Trên Kaggle biết đúng tốc độ giọng VieNeu và KHÔNG có vòng AI rút gọn sau khi đọc (Kaggle
        không giữ key) -> ước tính đủ (x1.0); trên Botkeep còn vòng rút gọn sau nên ước tính thận trọng (x0.85)."""
        if self.job.approved:
            return
        est_k = 1.0 if self.remote else 0.85
        est = [0.0 if sg.get("ost") == "orig" else
               prompt_engine.spoken_words(sg["text"]) / prompt_engine.words_per_sec() * est_k for sg in segs_t]
        _, sure_long = voiceplan.plan_voice(est, segs_t, self.out_dur)
        if sure_long:
            self.job.step = f"Rút gọn trước {len(sure_long)} đoạn chắc chắn quá dài"
            await self.shorten(segs_t, sure_long, est)

    async def render_remote(self, segs_t: list) -> Any:
        """Đọc giọng + tách giọng + dựng trên Kaggle. None = không gửi được / Kaggle lỗi (đã ghi chú): làm trên Botkeep.
        Phần của video dài: trả Deferred để chạy chung một phiên với các phần khác."""
        job = self.job
        post_title = common.post_title(job, self.title)
        # Kaggle 4 lõi: không cần giới hạn của Botkeep; tách CẢ BÀI nên không cần mốc lời (bóc lời không nghe được
        # chữ nào thì vẫn tách, thay vì tắt hết tiếng nền)
        sep_r = {"ok": self.sep.ok, "windows": self.sep.windows, "secs": self.sep.secs, "why": self.sep.why,
                 "engines": self.sep.engines, "ok_remote": job.mode == Mode.COOK and config.VOICE_SEPARATION != "0"}
        try:
            spec = kaggle_worker.build_spec(
                job, segs_t, self.out_dur, self.render_dur, self.stretch, not self.is_long, post_title, self.bands,
                self.pos_y, sep_r, self.speech, config.LONG_MAX if job.long_mode == LongMode.FULL else config.MAX_DURATION,
                local_path=self.inp, src=job.remote_src or None, amb_token=self.an["token"] if self.an else "",
                teaser=self.tz, edit=self.edit.to_dict() if self.edit else None)
        except Exception as e:
            job.note(f"ℹ️ Không gửi được sang Kaggle ({str(e)[:160]}): làm trên Botkeep. ")
            return None

        async def finish(res: dict, cover: Any = "start") -> str:
            metrics.record("kaggle", "kaggle")
            job.srt = res.get("srt") or ""
            job.note("".join(res.get("notes") or []))
            await self.finish_cover(self.start_cover(post_title) if cover == "start" else cover)
            job.final_segments = common.final_timeline(segs_t, res["plan"], self.out_dur)
            tm = res.get("timings") or {}
            eng = kaggle_worker.voice_engine(job.voice)
            job.note(f"ℹ️ Kaggle ({'GPU' if res.get('gpu') else 'CPU'}) đọc giọng "
                     + (f"VieNeu {kaggle_worker.voice_name(job.voice)}" if eng == "vieneu" else eng)
                     + f" {tm.get('tts', 0):.0f}s, dựng {tm.get('render', 0):.0f}s"
                     + ("; đã tách bỏ giọng gốc bằng AI" if res.get("amb") else "")
                     + (f"; {len(res['too_long'])} đoạn vẫn hơi dài (đọc nhanh hơn)" if res.get("too_long") else "")
                     + ". ")
            return res["video"]
        if kaggle_worker.collecting():
            # video dài: chạy chung một phiên với các phần khác; kèm lời đã chốt để nếu Kaggle lỗi thì làm lại phần
            # này trên Botkeep mà KHÔNG bóc lời + gọi AI lần nữa (đi đường "kịch bản đã duyệt")
            return kaggle_worker.Deferred(spec, finish, job_factory.approved_segments(segs_t))
        self.pre_cover = self.start_cover(post_title)    # ảnh bìa làm trên Botkeep trong lúc Kaggle chạy
        job.step = "Kaggle: chuẩn bị"
        res = await backends.try_produce(job, spec, self.tmp, "ℹ️ Kaggle lỗi ({err:.160}): đọc giọng + dựng trên Botkeep. ")
        return None if res is None else await finish(res, self.pre_cover)

    async def render_local(self, segs_t: list, timed: dict) -> str:
        """Đọc giọng + xếp + phụ đề + tiếng nền + render trên Botkeep (cùng hàm dựng với Kaggle)."""
        job, sep = self.job, self.sep
        self.local_ambient()
        cover_task = None

        def cover_now():
            nonlocal cover_task
            cover_task = self.pre_cover or self.start_cover(common.post_title(job, self.title))
        res = await assemble(
            self.inp, self.tmp, mode=job.mode, segs=segs_t, out_dur=self.out_dur, render_dur=self.render_dur,
            synth=lambda items, tmp_dir: self.synth(segs_t, items, tmp_dir), ambient=self.local_ambient,
            subs=job.subs, pos_y=self.pos_y, sub_style=config.SUB_STYLE, bands=self.bands, allow_hd=not self.is_long,
            title=common.post_title(job, self.title), stretch=self.stretch, teaser=self.tz,
            shorten=lambda idx, durs: self.shorten(segs_t, idx, durs), on_step=lambda st: setattr(job, "step", st),
            tts_step=f"Đọc giọng {len(segs_t)} đoạn song song" + (" + tách giọng gốc" if sep.ok else ""),
            wait_step="Chờ tách giọng gốc xong" if sep.ok else "Render (FFmpeg)", before_render=cover_now,
            edit=self.edit.to_dict() if self.edit else None)
        if res.srt:
            job.srt = res.srt
        if res.notes:
            job.note("".join(res.notes))
        await self.finish_cover(cover_task)
        job.final_segments = common.final_timeline(segs_t, res.plan, self.out_dur)
        if job.mode == Mode.COOK:
            job.note("ℹ️ Đã tách bỏ giọng gốc bằng AI. " if res.ambient else
                     f"ℹ️ Không tách giọng AI ({sep.why}): tắt tiếng nền lúc người gốc nói. " if sep.why else
                     "ℹ️ Tách giọng AI không chạy được (máy chậm/thiếu RAM): tắt tiếng nền lúc người gốc nói. ")
        fast, drift, too_long, bands, cuts = res.fast, res.drift(segs_t), res.too_long, self.bands, self.cuts
        job.note((f"ℹ️ Lời mới {timed['fresh'] * 100:.0f}% khác lời gốc; " if "fresh" in timed else "ℹ️ ")
                 + f"{len(res.clips)} đoạn giọng" + (f", {len(cuts)} điểm chuyển cảnh" if cuts else "")
                 + f"; lệch tối đa {drift:.1f}s"
                 + (f"; {fast} đoạn nói nhanh" if fast else "")
                 + (f"; {len(too_long)} đoạn vẫn hơi dài" if too_long else "")
                 + (f"; đã che {len(bands)} dải chữ gốc" if bands else "") + ".")
        return res.video

    async def render_oneshot(self) -> str:
        """Đường cũ: không có mốc thời gian -> AI viết một kịch bản đọc một mạch."""
        job, title, desc, text, target = self.job, self.title, self.desc, self.text, self.target
        job.step = "Viết kịch bản + content (AI)"
        pack = await prompt_engine.generate(title, desc, job.mode, text, target)
        if pack.get("fallback"):
            job.note("⚠️ AI viết kịch bản lỗi (xem log), đang dùng kịch bản mẫu. Kiểm tra key/model trong Cài đặt. ")
            job.no_autopost = "AI viết kịch bản lỗi, video dùng kịch bản mẫu"
        job.script = pack["script"]
        job.post = prompt_engine.platform_post(pack["title"], pack["caption"], pack["hashtags"], common.credit_line(job))
        job.step = "Đọc giọng AI + phụ đề"
        voice_mp3 = os.path.join(self.tmp, "voice.mp3")
        ass = self.ass if job.subs else None
        got_subs = await tts_engine.tts(job.script, voice_mp3, job.voice, ass, self.pos_y)
        # Lưới an toàn: giọng đọc xong quá sớm (provider đọc nhanh hơn dự tính) -> viết lại dài hơn 1 lần
        voice_dur = await ff.probe_duration(voice_mp3)
        if target and voice_dur < 0.72 * target:
            job.step = f"Giọng {voice_dur:.0f}s ngắn hơn video {target}s, viết lại dài hơn"
            scaled = int(target * target / max(voice_dur, 1.0) * 0.95)
            pack2 = await prompt_engine.generate(title, desc, job.mode, text, scaled)
            if not pack2.get("fallback") and len(pack2["script"].split()) > len(job.script.split()):
                job.script = pack2["script"]
                job.post = prompt_engine.platform_post(pack2["title"], pack2["caption"], pack2["hashtags"],
                                                       common.credit_line(job))
                got_subs = await tts_engine.tts(job.script, voice_mp3, job.voice, ass, self.pos_y)
        job.step = "Render (FFmpeg)"
        out = os.path.join(self.tmp, "output.mp4")
        amb_wav, gate = await self.local_ambient()
        post_title = common.post_title(job, title)
        cover_task = self.start_cover(post_title)
        notes: list = []
        await renderer.render(self.inp, voice_mp3, out, job.mode, self.render_dur,
                                self.ass if (job.subs and got_subs) else None, self.bands, amb_wav, gate,
                                allow_hd=not self.is_long, title=post_title, tmp=self.tmp, stretch=self.stretch,
                                notes=notes, teaser=self.tz, **_edit_kw(self.edit.to_dict() if self.edit else None))
        if notes:
            job.note("".join(notes))
        await self.finish_cover(cover_task)
        if self.bands:
            job.note(f"ℹ️ Đã che {len(self.bands)} dải chữ gốc và đặt phụ đề mới lên đó.")
        return out

    # ---------------- công cụ của các bước ----------------

    async def late_cuts(self) -> bool:
        """Phân tích nhẹ không dò chuyển cảnh; video không lời mới cần (mỗi cảnh một câu): dò ở mức vừa lúc này."""
        self.job.step = "Video không lời: dò chuyển cảnh"
        self.cuts = [round(c * self.stretch, 2) for c in await scenes.detect_cuts(self.inp)]
        return len(self.cuts) >= 2

    def src_time(self, x: float) -> float:
        """Mốc trên video ra -> giây trên video gốc (bỏ đoạn mở đầu, bỏ kéo chậm)."""
        tz = self.tz
        if tz and x < tz["dur"]:
            return tz["a"] + (0.0 if tz.get("still") else min(x, tz["dur"] - 0.1))
        x -= tz["dur"] if tz else 0.0
        return self.edit.src_time(x) if self.edit else x / self.stretch

    def keyframes_for(self, sg: list):
        """Ảnh cho AI: ảnh GIỮA mỗi đoạn, lấy trên video gốc."""
        return scenes.keyframes(self.inp, [(m, m, t) for m, t in ((self.src_time((a + b) / 2), t) for a, b, t in sg)])

    async def ambient(self) -> tuple:
        """Chế độ nấu ăn: tiếng nền KHÔNG còn giọng người gốc. Ưu tiên tách giọng AI (chỉ các đoạn có lời); không được
        -> tắt tiếng nền đúng lúc người gốc nói; không có mốc -> bỏ tiếng nền."""
        if self.job.mode != Mode.COOK:
            return None, None
        an, sep = self.an, self.sep
        if an and an.get("ambient") and os.path.exists(an["ambient"]):
            return an["ambient"], None                    # đã tách CẢ BÀI trên Kaggle lúc phân tích
        if sep.ok:
            for k_eng, engine in enumerate(sep.engines):  # Kim_Vocal_2 (sạch hơn) -> Spleeter (nhanh hơn)
                k = cache.key("ambient", self.src_hash, sep.windows, engine)
                hit = cache.get("ambient", k, ".wav")
                if hit:
                    return hit, None
                p = os.path.join(self.tmp, f"ambient_{engine}.wav")
                run = vocal_sep.remove_voice if engine == "kim" else offline.remove_voice_fast
                if await run(self.inp, p, windows=sep.windows):
                    metrics.record("sep", "onnx" if engine == "kim" else "spleeter", fallback=k_eng > 0)
                    try:                                  # chuyển (không chép): 10MB/phút không nằm hai chỗ tới hết job
                        p = cache.put("ambient", k, p, ".wav", move=True)
                    except OSError:
                        pass
                    return p, None
                metrics.record("sep", "onnx" if engine == "kim" else "spleeter", ok=False, fallback=k_eng > 0)
            metrics.record("sep", "gate", fallback=True)
        return None, (self.speech or FULL_GATE)

    def local_ambient(self) -> asyncio.Task:
        """Việc tách giọng trên Botkeep (tạo một lần; Kaggle lỗi / không dùng thì tạo lúc cần)."""
        if self.amb_task is None:
            self.amb_task = asyncio.create_task(self.ambient())
            self.tasks.append(self.amb_task)
        return self.amb_task

    def start_cover(self, post_title: str) -> asyncio.Task | None:
        """Ảnh bìa chạy song song với render (chấm điểm khung + AI chọn ảnh: nhẹ CPU, chủ yếu chờ mạng)."""
        if config.COVER == "0":
            return None
        a = scenes.analyzed(self.inp)
        t = asyncio.create_task(cover_mod.make_cover(
            self.inp, self.tmp, self.job.mode, post_title, self.src_dur, cuts=self.cover_cuts, end=self.cover_end,
            bands=self.bands, thumbs_dir=a["dir"] if a else None, thumb_times=a.get("times") if a else None,
            at=self.cover_at, **({"series": s} if (s := brand.series_label(self.src_hash)) else {})))
        self.tasks.append(t)
        return t

    async def finish_cover(self, t: asyncio.Task | None):
        if t is not None and not t.done():
            self.job.step = "Tạo ảnh bìa"
        if t is not None:
            await t

    async def synth(self, segs_t: list, items: list, tmp_dir: str) -> dict:
        """Đọc giọng trên Botkeep: tối đa 3 đoạn song song, đoạn không đổi lấy từ cache; một đoạn lỗi thì huỷ cả lô."""
        sem = asyncio.Semaphore(3)

        async def one(k: int, text: str):
            p = os.path.join(tmp_dir, f"clip_{segs_t[k]['i']}_{cache.key('clip', text)[:10]}.mp3")
            async with sem:
                w = await tts_engine.synth_clip_cached(text, self.job.voice, p)
            return k, {"path": p, "words": w}
        return dict(await common.gather_all([one(k, t) for k, t in items]))

    async def shorten(self, segs_t: list, idx: list, durs: list) -> list:
        """AI rút gọn các đoạn idx (nguyên câu, không mất nguyên liệu/con số). Trả chỉ số đoạn đã đổi."""
        out_dur = self.out_dur
        items = []
        for i in idx:
            room = (segs_t[i + 1]["start"] if i + 1 < len(segs_t) else out_dur) - segs_t[i]["start"]
            n = len(segs_t[i]["text"].split())
            items.append((i, segs_t[i]["text"], max(3, int(n * room / max(durs[i], 0.1) * 0.95))))
        changed = []
        for i, text in (await prompt_engine.shorten(items)).items():
            if 0 <= i < len(segs_t) and len(text.split()) < len(segs_t[i]["text"].split()) \
                    and ingredients.find(text) >= ingredients.find(segs_t[i]["text"]) \
                    and not ingredients.check_numbers(segs_t[i]["text"], text)[0]:   # không được mất nguyên liệu/số
                segs_t[i]["text"] = text
                changed.append(i)
        if changed:
            self.job.script = spoken_script(segs_t)
        return changed
