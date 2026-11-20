"""Thêm dạng video bằng nút bấm (typewiz): luồng đúng, mọi kiểu gõ sai, nút cũ, không bao giờ đè file có sẵn."""
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from vidagents import phieu
from vidagents import schemas as S
from vidagents import typewiz as T

REPO_TYPES = Path(__file__).resolve().parent.parent / "video_types"


@pytest.fixture
def env(tmp_path):
    vdir, adir = tmp_path / "video_types", tmp_path / "agents" / "edit"
    shutil.copytree(REPO_TYPES, vdir)
    adir.mkdir(parents=True)
    (adir / "tools.json").write_text(json.dumps({"actions": {"build": {"A": [], "B": [{"name": "build"}], "C": [], "S": []}}}), encoding="utf-8")
    cfg = SimpleNamespace(video_types_dir=vdir, agents_dir=tmp_path / "agents")
    types = S.load_video_types(vdir)
    st, first = T.start(types)
    return SimpleNamespace(cfg=cfg, types=types, st=st, first=first, vdir=vdir)


def cb(e, code, arg=""):
    return T.on_callback(e.st, e.cfg, e.types, code, arg)


def tx(e, text):
    return T.on_text(e.st, e.cfg, e.types, text)


def labels(r):
    return [b[0] for row in r.kb for b in row]


def to_preview(e, name="Video so sánh với đối thủ", mode="B", secs="45"):
    cb(e, "tpl")
    tx(e, name)
    cb(e, "cok")
    cb(e, "m", mode)
    cb(e, "s", secs)
    return cb(e, "fdone")   # -> bước ghi chú


# ───────────────────────── luồng đúng ─────────────────────────
def test_happy_path_writes_a_loadable_file_and_updates_the_live_list(env):
    cb(env, "tpl")
    r = tx(env, "Video so sánh với đối thủ")
    assert "so-sanh-voi-doi-thu" in r.text and env.st["stage"] == "code"
    cb(env, "cok"), cb(env, "m", "B"), cb(env, "s", "45")
    cb(env, "f", "price_text")
    cb(env, "cadd")
    tx(env, "Điểm so sánh chính; Bảng so sánh")
    cb(env, "cdone")
    r = cb(env, "fdone")
    assert env.st["stage"] == "note" and "Bỏ qua" in " ".join(labels(r))
    tx(env, "Không nêu tên đối thủ.")
    tx(env, "Chỉ so sánh tính năng đã công bố.")
    prev = cb(env, "ndone")
    assert env.st["stage"] == "preview" and "so-sanh-voi-doi-thu.json" in prev.text and "Bảng so sánh" in prev.text
    done = cb(env, "add")
    assert done.end and "✅ Đã thêm dạng so-sanh-voi-doi-thu" in done.text
    path = env.vdir / "so-sanh-voi-doi-thu.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["id"] == "so-sanh-voi-doi-thu" and d["suggested_mode"] == "B" and d["target_seconds"] == 45 and d["status"] == "nhap"
    assert d["required_brief"] == ["price_text", "extra.diem_so_sanh_chinh", "extra.bang_so_sanh"]
    assert d["labels"] == {"extra.diem_so_sanh_chinh": "Điểm so sánh chính", "extra.bang_so_sanh": "Bảng so sánh"}
    assert d["notes"] == "Không nêu tên đối thủ. Chỉ so sánh tính năng đã công bố."
    assert "so-sanh-voi-doi-thu" in env.types                                   # /loai và /video thấy ngay, không cần khởi động lại
    assert "so-sanh-voi-doi-thu" in S.load_video_types(env.vdir)                 # bộ nạp của hệ thống đọc được


