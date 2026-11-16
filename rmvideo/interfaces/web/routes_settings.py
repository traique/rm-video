"""Cài đặt (nhập lại mật khẩu khi đổi khoá nhạy cảm), danh sách giọng, logo, giọng mẫu nhân bản."""
import asyncio
import os
import secrets

from aiohttp import web

import config
from queue_manager import JobQueue
from rmvideo.media import proc
from rmvideo.interfaces.web.auth import _check, _client_key, _password_ok, login_locked, record_fail
from rmvideo.interfaces.web.http import (REAUTH_FIELD, VOICE_REF_MAX, _json_dict, _multipart, _read_capped,
                                         _with_options)



def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    async def list_voices(request):
        _check(request)
        voices = [{"id": "female", "name": "Nữ mặc định (Cài đặt)"}, {"id": "male", "name": "Nam mặc định (Cài đặt)"}]
        if config.TTS_PROVIDER == "capcut":
            import tts_capcut
            # CapCut có liệt kê Hoai My / Nam Minh nhưng đọc là lỗi TTSInvalidSpeaker (thử 11/2026): bỏ khỏi danh sách
            voices += [{"id": v["id"], "name": "CapCut · " + v["name"]} for v in tts_capcut.list_voices()
                       if v["id"] not in (config.VOICE_FEMALE, config.VOICE_MALE)]
        if config.TTS_PROVIDER == "vieneu":
            import kaggle_worker
            voices += [{"id": f"vieneu:{n}", "name": f"VieNeu · {n} (miền Nam, nữ)"} for n in kaggle_worker.SOUTH_FEMALE]
            voices += [{"id": f"vieneu:{n}", "name": f"VieNeu · {n} (miền Nam, nam)"} for n in kaggle_worker.SOUTH_MALE]
            if os.path.exists(kaggle_worker.VOICE_REF):
                voices.append({"id": "vieneu:clone", "name": "VieNeu · giọng nhân bản (đoạn mẫu đã tải lên)"})
        voices += [{"id": config.VOICE_FEMALE, "name": "Edge · Hoài My"}, {"id": config.VOICE_MALE, "name": "Edge · Nam Minh"}]
        return web.json_response(voices)

    async def get_settings(request):
        _check(request)
        return web.json_response({"fields": _with_options(config.describe_settings()), "groups": config.setting_groups()})

    async def set_settings(request):
        _check(request)
        body = await _json_dict(request)
        pw = str(body.pop(REAUTH_FIELD, "") or "")
        need = config.reauth_keys(body)
        # A5: proxy tải video, link web, token Kaggle / Fanpage, người dùng Telegram đổi được nơi dữ liệu và quyền đi
        # tới -> phiên đăng nhập (cookie có thể bị lộ) là chưa đủ, phải nhập lại mật khẩu. X-Auth đã là mật khẩu.
        if need and request.headers.get("X-Auth") is None:
            key = _client_key(request)
            wait = login_locked(key)
            if wait:
                raise web.HTTPTooManyRequests(text=f"Sai mật khẩu quá nhiều lần, thử lại sau {wait} giây.")
            if not _password_ok(pw):
                if pw:
                    record_fail(key)
                    await asyncio.sleep(0.5)
                return web.json_response(
                    {"need_password": True, "keys": need,
                     "error": "Sai mật khẩu" if pw else "Nhập lại mật khẩu web để đổi: " + ", ".join(need)}, status=403)
        try:
            config.save_settings(body)
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response({"fields": _with_options(config.describe_settings()), "groups": config.setting_groups()})

    async def upload_logo(request):
        _check(request)
        reader = await _multipart(request)
        part = await reader.next()
        if not part or not (part.filename or "").lower().endswith(".png"):
            raise web.HTTPBadRequest(text="Logo phải là file .png (nền trong suốt)")
        data = await _read_capped(part, config.LOGO_MAX_BYTES,
                                  f"Logo tối đa {config.LOGO_MAX_BYTES // 1024}KB (logo đi kèm mỗi việc gửi Kaggle, "
                                  "notebook chỉ chứa được 1MB): thu nhỏ về ≤512px rồi tải lại")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise web.HTTPBadRequest(text="File không phải PNG thật")
        import core_video
        with open(core_video.LOGO_PATH, "wb") as f:
            f.write(data)
        return web.json_response({"ok": True})

    async def upload_voice_ref(request):
        """Đoạn giọng mẫu cho giọng nhân bản VieNeu (3-15 giây, giọng bạn có quyền dùng). Chuẩn hoá thành mp3 mono
        24kHz, cắt còn 10 giây; file gốc không giữ."""
        _check(request)
        import kaggle_worker
        reader = await _multipart(request)
        part = await reader.next()
        if not part:
            raise web.HTTPBadRequest(text="Chưa chọn file giọng mẫu")
        data = await _read_capped(part, VOICE_REF_MAX, "Giọng mẫu tối đa 5MB")
        # tên tạm riêng cho mỗi lần tải: bấm Lưu 2 lần thì 2 request chạy song song, dùng chung tên là request này
        # xoá mất file của request kia (lỗi thật: "ffprobe lỗi: voice_ref.mp3.tmp.mp3: No such file")
        tag = secrets.token_hex(6)
        tmp = f"{kaggle_worker.VOICE_REF}.{tag}.upload"
        out = f"{kaggle_worker.VOICE_REF}.{tag}.tmp.mp3"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            r = await proc.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-t", "10", "-vn", "-ac", "1", "-ar", "24000",
                                "-b:a", "96k", out], timeout=120, check=False)     # không thuộc job: huỷ job không giết
            if r.code != 0 or not os.path.exists(out):
                raise web.HTTPBadRequest(text="Không đọc được file âm thanh: " + r.stderr[-200:])
            import core_video
            try:
                dur = await core_video.probe_duration(out)
            except RuntimeError as e:
                raise web.HTTPBadRequest(text=f"Không đọc được độ dài giọng mẫu: {str(e)[-150:]}") from None
            if dur < 3:
                raise web.HTTPBadRequest(text=f"Giọng mẫu chỉ {dur:.1f} giây, cần 3-15 giây")
            os.replace(out, kaggle_worker.VOICE_REF)
        finally:
            for leftover in (tmp, out):
                if os.path.exists(leftover):
                    os.remove(leftover)
        return web.json_response({"ok": True, "seconds": round(dur, 1)})

    app.router.add_post("/api/logo", upload_logo)
    app.router.add_post("/api/voice_ref", upload_voice_ref)
    app.router.add_get("/api/voices", list_voices)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", set_settings)
