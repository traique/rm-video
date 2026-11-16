"""Phase 4-5: mặt tiền tương thích (pipeline.py, web_panel.py) và web panel sau khi tách thành các nhóm route."""
import pipeline
import web_panel
from queue_manager import JobQueue
from rmvideo.app.flow import delivery, remake
from rmvideo.interfaces.web import auth, http


def test_facade_patch_reaches_real_module_and_undo_restores(monkeypatch, tmp_path):
    old_key, old_reel, old_out = auth._KEY_FILE, remake.REEL_MAX, delivery.OUT_DIR
    with monkeypatch.context() as m:
        m.setattr(web_panel, "_KEY_FILE", str(tmp_path / "k"))
        m.setattr(pipeline, "REEL_MAX", 10)
        m.setattr(pipeline, "OUT_DIR", str(tmp_path))
        assert auth._KEY_FILE == str(tmp_path / "k") and web_panel._KEY_FILE == auth._KEY_FILE
        assert remake.REEL_MAX == 10 and delivery.OUT_DIR == str(tmp_path)      # vá có tác dụng nơi tên được dùng
    assert (auth._KEY_FILE, remake.REEL_MAX, delivery.OUT_DIR) == (old_key, old_reel, old_out)
    assert web_panel.valid_session is auth.valid_session and pipeline.process.__module__ == "rmvideo.app.flow.dispatch"


def test_web_app_registers_every_route(tmp_path):
    app = web_panel.build_app(JobQueue(db_path=str(tmp_path / "q.db")), {})
    paths = {r.resource.canonical for r in app.router.routes()}
    for p in ("/", "/login", "/health", "/api/login", "/api/jobs", "/api/events", "/api/upload", "/api/settings",
              "/api/autopost/{act}", "/api/bgm", "/kin/{token}/{name}", "/download/{id}/{k}", "/api/voice_ref"):
        assert p in paths, p
    assert len(list(app.router.routes())) >= 29


def test_capcut_voice_options_found_after_move():
    d = http._with_options({"CAPCUT_VOICE_FEMALE": {}, "QUALITY": {}})
    assert len(d["CAPCUT_VOICE_FEMALE"]["options"]) > 5 and "options" not in d["QUALITY"]   # Voice.json ở gốc bot
