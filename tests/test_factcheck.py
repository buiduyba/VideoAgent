"""Đối chiếu giá / % / ngày trên màn hình với brief bằng chương trình (không dùng AI)."""
import json

import pytest

from conftest import run_to_approval_1
from vidagents import factcheck as FC
from vidagents import schemas as S


def keys(text):
    return [(f.kind, f.key) for f in FC.extract_facts(text)]


def test_extract_money_in_many_writings():
    assert keys("Giá hội chợ 110K / chai") == [("gia", "110000")]
    assert keys("còn 110.000đ") == [("gia", "110000")]
    assert keys("1,5 triệu") == [("gia", "1500000")]
    assert keys("1.250.000 đồng") == [("gia", "1250000")]
    assert keys("2-3 nắp bột, 60°C, 3 phút, 5kg") == []  # số thường không bị kiểm


def test_extract_percent_date_buy_get():
    assert keys("giảm 30% đến hết 30/11/2026") == [("phan_tram", "30%"), ("ngay", "30/11")]
    assert keys("ngày 5 tháng 11") == [("ngay", "05/11")]
    assert keys("mua 2 tặng 1") == [("mua_tang", "mua2tang1")]
    assert keys("giảm 25,5 %") == [("phan_tram", "25.5%")]


def run(truth, shown, script=(), brief=None, vt=None):
    return FC.check(truth_texts=truth, displayed={"test": shown} if shown else {}, script_lines=list(script), brief=brief, vt=vt)


def test_price_on_screen_not_in_brief_is_heavy():
    r = run(["Giá hội chợ 110K"], ["GIÁ HỘI CHỢ", "99K / chai"])
    assert [f.severity for f in r.findings] == ["nang"] and "99K" in r.findings[0].desc and r.findings[0].layer == 1


def test_matching_price_written_differently_passes():
    assert run(["Giá 110.000đ một chai"], ["110K / chai"]).findings == []
    assert run(["giảm 30%, hết 30/11"], ["-30%", "đến 30/11"]).findings == []


def test_percent_and_date_mismatch_are_heavy():
    r = run(["giảm 30%, hết 30/11/2026"], ["GIẢM 40%", "HẾT 01/12"])
    assert len(r.findings) == 2 and {f.severity for f in r.findings} == {"nang"}


def test_same_day_other_year_is_a_mismatch():
    assert len(run(["hết 30/11/2026"], ["đến 30/11/2025"]).findings) == 1
    assert run(["hết 30/11/2026"], ["đến 30/11"]).findings == []  # màn hình không ghi năm thì không bắt bẻ


def test_number_only_in_script_line_is_medium():
    r = run(["Giá 110K"], ["110K"], script=["Chỉ 90K thôi nhé"])
    assert [f.severity for f in r.findings] == ["vua"] and "90K" in r.findings[0].desc


def test_brief_says_no_price_but_price_shown():
    r = run(["Giá 110K"], ["110K / chai"], brief={"show_price": False})
    assert any("KHÔNG hiện giá" in f.desc and f.severity == "nang" for f in r.findings)


def test_required_offer_missing_on_screen_is_medium():
    vt = S.VideoType(id="x", name_vi="x", suggested_mode="A", required_brief=["offer_text", "offer_deadline"])
    brief = {"offer_text": "Mua 2 tặng 1", "offer_deadline": "hết 30/11"}
    r = run(["Mua 2 tặng 1, hết 30/11"], ["LÀM SẠCH MỌI VẾT BẨN"], brief=brief, vt=vt)
    assert sorted(f.severity for f in r.findings) == ["vua", "vua"]
    ok = run(["Mua 2 tặng 1, hết 30/11"], ["MUA 2 TẶNG 1", "đến 30/11"], brief=brief, vt=vt)
    assert ok.findings == []


def test_nothing_to_compare_gives_a_note_not_a_pass_claim():
    r = run(["Giá 110K"], [])
    assert r.findings == [] and r.notes and "Chưa có" in r.notes[0]


def test_load_displayed_reads_edl_overlays_captions_srt(tmp_path):
    ed, proj = tmp_path / "edit", tmp_path / "proj"
    (proj / "assets").mkdir(parents=True)
    ed.mkdir()
    (ed / "edl.json").write_text(json.dumps({"graphics": {"info": [{"title": "GIÁ", "rows": ["99K / chai"]}]},
                                              "gaps": [{"card": "ƯU ĐÃI 50%"}]}), encoding="utf-8")
    (proj / "assets" / "overlays.json").write_text(json.dumps({"overlays": [{"text": "Hết 05/11"}]}), encoding="utf-8")
    (proj / "assets" / "captions.json").write_text(json.dumps({"groups": [{"words": "giảm 20%"}]}), encoding="utf-8")
    srt = tmp_path / "v.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nChỉ 80K thôi\n\n", encoding="utf-8")
    shown = FC.load_displayed(ed, proj, srt)
    flat = " | ".join(t for v in shown.values() for t in v)
    assert "99K" in flat and "50%" in flat and "05/11" in flat and "20%" in flat and "80K" in flat and "-->" not in flat
    assert len(shown) == 4


def test_critic_node_flags_wrong_price_even_without_preview_file(env):
    e = env
    run_to_approval_1(e)
    ed = type(e.cfg.work_root)(e.state["edit_dir"])
    (ed / "edl.json").write_text(json.dumps({"graphics": {"info": [{"title": "GIÁ HỘI CHỢ", "rows": ["99K / chai"]}]}}), encoding="utf-8")
    e.state["critic_stage"] = "preview"
    upd = e.mr.nodes["critic"](e.state)
    assert upd["critic_has_heavy"] is True
    assert any("Đối chiếu brief" in f["desc"] and "99K" in f["desc"] for f in upd["critic_findings"])
    # người giao sửa brief cho đúng giá -> hết lỗi này
    e.state["brief_text"] += "\nGiá hội chợ 99K / chai"
    upd2 = e.mr.nodes["critic"](e.state)
    assert not any("Đối chiếu brief" in f["desc"] for f in upd2["critic_findings"])
