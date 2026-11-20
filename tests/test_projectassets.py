"""Tài nguyên của project (logo, ảnh sản phẩm, video AI) nằm cạnh thư mục ngày/sản phẩm: tìm, chép về, báo cho AI, dùng đúng chỗ."""
import json
import shutil
import subprocess

import pytest

from conftest import JOB, make_env
from vidagents import projectassets as PA
from vidagents import schemas as S
from vidagents import wizard as W

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="cần ffmpeg + ffprobe")


def build_project(e, with_assets=True):
    """footage/Nhật Hải/2026-11-20 Nước hoa/{clip.mp4} + logo/, ảnh sản phẩm/, video AI/ ở cấp project."""
    root = e.cfg.footage_root
    proj = root / "Nhật Hải"
    job = proj / "2026-11-20 Nước hoa"
    job.mkdir(parents=True)
    (job / "clip1.mp4").write_bytes(b"x")
    if with_assets:
        (proj / "Logo").mkdir()
        (proj / "Logo" / "logo.png").write_bytes(b"png")
        (proj / "Logo" / "ban-cu.png").write_bytes(b"png2")
        (proj / "Ảnh sản phẩm").mkdir()
        (proj / "Ảnh sản phẩm" / "b-chai.png").write_bytes(b"1")
        (proj / "Ảnh sản phẩm" / "a-hop.jpg").write_bytes(b"2")
        (proj / "Video AI").mkdir()
        (proj / "Video AI" / "canh-1.mp4").write_bytes(b"v1")
        (proj / "Video AI" / "canh-2.mov").write_bytes(b"v2")
        (proj / "tai-lieu-khac").mkdir()                      # thư mục không phải tài nguyên: bỏ qua
        (proj / "tai-lieu-khac" / "x.png").write_bytes(b"x")
    return proj, job


def test_asset_folder_names_match_without_accents_case_or_separators():
    for n in ("logo", "Logo", "LOGOS"):
        assert PA.asset_kind(n) == "logo"
    for n in ("ảnh sản phẩm", "Anh San Pham", "anh-san-pham", "ANH_SAN_PHAM", "Products"):
        assert PA.asset_kind(n) == "products"
    for n in ("video ai", "Video-AI", "video_ai", "Video trám", "AI"):
        assert PA.asset_kind(n) == "ai"
    for n in ("2026-11-20 Nước hoa", "Test AI", "edit", "footage", "logo cu 2024"):
        assert PA.asset_kind(n) is None


