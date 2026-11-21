"""Hỏi hàng ngày chạy qua các handler thật của bot (Update giả): /homnay, nút bấm, giao job, mở phiếu lần đầu, gõ giá mới, lưu hồ sơ brief."""
import inspect
import json
from pathlib import Path

import pytest

from conftest import JOB
from test_bot_flow import FakeRunner, Query, build, ctx, kb_data, kb_labels, run, upd
from test_daily import VALUES, mk

from vidagents import bot as bot_mod
from vidagents.daily import EXPIRED, DailyStore

U = 1001


@pytest.fixture
def bot(tmp_path, monkeypatch):
    holder = {}

    def start(self, **kw):                                         # runner giả nhưng GHI SỔ thật để bot tìm được job trùng footage
        r = holder["e"].deps.jobs.create_next(kw["brand"], requester_id=str(kw["requester_id"]), requester_name=kw["requester_name"],
                                              footage_dir=kw["footage_dir"], video_type=kw["video_type"], status="running")
        self.started.append(kw)
        return r
    monkeypatch.setattr(FakeRunner, "start", start)
    e, runner, h = build(tmp_path, monkeypatch)
    holder["e"] = e
    e.deps.jobs.update(JOB, status="done")
    nas = tmp_path / "nas"
    e.cfg.footage_root = nas
    mk(nas, "KOC Nhật Hải", "2026-11-19", 4)
    mk(nas, "Trang Chun", "2026-11-20", 2)
    e.store = DailyStore(e.cfg.jobs_db)
    e.store.save_profile("KOC Nhật Hải", "koc-review", "demo", {**VALUES, "deadline": "31/12/2099"})
    e.nas = nas
    return e, runner, h


def tap(h, c, markup, label_start, uid=U):
    data = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text.lstrip("✅⬜ ").startswith(label_start) or b.text.startswith(label_start))
    q = Query(data, uid=uid)
    run(h["button"](upd(query=q, uid=uid), c))
    return q


def last_kb(q):
    return q.edits[-1][1]


def test_homnay_asks_any_allowed_user_and_gives_a_stranger_only_their_id(bot):
    e, runner, h = bot
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, ctx()))
    text, markup = u.message.sent[0]
    assert "Bạn muốn dựng video nào?" in text and "KOC Nhật Hải · 2026-11-19 · 4 clip" in " ".join(kb_labels(markup))
    assert all(d.startswith("d|") for d in kb_data(markup)) and max(len(d.encode()) for d in kb_data(markup)) <= 64
    s = upd("/homnay", uid=7777)
    run(h["/homnay"](s, ctx()))
    assert "7777" in s.message.sent[0][0] and s.message.sent[0][1] is None
    m = Query("m|today|-", uid=U)                                    # nút "Hôm nay dựng gì" trong menu
    run(h["button"](upd(query=m, uid=U), ctx()))
    assert "Bạn muốn dựng video nào?" in m.message.sent[0][0]


def test_menu_shows_the_today_button_to_everyone(bot):
    e, runner, h = bot
    u = upd("/menu", uid=U)
    run(h["/menu"](u, ctx()))
    assert "📅 Hôm nay dựng gì" in kb_labels(u.message.sent[0][1])


def test_full_button_flow_creates_the_job_with_the_saved_brief_and_todays_price_choice(bot):
    e, runner, h = bot
    c = ctx()
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, c))
    markup = u.message.sent[0][1]
    q = tap(h, c, markup, "KOC Nhật Hải")
    q = tap(h, c, last_kb(q), "➡️ Tiếp")
    assert "Video 1/1" in q.edits[-1][0] and "Dùng lại như trên" in " ".join(kb_labels(last_kb(q)))
    q = tap(h, c, last_kb(q), "✅ Dùng lại")
    assert "Kế hoạch: 1 video" in q.edits[-1][0]
    q = tap(h, c, last_kb(q), "🚀 Bắt đầu dựng")
    text = q.edits[-1][0]
    assert "✅ Đã giao 1 job:" in text and "KOC Nhật Hải · 2026-11-19" in text
    assert len(runner.started) == 1
    s = runner.started[0]
    assert s["requester_id"] == str(U) and s["video_type"] == "koc-review" and s["brand"] == "demo"
    assert s["footage_dir"] == str(e.nas / "KOC Nhật Hải" / "2026-11-19") and "199.000đ" in s["brief_text"]
    jobs = [j for j in e.deps.jobs.list(None, active_only=False) if "KOC Nhật Hải" in j.get("footage_dir", "")]
    assert len(jobs) == 1 and jobs[0]["status"] == "running" and jobs[0]["requester_id"] == str(U)
    q2 = Query(last_kb(q) and "d|st|1.", uid=U)                      # bấm lại nút cũ: không giao trùng
    run(h["button"](upd(query=q2, uid=U), c))
    assert len(runner.started) == 1


