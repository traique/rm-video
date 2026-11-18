"""(Kaggle) Model chữ cho truyện bé: Qwen3.6-35B-A3B (GGUF 4-bit) chạy bằng llama-server của llama.cpp, chia lên 2 GPU T4.

Bài đo 17/11/2026 (docs/KE_HOACH_KIDS_STORY.md): 57 token/giây, nạp 81-93 giây, biên dịch llama.cpp 35 phút (một lần,
lưu vào notebook / dataset rồi gắn vào phiên). Thứ tự tìm bộ chạy: file `llama-server` trong /kaggle/input (notebook
rmvideo-kids-models hoặc dataset bạn chỉ ở KIDS_KAGGLE_SOURCES) -> gói python `llama_cpp` nếu đã cài -> báo lỗi rõ.
Model: file .gguf trong /kaggle/input khớp mẫu; không có thì tải từ Hugging Face (~21GB, ~3 phút trên Kaggle).

Sinh JSON theo schema (grammar của llama.cpp: không có JSON hỏng), kiểm bằng mã (story.validate), bảo model viết lại
tối đa RETRIES lần kèm lý do; tắt chế độ suy nghĩ (--reasoning-budget 0 + enable_thinking=false)."""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from rmvideo.kids import prompts, story as story_mod

INPUT_ROOT = "/kaggle/input"
DEFAULT_HF = "lmstudio-community/Qwen3.6-35B-A3B-GGUF"   # một file Q4_K_M 20,2GB (unsloth chỉ có UD-/MXFP4)
DEFAULT_PATTERN = "*Qwen3.6-35B-A3B*Q4_K_M*.gguf"
PORT = 8089
N_CTX = 8192
RETRIES = 3                 # viết lại tối đa chừng này lần khi bộ kiểm báo lỗi (mỗi lượt ~11 giây, đo 18/11/2026)
MAX_TOKENS = 2600
TEMPERATURE = 0.75
READY_TIMEOUT = 420         # giây chờ llama-server nạp model (nạp 21GB lên 2 GPU: đo 81-93 giây)
Log = Callable[[str], None]


def _walk(root: str, depth: int = 6) -> Iterator[tuple[str, list[str], list[str]]]:
    root = root.rstrip("/")
    base = root.count("/")
    for d, dirs, files in os.walk(root):
        if d.count("/") - base >= depth:
            dirs[:] = []
        yield d, dirs, files


def find_binary(root: str = INPUT_ROOT) -> str | None:
    """`llama-server` (hoặc bản cũ `server`) đã biên dịch, nằm đâu đó trong các nguồn gắn vào phiên."""
    if not os.path.isdir(root):
        return None
    for d, _, files in _walk(root):
        for name in ("llama-server", "llama-server.bin"):
            if name in files:
                p = os.path.join(d, name)
                return p
    return None


def find_gguf(pattern: str = DEFAULT_PATTERN, root: str = INPUT_ROOT) -> str | None:
    """File GGUF khớp mẫu (model chia nhiều phần: trả phần 00001, llama.cpp tự nạp các phần còn lại)."""
    if not os.path.isdir(root):
        return None
    hits = sorted(glob.glob(os.path.join(root, "**", pattern), recursive=True))
    first = [h for h in hits if "-00001-of-" in h]
    return first[0] if first else (hits[0] if hits else None)


def download_gguf(repo: str, pattern: str, dst: str, log: Log = logging.info) -> str:
    """Tải các file GGUF khớp mẫu từ Hugging Face (không cần token với repo công khai)."""
    from huggingface_hub import snapshot_download
    log(f"Tải model chữ {repo} ({pattern}) từ Hugging Face")
    t0 = time.time()
    d = snapshot_download(repo, allow_patterns=[pattern, "*" + pattern.lstrip("*")], local_dir=dst)
    p = find_gguf(pattern, d)
    if not p:
        raise RuntimeError(f"Tải {repo} xong nhưng không thấy file khớp {pattern}")
    log(f"Tải model chữ xong sau {time.time() - t0:.0f}s: {os.path.basename(p)}")
    return p


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def parse_json(text: str) -> dict:
    """JSON trong câu trả lời (bỏ khối suy nghĩ, bỏ rào ``` nếu có, cắt từ { đầu tới } cuối)."""
    t = strip_think(text)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.S)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("model không trả JSON")
    return json.loads(t[a:b + 1])


