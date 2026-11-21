"""(Botkeep) Gói đăng TikTok bán tự động (1.2.0): đến giờ vàng, gửi cho admin video + caption TikTok soạn sẵn để đăng
tay (~30 giây / video) hoặc hẹn giờ trong TikTok Studio.

Vì sao không đăng thẳng: Content Posting API của TikTok (tài liệu 8-10/2026) chỉ cho ứng dụng CHƯA kiểm duyệt đăng video
«chỉ mình tôi» lên tài khoản đang để riêng tư, và hướng dẫn kiểm duyệt ghi rõ «không chấp nhận» công cụ đăng lên tài
khoản của chính mình / nhóm mình và ứng dụng chép nội dung từ nền tảng khác — đúng loại bot này. API cũng không có hẹn giờ.

Cách gửi (cùng kênh quản trị với thông báo, state.notify_admin):
- Telegram: file video (≤ TG_LIMIT; lớn hơn thì link tải) kèm dòng đầu «đăng lúc …», rồi một tin RIÊNG chỉ có caption
  TikTok (nhấn giữ -> sao chép, dán vào TikTok).
- Zoom: chữ + link tải (Zoom không gửi file video).
Chế độ (TIKTOK_KIT kênh bếp, KIDS_TIKTOK_KIT kênh bé): «slot» gửi trước giờ vàng LEAD giây (giờ hẹn Fanpage), «ready» gửi
ngay khi Fanpage nhận bài (để tự hẹn giờ trong TikTok Studio), «off» tắt. Caption: phần «▶ TikTok» của gói nội dung
(prompt_engine.platform_post). Trạng thái: tiktok_kit.json (TIKTOK_STATE)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = os.environ.get("TIKTOK_STATE") or os.path.join(_ROOT, "tiktok_kit.json")
VN = timezone(timedelta(hours=7))
CHANNELS: dict[str, dict[str, str]] = {"cook": {"key": "TIKTOK_KIT", "vi": "kênh bếp"},
                                       "kids": {"key": "KIDS_TIKTOK_KIT", "vi": "kênh truyện bé"}}
LEAD = 10 * 60                 # «slot»: gửi trước giờ vàng 10 phút (kịp lưu video, mở TikTok, dán caption)
RETRY = 10 * 60                # chưa kênh nào nhận được: thử lại sau 10 phút
MAX_TRIES = 3
STALE = 6 * 3600               # quá giờ vàng chừng này mà vẫn chưa gửi được (bot tắt lâu): bỏ, không gửi gói cũ
KEEP_ITEMS = 200
_LOCK = asyncio.Lock()


def mode(ch: str) -> str:
    v = str(getattr(config, CHANNELS[ch]["key"], "") or "").strip()
    return v if v in ("slot", "ready", "off") else "slot"


def load_state(path: str | None = None) -> dict:
    path = path or STATE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("không phải object JSON")
    except FileNotFoundError:
        st = {}
    except (OSError, ValueError) as e:
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        logging.error("tiktok_kit.json hỏng (%s), đã đổi tên thành .bad", e)
        st = {}
    st.setdefault("items", [])
    return st


def save_state(st: dict, path: str | None = None) -> None:
    path = path or STATE_FILE
    st["items"] = st["items"][-KEEP_ITEMS:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _patch(key: str, **fields: Any) -> None:
    st = load_state()
    for it in st["items"]:
        if it.get("key") == key:
            it.update(fields)
    save_state(st)


def caption(post: str, fallback: str = "") -> str:
    """Phần «▶ TikTok» của gói nội dung (caption ngắn + hashtag + dòng công thức / ghi nguồn); không có thì fallback."""
    post = (post or "").strip()
    if "▶ TikTok:\n" in post:
        sec = post.split("▶ TikTok:\n", 1)[1].split("\n\n▶ ", 1)[0].strip()
        if sec:
            return sec[:2200]                      # TikTok: caption tối đa 2200 ký tự
    return (fallback or post)[:2200]


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts, VN).strftime("%H:%M %d/%m")


def after_facebook(ch: str, job: Any, path: str, desc: str, when: float | None, now: float | None = None) -> bool:
    """Gọi sau khi Fanpage đã nhận bài: xếp gói TikTok cho giờ hẹn `when` (None = đăng ngay). Lỗi gì cũng không làm
    hỏng bài Facebook."""
    try:
        m = mode(ch)
        if m == "off":
            return False
        now = time.time() if now is None else now
        slot = float(when or now)
        send_at = now if m == "ready" else max(now, slot - LEAD)
        from rmvideo.app import notifier
        part = next((p for p in getattr(job, "parts", []) or [] if p.get("path") == path), None)
        st = load_state()
        key = f"{ch}:{job.id}"
        if any(it.get("key") == key for it in st["items"]):
            return False
        st["items"].append({"key": key, "ch": ch, "job": int(job.id), "path": path,
                            "link": notifier.part_link(job, part) if part else "",
                            "caption": caption(str(getattr(job, "post", "") or ""), desc), "when": slot,
                            "send_at": send_at, "mode": m, "status": "pending", "tries": 0})
        save_state(st)
        logging.info("Gói TikTok: video #%s (%s) gửi lúc %s", job.id, CHANNELS[ch]["vi"], _at(send_at))
        return True
    except Exception as e:
        logging.warning("Gói TikTok: không xếp được video #%s (%s)", getattr(job, "id", "?"), str(e)[:150])
        return False


def _head(it: dict) -> str:
    vi = CHANNELS[it["ch"]]["vi"]
    if it.get("mode") == "ready":
        return (f"🎵 Gói đăng TikTok · {vi} · video #{it['job']}\nHẹn giờ trong TikTok Studio (web) lúc {_at(it['when'])}, "
                "hoặc đăng tay đúng giờ đó. Chữ đăng ở tin kế tiếp: nhấn giữ -> Sao chép.")
    return (f"🎵 Tới giờ đăng TikTok · {vi} · video #{it['job']} — đăng lúc {_at(it['when'])}\n"
            "Lưu video -> mở TikTok -> + -> Tải lên -> dán chữ đăng ở tin kế tiếp (nhấn giữ -> Sao chép).")


async def _telegram(it: dict) -> str:
    import state
    from rmvideo.app import notifier
    if not config.BOT_TOKEN or not state.bot:
        return "bot Telegram chưa chạy"
    cid = config.admin_chat_id()
    if not cid:
        return "chưa đặt ADMIN_CHAT_ID / ALLOWED_USERS"
    head = _head(it)
    try:
        if not it.get("tg_video"):                  # đã gửi video ở lượt trước (chỉ tin chữ lỗi): không gửi lại 48MB
            if os.path.getsize(it["path"]) <= config.TG_LIMIT:
                from aiogram.types import FSInputFile
                await state.bot.send_video(cid, FSInputFile(it["path"]), caption=head[:1024], supports_streaming=True,
                                           request_timeout=600)
            else:
                await state.bot.send_message(cid, f"{head}\nFile lớn quá giới hạn Telegram, tải tại: "
                                                  f"{it.get('link') or 'web panel'}")
            it["tg_video"] = True
            _patch(it["key"], tg_video=True)
        await state.bot.send_message(cid, it["caption"][:4000] or "(không có chữ đăng)")
        return "ok"
    except Exception as e:
        logging.warning("Gói TikTok: gửi Telegram lỗi: %s", str(e)[:150])
        return notifier.explain_telegram(e)


async def _zoom(it: dict) -> str:
    from rmvideo.app import notifier
    from rmvideo.services import zoom
    if not notifier.zoom_in_use():
        return ""
    text = f"{_head(it)}\nTải video: {it.get('link') or '(đặt PUBLIC_URL để có link)'}\n\n{it['caption']}"
    return await zoom.notify(zoom.admin_target(), text, f"rmvideo · TikTok #{it['job']}")


async def _send(it: dict, now: float) -> str:
    vi = CHANNELS[it["ch"]]["vi"]
    if not os.path.isfile(str(it.get("path") or "")):
        _patch(it["key"], status="failed", why="file video không còn trên máy")
        return f"{vi}: video #{it['job']} không còn file"
    res = {"telegram": await _telegram(it)}
    z = await _zoom(it)
    if z:
        res["zoom"] = z
    if any(v == "ok" for v in res.values()):
        _patch(it["key"], status="sent", sent=now, res=res)
        return f"{vi}: đã gửi gói video #{it['job']} ({', '.join(k for k, v in res.items() if v == 'ok')})"
    tries = int(it.get("tries") or 0) + 1
    why = "; ".join(f"{k}: {v}" for k, v in res.items())[:200]
    if tries >= MAX_TRIES:
        _patch(it["key"], status="failed", tries=tries, why=why)
        return f"{vi}: không gửi được gói video #{it['job']} ({why})"
    _patch(it["key"], tries=tries, why=why, send_at=now + RETRY)
    return f"{vi}: chưa gửi được gói video #{it['job']} ({why}), thử lại sau"


async def tick(now: float | None = None) -> str:
    if _LOCK.locked():
        return ""
    async with _LOCK:
        now = time.time() if now is None else now
        out = []
        for it in load_state()["items"]:
            if it.get("status") != "pending" or float(it.get("send_at") or 0) > now:
                continue
            if it.get("mode") != "ready" and now > float(it.get("when") or 0) + STALE:
                _patch(it["key"], status="failed", why="quá giờ vàng (bot tắt lâu), không gửi gói cũ")
                continue
            out.append(await _send(it, now))
        return "; ".join(out)


def status_lines(ch: str, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    m = mode(ch)
    if m == "off":
        return ["🎵 TikTok: tắt gói đăng (bật ở Cài đặt)"]
    items = [i for i in load_state()["items"] if i.get("ch") == ch]
    wait = [i for i in items if i.get("status") == "pending"]
    sent = [i for i in items if i.get("status") == "sent"]
    line = ("🎵 TikTok: gửi gói đăng (video + chữ) qua Telegram / Zoom "
            + ("10 phút trước giờ vàng" if m == "slot" else "ngay khi video xong, bạn tự hẹn giờ trong TikTok Studio")
            + f" | đã gửi {len(sent)}")
    if wait:
        line += " | sắp gửi: " + ", ".join(_at(float(i["send_at"])) for i in wait[:4])
    bad = [i for i in items if i.get("status") == "failed" and i.get("why")]
    lines = [line]
    if bad and float(bad[-1].get("send_at") or 0) > now - 2 * 86400:
        lines.append(f"TikTok lỗi gần nhất (video #{bad[-1]['job']}): {bad[-1]['why']}")
    return lines


async def run_forever(every: float = 60) -> None:
    await asyncio.sleep(80)
    while True:
        try:
            msg = await tick()
            if msg:
                logging.info("Gói TikTok: %s", msg)
        except Exception as e:
            logging.warning("Gói TikTok lỗi: %s", str(e)[:200])
        await asyncio.sleep(every)


def pending_paths() -> set[str]:
    """File video còn chờ (storage không dọn các file này)."""
    return {str(i["path"]) for i in load_state()["items"] if i.get("status") in ("pending",) and i.get("path")}