def test_new_type_flows_into_the_brief_form_and_required_checks(env):
    cb(env, "tpl"), tx(env, "Video so sánh với đối thủ"), cb(env, "cok"), cb(env, "m", "B"), cb(env, "s", "45")
    cb(env, "cadd"), tx(env, "Điểm so sánh chính"), cb(env, "cdone"), cb(env, "fdone"), cb(env, "nskip")
    assert cb(env, "add").end
    vt = env.types["so-sanh-voi-doi-thu"]
    fs = {f.label: f for f in phieu.fields_for(vt)}
    assert fs["Điểm so sánh chính"].req and fs["Điểm so sánh chính"].section == "D"       # ô riêng hiện ở mục D, bắt buộc
    assert any(f.key == "consent" for f in phieu.fields_for(vt))                           # chế độ B: ô đồng ý xuất hiện tự có
    assert "extra.diem_so_sanh_chinh" in S.missing_fields(S.BriefExtract(brand="x", product="y", goal="z"), vt)


def test_template_copies_mode_length_fields_and_keeps_original_labels(env):
    cb(env, "tpl", "uu-dai-ctkm")
    assert (env.st["mode"], env.st["secs"], env.st["fields"]) == ("A", 20, ["offer_text", "offer_deadline", "where_to_buy"])
    e2 = SimpleNamespace(**{**env.__dict__, "st": T.new_state()})
    T.on_callback(e2.st, e2.cfg, e2.types, "tpl", "dao-tao-noi-bo")
    assert e2.st["mode"] == "S" and [k for k, _ in e2.st["custom"]] == ["extra.audience", "extra.source_slides"]
    assert [l for _, l in e2.st["custom"]] == ["Đối tượng xem", "File slide nguồn"]


# ───────────────────────── gõ sai ─────────────────────────
def test_short_name_is_refused_and_stays_on_the_step(env):
    cb(env, "tpl")
    r = tx(env, "ab")
    assert r.text.startswith("⚠") and env.st["stage"] == "name"


def test_suggested_code_that_already_exists_forces_a_new_code(env):
    cb(env, "tpl")
    r = tx(env, "KOC review")           # gợi ý koc-review, đã có
    assert "đã có" in r.text and env.st["stage"] == "codeEdit" and "Dùng mã này" not in " ".join(labels(r))


@pytest.mark.parametrize("bad,why", [("koc-review", "đã có"), ("So Sanh", "chữ thường"), ("so sanh", "chữ thường"), ("-abc", "chữ thường"),
                                     ("con", "Windows"), ("a" * 41, "dài quá")])
def test_bad_codes_are_refused_with_a_reason(env, bad, why):
    cb(env, "tpl"), tx(env, "Dạng thử nghiệm"), cb(env, "cedit")
    r = tx(env, bad)
    assert r.text.startswith("⚠") and why in r.text and env.st["stage"] == "codeEdit" and env.st["code"] == "dang-thu-nghiem"


def test_stage_flow_after_editing_the_code(env):
    cb(env, "tpl"), tx(env, "Dạng thử nghiệm"), cb(env, "cedit")
    tx(env, "thu-nghiem-2")
    assert env.st["stage"] == "mode" and env.st["code"] == "thu-nghiem-2"


@pytest.mark.parametrize("text", ["4", "601", "abc", "-3", "12.5"])
def test_seconds_out_of_range_is_refused(env, text):
    to_preview(env)  # đi tới note; quay lại bước secs bằng nút sửa
    cb(env, "nskip"), cb(env, "edit"), cb(env, "e", "secs")
    r = tx(env, text)
    assert r.text.startswith("⚠") and env.st["secs"] == 45 and env.st["stage"] == "secs"


