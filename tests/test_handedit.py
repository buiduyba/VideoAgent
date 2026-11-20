"""Nhánh chỉnh tay bằng VideoFac ở Duyệt 2: /chinhtay đóng gói lên NAS, /xong nhận bản xuất, giao không qua AI."""
import shutil
import subprocess
import sys

import pytest

from conftest import JOB, make_env, pay, run_to_approval_1
from vidagents import handedit as H

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="cần ffmpeg + ffprobe")

# import.js giả (chạy bằng Python): làm đúng việc của `--pack` thật: tạo gói + in dòng RESULT
FAKE_IMPORT = '''
import sys, json, pathlib
a = sys.argv[1:]
dest = pathlib.Path(a[a.index("--pack") + 1])
(dest / "files").mkdir(parents=True, exist_ok=True)
(dest / "export").mkdir(exist_ok=True)
(dest / "handover.json").write_text("{}")
(dest / "Mo-trong-VideoFac.cmd").write_text("@echo off")
print("RESULT " + json.dumps({"dir": str(dest), "files": 3, "bytes": 3145728, "cmd": str(dest / "Mo-trong-VideoFac.cmd"),
                              "handover": str(dest / "handover.json"), "exportDir": str(dest / "export")}))
'''


def make_mp4(path, size="1080x1920", audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={size}:d=1.5:r=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    cmd += ["-t", "1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True)


def env_with_videofac(tmp_path):
    imp = tmp_path / "fake_import.py"
    imp.write_text(FAKE_IMPORT, encoding="utf-8")
    e = make_env(tmp_path, videofac_import=imp, node_exe=sys.executable)
    return e


def at_approve_2(e):
    run_to_approval_1(e)
    assert e.mr.resume(e.state, pay("approve")) == "paused" and e.mr.paused == "approve_2"
    proj = e.cfg.projects_root / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    e.state.setdefault("outputs", {})["project_dir"] = str(proj)   # dự án HyperFrames (công cụ giả không tạo thật)


def test_duyet2_message_offers_chinhtay(tmp_path):
    e = env_with_videofac(tmp_path)
    run_to_approval_1(e)
    e.mr.resume(e.state, pay("approve"))
    t = e.mr.pending["text"]
    assert f"/chinhtay {JOB}" in t and "không tốn AI" in t and "VideoFac" in t


def test_chinhtay_without_videofac_configured_is_refused_and_keeps_waiting(tmp_path):
    e = make_env(tmp_path)   # không cấu hình videofac_import
    at_approve_2(e)
    assert e.mr.resume(e.state, pay("handedit")) == "paused" and e.mr.paused == "approve_2"
    assert any("Chưa chỉnh tay được" in m[1] for m in e.notifier.messages)


def test_chinhtay_packs_to_nas_and_waits_for_xong(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    assert e.mr.resume(e.state, pay("handedit")) == "paused" and e.mr.paused == "hand_edit_wait"
    dest = H.pack_dir(e.state["source_dir"], JOB)
    assert (dest / "handover.json").exists() and (dest / "Mo-trong-VideoFac.cmd").exists()
    assert e.state["outputs"]["videofac_dir"] == str(dest)
    t = e.mr.pending["text"]
    assert str(dest) in t and f"/xong {JOB}" in t and "Chi phí AI trong lúc chờ: 0" in t


def test_cancel_while_waiting_for_hand_edit(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.mr.resume(e.state, pay("handedit"))
    assert e.mr.resume(e.state, pay("cancel")) == "done" and e.state["status"] == "cancelled"


def test_other_people_cannot_xong(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.mr.resume(e.state, pay("handedit"))
    assert e.mr.resume(e.state, pay("done", by="2002")) == "paused" and e.mr.paused == "hand_edit_wait"   # người lạ bị bỏ qua


@needs_ffmpeg
def test_find_export_checks(tmp_path):
    cfg = make_env(tmp_path).cfg
    pdir = tmp_path / "videofac"
    (pdir / "export").mkdir(parents=True)
    (pdir / "handover.json").write_text("{}")
    assert "Chưa có bản xuất" in " ".join(H.find_export(cfg, pdir).problems)
    make_mp4(pdir / "export" / "a.mp4", size="720x1280")
    assert any("720x1280" in p and "1080x1920" in p for p in H.find_export(cfg, pdir).problems)
    make_mp4(pdir / "export" / "b.mp4", audio=False)
    assert any("không có tiếng" in p for p in H.find_export(cfg, pdir).problems)
    make_mp4(pdir / "export" / "c.mp4")
    (pdir / ".dang-mo-boi").write_text("Hoa tren MAY-2")
    assert any("Hoa tren MAY-2" in p for p in H.find_export(cfg, pdir).problems)    # VideoFac còn giữ khóa
    (pdir / ".dang-mo-boi").unlink()
    ok = H.find_export(cfg, pdir)
    assert ok.problems == [] and ok.mp4.name == "c.mp4" and "1080x1920" in ok.info and "có tiếng" in ok.info


def export_ready(e, price_line="Xin chào"):
    dest = H.pack_dir(e.state["source_dir"], JOB)
    make_mp4(dest / "export" / "chinhtay.mp4")
    (dest / "export" / "chinhtay.srt").write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{price_line}\n", encoding="utf-8")
    return dest


@needs_ffmpeg
def test_hand_edited_video_goes_through_final_critic_without_skill_layer_1_or_ai_fix(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.mr.resume(e.state, pay("handedit"))
    n_calls, n_exec = len(e.llm.calls), len(e.deps.tools.executed)
    export_ready(e)
    e.llm.heavy_left = 0
    assert e.mr.run(e.state, start="hand_edit_done") == "done"
    assert e.mr.trace[-6:-1].count("critic") == 1 and "edit_fix" not in e.mr.trace[e.mr.trace.index("hand_edit"):]
    new_calls = e.llm.calls[n_calls:]
    assert ("critic", "Layer2Report") in new_calls                       # lớp 2 (AI xem khung hình) chạy
    assert ("critic", "Layer1Report") not in new_calls and not any(a == "edit" for a, _ in new_calls)
    assert [n for n, _ in e.deps.tools.executed[n_exec:] if n in ("critic", "render")] == []   # lớp 1 của skill + render không chạy
    assert e.state["critic_stage"] == "final" and e.state["hand_edited"] is True
    assert any("không chạy trên bản chỉnh tay" in n for n in e.state["steps"][-1].get("notes", []) + [x for s in e.state["steps"] for x in s.get("notes", [])])


@needs_ffmpeg
def test_heavy_finding_on_hand_edit_asks_the_human_instead_of_ai_fixing(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.mr.resume(e.state, pay("handedit"))
    export_ready(e)
    e.llm.heavy_left = 1                                     # AI giả báo một lỗi nặng ở lần soi tới
    assert e.mr.run(e.state, start="hand_edit_done") == "paused" and e.mr.paused == "hand_edit_review"
    assert "edit_fix" not in e.mr.trace[e.mr.trace.index("hand_edit"):]       # không dựng lại: sẽ mất phần người đã chỉnh
    t = e.mr.pending["text"]
    assert "SOI LỖI BẢN CHỈNH TAY" in t and f"/chinhtay {JOB}" in t and f"/duyet {JOB}" in t
    assert e.mr.resume(e.state, pay("handedit")) == "paused" and e.mr.paused == "hand_edit_wait"   # chỉnh lại trong VideoFac
    e.llm.heavy_left = 1
    assert e.mr.resume(e.state, pay("done")) == "paused" and e.mr.paused == "hand_edit_review"
    assert e.mr.resume(e.state, pay("approve")) == "done" and e.state["status"] == "done"            # chấp nhận giao
    done = next(m[1] for m in e.notifier.messages if "XONG" in m[1])
    assert "còn lỗi nặng theo critic" in done


@needs_ffmpeg
def test_full_hand_edit_branch_delivers_without_new_ai_edit_or_critic_calls(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.mr.resume(e.state, pay("handedit"))
    calls_before = list(e.llm.calls)
    # /xong khi chưa có bản xuất: node báo lỗi rõ, job không giao bản rỗng
    with pytest.raises(RuntimeError, match="Bản xuất chưa dùng được"):
        e.mr.resume(e.state, pay("done"))
    dest = H.pack_dir(e.state["source_dir"], JOB)
    make_mp4(dest / "export" / "chinhtay.mp4")
    (dest / "export" / "chinhtay.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    assert e.mr.run(e.state, start="hand_edit_done") == "done"
    assert e.state["status"] == "done" and e.state["outputs"]["hand_edited"] == "1"
    assert e.state["outputs"]["final"].endswith("chinhtay.mp4") and e.state["outputs"]["light"].endswith("-nhe.mp4")
    new_calls = e.llm.calls[len(calls_before):]
    assert not any(a == "edit" for a, _ in new_calls)                       # chỉnh tay không gọi AI sửa EDL
    done = next(m[1] for m in e.notifier.messages if "XONG" in m[1])
    assert "Bản chỉnh tay bằng VideoFac" in done and "Hệ thống KHÔNG tự đăng bài" in done
    assert (e.clip / "edit" / JOB / "chinhtay.mp4").exists()                  # giao về thư mục footage gốc
    assert not (e.clip / "edit" / JOB / "Mo-trong-VideoFac.cmd").exists()      # file mở gói không phải kết quả


@needs_ffmpeg
def test_hand_edited_srt_with_wrong_price_is_flagged(tmp_path):
    e = env_with_videofac(tmp_path)
    at_approve_2(e)
    e.state["brief_text"] = "Giá 199.000đ"
    e.state["brief"] = {"brand": "demo", "product": "p", "goal": "g", "show_price": True, "price_text": "199.000đ"}
    e.mr.resume(e.state, pay("handedit"))
    dest = H.pack_dir(e.state["source_dir"], JOB)
    make_mp4(dest / "export" / "x.mp4")
    (dest / "export" / "x.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nChỉ còn 99.000đ hôm nay\n", encoding="utf-8")
    e.llm.heavy_left = 0
    assert e.mr.run(e.state, start="hand_edit_done") == "paused" and e.mr.paused == "hand_edit_review"   # giá sai -> hỏi người
    assert e.state["critic_has_heavy"] is True
    assert any("99.000" in f["desc"] for f in e.state["critic_findings"])
