"""Cấp mã job khi nhiều người giao cùng lúc: không bao giờ trùng, không job nào bị mất vì va chạm mã."""
import sqlite3
import threading
from datetime import datetime
from types import SimpleNamespace

from conftest import make_env

from vidagents.jobs import JobStore
from vidagents.runner import JobRunner

DAY = datetime.now().strftime("%Y%m%d")


class FakeGraph:
    """Đồ thị giả chạy xong ngay: bài thử này chỉ quan tâm tới việc cấp mã và ghi sổ."""

    def invoke(self, inp, cfg):
        return None

    def get_state(self, cfg):
        return SimpleNamespace(tasks=(), next=(), values={})


def hammer(fn, n):
    """Chạy n luồng cùng lúc, dồn vào một rào chắn để va chạm xảy ra thật."""
    barrier, out, errs = threading.Barrier(n), [], []

    def work(i):
        barrier.wait()
        try:
            out.append(fn(i))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")
    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out, errs


def test_30_threads_get_30_unique_contiguous_ids(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    out, errs = hammer(lambda i: store.create_next("demo", requester_id=str(1000 + i % 10)), 30)
    assert not errs and sorted(out) == [f"{DAY}-demo-{n:02d}" for n in range(1, 31)]
    assert len(store.list(None, active_only=False)) == 30


def test_two_stores_on_one_file_like_two_processes_do_not_collide(tmp_path):
    a, b = JobStore(tmp_path / "jobs.sqlite"), JobStore(tmp_path / "jobs.sqlite")
    out, errs = hammer(lambda i: (a if i % 2 else b).create_next("demo", requester_id="1"), 24)
    assert not errs and len(set(out)) == 24 and len(a.list(None, active_only=False)) == 24


def test_brands_count_independently(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.create_next("demo") == f"{DAY}-demo-01"
    assert store.create_next("oillan") == f"{DAY}-oillan-01"
    assert store.create_next("demo") == f"{DAY}-demo-02"


def test_a_deleted_job_never_makes_the_next_id_collide(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    ids = [store.create_next("demo") for _ in range(3)]
    with sqlite3.connect(str(tmp_path / "jobs.sqlite")) as c:
        c.execute("DELETE FROM jobs WHERE job_id=?", (ids[1],))      # xóa job giữa chừng (dọn tay): đếm số dòng sẽ ra mã đã có
    assert store.create_next("demo") == f"{DAY}-demo-04"


def test_brand_prefix_is_matched_literally_not_as_a_pattern(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    store.create_next("a_b")
    assert store.create_next("axb") == f"{DAY}-axb-01"                # "_" trong LIKE khớp một ký tự bất kỳ: không được lẫn mã hai thương hiệu


def test_next_id_preview_matches_what_create_next_would_give(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.next_id("demo") == f"{DAY}-demo-01"
    store.create_next("demo")
    assert store.next_id("demo") == f"{DAY}-demo-02"


def test_runner_start_from_30_threads_creates_30_distinct_jobs(tmp_path):
    e = make_env(tmp_path)
    runner = JobRunner(FakeGraph(), e.deps)
    try:
        out, errs = hammer(lambda i: runner.start(requester_id=str(1000 + i % 10), requester_name=f"Người {i % 10}", footage_dir="/x",
                                                  video_type="koc-review", brand="demo", brief_text="Video demo"), 30)
    finally:
        runner.pool.shutdown(wait=True)
    assert not errs and len(set(out)) == 30
    rows = [j for j in e.deps.jobs.list(None, active_only=False) if j["job_id"].startswith(f"{DAY}-demo-")]
    assert len(rows) == 30 and {j["requester_id"] for j in rows} == {str(1000 + k) for k in range(10)}


def test_job_id_uses_a_clean_slug_but_the_ledger_keeps_the_original_brand(tmp_path):
    e = make_env(tmp_path)
    runner = JobRunner(FakeGraph(), e.deps)
    try:
        jid = runner.start(requester_id="1001", requester_name="A", footage_dir="/x", video_type="koc-review", brand="quảng cáo", brief_text="b")
    finally:
        runner.pool.shutdown(wait=True)
    assert jid == f"{DAY}-job-01" and e.deps.jobs.get(jid)["brand"] == "quảng cáo"        # mã job không dấu để trang theo dõi mở được
