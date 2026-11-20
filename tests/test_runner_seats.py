"""Job đang chờ/chạy bước nặng không được chiếm chỗ của việc nhẹ: duyệt, trả lời, bước nhẹ của job khác không bị kẹt sau hàng chờ suất render."""
import contextlib
import threading
import time
from types import SimpleNamespace

from conftest import make_env

from vidagents.jobs import JobStore  # noqa: F401 - bảo đảm module nạp được
from vidagents.runner import JobRunner
from vidagents.tools import RunResult


class Graph:
    """Đồ thị giả: `fn(inp)` quyết định việc làm trong invoke; hook nặng lấy từ deps.tools như bước nặng thật."""

    def __init__(self, fn):
        self.fn = fn

    def invoke(self, inp, cfg):
        return self.fn(inp)

    def get_state(self, cfg):
        return SimpleNamespace(tasks=(), next=(), values={})


def start(runner, kind):
    return runner.start(requester_id="1001", requester_name="A", footage_dir="/x", video_type="koc-review", brand="demo", brief_text=kind)


class CountingSeats(threading.BoundedSemaphore):
    """Đếm số chỗ nhẹ đang bị giữ cùng lúc để chứng minh không vượt light_workers."""

    def __init__(self, n):
        super().__init__(n)
        self.in_use = self.peak = 0
        self._m = threading.Lock()

    def acquire(self, *a, **k):
        ok = super().acquire(*a, **k)
        with self._m:
            self.in_use += 1
            self.peak = max(self.peak, self.in_use)
        return ok

    def release(self, *a, **k):
        with self._m:
            self.in_use -= 1
        super().release(*a, **k)


def test_a_job_in_its_heavy_phase_does_not_block_a_light_job(tmp_path):
    e = make_env(tmp_path, light_workers=1)               # chỉ MỘT chỗ nhẹ: trước đây job nặng đang chờ là chặn cả hệ thống
    gate, in_heavy, light_done = threading.Event(), threading.Event(), threading.Event()

    def work(inp):
        if inp["brief_text"] == "heavy":
            with e.deps.tools.heavy_hook(inp["job_id"]):
                in_heavy.set()
                assert gate.wait(10)
        else:
            light_done.set()
    runner = JobRunner(Graph(work), e.deps)
    try:
        start(runner, "heavy")
        assert in_heavy.wait(5)
        start(runner, "light")
        assert light_done.wait(5), "việc nhẹ bị chặn sau job đang ở bước nặng"
        assert not gate.is_set()                          # job nặng vẫn đang chờ khi việc nhẹ đã xong
    finally:
        gate.set()
        runner.pool.shutdown(wait=True)
    assert runner._seats._value == 1                      # trả đủ chỗ, không rò


def test_light_work_never_exceeds_light_workers_even_with_many_heavy_jobs(tmp_path):
    e = make_env(tmp_path, light_workers=2)
    seats = CountingSeats(2)
    running, peak, m = [0], [0], threading.Lock()

    def work(inp):
        if inp["brief_text"] == "heavy":
            with e.deps.tools.heavy_hook(inp["job_id"]):
                time.sleep(0.05)
        with m:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.03)
        with m:
            running[0] -= 1
    runner = JobRunner(Graph(work), e.deps)
    runner._seats = seats
    try:
        for i in range(12):
            start(runner, "heavy" if i % 3 else "light")
    finally:
        runner.pool.shutdown(wait=True)
    assert seats.peak <= 2 and seats.in_use == 0          # giữ chỗ tối đa 2, xong thì trả hết
    assert 1 <= peak[0] <= 2                               # phần nhẹ (cả đoạn sau bước nặng) luôn nằm trong giới hạn 2 chỗ
    assert len(runner.d.jobs.list(None, active_only=False)) >= 12