def test_custom_labels_many_at_once_with_reasons_for_the_bad_ones(env):
    to_preview(env)
    cb(env, "nskip"), cb(env, "edit"), cb(env, "e", "fields"), cb(env, "cadd")
    r = tx(env, "Điểm so sánh chính; Điểm so sánh chính; ab; Giá: đối thủ; Sản phẩm chính; Giá hiển thị; Consent; Bảng giá (VNĐ); Thông số")
    got = [l for _, l in env.st["custom"]]
    assert got == ["Điểm so sánh chính", "Thông số"]
    for why in ("đã có trong danh sách", "quá ngắn", "không được chứa", "trùng với một ô có sẵn"):
        assert why in r.text
    r = tx(env, "ab; Sản phẩm chính")
    assert r.text.startswith("⚠ Chưa thêm được") and [l for _, l in env.st["custom"]] == got


def test_custom_undo_and_remove_one(env):
    to_preview(env)
    cb(env, "nskip"), cb(env, "edit"), cb(env, "e", "fields"), cb(env, "cadd")
    tx(env, "Ô một; Ô hai; Ô ba")
    cb(env, "cundo")
    assert [l for _, l in env.st["custom"]] == ["Ô một", "Ô hai"]
    cb(env, "cdone")
    cb(env, "cd", "0")
    assert [l for _, l in env.st["custom"]] == ["Ô hai"]


def test_notes_many_undo_and_limits(env):
    to_preview(env)
    r = tx(env, "Ghi chú một")
    tx(env, "Ghi chú hai"), tx(env, "Ghi chú ba")
    cb(env, "nundo")
    assert env.st["notes"] == ["Ghi chú một", "Ghi chú hai"] and "Ghi chú hiện có" in r.text
    assert tx(env, "x" * 201).text.startswith("⚠") and len(env.st["notes"]) == 2
    for _ in range(4):
        tx(env, "y" * 190)
    assert sum(map(len, env.st["notes"])) <= T.MAX_NOTES_TOTAL


# ───────────────────────── nút cũ, hủy, hết hạn ─────────────────────────
def test_stale_button_does_nothing(env):
    to_preview(env)                      # đang ở bước ghi chú
    before = json.dumps(env.st, sort_keys=True, default=str)
    for code, arg in (("m", "C"), ("tpl", "koc-review"), ("f", "price_text"), ("add", ""), ("cok", "")):
        r = cb(env, code, arg)
        assert r.text == T.EXPIRED_BTN and not r.end
    assert json.dumps(env.st, sort_keys=True, default=str) == before
    assert not (env.vdir / "so-sanh-voi-doi-thu.json").exists()


def test_cancel_writes_nothing(env):
    to_preview(env)
    r = cb(env, "x")
    assert r.end and "chưa ghi gì" in r.text
    assert sorted(p.name for p in env.vdir.glob("*.json")) == sorted(p.name for p in REPO_TYPES.glob("*.json"))


def test_expired_session(env):
    env.st["ts"] = time.time() - T.TTL_S - 5
    assert cb(env, "tpl").end and tx(env, "abc").end
    assert T.EXPIRED in cb(env, "tpl").text


def test_text_is_ignored_on_button_only_steps(env):
    assert tx(env, "xin chào") is None            # bước chọn mẫu: bot cho chữ đi hỏi đáp thường
    cb(env, "tpl"), tx(env, "Dạng thử nghiệm"), cb(env, "cok")
    assert env.st["stage"] == "mode" and tx(env, "B") is None


# ───────────────────────── sửa từ màn xem lại ─────────────────────────
def test_edit_from_preview_returns_to_preview_and_name_goes_through_code(env):
    to_preview(env)
    cb(env, "nskip")
    cb(env, "edit"), cb(env, "e", "mode")
    assert env.st["stage"] == "mode"
    r = cb(env, "m", "auto")
    assert env.st["stage"] == "preview" and "Chế độ đề xuất: auto" in r.text
    cb(env, "edit"), cb(env, "e", "name")
    r = tx(env, "So sánh đối thủ trực tiếp")
    assert env.st["stage"] == "code" and "so-sanh-doi-thu-truc-tiep" in r.text
    r = cb(env, "cok")
    assert env.st["stage"] == "preview" and "so-sanh-doi-thu-truc-tiep.json" in r.text
    assert cb(env, "edit") and cb(env, "e", "back").text.startswith("XEM LẠI")


