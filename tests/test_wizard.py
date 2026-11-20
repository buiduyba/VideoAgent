"""Giao việc bằng nút bấm: chọn thư mục -> loại -> thương hiệu -> điền ô bắt buộc -> giao. Mọi đường đi đều qua kiểm tra cũ."""
import json

from conftest import make_env
from vidagents import wizard as W


def setup(tmp_path):
    e = make_env(tmp_path)
    root = e.cfg.footage_root                       # có sẵn footage/clip-demo/clip.mp4
    (root / "A-rong").mkdir()
    (root / "B-co-clip").mkdir()
    (root / "B-co-clip" / "x.mp4").write_bytes(b"x")
    (root / "B-co-clip" / "y.MOV").write_bytes(b"x")
    (root / "edit").mkdir()                         # thư mục kết quả: không được hiện ra
    kit = e.cfg.projects_root / "_brands" / "saphir"
    kit.mkdir(parents=True)
    (kit / "brand.json").write_text(json.dumps({"name": "Saphir", "stores": "Fanpage Saphir", "claims_avoid": ["trị dứt điểm"],
                                                "grade": "tuoi_min", "music": {"default": "music/a_tuoi.wav"}, "logo": None, "tagline": ""}),
                                    encoding="utf-8")
    return e


def labels(r):
    return [lbl for row in r.kb for lbl, _ in row]


def press(st, e, r, label_start):
    """Bấm nút đầu tiên có nhãn bắt đầu bằng label_start (đúng như người dùng bấm)."""
    data = next(d for row in r.kb for lbl, d in row if lbl.startswith(label_start))
    assert len(data.encode()) <= 64                       # giới hạn callback_data của Telegram
    _, code, arg = data.split("|")
    return W.on_callback(st, e.cfg, e.deps.types, code, "" if arg == "-" else arg)


