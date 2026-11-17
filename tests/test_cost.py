"""Chi phí AI theo job: đếm token + tiền, trần chi phí, dừng-hỏi-người-giao khi chạm trần."""
import pytest

from conftest import JOB, make_env, pay, run_to_approval_1
from vidagents.bot import parse_cap, status_line
from vidagents.fakes import DemoLLM
from vidagents.runner import JobRunner
from vidagents.usage import CostCapExceeded, UsageMeter, job_context, price_usd

PER_CALL = (1000 * 2.0 + 200 * 10.0) / 1_000_000  # 1000 token vào + 200 ra với giá Sonnet mặc định = $0,004


def finish(e):
    run_to_approval_1(e)
    e.mr.resume(e.state, pay("approve"))
    assert e.mr.resume(e.state, pay("approve")) == "done"


def test_cost_recorded_per_job_and_reported_on_delivery(env):
    e = env
    finish(e)
    t = e.deps.meter.job_total(JOB)
    assert t["calls"] == len(e.llm.calls) > 5
    assert t["usd"] == pytest.approx(t["calls"] * PER_CALL)
    assert t["input_tokens"] == t["calls"] * 1000 and t["output_tokens"] == t["calls"] * 200
    assert {"orchestrator", "script", "critic", "edit"} <= set(t["by_agent"])
    done = next(m[1] for m in e.notifier.messages if "XONG" in m[1])
    assert "Chi phí AI: ~$" in done and "lượt gọi" in done


def test_two_jobs_are_metered_separately(tmp_path):
    m = UsageMeter(tmp_path / "j.sqlite")
    with job_context("a"):
        m.record("script", "claude-sonnet-5-5", 1000, 0)
    with job_context("b"):
        m.record("script", "claude-sonnet-5-5", 3000, 0)
    assert m.job_total("a")["usd"] == pytest.approx(0.002) and m.job_total("b")["usd"] == pytest.approx(0.006)
    assert m.job_total("zzz")["calls"] == 0


def test_unknown_model_is_counted_but_never_priced_by_guessing(tmp_path):
    m = UsageMeter(tmp_path / "j.sqlite")
    assert price_usd(m.pricing, "claude-moi-chua-co-gia", 10, 10) is None
    m.record("script", "claude-moi-chua-co-gia", 5000, 100, job_id="j")
    t = m.job_total("j")
    assert t["usd"] == 0.0 and t["unpriced_models"] == ["claude-moi-chua-co-gia"] and t["input_tokens"] == 5000
    assert "chưa có giá" in m.summary_line("j")


def test_pricing_can_be_overridden_by_config(tmp_path):
    m = UsageMeter(tmp_path / "j.sqlite", pricing={"claude-sonnet-5-5": {"in": 3.0, "out": 15.0}})
    assert m.record("script", "claude-sonnet-5-5", 1_000_000, 0, job_id="j") == pytest.approx(3.0)
    assert m.record("script", "claude-haiku-5-5", 1_000_000, 0, job_id="j") == pytest.approx(0.10)  # model khác vẫn dùng giá mặc định


def test_cap_stops_job_then_raise_cap_lets_it_continue(tmp_path):
    e = make_env(tmp_path, max_cost_per_job_usd=PER_CALL * 2.5)  # 3 lượt gọi AI đầu qua; lượt thứ 4 bị chặn trước khi gọi
    assert e.mr.run(e.state) == "paused"  # lượt 1: trích brief, hệ hỏi thêm thông tin
    with pytest.raises(CostCapExceeded) as ex:
        e.mr.resume(e.state, pay("answer", note="Mục tiêu: bán hàng"))
    assert ex.value.job_id == JOB and ex.value.spent >= ex.value.cap
    assert e.deps.meter.job_total(JOB)["calls"] == 3  # trích brief lần 2, báo cáo footage; chặn ở bước kịch bản
    stopped_at = e.mr.trace[-1]
    assert stopped_at == "script"
    # runner báo người giao bằng tin có hướng dẫn rõ, và đánh dấu job bị chặn
    JobRunner(None, e.deps)._blocked(JOB, ex.value)
    msg = e.notifier.messages[-1][1]
    assert "/nangtran" in msg and "chạm trần" in msg and e.deps.jobs.get(JOB)["status"] == "blocked"
    # nâng trần riêng cho job này rồi chạy tiếp từ node đang dừng
    e.deps.meter.set_cap(JOB, 50.0)
    assert e.mr.run(e.state, start=stopped_at) == "paused" and e.mr.pending["kind"] == "approval"
    assert e.deps.meter.job_total(JOB)["calls"] == 4


def test_no_cap_means_no_stop(tmp_path):
    e = make_env(tmp_path)
    assert e.deps.meter.cap_for(JOB) is None
    finish(e)


def test_per_job_cap_overrides_default(tmp_path):
    m = UsageMeter(tmp_path / "j.sqlite", default_cap_usd=1.0)
    assert m.cap_for("x") == 1.0
    m.set_cap("x", 5.0)
    assert m.cap_for("x") == 5.0 and m.cap_for("y") == 1.0
    with pytest.raises(ValueError):
        m.set_cap("x", 0)


def test_bot_helpers(tmp_path):
    assert parse_cap(["8"]) == 8.0 and parse_cap(["$8,5"]) == 8.5
    assert parse_cap([]) is None and parse_cap(["abc"]) is None and parse_cap(["-2"]) is None and parse_cap(["0"]) is None
    m = UsageMeter(tmp_path / "j.sqlite", default_cap_usd=2.0)
    m.record("script", "claude-sonnet-5-5", 1_000_000, 0, job_id="j1")
    line = status_line({"job_id": "j1", "status": "running", "stage": "script", "video_type": "koc-review"}, m)
    assert "AI ~$2.00/$2.00" in line
    assert status_line({"job_id": "j2", "status": "queued", "stage": "new", "video_type": "x"}, m).endswith("· x")
