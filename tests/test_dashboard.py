"""Dashboard chỉ-đọc: suy tiến độ từ state, đọc checkpoint thật, máy chủ HTTP (chỉ GET)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import JOB, make_env
from vidagents.cli import demo_config, main
from vidagents.dashboard import MILESTONES, Reader, make_server


def steps(*names, **extra):
    return [{"name": n, "status": "ok", "at": "2026-11-14T10:00:00", **extra} for n in names]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = make_env(tmp_path)
    r = Reader(e.cfg)
    state = {}
    monkeypatch.setattr(r, "_checkpoint_values", lambda job_id: (dict(state), None))
    return type("Rig", (), {"e": e, "r": r, "state": state})


def set_status(rig, **kw):
    rig.e.deps.jobs.update(JOB, **kw)


def test_new_job_has_no_progress_and_orchestrator_active(rig):
    set_status(rig, status="running", stage="intake")
    j = rig.r.job(JOB)
    assert j["progress"] == 0 and j["active"] == "orchestrator"
    assert j["milestones"][0]["state"] == "active"
    assert [a["id"] for a in j["agents"] if a["state"] == "active"] == ["orchestrator"]


def test_progress_and_active_agent_follow_last_step(rig):
    rig.state.update(steps=steps("intake", "extract_brief", "footage", "script"), approvals={})
    set_status(rig, status="running", stage="script")
    j = rig.r.job(JOB)
    assert j["progress"] == round(100 * 4 / len(MILESTONES))
    assert j["active"] == "human"  # sau kịch bản là cổng duyệt 1
    rig.state.update(steps=steps("intake", "extract_brief", "footage", "script", "edit_build"), approvals={"duyet_1": True})
    j = rig.r.job(JOB)
    assert j["active"] == "critic" and [a["id"] for a in j["agents"] if a["state"] == "active"] == ["critic"]


def test_critic_with_heavy_findings_sends_work_back_to_edit(rig):
    rig.state.update(steps=steps("intake", "extract_brief", "footage", "script", "edit_build", "critic_preview"),
                     approvals={"duyet_1": True}, critic_has_heavy=True,
                     critic_findings=[{"severity": "nang", "at_seconds": 3, "desc": "Mất tiếng"}, {"severity": "vua", "desc": "Chữ nhỏ"}])
    set_status(rig, status="running", stage="build")
    j = rig.r.job(JOB)
    assert j["active"] == "edit"
    assert j["critic"]["heavy"] == 1 and j["critic"]["mid"] == 1


@pytest.mark.parametrize("status,approvals,esc,expect", [
    ("waiting_approval", {}, None, "approve_1"),
    ("waiting_approval", {"duyet_1": True}, None, "approve_2"),
    ("waiting_info", {}, None, "info"),
    ("waiting_approval", {"duyet_1": True}, {"reason": "vượt 2 vòng"}, "escalate"),  # cần người quyết thắng mọi lý do khác
    ("running", {}, None, ""),
])
def test_waiting_for(rig, status, approvals, esc, expect):
    names = ["intake", "extract_brief", "footage", "script"] + (["edit_build", "critic_preview"] if approvals.get("duyet_1") else [])
    rig.state.update(steps=steps(*names), approvals=approvals, escalation=esc)
    set_status(rig, status=status)
    assert rig.r.job(JOB)["waiting_for"] == expect


def test_done_job_is_100_percent_and_nobody_active(rig):
    set_status(rig, status="done", stage="done")
    j = rig.r.job(JOB)
    assert j["progress"] == 100 and j["active"] is None
    assert all(m["state"] == "done" for m in j["milestones"])


def test_blocked_job_marks_stopped_milestone_and_keeps_note(rig):
    rig.state.update(steps=steps("intake", "extract_brief", "footage"))
    set_status(rig, status="blocked", note="CostCapExceeded: chạm trần")
    j = rig.r.job(JOB)
    assert [m["key"] for m in j["milestones"] if m["state"] == "stopped"] == ["script"]
    assert "chạm trần" in j["note"]


def test_unreadable_checkpoint_degrades_instead_of_crashing(tmp_path):
    e = make_env(tmp_path)
    e.cfg.checkpoint_db.write_bytes(b"day khong phai sqlite")
    j = Reader(e.cfg).job(JOB)
    assert j["job_id"] == JOB and j["checkpoint_error"]
    assert Reader(e.cfg).overview()["jobs"][0]["job_id"] == JOB


def test_unknown_job_is_none(rig):
    assert rig.r.job("khong-co") is None


# ───────── máy chủ HTTP ─────────
@pytest.fixture
def server(rig):
    srv = make_server(rig.r, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def http(url, method="GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, data=b"{}" if method != "GET" else None), timeout=5) as r:
            return r.status, r.read().decode("utf-8"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers


def test_server_serves_page_and_json(server):
    code, body, h = http(server + "/")
    assert code == 200 and "text/html" in h["Content-Type"] and "theo dõi" in body
    code, body, _ = http(server + "/api/overview")
    assert code == 200 and json.loads(body)["jobs"][0]["job_id"] == JOB
    code, body, _ = http(f"{server}/api/jobs/{JOB}")
    assert code == 200 and json.loads(body)["job_id"] == JOB


def test_server_rejects_bad_ids_unknown_and_any_write(server):
    assert http(server + "/api/jobs/..%2F..%2Fconfig")[0] == 400
    assert http(server + "/api/jobs/" + "a" * 200)[0] == 400
    assert http(server + "/api/jobs/khong-co")[0] == 404
    assert http(server + "/api/nope")[0] == 404
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        assert http(f"{server}/api/jobs/{JOB}", m)[0] == 405  # không có đường nào ghi/duyệt/hủy từ dashboard


# ───────── dữ liệu thật: chạy demo (LangGraph thật) rồi đọc checkpoint ─────────
def test_reads_real_langgraph_checkpoint_from_demo(tmp_path, capsys):
    assert main(["demo", "--workdir", str(tmp_path)]) == 0
    capsys.readouterr()
    r = Reader(demo_config(tmp_path))
    j = r.job("20261106-demo-01")
    assert j["checkpoint_error"] is None
    assert j["status"] == "done" and j["progress"] == 100
    names = [s["name"] for s in j["steps"]]
    assert names[0] == "intake" and "render" in names and "deliver" in names
    assert {a["id"]: a["steps"] for a in j["agents"]}["edit"] >= 2
    assert j["critic"]["mid"] >= 1  # demo có 1 lỗi vừa ở critic bản cuối
