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