def test_second_person_picking_the_same_day_is_warned_about_the_first(bot):
    e, runner, h = bot
    e.deps.jobs.create_next("demo", requester_id="2", requester_name="Hà", footage_dir=str(e.nas / "KOC Nhật Hải" / "2026-11-19"), status="running")
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, ctx()))
    assert "KOC Nhật Hải · 2026-11-19" not in " ".join(kb_labels(u.message.sent[0][1]))          # thư mục đang có job: không mời dựng trùng
    assert "Đang có job trên các thư mục" in u.message.sent[0][0] and "(Hà)" in u.message.sent[0][0]


def test_first_time_folder_opens_the_brief_form_and_the_other_folders_are_not_held_up(bot):
    e, runner, h = bot
    c = ctx()
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, c))
    q = tap(h, c, u.message.sent[0][1], "KOC Nhật Hải")
    q = tap(h, c, last_kb(q), "Trang Chun")
    q = tap(h, c, last_kb(q), "➡️ Tiếp")
    q = tap(h, c, last_kb(q), "✅ Dùng lại")
    assert "CHƯA có hồ sơ brief" in q.edits[-1][0]
    q = tap(h, c, last_kb(q), "🚀 Bắt đầu dựng")
    assert "Đã giao 1 job" in q.edits[-1][0] and "1 thư mục chưa có hồ sơ" in q.edits[-1][0]
    prompt_text, prompt_kb = q.message.sent[0]
    assert "Trang Chun · 2026-11-20" in prompt_text and "chưa có hồ sơ brief" in prompt_text
    w = tap(h, c, prompt_kb, "📝 Điền phiếu")
    assert "wz" in c.user_data and c.user_data["wz"]["folder"] == str(e.nas / "Trang Chun" / "2026-11-20")
    assert "Bước 2/4" in w.message.sent[-1][0]                                                  # vào thẳng bước chọn loại video của luồng giao việc


def test_typing_todays_price_goes_to_the_daily_flow_not_the_chat_assistant(bot):
    e, runner, h = bot
    c = ctx()
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, c))
    q = tap(h, c, u.message.sent[0][1], "KOC Nhật Hải")
    q = tap(h, c, last_kb(q), "➡️ Tiếp")
    q = tap(h, c, last_kb(q), "✏️ Gõ giá")
    assert "gõ giá và ưu đãi HÔM NAY" in q.edits[-1][0]
    bad = upd("giá rẻ lắm", uid=U)
    run(h["text"](bad, c))
    assert bad.message.sent[0][0].startswith("⚠ Chưa đọc được dòng giá")
    good = upd("Giá còn áp dụng: Có, giá 219.000đ\nƯu đãi: Giảm 15%\nHạn ưu đãi: 31/12/2099", uid=U)
    run(h["text"](good, c))
    text, markup = good.message.sent[0]
    assert text.startswith("Kế hoạch: 1 video") and "219.000đ" in text and "Giảm 15% đến 31/12/2099" in text
    q = tap(h, c, markup, "🚀 Bắt đầu dựng")
    assert "219.000đ" in runner.started[-1]["brief_text"]
    p = e.store.profile("KOC Nhật Hải")                                                          # giá mới trở thành mặc định gợi ý cho lần sau
    assert p["values"]["price"] == "Có, giá 219.000đ" and p["values"]["offer"] == "Giảm 15%"


