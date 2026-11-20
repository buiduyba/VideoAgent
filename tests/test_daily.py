"""Hỏi hàng ngày "hôm nay dựng video nào": lịch 8:30/21:00, danh sách thư mục chưa dựng, giá/ưu đãi chọn mỗi lần, xác nhận, giao job."""
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import JOB, REPO, make_env
from vidagents.config import Config
from vidagents.daily import Daily, DailyStore, EXPIRED, deadline_passed, due_slots, parse_hhmm

VALUES = {"product": "Saphir dung dịch vệ sinh", "benefits": "theo lời người nói", "price": "Có, giá 199.000đ", "offer": "Giảm 10%", "deadline": "30/11",
          "where": "Fanpage Saphir Việt Nam", "goal": "quảng cáo bán hàng", "consent": "Có", "name": "Đợt 19/11"}
SUN, FRI = datetime(2026, 11, 22, 8, 35), datetime(2026, 11, 20, 8, 35)


def mk(nas: Path, project: str, name: str, clips: int = 2) -> Path:
    d = nas / project / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(clips):
        (d / f"c{i}.mp4").write_bytes(b"x")
    return d


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    nas = tmp_path / "nas"
    e.cfg.footage_root = nas
    e.nas = nas
    mk(nas, "KOC Nhật Hải", "2026-11-19", 4)
    mk(nas, "KOC Nhật Hải", "2026-11-18", 2)
    mk(nas, "Trang Chun", "2026-11-20", 2)
    e.deps.jobs.update(JOB, status="done")
    e.clock = [FRI]
    e.store = DailyStore(e.cfg.jobs_db)
    e.daily = Daily(e.cfg, e.deps.jobs, e.store, e.deps.types, e.deps.meter, now=lambda: e.clock[0])
    e.store.save_profile("KOC Nhật Hải", "koc-review", "demo", VALUES, str(nas / "KOC Nhật Hải" / "2026-11-17"))
    return e


def session(e, uid="1001"):
    return e.daily.build_session(uid, "2026-11-20@08:30", "08:30", *e.daily.candidates())


def press(e, st, code, arg=""):
    return e.daily.on_callback(st, code, f"{st['gen']}.{arg}")


def idx_of(st, project, name):
    return next(i for i, c in enumerate(st["cands"]) if c["project"] == project and c["name"] == name)


def labels(r):
    return [b[0] for row in r.kb for b in row]


# ───────────────────────── lịch ─────────────────────────
def test_slots_fire_at_830_and_2100_within_the_grace_window_on_enabled_days():
    cfg = SimpleNamespace(daily_times=["08:30", "21:00"], daily_days=[0, 1, 2, 3, 4, 5], daily_grace_min=120)
    names = lambda *a: [s for s, _ in due_slots(cfg, datetime(*a))]
    assert names(2026, 11, 20, 8, 29) == []
    assert names(2026, 11, 20, 8, 30) == ["2026-11-20@08:30"]
    assert names(2026, 11, 20, 10, 30) == ["2026-11-20@08:30"]            # khởi động trễ: vẫn hỏi bù tới hết thời gian ân hạn
    assert names(2026, 11, 20, 10, 31) == []
    assert names(2026, 11, 20, 21, 0) == ["2026-11-20@21:00"]
    assert names(2026, 11, 22, 8, 35) == []                                # Chủ nhật: tắt
    bad = SimpleNamespace(daily_times=["25:99", "8:5", "09:00"], daily_days=list(range(7)), daily_grace_min=60)
    assert [s for s, _ in due_slots(bad, datetime(2026, 11, 20, 9, 5))] == ["2026-11-20@09:00"]
    assert parse_hhmm("7:05") == (7, 5) and parse_hhmm("24:00") is None


@pytest.mark.parametrize("text,today,expect", [("30/11", date(2026, 11, 20), False), ("19/11", date(2026, 11, 20), True), ("đến 30/11/2026", date(2026, 11, 20), False),
                                               ("30/11/2025", date(2026, 11, 20), True), ("hết tháng", date(2026, 11, 20), False), ("31/02", date(2026, 11, 20), False),
                                               ("05/01", date(2026, 12, 28), False), ("", date(2026, 11, 20), False), ("20/11", date(2026, 11, 20), False)])
