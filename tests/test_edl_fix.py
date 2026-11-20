"""EDL do AI viết hay sai hai chỗ máy móc (đường dẫn clip, nền của thẻ). Phải tự sửa hoặc báo rõ TRƯỚC khi chạy lệnh dựng."""
from vidagents import schemas as S


def folder_with(tmp_path, *names):
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    return tmp_path


def edl(**over):
    base = {"sources": {"A": "C7353 (khung tự điền đường dẫn đầy đủ)", "B": "c7356.mp4"},
            "ranges": [{"n": 1, "source": "A", "start": 1.0, "end": 5.0, "beat": "HOOK", "quote": "x"},
                       {"n": 4, "source": "B", "start": 69.4, "end": 76.04, "beat": "CTA", "quote": "y"}],
            "gaps": [{"after_range": 4, "seconds": 2.5, "card": "END", "source": "chọn sau khi xem ảnh", "start": None}]}
    base.update(over)
    return base


def test_clip_names_become_real_paths_and_gap_gets_background(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4", "C7356.MP4", "C7359.MP4")
    e = edl()
    assert S.validate_edl("B", e)                      # trước khi sửa: gap sai khuôn
    notes = S.normalize_edl_b(e, f)
    assert len(notes) == 3
    assert e["sources"]["A"] == (f / "C7353.MP4").as_posix() and e["sources"]["B"] == (f / "C7356.MP4").as_posix()
    g = e["gaps"][0]
    assert g["source"] == "B" and g["start"] == 73.54 and "nền tạm" in g["auto_background"]   # cuối đoạn 4 trừ 2,5 s
    assert S.validate_edl("B", e) == [] and S.missing_sources(e) == []


def test_before_range_gap_uses_start_of_next_range(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4", "C7356.MP4")
    e = edl(gaps=[{"before_range": 4, "seconds": 1.0, "card": "CÔNG DỤNG", "source": None, "start": None}])
    S.normalize_edl_b(e, f)
    assert e["gaps"][0]["source"] == "B" and e["gaps"][0]["start"] == 69.4


def test_valid_gap_is_left_alone(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4", "C7356.MP4")
    e = edl(gaps=[{"after_range": 1, "seconds": 1.0, "card": "X", "source": "A", "start": 2.0}])
    S.normalize_edl_b(e, f)
    assert e["gaps"][0] == {"after_range": 1, "seconds": 1.0, "card": "X", "source": "A", "start": 2.0}


def test_ambiguous_or_unknown_clip_is_not_guessed(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4")
    e = edl(sources={"A": "clip-khong-co", "B": "C7353 hoặc C7353"})
    S.normalize_edl_b(e, f)
    assert e["sources"]["A"] == "clip-khong-co"          # không đoán
    assert any("không phải file có thật" in m for m in S.missing_sources(e)) is True


def test_gap_validation_messages(tmp_path):
    e = edl(sources={"A": "a", "B": "b"},
            gaps=[{"after_range": 99, "seconds": 0, "card": "X", "source": "Z", "start": "chọn sau"}])
    msg = " | ".join(S.validate_edl("B", e))
    assert "KHÓA" in msg and "số giây trong clip" in msg and "seconds" in msg and "after_range" in msg


def test_end_card_where_comes_from_brief_not_from_the_model(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4", "C7356.MP4")
    e = edl(graphics={"end": {"title": "Saphir"}})
    notes = S.normalize_edl_b(e, f, {"where_to_buy": "Fanpage Saphir"})
    assert e["graphics"]["end"] == {"title": "Saphir", "where": "Fanpage Saphir"} and any("Nơi mua" in n for n in notes)
    e2 = edl(graphics={"end": {"where": "Hotline 123"}})
    S.normalize_edl_b(e2, f, {"where_to_buy": "Fanpage Saphir"})
    assert e2["graphics"]["end"]["where"] == "Hotline 123"          # AI/người đã ghi riêng thì giữ
    e3 = edl()
    S.normalize_edl_b(e3, f, {"where_to_buy": "Fanpage Saphir"})   # không có graphics -> tạo
    assert e3["graphics"]["end"]["where"] == "Fanpage Saphir"


def test_existing_absolute_path_is_kept_and_posix_normalized(tmp_path):
    f = folder_with(tmp_path, "C7353.MP4")
    e = edl(sources={"A": str(f / "C7353.MP4")}, ranges=[{"n": 1, "source": "A", "start": 1.0, "end": 2.0}], gaps=[])
    S.normalize_edl_b(e, f)
    assert e["sources"]["A"] == (f / "C7353.MP4").as_posix() and S.missing_sources(e) == []