# ───────────────────────── an toàn khi ghi ─────────────────────────
def test_commit_never_overwrites_an_existing_file(env):
    to_preview(env)
    cb(env, "nskip")
    mine = env.vdir / "so-sanh-voi-doi-thu.json"
    mine.write_text('{"id": "so-sanh-voi-doi-thu", "name_vi": "của người khác", "suggested_mode": "B"}', encoding="utf-8")   # admin khác vừa thêm trước
    r = cb(env, "add")
    assert not r.end and "đã có" in r.text
    assert "của người khác" in mine.read_text(encoding="utf-8")        # file cũ còn nguyên
    assert "so-sanh-voi-doi-thu" not in env.types


def test_commit_race_between_validation_and_write_still_never_overwrites(env, monkeypatch):
    to_preview(env)
    cb(env, "nskip")
    mine = env.vdir / "so-sanh-voi-doi-thu.json"
    mine.write_text('{"id": "so-sanh-voi-doi-thu", "name_vi": "của người khác", "suggested_mode": "B"}', encoding="utf-8")
    monkeypatch.setattr(T, "validate_all", lambda *a, **k: [])         # kiểm trùng đã qua rồi file mới xuất hiện: lớp bảo vệ cuối là mở file bằng "x"
    r = cb(env, "add")
    assert not r.end and "trước bạn" in r.text
    assert "của người khác" in mine.read_text(encoding="utf-8") and "so-sanh-voi-doi-thu" not in env.types


def test_unwritable_folder_reports_and_adds_nothing(env, tmp_path):
    to_preview(env)
    cb(env, "nskip")
    env.cfg.video_types_dir = tmp_path / "khong-co-thu-muc"          # không có thư mục để ghi
    env.types.pop("so-sanh-voi-doi-thu", None)
    r = cb(env, "add")
    assert not r.end and "Không ghi được" in r.text and "so-sanh-voi-doi-thu" not in env.types


def test_validate_all_blocks_invalid_state(env):
    to_preview(env)
    env.st["mode"] = "Z"
    r = cb(env, "nskip")                  # preview vẫn dựng được; ghi thì bị chặn
    r = cb(env, "add")
    assert "Chưa ghi được" in r.text and "chế độ" in r.text.lower()


def test_not_ready_mode_warns_in_preview_and_after_saving(env):
    to_preview(env, mode="C")
    prev = cb(env, "nskip")
    assert "chưa có lệnh dựng" in prev.text
    done = cb(env, "add")
    assert done.end and "chưa có lệnh dựng" in done.text and "so-sanh-voi-doi-thu" in env.types


def test_mode_ready_reads_the_real_manifest(env):
    assert T.mode_ready(env.cfg, "B") and not T.mode_ready(env.cfg, "A") and T.mode_ready(env.cfg, "auto")
    assert not T.mode_ready(SimpleNamespace(agents_dir=env.cfg.agents_dir / "khong-co"), "B")


def test_real_repo_manifest_marks_B_ready():
    cfg = SimpleNamespace(agents_dir=Path(__file__).resolve().parent.parent / "agents")
    assert T.mode_ready(cfg, "B")


def test_consent_toggle_hidden_for_modes_that_always_have_it(env):
    to_preview(env, mode="B")
    assert "consent" not in [k for k, _ in T._fields_list(env.st)]
    env.st["mode"] = "C"
    assert "consent" in [k for k, _ in T._fields_list(env.st)]


def test_all_button_data_fit_telegram_limit(env):
    r = T.tpl_view(env.types)
    longest = "x" * T.MAX_CODE
    assert len(T.tb("tpl", longest).encode()) <= 64
    for row in r.kb:
        for _label, data in row:
            assert len(data.encode()) <= 64 and data.startswith("t|")