def test_heavy_phase_error_blocks_the_job_without_leaking_a_seat(tmp_path):
    e = make_env(tmp_path, light_workers=1)

    def work(inp):
        if inp["brief_text"] == "boom":
            with e.deps.tools.heavy_hook(inp["job_id"]):
                raise RuntimeError("render hỏng")
    runner = JobRunner(Graph(work), e.deps)
    try:
        bad = start(runner, "boom")
        ok = start(runner, "fine")
    finally:
        runner.pool.shutdown(wait=True)
    assert e.deps.jobs.get(bad)["status"] == "blocked" and "render hỏng" in e.deps.jobs.get(bad)["note"]
    assert e.deps.jobs.get(ok)["status"] != "blocked"
    assert runner._seats._value == 1


def test_error_outside_the_heavy_phase_also_returns_the_seat(tmp_path):
    e = make_env(tmp_path, light_workers=1)
    runner = JobRunner(Graph(lambda inp: (_ for _ in ()).throw(ValueError("lỗi nhẹ")) if inp["brief_text"] == "boom" else None), e.deps)
    try:
        bad, ok = start(runner, "boom"), start(runner, "fine")
    finally:
        runner.pool.shutdown(wait=True)
    assert e.deps.jobs.get(bad)["status"] == "blocked" and runner._seats._value == 1 and e.deps.jobs.get(ok)["status"] != "blocked"


def test_thread_without_a_seat_passes_through_the_hook_untouched(tmp_path):
    e = make_env(tmp_path, light_workers=1)
    runner = JobRunner(Graph(lambda inp: None), e.deps)
    try:
        with runner._off_seat("J"):                       # CLI, test, luồng không do bộ chạy tạo: không có chỗ để nhả
            pass
        assert runner._seats._value == 1
    finally:
        runner.pool.shutdown(wait=True)


# ── ToolRunner: hook bao quanh đúng các bước ──
def _run_step(e, step, order):
    tools = e.deps.tools

    @contextlib.contextmanager
    def hook(owner):
        order.append("nhả-chỗ")
        try:
            yield
        finally:
            order.append("xin-lại-chỗ")

    @contextlib.contextmanager
    def slot(owner, **kw):
        order.append("vào-suất-render")
        try:
            yield
        finally:
            order.append("trả-suất-render")
    tools.heavy_hook = hook
    tools.locks.hold = slot
    tools._step("edit", "x", step, {"brand": "demo"}, cwd=e.cfg.work_root, approvals={}, owner="J", on_wait=None, res=RunResult())


def test_hook_wraps_heavy_steps_outside_the_render_slot_and_skips_light_steps(tmp_path):
    e = make_env(tmp_path)
    order = []
    _run_step(e, {"name": "nhe", "cmd": ["x"]}, order)
    assert order == []
    _run_step(e, {"name": "nang", "cmd": ["x"], "heavy": True}, order)
    # thứ tự bắt buộc: nhả chỗ nhẹ TRƯỚC khi chờ suất render; trả suất render XONG mới xin lại chỗ nhẹ (không giữ suất render để chờ chỗ)
    assert order == ["nhả-chỗ", "vào-suất-render", "trả-suất-render", "xin-lại-chỗ"]


def test_hook_also_wraps_brand_lock_steps(tmp_path):
    e = make_env(tmp_path)
    order = []
    _run_step(e, {"name": "thuong-hieu", "cmd": ["x"], "brand_lock": True}, order)
    assert order == ["nhả-chỗ", "xin-lại-chỗ"]


def test_runner_attaches_the_hook_and_tools_work_without_it(tmp_path):
    e = make_env(tmp_path)
    assert e.deps.tools.heavy_hook is None                # chưa có bộ chạy: bước nặng chạy như cũ
    e.deps.tools._step("edit", "x", {"name": "nang", "cmd": ["x"], "heavy": True}, {}, cwd=e.cfg.work_root, approvals={}, owner="J", on_wait=None, res=RunResult())
    runner = JobRunner(Graph(lambda inp: None), e.deps)
    try:
        assert e.deps.tools.heavy_hook == runner._off_seat
    finally:
        runner.pool.shutdown(wait=True)
