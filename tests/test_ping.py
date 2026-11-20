"""/ping và phản hồi cho lệnh lạ: bot không được im lặng (im lặng làm người dùng tưởng bot hỏng)."""
from conftest import JOB, make_env
from vidagents.bot import ping_text, unknown_command_text


def test_ping_reports_alive_and_counts_jobs_by_state(tmp_path):
    e = make_env(tmp_path)                       # có sẵn một job của người 1001
    e.deps.jobs.update(JOB, status="waiting_approval")
    t = ping_text(e.deps.jobs, busy=0, slots=1, uid="1001", is_admin=False)
    assert "Bot đang chạy ✓" in t and "Job của bạn: 0 đang chạy · 1 chờ duyệt/bổ sung · 0 bị dừng" in t and "Suất render: 0/1" in t
    assert "/video" in t and "/start" in t


def test_ping_flags_blocked_jobs_with_the_command_to_resume(tmp_path):
    e = make_env(tmp_path)
    e.deps.jobs.update(JOB, status="blocked")
    t = ping_text(e.deps.jobs, busy=1, slots=1, uid="1001", is_admin=False)
    assert "1 bị dừng" in t and f"⚠ {JOB} bị dừng: gõ /tieptuc {JOB}" in t and "Suất render: 1/1" in t


def test_ping_scope_admin_sees_everyone_but_others_only_their_own(tmp_path):
    e = make_env(tmp_path)
    e.deps.jobs.update(JOB, status="running")
    assert "Job tất cả: 1 đang chạy" in ping_text(e.deps.jobs, 0, 1, uid="9", is_admin=True)
    other = ping_text(e.deps.jobs, 0, 1, uid="2002", is_admin=False)
    assert "Job của bạn: 0 đang chạy" in other and JOB not in other        # không lộ job của người khác


def test_unknown_command_gets_a_helpful_reply():
    t = unknown_command_text("/viec")
    assert "không có lệnh /viec" in t and "/start" in t and "/ping" in t and "/video" in t
    assert "không có lệnh ?" in unknown_command_text("")
