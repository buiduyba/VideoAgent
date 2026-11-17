"""Báo video đứng + khóa thương hiệu + cờ 'soft' của bước chạy lệnh."""
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

from conftest import JOB, make_env
from vidagents.brand import brand_lock, brand_path, update_brand
from vidagents.jobs import JobStore
from vidagents.locks import LockTimeout, SlotLock
from vidagents.tools import Blocked, ToolError, ToolRunner
from vidagents.watchdog import check_stalled, stall_message


def later(jobs, job_id, minutes):
    return datetime.fromisoformat(jobs.get(job_id)["updated_at"]) + timedelta(minutes=minutes)


def collect():
    sent = []
    return sent, lambda uid, text: sent.append((uid, text))


# ───────── video đứng ─────────
def test_stalled_job_alerts_requester_and_admin_once(tmp_path):
    e = make_env(tmp_path, stall_minutes=60)
    jobs = e.deps.jobs
    jobs.update(JOB, status="running", stage="build")
    sent, send = collect()
    assert check_stalled(e.cfg, jobs, send, now=later(jobs, JOB, 30)) == []  # chưa đủ 60 phút
    assert check_stalled(e.cfg, jobs, send, now=later(jobs, JOB, 90)) == [JOB]
    assert {u for u, _ in sent} == {"1001", "9"}
    assert "ĐỨNG" in sent[0][1] and "dựng" in sent[0][1] and "90 phút" in sent[0][1] and JOB in sent[0][1]
    sent.clear()
    assert check_stalled(e.cfg, jobs, send, now=later(jobs, JOB, 200)) == [] and sent == []  # cùng một lần đứng: không báo lại
    time.sleep(1.1)  # sổ job ghi giờ theo giây: đợi sang giây mới để mốc cập nhật khác mốc đã báo
    jobs.update(JOB, stage="render")  # job nhúc nhích rồi lại đứng -> báo lại
    assert check_stalled(e.cfg, jobs, send, now=later(jobs, JOB, 90)) == [JOB]


def test_jobs_waiting_for_a_person_are_not_stalled(tmp_path):
    e = make_env(tmp_path, stall_minutes=10)
    for st in ("waiting_approval", "waiting_info", "blocked", "done", "cancelled"):
        e.deps.jobs.update(JOB, status=st)
        sent, send = collect()
        assert check_stalled(e.cfg, e.deps.jobs, send, now=later(e.deps.jobs, JOB, 1000)) == [], st


def test_failed_send_is_retried_next_round(tmp_path):
    e = make_env(tmp_path, stall_minutes=10)
    e.deps.jobs.update(JOB, status="running")
    now = later(e.deps.jobs, JOB, 60)

    def boom(uid, text):
        raise RuntimeError("mạng rớt")

    assert check_stalled(e.cfg, e.deps.jobs, boom, now=now) == []
    sent, send = collect()
    assert check_stalled(e.cfg, e.deps.jobs, send, now=now) == [JOB]


def test_stall_message_reports_nas_and_render_slots(tmp_path):
    e = make_env(tmp_path)
    job = {"job_id": "j", "status": "running", "stage": "render", "idle_minutes": 75}
    assert "truy cập được" in stall_message(e.cfg, job) and "0/1" in stall_message(e.cfg, job)
    SlotLock(e.cfg.locks_dir, e.cfg.render_slots).acquire("x")
    assert "đã kín" in stall_message(e.cfg, job)
    import shutil
    shutil.rmtree(e.cfg.footage_root)
    assert "KHÔNG truy cập được" in stall_message(e.cfg, job)


def test_old_jobs_table_gets_alerted_at_column(tmp_path):
    db = tmp_path / "old.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, requester_id TEXT, requester_name TEXT, brand TEXT, video_type TEXT,"
              " footage_dir TEXT, status TEXT, stage TEXT, note TEXT, created_at TEXT, updated_at TEXT)")
    c.execute("INSERT INTO jobs VALUES('a','1','n','b','t','f','running','build','','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    c.commit()
    c.close()
    js = JobStore(db)
    assert [j["job_id"] for j in js.stalled(60, datetime(2026, 1, 2))] == ["a"]
    js.mark_alerted("a")
    assert js.stalled(60, datetime(2026, 1, 2)) == []


# ───────── khóa thương hiệu ─────────
def test_brand_name_is_validated(tmp_path):
    e = make_env(tmp_path)
    with pytest.raises(ValueError):
        brand_path(e.cfg, "../x")
    with pytest.raises(ValueError):
        with brand_lock(e.cfg, "A B", "o"):
            pass


def test_brand_lock_is_exclusive_per_brand(tmp_path):
    e = make_env(tmp_path)
    with brand_lock(e.cfg, "femfresh", "job1"):
        with pytest.raises(LockTimeout):
            with brand_lock(e.cfg, "femfresh", "job2", timeout=0.3):
                pass
        with brand_lock(e.cfg, "oilan", "job3", timeout=0.3):  # thương hiệu khác không bị chặn
            pass
    with brand_lock(e.cfg, "femfresh", "job2", timeout=0.3):  # nhả khóa rồi thì vào được
        pass


def test_concurrent_updates_do_not_lose_edits_and_backups_are_capped(tmp_path):
    e = make_env(tmp_path)
    p = brand_path(e.cfg, "femfresh")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"claims_avoid": []}), encoding="utf-8")
    words = [f"tu{i}" for i in range(8)]
    errs = []

    def add(w):
        try:
            update_brand(e.cfg, "femfresh", lambda b: b["claims_avoid"].append(w), owner=f"job-{w}")
        except Exception as ex:  # noqa: BLE001
            errs.append(ex)

    ts = [threading.Thread(target=add, args=(w,)) for w in words]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert sorted(json.loads(p.read_text(encoding="utf-8"))["claims_avoid"]) == sorted(words)
    assert 1 <= len(list(p.parent.glob("brand.json.bak-*"))) <= 5
    assert not (p.parent / "brand.json.tmp").exists()


