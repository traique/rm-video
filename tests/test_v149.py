"""1.4.9 (24/11: người dùng lỡ xoá hết «Your Work» trên Kaggle): bot tự nhận ra thiếu gì và tự khôi phục được gì."""
import asyncio
import time

import pytest

import config
import kaggle_api
import kaggle_worker
import kids_auto
from rmvideo.media import lyria


def test_kids_ready_states(monkeypatch):
    monkeypatch.setattr(kaggle_worker, "_READY", {})
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "")

    def gone(slug):
        raise kaggle_api.KaggleError("Kaggle lỗi 404 ở GetKernelSessionStatus: Not found")
    monkeypatch.setattr(kaggle_api, "status", gone)
    assert "gõ kids_setup" in kaggle_worker.kids_ready()
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("RUNNING", ""))
    assert "đang chuẩn bị" in kaggle_worker.kids_ready()
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("ERROR", "OOM"))
    assert "kids_setup lại" in kaggle_worker.kids_ready()
    monkeypatch.setattr(kaggle_api, "status", lambda slug: ("COMPLETE", ""))
    assert kaggle_worker.kids_ready() == ""
    monkeypatch.setattr(kaggle_api, "status", gone)
    assert kaggle_worker.kids_ready() == ""                         # vừa thấy sẵn sàng: nhớ 30 phút, khỏi hỏi mỗi lượt
    monkeypatch.setattr(kaggle_worker, "_READY", {})
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "dataset:ban/models, notebook:ban/llama")
    assert kaggle_worker.kids_ready() == ""                         # nguồn riêng: không xét notebook mặc định


def test_kids_setup_kernel_downloads_lora(monkeypatch):
    text = kaggle_worker.kids_models_kernel()
    compile(text, "kids_models.py", "exec")
    assert kaggle_worker.LORA_URL in text and kaggle_worker.LORA_SHA in text and "__LORA" not in text
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("push_kids_lora", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts", "push_kids_lora.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.SHA256[kaggle_worker.LORA_FILE] == kaggle_worker.LORA_SHA
    pushed = []
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(kaggle_api, "push", lambda slug, title, text, gpu=True, **k: pushed.append((slug, text, gpu)) or {})
    assert "LoRA" in kaggle_worker.kids_setup() and pushed[0][0] == "rmvideo-kids-models" and kaggle_worker.LORA_URL in pushed[0][1]


def test_lora_falls_back_to_models_notebook(monkeypatch):
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_LORA", "dcn_lora_v1")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "")
    monkeypatch.setattr(kaggle_worker, "_LORA_OK", {})
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: False)
    assert kaggle_worker.lora_source() == ("notebook", "rmvideo-kids-models", "dcn_lora_v1")
    assert kaggle_worker.kids_sources() == (["rmvideo-kids-models"], [])         # không gắn dataset đã mất, không lặp notebook


def test_music_dataset_gone_is_pushed_again(tmp_path, monkeypatch):
    from rmvideo.kids import bgm
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    lib = bgm._lib(str(tmp_path / "lib"))
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "playful_1.m4a").write_bytes(b"x")
    lyria.add_track(lib.dir, "playful_1.m4a", "playful", 3, "p")
    data = lyria.load_library(lib.dir)
    data["pushed"] = ["playful_1.m4a"]
    lyria.save_library(data, lib.dir)
    monkeypatch.setattr(lyria, "_REMOTE", {})
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: True)
    assert lyria.remote_ok(lib) and not lyria.changed_since_push(lyria.load_library(lib.dir))
    monkeypatch.setattr(lyria, "_REMOTE", {})
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: False)
    assert not lyria.remote_ok(lib) and lyria.changed_since_push(lyria.load_library(lib.dir))     # sẽ đẩy lại
    pushed = []
    monkeypatch.setattr(bgm, "push_dataset", lambda d=None, log=None: _done(pushed, d, lib))
    from rmvideo.media import lyria_kaggle
    monkeypatch.setattr(lyria_kaggle, "proven", lambda: False)
    monkeypatch.setattr(bgm, "TRACKS_PER_MOOD", 1)
    data = lyria.load_library(lib.dir)
    data["pushed"] = ["playful_1.m4a"]
    lyria.save_library(data, lib.dir)
    monkeypatch.setattr(lyria, "_REMOTE", {})
    ref = asyncio.run(bgm.pick({"plot": ""}, {"plots": []}, lib_dir=lib.dir, log=lambda m: None))
    assert pushed == [lib.dir] and ref["name"] == "playful_1.m4a"


async def _done(pushed, d, lib):
    pushed.append(d)
    data = lyria.load_library(lib.dir)
    data["pushed"] = ["playful_1.m4a"]
    lyria.save_library(data, lib.dir)
    return data


