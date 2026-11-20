"""Gõ nhầm mã job (vd. job hôm qua đã xong) thì bot phải báo lỗi chứ không được đẩy tin vào job đó rồi nói "Đã ghi nhận"."""
import pytest

from vidagents.bot import resume_block


def job(status, jid="20261120-saphir-01"):
    return {"job_id": jid, "status": status}


@pytest.mark.parametrize("action,status", [
    ("answer", "waiting_info"), ("approve", "waiting_approval"), ("revise", "waiting_approval"),
    ("continue", "waiting_approval"), ("cancel", "running"), ("cancel", "waiting_info"), ("cancel", "blocked"), ("cancel", "queued"),
])
def test_valid_command_for_the_jobs_state_passes(action, status):
    assert resume_block(job(status), action) == ""


def test_bosung_to_a_finished_job_is_refused_and_points_at_the_job_that_is_waiting():
    # đúng tình huống thật: trả lời hỏi thông tin của job 20261120 nhưng gõ mã job 20261119 đã xong
    msg = resume_block(job("done", "20261119-saphir-01"), "answer", ["20261120-saphir-01"])
    assert msg.startswith("Chưa gửi gì cả") and "20261119-saphir-01 đã xong" in msg
    assert "không có việc nào cần /bosung" in msg and "Job của bạn đang chờ /bosung: 20261120-saphir-01" in msg


@pytest.mark.parametrize("action,status,expect", [
    ("approve", "waiting_info", "/bosung 20261120-saphir-01 <nội dung>"),   # duyệt nhầm lúc job đang hỏi thông tin
    ("answer", "waiting_approval", "/duyet 20261120-saphir-01"),            # bổ sung nhầm lúc job chờ duyệt
    ("revise", "running", "đang chạy"),
    ("approve", "blocked", "/tieptuc 20261120-saphir-01"),
    ("approve", "cancelled", "đã hủy"),
])
def test_wrong_command_for_the_state_says_what_to_type_instead(action, status, expect):
    msg = resume_block(job(status), action)
    assert msg.startswith("Chưa gửi gì cả") and expect in msg


def test_cancelling_a_finished_or_cancelled_job_is_refused():
    assert "không còn gì để hủy" in resume_block(job("done"), "cancel")
    assert "đã hủy" in resume_block(job("cancelled"), "cancel")


def test_no_candidates_means_no_misleading_suggestion():
    assert "Job của bạn đang chờ" not in resume_block(job("done"), "answer", [])
