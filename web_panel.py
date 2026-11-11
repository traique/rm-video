"""Giao diện web điều khiển bot, chạy chung tiến trình (aiohttp có sẵn từ aiogram).
Botkeep cấp link web -> trỏ thẳng vào đây.

Bảo mật: không đặt WEB_PASSWORD thì mọi /api bị khoá. Đăng nhập một lần -> cookie phiên ký HMAC
(HttpOnly, SameSite=Strict, sống qua restart, đổi mật khẩu là mọi phiên cũ hết hiệu lực). POST kiểm
Origin chống CSRF; sai mật khẩu nhiều lần bị khoá tạm. Link tải dùng mã ngẫu nhiên riêng từng file."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web

import config
import slideshow
import url_guard
from queue_manager import Job, JobQueue, QueueFull, phase_times

_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(_DIR, "static", "index.html")

_STEP_LABEL = {"queued": "Đang chờ", "processing": "Đang xử lý", "done": "Xong", "error": "Lỗi",
               "review": "📝 Chờ duyệt", "cancelled": "Đã hủy", "approved": "Đã duyệt"}


IMAGE_MAX = 15 * 1024 * 1024
PHOTO_DURATIONS = ("0", "10", "15", "30", "60")     # 0 = tự động theo nhạc


def _parse_duration(v) -> tuple:
    """'30'/'60'/'90'/'0' -> (giây, ""); 'full'/'split60'/'split90'/'highlight' -> (0, chế độ video dài)."""
    v = str(v or "0").strip()
    if v in ("full", "split60", "split90", "highlight"):
        return 0, v
    try:
        d = int(v)
    except ValueError:
        return 0, ""
    return (d if d in (0, 30, 60, 90) else 0), ""


def _pick_voice(v: str) -> str:
    if v == "male":
        return config.CAPCUT_VOICE_MALE if config.TTS_PROVIDER == "capcut" else config.VOICE_MALE
    if not v or v == "female":
        return config.CAPCUT_VOICE_FEMALE if config.TTS_PROVIDER == "capcut" else config.VOICE_FEMALE
    return v[:80]   # id giọng cụ thể do người dùng chọn trong dropdown


# ---------------- Phiên đăng nhập ----------------
SSE_MAX = 8
SESSION_COOKIE = "vb_session"
SESSION_TTL = 30 * 86400
_KEY_FILE = os.path.join(_DIR, ".session_key")
FAIL_WINDOW, FAIL_PER_IP, FAIL_GLOBAL = 60.0, 5, 30   # 5 lần sai/phút mỗi IP, 30 lần/phút toàn bộ
_fails: dict[str, list[float]] = {}


def _server_salt() -> bytes:
    """Muối ngẫu nhiên lưu cạnh bot (quyền 600): cookie không giả mạo được kể cả khi biết mã nguồn."""
    try:
        with open(_KEY_FILE, "rb") as f:
            salt = f.read()
        if len(salt) >= 32:
            return salt
    except OSError:
        pass
    salt = secrets.token_bytes(32)
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(salt)
    return salt


def _session_key() -> bytes:
    return hashlib.sha256(b"vb-session|" + _server_salt() + b"|" + config.WEB_PASSWORD.encode()).digest()


def make_session(now: float | None = None) -> str:
    exp = str(int((now or time.time()) + SESSION_TTL))
    sig = hmac.new(_session_key(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_session(token: str, now: float | None = None) -> bool:
    try:
        exp, sig = (token or "").split(".", 1)
        if int(exp) < (now or time.time()):
            return False
    except ValueError:
        return False
    good = hmac.new(_session_key(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.encode(), good.encode())     # so bytes: chuỗi có dấu không làm lỗi 500


def _password_ok(pw: str) -> bool:
    return bool(config.WEB_PASSWORD) and hmac.compare_digest(pw.encode(), config.WEB_PASSWORD.encode())


def _client_key(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return fwd or request.remote or "?"


def _recent(key: str, now: float) -> list:
    lst = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    _fails[key] = lst
    return lst


def login_locked(key: str, now: float | None = None) -> int:
    """Số giây còn bị khoá (0 = được thử). Khoá theo IP và cả toàn cục (IP giả qua X-Forwarded-For)."""
    now = now or time.time()
    for k, cap in ((key, FAIL_PER_IP), ("*", FAIL_GLOBAL)):
        lst = _recent(k, now)
        if len(lst) >= cap:
            return int(FAIL_WINDOW - (now - lst[0])) + 1
    return 0


def record_fail(key: str, now: float | None = None):
    now = now or time.time()
    for k in (key, "*"):
        _recent(k, now).append(now)


def _allowed_hosts(request: web.Request) -> set:
    hosts = {request.host}
    if request.headers.get("X-Forwarded-Host"):
        hosts.add(request.headers["X-Forwarded-Host"].split(",")[0].strip())
    if config.PUBLIC_URL:
        hosts.add(urlsplit(config.PUBLIC_URL).netloc)
    return {h.lower() for h in hosts if h}


def _check_origin(request: web.Request):
    """Chống CSRF cho request đổi trạng thái: Origin (nếu trình duyệt gửi) phải là chính trang này."""
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return                      # cookie SameSite=Strict đã không đi kèm request từ trang khác
    host = urlsplit(origin).netloc.lower()
    if host not in _allowed_hosts(request):
        logging.warning("Chặn request khác nguồn: Origin=%s Host=%s", origin, request.host)
        raise web.HTTPForbidden(text="Request từ trang khác bị chặn (CSRF). Nếu proxy đổi tên miền, đặt biến "
                                     "môi trường PUBLIC_URL bằng link web của bạn rồi khởi động lại bot.")


def _authed(request: web.Request) -> bool:
    """Chỉ xét cookie phiên (dùng cho /api/me công khai: không được thành chỗ dò mật khẩu)."""
    return bool(config.WEB_PASSWORD) and valid_session(request.cookies.get(SESSION_COOKIE, ""))


def _check(request: web.Request):
    if not config.WEB_PASSWORD:
        raise web.HTTPForbidden(text="Web panel đang khoá vì chưa đặt WEB_PASSWORD. "
                                     "Đặt biến môi trường WEB_PASSWORD rồi khởi động lại bot.")
    hdr = request.headers.get("X-Auth")
    if hdr is not None:
        # cho script/curl: header tuỳ biến không gửi được từ trang khác (khỏi kiểm Origin), nhưng vẫn chịu
        # cùng giới hạn thử mật khẩu như trang đăng nhập
        key = _client_key(request)
        wait = login_locked(key)
        if wait:
            raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
        if not _password_ok(hdr):
            record_fail(key)
            raise web.HTTPUnauthorized(text="Sai mật khẩu")
        return
    if request.method not in ("GET", "HEAD"):
        _check_origin(request)
    if not _authed(request):
        raise web.HTTPUnauthorized(text="Cần đăng nhập")


def _is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


_ONOFF = [["1", "Bật"], ["0", "Tắt"]]
_SETTING_OPTIONS = {
    "TTS_PROVIDER": [["capcut", "CapCut (tự nhiên, khuyên dùng)"], ["edge", "Edge TTS (dự phòng)"]],
    "SPEECH_STYLE": [["mien_tay", "Miền Tây (tui, bà con, nè, nghen)"], ["mien_nam", "Miền Nam"], ["pho_thong", "Phổ thông"]],
    "SUB_STYLE": [["karaoke", "Karaoke (sáng dần theo giọng)"], ["box", "Hộp nền đen"], ["outline", "Chữ trắng viền đen"], ["plain", "Vàng gọn"]],
    "QUALITY": [["fast", "Nhanh (superfast, crf 23)"], ["balanced", "Cân bằng (mặc định)"], ["high", "Chuẩn TVC (chậm ~2x)"]],
    "OPENROUTER_MODEL": [["auto", "auto (tự dò model miễn phí)"]],
    "VOICE_SEPARATION": [["1", "Bật (tách giọng AI các đoạn có lời, tối đa 45s lời)"], ["0", "Tắt"]],
    "VOICE_HUMANIZE": _ONOFF, "BRAND_LOGO": _ONOFF, "BRAND_INTRO": _ONOFF,
    "COVER": [["1", "Bật (khung đẹp nhất + tiêu đề; AI chọn ảnh nếu có key Gemini)"], ["0", "Tắt"]],
    "REVIEW_SCRIPT": [["0", "Tắt"], ["1", "Luôn dừng cho duyệt kịch bản"]],
    "TEXT_DETECT": [["heuristic", "Heuristic (mặc định, không cần cài)"], ["ocr", "Model OCR (bắt chữ màu, cần setup_offline)"]],
    "TTS_OFFLINE": [["0", "Tắt"], ["1", "Bật giọng Piper khi CapCut + edge đều lỗi (xem lưu ý giấy phép)"]],
    "OST_AUTO": [["0", "Tắt (chỉ khi bạn chọn trong màn duyệt)"], ["1", "AI được đề xuất đoạn giữ tiếng gốc"]],
    "ASR_ORDER": [["groq,gemini,capcut", "Groq → Gemini → CapCut"], ["gemini,groq,capcut", "Gemini → Groq → CapCut"],
                  ["gemini,capcut", "Gemini → CapCut"], ["groq,capcut", "Groq → CapCut"]],
    "SUB_TIMING": [["spread", "Theo khoảng có tiếng của giọng (mặc định, lệch ~70ms, không gọi mạng)"],
                   ["stt", "Nghe lại bằng STT CapCut (lệch ~85ms, chậm thêm 7-11 giây)"]],
    "SOURCE_CREDIT": [["1", "Bật (ghi nguồn + Giọng đọc AI)"], ["0", "Tắt"]],
    "CUT_PROMO": [["1", "Bật (cắt quảng cáo sản phẩm của người gốc ở cuối)"], ["0", "Tắt"]],
}


def _voice_options() -> list:
    try:
        import json as _json
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capcut_tts_api", "Voice.json")
        seen, out = set(), []
        for x in _json.load(open(p, encoding="utf-8")):
            if x.get("lan") == "vi" and x["voice_type"] not in seen and "Neural" not in x["voice_type"]:
                seen.add(x["voice_type"])
                out.append([x["voice_type"], x["display_name"].strip()])
        return out
    except Exception:
        return []


def _with_options(d: dict) -> dict:
    """Thêm 'options' cho khóa có giá trị cố định để web hiện ô xổ xuống."""
    voices = _voice_options()
    for k, v in d.items():
        if k in _SETTING_OPTIONS:
            v["options"] = _SETTING_OPTIONS[k]
        elif k in ("CAPCUT_VOICE_FEMALE", "CAPCUT_VOICE_MALE") and voices:
            v["options"] = voices
    return d


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)


def build_app(queue: JobQueue, hooks: dict | None = None) -> web.Application:
    hooks = hooks or {}

    async def index(_):
        return web.FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})

    async def health(_):
        # công khai cho uptime-check nên không lộ gì ngoài "còn sống" và số job chờ
        return web.json_response({"ok": True, "pending": queue.pending})

    async def me(request):
        return web.json_response({"password_set": bool(config.WEB_PASSWORD), "authed": _authed(request)})

    async def login(request):
        if not config.WEB_PASSWORD:
            raise web.HTTPForbidden(text="Chưa đặt WEB_PASSWORD: đặt biến môi trường rồi khởi động lại bot.")
        _check_origin(request)
        key = _client_key(request)
        wait = login_locked(key)
        if wait:
            raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
        try:
            pw = str((await request.json()).get("password", ""))
        except Exception:
            pw = ""
        if not _password_ok(pw):
            record_fail(key)
            await asyncio.sleep(0.5)                    # làm chậm dò mật khẩu
            raise web.HTTPUnauthorized(text="Sai mật khẩu")
        resp = web.json_response({"ok": True})
        resp.set_cookie(SESSION_COOKIE, make_session(), max_age=SESSION_TTL, httponly=True,
                        samesite="Strict", secure=_is_https(request), path="/")
        return resp

    async def logout(request):
        _check_origin(request)
        resp = web.json_response({"ok": True})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

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

    async def events(request):
        """Server-Sent Events: đẩy danh sách job khi có thay đổi (thay cho trình duyệt hỏi lại mỗi vài giây).
        Gửi khi nội dung đổi, hoặc 10 giây một lần để cập nhật thời gian chạy; tối đa SSE_MAX kết nối."""
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
                data = jobs_payload()
                key = json.dumps([{k: v for k, v in j.items() if k not in ("elapsed", "eta", "age", "phases")}
                                  for j in data["jobs"]] + [data["pending"]], ensure_ascii=False)
                now = time.time()
                if key != last_key or now - last_sent >= 10:
                    # client ngừng đọc thì write treo: tối đa 10s rồi đóng, trả chỗ cho người khác
                    await asyncio.wait_for(resp.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()), 10)
                    last_key, last_sent = key, now
                await asyncio.sleep(1)
        except (ConnectionResetError, ConnectionError, RuntimeError, asyncio.TimeoutError):
            pass
        finally:
            sse["clients"] -= 1
        return resp

    def _job_or_404(request):
        try:
            job = queue.jobs.get(int(request.match_info["id"]))
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

    async def _guard(url: str) -> str:
        try:
            return await url_guard.check_url_async(url)
        except url_guard.BlockedURL as e:
            raise web.HTTPBadRequest(text=str(e)) from None

    async def add_job(request):
        _check(request)
        d = await request.json()
        urls = [u.strip() for u in re.split(r"[\s,]+", (d.get("url") or "").strip()) if u.strip()]
        if not urls:
            raise web.HTTPBadRequest(text="Link không hợp lệ")
        urls = [await _guard(u) for u in urls[:10]]
        if d.get("mode") == "photo":
            raise web.HTTPBadRequest(text="Chế độ 📸 cần tải ảnh lên (dùng ô chọn ảnh)")
        mode = d.get("mode") if d.get("mode") in ("cook", "review", "music", "download") else "cook"
        dur, long_mode = _parse_duration(d.get("duration"))
        voice = _pick_voice(d.get("voice") or "")
        subs = bool(d.get("subs", True))
        music_url = (d.get("music_url") or "").strip()
        if mode == "music":
            if not music_url:
                raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc mới (hoặc upload file nhạc)")
            music_url = await _guard(music_url)
        ids, pos = [], 0
        for u in urls:
            job = Job(url=u, mode=mode, duration=dur, voice=voice, subs=subs, long_mode=long_mode,
                      cover=bool(d.get("cover", True)), review=bool(d.get("review", False)),
                      quality=str(d.get("quality", ""))[:8], music_url=music_url, source="web",
                      music_mix="bg" if (mode == "music" and d.get("music_mix") == "bg") else "")
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
        reader = await request.multipart()
        fields, saved, music_saved, images = {}, "", "", []

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
                    saved, fields["filename"] = await _save_part(
                        part, (".mp4", ".mov", ".webm", ".mkv", ".m4v"), "video")
                elif part.name == "music" and part.filename:
                    music_saved, _ = await _save_part(
                        part, (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"), "nhạc", 30 * 1024 * 1024)
                elif part.name == "images" and part.filename:
                    if len(images) >= slideshow.MAX_IMAGES:
                        raise web.HTTPBadRequest(text=f"Tối đa {slideshow.MAX_IMAGES} ảnh")
                    images.append((await _save_part(part, (".jpg", ".jpeg", ".png", ".webp", ".bmp"), "ảnh",
                                                    IMAGE_MAX))[0])
                elif part.name:
                    fields[part.name] = (await part.text())[:4000].strip()
            if fields.get("mode") == "photo":
                if not images:
                    raise web.HTTPBadRequest(text="Chế độ 📸 cần 1-4 ảnh")
                music_url = fields.get("music_url", "").strip()
                if not music_saved:
                    if not music_url:
                        raise web.HTTPBadRequest(text="Chế độ 📸 cần nhạc: link TikTok/YouTube hoặc file nhạc")
                    music_url = await _guard(music_url)
                pdur = fields.get("duration", "0")
                job = Job(url="", mode="photo", duration=int(pdur) if pdur in PHOTO_DURATIONS else 0, voice="",
                          images=[p for p in images], music_local=music_saved, music_url=music_url,
                          title_hint=fields.get("title", "")[:200], source="web")
                pos = queue.put(job)
                return web.json_response({"id": job.id, "position": pos})
            url_field = fields.get("url", "").strip()
            if not saved:
                if not url_field:
                    raise web.HTTPBadRequest(text="Thiếu file video hoặc link video")
                url_field = await _guard(url_field.split()[0])
            mode = fields.get("mode") if fields.get("mode") in ("cook", "review", "music", "download") else "cook"
            dur, long_mode = _parse_duration(fields.get("duration"))
            voice = _pick_voice(fields.get("voice", ""))
            music_url = fields.get("music_url", "").strip()
            if mode == "music" and not music_saved:
                if not music_url:
                    raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc hoặc file nhạc")
                music_url = await _guard(music_url)
            job = Job(url="" if saved else url_field, mode=mode, duration=dur, voice=voice, long_mode=long_mode,
                      subs=fields.get("subs") != "false", cover=fields.get("cover") != "false", local=saved,
                      review=fields.get("review") == "true",
                      music_local=music_saved, music_url=music_url if mode == "music" else "",
                      music_mix="bg" if (mode == "music" and fields.get("music_mix") == "bg") else "",
                      title_hint=fields.get("filename", ""), source="web")
            pos = queue.put(job)
        except QueueFull as e:
            _cleanup(saved, music_saved, *images)
            raise web.HTTPTooManyRequests(text=str(e)) from None
        except BaseException:
            _cleanup(saved, music_saved, *images)
            raise
        return web.json_response({"id": job.id, "position": pos})

    async def list_voices(request):
        _check(request)
        voices = [{"id": "female", "name": "Nữ mặc định (Cài đặt)"}, {"id": "male", "name": "Nam mặc định (Cài đặt)"}]
        if config.TTS_PROVIDER == "capcut":
            import tts_capcut
            voices += [{"id": v["id"], "name": "CapCut · " + v["name"]} for v in tts_capcut.list_voices()]
        voices += [{"id": config.VOICE_FEMALE, "name": "Edge · Hoài My"}, {"id": config.VOICE_MALE, "name": "Edge · Nam Minh"}]
        return web.json_response(voices)

    async def get_settings(request):
        _check(request)
        return web.json_response(_with_options(config.describe_settings()))

    async def set_settings(request):
        _check(request)
        try:
            config.save_settings(await request.json())
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response(_with_options(config.describe_settings()))

    def _file_response(path: str, name: str):
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def download(request):
        # Mã ngẫu nhiên riêng từng video thay cho mật khẩu: link tải nằm trong lịch sử
        # trình duyệt và access log, nên không được chứa mật khẩu.
        try:
            job = queue.jobs.get(int(request.match_info["id"]))
        except ValueError:
            raise web.HTTPNotFound() from None
        if not job or not hmac.compare_digest(request.query.get("t", "").encode(), job.dl_token.encode()):
            raise web.HTTPNotFound()
        if not job.output or not os.path.exists(job.output):
            raise web.HTTPNotFound(text="Video không còn (chỉ giữ vài video gần nhất)")
        return _file_response(job.output, f"video_{job.id}{os.path.splitext(job.output)[1] or '.mp4'}")

    async def download_part(request):
        try:
            job = queue.jobs.get(int(request.match_info["id"]))
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
        import core_video
        try:
            with tempfile.TemporaryDirectory() as td:
                d = await core_video.probe_formats(url, td)
            return web.json_response(d)
        except Exception as e:
            return web.json_response({"error": str(e)})

    async def cancel(request):
        _check(request)
        fn = hooks.get("cancel")
        return web.json_response({"msg": fn(int(request.match_info["id"])) if fn else "không hỗ trợ"})

    async def approve(request):
        _check(request)
        d = await request.json()                 # đọc xong body rồi mới kiểm trạng thái (không có await xen giữa)
        job = queue.jobs.get(int(request.match_info["id"]))
        if not job or job.status != "review":
            raise web.HTTPBadRequest(text="Job không ở trạng thái chờ duyệt")
        # chỉ đổi cờ tiếng gốc khi client gửi lên (script/trang cũ không gửi thì giữ nguyên cờ AI đã đặt)
        edits = {int(x["i"]): {"text": str(x.get("text", "")), **({"ost": str(x["ost"])} if "ost" in x else {})}
                 for x in d.get("segments", []) if "i" in x}
        try:
            new = hooks["approve"](job, edits)
        except QueueFull as e:
            raise web.HTTPTooManyRequests(text=f"{e} Kịch bản vẫn được giữ, bấm Render lại sau.") from None
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response({"id": new.id})

    async def upload_logo(request):
        _check(request)
        reader = await request.multipart()
        part = await reader.next()
        if not part or not (part.filename or "").lower().endswith(".png"):
            raise web.HTTPBadRequest(text="Logo phải là file .png (nền trong suốt)")
        data = await part.read()
        if len(data) > 2 * 1024 * 1024:
            raise web.HTTPBadRequest(text="Logo tối đa 2MB")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise web.HTTPBadRequest(text="File không phải PNG thật")
        import core_video
        with open(core_video.LOGO_PATH, "wb") as f:
            f.write(data)
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=config.MAX_UPLOAD + 5 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/jobs/{id}/timeline.json", timeline_json)
    app.router.add_get("/api/jobs/{id}/subs.srt", subs_srt)
    app.router.add_post("/api/jobs", add_job)
    app.router.add_get("/download/{id}", download)
    app.router.add_get("/download/{id}/{k}", download_part)
    app.router.add_get("/api/yt/info", yt_info)
    app.router.add_post("/api/jobs/{id}/cancel", cancel)
    app.router.add_post("/api/jobs/{id}/approve", approve)
    app.router.add_post("/api/logo", upload_logo)
    app.router.add_post("/api/upload", upload)
    app.router.add_get("/api/voices", list_voices)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", set_settings)
    return app
