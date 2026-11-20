"""Nhiều người giao việc trên CÙNG một thư mục footage: không đè file, chỉ một lần gọi Scribe (tính tiền), có cảnh báo trùng job."""
import json
import os
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import JOB, make_env, pay
from test_bot_flow import FakeRunner, build, ctx, run, upd

from vidagents import workspace as W
from vidagents.bot import same_footage_note
from vidagents.fakes import DemoTools
from vidagents.jobs import JobStore


# ───────────────────────── ghi bản nhận dạng lời lên NAS ─────────────────────────
def _big_transcript(path: Path, mb: int = 12) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"words": ["lời"] * (mb * 60_000)}), encoding="utf-8")


def test_return_transcripts_never_exposes_a_half_written_file(tmp_path, monkeypatch):
    def slow_copy2(a, b, **kw):                     # mô phỏng NAS chậm: chép từng khúc 1 MB, nghỉ giữa các khúc
        with open(a, "rb") as f, open(b, "wb") as g:
            while chunk := f.read(1 << 20):
                g.write(chunk)
                g.flush()
                time.sleep(0.01)
        return b
    monkeypatch.setattr(W.shutil, "copy2", slow_copy2)
    src, work = tmp_path / "nas", tmp_path / "work"
    src.mkdir()
    _big_transcript(work / W.TRANSCRIPTS / "clip.json")
    dst = src / W.TRANSCRIPTS / "clip.json"
    done, bad, seen = threading.Event(), [], [0]

    def reader():                                   # job khác đang lấy bản nhận dạng đúng lúc job này đẩy lên
        while not done.is_set():
            if dst.exists():
                seen[0] += 1
                try:
                    json.loads(dst.read_text(encoding="utf-8"))
                except ValueError:
                    bad.append(1)
            time.sleep(0.0005)
    t = threading.Thread(target=reader)
    t.start()
    try:
        assert W.return_transcripts(src, work) == 1
    finally:
        time.sleep(0.05)
        done.set()
        t.join()
    assert not bad, "người đọc thấy file nhận dạng chép dở"
    assert json.loads(dst.read_text(encoding="utf-8"))["words"][0] == "lời"
    assert not list(dst.parent.glob("*.part"))


def test_concurrent_returns_leave_one_valid_file_and_no_temp_files(tmp_path):
    src, work = tmp_path / "nas", tmp_path / "work"
    src.mkdir()
    for n in ("a", "b", "c"):
        _big_transcript(work / W.TRANSCRIPTS / f"{n}.json", mb=2)
    barrier, errs = threading.Barrier(5), []

    def go():
        barrier.wait()
        try:
            W.return_transcripts(src, work)
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    got = sorted(p.name for p in (src / W.TRANSCRIPTS).iterdir())
    assert got == ["a.json", "b.json", "c.json"]            # không file tạm sót lại
    for p in (src / W.TRANSCRIPTS).iterdir():
        json.loads(p.read_text(encoding="utf-8"))


def test_return_never_overwrites_a_transcript_that_already_exists_on_the_nas(tmp_path):
    src, work = tmp_path / "nas", tmp_path / "work"
    (src / W.TRANSCRIPTS).mkdir(parents=True)
    (src / W.TRANSCRIPTS / "clip.json").write_text('{"cũ": true}', encoding="utf-8")
    (work / W.TRANSCRIPTS).mkdir(parents=True)
    (work / W.TRANSCRIPTS / "clip.json").write_text('{"mới": true}', encoding="utf-8")
    assert W.return_transcripts(src, work) == 0
    assert json.loads((src / W.TRANSCRIPTS / "clip.json").read_text(encoding="utf-8")) == {"cũ": True}


# ───────────────────────── khóa theo thư mục footage ─────────────────────────
def _cfg(tmp_path):
    return SimpleNamespace(locks_dir=tmp_path / "locks", lock_stale_s=3600, lock_timeout_s=30)


def test_second_job_on_the_same_footage_waits_and_ticks_while_waiting(tmp_path):
    cfg, foot = _cfg(tmp_path), tmp_path / "Footage Oillan"
    foot.mkdir()
    held, release, order, ticks = threading.Event(), threading.Event(), [], []

    def first():
        with W.footage_lock(cfg, foot, "J1"):
            order.append("J1 vào")
            held.set()
            release.wait(5)
            order.append("J1 ra")

    def second():
        with W.footage_lock(cfg, foot, "J2", on_tick=lambda: ticks.append(1)):
            order.append("J2 vào")
    t1 = threading.Thread(target=first)
    t1.start()
    assert held.wait(5)
    t2 = threading.Thread(target=second)
    t2.start()
    time.sleep(0.5)
    assert order == ["J1 vào"]                              # J2 phải chờ J1
    release.set()
    t1.join(), t2.join()
    assert order == ["J1 vào", "J1 ra", "J2 vào"]


