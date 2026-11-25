"""1.4.10: nút «Xóa» / «Xóa tất cả» trong Hàng đợi & kết quả — xoá job khỏi danh sách + jobs.db và xoá file của nó trên
server cho nhẹ; giữ job đang dở, video đang chờ đăng, truyện bé đang giữ chỗ Kaggle."""
import asyncio
import os
import time

import pytest

import autopost
import config
import kids_auto
import storage
from queue_manager import Job, JobQueue


@pytest.fixture
def env(tmp_path, monkeypatch):
    out, up = tmp_path / "outputs", tmp_path / "uploads_in"
    out.mkdir()
    up.mkdir()
    q = JobQueue(db_path=str(tmp_path / "jobs.db"))
    monkeypatch.setattr(storage, "OUT_DIR", str(out))
    monkeypatch.setattr(storage, "WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(up))
    monkeypatch.setattr(storage, "queue", q)
    monkeypatch.setattr(storage, "_awaiting_post", lambda strict=False: set())
    monkeypatch.setattr(kids_auto, "STATE_FILE", str(tmp_path / "kids_auto.json"))
    monkeypatch.setattr(autopost, "STATE_FILE", str(tmp_path / "autopost.json"))
    return q, out, up


def _file(path, mb=1, age=0):
    with open(path, "wb") as f:
        f.write(b"0" * (mb << 20))
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return str(path)


def _job(q, status, out, k, local=""):
    j = Job(url="https://x", mode="cook", duration=0, voice="v", status=status)
    j.output = _file(out / f"out_{j.id}_0.mp4")
    j.parts = [{"label": "Video", "path": j.output, "token": "t"},
               {"label": "Ảnh bìa", "path": _file(out / f"out_{j.id}_1.jpg"), "token": "t2"}]
    j.local = local
    q.jobs[j.id] = j
    q.save(j)
    return j


def test_delete_one_job_removes_files_and_rows(env):
    q, out, up = env
    src = _file(up / "up_abc")
    j = _job(q, "done", out, 0, local=src)
    ok, msg = storage.delete_job(j.id)
    assert ok and "giải phóng 3MB" in msg
    assert not os.path.exists(j.output) and not os.path.exists(src) and not os.listdir(out)
    assert j.id not in q.jobs and all(d["id"] != j.id for d in q.all_saved())
    assert storage.delete_job(j.id) == (False, f"Không thấy job #{j.id}.")


@pytest.mark.parametrize("status", ["queued", "processing", "review"])
def test_delete_refuses_active_job(env, status):
    q, out, _ = env
    j = _job(q, status, out, 0)
    ok, msg = storage.delete_job(j.id)
    assert not ok and "Hủy trước" in msg and os.path.exists(j.output) and j.id in q.jobs


def test_delete_refuses_video_waiting_to_post(env, monkeypatch):
    q, out, _ = env
    j = _job(q, "done", out, 0)
    monkeypatch.setattr(storage, "_awaiting_post", lambda strict=False: {os.path.abspath(j.output)})
    ok, msg = storage.delete_job(j.id)
    assert not ok and "chờ đăng YouTube" in msg and os.path.exists(j.output)
    monkeypatch.setattr(storage, "_awaiting_post", lambda strict=False: set())
    st = kids_auto.load_state()
    st["pending"] = {"job": j.id, "slot_ts": time.time() + 3600, "slot": "19:00"}
    kids_auto.save_state(st)
    ok, msg = storage.delete_job(j.id)
    assert not ok and "tự động đăng Fanpage" in msg


def test_delete_parked_kids_job_stops_tracking(env):
    q, out, _ = env
    j = _job(q, "error", out, 0)
    kids_auto.park_record({"job": j.id, "slug": "rmvideo-kids", "token": "t", "since": time.time(),
                           "expires_at": time.time() + 3600})
    ok, _ = storage.delete_job(j.id)
    assert ok and kids_auto.parked_info(j.id) is None


def test_clear_all_keeps_active_waiting_parked(env, monkeypatch):
    q, out, up = env
    done, err, canc = _job(q, "done", out, 0), _job(q, "error", out, 1), _job(q, "cancelled", out, 2)
    run = _job(q, "processing", out, 3, local=_file(up / "up_run"))
    rev = _job(q, "review", out, 4)
    wait = _job(q, "done", out, 5)
    parked = _job(q, "error", out, 6)
    kids_auto.park_record({"job": parked.id, "slug": "rmvideo-kids", "token": "t", "since": time.time(),
                           "expires_at": time.time() + 3600})
    old = Job(url="https://old", mode="cook", duration=0, voice="v", status="done")    # chỉ còn trong jobs.db
    q.save(old)
    orphan_old = _file(out / "out_1_0.mp4", age=7200)
    orphan_new = _file(out / "out_999_0.mp4")                    # đang giao kết quả: chưa thuộc job nào
    up_old, up_new = _file(up / "up_old", age=7200), _file(up / "up_new")
    monkeypatch.setattr(storage, "_awaiting_post", lambda strict=False: {os.path.abspath(wait.output)})
    res = storage.clear_jobs()
    assert res["deleted"] == 4 and res["kept"] == 4 and "Đã xoá 4 job" in res["msg"]
    gone = {done.id, err.id, canc.id, old.id}
    assert not (gone & set(q.jobs)) and not (gone & {d["id"] for d in q.all_saved()})
    for j in (done, err, canc):
        assert not os.path.exists(j.output)
    for j in (run, rev, wait, parked):
        assert os.path.exists(j.output) and j.id in q.jobs
    assert os.path.exists(run.local) and os.path.exists(orphan_new) and os.path.exists(up_new)
    assert not os.path.exists(orphan_old) and not os.path.exists(up_old)
    assert kids_auto.parked_info(parked.id) is not None


def test_job_files_never_outside_bot_dirs(env, tmp_path):
    outside = _file(tmp_path / "quan_trong.mp4")
    d = {"output": outside, "local": "/etc/passwd", "parts": [{"path": outside}], "images": ["../x"]}
    assert storage._job_files(d) == []


def test_web_routes_delete_and_clear(env, monkeypatch, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    import web_panel
    q, out, _ = env
    monkeypatch.setattr(config, "WEB_PASSWORD", "pw")
    monkeypatch.setattr(web_panel, "_KEY_FILE", str(tmp_path / "k"))
    a, b = _job(q, "done", out, 0), _job(q, "processing", out, 1)

    async def go():
        async with TestClient(TestServer(web_panel.build_app(q, {}))) as c:
            r = await c.post(f"/api/jobs/{b.id}/delete", headers={"X-Auth": "pw"})
            assert r.status == 409 and "Hủy trước" in (await r.json())["msg"]
            r = await c.post(f"/api/jobs/{a.id}/delete", headers={"X-Auth": "pw"})
            assert r.status == 200 and (await r.json())["ok"]
            r = await c.post("/api/jobs/clear", headers={"X-Auth": "pw"})
            d = await r.json()
            assert r.status == 200 and d["deleted"] == 0 and d["kept"] == 1
            assert (await c.post("/api/jobs/clear")).status == 401                   # phải đăng nhập
    asyncio.run(go())
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"),
                encoding="utf-8").read()
    assert "deleteJob(" in html and "clearJobs()" in html and "/api/jobs/clear" in html



# ---------------- rà soát 25/11 ----------------

def test_delete_approved_job_keeps_source_of_render_job(env):
    """Duyệt kịch bản: job render mới dùng chung file nguồn với job gốc «approved». Xoá job gốc (một / tất cả) không được
    xoá file nguồn đó."""
    q, out, up = env
    src = _file(up / "review_10.mp4")
    orig = _job(q, "approved", out, 0, local=src)
    render = Job(url="https://x", mode="cook", duration=0, voice="v", status="queued", local=src)
    q.jobs[render.id] = render
    q.save(render)
    ok, _ = storage.delete_job(orig.id)
    assert ok and os.path.exists(src) and not os.path.exists(orig.output)
    orig2 = _job(q, "approved", out, 1, local=src)
    storage.clear_jobs()
    assert os.path.exists(src) and orig2.id not in q.jobs and render.id in q.jobs


def test_job_ids_not_reused_after_clear(env, tmp_path):
    from queue_manager import JobQueue
    q, out, _ = env
    jobs = [_job(q, "done", out, k) for k in range(3)]
    storage.clear_jobs()
    q2 = JobQueue(db_path=str(tmp_path / "jobs.db"))        # bot khởi động lại
    new = Job(url="https://y", mode="cook", duration=0, voice="v")
    assert new.id > max(j.id for j in jobs)
    assert q2._max_issued() >= max(j.id for j in jobs)


def test_deleted_job_not_resurrected_by_late_save(env):
    q, out, _ = env
    j = _job(q, "error", out, 0)
    storage.delete_job(j.id)
    q.save(j)                                                # finally của worker ghi lại sau khi đã bấm Xóa
    assert q.saved(j.id) is None


def test_bad_rows_in_db_do_not_break_clear(env):
    q, _, _ = env
    q._db.execute("INSERT INTO jobs (id, data) VALUES (?, ?)", (900, "null"))
    q._db.execute("INSERT INTO jobs (id, data) VALUES (?, ?)", (901, "{hỏng"))
    q._db.commit()
    res = storage.clear_jobs()
    assert res["deleted"] == 2 and q.saved(900) is None


def test_delete_refuses_when_post_list_unreadable(env, monkeypatch):
    q, out, _ = env
    j = _job(q, "done", out, 0)

    def boom(strict=False):
        raise RuntimeError("youtube.json hỏng")
    monkeypatch.setattr(storage, "_awaiting_post", boom)
    ok, msg = storage.delete_job(j.id)
    assert not ok and "không đọc được" in msg and os.path.exists(j.output)
    assert "Chưa xoá gì" in storage.clear_jobs()["msg"] and os.path.exists(j.output)
