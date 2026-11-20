"""Job dừng thì người giao VÀ mọi admin phải biết ngay, kể cả khi Telegram chập chờn hoặc bot vừa khởi động lại."""
import time
from types import SimpleNamespace

import pytest

from conftest import JOB, make_env, run_to_approval_1
from vidagents.notify import RecordingNotifier
from vidagents.outbox import MAX_AGE_S, Outbox, ReliableNotifier
from vidagents.runner import JobRunner


class Graph:
    def __init__(self, fn):
        self.fn = fn

    def invoke(self, inp, cfg):
        return self.fn(inp)

    def get_state(self, cfg):
        return SimpleNamespace(tasks=(), next=(), values={})


class Flaky(RecordingNotifier):
    """Telegram chập chờn: gửi lỗi cho một số người hoặc khi `down`."""

    def __init__(self, fail_for=(), down=False):
        super().__init__()
        self.fail_for, self.down, self.attempts = set(map(str, fail_for)), down, 0

    def send(self, user_id, text, files=(), buttons=None):
        self.attempts += 1
        if self.down or str(user_id) in self.fail_for:
            raise ConnectionError("Telegram không trả lời")
        super().send(user_id, text, files, buttons)


def boom(inp):
    raise RuntimeError("render hỏng")


def stop_job(e, runner, requester="1001"):
    jid = runner.start(requester_id=requester, requester_name="Lan", footage_dir="/x", video_type="koc-review", brand="demo", brief_text="b")
    runner.pool.shutdown(wait=True)
    return jid


def to(e, uid):
    return [(m[1], k) for m, k in zip(e.deps.notifier.messages, e.deps.notifier.keyboards) if m[0] == str(uid)]


# ───────────────────────── báo job dừng ─────────────────────────
def test_requester_and_every_admin_are_told_when_a_job_stops(tmp_path):
    e = make_env(tmp_path)
    e.cfg.admin_ids = ["9", "10"]
    jid = stop_job(e, JobRunner(Graph(boom), e.deps))
    req, a1, a2 = to(e, "1001"), to(e, "9"), to(e, "10")
    assert len(req) == 1 and f"/tieptuc {jid}" in req[0][0] and req[0][1]                  # người giao: có nút xử lý ngay
    assert len(a1) == 1 and len(a2) == 1                                                    # không chỉ admin đầu tiên
    for text, kb in (a1[0], a2[0]):
        assert jid in text and "render hỏng" in text and "Lan" in text and "RuntimeError" in text and not kb


def test_a_failing_send_to_the_requester_does_not_stop_the_admin_alert(tmp_path):
    e = make_env(tmp_path)
    e.deps.notifier = Flaky(fail_for={"1001"})
    jid = stop_job(e, JobRunner(Graph(boom), e.deps))
    assert [m for m in e.deps.notifier.messages if m[0] == "9" and jid in m[1]]             # trước đây: ngoại lệ làm mất luôn tin cho admin
    assert e.deps.jobs.get(jid)["status"] == "blocked"


def test_a_total_telegram_outage_still_marks_the_job_blocked_without_raising(tmp_path):
    e = make_env(tmp_path)
    e.deps.notifier = Flaky(down=True)
    jid = stop_job(e, JobRunner(Graph(boom), e.deps))
    j = e.deps.jobs.get(jid)
    assert j["status"] == "blocked" and "render hỏng" in j["note"]


def test_requester_who_is_also_an_admin_gets_one_message_with_buttons(tmp_path):
    e = make_env(tmp_path)
    e.cfg.admin_ids = ["1001", "9"]
    stop_job(e, JobRunner(Graph(boom), e.deps), requester="1001")
    mine = to(e, "1001")
    assert len(mine) == 1 and mine[0][1] and len(to(e, "9")) == 1


def test_legacy_admin_notify_callback_is_still_honoured(tmp_path):
    e = make_env(tmp_path)
    got = []
    stop_job(e, JobRunner(Graph(boom), e.deps, admin_notify=got.append))
    assert len(got) == 1 and "render hỏng" in got[0]
    assert not to(e, "9")                                                                  # có callback riêng thì không gửi thêm bản mặc định


def test_cost_cap_stop_is_reported_to_requester_and_admins(tmp_path):
    from vidagents.usage import CostCapExceeded
    e = make_env(tmp_path)
    e.cfg.admin_ids = ["9", "10"]

    def over(inp):
        raise CostCapExceeded("J", 2.5, 2.0)
    jid = stop_job(e, JobRunner(Graph(over), e.deps))
    for uid in ("1001", "9", "10"):
        assert any("chạm trần" in t and jid in t for t, _ in to(e, uid))