def test_kids_job_stops_early_without_models(tmp_path, monkeypatch):
    """Thiếu model trên Kaggle: job truyện bé báo «gõ kids_setup» NGAY (không gọi Gemini nghĩ ý / viết truyện); lượt tự động
    không tạo job (khỏi 3 lần lỗi liền -> dừng tự động)."""
    import pipeline
    import prompt_engine
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KAGGLE_USERNAME", "u")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(kaggle_worker, "_READY", {})
    monkeypatch.setattr(kaggle_api, "status", lambda slug: (_ for _ in ()).throw(kaggle_api.KaggleError("404 Not found")))
    called = []

    async def gem(*a, **k):
        called.append(1)
        return "{}"
    monkeypatch.setattr(prompt_engine, "_call_gemini", gem)
    from state import queue
    monkeypatch.setattr(queue, "pending", 0)
    job = kids_auto.make_now("web")
    (tmp_path / "w").mkdir()
    with pytest.raises(RuntimeError, match="gõ kids_setup"):
        asyncio.run(pipeline.process(job, str(tmp_path / "w")))
    assert called == []
    monkeypatch.setattr(config, "KIDS_AUTO", "1")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_ID", "222")
    monkeypatch.setattr(config, "KIDS_FB_PAGE_TOKEN", "tok")
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "k.json"))
    put = []
    monkeypatch.setattr(queue, "put", lambda j, force=False: put.append(j) or 1)
    msg = asyncio.run(kids_auto.tick(time.time()))
    assert msg.startswith("chưa làm truyện") and "kids_setup" in msg and put == []


def test_resources_report_lines(monkeypatch, tmp_path):
    import bgm as cook_bgm
    from rmvideo.kids import bgm
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "")
    monkeypatch.setattr(config, "KAGGLE_ASR", "large-v3")
    monkeypatch.setattr(bgm, "LIB_DIR", str(tmp_path / "kids"))
    monkeypatch.setattr(cook_bgm, "LYRIA_DIR", str(tmp_path / "cook"))
    monkeypatch.setattr(kaggle_worker, "_READY", {})
    monkeypatch.setattr(kaggle_worker, "_LORA_OK", {})
    monkeypatch.setattr(kaggle_api, "status", lambda slug: (_ for _ in ()).throw(kaggle_api.KaggleError("404 Not found")))
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: False)
    lines = kaggle_worker.resources_report(heal=False)
    assert "gõ kids_setup" in lines[0] and any("LoRA" in x and "notebook" in x for x in lines)
    assert sum("chưa có bài" in x for x in lines) == 2


def test_auto_setup_only_when_models_gone(monkeypatch):
    """Người dùng lỡ xoá hết trên Kaggle (24/11) và nhờ bot tự tải lại model: khởi động thấy notebook model mất (404) thì tự
    chạy kids_setup; đang chạy / đã có / lỗi thì không gửi lại; thiếu giờ GPU thì chỉ báo."""
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "")
    pushed = []
    monkeypatch.setattr(kaggle_api, "push", lambda slug, title, text, gpu=True, **k: pushed.append(slug) or {})
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 28.5 * 3600})
    for st in ("RUNNING", "QUEUED", "COMPLETE", "ERROR"):
        monkeypatch.setattr(kaggle_api, "status", lambda slug, st=st: (st, ""))
        assert kaggle_worker.auto_setup() == "" and pushed == []

    def gone(slug):
        raise kaggle_api.KaggleError("Kaggle lỗi 404 ở GetKernelSessionStatus: Not found")
    monkeypatch.setattr(kaggle_api, "status", gone)
    msg = kaggle_worker.auto_setup()
    assert msg.startswith("tự chạy kids_setup") and pushed == ["rmvideo-kids-models"]
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 0.5 * 3600})
    assert "chỉ còn 0.5 giờ GPU" in kaggle_worker.auto_setup() and pushed == ["rmvideo-kids-models"]
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "notebook:ban/llama")
    assert kaggle_worker.auto_setup() == ""



# ---------------- rà soát 25/11 ----------------

