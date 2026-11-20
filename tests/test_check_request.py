"""Kiểm yêu cầu giao việc trước khi tạo job: sai thì báo cách sửa, không tạo job, không tốn tiền."""
from vidagents.nodes import brand_kits, check_request


def check(e, folder=None, vtype="koc-review", brand="demo"):
    return check_request(e.cfg, e.deps.types, str(folder or e.clip), vtype, brand)


def test_valid_request_has_no_errors(env):
    assert check(env) == []


def test_unknown_brand_lists_existing_kits(env):
    errs = check(env, brand="saphir")
    assert len(errs) == 1 and "saphir" in errs[0] and "demo" in errs[0]
    assert brand_kits(env.cfg) == ["demo"]


def test_accented_brand_is_rejected_as_invalid_slug(env):
    errs = check(env, brand="quảng")
    assert len(errs) == 1 and "không hợp lệ" in errs[0]


def test_free_text_after_folder_is_reported_with_all_problems_at_once(env):
    # giống tin nhắn thật: quên loại video nên "Video" thành loại, "quảng" thành thương hiệu
    errs = check(env, vtype="video", brand="quảng")
    assert len(errs) == 2


def test_file_instead_of_folder_points_to_parent(env):
    errs = check(env, folder=env.clip / "clip.mp4")
    assert len(errs) == 1 and "FILE" in errs[0] and str(env.clip) in errs[0]


def test_missing_folder_suggests_parent_contents(env):
    errs = check(env, folder=env.clip.parent / "khong-co")
    assert len(errs) == 1 and "Không tìm thấy" in errs[0]
