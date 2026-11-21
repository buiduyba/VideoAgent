"""Job xếp hàng chờ suất render không bị báo "đứng" sai; job treo thật vẫn bị báo."""
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from conftest import make_env

from vidagents.locks import SlotLock
from vidagents.runner import JobRunner
from vidagents.tools import RunResult


class Graph:
    def invoke(self, inp, cfg):
        return None

    def get_state(self, cfg):
        return SimpleNamespace(tasks=(), next=(), values={})


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ── khóa render: nhịp chỉ bắn lúc CHỜ ──
def test_tick_fires_periodically_only_while_waiting(tmp_path):
    lk = SlotLock(tmp_path, slots=1, poll_s=0.01, tick_s=0.05)
    ticks = []
    with lk.hold("A", on_tick=lambda: ticks.append("rảnh")):        # suất rảnh: không phải chờ nên không có nhịp
        pass
    assert ticks == []
    held, release, got = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with lk.hold("H"):
            held.set()
            release.wait(5)

    def waiter():
        with lk.hold("W", on_tick=lambda: ticks.append("chờ")):
            got.set()
    th, tw = threading.Thread(target=holder), threading.Thread(target=waiter)
    th.start()
    assert held.wait(5)
    tw.start()
    t0 = time.time()
    assert wait_until(lambda: len(ticks) >= 3, 10.0)               # đợi đủ 3 nhịp thay vì ngủ một khoảng cố định (máy bận thì khoảng ngủ bị kéo dài, số nhịp lệch)
    waiting_ticks, elapsed = len(ticks), time.time() - t0
    release.set()
    assert got.wait(5)
    th.join(), tw.join()
    assert set(ticks) == {"chờ"} and waiting_ticks <= elapsed / 0.05 + 2     # không nhiều hơn một nhịp mỗi 0,05 giây
    after = len(ticks)
    time.sleep(0.2)
    assert len(ticks) == after                                     # có suất rồi thì không còn nhịp nào nữa


def test_a_failing_tick_never_breaks_the_wait(tmp_path):
    lk = SlotLock(tmp_path, slots=1, poll_s=0.01, tick_s=0.02)
    held, release, got = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with lk.hold("H"):
            held.set()
            release.wait(5)

    def boom():
        raise RuntimeError("sổ job đang bận")

    def waiter():
        with lk.hold("W", on_tick=boom):
            got.set()
    th, tw = threading.Thread(target=holder), threading.Thread(target=waiter)
    th.start()
    assert held.wait(5)
    tw.start()
    time.sleep(0.15)
    release.set()
    assert got.wait(5), "nhịp lỗi làm hỏng việc chờ suất"
    th.join(), tw.join()


# ── nối vào sổ job: giờ cập nhật và bộ canh ──
def _old_job(e, hours=2, status="running"):
    jid = e.deps.jobs.create_next("demo", requester_id="1001", status=status)
    old = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with sqlite3.connect(str(e.cfg.jobs_db)) as c:
        c.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (old, jid))
    return jid


def stalled_ids(e):
    return [j["job_id"] for j in e.deps.jobs.stalled(60)]


def test_job_waiting_for_the_render_slot_is_not_reported_as_stalled_but_a_hung_one_still_is(tmp_path):
    e = make_env(tmp_path)
    runner = JobRunner(Graph(), e.deps)
    try:
        tools = e.deps.tools
        tools.locks.tick_s, tools.locks.poll_s = 0.05, 0.01
        waiting, hung = _old_job(e), _old_job(e)                     # cả hai "2 giờ không cập nhật", trạng thái đang chạy
        assert {waiting, hung} <= set(stalled_ids(e))                # điều kiện đầu: cả hai đều bị coi là đứng
        done = threading.Event()

        def heavy():
            tools._step("edit", "build", {"name": "nang", "cmd": ["x"], "heavy": True}, {}, cwd=e.cfg.work_root, approvals={}, owner=waiting,
                        on_wait=None, res=RunResult())
            done.set()
        with tools.locks.hold("NGUOI-KHAC"):                          # suất render đang bị job khác giữ
            t = threading.Thread(target=heavy)
            t.start()
            assert wait_until(lambda: waiting not in stalled_ids(e)), "job chờ render vẫn bị coi là đứng"
            assert hung in stalled_ids(e)                             # job không chờ gì mà 2 giờ không nhúc nhích: vẫn phải bị báo
            assert not done.is_set()
        assert done.wait(5)
        t.join()
    finally:
        runner.pool.shutdown(wait=True)


def test_no_heartbeat_once_the_job_holds_the_slot_and_runs(tmp_path):
    e = make_env(tmp_path)
    runner = JobRunner(Graph(), e.deps)
    try:
        tools = e.deps.tools
        tools.locks.tick_s, tools.locks.poll_s = 0.02, 0.01
        jid = _old_job(e)
        beats = []
        tools.wait_tick = lambda owner: beats.append(owner)
        gate = threading.Event()
        orig = tools._exec
        tools._exec = lambda *a, **k: (gate.wait(5), (0, "ok"))[1]   # bước nặng đang CHẠY (đã có suất), kẹt tại đây
        t = threading.Thread(target=lambda: tools._step("edit", "build", {"name": "nang", "cmd": ["x"], "heavy": True}, {}, cwd=e.cfg.work_root,
                                                        approvals={}, owner=jid, on_wait=None, res=RunResult()))
        t.start()
        time.sleep(0.3)
        assert beats == []                                            # có suất ngay thì không phải chờ, không nhịp: job treo khi chạy vẫn lộ ra
        assert jid in stalled_ids(e)
        gate.set()
        t.join()
        tools._exec = orig
    finally:
        runner.pool.shutdown(wait=True)


def test_runner_wires_the_heartbeat_and_survives_a_failing_ledger(tmp_path):
    e = make_env(tmp_path)
    assert e.deps.tools.wait_tick is None
    runner = JobRunner(Graph(), e.deps)
    try:
        assert e.deps.tools.wait_tick == runner._heartbeat
        jid = _old_job(e)
        runner._heartbeat(jid)
        assert jid not in stalled_ids(e)                              # một nhịp là đủ làm mới giờ cập nhật

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        e.deps.jobs.update = boom
        runner._heartbeat(jid)                                        # sổ job lỗi: nuốt, không ném ra làm hỏng việc chờ
    finally:
        runner.pool.shutdown(wait=True)