# ───────────────────────── bot khởi động lại giữa chừng ─────────────────────────
def test_jobs_running_when_the_bot_restarts_are_blocked_and_reported(tmp_path):
    e = make_env(tmp_path)
    runner = JobRunner(Graph(lambda i: None), e.deps)
    jobs = e.deps.jobs
    run = jobs.create_next("demo", requester_id="1001", requester_name="Lan", status="running", stage="build")
    que = jobs.create_next("demo", requester_id="1001", requester_name="Lan", status="queued", stage="new")
    wait = jobs.create_next("demo", requester_id="1001", status="waiting_approval", stage="duyet_1")
    done = jobs.create_next("demo", requester_id="1001", status="done")
    jobs.update(JOB, status="done")
    got = runner.recover_interrupted()
    runner.pool.shutdown(wait=True)
    assert sorted(got) == sorted([run, que])
    assert jobs.get(run)["status"] == "blocked" and "khởi động lại" in jobs.get(run)["note"]
    assert jobs.get(wait)["status"] == "waiting_approval" and jobs.get(done)["status"] == "done"      # việc chờ người hoặc đã xong không đụng tới
    texts = [t for t, _ in to(e, "1001")]
    assert any(run in t and f"/tieptuc {run}" in t for t in texts)
    assert any("khởi động lại" in t and run in t for t, _ in to(e, "9"))                              # admin cũng biết
    assert runner.recover_interrupted() == []                                                       # gọi lại không báo trùng


# ───────────────────────── hộp thư đi ─────────────────────────
@pytest.fixture
def box(tmp_path):
    clock = [1000.0]
    return Outbox(tmp_path / "jobs.sqlite", now=lambda: clock[0]), clock


def test_failed_message_is_kept_and_delivered_once_telegram_is_back(box):
    outbox, clock = box
    inner = Flaky(down=True)
    n = ReliableNotifier(inner, outbox, log=lambda s: None)
    n.send("1001", "[J1] job dừng", ["a.mp4"], buttons=[[("▶ Chạy tiếp", "j|tt|J1")]])           # không ném lỗi
    assert outbox.pending() == 1 and inner.messages == []
    clock[0] += 1
    assert n.flush() == 0                                                                         # chưa tới hạn thử lại
    inner.down = False
    clock[0] += 10
    assert n.flush() == 1 and outbox.pending() == 0
    assert inner.messages == [("1001", "[J1] job dừng", ["a.mp4"])] and inner.keyboards == [[[("▶ Chạy tiếp", "j|tt|J1")]]]
    assert n.flush() == 0                                                                         # không gửi lại lần hai


def test_retry_backoff_grows_and_survives_a_restart(box, tmp_path):
    outbox, clock = box
    inner = Flaky(down=True)
    n = ReliableNotifier(inner, outbox, log=lambda s: None)
    n.send("1", "x")
    waits = []
    for _ in range(4):
        row = outbox._c().execute("SELECT next_at FROM outbox").fetchone()
        waits.append(round(row["next_at"] - clock[0]))
        clock[0] = row["next_at"] + 0.1
        n.flush()
    assert waits == [5, 30, 120, 600]                                                             # giãn dần, không dồn dập
    again = Outbox(tmp_path / "jobs.sqlite", now=lambda: clock[0])                                # bot khởi động lại: tin vẫn còn
    assert again.pending() == 1


def test_messages_older_than_a_day_are_dropped(box):
    outbox, clock = box
    n = ReliableNotifier(Flaky(down=True), outbox, log=lambda s: None)
    n.send("1", "cũ")
    clock[0] += MAX_AGE_S + 100
    n.flush()
    assert outbox.pending() == 0


def test_a_bot_that_is_not_connected_yet_keeps_messages_instead_of_dropping_them(box):
    outbox, _ = box
    inner = SimpleNamespace(app=None, sent=[], send=lambda *a, **k: inner.sent.append(a))       # giống TelegramNotifier trước khi attach
    n = ReliableNotifier(inner, outbox, log=lambda s: None)
    n.send("1", "báo sớm")
    assert outbox.pending() == 1 and inner.sent == []
    inner.app = object()
    n.outbox.now = lambda: 10**9
    assert n.flush() == 1 and inner.sent[0][:2] == ("1", "báo sớm")


def test_other_attributes_are_forwarded_to_the_real_notifier(box):
    outbox, _ = box
    inner = SimpleNamespace(markup=lambda kb: ("markup", kb), app=True)
    assert ReliableNotifier(inner, outbox).markup([["x"]]) == ("markup", [["x"]])


def test_a_telegram_hiccup_no_longer_kills_the_job_that_was_only_reporting_progress(tmp_path):
    e = make_env(tmp_path)
    flaky = Flaky(down=True)
    e.deps.notifier = flaky                                                                       # KHÔNG bọc: lỗi gửi tin ném thẳng vào node
    with pytest.raises(ConnectionError):
        run_to_approval_1(e)
    e2 = make_env(tmp_path / "b")
    e2.deps.notifier = ReliableNotifier(Flaky(down=True), Outbox(tmp_path / "b" / "o.sqlite"), log=lambda s: None)
    run_to_approval_1(e2)                                                                         # có bọc: job đi hết, tin chờ gửi lại
    assert e2.deps.notifier.outbox.pending() > 0
