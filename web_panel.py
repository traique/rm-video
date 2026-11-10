"""Giao diện web điều khiển bot, chạy chung tiến trình (aiohttp có sẵn từ aiogram).
Botkeep cấp link web -> trỏ thẳng vào đây. Đặt WEB_PASSWORD để người lạ không dùng chùa quota."""
import hmac
import os
import re
import time
import uuid

from aiohttp import web

import config
from queue_manager import Job, JobQueue, QueueFull

_STEP_LABEL = {"queued": "Đang chờ", "processing": "Đang xử lý", "done": "Xong", "error": "Lỗi",
               "review": "📝 Chờ duyệt", "cancelled": "Đã hủy", "approved": "Đã duyệt"}


def _parse_duration(v) -> tuple:
    """'30'/'60'/'90'/'0' -> (giây, ""); 'full'/'split60'/'split90' -> (0, chế độ video dài)."""
    v = str(v or "0").strip()
    if v in ("full", "split60", "split90"):
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


def _check(request: web.Request):
    if config.WEB_PASSWORD:
        auth = request.headers.get("X-Auth") or request.query.get("auth") or ""
        if auth != config.WEB_PASSWORD:
            raise web.HTTPUnauthorized(text="Sai mật khẩu")


_ONOFF = [["1", "Bật"], ["0", "Tắt"]]
_SETTING_OPTIONS = {
    "TTS_PROVIDER": [["capcut", "CapCut (tự nhiên, khuyên dùng)"], ["edge", "Edge TTS (dự phòng)"]],
    "SPEECH_STYLE": [["mien_tay", "Miền Tây (tui, bà con, nè, nghen)"], ["mien_nam", "Miền Nam"], ["pho_thong", "Phổ thông"]],
    "SUB_STYLE": [["karaoke", "Karaoke (sáng dần theo giọng)"], ["box", "Hộp nền đen"], ["outline", "Chữ trắng viền đen"], ["plain", "Vàng gọn"]],
    "QUALITY": [["fast", "Nhanh (superfast, crf 23)"], ["balanced", "Cân bằng (mặc định)"], ["high", "Chuẩn TVC (chậm ~2x)"]],
    "OPENROUTER_MODEL": [["auto", "auto (tự dò model miễn phí)"]],
    "VOICE_SEPARATION": [["1", "Bật (tách giọng AI, video ≤45s)"], ["0", "Tắt"]],
    "VOICE_HUMANIZE": _ONOFF, "BRAND_LOGO": _ONOFF, "BRAND_INTRO": _ONOFF,
    "REVIEW_SCRIPT": [["0", "Tắt"], ["1", "Luôn dừng cho duyệt kịch bản"]],
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


def build_app(queue: JobQueue, hooks: dict | None = None) -> web.Application:
    hooks = hooks or {}
    async def index(_):
        return web.Response(text=HTML, content_type="text/html")

    async def health(_):
        cur = next((j for j in queue.jobs.values() if j.status == "processing"), None)
        import core_video as _cv
        return web.json_response({"ok": True, "pending": queue.pending, "pot": _cv.pot_status() or "off",
                                  "current": {"id": cur.id, "step": cur.step} if cur else None,
                                  "free_mb": __import__("shutil").disk_usage(os.path.dirname(os.path.abspath(__file__))).free // (1 << 20)})

    async def list_jobs(request):
        _check(request)
        jobs = sorted(queue.jobs.values(), key=lambda j: -j.id)
        return web.json_response({
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
                "age": int(time.time() - j.created),
            } for j in jobs],
        })

    async def add_job(request):
        _check(request)
        d = await request.json()
        urls = [u.strip() for u in re.split(r"[\s,]+", (d.get("url") or "").strip()) if u.strip()]
        if not urls or not all(u.startswith(("http://", "https://")) for u in urls):
            raise web.HTTPBadRequest(text="Link không hợp lệ")
        url = urls[0]
        mode = d.get("mode") if d.get("mode") in ("cook", "review", "music", "download") else "cook"
        dur, long_mode = _parse_duration(d.get("duration"))
        voice = _pick_voice(d.get("voice") or "")
        subs = bool(d.get("subs", True))
        music_url = (d.get("music_url") or "").strip()
        if mode == "music" and not music_url:
            raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc mới (hoặc upload file nhạc)")
        ids, pos = [], 0
        for u in urls[:10]:
            job = Job(url=u, mode=mode, duration=dur, voice=voice, subs=subs, long_mode=long_mode,
                      cover=bool(d.get("cover", True)), review=bool(d.get("review", False)),
                      quality=str(d.get("quality", "")), music_url=music_url, source="web")
            try:
                pos = queue.put(job)
            except QueueFull as e:
                if ids:
                    break
                raise web.HTTPTooManyRequests(text=str(e))
            ids.append(job.id)
        return web.json_response({"id": ids[0], "ids": ids, "position": pos})

    async def upload(request):
        _check(request)
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
        reader = await request.multipart()
        fields, saved, music_saved = {}, "", ""

        async def _save_part(part, exts, kind):
            name = (part.filename or "")
            if exts and not name.lower().endswith(exts):
                raise web.HTTPBadRequest(text=f"File {kind} không đúng định dạng")
            path = os.path.join(config.UPLOAD_DIR, f"up_{uuid.uuid4().hex}")
            size = 0
            with open(path, "wb") as f:
                while chunk := await part.read_chunk(1 << 20):
                    size += len(chunk)
                    if size > config.MAX_UPLOAD:
                        f.close(); os.remove(path)
                        raise web.HTTPRequestEntityTooLarge(max_size=config.MAX_UPLOAD, actual_size=size)
                    f.write(chunk)
            return path, name

        async for part in reader:
            if part.name == "file" and part.filename:
                saved, fields["filename"] = await _save_part(
                    part, (".mp4", ".mov", ".webm", ".mkv", ".m4v"), "video")
            elif part.name == "music" and part.filename:
                music_saved, _ = await _save_part(
                    part, (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"), "nhạc")
            else:
                fields[part.name] = (await part.text()).strip()
        url_field = fields.get("url", "")
        if not saved and not url_field.startswith(("http://", "https://")):
            raise web.HTTPBadRequest(text="Thiếu file video hoặc link video")
        mode = fields.get("mode") if fields.get("mode") in ("cook", "review", "music", "download") else "cook"
        dur, long_mode = _parse_duration(fields.get("duration"))
        voice = _pick_voice(fields.get("voice", ""))
        music_url = fields.get("music_url", "")
        if mode == "music" and not music_saved and not music_url.startswith(("http://", "https://")):
            for p in (saved, music_saved):
                if p and os.path.exists(p):
                    os.remove(p)
            raise web.HTTPBadRequest(text="Chế độ 🎵 cần link nhạc hoặc file nhạc")
        job = Job(url="" if saved else url_field, mode=mode, duration=dur, voice=voice, long_mode=long_mode,
                  subs=fields.get("subs") != "false", cover=fields.get("cover") != "false", local=saved,
                  review=fields.get("review") == "true",
                  music_local=music_saved, music_url=music_url,
                  title_hint=fields.get("filename", ""), source="web")
        try:
            pos = queue.put(job)
        except QueueFull as e:
            for p in (saved, music_saved):
                if p and os.path.exists(p):
                    os.remove(p)
            raise web.HTTPTooManyRequests(text=str(e))
        return web.json_response({"id": job.id, "position": pos})

    async def list_voices(request):
        voices = [{"id": "female", "name": "Nữ mặc định (Cài đặt)"}, {"id": "male", "name": "Nam mặc định (Cài đặt)"}]
        if config.TTS_PROVIDER == "capcut":
            import tts_capcut
            voices += [{"id": v["id"], "name": "CapCut · " + v["name"]} for v in tts_capcut.list_voices()]
        voices += [{"id": config.VOICE_FEMALE, "name": "Edge · Hoài My"}, {"id": config.VOICE_MALE, "name": "Edge · Nam Minh"}]
        return web.json_response(voices)

    async def get_settings(request):
        if not config.WEB_PASSWORD:
            raise web.HTTPForbidden(text="Đặt biến môi trường WEB_PASSWORD trước khi dùng Cài đặt từ web")
        _check(request)
        return web.json_response(_with_options(config.describe_settings()))

    async def set_settings(request):
        if not config.WEB_PASSWORD:
            raise web.HTTPForbidden(text="Đặt biến môi trường WEB_PASSWORD trước khi dùng Cài đặt từ web")
        _check(request)
        try:
            config.save_settings(await request.json())
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        return web.json_response(_with_options(config.describe_settings()))

    async def download(request):
        # Mã ngẫu nhiên riêng từng video thay cho mật khẩu: link tải nằm trong lịch sử
        # trình duyệt và access log, nên không được chứa mật khẩu.
        try:
            job = queue.jobs.get(int(request.match_info["id"]))
        except ValueError:
            raise web.HTTPNotFound()
        if not job or not hmac.compare_digest(request.query.get("t", ""), job.dl_token):
            raise web.HTTPNotFound()
        if not job.output or not os.path.exists(job.output):
            raise web.HTTPNotFound(text="Video không còn (chỉ giữ vài video gần nhất)")
        return web.FileResponse(
            job.output,
            headers={"Content-Disposition": f'attachment; filename="video_{job.id}{os.path.splitext(job.output)[1] or ".mp4"}"'},
        )

    async def yt_info(request):
        _check(request)
        url = (request.query.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise web.HTTPBadRequest(text="Link không hợp lệ")
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
        job = queue.jobs.get(int(request.match_info["id"]))
        if not job or job.status != "review":
            raise web.HTTPBadRequest(text="Job không ở trạng thái chờ duyệt")
        d = await request.json()
        edits = {int(x["i"]): str(x.get("text", "")) for x in d.get("segments", []) if "i" in x}
        new = hooks["approve"](job, edits)
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
        import core_video
        open(core_video.LOGO_PATH, "wb").write(data)
        return web.json_response({"ok": True})

    async def download_part(request):
        try:
            job = queue.jobs.get(int(request.match_info["id"]))
            k = int(request.match_info["k"])
        except ValueError:
            raise web.HTTPNotFound()
        if not job or not (0 <= k < len(job.parts)):
            raise web.HTTPNotFound()
        part = job.parts[k]
        if not hmac.compare_digest(request.query.get("t", ""), part["token"]) or not os.path.exists(part["path"]):
            raise web.HTTPNotFound(text="Video không còn (server chỉ giữ ~700MB video gần nhất)")
        return web.FileResponse(part["path"], headers={
            "Content-Disposition": f'attachment; filename="video_{job.id}_phan{k + 1}{os.path.splitext(part["path"])[1] or ".mp4"}"'})

    app = web.Application(client_max_size=config.MAX_UPLOAD + 5 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/jobs", list_jobs)
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


HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Video Bot</title>
<style>
:root{color-scheme:light dark;--bg:#f5f5f4;--card:#fff;--text:#1c1917;--mut:#78716c;--line:#e7e5e4;--acc:#ea580c;
box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1c1917;--card:#292524;--text:#fafaf9;--mut:#a8a29e;--line:#44403c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:1.3em;margin:8px 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
label{display:block;font-size:.85em;color:var(--mut);margin:10px 0 4px}
input,select,button{width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
button{background:var(--acc);color:#fff;border:0;font-weight:600;margin-top:14px;cursor:pointer}
button:disabled{opacity:.5}
.row{display:flex;gap:10px}.row>div{flex:1}
table{width:100%;border-collapse:collapse;font-size:.9em}
td,th{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
.st-done{color:#16a34a}.st-error{color:#dc2626}.st-processing{color:var(--acc)}.st-review{color:#2563eb;font-weight:600}.st-cancelled{color:var(--mut)}
a{color:var(--acc)}.mut{color:var(--mut);font-size:.85em}#msg{margin-top:10px;font-size:.9em}
</style></head><body><main>
<h1>🎬 Video Bot — bảng điều khiển</h1>
<div class="card">
  <label>Mật khẩu (nếu đã đặt WEB_PASSWORD)</label>
  <input id="pw" type="password" placeholder="để trống nếu chưa đặt">
  <label>Link video (TikTok / Facebook / YouTube...) — nhiều link thì mỗi link một dòng — hoặc chọn file bên dưới</label>
  <textarea id="url" rows="2" placeholder="https://..." style="width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit"></textarea>
  <label>Hoặc tải video lên để remake (≤ 300MB)</label>
  <input id="file" type="file" accept="video/mp4,video/quicktime,video/webm,.mkv,.m4v">
  <div class="row">
    <div><label>Chế độ</label>
      <select id="mode"><option value="cook">🍲 Nấu ăn (giữ ASMR)</option><option value="review">📦 Review (đổi giọng)</option><option value="music">🎵 Nhảy/Nhạc (thay nhạc + khớp beat)</option><option value="download">⬇️ Chỉ tải về (TikTok không logo / Facebook / YouTube)</option></select></div>
    <div><label>Thời lượng</label>
      <select id="dur"><option value="0">Giữ nguyên</option><option value="30">30 giây</option><option value="60">60 giây</option><option value="90">90 giây</option><option value="full">🎞 Video dài: remake nguyên (≤5 phút)</option><option value="split60">✂️ Cắt thành phần 60s (nguồn ≤10 phút)</option><option value="split90">✂️ Cắt thành phần 90s (nguồn ≤10 phút)</option></select></div>
    <div><label>Giọng đọc</label>
      <select id="voice"><option value="female">Nữ mặc định</option><option value="male">Nam mặc định</option></select></div>
  </div>
  <div id="music_box" style="display:none">
    <label>🎵 Link nhạc mới (YouTube/TikTok...)</label>
    <input id="music_url" placeholder="https://...">
    <label>Hoặc tải file nhạc lên (mp3/m4a/wav...)</label>
    <input id="music_file" type="file" accept="audio/*,.m4a,.opus">
    <div class="mut">Bot dò BPM của điệu nhảy và nhạc mới, chỉnh tốc độ video + căn beat cho khớp.</div>
  </div>
  <label style="display:flex;align-items:center;gap:8px;margin-top:12px" id="subs_box">
    <input type="checkbox" id="subs" checked style="width:auto"> Phụ đề tự động (khớp giọng đọc)</label>
  <label style="display:flex;align-items:center;gap:8px;margin-top:6px" id="cover_box">
    <input type="checkbox" id="cover" checked style="width:auto"> Che phụ đề in cứng của video gốc, đặt phụ đề mới lên đó (tự dò vị trí)</label>
  <label style="display:flex;align-items:center;gap:8px;margin-top:6px">
    <input type="checkbox" id="review" style="width:auto"> 📝 Duyệt/sửa kịch bản trước khi render</label>
  <button id="go">Tạo video</button>
  <div id="msg"></div>
</div>
<div class="card">
  <details><summary><b>⚙️ Cài đặt API</b> <span class="mut">(key, model, base URL, cookies, giọng đọc — lưu vào settings.json)</span></summary>
    <div id="set_fields"></div>
    <label>Logo kênh (PNG nền trong suốt, góc phải trên video)</label>
    <input id="logo_file" type="file" accept="image/png">
    <button id="save_set">Lưu cài đặt</button>
    <div class="mut" style="margin-top:8px">Bỏ trống = không đổi · nhập <b>-</b> = xóa, quay về biến môi trường · key hiển thị dạng che bớt.</div>
  </details>
</div>
<div class="card">
  <b>⬇️ Trình tải video (YouTube / TikTok / Facebook)</b>
  <label>Dán link rồi bấm Xem định dạng — hoặc tải thẳng chất lượng tốt nhất</label>
  <input id="dl_url" placeholder="https://www.youtube.com/watch?v=...">
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
    <button id="dl_probe" style="flex:1">🔎 Xem định dạng</button>
    <button id="dl_best" style="flex:1;background:var(--mut)">⭐ Tải tốt nhất</button>
  </div>
  <div id="dl_info" style="display:none;margin-top:12px">
    <div style="display:flex;gap:12px;align-items:flex-start">
      <img id="dl_thumb" style="width:110px;border-radius:8px" alt="">
      <div style="min-width:0"><b id="dl_title" style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden"></b><div class="mut" id="dl_meta"></div></div>
    </div>
    <div id="dl_opts" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px"></div>
  </div>
</div>
<div class="card" id="review_card" style="display:none">
  <b>📝 Duyệt kịch bản</b> <span class="mut" id="review_info"></span>
  <div id="review_rows"></div>
  <button id="approve_btn">Render với kịch bản này</button>
</div>
<div class="card">
  <b>Hàng đợi & kết quả</b> <span class="mut" id="qinfo"></span>
  <table><thead><tr><th>#</th><th>Trạng thái</th><th>Chi tiết</th><th></th></tr></thead><tbody id="rows"></tbody></table>
  <div class="mut">Chỉ giữ vài video gần nhất trên server — tải về ngay khi xong. Video gửi từ Telegram trả kết quả trong chat Telegram.</div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
$('pw').value=localStorage.getItem('pw')||'';
const hdr=()=>({'X-Auth':$('pw').value,'Content-Type':'application/json'});
$('mode').onchange=()=>{const v=$('mode').value,m=v=='music',d=v=='download';
  $('music_box').style.display=m?'block':'none';
  $('subs_box').style.display=(m||d)?'none':'flex';$('cover_box').style.display=(m||d)?'none':'flex';
  $('voice').disabled=m||d;$('dur').disabled=d;};
$('go').onclick=async()=>{
  localStorage.setItem('pw',$('pw').value);$('go').disabled=true;
  try{
    let r;
    if($('file').files.length||$('music_file').files.length){
      $('msg').textContent='Đang tải file lên...';
      const fd=new FormData();
      fd.append('mode',$('mode').value);fd.append('duration',$('dur').value);
      fd.append('voice',$('voice').value);fd.append('subs',$('subs').checked);fd.append('cover',$('cover').checked);fd.append('review',$('review').checked);
      fd.append('url',$('url').value);fd.append('music_url',$('music_url').value);
      if($('file').files.length)fd.append('file',$('file').files[0]);
      if($('music_file').files.length)fd.append('music',$('music_file').files[0]);
      r=await fetch('/api/upload',{method:'POST',headers:{'X-Auth':$('pw').value},body:fd});
    }else{
      $('msg').textContent='Đang gửi...';
      r=await fetch('/api/jobs',{method:'POST',headers:hdr(),body:JSON.stringify({
        url:$('url').value,mode:$('mode').value,duration:$('dur').value,voice:$('voice').value,subs:$('subs').checked,cover:$('cover').checked,review:$('review').checked,music_url:$('music_url').value})});
    }
    if(!r.ok)throw new Error(await r.text());
    const d=await r.json();$('msg').textContent='✅ Đã nhận '+((d.ids&&d.ids.length>1)?d.ids.length+' video':'')+', vị trí hàng đợi: #'+d.position;$('url').value='';$('file').value='';$('music_url').value='';$('music_file').value='';
  }catch(e){$('msg').textContent='❌ '+e.message}
  $('go').disabled=false;refresh();
};
const FIELD='width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit';
async function loadSettings(){
  try{
    const r=await fetch('/api/settings',{headers:hdr()});
    if(!r.ok){$('set_fields').innerHTML='<p class="mut">'+(r.status==403?esc(await r.text()):'Nhập đúng mật khẩu ở ô trên cùng để xem và sửa cài đặt.')+'</p>';return;}
    const d=await r.json();
    $('set_fields').innerHTML=Object.entries(d).map(([k,v])=>{
      let f;
      if(k=='YTDLP_COOKIES')
        f='<textarea data-k="'+k+'" rows="3" placeholder="'+(v.set?'(đã đặt — dán mới để thay, nhập - để xóa)':'dán nội dung cookies.txt')+'" style="'+FIELD+'"></textarea>';
      else if(v.options){
        const cur=String(v.value||'');const known=v.options.some(o=>o[0]==cur);
        f='<select data-k="'+k+'" style="'+FIELD+'"><option value="">— giữ nguyên'+(cur?': '+esc((v.options.find(o=>o[0]==cur)||[cur,cur])[1]):'')+' —</option>'+
          v.options.map(o=>'<option value="'+esc(o[0])+'">'+(o[0]==cur?'✓ ':'')+esc(o[1])+'</option>').join('')+
          (v.overridden?'<option value="-">↺ Xóa, quay về mặc định</option>':'')+'</select>';
      }else f='<input data-k="'+k+'" placeholder="'+esc(v.value||'chưa đặt')+'">';
      return '<label>'+esc(k)+(v.overridden?' <span class="mut">(đang đè từ web)</span>':'')+'</label>'+f;
    }).join('');
  }catch(e){$('set_fields').innerHTML='<p class="mut">Không tải được cài đặt: '+esc(String(e))+'</p>';}
}
$('pw').addEventListener('change',()=>{localStorage.setItem('pw',$('pw').value);loadSettings();refresh();});
document.querySelector('#save_set').onclick=async()=>{
  if($('logo_file').files.length){const lf=new FormData();lf.append('logo',$('logo_file').files[0]);
    const lr=await fetch('/api/logo',{method:'POST',headers:{'X-Auth':$('pw').value},body:lf});
    $('msg').textContent=lr.ok?'✅ Đã lưu logo':'❌ '+await lr.text();if(!lr.ok)return;$('logo_file').value='';}
  const body={};document.querySelectorAll('[data-k]').forEach(el=>{if(el.value.trim())body[el.dataset.k]=el.value});
  const r=await fetch('/api/settings',{method:'POST',headers:hdr(),body:JSON.stringify(body)});
  $('msg').textContent=r.ok?'✅ Đã lưu cài đặt, áp dụng ngay cho job kế tiếp':'❌ '+await r.text();
  if(r.ok){document.querySelectorAll('[data-k]').forEach(el=>el.value='');loadSettings();}
};
loadSettings();
function loadVoices(){fetch('/api/voices').then(r=>r.json()).then(vs=>{
  const cur=$('voice').value;
  $('voice').innerHTML=vs.map(v=>'<option value="'+esc(v.id)+'">'+esc(v.name)+'</option>').join('');
  if([...$('voice').options].some(o=>o.value===cur))$('voice').value=cur;
}).catch(e=>{$('msg').textContent='⚠️ Không tải được danh sách giọng: '+e.message});}
loadVoices();
async function refresh(){
  try{
    const r=await fetch('/api/jobs',{headers:hdr()});
    if(!r.ok){$('qinfo').textContent=' — '+(r.status==401?'nhập mật khẩu rồi bấm Tạo video':'lỗi '+r.status);return}
    const d=await r.json();
    $('qinfo').textContent=' — đang chờ: '+d.pending+(d.password_set?'':' (⚠️ chưa đặt WEB_PASSWORD, ai có link cũng dùng được)');
    $('rows').innerHTML=d.jobs.map(j=>'<tr><td>'+j.id+'</td>'+
      '<td class="st-'+esc(j.status)+'">'+esc(j.status_label)+(j.step&&['processing','review'].includes(j.status)?'<div class="mut">'+esc(j.step)+'</div>':'')+
      (j.status=='processing'?'<div class="mut">đã chạy '+Math.floor(j.elapsed/60)+'p'+(j.eta!=null?', còn ~'+Math.ceil(j.eta/60)+'p':'')+'</div>':'')+
      (['queued','processing'].includes(j.status)?'<a href="#" onclick="cancelJob('+j.id+');return false" class="st-error">Hủy</a>':'')+
      (j.status=='review'?'<a href="#" onclick="openReview('+j.id+');return false">Duyệt kịch bản</a>':'')+'</td>'+
      '<td>'+(j.mode=='cook'?'🍲':j.mode=='music'?'🎵':j.mode=='download'?'⬇️':'📦')+' '+(j.duration||'gốc')+(j.duration?'s':'')+' · '+j.source+
      (j.error?'<div class="'+(j.status=='done'?'st-processing':'st-error')+' mut">'+esc(j.error)+'</div>':'')+
      (j.script?'<div class="mut">'+esc(j.script)+'…</div>':'')+'</td>'+
      '<td>'+(j.parts&&j.parts.length>1?j.parts.map(p=>'<a href="'+esc(p.dl)+'">'+esc(p.label)+'</a>').join('<br>')+'<br>'
             :(j.downloadable?'<a href="'+esc(j.dl)+'">Tải về</a><br>':''))+
      (j.post?'<a href="#" onclick="copyPost('+j.id+');return false">Copy content</a>':'')+'</td></tr>').join('');
  window._posts={};d.jobs.forEach(j=>{if(j.post)window._posts[j.id]=j.post;window._jobs[j.id]=j;});
  }catch(e){}
}
window._jobs={};
async function cancelJob(id){const r=await fetch('/api/jobs/'+id+'/cancel',{method:'POST',headers:hdr()});$('msg').textContent=(await r.json()).msg;refresh();}
function openReview(id){const j=window._jobs[id];if(!j)return;$('review_card').style.display='block';$('review_card').dataset.id=id;
  $('review_info').textContent='— job #'+id+', '+j.approved.length+' đoạn. Sửa trực tiếp rồi bấm Render.';
  $('review_rows').innerHTML=j.approved.map(s=>'<div style="margin:8px 0;border-top:1px solid var(--line);padding-top:6px"><div class="mut">'+s.start.toFixed(1)+'s · gốc: '+esc(s.orig||'')+'</div><textarea data-i="'+s.i+'" rows="2" style="width:100%;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit">'+esc(s.text)+'</textarea></div>').join('');
  $('review_card').scrollIntoView({behavior:'smooth'});}
$('approve_btn').onclick=async()=>{const id=$('review_card').dataset.id;
  const segs=[...document.querySelectorAll('#review_rows textarea')].map(t=>({i:+t.dataset.i,text:t.value}));
  const r=await fetch('/api/jobs/'+id+'/approve',{method:'POST',headers:hdr(),body:JSON.stringify({segments:segs})});
  $('msg').textContent=r.ok?'✅ Đã duyệt, đang render ở job #'+(await r.json()).id:'❌ '+await r.text();$('review_card').style.display='none';refresh();};
$('dl_probe').onclick=async()=>{const u=$('dl_url').value.trim();if(!u)return;
  $('dl_probe').disabled=true;$('dl_probe').textContent='Đang xem...';
  try{const r=await fetch('/api/yt/info?url='+encodeURIComponent(u),{headers:hdr()});const d=await r.json();
    if(!r.ok||d.error){$('msg').textContent='❌ '+(d.error||await r.text());$('dl_info').style.display='none';return;}
    $('dl_info').style.display='block';$('dl_thumb').src=d.thumbnail;$('dl_title').textContent=d.title;
    $('dl_meta').textContent=(d.uploader?d.uploader+' · ':'')+Math.floor(d.duration/60)+':'+String(d.duration%60).padStart(2,'0')+(d.max_short?' · tối đa '+d.max_short+'p':'');
    $('dl_opts').innerHTML=d.options.map(o=>'<button data-q="'+esc(o.key)+'" style="margin:0;padding:10px 6px'+(o.key=='mp3'?';background:var(--mut);grid-column:1/-1':'')+'">'+esc(o.label)+(o.size_mb?' · ~'+o.size_mb+'MB':'')+'</button>').join('');
    $('dl_opts').querySelectorAll('button').forEach(b=>b.onclick=()=>dlGo(b.dataset.q));
  }finally{$('dl_probe').disabled=false;$('dl_probe').textContent='🔎 Xem định dạng';}};
$('dl_best').onclick=()=>dlGo('');
async function dlGo(q){const u=$('dl_url').value.trim();if(!u)return;
  const r=await fetch('/api/jobs',{method:'POST',headers:hdr(),body:JSON.stringify({url:u,mode:'download',quality:q})});
  if(r.ok){const d=await r.json();$('msg').textContent='✅ Đang tải (job #'+d.id+'), xem Hàng đợi bên dưới.';refresh();}
  else $('msg').textContent='❌ '+await r.text();}
function copyPost(id){navigator.clipboard.writeText(window._posts[id]||'').then(()=>{$('msg').textContent='📋 Đã copy content đăng bài #'+id});}
refresh();setInterval(refresh,3000);
</script></main></body></html>"""
