"""Phiếu brief điền sẵn: tạo phiếu, đọc phiếu, chặn thiếu ô bắt buộc, và không gọi AI khi đã có phiếu."""
import re

from conftest import make_env
from vidagents import phieu
from vidagents import schemas as S
from vidagents.bot import plan_video_request

BRAND = {"name": "Saphir", "stores": "Fanpage Saphir Việt Nam", "claims_avoid": ["trị dứt điểm", "số một"],
         "grade": "tuoi_min", "music": {"default": "music/a_tuoi.wav"}}


def fill(form: str, **by_label: str) -> str:
    """Điền giá trị vào các dòng của phiếu theo nhãn (như người dùng gõ sau dấu hai chấm)."""
    out = []
    for line in form.splitlines():
        head, sep, _rest = line.partition(":")
        key = next((k for k in by_label if sep and phieu.norm(head) == phieu.norm(k)), None)
        out.append(f"{head}: {by_label[key]}" if key else line)
    return "\n".join(out)


def vt_of(env, vid="koc-review"):
    return env.deps.types[vid]


FILLED = dict(**{"Sản phẩm chính": "Saphir dung dịch vệ sinh",
                 "Lợi ích được phép nói": "theo lời người nói",
                 "Giá còn áp dụng": "Có, giá 199.000đ",
                 "Nơi mua": "Fanpage Saphir Việt Nam",
                 "Mục tiêu": "quảng cáo bán hàng",
                 "Người nói đã đồng ý xuất hiện và đăng video chưa": "Có"})


def test_form_has_prefill_with_sources_and_never_prefills_money(env):
    vt = vt_of(env)
    form = phieu.build_form(vt, "saphir", BRAND, str(env.clip), "1 video")
    lines = form.splitlines()
    assert lines[0] == f'/video "{env.clip}" koc-review saphir'
    assert "Nơi mua" in form and "Fanpage Saphir Việt Nam [từ thương hiệu]" in form
    assert "trị dứt điểm; số một [từ thương hiệu]" in form
    assert "30 giây [mặc định của dạng video]" in form and "A tươi [mặc định thương hiệu]" in form
    for must_be_blank in ("Giá còn áp dụng", "Ưu đãi", "Hạn ưu đãi", "Lợi ích được phép nói", "Sản phẩm chính"):
        line = next(l for l in lines if l.startswith(must_be_blank))
        assert line.rstrip().endswith(":"), line   # giá/ưu đãi/lợi ích không bao giờ điền sẵn
    assert "Người nói đã đồng ý" in form   # koc-review có người nói trước máy
    assert len(form) < 4000                # vừa một tin Telegram


def test_form_warns_about_incomplete_brand_kit(env):
    vt = vt_of(env)
    form = phieu.build_form(vt, "saphir", {"name": "Saphir", "logo": None, "tagline": "", "claims_avoid": []}, str(env.clip))
    warn = next(l for l in form.splitlines() if l.startswith("⚠"))
    assert "logo" in warn and "câu định vị" in warn and "câu chữ cấm" in warn
    full = phieu.build_form(vt, "oillan", {"logo": "logo/x.png", "tagline": "ok", "claims_avoid": ["a"]}, str(env.clip))
    assert "⚠" not in full


def test_unfilled_form_reports_every_required_field_at_once(env):
    vt = vt_of(env)
    form = phieu.build_form(vt, "saphir", BRAND, str(env.clip))
    miss = phieu.missing(phieu.parse_form(form, vt), vt)
    names = " | ".join(miss)
    for need in ("Sản phẩm chính", "Lợi ích", "Giá còn áp dụng", "Mục tiêu", "Người nói đã đồng ý"):
        assert need in names
    assert "Nơi mua" not in names   # đã điền sẵn từ thương hiệu, không phải hỏi lại


def test_filled_form_parses_to_brief_without_hints_or_sources(env):
    vt = vt_of(env)
    form = fill(phieu.build_form(vt, "saphir", BRAND, str(env.clip)), **FILLED)
    values = phieu.parse_form(form, vt)
    assert phieu.missing(values, vt) == []
    b = phieu.to_brief(values, vt, "saphir")
    assert (b.brand, b.product, b.goal) == ("saphir", "Saphir dung dịch vệ sinh", "quảng cáo bán hàng")
    assert b.show_price is True and b.price_text == "199.000đ"
    assert b.where_to_buy == "Fanpage Saphir Việt Nam" and b.duration_seconds == 30
    assert b.must_avoid == ["trị dứt điểm", "số một"] and b.extra["consent"] == "Có"
    assert S.missing_fields(b, vt) == []
    text = phieu.canonical_text(values, vt)
    assert "[từ" not in text and "(" not in text and "giây" in text   # gợi ý/nguồn không lọt vào nội dung brief


def test_price_forms():
    assert phieu.parse_price("Có, giá 199.000đ") == (True, "199.000đ")
    assert phieu.parse_price("có giá: 99k") == (True, "99k")
    assert phieu.parse_price("Không") == (False, None)
    assert phieu.parse_price("Có") == (True, None)           # có giá mà không ghi số -> bị bắt thiếu
    assert phieu.parse_price("tùy") == (None, None)