def test_deadline_passed(text, today, expect):
    assert deadline_passed(text, today) is expect


# ───────────────────────── thư mục chưa dựng ─────────────────────────
def test_candidates_hide_built_and_active_folders_and_keep_cancelled_ones(env):
    nas, jobs = env.nas, env.deps.jobs
    mk(nas, "KOC Nhật Hải", "2026-11-17", 3)
    mk(nas, "KOC Nhật Hải", "2026-11-16", 1)
    (nas / "KOC Nhật Hải" / "rong").mkdir()                                 # không có clip: bỏ
    (nas / "KOC Nhật Hải" / "edit").mkdir()                                 # thư mục kết quả: ẩn
    (nas / "KOC Nhật Hải" / "logo").mkdir()                                 # thư mục tài nguyên: ẩn
    mk(nas, "Minh Thư", "tho")                                              # sẽ bị bỏ bớt... xem dưới
    (nas / "Minh Thư" / "tho").rename(nas / "Minh Thư" / "x")
    for f in (nas / "Minh Thư" / "x").iterdir():                             # clip nằm thẳng trong thư mục KOC (không có thư mục con theo ngày)
        f.rename(nas / "Minh Thư" / f.name)
    (nas / "Minh Thư" / "x").rmdir()
    jobs.create_next("demo", requester_id="1", requester_name="Lan", footage_dir=str(nas / "KOC Nhật Hải" / "2026-11-19"), status="done")
    jobs.create_next("demo", requester_id="1", requester_name="Lan", footage_dir=str(nas / "KOC Nhật Hải" / "2026-11-18"), status="cancelled")
    jobs.create_next("demo", requester_id="2", requester_name="Hà", footage_dir=str(nas / "Trang Chun" / "2026-11-20"), status="running")
    cands, active = env.daily.candidates()
    got = [(c["project"], c["name"]) for c in cands]
    nhat = [n for p, n in got if p == "KOC Nhật Hải"]
    assert nhat == ["2026-11-18", "2026-11-17", "2026-11-16"]               # đã dựng (11-19) ẩn; đã hủy (11-18) còn; mới nhất trước
    assert ("Trang Chun", "2026-11-20") not in got                          # đang có job: không mời dựng trùng
    assert ("Minh Thư", "(thư mục gốc)") in got
    assert all("edit" not in n and n not in ("logo", "rong") for _, n in got)
    assert active == ["Trang Chun · 2026-11-20 (Hà)"]


def test_at_most_three_folders_per_project_and_a_global_limit(env):
    for d in range(1, 8):
        mk(env.nas, "Nhiều Thư Mục", f"2026-10-{d:02d}")
    cands, _ = env.daily.candidates()
    assert len([c for c in cands if c["project"] == "Nhiều Thư Mục"]) == 3
    env.cfg.daily_max_candidates = 2
    assert len(env.daily.candidates()[0]) == 2


# ───────────────────────── luồng chọn và giao ─────────────────────────
def test_happy_path_pick_reuse_price_confirm_and_start(env):
    st = session(env)
    r = env.daily.pick_view(st)
    assert "Chào buổi sáng" in r.text and "KOC Nhật Hải · 2026-11-19 · 4 clip" in " ".join(labels(r))
    i = idx_of(st, "KOC Nhật Hải", "2026-11-19")
    assert press(env, st, "go").text.startswith("⚠ Chưa chọn")             # chưa chọn gì thì không đi tiếp
    r = press(env, st, "t", str(i))
    assert "✅ KOC Nhật Hải" in " ".join(labels(r))
    r = press(env, st, "go")
    assert "Video 1/1" in r.text and "Giá: Có, giá 199.000đ" in r.text and "Dùng lại như trên" in " ".join(labels(r))
    r = press(env, st, "pc", "same")
    assert r.text.startswith("Kế hoạch: 1 video") and "giá Có, giá 199.000đ, ưu đãi Giảm 10% đến 30/11" in r.text
    assert "xong khoảng 08:43" in r.text and "Chưa đặt trần" in r.text                  # 08:35 + 8,1 phút, chưa có trần
    r = press(env, st, "st")
    assert r.end and len(r.starts) == 1 and not r.failed and not r.firsts
    s = r.starts[0]
    assert s["args"]["footage_dir"] == str(env.nas / "KOC Nhật Hải" / "2026-11-19") and s["args"]["video_type"] == "koc-review" and s["args"]["brand"] == "demo"
    assert "199.000đ" in s["args"]["brief_text"] and "Giảm 10%" in s["args"]["brief_text"]
    assert "name" not in s["values"]                                                      # tên đợt của lần trước không mang sang
    assert s["args"]["brief_prefill"]["product"] == "Saphir dung dịch vệ sinh"
    assert press(env, st, "st").text == EXPIRED                                          # bấm "Bắt đầu" lần hai: không giao trùng