def test_failed_mutation_leaves_brand_file_untouched_and_releases_lock(tmp_path):
    e = make_env(tmp_path)
    p = brand_path(e.cfg, "b1")
    p.parent.mkdir(parents=True)
    p.write_text('{"a": 1}', encoding="utf-8")

    def bad(b):
        raise KeyError("lỗi giữa chừng")

    with pytest.raises(KeyError):
        update_brand(e.cfg, "b1", bad, "o")
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    update_brand(e.cfg, "b1", lambda b: b.update(a=2), "o2")  # khóa đã nhả
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 2}


# ───────── cờ của bước chạy lệnh: brand_lock, soft ─────────
def runner_with(e, steps):
    m = {"actions": {"go": {"B": steps}}}
    (e.agents / "edit" / "tools.json").write_text(json.dumps(m), encoding="utf-8")
    return ToolRunner(e.cfg, SlotLock(e.cfg.locks_dir, 1))


def py(code):
    return [sys.executable, "-c", code]


def test_step_with_brand_lock_waits_for_the_brand(tmp_path):
    e = make_env(tmp_path, lock_timeout_s=1)
    tr = runner_with(e, [{"name": "s", "cmd": py("print(1)"), "brand_lock": True}])
    v = {"brand": "femfresh"}
    with brand_lock(e.cfg, "femfresh", "ai-do-khac"):
        with pytest.raises(ToolError):
            tr.run("edit", "go", "B", v, cwd=tmp_path / "cwd", approvals={}, owner="j")
    assert tr.run("edit", "go", "B", v, cwd=tmp_path / "cwd", approvals={}, owner="j").steps == [{"name": "s", "code": 0}]
    assert not list(e.cfg.locks_dir.glob("brand-femfresh-*.lock"))  # chạy xong đã nhả khóa


def test_soft_step_failure_is_recorded_not_raised(tmp_path):
    e = make_env(tmp_path)
    steps = [{"name": "xem-truoc", "cmd": py("import sys; sys.exit(3)"), "soft": True, "retry": False},
             {"name": "ghi", "cmd": py("print('ok')")}]
    res = runner_with(e, steps).run("edit", "go", "B", {}, cwd=tmp_path / "c", approvals={}, owner="j")
    assert [s["name"] for s in res.steps] == ["ghi"]
    assert len(res.soft_failed) == 1 and res.soft_failed[0]["name"] == "xem-truoc" and "mã 3" in res.soft_failed[0]["why"]


def test_soft_step_missing_output_is_recorded(tmp_path):
    e = make_env(tmp_path)
    steps = [{"name": "xem-truoc", "cmd": py("print(1)"), "soft": True, "outputs": {"preview": str(tmp_path / "khong-co.mp4")}}]
    res = runner_with(e, steps).run("edit", "go", "B", {}, cwd=tmp_path / "c", approvals={}, owner="j")
    assert res.outputs == {} and "thiếu đầu ra" in res.soft_failed[0]["why"]


def test_non_soft_failure_still_raises_and_soft_never_hides_missing_approval(tmp_path):
    e = make_env(tmp_path)
    with pytest.raises(ToolError):
        runner_with(e, [{"name": "x", "cmd": py("import sys; sys.exit(1)"), "retry": False}]).run(
            "edit", "go", "B", {}, cwd=tmp_path / "c", approvals={}, owner="j")
    with pytest.raises(Blocked):
        runner_with(e, [{"name": "x", "cmd": py("print(1)"), "soft": True, "requires": "duyet_1"}]).run(
            "edit", "go", "B", {}, cwd=tmp_path / "c", approvals={}, owner="j")
