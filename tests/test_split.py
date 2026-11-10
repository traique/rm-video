"""Cắt phần video dài: dời điểm cắt về khung khoá để chép luồng, không mã hoá lại."""
import asyncio
import subprocess

import core_video


def test_snap_to_keyframes():
    kfs = [0.0, 2.0, 4.0, 6.0, 8.0]
    assert core_video.snap_to_keyframes([0.0, 3.8, 6.5, 9.0], kfs) == [0.0, 4.0, 6.5, 9.0]   # 6.5 lệch 0.5s: giữ
    assert core_video.snap_to_keyframes([0.0, 9.0], kfs) == [0.0, 9.0]
    assert core_video.snap_to_keyframes([0.0, 3.8, 9.0], []) == [0.0, 3.8, 9.0]
    assert core_video.is_keyframe(4.0, kfs) and core_video.is_keyframe(0.0, []) and not core_video.is_keyframe(3.9, kfs)


def test_cut_copies_at_keyframe(tmp_path):
    src = str(tmp_path / "s.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=12:s=320x240:r=25", "-f", "lavfi",
                    "-i", "sine=f=300:d=12", "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-sc_threshold", "0",
                    "-c:a", "aac", "-shortest", src], check=True)

    async def go():
        kfs = await core_video.keyframe_times(src)
        assert kfs[:3] == [0.0, 2.0, 4.0]
        b = core_video.snap_to_keyframes([0.0, 5.8, 12.0], kfs)
        assert b == [0.0, 6.0, 12.0]
        p1, p2 = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
        assert await core_video.cut(src, p1, b[1], b[2], copy=core_video.is_keyframe(b[1], kfs)) == "copy"
        assert abs(await core_video.probe_duration(p1) - 6.0) < 0.1
        assert await core_video.cut(src, p2, 5.1, 7.0, copy=False) == "encode"
    asyncio.run(go())
