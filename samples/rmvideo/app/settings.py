"""Cài đặt có kiểu — MỘT nguồn sự thật cho: giá trị mặc định, kiểm tra hợp lệ, lựa chọn hiển thị trên web, bí mật
(che + không đưa vào notebook Kaggle), và lưu atomically với quyền 0600. Thay cho chuỗi `if k == ...` trong
config.save_settings và bảng `_SETTING_OPTIONS` tách rời trong web_panel."""
from __future__ import annotations
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Setting:
    key: str
    default: str
    help: str
    options: tuple[tuple[str, str], ...] = ()            # (giá trị, nhãn) -> web hiện <select>, validate tự động
    secret: bool = False                                 # che trên web, không vào Kaggle snapshot
    validate: Callable[[str], str] | None = None         # nhận chuỗi, trả chuỗi đã chuẩn hoá hoặc ném ValueError
    group: str = "chung"


def _regex(pattern: str, msg: str) -> Callable[[str], str]:
    def f(v: str) -> str:
        if not re.fullmatch(pattern, v):
            raise ValueError(msg)
        return v
    return f


def _csv_of(norm: Callable[[str], str]) -> Callable[[str], str]:
    def f(v: str) -> str:
        items = [norm(x) for x in re.split(r"[,\s]+", v) if x]
        return ",".join(dict.fromkeys(x for x in items if x))
    return f


class Settings:
    """Đọc env → đè bằng settings.json → truy cập `settings.get("KEY")`; `save()` kiểm tra từng khoá theo schema."""

    def __init__(self, schema: list[Setting], path: str, env: dict | None = None):
        self.schema = {s.key: s for s in schema}
        self.path = path
        env = os.environ if env is None else env
        self._env = {k: env.get(k, s.default) for k, s in self.schema.items()}
        self._over: dict[str, str] = {}
        self._lock = threading.Lock()
        self.reload()

    def reload(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                over = json.load(f)
        except FileNotFoundError:
            over = {}
        with self._lock:
            self._over = {k: str(v) for k, v in over.items() if k in self.schema}

    def get(self, key: str) -> str:
        return self._over.get(key) or self._env[key]

    def validate(self, key: str, value: str) -> str:
        s = self.schema[key]
        value = value.strip()
        if s.options and value not in {o[0] for o in s.options}:
            raise ValueError(f"{key} chỉ nhận: {', '.join(o[0] for o in s.options)}")
        return s.validate(value) if s.validate else value

    def save(self, updates: dict) -> None:
        with self._lock:
            new = dict(self._over)
            for k, v in updates.items():
                if k not in self.schema:
                    continue
                v = str(v).strip()
                if v == "-":
                    new.pop(k, None)
                elif v:
                    new[k] = self.validate(k, v)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # quyền đặt NGAY lúc tạo
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._over = new

    def describe(self) -> dict:
        """Cho web: giá trị (che bí mật), đã đè chưa, lựa chọn, nhóm, chú thích — một hàm thay cho ba."""
        out = {}
        for k, s in self.schema.items():
            v = self.get(k)
            shown = (v[:4] + "•••" + v[-3:] if len(v) > 10 else "(đã đặt)" if v else "") if s.secret else v
            out[k] = {"value": shown, "set": bool(v), "overridden": k in self._over, "options": list(s.options),
                      "help": s.help, "group": s.group}
        return out

    def snapshot(self, exclude_prefixes: tuple[str, ...] = ()) -> dict:
        """Gửi sang máy khác (Kaggle): KHÔNG bao giờ có bí mật, dù ai thêm khoá mới sau này."""
        return {k: self.get(k) for k, s in self.schema.items() if not s.secret and not k.startswith(exclude_prefixes)}


def _platform(x: str) -> str:
    x = x.lower()
    if x not in ("facebook", "tiktok"):
        raise ValueError("AUTO_PLATFORMS chỉ nhận: facebook, tiktok")
    return x


# ---- ví dụ một phần schema (rút từ config.EDITABLE hiện tại) ----
SCHEMA = [
    Setting("GEMINI_API_KEY", "", "Key Gemini", secret=True, group="AI"),
    Setting("KAGGLE_API_TOKEN", "", "Token Kaggle", secret=True, group="Kaggle"),
    Setting("KAGGLE_ASR", "large-v3", "Model bóc lời trên Kaggle",
            options=(("large-v3", "Whisper large-v3"), ("phowhisper", "PhoWhisper-large")), group="Kaggle"),
    Setting("TTS_RATE", "+8%", "Tốc độ giọng edge", validate=_regex(r"[+-]\d{1,3}%", "TTS_RATE dạng +8% hoặc -5%"), group="Giọng"),
    Setting("AUTO_PLATFORMS", "facebook,tiktok", "Nguồn tự động đăng", validate=_csv_of(_platform), group="Tự động đăng"),
]
