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
              "/api/autopost/{act}", "/api/bgm", "/kin/{token}/{name}", "/download/{id}/{k}", "/api/voice_ref",
              "/privacy", "/terms"):
        assert p in paths, p
    assert len(list(app.router.routes())) >= 29


def test_capcut_voice_options_found_after_move():
    d = http._with_options({"CAPCUT_VOICE_FEMALE": {}, "QUALITY": {}})
    assert len(d["CAPCUT_VOICE_FEMALE"]["options"]) > 5 and "options" not in d["QUALITY"]   # Voice.json ở gốc bot


def test_paths_still_point_at_bot_root_after_moving_code():
    """Mã dời vào rmvideo/ không được tìm tài nguyên cạnh chính nó: font tiêu đề / logo (thiếu font -> chữ tiêu đề mất
    dấu, logo không gắn — lỗi thật bắt được khi chạy thử trên Kaggle), tốc độ dựng đã đo, Voice.json, trang web."""
    import os
    from rmvideo.interfaces.web import routes_auth
    from rmvideo.media import ff, renderer
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert ff.ASSETS_DIR == os.path.join(root, "assets") and os.path.isfile(ff.FONT_PATH)
    assert os.path.dirname(ff.LOGO_PATH) == ff.ASSETS_DIR
    assert os.path.dirname(renderer.DEFAULT_SPEED_FILE) == root
    assert os.path.isfile(routes_auth.INDEX_HTML) and os.path.isfile(routes_auth.LOGIN_HTML)
    assert auth._KEY_FILE == os.path.join(root, ".session_key")