def test_project_dir_is_the_first_level_folder_under_the_workshop(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    assert PA.project_dir(e.cfg, job) == proj
    assert PA.project_dir(e.cfg, proj) == proj                              # job nằm ngay trong project
    deep = job / "buoi-sang"
    deep.mkdir()
    assert PA.project_dir(e.cfg, deep) == proj
    assert PA.project_dir(e.cfg, tmp_path) == tmp_path                      # ngoài vùng cho phép: chính nó


def test_collect_reads_project_level_assets_and_ignores_other_folders(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    a = PA.collect(e.cfg, job)
    assert a.logo.name == "logo.png" and any("2 ảnh" in n or "'logo.png'" in n for n in a.notes)      # nhiều logo: ưu tiên file tên 'logo'
    assert [p.name for p in a.products] == ["a-hop.jpg", "b-chai.png"]                                 # theo tên
    assert [p.name for p in a.ai] == ["canh-1.mp4", "canh-2.mov"]
    assert a.summary() == "logo · 2 ảnh sản phẩm · 2 video AI"
    assert all("tai-lieu-khac" not in str(p) for p in a.products)


def test_job_level_assets_override_project_level_logo(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    (job / "logo").mkdir()
    (job / "logo" / "rieng-cho-job.png").write_bytes(b"job")
    assert PA.collect(e.cfg, job).logo == job / "logo" / "rieng-cho-job.png"                         # thư mục job thắng thư mục project
    (job / "anh sp").mkdir()
    (job / "anh sp" / "c.png").write_bytes(b"3")
    assert len(PA.collect(e.cfg, job).products) == 3                                                 # ảnh thì cộng dồn


def test_limits_on_ai_videos_are_reported_not_silently_dropped(tmp_path):
    e = make_env(tmp_path, project_assets_max_mb=1)
    proj, job = build_project(e, with_assets=False)
    (proj / "video ai").mkdir()
    for i in range(3):
        (proj / "video ai" / f"v{i}.mp4").write_bytes(b"x" * (600 * 1024))   # 3 x 600 KB > 1 MB
    a = PA.collect(e.cfg, job)
    assert len(a.ai) == 1 and sum("Bỏ qua video AI" in n for n in a.notes) == 2


def test_stage_copies_outside_the_top_level_so_scribe_never_sees_ai_videos(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    edit = tmp_path / "work" / "edit"
    man = PA.stage(e.cfg, PA.collect(e.cfg, job), edit)
    base = edit / "project-assets"
    assert (base / "logo" / "logo.png").exists() and (base / "ai" / "canh-1.mp4").exists() and (base / "products" / "a-hop.jpg").exists()
    assert man["logo"].endswith("project-assets/logo/logo.png") and [x["ten"] for x in man["anh_san_pham"]] == ["a-hop.jpg", "b-chai.png"]
    assert [x["ten"] for x in man["video_ai"]] == ["canh-1.mp4", "canh-2.mov"]
    assert json.loads((edit / "project_assets.json").read_text(encoding="utf-8")) == man == PA.load_manifest(edit)
    assert PA.stage(e.cfg, PA.collect(e.cfg, job), edit)["logo"] == man["logo"]                      # chạy lại an toàn (không nhân đôi tên)
    assert not (tmp_path / "work" / "canh-1.mp4").exists()                                           # KHÔNG nằm ở cấp trên cùng của footage làm việc


@needs_ffmpeg
def test_stage_probes_ai_videos_for_duration_size_and_audio(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e, with_assets=False)
    (proj / "video ai").mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=teal:s=1280x720:d=2:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(proj / "video ai" / "ai.mp4")], check=True)
    man = PA.stage(e.cfg, PA.collect(e.cfg, job), tmp_path / "edit")
    v = man["video_ai"][0]
    assert v["giay"] == 2.0 and (v["w"], v["h"]) == (1280, 720) and v["co_tieng"] is False


def test_usage_hint_only_when_there_is_something_to_use(tmp_path):
    assert PA.usage_hint({}) == "" and PA.usage_hint({"logo": "x"}) == ""
    h = PA.usage_hint({"video_ai": [{"ten": "a.mp4", "path": "p"}], "anh_san_pham": [{"ten": "a.png", "path": "p"}], "logo": "x"})
    assert "covers" in h and "broll" in h and "KHÔNG dùng làm đoạn lời" in h and '"image"' in h and "tự gắn" in h


# ───────────────────────── EDL ─────────────────────────
def man_for(tmp_path):
    d = tmp_path / "project-assets"          # đúng tên thư mục thật bot chép về (edit\project-assets)
    (d / "ai").mkdir(parents=True)
    (d / "products").mkdir()
    (d / "logo").mkdir()
    (d / "ai" / "canh-1.mp4").write_bytes(b"v")
    (d / "products" / "chai.png").write_bytes(b"i")
    (d / "logo" / "logo.png").write_bytes(b"l")
    return d, {"logo": (d / "logo" / "logo.png").as_posix(),
               "anh_san_pham": [{"ten": "chai.png", "path": (d / "products" / "chai.png").as_posix()}],
               "video_ai": [{"ten": "canh-1.mp4", "path": (d / "ai" / "canh-1.mp4").as_posix()}]}


def base_edl(folder):
    return {"sources": {"A": (folder / "clip1.mp4").as_posix()},
            "ranges": [{"n": 1, "source": "A", "start": 0.0, "end": 3.0}, {"n": 2, "source": "A", "start": 3.0, "end": 6.0}]}


def test_edl_gets_the_project_logo_automatically_and_bad_logo_paths_are_dropped(tmp_path):
    d, man = man_for(tmp_path)
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "clip1.mp4").write_bytes(b"x")
    edl = base_edl(folder)
    notes = S.normalize_edl_b(edl, folder, None, man)
    assert edl["graphics"]["end"]["logo"] == man["logo"] and any("logo của project" in n for n in notes)
    bad = base_edl(folder)
    bad["graphics"] = {"end": {"title": "X", "logo": "D:/khong-co/logo.png"}}
    S.normalize_edl_b(bad, folder, None, {})                                      # project không có logo
    assert "logo" not in bad["graphics"]["end"] and bad["graphics"]["end"]["title"] == "X"
    mine = base_edl(folder)
    mine["graphics"] = {"end": {"logo": man["logo"]}}
    assert S.normalize_edl_b(mine, folder, None, man) == []                       # đã đúng thì không đụng


def test_ai_names_and_product_image_names_resolve_to_real_paths(tmp_path):
    d, man = man_for(tmp_path)
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "clip1.mp4").write_bytes(b"x")
    edl = base_edl(folder)
    edl["sources"]["B"] = "canh-1.mp4 (video AI trám)"
    edl["covers"] = [{"range": 1, "source": "B", "start": 0, "dur": 2}]
    edl["gaps"] = [{"after_range": 2, "seconds": 2.0, "card": "SẢN PHẨM", "image": "chai"}]
    S.normalize_edl_b(edl, folder, None, man)
    assert edl["sources"]["B"] == man["video_ai"][0]["path"] and edl["gaps"][0]["image"] == man["anh_san_pham"][0]["path"]
    assert S.validate_edl("B", edl) == [] and S.missing_sources(edl) == []