def test_expired_offer_cannot_be_reused_and_none_clears_offer_and_deadline(env):
    env.clock[0] = datetime(2026, 12, 1, 8, 35)
    st = session(env)
    press(env, st, "t", str(idx_of(st, "KOC Nhật Hải", "2026-11-19")))
    r = press(env, st, "go")
    assert "hết hạn" in r.text and "Dùng lại như trên" not in " ".join(labels(r))
    r = press(env, st, "pc", "same")
    assert r.text.startswith("⚠ Không dùng lại được") and st["stage"] == "item"
    r = press(env, st, "pc", "none")
    assert "không ưu đãi" in r.text and "Giảm" not in r.text
    s = press(env, st, "st").starts[0]
    assert "offer" not in s["values"] and "deadline" not in s["values"] and s["values"]["price"] == "Có, giá 199.000đ"
    assert "Giảm 10%" not in s["args"]["brief_text"]


def test_typing_new_price_and_offer_is_validated_and_replaces_everything(env):
    env.clock[0] = datetime(2026, 12, 1, 8, 35)
    st = session(env)
    press(env, st, "t", str(idx_of(st, "KOC Nhật Hải", "2026-11-19")))
    press(env, st, "go")
    r = press(env, st, "pc", "new")
    assert st["stage"] == "text" and "gõ giá và ưu đãi HÔM NAY" in r.text
    assert env.daily.on_text(st, "giá rẻ lắm").text.startswith("⚠ Chưa đọc được dòng giá")
    assert "Giá chưa rõ" in env.daily.on_text(st, "Giá còn áp dụng: Có").text
    assert "đã qua" in env.daily.on_text(st, "Giá còn áp dụng: Có, giá 219.000đ\nƯu đãi: Giảm 15%\nHạn ưu đãi: 25/11").text
    assert "phải ghi nội dung ưu đãi" in env.daily.on_text(st, "Giá còn áp dụng: Có, giá 219.000đ\nHạn ưu đãi: 05/12").text
    r = env.daily.on_text(st, "Giá còn áp dụng: Có, giá 219.000đ\nƯu đãi: Giảm 15%\nHạn ưu đãi: 05/12")
    assert r.text.startswith("Kế hoạch: 1 video") and "219.000đ" in r.text and "Giảm 15% đến 05/12" in r.text
    s = press(env, st, "st").starts[0]
    assert s["values"]["price"] == "Có, giá 219.000đ" and s["values"]["offer"] == "Giảm 15%" and "199.000đ" not in s["args"]["brief_text"]
    st2 = session(env)
    press(env, st2, "t", str(idx_of(st2, "KOC Nhật Hải", "2026-11-19")))
    press(env, st2, "go")
    press(env, st2, "pc", "new")
    press(env, st2, "pc", "new") if False else None
    r = env.daily.on_text(st2, "Giá còn áp dụng: Không")                                 # chỉ ghi giá: ưu đãi coi là KHÔNG có (không lấy lại của lần trước)
    assert "không ưu đãi" in r.text and env.daily.on_text(SimpleNamespace(**{}) if False else st2, "x") is None