def test_different_footage_folders_do_not_block_each_other(tmp_path):
    cfg = _cfg(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    inside = threading.Event()

    def other():
        with W.footage_lock(cfg, b, "J2"):
            inside.set()
    with W.footage_lock(cfg, a, "J1"):
        t = threading.Thread(target=other)
        t.start()
        assert inside.wait(3), "hai thư mục khác nhau không được chặn nhau"
        t.join()


@pytest.mark.skipif(os.name != "nt", reason="Windows không phân biệt hoa thường trong đường dẫn")
def test_same_folder_written_with_different_case_is_one_lock(tmp_path):
    cfg, foot = _cfg(tmp_path), tmp_path / "Footage"
    foot.mkdir()
    got = threading.Event()

    def other():
        with W.footage_lock(cfg, Path(str(foot).upper()), "J2", on_tick=None):
            got.set()
    with W.footage_lock(cfg, foot, "J1"):
        t = threading.Thread(target=other, daemon=True)
        t.start()
        time.sleep(0.5)
        assert not got.is_set()                            # cùng thư mục dù viết hoa khác: phải chờ
    t.join(5)
    assert got.is_set()


# ───────────────────────── Scribe chỉ gọi một lần (chạy qua node footage thật) ─────────────────────────
SCRIBE, _L = Counter(), threading.Lock()


class ScribeTools(DemoTools):
    """Bắt chước skill: clip chưa có bản nhận dạng trong <edit>/transcripts thì 'gọi Scribe' (đếm một lần, mất thời gian), có rồi thì dùng lại."""

    def _exec(self, step, cmd, cwd, variables):
        if step["name"] == "prep":
            tdir = Path(variables["edit_dir"]) / "transcripts"
            tdir.mkdir(parents=True, exist_ok=True)
            for f in sorted(Path(variables["footage"]).iterdir()):
                if f.is_file() and f.suffix.lower() == ".mp4" and not (tdir / f"{f.stem}.json").exists():
                    time.sleep(0.4)
                    with _L:
                        SCRIBE[f.name] += 1
                    (tdir / f"{f.stem}.json").write_text('{"words": []}', encoding="utf-8")
        return super()._exec(step, cmd, cwd, variables)


def _two_jobs_on_one_footage(tmp_path):
    a = make_env(tmp_path / "a", tools_cls=ScribeTools)
    b = make_env(tmp_path / "b", tools_cls=ScribeTools)
    b.cfg.footage_root, b.cfg.locks_dir = a.cfg.footage_root, a.cfg.locks_dir      # cùng NAS, cùng thư mục khóa (như hai người trên một máy chung)
    b.state["footage_dir"] = a.state["footage_dir"] = str(a.clip)
    b.state["job_id"] = "20261106-demo-02"
    b.deps.jobs.create("20261106-demo-02", requester_id="1001", brand="demo", video_type="koc-review", footage_dir=str(a.clip))
    return a, b


def _drive_to_approval_1(e, errs):
    try:
        assert e.mr.run(e.state) == "paused" and e.mr.pending["kind"] == "ask_info"
        assert e.mr.resume(e.state, pay("answer", note="Mục tiêu: bán hàng")) == "paused"
        assert e.mr.pending["kind"] == "approval"
    except BaseException as ex:  # noqa: BLE001
        errs.append(repr(ex))


def test_two_jobs_on_the_same_footage_call_scribe_only_once(tmp_path):
    SCRIBE.clear()
    a, b = _two_jobs_on_one_footage(tmp_path)
    barrier, errs = threading.Barrier(2), []

    def go(e):
        barrier.wait()
        _drive_to_approval_1(e, errs)
    ts = [threading.Thread(target=go, args=(e,)) for e in (a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert dict(SCRIBE) == {"clip.mp4": 1}                  # trước khi sửa: 2 (mỗi job một lần, gấp đôi tiền)
    for e in (a, b):                                        # cả hai job đều có bản nhận dạng để dựng
        assert (Path(e.state["edit_dir"]) / "transcripts" / "clip.json").is_file()
    assert (a.clip / W.TRANSCRIPTS / "clip.json").is_file()  # đã đẩy lên NAS ngay sau prep, chưa cần đợi giao kết quả


def test_each_job_still_gets_its_own_work_folder_and_result_folder(tmp_path):
    SCRIBE.clear()
    a, b = _two_jobs_on_one_footage(tmp_path)
    errs = []
    ts = [threading.Thread(target=_drive_to_approval_1, args=(e, errs)) for e in (a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert a.state["work_dir"] != b.state["work_dir"] and a.state["edit_dir"] != b.state["edit_dir"]
    assert a.state["source_dir"] == b.state["source_dir"]   # cùng footage gốc
    assert Path(a.state["work_dir"]).name == a.state["job_id"] and Path(b.state["work_dir"]).name == "20261106-demo-02"


# ───────────────────────── tìm job trùng footage ─────────────────────────
def _job(store, path, status="running", who="1001"):
    jid = store.create_next("demo", requester_id=who, requester_name=f"Người {who}", footage_dir=path, video_type="koc-review", status=status)
    return jid


def test_same_footage_finds_other_active_jobs_only(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    me = _job(store, str(tmp_path / "Footage"))
    other = _job(store, str(tmp_path / "Footage"), who="1002")
    _job(store, str(tmp_path / "Footage"), status="done", who="1003")        # job xong không tính
    _job(store, str(tmp_path / "Footage"), status="cancelled", who="1004")   # job hủy không tính
    _job(store, str(tmp_path / "Khac"), who="1005")                          # footage khác không tính
    assert [j["job_id"] for j in store.same_footage(me)] == [other]
    assert [j["job_id"] for j in store.same_footage(other)] == [me]


def test_same_footage_ignores_trailing_slash_and_windows_case(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    me = _job(store, str(tmp_path / "Footage"))
    other = _job(store, str(tmp_path / "Footage") + os.sep, who="1002")
    assert [j["job_id"] for j in store.same_footage(me)] == [other]
    if os.name == "nt":
        shout = _job(store, str(tmp_path / "FOOTAGE"), who="1003")
        assert shout in [j["job_id"] for j in store.same_footage(me)]


def test_same_footage_is_empty_for_unknown_or_blank(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.same_footage("khong-co") == []
    assert store.same_footage(_job(store, "")) == []


# ───────────────────────── cảnh báo trong bot ─────────────────────────
def test_note_is_empty_without_duplicates_and_lists_who_is_working_on_it():
    assert same_footage_note([]) == ""
    rows = [{"job_id": f"20261120-demo-{i:02d}", "requester_name": "Lan", "video_type": "koc-review", "brand": "demo", "status": "running"} for i in range(1, 8)]
    t = same_footage_note(rows)
    assert "đang có job khác" in t and "20261120-demo-01 (Lan, koc-review/demo, đang chạy)" in t and "và 2 job nữa" in t and "20261120-demo-06" not in t
    assert "không tính tiền Scribe lần hai" in t and "/huy" in t


def test_video_command_warns_the_second_person_on_the_same_footage(tmp_path, monkeypatch):
    holder = {}

    def start(self, **kw):                                   # runner giả nhưng GHI SỔ thật để bot tìm được job trùng
        return holder["e"].deps.jobs.create_next(kw["brand"], requester_id=str(kw["requester_id"]), requester_name=kw["requester_name"],
                                                 footage_dir=kw["footage_dir"], video_type=kw["video_type"], status="running")
    monkeypatch.setattr(FakeRunner, "start", start)
    e, runner, h = build(tmp_path, monkeypatch)
    holder["e"] = e
    e.deps.jobs.update(JOB, status="done")                 # job mẫu có sẵn của make_env cũng nằm trên thư mục này: coi như đã xong
    cmd = f'/video "{e.clip}" koc-review demo\nVideo KOC dài 30 giây, giá 199k, mua ở fanpage'
    first = upd(cmd, uid=1001)
    run(h["/video"](first, ctx()))
    assert first.message.sent[0][0].startswith("Đã nhận, mã job:") and "đang có job khác" not in first.message.sent[0][0]
    second = upd(cmd, uid=9)                                 # quản trị viên giao cùng thư mục
    run(h["/video"](second, ctx()))
    text = second.message.sent[0][0]
    assert "Đã nhận, mã job:" in text and "đang có job khác" in text
    assert "(Người Dùng" in text or "koc-review/demo" in text   # nói rõ job nào đang làm
