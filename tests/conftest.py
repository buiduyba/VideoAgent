import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from vidagents.app import build_deps  # noqa: E402
from vidagents.config import Config  # noqa: E402
from vidagents.fakes import DemoLLM, DemoTools  # noqa: E402
from vidagents.notify import RecordingNotifier  # noqa: E402
from vidagents.testing import MiniRunner  # noqa: E402

JOB = "20261106-demo-01"


def make_env(tmp_path: Path, *, heavy_rounds: int = 1, preview: bool = True, llm=None, tools_cls=DemoTools,
             video_type: str = "koc-review", **cfg_over):
    foot = tmp_path / "footage"
    clip = foot / "clip-demo"
    clip.mkdir(parents=True)
    (clip / "clip.mp4").write_bytes(b"demo")
    agents = tmp_path / "agents"
    shutil.copytree(REPO / "agents", agents)
    if preview:  # mô phỏng việc skill khai báo thêm bước xem trước + critic xem trước
        p = agents / "edit" / "tools.json"
        m = json.loads(p.read_text(encoding="utf-8"))
        m["actions"]["preview"]["B"] = [{"name": "preview", "cmd": ["x"], "outputs": {"preview": "{edit_dir}/{name}-preview.mp4"}}]
        p.write_text(json.dumps(m), encoding="utf-8")
        p = agents / "critic" / "tools.json"
        m = json.loads(p.read_text(encoding="utf-8"))
        m["actions"]["critic_preview"]["B"] = [{"name": "critic", "cmd": ["x"], "report_files": ["{critic_out}/report.md"]}]
        p.write_text(json.dumps(m), encoding="utf-8")
    (tmp_path / "skill").mkdir()
    cfg_over.setdefault("work_reserve_gb", 0.0)
    cfg_over.setdefault("scribe_auto_max_minutes", 1e9)  # test cũ không quan tâm cổng Scribe; test riêng đặt lại
    cfg_over.setdefault("work_root", tmp_path / "work")
    cfg = Config(footage_root=foot, projects_root=tmp_path / "projects", jobs_db=tmp_path / "jobs.sqlite",
                 checkpoint_db=tmp_path / "ck.sqlite", locks_dir=tmp_path / "locks", agents_dir=agents,
                 video_types_dir=REPO / "video_types", skill_dir=tmp_path / "skill", allowed_user_ids=["1001"],
                 admin_ids=["9"], **cfg_over)
    notifier = RecordingNotifier()
    llm = llm or DemoLLM(heavy_rounds=heavy_rounds)
    deps = build_deps(cfg, llm=llm, notifier=notifier, tools_cls=tools_cls)
    if isinstance(llm, DemoLLM) and llm.meter is None:
        llm.meter = deps.meter  # AI giả ghi token giả như AI thật
    mr = MiniRunner(deps)
    deps.jobs.create(JOB, requester_id="1001", brand="demo", video_type=video_type, footage_dir=str(clip))
    state = {"job_id": JOB, "requester_id": "1001", "requester_name": "Demo", "footage_dir": str(clip), "brand": "demo",
             "video_type": video_type, "brief_text": "Video KOC Sản phẩm demo", "approvals": {}, "feedback": [], "steps": [],
             "human_revisions": 0, "critic_fix_rounds": 0, "info_rounds": 0, "outputs": {}}
    return SimpleNamespace(cfg=cfg, deps=deps, mr=mr, llm=llm, notifier=notifier, state=state, clip=clip, agents=agents)


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def pay(action, by="1001", **kw):
    return {"action": action, "by": by, **kw}


def run_to_approval_1(e):
    assert e.mr.run(e.state) == "paused" and e.mr.pending["kind"] == "ask_info"
    assert e.mr.resume(e.state, pay("answer", note="Mục tiêu: bán hàng")) == "paused"
    assert e.mr.pending["kind"] == "approval" and e.mr.paused == "approve_1"