def test_folder_view_lists_subfolders_hides_edit_and_shows_clip_count(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    r = W.folder_view(st, e.cfg)
    names = labels(r)
    assert "📁 A-rong" in names and "🎞 B-co-clip (2)" in names and "🎞 clip-demo (1)" in names   # 🎞 = có video ngay trong đó
    assert not any("edit" in n for n in names)                      # thư mục kết quả bị giấu
    assert "Thư mục này có" not in r.text and not any(n.startswith("✅ Chọn") for n in names)   # gốc không có video: không đề nghị chọn
    r2 = press(st, e, r, "🎞 B-co-clip")
    assert "B-co-clip" in r2.text and "có 2 video" in r2.text and "✅ Chọn thư mục này (2 video)" in labels(r2)
    assert "⬆️ Lên" in labels(r2)


def form_state(e, brand="saphir", vtype="koc-review", folder="B-co-clip"):
    """Trạng thái wizard ngay lúc bảng phiếu hiện ra (sau khi chọn thư mục, loại, thương hiệu)."""
    st = W.new_state(e.cfg)
    st.update(folder=str(e.cfg.footage_root / folder), vtype=vtype, brand=brand)
    W._start_form(st, e.cfg, e.deps.types)
    return st


def test_pick_folder_type_brand_then_fill_the_form_panel_then_start(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    r = W.folder_view(st, e.cfg)
    r = press(st, e, r, "🎞 B-co-clip")
    r = press(st, e, r, "✅ Chọn thư mục này")
    assert "Bước 2/4" in r.text
    r = press(st, e, r, "Video KOC review")
    assert "Bước 3/4" in r.text and "saphir ⚠" in labels(r)          # saphir thiếu logo/tagline -> có cảnh báo
    r = press(st, e, r, "saphir")
    assert st["stage"] == "form" and r.panel
    assert st["values"]["where"] == "Fanpage Saphir" and st["values"]["duration"] == "30 giây"   # điền sẵn từ thương hiệu/dạng
    assert "📝 PHIẾU BRIEF" in r.text and "Bắt buộc: 1/6" in r.text and "✅ Nơi mua *: Fanpage Saphir" in r.text
    assert "⬜ Sản phẩm chính *" in labels(r) and "✅ Nơi mua *" in labels(r) and "🚀 Giao việc (còn thiếu 5)" in labels(r)
    r = press(st, e, r, "⬜ Sản phẩm chính")                        # bấm ô để điền
    assert st["stage"] == "edit" and "Sản phẩm chính" in r.text and "Gõ giá trị" in r.text
    r = W.on_text(st, e.cfg, e.deps.types, "Nước hoa Saphir")        # gõ giá trị: về lại bảng, ô đã ✅
    assert st["stage"] == "form" and "✅ Đã lưu: Sản phẩm chính" in r.text and "✅ Sản phẩm chính *: Nước hoa Saphir" in r.text
    for field, choice in (("Lợi ích", "Theo lời người nói"), ("Giá còn áp dụng", "Không"), ("Mục tiêu", "Quảng cáo bán hàng"),
                          ("Người nói đã đồng ý", "Có")):
        r = press(st, e, r, "⬜ " + field)
        assert choice in labels(r)                                   # ô có lựa chọn nhanh thì hiện nút
        r = press(st, e, r, choice)
    assert "Bắt buộc: 6/6 · ✅ đủ, có thể giao" in r.text and "🚀 Giao việc ngay" in labels(r)
    r = press(st, e, r, "🚀 Giao việc ngay")
    assert r.end and r.start["video_type"] == "koc-review" and r.start["brand"] == "saphir"
    assert r.start["footage_dir"].endswith("B-co-clip") and r.start["brief_prefill"]["product"] == "Nước hoa Saphir"
    assert r.start["brief_prefill"]["show_price"] is False and "[" not in r.start["brief_text"]


def test_pasting_a_path_works_and_bad_paths_are_explained(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    r = press(st, e, W.folder_view(st, e.cfg), "✏️ Dán đường dẫn")
    assert st["stage"] == "path" and "Copy as path" in r.text
    bad = W.on_text(st, e.cfg, e.deps.types, '"' + str(e.cfg.footage_root / "A-rong") + '"')       # không có clip
    assert "không có clip video" in bad.text and st["stage"] == "folder"
    assert W.on_text(st, e.cfg, e.deps.types, "chu thuong") is None                                  # về màn hình chọn: chữ thường không bị coi là đường dẫn
    press(st, e, W.folder_view(st, e.cfg), "✏️ Dán đường dẫn")
    outside = W.on_text(st, e.cfg, e.deps.types, str(tmp_path))                                      # ngoài vùng cho phép
    assert "Không dùng được thư mục này" in outside.text
    press(st, e, W.folder_view(st, e.cfg), "✏️ Dán đường dẫn")
    good = W.on_text(st, e.cfg, e.deps.types, '"' + str(e.cfg.footage_root / "B-co-clip") + '"')
    assert st["stage"] == "type" and "Bước 2/4" in good.text


def test_optional_fields_are_collapsed_and_can_be_filled_by_button_or_pasted_lines(tmp_path):
    e = setup(tmp_path)
    st = form_state(e)
    r = W.form_view(st, e.cfg, e.deps.types)
    assert not any(l.startswith("⬜ Ưu đãi") for l in labels(r)) and any(l.startswith("➕ Mục tùy chọn (") for l in labels(r))   # gập lại cho gọn
    r = press(st, e, r, "➕ Mục tùy chọn")
    assert st["show_opt"] and "⬜ Ưu đãi" in labels(r) and "➖ Ẩn mục tùy chọn" in labels(r)
    r = press(st, e, r, "⬜ Phong cách dựng")
    assert "muot" in labels(r) and "ban-nhanh" in labels(r)                                       # ô có lựa chọn nhanh
    r = press(st, e, r, "muot")
    assert st["values"]["style"] == "muot" and "✅ Phong cách dựng: muot" in r.text               # ô tùy chọn đã điền thì hiện cả trong chữ
    # dán nhiều dòng một lần (cách cũ của phiếu): vừa hợp lệ vừa sai
    r = W.on_text(st, e.cfg, e.deps.types, "Ưu đãi: giảm 10%\nHạn ưu đãi: 30/11\nGiá còn áp dụng: tùy")
    assert st["values"]["offer"] == "giảm 10%" and st["values"]["deadline"] == "30/11" and "price" not in st["values"]
    assert "✅ Đã cập nhật 2 mục: Ưu đãi, Hạn ưu đãi" in r.text and "⚠ Giá còn áp dụng: Ghi 'Không'" in r.text
    assert "Chưa đọc được" in W.on_text(st, e.cfg, e.deps.types, "linh tinh").text and st["stage"] == "form"
    r = press(st, e, r, "✅ Ưu đãi")
    r = press(st, e, r, "🗑 Xóa giá trị")                                                         # ô tùy chọn xóa được
    assert "offer" not in st["values"] and "Đã xóa" in r.text
    r = press(st, e, r, "➖ Ẩn mục tùy chọn")
    assert not st["show_opt"]


def test_editing_a_required_field_can_be_cancelled_and_has_no_delete(tmp_path):
    e = setup(tmp_path)
    st = form_state(e)
    r = press(st, e, W.form_view(st, e.cfg, e.deps.types), "⬜ Sản phẩm chính")
    assert "🗑 Xóa giá trị" not in labels(r) and "↩️ Quay lại phiếu" in labels(r)
    r = press(st, e, r, "↩️ Quay lại phiếu")
    assert st["stage"] == "form" and "Bắt buộc: 1/6" in r.text
    r = press(st, e, r, "↩️ Đổi loại/thương hiệu")
    assert st["stage"] == "type"


def test_stale_buttons_expire_and_cancel_and_restart_work(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    r = W.folder_view(st, e.cfg)
    r = press(st, e, r, "🎞 B-co-clip")
    r = press(st, e, r, "✅ Chọn")                           # đã sang bước 2
    expired = W.on_callback(st, e.cfg, e.deps.types, "s", "")  # bấm lại nút "Chọn thư mục này" của tin cũ
    assert expired.text == W.EXPIRED and st["stage"] == "type"
    again = W.on_callback(st, e.cfg, e.deps.types, "re", "")
    assert st["stage"] == "folder" and "bước 1/4" in again.text
    bye = W.on_callback(st, e.cfg, e.deps.types, "x", "")
    assert bye.end and "chưa tốn tiền" in bye.text


def test_finish_is_blocked_when_a_required_field_is_missing_or_request_invalid(tmp_path):
    e = setup(tmp_path)
    st = form_state(e)
    r = W.on_callback(st, e.cfg, e.deps.types, "go", "")
    assert not r.start and "⚠ Còn thiếu (chưa tạo job)" in r.text and "Sản phẩm chính" in r.text and r.panel      # nói rõ thiếu gì, ở lại bảng
    st["values"].update({"product": "P", "benefits": "b", "price": "Không", "goal": "g", "consent": "Có"})
    st["brand"] = "khong-co"
    r = W.on_callback(st, e.cfg, e.deps.types, "go", "")
    assert not r.start and "Chưa có bộ nhận diện thương hiệu" in r.text
    back = W.on_callback(st, e.cfg, e.deps.types, "fb2", "")
    assert back.text.startswith("📝 PHIẾU BRIEF")


def test_paging_with_many_folders(tmp_path):
    e = setup(tmp_path)
    for i in range(20):
        (e.cfg.footage_root / f"thu-muc-{i:02d}").mkdir()
    st = W.new_state(e.cfg)
    r = W.folder_view(st, e.cfg)
    assert len([n for n in labels(r) if n[0] in "📁🎞"]) == W.PAGE
    r2 = press(st, e, r, "Sau ▶")
    assert st["page"] == 1 and any(n.startswith("◀ Trước") for n in labels(r2))


# ───────────────────────── hiển thị gọn cho cây thư mục lớn (Xưởng Video > KOC > ngày/sản phẩm) ─────────────────────────
def test_buttons_are_laid_out_in_two_columns(tmp_path):
    e = setup(tmp_path)
    for i in range(5):
        (e.cfg.footage_root / f"sp-{i}").mkdir()
    r = W.folder_view(W.new_state(e.cfg), e.cfg)
    folder_rows = [row for row in r.kb if all(lbl[0] in "📁🎞" for lbl, _ in row)]
    assert all(len(row) <= W.COLS for row in folder_rows) and max(len(row) for row in folder_rows) == 2
    assert sum(len(row) for row in folder_rows) == 8            # 3 thư mục của setup + 5 mới, mỗi thư mục một nút


def test_long_names_are_shortened_and_breadcrumb_keeps_only_the_last_levels(tmp_path):
    e = setup(tmp_path)
    long = "Chiến dịch khuyến mãi mùa hè rất dài tên"
    (e.cfg.footage_root / long).mkdir()
    r = W.folder_view(W.new_state(e.cfg), e.cfg)
    lab = next(lbl for row in r.kb for lbl, _ in row if lbl.startswith("📁 Chiến"))
    assert lab.endswith("…") and len(lab) <= W.LABEL_MAX + 3
    deep = e.cfg.footage_root / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    st = W.new_state(e.cfg)
    st["cwd"] = str(deep)
    crumb = next(line for line in W.folder_view(st, e.cfg).text.splitlines() if line.startswith("📍"))
    assert crumb == f"📍 {e.cfg.footage_root.name} › … › c › d › e"


def test_date_named_folders_are_listed_newest_first_and_others_by_name():
    names = ["2026-11-02 Serum", "2026-11-20 Nước hoa", "19.11.2026", "ghi-chu", "20261105"]
    assert W.sort_names(names)[:4] == ["2026-11-20 Nước hoa", "19.11.2026", "20261105", "2026-11-02 Serum"]   # 20/11, 19/11, 05/11, 02/11
    assert W.sort_names(names)[-1] == "ghi-chu"                                  # không có ngày thì xuống cuối
    assert W.sort_names(["Test AI", "Nhật Hải", "Trang Chun"]) == ["Nhật Hải", "Test AI", "Trang Chun"]   # không phải thư mục ngày: theo tên


def test_invalid_dates_do_not_crash_the_sort():
    assert W.sort_names(["2026-13-45", "2026-02-30 abc", "a"]) == ["2026-02-30 abc", "2026-13-45", "a"]


def test_search_filter_ignores_accents_and_case(tmp_path):
    e = setup(tmp_path)
    for n in ("Nhật Hải", "Trang Chun", "Test AI", "Nhật Linh"):
        (e.cfg.footage_root / n).mkdir()
    for i in range(12):
        (e.cfg.footage_root / f"thu-muc-{i:02d}").mkdir()                       # >10 thư mục: nút Tìm hiện ra
    st = W.new_state(e.cfg)
    r = W.folder_view(st, e.cfg)
    assert "🔎 Tìm theo tên" in labels(r)
    r = press(st, e, r, "🔎 Tìm theo tên")
    assert st["stage"] == "search" and "nhat hai" in r.text
    r = W.on_text(st, e.cfg, e.deps.types, "NHAT")                                # không dấu, viết hoa
    assert st["stage"] == "folder" and 'Đang lọc "NHAT": 2/' in r.text
    shown = [lbl for lbl in labels(r) if lbl[0] in "📁🎞"]
    assert sorted(shown) == ["📁 Nhật Hải", "📁 Nhật Linh"] and "✖ Bỏ lọc" in labels(r) and "🔎 Tìm theo tên" not in labels(r)
    none = W.on_text(W.new_state(e.cfg) | {"stage": "search"}, e.cfg, e.deps.types, "khongco")
    assert none.text.startswith('Không có thư mục nào khớp "khongco"')
    cleared = press(st, e, r, "✖ Bỏ lọc")
    assert st["q"] == "" and "Đang lọc" not in cleared.text


def test_entering_a_folder_clears_the_filter_and_search_button_is_hidden_for_short_lists(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    assert "🔎 Tìm theo tên" not in labels(W.folder_view(st, e.cfg))              # ít thư mục: không cần tìm
    st["q"] = "clip"
    r = W.folder_view(st, e.cfg)
    r = press(st, e, r, "🎞 clip-demo")
    assert st["q"] == "" and "clip-demo" in r.text


def test_other_footage_outside_the_workshop_root_is_refused(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    elsewhere = tmp_path / "ngoai-xuong-video"
    elsewhere.mkdir()
    (elsewhere / "a.mp4").write_bytes(b"x")
    st["stage"] = "path"
    r = W.on_text(st, e.cfg, e.deps.types, str(elsewhere))
    assert "Không dùng được thư mục này" in r.text and st["stage"] == "folder"


# ───────────────────────── "Như lần trước" ─────────────────────────
def test_same_as_last_time_button_skips_type_and_brand_and_goes_to_the_form(tmp_path):
    e = setup(tmp_path)
    past = [{"footage_dir": str(e.cfg.footage_root / "B-co-clip"), "video_type": "koc-review", "brand": "saphir"}]
    st = W.new_state(e.cfg, past=past)
    r = press(st, e, W.folder_view(st, e.cfg), "🎞 B-co-clip")
    r = press(st, e, r, "✅ Chọn thư mục này")
    first = labels(r)[0]
    assert first.startswith("⚡ Như lần trước:") and "saphir" in first and e.deps.types["koc-review"].name_vi in first   # nút đứng đầu
    assert len(first.encode()) < 200 and len(r.kb[0][0][1].encode()) <= 64
    r = press(st, e, r, "⚡ Như lần trước")
    assert st["stage"] == "form" and st["vtype"] == "koc-review" and st["brand"] == "saphir" and r.panel
    assert "📝 PHIẾU BRIEF" in r.text and st["values"]["where"] == "Fanpage Saphir"               # vẫn điền sẵn như đi đường dài


def test_last_time_prefers_the_same_project_then_any_and_ignores_stale_choices(tmp_path):
    e = setup(tmp_path)
    root = e.cfg.footage_root
    (root / "KOC-A" / "ngay-1").mkdir(parents=True)
    (root / "KOC-A" / "ngay-2").mkdir()
    (root / "KOC-B").mkdir()
    past = [{"footage_dir": str(root / "KOC-B"), "video_type": "unbox-san-pham", "brand": "saphir"},                 # mới nhất nhưng khác dự án
            {"footage_dir": str(root / "KOC-A" / "ngay-1"), "video_type": "koc-review", "brand": "saphir"},
            {"footage_dir": str(root / "KOC-A" / "x"), "video_type": "loai-da-xoa", "brand": "saphir"}]
    st = W.new_state(e.cfg, past=past)
    types = e.deps.types
    assert W.last_choice(st, e.cfg, types, str(root / "KOC-A" / "ngay-2")) == ("koc-review", "saphir")      # cùng dự án KOC-A, ngày khác
    assert W.last_choice(st, e.cfg, types, str(root / "B-co-clip")) == ("unbox-san-pham", "saphir")                   # dự án mới: lấy việc gần nhất
    assert W.last_choice(W.new_state(e.cfg), e.cfg, types, str(root / "KOC-A")) is None                    # người mới: không có nút
    stale = W.new_state(e.cfg, past=[{"footage_dir": str(root), "video_type": "loai-da-xoa", "brand": "saphir"},
                                     {"footage_dir": str(root), "video_type": "koc-review", "brand": "thuong-hieu-da-xoa"}])
    assert W.last_choice(stale, e.cfg, types, str(root / "KOC-A")) is None                                   # loại/thương hiệu không còn: bỏ qua


def test_no_last_time_button_for_a_new_user_and_pv_is_harmless_then(tmp_path):
    e = setup(tmp_path)
    st = W.new_state(e.cfg)
    r = press(st, e, W.folder_view(st, e.cfg), "🎞 B-co-clip")
    r = press(st, e, r, "✅ Chọn thư mục này")
    assert not any(l.startswith("⚡") for l in labels(r))
    assert W.on_callback(st, e.cfg, e.deps.types, "pv", "").text == W.EXPIRED and st["stage"] == "type"      # nút cũ không phá trạng thái


def test_restarting_the_wizard_keeps_the_history(tmp_path):
    e = setup(tmp_path)
    past = [{"footage_dir": str(e.cfg.footage_root / "B-co-clip"), "video_type": "koc-review", "brand": "saphir"}]
    st = W.new_state(e.cfg, past=past)
    W.on_callback(st, e.cfg, e.deps.types, "re", "")
    assert st["past"] == past
