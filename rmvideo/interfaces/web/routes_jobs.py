"""Job: danh sách, luồng sự kiện SSE, timeline/phụ đề, tạo job (link / tải file lên), huỷ, duyệt kịch bản, tải kết quả, xem định dạng YouTube."""
import asyncio
import hmac
import json
import os
import re
import secrets
import time
from typing import Any

from aiohttp import web

import config
import slideshow
from queue_manager import JobQueue, QueueFull, phase_times
from rmvideo.app import events, job_factory
from rmvideo.interfaces.web.auth import _check
from rmvideo.interfaces.web.http import (
    _STEP_LABEL,
    FIELD_MAX,
    IMAGE_MAX,
    PHOTO_DURATIONS,
    SSE_MAX,
    _cleanup,
    _file_response,
    _guard,
    _int_id,
    _json_dict,
    _multipart,
    _parse_duration,
    _pick_voice,
    _read_capped,
)



def _recipe_view(j) -> dict:
    """Bảng định lượng đã đối chiếu + tóm tắt kế hoạch dựng cho màn duyệt (rỗng nếu job không có)."""
    from rmvideo.domain import recipe
    f = recipe.Facts.from_dict(getattr(j, "recipe", None) or {})
    out = {"report": recipe.report(f), "table": recipe.table(f)} if f else {}
    plan = (getattr(j, "edit", None) or {}).get("summary")
    if plan:
        out["plan"] = plan
    return out