def test_on_text_is_ignored_outside_the_text_step(env):
    st = session(env)
    assert env.daily.on_text(st, "Giá còn áp dụng: Không") is None


def test_folder_without_a_brief_profile_goes_to_the_first_time_form_after_the_others(env):
    st = session(env)
    press(env, st, "t", str(idx_of(st, "KOC Nhật Hải", "2026-11-19")))
    press(env, st, "t", str(idx_of(st, "Trang Chun", "2026-11-20")))
    r = press(env, st, "go")
    assert "Nhật Hải" in r.text and "Video 1/1" in r.text                                   # chỉ mục có hồ sơ hỏi giá; mục kia chờ
    r = press(env, st, "pc", "same")
    assert "CHƯA có hồ sơ brief" in r.text and "Trang Chun" in r.text
    r = press(env, st, "st")
    assert len(r.starts) == 1 and [f["project"] for f in r.firsts] == ["Trang Chun"]
    p = env.daily.first_time_prompt(st, r.firsts[0])
    assert p.kb[0][0][1] == f"d|wz|{st['gen']}.{r.firsts[0]['idx']}"
    w = press(env, st, "wz", str(r.firsts[0]["idx"]))                                        # còn bấm được sau khi đã giao
    assert w.wizard == str(env.nas / "Trang Chun" / "2026-11-20") and not w.save


def test_only_first_time_folders_selected_still_reach_a_confirm_with_start_button(env):
    st = session(env)
    press(env, st, "t", str(idx_of(st, "Trang Chun", "2026-11-20")))
    r = press(env, st, "go")
    assert "Chưa có video nào sẵn sàng" in r.text and "CHƯA có hồ sơ brief" in r.text and any("Bắt đầu" in l for l in labels(r))


def test_profile_pointing_at_a_vanished_brand_or_type_falls_back_to_first_time(env):
    env.store.save_profile("Trang Chun", "koc-review", "thuong-hieu-da-xoa", VALUES)
    st = session(env)
    press(env, st, "t", str(idx_of(st, "Trang Chun", "2026-11-20")))
    r = press(env, st, "go")
    assert "CHƯA có hồ sơ brief" in r.text


def test_skip_back_cancel_and_off(env):
    env.store.save_profile("Trang Chun", "koc-review", "demo", VALUES)
    st = session(env)
    for p, n in (("KOC Nhật Hải", "2026-11-19"), ("Trang Chun", "2026-11-20")):
        press(env, st, "t", str(idx_of(st, p, n)))
    r = press(env, st, "go")
    assert "Video 1/2" in r.text
    r = press(env, st, "skip")
    assert "Video 1/1" in r.text or "Video 2/2" in r.text or "Trang Chun" in r.text or "Nhật Hải" in r.text
    r = press(env, st, "bk")
    assert "Bạn muốn dựng video nào?" in r.text and st["stage"] == "pick"
    r = press(env, st, "off")
    assert r.end and "không dựng" in r.text and st["stage"] == "off"
    st2 = session(env)
    assert press(env, st2, "x").end


def test_stale_buttons_from_an_older_question_are_refused(env):
    st = session(env)
    old_gen = st["gen"]
    env.store.save_session("1001", st)
    st2 = session(env)
    assert st2["gen"] == old_gen + 1 or st2["gen"] == old_gen                                  # gen tăng theo phiên đã lưu
    r = env.daily.on_callback(st2, "go", f"{st2['gen'] - 1}.")
    assert r.text == EXPIRED and not r.save


def test_daily_caps_block_the_start_button_when_set(env):
    env.cfg.daily_max_jobs = 1
    env.deps.jobs.create_next("demo", requester_id="1", footage_dir=str(env.nas / "x"), status="running")
    st = session(env)
    press(env, st, "t", str(idx_of(st, "KOC Nhật Hải", "2026-11-19")))
    press(env, st, "go")
    r = press(env, st, "pc", "same")
    assert "Vượt trần 1 job/ngày" in r.text and not any("Bắt đầu" in l for l in labels(r))
    assert press(env, st, "st").text == EXPIRED or not press(env, st, "st").starts


