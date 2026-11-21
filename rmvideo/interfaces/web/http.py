"""Tiện ích request/response: đọc JSON / multipart có trần, mã job trong đường dẫn, dọn file tải dở, kiểm link, file tải về, tuỳ chọn hiển thị."""
import os

from aiohttp import web

import url_guard
from rmvideo.app import job_factory

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # gốc bot



IMAGE_MAX = 15 * 1024 * 1024


PHOTO_DURATIONS = job_factory.PHOTO_DURATIONS     # 0 = tự động theo nhạc


_parse_duration = job_factory.parse_duration        # tên cũ (test / mã đang dùng)


_pick_voice = job_factory.pick_voice


SSE_MAX = 8


JSON_MAX = 1 << 20              # body JSON (cài đặt, duyệt kịch bản) tối đa 1MB; upload đi đường multipart từng khúc, không qua trần này


LOGIN_BODY_MAX = 4096           # /api/login là đường công khai: không đọc body lớn trước khi biết mật khẩu đúng


VOICE_REF_MAX = 5 * 1024 * 1024


FIELD_MAX = 4000


REAUTH_FIELD = "_password"          # mật khẩu nhập lại khi đổi khoá nhạy cảm (config.reauth_keys)


_STEP_LABEL = {"queued": "Đang chờ", "processing": "Đang xử lý", "done": "Xong", "error": "Lỗi",
               "review": "📝 Chờ duyệt", "cancelled": "Đã hủy", "approved": "Đã duyệt"}


def _voice_options() -> list:
    try:
        import json as _json
        p = os.path.join(ROOT, "capcut_tts_api", "Voice.json")
        seen, out = set(), []
        for x in _json.load(open(p, encoding="utf-8")):
            if x.get("lan") == "vi" and x["voice_type"] not in seen and "Neural" not in x["voice_type"]:
                seen.add(x["voice_type"])
                out.append([x["voice_type"], x["display_name"].strip()])
        return out
    except Exception:
        return []


def _with_options(d: dict) -> dict:
    """Lựa chọn cố định đã có sẵn từ schema cài đặt; thêm danh sách giọng CapCut (đọc từ Voice.json)."""
    voices = _voice_options()
    for k in ("CAPCUT_VOICE_FEMALE", "CAPCUT_VOICE_MALE"):
        if k in d and voices:
            d[k]["options"] = voices
    return d


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)


def _int_id(request: web.Request) -> int:
    try:
        return int(request.match_info["id"])
    except (KeyError, ValueError):
        raise web.HTTPNotFound(text="Job không tồn tại") from None


async def _json_dict(request: web.Request) -> dict:
    """Body JSON phải là object; body hỏng trả 400 thay vì lỗi 500 kèm dấu vết trong log."""
    try:
        d = await request.json()
    except web.HTTPException:
        raise
    except Exception:
        raise web.HTTPBadRequest(text="Body phải là JSON") from None
    if not isinstance(d, dict):
        raise web.HTTPBadRequest(text="Body phải là JSON object")
    return d


async def _multipart(request: web.Request):
    if not request.content_type.startswith("multipart/"):
        raise web.HTTPBadRequest(text="Cần gửi dạng multipart/form-data")
    return await request.multipart()


async def _read_capped(part, limit: int, too_big: str) -> bytes:
    """Đọc một phần multipart theo khúc 1MB, vượt trần là dừng ngay (part.read() gom cả phần vào RAM trước khi kiểm cỡ)."""
    data = bytearray()
    while chunk := await part.read_chunk(1 << 20):
        data += chunk
        if len(data) > limit:
            raise web.HTTPBadRequest(text=too_big)
    return bytes(data)



async def _guard(url: str) -> str:
    try:
        return await url_guard.check_url_async(url)
    except url_guard.BlockedURL as e:
        raise web.HTTPBadRequest(text=str(e)) from None


def _file_response(path: str, name: str):
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})
