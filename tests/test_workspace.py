"""Thư mục làm việc riêng từng job: chép footage từ NAS, tách biệt giữa các job, chép kết quả về, dọn sau hạn."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import JOB, make_env, pay, run_to_approval_1
from vidagents import workspace as W
from vidagents.nodes import make_nodes


def intake(e, job_id, **over):
    st = {**e.state, "job_id": job_id, **over}
    e.deps.jobs.create(job_id, requester_id="1001", brand="demo", video_type="koc-review", footage_dir=str(e.clip))
    return st, make_nodes(e.deps)["intake"](st)


def test_two_jobs_same_footage_do_not_collide(tmp_path):
    e = make_env(tmp_path)
    _, a = intake(e, "20261106-demo-07")
    _, b = intake(e, "20261106-demo-08")
    assert a["work_dir"] != b["work_dir"] and a["edit_dir"] != b["edit_dir"]
    # mỗi job có bản clip riêng và thư mục edit riêng
    assert (Path(a["work_dir"]) / "clip.mp4").read_bytes() == b"demo"
    (Path(a["edit_dir"]) / "edl.json").write_text("{\"job\": \"a\"}", encoding="utf-8")
    assert not (Path(b["edit_dir"]) / "edl.json").exists()
    # footage gốc trên NAS không bị ghi gì thêm
    assert not (e.clip / "edit").exists()
    assert a["source_dir"] == str(e.clip.resolve()) and a["footage_dir"] == a["work_dir"]


def test_only_allowed_files_copied_and_subdirs_reported(tmp_path):
    e = make_env(tmp_path)
    (e.clip / "ghi-chu.txt").write_text("x", encoding="utf-8")
    (e.clip / "thu-muc-con").mkdir()
    (e.clip / "thu-muc-con" / "z.mp4").write_bytes(b"z")
    (e.clip / "edit").mkdir()  # kết quả của lần dựng cũ
    (e.clip / "edit" / "cu.mp4").write_bytes(b"old")
    _, r = intake(e, "20261106-demo-03")
    names = sorted(p.name for p in Path(r["work_dir"]).iterdir() if p.is_file())
    assert names == ["clip.mp4"]
    assert not (Path(r["work_dir"]) / "thu-muc-con").exists() and not (Path(r["work_dir"]) / "edit" / "cu.mp4").exists()
    assert any("bỏ qua 1 thư mục con" in m[1] for m in e.notifier.messages)


def test_stage_is_idempotent_and_no_part_files(tmp_path):
    e = make_env(tmp_path)
    r1 = W.stage_footage(e.cfg, "j1", e.clip)
    r2 = W.stage_footage(e.cfg, "j1", e.clip)
    assert (r1.copied, r1.reused) == (1, 0) and (r2.copied, r2.reused) == (0, 1)
    assert not list(r1.work_dir.glob("*.part"))
    (e.clip / "clip.mp4").write_bytes(b"demo-moi-dai-hon")  # footage đổi -> chép lại
    assert W.stage_footage(e.cfg, "j1", e.clip).copied == 1


def test_not_enough_disk_cancels_before_copy(tmp_path, monkeypatch):
    e = make_env(tmp_path)
    monkeypatch.setattr(W, "free_bytes", lambda p: 0)
    st, r = intake(e, "20261106-demo-04")
    assert r["status"] == "cancelled" and "không đủ chỗ" in r["cancel_reason"]
    assert not (e.cfg.work_path / "20261106-demo-04" / "clip.mp4").exists()
    assert e.deps.jobs.get("20261106-demo-04")["status"] == "cancelled"


def test_empty_footage_and_nas_error_cancel(tmp_path, monkeypatch):
    e = make_env(tmp_path)
    (e.clip / "clip.mp4").unlink()
    _, r = intake(e, "20261106-demo-05")
    assert r["status"] == "cancelled" and "không có file" in r["cancel_reason"]
    (e.clip / "clip.mp4").write_bytes(b"demo")

    def boom(*a, **k):
        raise OSError("mất kết nối")

    monkeypatch.setattr(W, "plan", boom)
    _, r = intake(e, "20261106-demo-06")
    assert r["status"] == "cancelled" and "NAS" in r["cancel_reason"]


def test_full_flow_delivers_back_to_source_and_marks(tmp_path):
    e = make_env(tmp_path)
    run_to_approval_1(e)
    e.mr.resume(e.state, pay("approve"))
    assert e.mr.resume(e.state, pay("approve")) == "done"
    dest = e.clip / "edit" / JOB
    assert (dest / f"{e.state['video_name']}-1080x1920.mp4").exists()
    assert (dest / "brief.md").exists()
    assert (Path(e.state["work_dir"]) / W.MARKER).exists()
    msg = [m[1] for m in e.notifier.messages if "XONG" in m[1]][-1]
    assert str(dest) in msg and "giữ thêm 24 giờ" in msg


def test_delivery_failure_keeps_work_dir_unmarked(tmp_path, monkeypatch):
    e = make_env(tmp_path)

    def boom(*a, **k):
        raise OSError("NAS không ghi được")

    monkeypatch.setattr(W, "deliver_results", boom)
    run_to_approval_1(e)
    e.mr.resume(e.state, pay("approve"))
    assert e.mr.resume(e.state, pay("approve")) == "done"
    assert not (Path(e.state["work_dir"]) / W.MARKER).exists()
    assert any("KHÔNG bị dọn" in m[1] for m in e.notifier.messages)
    # cho dù quá hạn, job xong mà chưa về NAS thì không dọn
    later = datetime.now() + timedelta(days=3)
    assert W.cleanup_work(e.cfg, e.deps.jobs, now=later) == []
    assert Path(e.state["work_dir"]).exists()


def _mk_job(e, jid, status, hours_ago, marker=False, now=None):
    now = now or datetime.now()
    d = e.cfg.work_path / jid
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"x")
    if marker:
        (d / W.MARKER).write_text("ok", encoding="utf-8")
    e.deps.jobs.create(jid, requester_id="1001", brand="demo", video_type="koc-review", status=status)
    import sqlite3
    t = (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with sqlite3.connect(str(e.cfg.jobs_db)) as c:
        c.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (t, jid))
    return d


def test_cleanup_rules(tmp_path):
    e = make_env(tmp_path)
    now = datetime.now()
    keep = {
        "j-run": _mk_job(e, "j-run", "running", 100),                     # đang chạy: không bao giờ dọn
        "j-wait": _mk_job(e, "j-wait", "waiting_approval", 100),          # chờ duyệt: không dọn
        "j-recent": _mk_job(e, "j-recent", "done", 23, marker=True),      # xong chưa đủ 24 giờ
        "j-nomark": _mk_job(e, "j-nomark", "done", 100, marker=False),    # kết quả chưa về NAS
    }
    gone = {
        "j-old": _mk_job(e, "j-old", "done", 25, marker=True),
        "j-cancel": _mk_job(e, "j-cancel", "cancelled", 30),
    }
    orphan = e.cfg.work_path / "khong-phai-job"
    orphan.mkdir()
    removed = W.cleanup_work(e.cfg, e.deps.jobs, now=now)
    assert sorted(removed) == ["j-cancel", "j-old"]
    assert all(p.exists() for p in keep.values()) and orphan.exists()
    assert not any(p.exists() for p in gone.values())


def test_work_keep_hours_is_configurable(tmp_path):
    e = make_env(tmp_path, work_keep_hours=2)
    _mk_job(e, "j-a", "done", 3, marker=True)
    assert W.cleanup_work(e.cfg, e.deps.jobs) == ["j-a"]