def test_image_gap_needs_no_source_and_a_missing_image_falls_back_to_a_clip_background(tmp_path):
    d, man = man_for(tmp_path)
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "clip1.mp4").write_bytes(b"x")
    edl = base_edl(folder)
    edl["gaps"] = [{"after_range": 2, "seconds": 2.0, "card": "X", "image": man["anh_san_pham"][0]["path"]}]
    assert S.validate_edl("B", edl) == [] and "source" not in edl["gaps"][0]
    edl["gaps"] = [{"after_range": 2, "seconds": 2.0, "card": "X", "image": "khong-co-anh-nay"}]
    notes = S.normalize_edl_b(edl, folder, None, man)
    g = edl["gaps"][0]
    assert "image" not in g and g["source"] == "A" and g["start"] == 4.0 and any("không có thật" in n for n in notes)   # nền từ cuối đoạn 2
    assert S.validate_edl("B", edl) == []
    edl["gaps"] = [{"after_range": 2, "seconds": 2.0, "card": "X", "image": "D:/khong/co.png"}]
    assert any("gaps[0].image không phải file có thật" in m for m in S.missing_sources(edl))


def test_ai_video_cannot_be_used_as_a_talking_range(tmp_path):
    d, man = man_for(tmp_path)
    folder = tmp_path / "f"
    folder.mkdir()
    edl = {"sources": {"A": man["video_ai"][0]["path"]}, "ranges": [{"n": 1, "source": "A", "start": 0.0, "end": 3.0}]}
    msg = " ".join(S.validate_edl("B", edl))
    assert "video AI trám" in msg and "covers/broll" in msg


# ───────────────────────── luồng thật: intake chép tài nguyên, script được báo ─────────────────────────
def test_intake_stages_assets_and_script_prompt_mentions_them(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    e.state["footage_dir"] = str(job)
    e.deps.jobs.update(JOB, footage_dir=str(job))
    assert e.mr.run(e.state) == "paused"                                        # intake -> đọc brief -> hỏi thông tin
    ed = tmp_path / "work" / JOB / "edit"
    man = PA.load_manifest(ed)
    assert man["logo"] and len(man["anh_san_pham"]) == 2 and len(man["video_ai"]) == 2
    assert not any(p.suffix == ".mp4" and p.name.startswith("canh") for p in (tmp_path / "work" / JOB).iterdir())   # video AI không nằm ở cấp trên cùng
    assert any("Dùng tài nguyên của project: logo · 2 ảnh sản phẩm · 2 video AI" in m[1] for m in e.notifier.messages)
    assert (tmp_path / "work" / JOB / "clip1.mp4").exists()


def test_job_without_assets_behaves_exactly_as_before(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e, with_assets=False)
    e.state["footage_dir"] = str(job)
    assert e.mr.run(e.state) == "paused"
    assert PA.load_manifest(tmp_path / "work" / JOB / "edit") == {}
    assert not any("tài nguyên của project" in m[1] for m in e.notifier.messages)


# ───────────────────────── bot: danh sách chọn thư mục và màn hình kiểm lại ─────────────────────────
def test_wizard_hides_asset_folders_from_the_job_list(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    st = W.new_state(e.cfg)
    st["cwd"] = str(proj)
    names = [lbl for row in W.folder_view(st, e.cfg).kb for lbl, _ in row]
    assert any("2026-11-20 Nước hoa" in n for n in names) and any("tai-lieu-khac" in n for n in names)
    assert not any(k in n for n in names for k in ("Logo", "Ảnh sản phẩm", "Video AI"))               # thư mục tài nguyên không hiện


def test_form_panel_shows_project_assets_and_logo_covers_the_brand_gap(tmp_path):
    e = make_env(tmp_path)
    proj, job = build_project(e)
    kit = e.cfg.projects_root / "_brands" / "saphir"
    kit.mkdir(parents=True)
    (kit / "brand.json").write_text(json.dumps({"logo": None, "tagline": "", "claims_avoid": []}), encoding="utf-8")
    st = W.new_state(e.cfg)
    st.update(folder=str(job), vtype="koc-review", brand="saphir")
    text = W._start_form(st, e.cfg, e.deps.types).text
    assert "Tài nguyên project: logo · 2 ảnh sản phẩm · 2 video AI" in text
    assert "⚠ Bộ thương hiệu còn thiếu" in text and "logo (end card" not in text                        # project đã có logo
    (proj / "Logo" / "logo.png").unlink()
    (proj / "Logo" / "ban-cu.png").unlink()
    text2 = W.form_view(st, e.cfg, e.deps.types).text
    assert "Tài nguyên project: logo" not in text2 and "logo (end card" in text2                         # hết logo project: cảnh báo lại
