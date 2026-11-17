"""Khuôn dữ liệu trao đổi giữa các agent (nguyên tắc 2: giao thức chung).

Skill được đổi cách làm bên trong, nhưng đầu ra PHẢI đúng các khuôn này. Đổi khuôn = đổi SCHEMA_VERSION + sửa code.
"""
from __future__ import annotations

import json
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
        s, e = r.get("start"), r.get("end")
        if not (_num(s) and _num(e)) or e <= s:
            errs.append(f"ranges[{i}] start/end sai")
    for key in ("broll", "covers"):
        for i, x in enumerate(edl.get(key) or []):
            if not isinstance(x, dict) or x.get("source") not in sources:
                errs.append(f"{key}[{i}].source không có trong sources")
            elif key == "covers" and str(x.get("range")) not in ns:
                errs.append(f"covers[{i}].range '{x.get('range')}' không khớp đoạn nào")
    return errs


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
    action: Literal["approve", "revise", "cancel", "continue", "answer", "select", "skip"]
    by: str
    note: str = ""
    mode: Literal["A", "B", "C", "S"] | None = None
    ids: list[int] = Field(default_factory=list)
    pin: list[int] = Field(default_factory=list)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