def test_text_is_not_swallowed_after_the_person_moves_on_to_the_job_form(bot):
    e, runner, h = bot
    c = ctx()
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, c))
    q = tap(h, c, u.message.sent[0][1], "KOC Nhật Hải")
    q = tap(h, c, last_kb(q), "➡️ Tiếp")
    q = tap(h, c, last_kb(q), "✏️ Gõ giá")
    assert e.store.load_session(str(U))["stage"] == "text"
    run(h["/video"](upd("/video", uid=U), c))                                                    # mở giao việc bằng nút: thôi chờ chữ giá/ưu đãi
    assert e.store.load_session(str(U))["stage"] == "pick"


def test_buttons_without_a_session_or_from_an_older_question_say_expired(bot):
    e, runner, h = bot
    q = Query("d|go|1.", uid=U)
    run(h["button"](upd(query=q, uid=U), ctx()))
    assert EXPIRED in q.message.sent[0][0]
    c = ctx()
    u = upd("/homnay", uid=U)
    run(h["/homnay"](u, c))
    old = u.message.sent[0][1]
    run(h["/homnay"](upd("/homnay", uid=U), c))                                                  # hỏi lại: lượt cũ hết hạn
    q = tap(h, c, old, "KOC Nhật Hải")
    assert not q.edits and EXPIRED in q.message.sent[0][0]


def test_a_job_started_with_the_form_is_remembered_as_that_koc_brief_profile(bot):
    e, runner, h = bot
    kit = e.cfg.projects_root / "_brands" / "saphir"
    kit.mkdir(parents=True)
    (kit / "brand.json").write_text(json.dumps({"stores": "Fanpage Saphir", "logo": "x", "tagline": "t", "claims_avoid": ["a"]}), encoding="utf-8")
    mk(e.nas, "KOC Minh Thư", "2026-11-21", 2)
    c = ctx()
    q0 = Query("m|new|-", uid=U)
    run(h["button"](upd(query=q0, uid=U), c))
    markup = q0.message.sent[0][1]
    q = tap(h, c, markup, "📁 KOC Minh Thư")
    q = tap(h, c, last_kb(q), "🎞 2026-11-21")
    q = tap(h, c, last_kb(q), "✅ Chọn thư mục này")
    q = tap(h, c, last_kb(q), "Video KOC review")
    q = tap(h, c, last_kb(q), "saphir")
    markup = last_kb(q)
    q = tap(h, c, markup, "⬜ Sản phẩm chính")
    u = upd("Nước hoa Saphir", uid=U)
    run(h["text"](u, c))
    markup = c.bot.edited[-1][3]
    for field, choice in (("Lợi ích", "Theo lời người nói"), ("Giá còn áp dụng", "Không"), ("Mục tiêu", "Quảng cáo bán hàng"), ("Người nói đã đồng ý", "Có")):
        q = tap(h, c, markup, "⬜ " + field)
        q = tap(h, c, last_kb(q), choice)
        markup = last_kb(q)
    tap(h, c, markup, "🚀 Giao việc ngay")
    assert runner.started and runner.started[0]["brand"] == "saphir"
    p = e.store.profile("KOC Minh Thư")                                                          # khóa không phân biệt hoa/thường nên Windows hạ chữ thường vẫn khớp
    assert p and p["brand"] == "saphir" and p["vtype"] == "koc-review" and p["values"]["product"] == "Nước hoa Saphir" and p["values"]["price"] == "Không"
    e.deps.jobs.update(next(j["job_id"] for j in e.deps.jobs.list(None, active_only=False) if "KOC Minh Thư" in j.get("footage_dir", "")), status="done")
    mk(e.nas, "KOC Minh Thư", "2026-11-22", 2)
    nxt = upd("/homnay", uid=U)
    c2 = ctx()
    run(h["/homnay"](nxt, c2))                                                                   # lần sau KOC này chỉ hỏi giá và ưu đãi, không bắt điền lại phiếu
    q = tap(h, c2, nxt.message.sent[0][1], "KOC Minh Thư")
    q = tap(h, c2, last_kb(q), "➡️ Tiếp")
    assert "Video 1/1" in q.edits[-1][0] and "Loại: " in q.edits[-1][0] and "saphir" in q.edits[-1][0]


def test_scheduler_is_started_only_after_the_bot_is_attached_and_only_when_enabled():
    src = inspect.getsource(bot_mod.run_bot)
    assert "if cfg.daily_enabled:" in src and "start_daily_thread" in src
    assert src.index("notifier.attach(app") < src.index("start_daily_thread")