class _SyncThread:
    def __init__(self, target=None, name=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


def test_cancel_session_only_deletes_live_sessions(monkeypatch):
    """Chỉ xoá notebook khi phiên còn xếp hàng / chạy (phiên đã xong giữ đầu ra — tiếng nền dùng lại — và log)."""
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(kaggle_worker.threading, "Thread", _SyncThread)
    monkeypatch.setattr(kaggle_worker, "_slug_parked", lambda slug: False)
    deleted = []
    monkeypatch.setattr(kaggle_api, "delete_kernel", lambda slug: deleted.append(slug) or {})
    for st, want in (("COMPLETE", []), ("ERROR", []), ("QUEUED", ["rmvideo-analyze"]), ("RUNNING", ["rmvideo-analyze"])):
        deleted.clear()
        monkeypatch.setattr(kaggle_api, "status", lambda slug, st=st: (st, ""))
        kaggle_worker._cancel_session("rmvideo-analyze")
        assert deleted == want, st


def test_kids_setup_never_frees_slots(monkeypatch):
    """kids_setup chạy ngoài _LOCK: không được xoá notebook công việc của job đang chạy khi Kaggle kín phiên."""
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(kaggle_worker, "_free_slots", lambda cur: (_ for _ in ()).throw(AssertionError("không được dọn")))

    def full(*a, **k):
        raise kaggle_api.SessionLimit("Maximum batch GPU session count of 2 reached.")
    monkeypatch.setattr(kaggle_api, "push", full)
    with pytest.raises(kaggle_api.KaggleError, match="kín 2 phiên GPU"):
        kaggle_worker.kids_setup()


def test_push_wait_stops_on_cancel(monkeypatch):
    monkeypatch.setattr(kaggle_api, "_sleep", lambda s: None)
    monkeypatch.setattr(kaggle_worker, "_free_slots", lambda cur: [])
    monkeypatch.setattr(kaggle_worker, "_others_waiting", lambda: False)
    monkeypatch.setattr(kaggle_api, "push", lambda *a, **k: (_ for _ in ()).throw(kaggle_api.SessionLimit("kín")))
    flag = {"n": 0}

    def stop():
        flag["n"] += 1
        return flag["n"] > 3
    with pytest.raises(kaggle_api.KaggleError, match="đã huỷ"):
        kaggle_worker._push("rmvideo-kids", "t", "x", True, None, None, lambda s: None, wait_max=3600, stop=stop)


def test_render_push_retries_without_analyze_output(monkeypatch):
    """Đầu ra phiên phân tích không còn (notebook đã xoá): gửi lại không gắn rmvideo-analyze, không đổ về Botkeep."""
    monkeypatch.setattr(kaggle_api, "quota", lambda: {"gpu_left": 99999})
    calls = []

    def push(slug, title, text, gpu=True, **k):
        calls.append(k.get("kernel_sources"))
        if "rmvideo-analyze" in (k.get("kernel_sources") or []):
            raise kaggle_api.KaggleError("Kaggle lỗi 400 ở SaveKernel: kernel source not found")
        raise RuntimeError("dừng ở đây")                       # đã gửi lại đúng cách: thôi, khỏi chờ phiên
    monkeypatch.setattr(kaggle_api, "push", push)
    monkeypatch.setattr(kaggle_worker, "kernel_text", lambda spec: "x")
    spec = {"kind": "remake", "token": "t", "amb_token": "abc", "voice": {"engine": "vieneu"}}
    with pytest.raises(RuntimeError, match="dừng ở đây"):
        kaggle_worker._run_sync(spec, "/tmp", lambda s: None)
    assert calls == [["rmvideo-analyze"], None]


def test_gone_detection_not_fooled_by_retry_delay():
    import prompt_engine as pe
    assert pe._gone("404 NOT_FOUND. models/x is no longer available")
    assert pe._gone("Kaggle 404 Not found") and pe._gone("This model is no longer available")
    assert not pe._gone("429 RESOURCE_EXHAUSTED. Please retry in 40.404129s.")
    assert not pe._gone("retry in 12.404s")



def test_manual_setup_script_matches_kernel():
    """scripts/kids_setup_manual.py (dán 2 dòng vào notebook chạy tay trên Kaggle, 25/11) phải đúng bằng mã kids_setup."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "kids_setup_manual.py")
    assert open(path, encoding="utf-8").read() == kaggle_worker.kids_models_kernel()


def test_custom_notebook_source_ready_by_outputs(monkeypatch):
    """Notebook chạy tay (Quick Save kèm output) không có phiên chạy nền: xét có đầu ra hay chưa."""
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "notebook234d68765b")
    monkeypatch.setattr(kaggle_worker, "_OUT_OK", {})
    monkeypatch.setattr(kaggle_api, "status", lambda slug: (_ for _ in ()).throw(kaggle_api.KaggleError("404 Not found")))
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": []})
    assert "Quick Save" in kaggle_worker.kids_ready()
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": [{"fileName": "flux-schnell-nf4/x"}]})
    assert kaggle_worker.kids_ready() == "" and kaggle_worker.auto_setup() == ""
    lines = kaggle_worker.resources_report(heal=False)
    assert lines[0].startswith("✅") and any("notebook234d68765b" in x and x.startswith("✅") for x in lines)



def test_lora_report_checks_custom_notebook(monkeypatch):
    monkeypatch.setattr(config, "KAGGLE_API_TOKEN", "KGAT_x")
    monkeypatch.setattr(config, "KIDS_KAGGLE_SOURCES", "notebookd0f466fb59")
    monkeypatch.setattr(config, "KIDS_LORA", "dcn_lora_v1")
    monkeypatch.setattr(kaggle_worker, "_OUT_OK", {})
    monkeypatch.setattr(kaggle_worker, "_LORA_OK", {})
    monkeypatch.setattr(kaggle_api, "dataset_exists", lambda slug: slug != "rmvideo-kids-lora")
    monkeypatch.setattr(kaggle_api, "outputs", lambda slug: {"files": [{"fileName": "lora/dcn_lora_v1.safetensors"},
                                                                       {"fileName": "flux-schnell-nf4/x"}]})
    lines = kaggle_worker.resources_report(heal=False)
    assert "✅ LoRA nhân vật: trong notebook notebookd0f466fb59" in lines