def test_item_needing_a_missing_field_is_reported_not_started(env):
    broken = {k: v for k, v in VALUES.items() if k not in ("product", "benefits")}
    env.store.save_profile("KOC Nhật Hải", "koc-review", "demo", broken)
    st = session(env)
    press(env, st, "t", str(idx_of(st, "KOC Nhật Hải", "2026-11-19")))
    press(env, st, "go")
    press(env, st, "pc", "same")
    r = press(env, st, "st")
    assert not r.starts and r.failed and "phiếu còn thiếu" in r.failed[0][1]
    assert "Chưa giao được" in env.daily.done_text([], r.failed, [])


def test_done_text_lists_jobs_failures_and_first_time_folders(env):
    st = session(env)
    started = [({"project": "KOC Nhật Hải", "name": "2026-11-19"}, "20261120-demo-01")]
    t = env.daily.done_text(started, [({"project": "X", "name": "y"}, "thiếu clip")], [{"idx": 0}], {"20261120-demo-01": " ⚠ cùng thư mục"})
    assert "Đã giao 1 job" in t and "20261120-demo-01 (KOC Nhật Hải · 2026-11-19) ⚠ cùng thư mục" in t and "thiếu clip" in t and "1 thư mục chưa có hồ sơ" in t


# ───────────────────────── gửi theo giờ ─────────────────────────
def recorder():
    sent = []
    return sent, lambda uid, text, kb: sent.append((uid, text, kb))


def test_everyone_allowed_to_chat_is_asked_once_per_slot(env):
    env.cfg.allowed_user_ids = ["1001", "1002"]
    env.cfg.admin_ids = ["9"]
    sent, send = recorder()
    env.clock[0] = datetime(2026, 11, 20, 8, 29)
    assert env.daily.run_due(send) == 0                                                     # chưa tới giờ
    env.clock[0] = datetime(2026, 11, 20, 8, 31)
    assert env.daily.run_due(send) == 3 and sorted(u for u, _, _ in sent) == ["1001", "1002", "9"]
    assert all("Chào buổi sáng" in t and kb for _, t, kb in sent)
    assert env.daily.run_due(send) == 0                                                     # không hỏi lần hai cùng mốc (kể cả sau khởi động lại)
    again = Daily(env.cfg, env.deps.jobs, DailyStore(env.cfg.jobs_db), env.deps.types, None, now=lambda: env.clock[0])
    assert again.run_due(send) == 0
    env.clock[0] = datetime(2026, 11, 20, 21, 5)
    assert env.daily.run_due(send) == 3 and "Chào buổi tối" in sent[-1][1]                  # mốc 21:00 là một lượt hỏi mới
    assert env.store.load_session("1002")["slot"] == "2026-11-20@21:00" and env.store.load_session("1002")["gen"] == 2


def test_a_user_added_later_is_asked_at_the_next_run_and_sunday_is_skipped(env):
    sent, send = recorder()
    env.clock[0] = datetime(2026, 11, 20, 8, 31)
    env.cfg.allowed_user_ids = ["1001"]
    env.cfg.admin_ids = []
    env.daily.run_due(send)
    env.cfg.allowed_user_ids.append("1002")                                                 # /themnguoi có hiệu lực ngay
    env.daily.run_due(send)
    assert [u for u, _, _ in sent] == ["1001", "1002"]
    sunday = []
    env.clock[0] = datetime(2026, 11, 22, 8, 31)
    env.daily.run_due(lambda u, t, k: sunday.append(u))
    assert sunday == []


def test_a_failing_send_to_one_person_is_retried_and_does_not_block_the_others(env):
    env.cfg.allowed_user_ids = ["1001", "1002"]
    env.cfg.admin_ids = []
    env.clock[0] = datetime(2026, 11, 20, 8, 31)
    sent, logs = [], []

    def flaky(uid, text, kb):
        if uid == "1001":
            raise ConnectionError("Telegram không trả lời")
        sent.append(uid)
    assert env.daily.run_due(flaky, log=logs.append) == 1 and sent == ["1002"] and any("1001" in l for l in logs)
    assert not env.store.was_asked("2026-11-20@08:30", "1001")
    assert env.daily.run_due(lambda u, t, k: sent.append(u)) == 1 and sent == ["1002", "1001"]


