"""/themloai chạy qua các handler thật của bot (Update giả, không mạng): chỉ admin, nút sửa tại chỗ, ghi file, nạp thẳng vào danh sách đang chạy."""
import json
import shutil

import pytest

from conftest import REPO
from test_bot_flow import Query, ctx, build, kb_data, kb_labels, run, upd

ADMIN, USER = 9, 1001


@pytest.fixture
def bot(tmp_path, monkeypatch):
    e, runner, h = build(tmp_path, monkeypatch)
    vdir = tmp_path / "video_types"
    shutil.copytree(REPO / "video_types", vdir)           # không bao giờ ghi vào video_types thật của repo
    e.cfg.video_types_dir = vdir
    return e, h, vdir


def press(h, c, data, uid=ADMIN):
    q = Query(data, uid=uid)
    run(h["button"](upd(query=q, uid=uid), c))
    return q


def say(h, c, text, uid=ADMIN):
    u = upd(text, uid=uid)
    run(h["text"](u, c))
    return u.message.sent


def test_command_opens_the_wizard_for_admin_only(bot):
    e, h, _ = bot
    c = ctx()
    u = upd("/themloai", uid=ADMIN)
    run(h["/themloai"](u, c))
    text, markup = u.message.sent[0]
    assert "THÊM DẠNG VIDEO" in text and "t|tpl|-" in kb_data(markup) and "t|tpl|koc-review" in kb_data(markup) and "tw" in c.user_data
    c2 = ctx()
    u = upd("/themloai", uid=USER)                                   # người giao thường: từ chối, không có trạng thái
    run(h["/themloai"](u, c2))
    assert "chỉ dành cho quản trị viên" in u.message.sent[0][0] and "tw" not in c2.user_data and u.message.sent[0][1] is None
    u = upd("/themloai", uid=7777)                                   # người lạ: chỉ nhận ID của chính họ
    run(h["/themloai"](u, ctx()))
    assert "7777" in u.message.sent[0][0]


def test_menu_button_only_for_admin(bot):
    e, h, _ = bot
    u = upd("/menu", uid=ADMIN)
    run(h["/menu"](u, ctx()))
    assert "➕ Thêm dạng video" in kb_labels(u.message.sent[0][1])
    u = upd("/menu", uid=USER)
    run(h["/menu"](u, ctx()))
    assert "➕ Thêm dạng video" not in kb_labels(u.message.sent[0][1])
    c = ctx()
    q = press(h, c, "m|addtype|-", uid=ADMIN)
    assert "tw" in c.user_data and "THÊM DẠNG VIDEO" in q.message.sent[0][0]
    c2 = ctx()
    press(h, c2, "m|addtype|-", uid=USER)
    assert "tw" not in c2.user_data


