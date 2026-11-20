"""Khuôn dữ liệu trao đổi giữa các agent (nguyên tắc 2: giao thức chung).

Skill được đổi cách làm bên trong, nhưng đầu ra PHẢI đúng các khuôn này. Đổi khuôn = đổi SCHEMA_VERSION + sửa code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

# A thuyết minh · B KOC/người nói · C hội chợ (cảnh nền, theo beat) · S dựng từ slide (dành sẵn, chưa có lệnh)
Mode = Literal["A", "B", "C", "S"]
MODES = ("A", "B", "C", "S")


class SchemaError(Exception):
    pass


# ───────────────────────── brief ─────────────────────────
class BriefExtract(BaseModel):
    brand: str | None = None
    product: str | None = None
    goal: str | None = None
    duration_seconds: int | None = None
    show_price: bool | None = None
    price_text: str | None = None
    offer_text: str | None = None
    offer_deadline: str | None = None
    where_to_buy: str | None = None
    benefits_allowed: str | None = None
    must_keep: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    other_brands_to_avoid: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)


COMMON_REQUIRED = ["brand", "product", "goal"]
FIELD_LABELS = {
    "brand": "Thương hiệu",
    "product": "Sản phẩm",
    "goal": "Mục tiêu video (bán hàng / recap / ...)",
    "duration_seconds": "Độ dài mong muốn",
    "price_text": "Giá hiển thị (đúng như muốn ghi)",
    "offer_text": "Nội dung ưu đãi",
    "offer_deadline": "Hạn ưu đãi",
    "where_to_buy": "Nơi mua / CTA",
    "benefits_allowed": "Lợi ích được phép ghi",
}


class VideoType(BaseModel):
    id: str
    name_vi: str
    suggested_mode: Literal["A", "B", "C", "S", "auto"]
    mode_note: str = ""
    target_seconds: int | None = None
    required_brief: list[str] = Field(default_factory=list)  # ngoài COMMON_REQUIRED; "extra.xxx" = ô riêng của dạng
    labels: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    status: Literal["nhap", "da_dung_that"] = "nhap"


def load_video_types(directory: Path) -> dict[str, VideoType]:
    types: dict[str, VideoType] = {}
    for f in sorted(Path(directory).glob("*.json")):
        if f.name.startswith("_"):
            continue
        vt = VideoType.model_validate_json(f.read_text(encoding="utf-8"))
        if vt.id != f.stem:
            raise SchemaError(f"{f.name}: id '{vt.id}' khác tên file")
        types[vt.id] = vt
    return types


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == []


def _get(brief: BriefExtract, key: str) -> Any:
    if key.startswith("extra."):
        return brief.extra.get(key[6:])
    return getattr(brief, key, None)


def missing_fields(brief: BriefExtract, vt: VideoType) -> list[str]:
    keys = list(dict.fromkeys(COMMON_REQUIRED + vt.required_brief))
    out = [k for k in keys if _blank(_get(brief, k))]
    if brief.show_price and _blank(brief.price_text) and "price_text" not in out:
        out.append("price_text")
    return out


def label_of(key: str, vt: VideoType) -> str:
    return vt.labels.get(key) or FIELD_LABELS.get(key) or key.replace("extra.", "")


# ───────────────────────── footage ─────────────────────────
class ModeAdvice(BaseModel):
    mode: Literal["A", "B", "C", "S"]
    reason: str


class FootageReport(BaseModel):
    summary: str
    usable_clips: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    other_brands_seen: list[str] = Field(default_factory=list)
    notable_speech: list[str] = Field(default_factory=list)


# ───────────────────────── script ─────────────────────────
class ScriptRow(BaseModel):
    n: str
    idea: str
    source: str = ""
    time: str = ""
    line: str = ""
    reason: str = ""


class ExternalCost(BaseModel):
    what: str
    amount_text: str


class ScriptDoc(BaseModel):
    strategy_vi: str
    rows: list[ScriptRow]
    estimated_seconds: float | None = None
    style: str | None = None
    style_reason: str = ""
    external_costs: list[ExternalCost] = Field(default_factory=list)
    ad_risks: list[str] = Field(default_factory=list)
    need_info: list[str] = Field(default_factory=list)
    edl: dict[str, Any]


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_edl_b(edl: dict) -> list[str]:
    """Khuôn EDL của chế độ B, theo mục B3 của skill dựng video hiện có."""
    errs: list[str] = []
    sources = edl.get("sources")
    if not isinstance(sources, dict) or not sources:
        errs.append("thiếu 'sources'")
        sources = {}
    ranges = edl.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        errs.append("thiếu 'ranges'")
        return errs
    ns: set[str] = set()
    for i, r in enumerate(ranges):
        if not isinstance(r, dict):
            errs.append(f"ranges[{i}] không phải object")
            continue
        ns.add(str(r.get("n")))
        if r.get("source") not in sources:
            errs.append(f"ranges[{i}].source '{r.get('source')}' không có trong sources")
        elif "/project-assets/ai/" in str(sources.get(r.get("source"))).replace("\\", "/"):
            errs.append(f"ranges[{i}].source là video AI trám (không có lời để cắt): chỉ dùng làm covers/broll, đoạn lời phải lấy từ clip quay")
        s, e = r.get("start"), r.get("end")
        if not (_num(s) and _num(e)) or e <= s:
            errs.append(f"ranges[{i}] start/end sai")
    for key in ("broll", "covers"):
        for i, x in enumerate(edl.get(key) or []):
            if not isinstance(x, dict) or x.get("source") not in sources:
                errs.append(f"{key}[{i}].source không có trong sources")
            elif key == "covers" and str(x.get("range")) not in ns:
                errs.append(f"covers[{i}].range '{x.get('range')}' không khớp đoạn nào")
    # khoảng thẻ (gaps): skill luôn cắt một hình nền dưới thẻ, nên cần source (KHÓA trong sources) + start (giây) + seconds
    for i, g in enumerate(edl.get("gaps") or []):
        if not isinstance(g, dict):
            errs.append(f"gaps[{i}] không phải object")
            continue
        if g.get("image") is not None:   # nền thẻ là ảnh (ảnh sản phẩm): không cần source/start; file có thật kiểm ở missing_sources
            if not isinstance(g["image"], str) or not g["image"].strip():
                errs.append(f"gaps[{i}].image phải là đường dẫn ảnh")
        else:
            if g.get("source") not in sources:
                errs.append(f"gaps[{i}].source phải là KHÓA trong sources (vd 'A'), không phải chữ mô tả")
            if not _num(g.get("start")) or g["start"] < 0:
                errs.append(f"gaps[{i}].start phải là số giây trong clip nguồn (hình nền dưới thẻ)")
        if not _num(g.get("seconds")) or g["seconds"] <= 0:
            errs.append(f"gaps[{i}].seconds phải là số giây > 0")
        ref = g.get("after_range", g.get("before_range"))
        if str(ref) not in ns:
            errs.append(f"gaps[{i}] cần after_range hoặc before_range trỏ đúng một đoạn (n) trong ranges")
    return errs


_VIDEO_EXT = (".mp4", ".mov", ".mkv", ".m4v")


def clips_in(folder: Any) -> list[Path]:
    """Các clip video ở cấp trên cùng của thư mục (đúng phạm vi bước prep của skill)."""
    try:
        return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXT)
    except (OSError, TypeError):
        return []


def _match_name(value: str, pool: list[dict]) -> str | None:
    """Tên/đường dẫn AI viết -> đường dẫn thật trong bảng tài nguyên, chỉ khi khớp ĐÚNG MỘT mục (theo tên file hoặc tên bỏ đuôi)."""
    base = Path(value.replace("\\", "/")).name.casefold()
    hits = [x for x in pool if x.get("ten", "").casefold() == base or Path(x.get("ten", "")).stem.casefold() == Path(base).stem]
    if not hits:
        hits = [x for x in pool if x.get("ten") and re.search(rf"(?<!\w){re.escape(Path(x['ten']).stem)}(?!\w)", value, re.I)]
    return hits[0]["path"] if len(hits) == 1 else None


def normalize_edl_b(edl: dict, folder: Any, brief: dict | None = None, assets: dict | None = None) -> list[str]:
    """Sửa tại chỗ các lỗi MÁY MÓC hay gặp ở EDL do AI viết (không đổi nội dung kịch bản); trả danh sách ghi chú đã sửa.
    1. sources ghi tên clip ('C7353', 'C7353.MP4', 'C7353 (chữ mô tả)') thay vì đường dẫn thật -> đổi thành đường dẫn file trong `folder`
       (hoặc trong bảng tài nguyên project: video AI).
    2. gaps thiếu nền (source không phải khóa, start rỗng): lấy nền tạm từ chính đoạn bên cạnh (cuối đoạn trước thẻ, hoặc đầu đoạn sau thẻ).
       gaps[i].image ghi tên ảnh -> đường dẫn ảnh thật trong bảng tài nguyên; ảnh không có thật thì bỏ (rơi về nền từ clip).
    3. end card thiếu nơi mua: lấy 'Nơi mua' người giao ghi trong brief (graphics.end.where), thay vì để AI viết hoặc chờ critic bắt lỗi.
    4. end card dùng logo của project (graphics.end.logo) nếu project có logo; logo ghi sai đường dẫn thì bỏ.
    `assets` = bảng project_assets.json (logo, anh_san_pham, video_ai)."""
    notes: list[str] = []
    assets = assets or {}
    logo = assets.get("logo")
    gr0 = edl.get("graphics")
    if (logo and Path(logo).is_file()) or (isinstance(gr0, dict) and isinstance(gr0.get("end"), dict) and gr0["end"].get("logo")):
        gr = edl.get("graphics")
        if not isinstance(gr, dict):
            gr = edl["graphics"] = {}
        end = gr.get("end") if isinstance(gr.get("end"), dict) else {}
        cur = str(end.get("logo") or "").strip()
        if logo and Path(logo).is_file() and not (cur and Path(cur).is_file()):
            gr["end"] = {**end, "logo": Path(logo).as_posix()}
            notes.append("graphics.end.logo: lấy logo của project")
        elif cur and not Path(cur).is_file():
            gr["end"] = {k: v for k, v in end.items() if k != "logo"}
            notes.append("graphics.end.logo: bỏ vì file không có thật (end card dùng logo của thương hiệu)")
    where = ((brief or {}).get("where_to_buy") or "").strip()
    if where:
        gr = edl.get("graphics")
        if not isinstance(gr, dict):
            gr = edl["graphics"] = {}
        end = gr.get("end") if isinstance(gr.get("end"), dict) else {}
        if not str(end.get("where") or "").strip():
            gr["end"] = {**end, "where": where}
            notes.append("graphics.end.where: lấy từ ô 'Nơi mua' của brief")
    srcs = edl.get("sources")
    if not isinstance(srcs, dict):
        return notes
    clips = clips_in(folder)
    for k, v in list(srcs.items()):
        if not isinstance(v, str):
            continue
        p = Path(v)
        if p.is_file():
            if v != p.as_posix():
                srcs[k] = p.as_posix()
            continue
        hits = [c for c in clips if re.search(rf"(?<!\w){re.escape(c.stem)}(?!\w)", v, re.I)]
        if len(hits) == 1:
            srcs[k] = hits[0].as_posix()
            notes.append(f"sources['{k}']: '{v[:40]}' -> {hits[0].name}")
        elif (ai := _match_name(v, assets.get("video_ai") or [])):
            srcs[k] = ai
            notes.append(f"sources['{k}']: '{v[:40]}' -> video AI {Path(ai).name}")
    ranges = {str(r.get("n")): r for r in edl.get("ranges") or [] if isinstance(r, dict)}
    for i, g in enumerate(edl.get("gaps") or []):
        if isinstance(g, dict) and g.get("image") is not None:
            img = str(g["image"]).strip()
            if not Path(img).is_file():
                fixed = _match_name(img, assets.get("anh_san_pham") or [])
                if fixed:
                    g["image"] = fixed
                    notes.append(f"gaps[{i}].image: '{img[:40]}' -> {Path(fixed).name}")
                else:
                    g.pop("image")   # ảnh không có thật: bỏ, để nền lấy từ clip bên dưới
                    notes.append(f"gaps[{i}].image: '{img[:40]}' không có thật, bỏ (nền lấy từ clip)")
            elif img != Path(img).as_posix():
                g["image"] = Path(img).as_posix()
        if not isinstance(g, dict) or g.get("image") or (g.get("source") in srcs and _num(g.get("start"))):
            continue
        after = "after_range" in g
        ref = ranges.get(str(g.get("after_range" if after else "before_range")))
        if not ref or ref.get("source") not in srcs or not (_num(ref.get("start")) and _num(ref.get("end"))):
            continue
        secs = g["seconds"] if _num(g.get("seconds")) and g["seconds"] > 0 else 2.0
        g["source"] = ref["source"]
        g["start"] = round(max(0.0, ref["end"] - secs) if after else ref["start"], 2)
        g["auto_background"] = f"nền tạm lấy từ {'cuối' if after else 'đầu'} đoạn {ref.get('n')}; đổi source/start nếu muốn cảnh khác"
        notes.append(f"gaps[{i}]: nền tạm từ đoạn {ref.get('n')}")
    return notes


def missing_sources(edl: dict) -> list[str]:
    """sources trỏ tới file không có thật (đường dẫn sai / chữ mô tả)."""
    srcs = edl.get("sources") if isinstance(edl, dict) else None
    out = [f"sources['{k}'] không phải file có thật: '{str(v)[:60]}'" for k, v in (srcs or {}).items() if not Path(str(v)).is_file()]
    for i, g in enumerate((edl.get("gaps") or []) if isinstance(edl, dict) else []):
        if isinstance(g, dict) and g.get("image") and not Path(str(g["image"])).is_file():
            out.append(f"gaps[{i}].image không phải file có thật: '{str(g['image'])[:60]}'")
    return out


def validate_edl(mode: str, edl: Any) -> list[str]:
    """Chế độ A/C/S: skill chưa khai báo khuôn riêng nên chỉ kiểm tối thiểu. Khai báo thêm ở đây khi skill thật vào."""
    if not isinstance(edl, dict) or not edl:
        return ["edl rỗng hoặc không phải object"]
    if mode == "B":
        return validate_edl_b(edl)
    return []


class FixPlan(BaseModel):
    edl: dict[str, Any]
    changes: list[str] = Field(default_factory=list)
    unfixable: list[str] = Field(default_factory=list)


# ───────────────────────── critic ─────────────────────────
class CriticFinding(BaseModel):
    severity: Literal["nang", "vua"]  # nặng / vừa
    desc: str
    at_seconds: float | None = None
    fix_hint: str = ""
    needs_human: bool = False
    layer: Literal[1, 2] = 1


class CriticReport(BaseModel):
    findings: list[CriticFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Layer1Report(CriticReport):
    """Chuyển báo cáo của script critic thành danh sách lỗi."""


class Layer2Report(CriticReport):
    """Lớp AI xem khung hình."""


# ───────────────────────── bài học ─────────────────────────
class LessonProposal(BaseModel):
    agent: Literal["script", "footage", "edit", "critic", "orchestrator"]
    text: str
    scope: str = "chung"  # chung | dang:<id> | brand:<tên>
    kind: Literal["quy_tac", "so_thich", "loi", "thong_so", "mau"] = "quy_tac"
    source: Literal["human", "critic", "edit_fix", "human_miss"] = "human"
    feedback_index: int | None = None


class LessonProposals(BaseModel):
    items: list[LessonProposal] = Field(default_factory=list)


# ───────────────────────── tin nhắn từ người dùng vào cổng duyệt ─────────────────────────
class Payload(BaseModel):
    action: Literal["approve", "revise", "cancel", "continue", "answer", "select", "skip", "handedit", "done"]
    by: str
    note: str = ""
    mode: Literal["A", "B", "C", "S"] | None = None
    ids: list[int] = Field(default_factory=list)
    pin: list[int] = Field(default_factory=list)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