def test_price_yes_without_amount_is_missing(env):
    vt = vt_of(env)
    values = phieu.parse_form(fill(phieu.build_form(vt, "saphir", BRAND, str(env.clip)), **{**FILLED, "Giá còn áp dụng": "Có"}), vt)
    assert any("số tiền" in m for m in phieu.missing(values, vt))


def test_type_specific_fields_with_colons_in_labels(env):
    vt = vt_of(env, "hoi-cho-ban-hang")
    form = phieu.build_form(vt, "saphir", BRAND, str(env.clip))
    assert "Lời dẫn * (a. chữ + nhạc" in form and "Tên + địa điểm + ngày hội chợ *:" in form
    form = fill(form, **{"Lời dẫn": "(a) chữ + nhạc", "Tên + địa điểm + ngày hội chợ": "Hội chợ X, Hà Nội, 20/11"})
    values = phieu.parse_form(form, vt)
    b = phieu.to_brief(values, vt, "saphir")
    assert b.extra == {"event": "Hội chợ X, Hà Nội, 20/11", "narration": "(a) chữ + nhạc"}
    miss = " | ".join(phieu.missing(values, vt))
    assert "Ưu đãi" in miss and "Hạn ưu đãi" in miss        # dạng hội chợ bắt buộc ưu đãi + hạn


def test_multiline_value_and_free_text_detection(env):
    vt = vt_of(env)
    form = fill(phieu.build_form(vt, "saphir", BRAND, str(env.clip)), **FILLED)
    form = form.replace("Câu bắt buộc giữ (nhiều câu thì ngăn bằng dấu ;): ",
                        "Câu bắt buộc giữ: câu một;\ncâu hai")
    assert phieu.parse_form(form, vt)["keep"] == "câu một; câu hai"
    assert phieu.is_form(form, vt)
    assert not phieu.is_form(f'/video "x" koc-review saphir\nVideo 30 giây, giá 199k, mua ở fanpage', vt)


# ───────────────────────── bot: quyết định cho một tin nhắn ─────────────────────────
def head(env, cmd="/video", vtype="koc-review"):
    return f'{cmd} "{env.clip}" {vtype} demo'


def test_video_without_brief_returns_the_form_and_creates_no_job(env):
    plan = plan_video_request(env.cfg, env.deps.types, head(env))
    assert "start" not in plan and "PHIẾU BRIEF" in plan["reply"] and "Sản phẩm chính *" in plan["reply"]


def test_phieu_command_returns_form_even_when_brief_is_attached(env):
    plan = plan_video_request(env.cfg, env.deps.types, head(env, "/phieu") + "\nvideo 30 giây")
    assert "PHIẾU BRIEF" in plan["reply"]


def test_incomplete_form_is_refused_with_list_of_missing_fields(env):
    form = phieu.build_form(vt_of(env), "demo", {}, str(env.clip))
    plan = plan_video_request(env.cfg, env.deps.types, form)
    assert "start" not in plan and "Phiếu còn thiếu" in plan["reply"] and "chưa tốn tiền" in plan["reply"]


def test_complete_form_starts_job_with_parsed_brief(env):
    form = fill(phieu.build_form(vt_of(env), "demo", {}, str(env.clip)), **FILLED)
    plan = plan_video_request(env.cfg, env.deps.types, form)
    s = plan["start"]
    assert s["brand"] == "demo" and s["video_type"] == "koc-review" and s["brief_prefill"]["product"] == "Saphir dung dịch vệ sinh"
    assert re.search(r"Giá còn áp dụng: Có, giá 199.000đ", s["brief_text"]) and "(" not in s["brief_text"]


def test_free_text_brief_still_works_the_old_way(env):
    plan = plan_video_request(env.cfg, env.deps.types, head(env) + "\nVideo KOC dài 30 giây, giá 199k, mua ở fanpage")
    assert plan["start"]["brief_text"].startswith("Video KOC") and "brief_prefill" not in plan["start"]


def test_bad_request_is_refused_before_form(env):
    plan = plan_video_request(env.cfg, env.deps.types, f'/video "{env.clip}" khong-co demo')
    assert "start" not in plan and "Loại video" in plan["reply"]


# ───────────────────────── luồng: có phiếu thì không gọi AI trích brief ─────────────────────────
def test_prefilled_brief_skips_ai_extraction(tmp_path):
    e = make_env(tmp_path)
    vt = e.deps.types["koc-review"]
    form = fill(phieu.build_form(vt, "demo", {}, str(e.clip)), **FILLED)
    plan = plan_video_request(e.cfg, e.deps.types, form)
    e.state["brief_prefill"] = plan["start"]["brief_prefill"]
    e.state["brief_text"] = plan["start"]["brief_text"]
    assert e.mr.run(e.state) == "paused" and e.mr.pending["kind"] == "approval"   # qua cổng hỏi thêm, tới cổng duyệt 1
    assert ("orchestrator", "BriefExtract") not in e.llm.calls
    assert e.state["brief"]["product"] == "Saphir dung dịch vệ sinh"