class Server:
    """llama-server: một tiến trình, API OpenAI-compatible ở localhost."""

    def __init__(self, binary: str, model: str, n_ctx: int = N_CTX, port: int = PORT, log: Log = logging.info):
        self.binary, self.model, self.n_ctx, self.port, self.log = binary, model, n_ctx, port, log
        self.proc: subprocess.Popen | None = None
        self.log_path = os.path.join(tempfile.gettempdir(), "rm_llama_server.log")

    def start(self) -> None:
        # /kaggle/input chỉ đọc và file đầu ra notebook mất quyền chạy (lỗi thật 18/11/2026): chép cả thư mục (binary +
        # các .so cạnh nó) sang chỗ ghi được rồi đặt quyền chạy
        src_dir = os.path.dirname(self.binary)
        if not os.access(src_dir, os.W_OK):
            dst_dir = os.path.join(tempfile.gettempdir(), "rm_llama")
            if not os.path.exists(os.path.join(dst_dir, os.path.basename(self.binary))):
                shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
            self.binary = os.path.join(dst_dir, os.path.basename(self.binary))
        mode = os.stat(self.binary).st_mode
        if not mode & stat.S_IXUSR:
            os.chmod(self.binary, mode | stat.S_IXUSR | stat.S_IRUSR)
        env = dict(os.environ)
        lib = os.path.dirname(self.binary)
        env["LD_LIBRARY_PATH"] = lib + ":" + env.get("LD_LIBRARY_PATH", "")
        self.log_path = os.path.join(tempfile.gettempdir(), "rm_llama_server.log")
        args = [self.binary, "-m", self.model, "-c", str(self.n_ctx), "-ngl", "999", "--port", str(self.port),
                "--host", "127.0.0.1", "--jinja", "--reasoning-budget", "0",
                "--chat-template-kwargs", '{"enable_thinking": false}', "-np", "1", "--no-warmup", "--flash-attn", "on"]
        self.log(f"llama-server: nạp {os.path.basename(self.model)} ({os.path.getsize(self.model) >> 20}MB)")
        # log ra FILE: ống PIPE không ai đọc đầy sau ~64KB là llama-server kẹt khi ghi log, request treo mãi
        self._logf = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(args, env=env, stdout=self._logf, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        while time.time() - t0 < READY_TIMEOUT:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server thoát sớm: " + self._log_tail())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
                    if json.loads(r.read().decode() or "{}").get("status") == "ok":
                        self.log(f"llama-server sẵn sàng sau {time.time() - t0:.0f}s")
                        return
            except Exception:
                pass
            time.sleep(3)
        raise RuntimeError(f"llama-server không sẵn sàng sau {READY_TIMEOUT}s: " + self._log_tail())

    def _log_tail(self, n: int = 1500) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="ignore") as f:
                return f.read()[-n:]
        except OSError:
            return ""

    def chat(self, messages: list[dict[str, str]], schema: dict, max_tokens: int = MAX_TOKENS,
             temperature: float = TEMPERATURE, seed: int | None = None) -> str:
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.9,
                "response_format": {"type": "json_schema", "json_schema": {"name": "story", "schema": schema}},
                "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True}
        if seed is not None:
            body["seed"] = int(seed)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read().decode())
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        content = msg.get("content") or ""
        if "{" not in content and msg.get("reasoning_content"):        # lỗi đã thấy ở Qwen3.5: JSON rơi vào reasoning
            content = msg["reasoning_content"]
        return content

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        f = getattr(self, "_logf", None)
        if f:
            f.close()


class PyBackend:
    """Gói python llama_cpp (nếu đã cài sẵn wheel CUDA): dự phòng khi không có file llama-server."""

    def __init__(self, model: str, n_ctx: int = N_CTX, log: Log = logging.info):
        self.model, self.n_ctx, self.log = model, n_ctx, log
        self.llm: Any = None

    def start(self) -> None:
        from llama_cpp import Llama
        self.log(f"llama_cpp (python): nạp {os.path.basename(self.model)}")
        self.llm = Llama(model_path=self.model, n_gpu_layers=-1, n_ctx=self.n_ctx, verbose=False)

    def chat(self, messages: list[dict[str, str]], schema: dict, max_tokens: int = MAX_TOKENS,
             temperature: float = TEMPERATURE, seed: int | None = None) -> str:
        assert self.llm is not None
        d: dict[str, Any]
        d = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature,
                                            response_format={"type": "json_object", "schema": schema},
                                            seed=seed)
        return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def stop(self) -> None:
        self.llm = None


