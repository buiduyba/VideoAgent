"""Bản xem trước nhẹ (adapters/preview_b.py) + cờ 'soft' khi nối vào luồng. Dùng ffmpeg thật và `npx` giả (không có HyperFrames ở đây)."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, make_env, pay, run_to_approval_1
from vidagents.fakes import DemoTools
from vidagents.locks import SlotLock
from vidagents.tools import ToolRunner

ADAPTER = REPO / "adapters" / "preview_b.py"
pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="cần ffmpeg + ffprobe")

FAKE_NPX = """#!/bin/sh
# npx giả: ghi lại tham số, rồi tạo video thử theo FAKE_FPS / FAKE_DUR; FAKE_FAIL=1 thì báo lỗi
echo "$@" >> "$FAKE_LOG"
[ "$FAKE_FAIL" = "1" ] && { echo "boom" >&2; exit 1; }
out=""
while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done
exec ffmpeg -y -v error -f lavfi -i "testsrc=duration=${FAKE_DUR:-2}:size=270x480:rate=${FAKE_FPS:-30}" \\
  -f lavfi -i "sine=duration=${FAKE_DUR:-2}" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$out"
"""


# Windows: adapter gọi `npx.cmd`, và file shell không đuôi không chạy được -> bản giả viết bằng Python, bọc bằng npx.cmd
FAKE_NPX_PY = '''import os, subprocess, sys
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a", encoding="utf-8").write(" ".join(a) + "\\n")
if os.environ.get("FAKE_FAIL") == "1":
    sys.stderr.write("boom\\n")
    sys.exit(1)
out = a[a.index("--output") + 1]
dur, fps = os.environ.get("FAKE_DUR", "2"), os.environ.get("FAKE_FPS", "30")
sys.exit(subprocess.call(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=270x480:rate={fps}",
                          "-f", "lavfi", "-i", f"sine=duration={dur}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out]))
'''


@pytest.fixture
def rig(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npx = bindir / "npx"
    npx.write_text(FAKE_NPX, encoding="utf-8")
    npx.chmod(npx.stat().st_mode | stat.S_IXUSR)
    if os.name == "nt":
        (bindir / "fake_npx.py").write_text(FAKE_NPX_PY, encoding="utf-8")
        (bindir / "npx.cmd").write_text(f'@"{sys.executable}" "%~dp0fake_npx.py" %*\r\n', encoding="utf-8")
    log = tmp_path / "npx.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    proj, edit = tmp_path / "proj", tmp_path / "edit"
    (proj / "assets").mkdir(parents=True)
    edit.mkdir()
    (proj / "assets" / "timeline.json").write_text(json.dumps([{"out_start": 0.0, "dur": 1.0}, {"out_start": 1.0, "dur": 1.0}]), encoding="utf-8")
    return type("Rig", (), {"proj": proj, "edit": edit, "log": log, "out": edit / "v-xemtruoc.mp4", "tmp": tmp_path})


def adapter(r, *extra):
    return subprocess.run([sys.executable, str(ADAPTER), "--project", str(r.proj), "--name", "v", "--edit-dir", str(r.edit), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")  # adapter in UTF-8; mặc định Windows là cp1252


def probe(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,avg_frame_rate", "-of", "json", str(path)],
                       capture_output=True, text=True).stdout
    return json.loads(o)["streams"][0]


def test_success_makes_540_wide_preview_and_cleans_temp(rig):
    r = adapter(rig)
    assert r.returncode == 0 and "PREVIEW" in r.stdout, r.stderr
    assert probe(rig.out)["width"] == 540 and probe(rig.out)["avg_frame_rate"] == "30/1"
    assert not list(rig.proj.glob("*xemtruoc*"))  # file trung gian đã dọn


def test_quality_flag_only_sent_when_configured(rig):
    adapter(rig)
    assert "--quality" not in rig.log.read_text()
    adapter(rig, "--quality", "draft")
    assert "--quality draft" in rig.log.read_text()


def test_wrong_fps_is_rejected(rig, monkeypatch):
    monkeypatch.setenv("FAKE_FPS", "25")
    r = adapter(rig)
    assert r.returncode == 3 and "fps" in r.stderr and not rig.out.exists()


def test_wrong_duration_is_rejected(rig, monkeypatch):
    monkeypatch.setenv("FAKE_DUR", "3.5")
    r = adapter(rig)
    assert r.returncode == 3 and "lệch" in r.stderr and not rig.out.exists()


def test_render_failure_exit_2_and_stale_preview_removed(rig, monkeypatch):
    rig.out.write_bytes(b"cu")
    monkeypatch.setenv("FAKE_FAIL", "1")
    r = adapter(rig)
    assert r.returncode == 2 and "boom" in r.stderr and not rig.out.exists()


def test_project_not_built_yet(rig):
    (rig.proj / "assets" / "timeline.json").unlink()
    assert adapter(rig).returncode == 3


# ───────── nối qua manifest thật + ToolRunner ─────────
def manifest_runner(e):
    """Dùng đúng agents/edit/tools.json của khung, chỉ đổi `py` thành python đang chạy test."""
    p = e.agents / "edit" / "tools.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    for st in m["actions"]["preview"]["B"]:
        st["cmd"][0] = sys.executable
    p.write_text(json.dumps(m), encoding="utf-8")
    return ToolRunner(e.cfg, SlotLock(e.cfg.locks_dir, 1))


def variables(e, rig):
    return {"ADAPTERS": str(e.cfg.adapters_dir), "project_dir": str(rig.proj), "name": "v", "edit_dir": str(rig.edit),
            "FFMPEG": "ffmpeg", "FFPROBE": "ffprobe", "quality_args": []}


def test_real_manifest_runs_adapter_and_returns_preview_output(rig):
    e = make_env(rig.tmp, preview=False)
    res = manifest_runner(e).run("edit", "preview", "B", variables(e, rig), cwd=rig.edit, approvals={"duyet_1": True}, owner="j")
    assert list(res.outputs) == ["preview"] and Path(res.outputs["preview"]) == rig.out  # so theo Path: Windows lẫn / và \
    assert not res.soft_failed and rig.out.exists()


def test_real_manifest_render_failure_is_soft_not_fatal(rig, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")
    e = make_env(rig.tmp, preview=False)
    res = manifest_runner(e).run("edit", "preview", "B", variables(e, rig), cwd=rig.edit, approvals={"duyet_1": True}, owner="j")
    assert res.outputs == {} and res.soft_failed and res.soft_failed[0]["name"] == "preview" and "mã 2" in res.soft_failed[0]["why"]


def test_preview_needs_approval_1_even_though_soft(rig):
    from vidagents.tools import Blocked

    e = make_env(rig.tmp, preview=False)
    with pytest.raises(Blocked):
        manifest_runner(e).run("edit", "preview", "B", variables(e, rig), cwd=rig.edit, approvals={}, owner="j")


# ───────── trong luồng job ─────────
class PreviewFails(DemoTools):
    def _exec(self, step, cmd, cwd, variables):
        if step["name"] == "preview":
            return 2, "render lỗi giả"
        return super()._exec(step, cmd, cwd, variables)


def test_job_continues_and_tells_user_when_preview_fails(tmp_path):
    e = make_env(tmp_path, preview=False, tools_cls=PreviewFails)
    p = e.agents / "edit" / "tools.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    m["actions"]["preview"]["B"] = [{"name": "preview", "cmd": ["x"], "soft": True, "retry": False,
                                     "outputs": {"preview": "{edit_dir}/{name}-xemtruoc.mp4"}}]
    p.write_text(json.dumps(m), encoding="utf-8")
    run_to_approval_1(e)
    assert e.mr.resume(e.state, pay("approve")) == "paused"
    assert e.mr.paused == "approve_2" and "preview" not in e.state["outputs"]
    assert any("Không tạo được bản xem trước" in m[1] for m in e.notifier.messages)
    build_step = next(s for s in e.state["steps"] if s["name"] == "edit_build")
    assert build_step["soft_failed"][0]["name"] == "preview"


def test_stale_preview_file_is_not_reported_as_new(tmp_path):
    e = make_env(tmp_path, preview=False, tools_cls=PreviewFails)
    p = e.agents / "edit" / "tools.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    m["actions"]["preview"]["B"] = [{"name": "preview", "cmd": ["x"], "soft": True, "retry": False,
                                     "outputs": {"preview": "{edit_dir}/{name}-xemtruoc.mp4"}}]
    p.write_text(json.dumps(m), encoding="utf-8")
    run_to_approval_1(e)
    stale = Path(e.state["edit_dir"]) / f"{e.state['video_name']}-xemtruoc.mp4"
    stale.write_bytes(b"ban-cu")
    e.state["outputs"] = {"preview": str(stale)}
    e.mr.resume(e.state, pay("approve"))
    assert "preview" not in e.state["outputs"] and not stale.exists()