def test_full_flow_through_the_real_handlers_writes_the_file_and_updates_the_live_list(bot):
    e, h, vdir = bot
    c = ctx()
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    q = press(h, c, "t|tpl|-", uid=ADMIN)
    assert q.edits and "bước 2/6" in q.edits[-1][0]                    # nút sửa tại chỗ tin đang hiện, không gửi tin mới
    sent = say(h, c, "Video so sánh với đối thủ")
    assert "so-sanh-voi-doi-thu" in sent[0][0] and "t|cok|-" in kb_data(sent[0][1])
    press(h, c, "t|cok|-"), press(h, c, "t|m|B"), press(h, c, "t|s|45")
    press(h, c, "t|f|price_text")
    press(h, c, "t|cadd|-")
    sent = say(h, c, "Điểm so sánh chính; Bảng so sánh")
    assert "Điểm so sánh chính" in sent[0][0] and "t|cdone|-" in kb_data(sent[0][1])
    press(h, c, "t|cdone|-"), press(h, c, "t|fdone|-")
    say(h, c, "Không nêu tên đối thủ.")
    q = press(h, c, "t|ndone|-")
    assert "XEM LẠI TRƯỚC KHI THÊM" in q.edits[-1][0] and "t|add|-" in kb_data(q.edits[-1][1])
    assert not (vdir / "so-sanh-voi-doi-thu.json").exists()           # chưa bấm Thêm thì chưa ghi gì
    q = press(h, c, "t|add|-")
    assert "✅ Đã thêm dạng so-sanh-voi-doi-thu" in q.edits[-1][0] and "tw" not in c.user_data
    d = json.loads((vdir / "so-sanh-voi-doi-thu.json").read_text(encoding="utf-8"))
    assert d["required_brief"] == ["price_text", "extra.diem_so_sanh_chinh", "extra.bang_so_sanh"] and d["notes"] == "Không nêu tên đối thủ."
    assert "so-sanh-voi-doi-thu" in e.deps.types                       # nạp thẳng, không cần khởi động lại bot
    u = upd("/loai", uid=USER)                                          # người giao thường thấy dạng mới ngay
    run(h["/loai"](u, ctx()))
    assert "so-sanh-voi-doi-thu · Video so sánh với đối thủ" in u.message.sent[0][0]
    assert "m|addtype|-" in kb_data(q.edits[-1][1])


def test_non_admin_cannot_drive_someone_elses_wizard_state_and_text_is_not_swallowed(bot):
    e, h, vdir = bot
    c = ctx()
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    q = press(h, c, "t|tpl|-", uid=USER)                                 # người thường bấm nút (vd. tin được chuyển tiếp)
    assert "Chỉ quản trị viên" in q.message.sent[0][0] and c.user_data["tw"]["stage"] == "tpl"
    sent = say(h, c, "Video so sánh với đối thủ", uid=USER)             # chữ của người thường không đi vào wizard của admin
    assert "tw" not in c.user_data and "Job đang chạy bình thường." in sent[0][0]
    assert not list(vdir.glob("so-sanh*"))


def test_stale_button_is_answered_with_a_new_message_and_does_not_overwrite_the_current_step(bot):
    e, h, _ = bot
    c = ctx()
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    q = press(h, c, "t|m|C")                                            # đang ở bước chọn mẫu, bấm nút chọn chế độ của tin cũ
    assert not q.edits and "hết hạn" in q.message.sent[0][0] and c.user_data["tw"]["stage"] == "tpl" and not c.user_data["tw"]["mode"]


def test_button_without_a_live_session_says_expired(bot):
    e, h, _ = bot
    q = press(h, ctx(), "t|tpl|-")
    assert "hết hạn" in q.message.sent[0][0]


def test_huybo_and_other_wizards_clear_the_type_wizard(bot):
    e, h, _ = bot
    c = ctx()
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    u = upd("/huybo", uid=ADMIN)
    run(h["/huybo"](u, c))
    assert "tw" not in c.user_data and "Đã bỏ" in u.message.sent[0][0]
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    run(h["/video"](upd("/video", uid=ADMIN), c))                       # mở giao việc bằng nút thì bỏ wizard thêm dạng
    assert "tw" not in c.user_data and "wz" in c.user_data


def test_duplicate_code_typed_by_the_admin_is_refused_and_nothing_is_written(bot):
    e, h, vdir = bot
    before = sorted(p.name for p in vdir.glob("*.json"))
    c = ctx()
    run(h["/themloai"](upd("/themloai", uid=ADMIN), c))
    press(h, c, "t|tpl|-")
    sent = say(h, c, "KOC review")                                      # mã gợi ý koc-review đã có -> bắt đổi mã
    assert "đã có" in sent[0][0] and "t|cok|-" not in kb_data(sent[0][1])
    sent = say(h, c, "koc-review")
    assert sent[0][0].startswith("⚠") and c.user_data["tw"]["stage"] == "codeEdit"
    assert sorted(p.name for p in vdir.glob("*.json")) == before