def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    def jobs_payload() -> dict:
        jobs = sorted(queue.jobs.values(), key=lambda j: -j.id)
        return {
            "pending": queue.pending,
            "password_set": bool(config.WEB_PASSWORD),
            "jobs": [{
                "id": j.id, "url": j.url or "📎 " + (j.title_hint or "file tải lên"), "mode": j.mode, "duration": j.duration,
                "source": j.source, "status": j.status,
                "status_label": _STEP_LABEL.get(j.status, j.status),
                "step": j.step, "error": j.error[:300], "script": j.script[:300],
                "post": j.post,
                "downloadable": bool(j.output and os.path.exists(j.output)),
                "dl": f"/download/{j.id}?t={j.dl_token}",
                "parts": [{"label": p["label"], "dl": f"/download/{j.id}/{k}?t={p['token']}"}
                          for k, p in enumerate(j.parts) if os.path.exists(p["path"])],
                "long_mode": j.long_mode,
                "elapsed": int((j.finished_at or time.time()) - j.started_at) if j.started_at else 0,
                "eta": (max(0, int(j.out_dur * hooks.get("speed", lambda m: 18.0)(j.long_mode)
                                   - (time.time() - j.started_at))) if (j.status == "processing" and j.out_dur) else None),
                "approved": j.approved if j.status == "review" else [],
                "recipe": _recipe_view(j),
                "timeline": f"/api/jobs/{j.id}/timeline.json" if j.final_segments else "",
                "srt": f"/api/jobs/{j.id}/subs.srt" if j.srt else "",
                "age": int(time.time() - j.created),
                "phases": phase_times(j.timeline, ((j.finished_at or time.time()) - j.started_at) if j.started_at else 0),
            } for j in jobs],
        }

    async def list_jobs(request):
        _check(request)
        return web.json_response(jobs_payload())

    sse = {"clients": 0}

    cache: dict[str, Any] = {"v": None, "t": 0.0, "data": None}

    def payload_cached() -> tuple:
        """Danh sách job dựng MỘT lần cho mọi khách SSE: khi có job đổi (bus sự kiện) hoặc đã cũ quá 1 giây (thời
        gian chạy / ETA). Trước đây mỗi khách tự dựng lại mỗi giây (tối đa 8 khách x stat mọi file kết quả)."""
        v, now = events.version(), time.time()
        if cache["v"] != v or now - cache["t"] >= 1.0:
            cache.update(v=v, t=now, data=jobs_payload())
        return v, cache["data"]

    async def events_stream(request):
        """Server-Sent Events: đẩy danh sách job khi có thay đổi (chờ bus sự kiện, không hỏi lại theo giờ). Gửi khi
        nội dung đổi, hoặc 10 giây một lần để cập nhật thời gian chạy; tối đa SSE_MAX kết nối."""
        _check(request)
        if sse["clients"] >= SSE_MAX:
            raise web.HTTPTooManyRequests(text="Quá nhiều kết nối theo dõi")
        sse["clients"] += 1                       # giữ chỗ TRƯỚC khi await: không vượt SSE_MAX
        last_key, last_sent = None, 0.0
        try:
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                               "X-Accel-Buffering": "no"})
            await resp.prepare(request)
            while True:
                try:
                    _check(request)              # đăng xuất / đổi mật khẩu thì luồng cũ cũng dừng
                except web.HTTPException:
                    break
                v, data = payload_cached()
                key = json.dumps([{k: val for k, val in j.items() if k not in ("elapsed", "eta", "age", "phases")}
                                  for j in data["jobs"]] + [data["pending"]], ensure_ascii=False)
                now = time.time()
                if key != last_key or now - last_sent >= 10:
                    # client ngừng đọc thì write treo: tối đa 10s rồi đóng, trả chỗ cho người khác
                    await asyncio.wait_for(resp.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()), 10)
                    last_key, last_sent = key, now
                await events.wait_change(v, max(0.5, 10 - (time.time() - last_sent)))
        except (ConnectionResetError, ConnectionError, RuntimeError, asyncio.TimeoutError):
            pass
        finally:
            sse["clients"] -= 1
        return resp

    def _job_or_404(request):
        try:
            job = queue.jobs.get(_int_id(request))
        except ValueError:
            raise web.HTTPNotFound() from None
        if not job:
            raise web.HTTPNotFound()
        return job

    async def timeline_json(request):
        _check(request)
        job = _job_or_404(request)
        if not job.final_segments:
            raise web.HTTPNotFound(text="Job này chưa có timeline")
        body = {"job": job.id, "source": (job.source_meta or {}).get("webpage_url") or job.url, "duration": job.out_dur,
                "cut_promo_at": job.cut_at or None, "segments": job.final_segments}
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1), headers={
            "Content-Disposition": f'attachment; filename="timeline_{job.id}.json"'})

    async def subs_srt(request):
        _check(request)
        job = _job_or_404(request)
        if not job.srt:
            raise web.HTTPNotFound(text="Job này chưa có phụ đề")
        return web.Response(text=job.srt, content_type="application/x-subrip", headers={
            "Content-Disposition": f'attachment; filename="video_{job.id}.srt"'})

    async def add_job(request):
        _check(request)
        d = await _json_dict(request)
        urls = [u.strip() for u in re.split(r"[\s,]+", (d.get("url") or "").strip()) if u.strip()]
        if not urls:
            raise web.HTTPBadRequest(text="Link không hợp lệ")
        urls = [await _guard(u) for u in urls[:10]]
        if d.get("mode") == "photo":
            raise web.HTTPBadRequest(text="Chế độ 📸 cần tải ảnh lên (dùng ô chọn ảnh)")
        mode = job_factory.video_mode(d.get("mode"))
        dur, long_mode = _parse_duration(d.get("duration"))
        voice = _pick_voice(d.get("voice") or "")
        subs = bool(d.get("subs", True))
        music_url = (d.get("music_url") or "").strip()
        if mode == "music":
            if not music_url:
                raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc mới (hoặc upload file nhạc)")
            music_url = await _guard(music_url)
        ids: list[int] = []
        pos = 0
        for u in urls:
            job = job_factory.remake(url=u, mode=mode, duration=dur, voice=voice, subs=subs, long_mode=long_mode,
                                     cover=bool(d.get("cover", True)), review=bool(d.get("review", False)),
                                     quality=str(d.get("quality", "")), music_url=music_url, source="web",
                                     music_mix=str(d.get("music_mix") or ""))
            try:
                pos = queue.put(job)
            except QueueFull as e:
                if ids:
                    break
                raise web.HTTPTooManyRequests(text=str(e)) from None
            ids.append(job.id)
        return web.json_response({"id": ids[0], "ids": ids, "position": pos})

    async def upload(request):
        _check(request)
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
        reader = await _multipart(request)
        fields: dict[str, str] = {}
        saved, music_saved = "", ""
        images: list[str] = []

        async def _save_part(part, exts, kind, limit=None):
            name = (part.filename or "")
            if exts and not name.lower().endswith(exts):
                raise web.HTTPBadRequest(text=f"File {kind} không đúng định dạng")
            path = os.path.join(config.UPLOAD_DIR, f"up_{secrets.token_hex(16)}")
            size = 0
            try:
                with open(path, "wb") as f:
                    while chunk := await part.read_chunk(1 << 20):
                        size += len(chunk)
                        if size > (limit or config.MAX_UPLOAD):
                            raise web.HTTPRequestEntityTooLarge(max_size=limit or config.MAX_UPLOAD, actual_size=size)
                        f.write(chunk)
            except BaseException:          # quá cỡ, đầy đĩa, người dùng ngắt, bị huỷ: không để lại file dở tới 300MB
                _cleanup(path)
                raise
            return path, name

        try:
            async for part in reader:
                if part.name == "file" and part.filename:
                    if saved:
                        raise web.HTTPBadRequest(text="Mỗi lần chỉ gửi một file video")
                    saved, fields["filename"] = await _save_part(
                        part, (".mp4", ".mov", ".webm", ".mkv", ".m4v"), "video")
                elif part.name == "music" and part.filename:
                    if music_saved:
                        raise web.HTTPBadRequest(text="Mỗi lần chỉ gửi một file nhạc")
                    music_saved, _ = await _save_part(
                        part, (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"), "nhạc", 30 * 1024 * 1024)
                elif part.name == "images" and part.filename:
                    if len(images) >= slideshow.MAX_IMAGES:
                        raise web.HTTPBadRequest(text=f"Tối đa {slideshow.MAX_IMAGES} ảnh")
                    images.append((await _save_part(part, (".jpg", ".jpeg", ".png", ".webp", ".bmp"), "ảnh",
                                                    IMAGE_MAX))[0])
                elif part.name:
                    raw = await _read_capped(part, FIELD_MAX, f"Trường «{part.name}» quá dài")
                    fields[part.name] = raw.decode("utf-8", errors="replace").strip()
            if fields.get("mode") == "photo":
                if not images:
                    raise web.HTTPBadRequest(text="Chế độ 📸 cần 1-4 ảnh")
                music_url = fields.get("music_url", "").strip()
                if not music_saved:
                    if not music_url:
                        raise web.HTTPBadRequest(text="Chế độ 📸 cần nhạc: link TikTok/YouTube hoặc file nhạc")
                    music_url = await _guard(music_url)
                pdur = fields.get("duration", "0")
                job = job_factory.photo(source="web", duration=int(pdur) if pdur in PHOTO_DURATIONS else 0,
                                        images=images, music_local=music_saved, music_url=music_url,
                                        title_hint=fields.get("title", ""))
                pos = queue.put(job)
                _cleanup(saved)                              # video gửi kèm không dùng ở chế độ ảnh
                return web.json_response({"id": job.id, "position": pos})
            url_field = fields.get("url", "").strip()
            if not saved:
                if not url_field:
                    raise web.HTTPBadRequest(text="Thiếu file video hoặc link video")
                url_field = await _guard(url_field.split()[0])
            mode = job_factory.video_mode(fields.get("mode"))
            dur, long_mode = _parse_duration(fields.get("duration"))
            voice = _pick_voice(fields.get("voice", ""))
            music_url = fields.get("music_url", "").strip()
            if mode == "music" and not music_saved:
                if not music_url:
                    raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc hoặc file nhạc")
                music_url = await _guard(music_url)
            job = job_factory.remake(url="" if saved else url_field, mode=mode, duration=dur, voice=voice,
                                     long_mode=long_mode, subs=fields.get("subs") != "false",
                                     cover=fields.get("cover") != "false", local=saved,
                                     review=fields.get("review") == "true", music_local=music_saved,
                                     music_url=music_url if mode == "music" else "",
                                     music_mix=fields.get("music_mix", ""), title_hint=fields.get("filename", ""),
                                     source="web")
            pos = queue.put(job)
            _cleanup(*images)                                # ảnh gửi kèm không dùng ở chế độ video
        except QueueFull as e:
            _cleanup(saved, music_saved, *images)
            raise web.HTTPTooManyRequests(text=str(e)) from None
        except BaseException:
            _cleanup(saved, music_saved, *images)
            raise
        return web.json_response({"id": job.id, "position": pos})

    async def download(request):
        # Mã ngẫu nhiên riêng từng video thay cho mật khẩu: link tải nằm trong lịch sử
        # trình duyệt và access log, nên không được chứa mật khẩu.
        try:
            job = queue.jobs.get(_int_id(request))
        except ValueError:
            raise web.HTTPNotFound() from None
        if not job or not hmac.compare_digest(request.query.get("t", "").encode(), job.dl_token.encode()):
            raise web.HTTPNotFound()
        if not job.output or not os.path.exists(job.output):
            raise web.HTTPNotFound(text="Video không còn (chỉ giữ vài video gần nhất)")
        return _file_response(job.output, f"video_{job.id}{os.path.splitext(job.output)[1] or '.mp4'}")

    async def download_part(request):
        try:
            job = queue.jobs.get(_int_id(request))
            k = int(request.match_info["k"])
        except ValueError:
            raise web.HTTPNotFound() from None
        if not job or not (0 <= k < len(job.parts)):
            raise web.HTTPNotFound()
        part = job.parts[k]
        if not hmac.compare_digest(request.query.get("t", "").encode(), part["token"].encode()) \
                or not os.path.exists(part["path"]):
            raise web.HTTPNotFound(text="Video không còn (server chỉ giữ ~700MB video gần nhất)")
        ext = os.path.splitext(part["path"])[1] or ".mp4"
        name = f"anh_bia_{job.id}_{k + 1}{ext}" if ext == ".jpg" else f"video_{job.id}_phan{k + 1}{ext}"
        return _file_response(part["path"], name)

    async def yt_info(request):
        _check(request)
        url = await _guard((request.query.get("url") or "").strip())
        import tempfile

        from rmvideo.media import fetch
        try:
            with tempfile.TemporaryDirectory() as td:
                d = await fetch.probe_formats(url, td)
            return web.json_response(d)
        except Exception as e:
            return web.json_response({"error": str(e)})

    async def cancel(request):
        _check(request)
        fn = hooks.get("cancel")
        return web.json_response({"msg": fn(_int_id(request)) if fn else "không hỗ trợ"})

    async def approve(request):
        _check(request)
        d = await _json_dict(request)            # đọc xong body rồi mới kiểm trạng thái (không có await xen giữa)
        job = queue.jobs.get(_int_id(request))
        if not job or job.status != "review":
            raise web.HTTPBadRequest(text="Job không ở trạng thái chờ duyệt")
        # chỉ đổi cờ tiếng gốc khi client gửi lên (script/trang cũ không gửi thì giữ nguyên cờ AI đã đặt)
        try:
            edits = {int(x["i"]): {"text": str(x.get("text", "")), **({"ost": str(x["ost"])} if "ost" in x else {})}
                     for x in d.get("segments", []) if "i" in x}
        except (TypeError, ValueError, AttributeError):
            raise web.HTTPBadRequest(text="segments không hợp lệ") from None
        try:
            new = hooks["approve"](job, edits)
        except QueueFull as e:
            raise web.HTTPTooManyRequests(text=f"{e} Kịch bản vẫn được giữ, bấm Render lại sau.") from None
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response({"id": new.id})

    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_get("/api/events", events_stream)
    app.router.add_get("/api/jobs/{id}/timeline.json", timeline_json)
    app.router.add_get("/api/jobs/{id}/subs.srt", subs_srt)
    app.router.add_post("/api/jobs", add_job)
    app.router.add_get("/download/{id}", download)
    app.router.add_get("/download/{id}/{k}", download_part)
    app.router.add_get("/api/yt/info", yt_info)
    app.router.add_post("/api/jobs/{id}/cancel", cancel)
    app.router.add_post("/api/jobs/{id}/approve", approve)
    app.router.add_post("/api/upload", upload)
