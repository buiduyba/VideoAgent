"""/themnguoi, /xoanguoi, /danhsach: đổi danh sách người giao việc ngay trong bot, ghi lại config.json, có nhật ký."""
import json

import pytest

from conftest import REPO
from vidagents import access
from vidagents.config import Config


def make_cfg(tmp_path, name="a", allowed=("2000001",), admins=("9",)):
    d = tmp_path / name
    d.mkdir()
    raw = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
    raw["telegram"] = {"token_env": "TELEGRAM_BOT_TOKEN", "allowed_user_ids": list(allowed), "admin_ids": list(admins), "max_file_send_mb": 45}
    raw["jobs_db"] = str(d / "jobs.sqlite")
    raw["_giu_nguyen"] = "khóa khác không được mất khi ghi lại"
    p = d / "config.json"
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return Config.load(p), p


def on_disk(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_add_user_takes_effect_in_memory_and_is_saved_to_config_file(tmp_path):
    cfg, p = make_cfg(tmp_path)
    msg = access.add_user(cfg, "5234517033", "9", "Hoa Marketing")
    assert "Đã thêm 5234517033 (Hoa Marketing)" in msg and "không cần bật lại" in msg
    assert cfg.allowed_user_ids == ["2000001", "5234517033"]                      # bot đọc danh sách này mỗi lần có tin
    d = on_disk(p)
    assert d["telegram"]["allowed_user_ids"] == ["2000001", "5234517033"] and d["telegram"]["user_names"] == {"5234517033": "Hoa Marketing"}
    assert d["_giu_nguyen"].startswith("khóa khác") and d["footage_root"]       # không mất khóa nào
    assert "Hoa Marketing" in p.read_text(encoding="utf-8")                      # tiếng Việt không bị mã hóa thành \uXXXX
    assert Config.load(p).allowed_user_ids == ["2000001", "5234517033"]           # bật lại bot vẫn còn


@pytest.mark.parametrize("bad", ["abc", "123", "12 34 56 78", "", "-5234517033", "1" * 20])
def test_invalid_ids_are_refused_without_changing_anything(tmp_path, bad):
    cfg, p = make_cfg(tmp_path)
    before = p.read_text(encoding="utf-8")
    with pytest.raises(access.AccessError, match="dãy số"):
        access.add_user(cfg, bad, "9")
    assert p.read_text(encoding="utf-8") == before and cfg.allowed_user_ids == ["2000001"]


def test_duplicate_is_refused(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    with pytest.raises(access.AccessError, match="đã có trong danh sách"):
        access.add_user(cfg, "2000001", "9")


def test_cannot_add_an_existing_admin_as_user(tmp_path):
    cfg, _ = make_cfg(tmp_path, admins=("8782775717",))
    with pytest.raises(access.AccessError, match="quản trị viên"):
        access.add_user(cfg, "8782775717", "8782775717")
    assert cfg.allowed_user_ids == ["2000001"]


def test_re_adding_with_a_name_updates_only_the_name(tmp_path):
    cfg, p = make_cfg(tmp_path)
    assert "cập nhật tên: Lan" in access.add_user(cfg, "2000001", "9", "Lan")
    assert cfg.user_names == {"2000001": "Lan"} and on_disk(p)["telegram"]["user_names"] == {"2000001": "Lan"}


def test_remove_user(tmp_path):
    cfg, p = make_cfg(tmp_path, allowed=("2000001",))
    access.add_user(cfg, "5234517033", "9", "Hoa")
    msg = access.remove_user(cfg, "5234517033", "9", active_jobs=2)
    assert "Đã gỡ 5234517033 (Hoa)" in msg and "còn 2 job chưa xong" in msg
    assert cfg.allowed_user_ids == ["2000001"] and on_disk(p)["telegram"]["allowed_user_ids"] == ["2000001"]
    assert "user_names" not in on_disk(p)["telegram"]
    with pytest.raises(access.AccessError, match="không có trong danh sách"):
        access.remove_user(cfg, "5234517033", "9")


def test_admin_cannot_be_removed_via_bot(tmp_path):
    cfg, p = make_cfg(tmp_path, admins=("8782775717",))
    with pytest.raises(access.AccessError, match="Không gỡ quản trị viên"):
        access.remove_user(cfg, "8782775717", "8782775717")
    assert on_disk(p)["telegram"]["admin_ids"] == ["8782775717"]


def test_config_write_failure_leaves_memory_unchanged_on_add(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    cfg.source_path = tmp_path / "khong-co" / "config.json"       # không đọc/ghi được
    with pytest.raises(access.AccessError, match="Không ghi được config"):
        access.add_user(cfg, "5234517033", "9", "Hoa")
    assert cfg.allowed_user_ids == ["2000001"] and cfg.user_names == {}   # bot và file không lệch nhau


def test_config_write_failure_leaves_memory_unchanged_on_remove(tmp_path):
    cfg, _ = make_cfg(tmp_path, allowed=("2000001", "5234517033"))
    cfg.user_names["5234517033"] = "Hoa"
    cfg.source_path = tmp_path / "khong-co" / "config.json"
    with pytest.raises(access.AccessError, match="Không ghi được config"):
        access.remove_user(cfg, "5234517033", "9")
    assert cfg.allowed_user_ids == ["2000001", "5234517033"] and cfg.user_names == {"5234517033": "Hoa"}


def test_without_a_config_path_nothing_is_changed(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    cfg.source_path = None
    with pytest.raises(access.AccessError, match="không biết file config"):
        access.add_user(cfg, "5234517033", "9")
    assert cfg.allowed_user_ids == ["2000001"]


def test_every_change_is_logged(tmp_path):
    cfg, _ = make_cfg(tmp_path)
    access.add_user(cfg, "5234517033", "9", "Hoa")
    access.remove_user(cfg, "5234517033", "9")
    lines = (tmp_path / "a" / "access-log.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and "admin=9\tthem\t5234517033\tHoa" in lines[0] and "admin=9\tgo\t5234517033" in lines[1]


def test_render_list_shows_names_from_config_or_old_jobs(tmp_path):
    cfg, _ = make_cfg(tmp_path, allowed=("2000001", "5234517033"), admins=("9",))
    cfg.user_names["2000001"] = "Duy"
    t = access.render_list(cfg, lambda i: "Tên từ job cũ" if i == "5234517033" else None)
    assert "Quản trị viên" in t and "  • 9" in t and "2000001 · Duy" in t and "5234517033 · Tên từ job cũ" in t and "(2)" in t
    empty_cfg, _ = make_cfg(tmp_path, name="b", allowed=())
    assert "chưa có ai" in access.render_list(empty_cfg)
