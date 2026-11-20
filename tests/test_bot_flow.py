"""Bot Telegram chạy thật các handler (nút bấm, menu, wizard, hỏi đáp) với Update giả: không cần mạng, không cần token thật."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from conftest import JOB, make_env

# KHÔNG nạp telegram ở đầu file: test_all_modules_import_without_optional_dependencies kiểm rằng việc nạp module không kéo theo thư viện này
# (thu thập test của mọi file diễn ra trước khi chạy test nào). Nạp muộn, trong build().


class Msg:
    def __init__(self, text="", message_id=77):
        self.text, self.sent, self.chat_id, self.message_id = text, [], 1001, message_id

    async def reply_text(self, text, reply_markup=None, **kw):
        self.sent.append((text, reply_markup))


class Query:
    def __init__(self, data, uid=1001):
        self.data, self.from_user = data, SimpleNamespace(id=uid, full_name="Người Dùng")
        self.message, self.answers, self.edits, self.markup_edits = Msg(), [], [], []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)


class Chat:
    async def send_action(self, action):
        pass


class FakeRunner:
    def __init__(self):
        self.started, self.resumed, self.continued = [], [], []

    def start(self, **kw):
        self.started.append(kw)
        return "20261120-saphir-01"

    def resume(self, job_id, payload):
        self.resumed.append((job_id, payload))

    def continue_(self, job_id):
        self.continued.append(job_id)


class FakeChat:
    asked = []

    def __init__(self, cfg, deps):
        pass

    def spent_today(self, uid):
        return 0.0

    def ask(self, uid, is_admin, text):
        FakeChat.asked.append((uid, is_admin, text))
        from vidagents.chat import Answer
        return Answer("Job đang chạy bình thường.", [JOB])


def build(tmp_path, monkeypatch, uid="1001"):
    pytest.importorskip("telegram")
    import telegram.ext as tgx
    import vidagents.chat as chat_mod
    from vidagents.bot import TelegramNotifier, run_bot

    e = make_env(tmp_path)          # có sẵn người giao việc 1001 và admin 9
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setattr(chat_mod, "ChatAssistant", FakeChat)
    captured = {}
    monkeypatch.setattr(tgx.Application, "run_polling", lambda self, *a, **k: (captured.setdefault("app", self), captured.setdefault("poll_kw", k)))
    runner = FakeRunner()
    run_bot(e.cfg, runner, e.deps, TelegramNotifier(e.cfg))
    app = captured["app"]
    handlers = [h for group in app.handlers.values() for h in group]
    by = {}
    for h in handlers:
        if isinstance(h, tgx.CommandHandler):
            by["/" + sorted(h.commands)[0]] = h.callback
        elif isinstance(h, tgx.CallbackQueryHandler):
            by["button"] = h.callback
    by["error"] = next(iter(app.error_handlers))
    by["trace"] = next(h.callback for h in handlers if isinstance(h, tgx.TypeHandler))
    msg_handlers = [h.callback for h in handlers if isinstance(h, tgx.MessageHandler)]
    by["text"], by["unknown"] = msg_handlers[0], msg_handlers[1]        # thứ tự đăng ký: chữ thường trước, lệnh lạ sau
    return e, runner, by


def upd(text="", query=None, uid=1001):
    msg = query.message if query else Msg(text)
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid, full_name="Người Dùng", first_name="Dùng"), message=None if query else msg,
                           callback_query=query, effective_message=msg, effective_chat=Chat())


class FakeBot:
    def __init__(self):
        self.edited, self.sent = [], []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append((chat_id, message_id, text, reply_markup))


def ctx(user_data=None, args=None):
    return SimpleNamespace(user_data={} if user_data is None else user_data, args=args or [], bot=FakeBot())


def run(coro):
    return asyncio.run(coro)


def kb_labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


def kb_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


def test_start_shows_menu_and_unauthorized_gets_their_id(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    u = upd("/start")
    run(h["/start"](u, ctx()))
    text, markup = u.message.sent[0]
    assert "Xin chào" in text and "🎬 Giao việc mới" in kb_labels(markup) and "👥 Người dùng" not in kb_labels(markup)
    stranger = upd("/start", uid=7777)
    run(h["/start"](stranger, ctx()))
    assert "7777" in stranger.message.sent[0][0] and stranger.message.sent[0][1] is None           # chỉ trả ID của chính họ
    admin = upd("/menu", uid=9)
    run(h["/menu"](admin, ctx()))
    assert "👥 Người dùng" in kb_labels(admin.message.sent[0][1])


def test_wizard_by_buttons_creates_the_job_and_the_form_panel_updates_in_place(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    kit = e.cfg.projects_root / "_brands" / "saphir"
    kit.mkdir(parents=True)
    (kit / "brand.json").write_text(json.dumps({"stores": "Fanpage Saphir", "logo": "x", "tagline": "t", "claims_avoid": ["a"]}), encoding="utf-8")
    c = ctx()

    def tap(label_start, markup):
        data = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text.startswith(label_start))
        q = Query(data)
        run(h["button"](upd(query=q), c))
        return q

    q0 = Query("m|new|-")
    run(h["button"](upd(query=q0), c))
    first_text, first_kb = q0.message.sent[0]                                   # menu "Giao việc mới" gửi màn hình chọn thư mục
    assert "bước 1/4" in first_text and any(l.startswith("🎞 clip-demo") for l in kb_labels(first_kb))
    q = tap("🎞 clip-demo", first_kb)
    q = tap("✅ Chọn thư mục này", q.edits[-1][1])
    q = tap("Video KOC review", q.edits[-1][1])
    q = tap("saphir", q.edits[-1][1])
    panel_text, panel_kb = q.edits[-1]
    assert "📝 PHIẾU BRIEF" in panel_text and "⬜ Sản phẩm chính *" in kb_labels(panel_kb)         # bảng phiếu hiện ngay trong chat
    assert c.user_data["wz_msg"] == (1001, 77)                                                     # nhớ tin bảng để cập nhật tại chỗ
    q = tap("⬜ Sản phẩm chính", panel_kb)
    assert "Gõ giá trị" in q.edits[-1][0]
    u = upd("Nước hoa Saphir")
    run(h["text"](u, c))                                                                           # gõ giá trị: BẢNG được sửa tại chỗ, không gửi tin mới
    assert u.message.sent == [] and len(c.bot.edited) == 1
    cid, mid, text, markup = c.bot.edited[-1]
    assert (cid, mid) == (1001, 77) and "✅ Sản phẩm chính *: Nước hoa Saphir" in text and "Bắt buộc: 2/6" in text
    for field, choice in (("Lợi ích", "Theo lời người nói"), ("Giá còn áp dụng", "Không"), ("Mục tiêu", "Quảng cáo bán hàng"),
                          ("Người nói đã đồng ý", "Có")):
        q = tap("⬜ " + field, markup)
        q = tap(choice, q.edits[-1][1])
        markup = q.edits[-1][1]
    assert "Bắt buộc: 6/6 · ✅ đủ" in q.edits[-1][0]
    q = tap("🚀 Giao việc ngay", markup)
    assert runner.started and runner.started[0]["video_type"] == "koc-review" and runner.started[0]["brand"] == "saphir"
    assert runner.started[0]["brief_prefill"]["product"] == "Nước hoa Saphir" and "wz" not in c.user_data
    assert "20261120-saphir-01" in q.edits[-1][0]


def set_job(e, status, stage="", note=""):
    e.deps.jobs.update(JOB, status=status, stage=stage, note=note)


def test_button_approves_only_when_state_matches_and_stale_buttons_are_refused(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    set_job(e, "waiting_approval", "duyet_1")
    q = Query(f"j|ok|{JOB}")
    run(h["button"](upd(query=q), ctx()))
    assert runner.resumed == [(JOB, {"action": "approve", "by": "1001", "note": "", "mode": None})]
    assert q.message.sent[-1][0] == "Đã ghi nhận." and q.markup_edits == [None]                # nút đã dùng xong thì bỏ
    set_job(e, "running", "build")                                                              # tin cũ: job đã sang bước khác
    runner.resumed.clear()
    stale = Query(f"j|ok|{JOB}")
    run(h["button"](upd(query=stale), ctx()))
    assert runner.resumed == [] and stale.message.sent[-1][0].startswith("Chưa gửi gì cả")


def test_other_users_cannot_press_buttons_of_someone_elses_job(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    e.cfg.allowed_user_ids.append("2002")
    set_job(e, "waiting_approval", "duyet_1")
    q = Query(f"j|ok|{JOB}", uid=2002)
    run(h["button"](upd(query=q, uid=2002), ctx()))
    assert runner.resumed == [] and "Chỉ người giao việc" in q.message.sent[-1][0]
    nobody = Query(f"j|ok|{JOB}", uid=7777)
    run(h["button"](upd(query=nobody, uid=7777), ctx()))
    assert runner.resumed == [] and nobody.answers[-1] == ("Bạn chưa có quyền dùng bot này.", True)


def test_cancel_needs_confirmation_and_can_be_undone(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    set_job(e, "waiting_approval", "duyet_2")
    q = Query(f"j|no|{JOB}")
    run(h["button"](upd(query=q), ctx()))
    assert runner.resumed == [] and any("Chắc chắn hủy" in lbl for lbl in kb_labels(q.markup_edits[-1]))   # chưa hủy, hỏi lại
    back = Query(f"j|nc|{JOB}")
    run(h["button"](upd(query=back), ctx()))
    assert runner.resumed == [] and any("Duyệt và render" in lbl for lbl in kb_labels(back.markup_edits[-1]))   # hoàn lại bàn phím cũ
    yes = Query(f"j|xc|{JOB}")
    run(h["button"](upd(query=yes), ctx()))
    assert runner.resumed == [(JOB, {"action": "cancel", "by": "1001", "note": "", "mode": None})]


def test_revise_button_asks_for_text_then_next_message_is_the_note(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    set_job(e, "waiting_approval", "duyet_2")
    c = ctx()
    q = Query(f"j|rv|{JOB}")
    run(h["button"](upd(query=q), c))
    assert "Gõ góp ý" in q.message.sent[-1][0] and c.user_data["await"]["action"] == "revise" and runner.resumed == []
    u = upd("đổi chữ ở giây 5 thành Saphir")
    run(h["text"](u, c))
    assert runner.resumed == [(JOB, {"action": "revise", "by": "1001", "note": "đổi chữ ở giây 5 thành Saphir", "mode": None})]
    assert "await" not in c.user_data and FakeChat.asked == []                                   # chữ này là góp ý, không phải câu hỏi


def test_free_text_goes_to_the_assistant_with_the_job_buttons(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    set_job(e, "waiting_approval", "duyet_1")
    FakeChat.asked.clear()
    u = upd("job của tôi tới đâu rồi?")
    run(h["text"](u, ctx()))
    assert FakeChat.asked == [("1001", False, "job của tôi tới đâu rồi?")]
    text, markup = u.message.sent[-1]
    assert text == "Job đang chạy bình thường." and any("Duyệt kịch bản" in lbl for lbl in kb_labels(markup)) and "📋 Menu" in kb_labels(markup)


def test_cap_button_raises_the_cap_with_the_amount_shown_on_the_button(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    e.deps.meter.set_cap(JOB, 1.0)
    set_job(e, "blocked", "build", "CostCapExceeded: chạm trần")
    from vidagents import ui
    kb = ui.job_keyboard(e.deps.jobs.get(JOB), cap=1.0)
    label, data = next((lbl, d) for row in kb for lbl, d in row if "Nâng trần" in lbl)
    assert "$2" in label and data == f"j|cp|{JOB}|2"
    q = Query(data)
    run(h["button"](upd(query=q), ctx()))
    assert e.deps.meter.cap_for(JOB) == 2.0 and runner.continued == [JOB]


def test_video_command_without_arguments_opens_the_wizard(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    u = upd("/video")
    run(h["/video"](u, ctx()))
    assert "bước 1/4" in u.message.sent[0][0]


def test_unknown_command_gets_a_reply_with_the_menu(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    u = upd("/viec")
    run(h["unknown"](u, ctx()))
    assert "không có lệnh /viec" in u.message.sent[0][0] and "🎬 Giao việc mới" in kb_labels(u.message.sent[0][1])


def test_error_handler_tells_the_user_and_the_admin_instead_of_staying_silent(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    u, c = upd("/video"), ctx()
    c.error = RuntimeError("NAS mất kết nối")
    run(h["error"](u, c))
    assert "Bot gặp lỗi khi xử lý" in u.message.sent[0][0]                            # người dùng thấy ngay
    assert c.bot.sent == [(9, "⚠ Lỗi bot: RuntimeError: NAS mất kết nối")]            # quản trị được báo
    c2 = ctx()
    c2.error = ValueError("không có update")
    run(h["error"](None, c2))                                                          # lỗi ngoài một update cụ thể cũng không làm sập
    assert c2.bot.sent[0][0] == 9


def test_a_too_old_button_still_gets_processed(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    set_job(e, "running", "build")

    class OldQuery(Query):
        async def answer(self, text=None, show_alert=False):
            raise RuntimeError("Query is too old and response timeout expired or query ID is invalid")

    q = OldQuery(f"j|st|{JOB}")
    run(h["button"](upd(query=q), ctx()))
    assert JOB in q.message.sent[-1][0]                                                # vẫn trả lời tiến độ dù không tắt được vòng xoay


def test_every_update_is_logged_without_free_text_content(tmp_path, monkeypatch, caplog):
    import logging
    e, runner, h = build(tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="vidagents.bot"):
        run(h["trace"](upd(query=Query("m|new|-")), ctx()))
        run(h["trace"](upd("/video"), ctx()))
        run(h["trace"](upd("giá 199k, mật khẩu của tôi là 123"), ctx()))
    log = caplog.text
    assert "nhận NÚT m|new|-" in log and "nhận /video từ 1001" in log and "chữ (" in log
    assert "mật khẩu" not in log and "199k" not in log                                 # nội dung người dùng gõ không vào nhật ký


def test_polling_asks_telegram_for_button_presses_too(tmp_path, monkeypatch):
    """Telegram nhớ loại cập nhật của lần lấy tin trước; thiếu callback_query thì nút bấm im lặng hoàn toàn."""
    import telegram
    cap = {}
    import telegram.ext as tgx
    orig = tgx.Application.run_polling
    build(tmp_path, monkeypatch)
    monkeypatch.setattr(tgx.Application, "run_polling", lambda self, *a, **k: cap.update(k))
    from vidagents import bot
    import inspect
    assert "allowed_updates=Update.ALL_TYPES" in inspect.getsource(bot.run_bot)
    assert "callback_query" in telegram.Update.ALL_TYPES


def test_wizard_offers_last_time_from_the_users_own_previous_jobs_only(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    kit = e.cfg.projects_root / "_brands" / "saphir"
    kit.mkdir(parents=True)
    (kit / "brand.json").write_text(json.dumps({"stores": "Fanpage Saphir", "logo": "x", "tagline": "t", "claims_avoid": ["a"]}), encoding="utf-8")
    folder = str(e.cfg.footage_root / "clip-demo")
    e.deps.jobs.create("20261119-saphir-01", requester_id="1001", brand="saphir", video_type="koc-review", footage_dir=folder, status="done")
    e.deps.jobs.create("20261119-saphir-02", requester_id="9999", brand="saphir", video_type="unbox-san-pham", footage_dir=folder, status="done")   # của người khác
    e.deps.jobs.create("20261119-saphir-03", requester_id="1001", brand="saphir", video_type="phong-van-khach", footage_dir=folder, status="cancelled")  # đã hủy: không tính
    c = ctx()

    def tap(label_start, markup):
        data = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text.startswith(label_start))
        q = Query(data)
        run(h["button"](upd(query=q), c))
        return q

    q0 = Query("m|new|-")
    run(h["button"](upd(query=q0), c))
    q = tap("🎞 clip-demo", q0.message.sent[0][1])
    q = tap("✅ Chọn thư mục này", q.edits[-1][1])
    labels_ = kb_labels(q.edits[-1][1])
    assert labels_[0].startswith("⚡ Như lần trước:") and "koc" in labels_[0].lower() or "KOC" in labels_[0]       # đúng việc của chính người này, bỏ qua việc hủy/của người khác
    assert "unbox" not in labels_[0].lower() and "phỏng vấn" not in labels_[0].lower()
    q = tap("⚡ Như lần trước", q.edits[-1][1])
    assert "📝 PHIẾU BRIEF" in q.edits[-1][0]                                                      # nhảy thẳng tới phiếu, bỏ 2 bước