def backend(spec_llm: dict, tmp: str, log: Log = logging.info) -> Server | PyBackend:
    """Chọn bộ chạy + model theo cài đặt trong spec: {"pattern", "hf_repo"}."""
    pattern = spec_llm.get("pattern") or DEFAULT_PATTERN
    model = find_gguf(pattern) or download_gguf(spec_llm.get("hf_repo") or DEFAULT_HF, pattern,
                                                os.path.join(tmp, "llm_model"), log)
    binary = find_binary()
    if binary:
        return Server(binary, model, int(spec_llm.get("n_ctx") or N_CTX), log=log)
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        raise RuntimeError("Không thấy llama-server trong /kaggle/input (chạy lệnh console kids_setup để biên dịch và "
                           "lưu một lần, hoặc điền KIDS_KAGGLE_SOURCES trỏ tới dataset/notebook có sẵn llama-server)") \
            from None
    return PyBackend(model, int(spec_llm.get("n_ctx") or N_CTX), log)


class StoryRejected(RuntimeError):
    """Hết lượt mà bản tốt nhất vẫn còn lỗi chặn; mang theo nhật ký các lượt (vào result.json để soát)."""

    def __init__(self, msg: str, attempts: list[dict]):
        super().__init__(msg)
        self.attempts = attempts


def generate_story(spec: dict, data: dict, tmp: str, log: Log = logging.info,
                   srv: Server | PyBackend | None = None) -> tuple[dict, list[dict]]:
    """Truyện đã chuẩn hoá, cùng nhật ký các lượt [{attempt, problems, secs, words, text}]. Đạt bộ kiểm thì trả ngay;
    hết lượt thì chấp nhận bản ít lỗi nhất nếu không còn lỗi CHẶN (story.blocking), từ tiếng Anh bỏ sót chèn bằng mã
    (story.inject_words), ghi «accepted_with» vào lượt cuối; còn lỗi chặn thì ném StoryRejected.
    srv: bộ chạy đã khởi động (test); None thì tự chọn, chạy, rồi tắt."""
    brief, channel = spec["brief"], spec.get("channel") or {}
    own = srv is None
    if srv is None:
        srv = backend(spec.get("llm") or {}, tmp, log)
        srv.start()
    run: Server | PyBackend = srv
    attempts: list[dict] = []
    best: tuple[tuple[int, int], dict] | None = None
    feedback: list[str] | None = None
    try:
        for k in range(1 + RETRIES):
            t0 = time.time()
            raw = run.chat(prompts.messages(brief, data, channel, feedback), prompts.schema(data),
                           seed=int(brief.get("seed") or 0) + k)
            try:
                parsed = parse_json(raw)
            except (ValueError, json.JSONDecodeError) as e:
                attempts.append({"attempt": k + 1, "problems": [f"JSON hỏng: {str(e)[:80]}"],
                                 "secs": round(time.time() - t0, 1)})
                feedback = ["trả về đúng JSON theo schema"]
                continue
            st = story_mod.normalize(parsed, data)
            problems = story_mod.validate(st, brief, data)
            hard = story_mod.blocking(st, brief, data)
            attempts.append({"attempt": k + 1, "problems": problems, "blocking": hard, "secs": round(time.time() - t0, 1),
                             "words": sum(story_mod.word_count(s["text"]) for s in st["scenes"]),
                             "text": story_mod.script_text(st)[:1500]})
            log(f"Truyện bé: lượt {k + 1} {attempts[-1]['secs']}s, {attempts[-1]['words']} chữ"
                + (f", lỗi: {'; '.join(problems)[:300]}" if problems else ", đạt"))
            if not problems:
                return st, attempts
            score = (len(hard), len(problems))
            if best is None or score < best[0]:
                best = (score, st)
            feedback = problems
    finally:
        if own:
            run.stop()
    if best is not None and not story_mod.blocking(best[1], brief, data):
        st = best[1]
        added = story_mod.inject_words(st, brief, data)
        soft = story_mod.validate(st, brief, data)
        attempts[-1]["accepted_with"] = soft
        attempts[-1]["injected"] = added
        log("Truyện bé: hết lượt, dùng bản ít lỗi nhất" + (f", chèn từ thiếu: {', '.join(added)}" if added else "")
            + (f"; còn: {'; '.join(soft)[:200]}" if soft else ""))
        return st, attempts
    raise StoryRejected("Kịch bản chưa đạt sau %d lượt: %s" % (1 + RETRIES, "; ".join(attempts[-1]["problems"])[:400]),
                        attempts)
