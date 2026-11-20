"""🎵 Thư viện nhạc nền và link tạm cho Kaggle tải file trên Botkeep (/kin/...)."""
import os

from aiohttp import web

import config
from queue_manager import JobQueue
from rmvideo.interfaces.web.auth import _check
from rmvideo.interfaces.web.http import _json_dict, _multipart


def add_routes(app: web.Application, queue: JobQueue, hooks: dict) -> None:
    def _bgm_state():
        import bgm
        return {"items": [{"key": x["key"], "name": x["name"], "kind": "link" if "url" in x else "file"}
                          for x in bgm.library()],
                "mode": config.NO_SPEECH, "orig": config.BGM_ORIG, "public_url": bool((config.PUBLIC_URL or "").strip()),
                "lyria": bgm.lyria_state()}

    async def bgm_list(request):
        """Thư viện nhạc nền cho video KHÔNG LỜI (NO_SPEECH=music): link (BGM_LINKS) + file đã tải lên."""
        _check(request)
        return web.json_response(_bgm_state())

    async def bgm_upload(request):
        """Tải lên một hoặc nhiều file nhạc nền (mp3/m4a/wav..., tối đa bgm.MAX_MB mỗi file). Kiểm tra đọc được nhạc."""
        _check(request)
        import bgm
        from rmvideo.media import ff
        reader = await _multipart(request)
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if not getattr(part, "filename", None):
                continue
            data = bytearray()
            while True:
                chunk = await part.read_chunk(1 << 20)
                if not chunk:
                    break
                data += chunk
                if len(data) > bgm.MAX_MB * 1024 * 1024:
                    raise web.HTTPBadRequest(text=f"{part.filename}: file nhạc tối đa {bgm.MAX_MB}MB")
            try:
                fn = bgm.save_upload(part.filename, bytes(data))
            except ValueError as e:
                raise web.HTTPBadRequest(text=str(e)) from None
            try:
                dur = await ff.probe_duration(os.path.join(bgm.DIR, fn))
            except (RuntimeError, ValueError):
                dur = 0.0
            if dur < 5:
                bgm.delete(fn)
                raise web.HTTPBadRequest(text=f"{part.filename}: không đọc được nhạc hoặc quá ngắn (cần từ 5 giây)")
            saved.append(fn)
        if not saved:
            raise web.HTTPBadRequest(text="Chưa chọn file nhạc")
        return web.json_response({**_bgm_state(), "saved": saved})

    async def bgm_add_link(request):
        _check(request)
        import bgm
        url = str((await _json_dict(request)).get("url") or "").strip()
        if not url:
            raise web.HTTPBadRequest(text="Dán link nhạc (TikTok / YouTube / file mp3)")
        if len(bgm.links()) >= 100:
            raise web.HTTPBadRequest(text="Tối đa 100 link nhạc nền, xoá bớt trước")
        try:
            config.save_settings({"BGM_LINKS": ",".join(bgm.links() + [url])})
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from None
        return web.json_response(_bgm_state())

    async def bgm_delete(request):
        _check(request)
        import bgm
        key = str((await _json_dict(request)).get("key") or "")
        if key.startswith("file:"):
            if not bgm.delete(key[5:]):
                raise web.HTTPNotFound(text="Không thấy file nhạc")
        else:
            links = bgm.links()
            if key not in links:
                raise web.HTTPNotFound(text="Không thấy link nhạc")
            config.save_settings({"BGM_LINKS": ",".join(x for x in links if x != key) or "-"})
        return web.json_response(_bgm_state())

    async def bgm_lyria(request):
        """Tạo đủ kho nhạc Lyria của bếp trong nền (như lệnh console cook_bgm)."""
        _check(request)
        import bgm
        msg = bgm.build_lyria_later()
        return web.json_response({**_bgm_state(), "msg": msg})

    async def kaggle_input(request):
        """Link tạm cho Kaggle tải file đang nằm trên Botkeep (video tải lên, nhạc, ảnh). Không cần đăng nhập: mã 32 byte
        ngẫu nhiên, chỉ trỏ đúng một file, hết hạn sau vài giờ (kaggle_worker.serve)."""
        import kaggle_worker
        path = kaggle_worker.served(request.match_info["token"])
        if not path:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    app.router.add_get("/api/bgm", bgm_list)
    app.router.add_post("/api/bgm", bgm_upload)
    app.router.add_post("/api/bgm/link", bgm_add_link)
    app.router.add_post("/api/bgm/delete", bgm_delete)
    app.router.add_post("/api/bgm/lyria", bgm_lyria)
    app.router.add_get("/kin/{token}/{name}", kaggle_input)