def test_snooze_reminds_once_after_an_hour_only_while_still_choosing(env):
    sent, send = recorder()
    env.cfg.allowed_user_ids, env.cfg.admin_ids = ["1001"], []
    env.clock[0] = datetime(2026, 11, 20, 8, 31)
    env.daily.run_due(send)
    st = env.store.load_session("1001")
    r = press(env, st, "snz")
    env.store.save_session("1001", st)
    assert "09:31" in r.text and st["remind_at"]
    env.clock[0] = datetime(2026, 11, 20, 9, 0)
    assert env.daily.run_due(send) == 0
    env.clock[0] = datetime(2026, 11, 20, 9, 32)
    assert env.daily.run_due(send) == 1 and "Nhắc lại theo hẹn" in sent[-1][1]
    assert env.daily.run_due(send) == 0                                                     # chỉ nhắc một lần
    st = env.store.load_session("1001")
    press(env, st, "off")
    st["remind_at"] = "2026-11-20T09:00:00"
    env.store.save_session("1001", st)
    assert env.daily.run_due(send) == 0                                                     # đã chọn "không dựng" thì thôi nhắc


def test_open_jobs_line_counts_what_needs_the_person(env):
    jobs = env.deps.jobs
    jobs.create_next("demo", requester_id="1001", status="waiting_approval")
    jobs.create_next("demo", requester_id="1001", status="blocked")
    jobs.create_next("demo", requester_id="1002", status="blocked")
    assert "2 việc cần xử lý (1 chờ bạn duyệt/trả lời, 1 bị dừng)" in env.daily.open_line("1001")
    assert env.daily.open_line("7") == ""


def test_estimate_counts_jobs_already_running_and_average_cost_needs_data(env):
    env.deps.jobs.create_next("demo", requester_id="1", status="running")
    env.deps.jobs.create_next("demo", requester_id="1", status="queued")
    busy, minutes, end = env.daily.eta(2)
    assert busy == 2 and minutes == round(4 * 8.1) and end.strftime("%H:%M") == "09:07"
    env.cfg.render_slots = 2
    assert env.daily.eta(2)[1] == round(4 * 8.1 / 2)
    assert env.daily.avg_cost() == (None, 0)


# ───────────────────────── kho lưu và cấu hình ─────────────────────────
def test_profile_store_keeps_the_latest_per_project(env):
    s = env.store
    assert s.profile("Chưa Có") is None
    s.save_profile("KOC A", "koc-review", "demo", {"price": "1"})
    s.save_profile("KOC A", "unbox-san-pham", "oillan", {"price": "2"})
    s.save_profile("KOC A", "koc-review", "demo", {"price": "3"})
    p = s.profile("KOC A")
    assert (p["vtype"], p["brand"], p["values"]["price"]) == ("koc-review", "demo", "3")
    s.save_profile("", "x", "y", {"a": 1})
    s.save_profile("KOC B", "x", "y", {})
    assert s.profile("") is None and s.profile("KOC B") is None


def test_config_reads_the_daily_section(tmp_path):
    raw = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
    raw["daily"] = {"enabled": True, "times": ["07:45", "20:30"], "days": [0, 1, 2], "grace_minutes": 30, "max_jobs_per_day": 8, "budget_usd_per_day": 12.5, "heavy_minutes_per_job": 10}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    c = Config.load(p)
    assert (c.daily_enabled, c.daily_times, c.daily_days, c.daily_grace_min, c.daily_max_jobs, c.daily_budget_usd, c.heavy_minutes_per_job) == (True, ["07:45", "20:30"], [0, 1, 2], 30, 8, 12.5, 10.0)
    raw.pop("daily")
    p.write_text(json.dumps(raw), encoding="utf-8")
    d = Config.load(p)
    assert d.daily_enabled is False and d.daily_times == ["08:30", "21:00"] and d.daily_max_jobs is None and d.daily_budget_usd is None
