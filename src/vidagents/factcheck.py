"""Đối chiếu bằng chương trình (không dùng AI): giá, phần trăm giảm, ngày, "mua X tặng Y" xuất hiện TRÊN MÀN HÌNH
(đồ họa, phụ đề) có khớp brief không.

Vì sao: sai giá / sai hạn ưu đãi là lỗi đắt nhất của video quảng cáo, và AI đọc lại hình rất dễ bỏ sót hoặc nhìn nhầm số.
Quy tắc:
- Con số nằm trên màn hình mà brief (hoặc brand.json) không có  -> lỗi NẶNG (chương trình quyết, không để AI tự kết luận).
- Con số chỉ nằm trong lời kịch bản, chưa lên màn hình  -> lỗi VỪA (nhắc, người duyệt quyết).
- Brief nói KHÔNG hiện giá mà màn hình có giá -> NẶNG.
- Dạng video bắt buộc có ưu đãi/hạn mà số trong brief không thấy trên màn hình -> VỪA.
Chỉ so các con số có đơn vị rõ (tiền, %, ngày, mua-tặng). Con số thường như "3 bước", "2 nắp" không bị kiểm.
Giới hạn: không đọc chữ trong ảnh/clip gốc (không OCR); chỉ đọc chữ mà khung dựng tự sinh ra (edl.json, overlays.json, captions.json, .srt).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import schemas as S

KIND_LABEL = {"gia": "Giá", "phan_tram": "Phần trăm", "ngay": "Ngày", "mua_tang": "Khuyến mãi mua-tặng"}

_NUM = r"\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?"
_MONEY = re.compile(rf"(?<![\d.,/])({_NUM})\s*(triệu|tr|nghìn|ngàn|k|đồng|vnđ|vnd|₫|đ)(?!\w)", re.I)
_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:%|phần trăm)", re.I)
_DATE_SLASH = re.compile(r"(?<![\d./])(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{4}|\d{2}))?(?![\d/])")
_DATE_WORDS = re.compile(r"(?:ngày\s+)?(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+(?:năm\s+)?(\d{4}))?", re.I)
_BUY_GET = re.compile(r"mua\s*(\d+)\s*tặng\s*(\d+)", re.I)
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


@dataclass(frozen=True)
class Fact:
    kind: str  # gia | phan_tram | ngay | mua_tang
    key: str
    text: str  # đúng như được viết
    year: str = ""  # chỉ dùng cho ngày


def _to_number(s: str) -> float:
    if _THOUSANDS.fullmatch(s):
        return float(s.replace(".", "").replace(",", ""))
    return float(s.replace(",", "."))


def extract_facts(text: str) -> list[Fact]:
    out: list[Fact] = []
    for m in _MONEY.finditer(text):
        unit = m.group(2).lower()
        mult = 1_000_000 if unit in ("triệu", "tr") else 1000 if unit in ("nghìn", "ngàn", "k") else 1
        out.append(Fact("gia", str(int(round(_to_number(m.group(1)) * mult))), m.group(0).strip()))
    for m in _PERCENT.finditer(text):
        out.append(Fact("phan_tram", f"{_to_number(m.group(1)):g}%", m.group(0).strip()))
    for rx in (_DATE_SLASH, _DATE_WORDS):
        for m in rx.finditer(text):
            d, mo = int(m.group(1)), int(m.group(2))
            if not (1 <= d <= 31 and 1 <= mo <= 12):
                continue
            y = m.group(3) or ""
            if len(y) == 2:
                y = "20" + y
            out.append(Fact("ngay", f"{d:02d}/{mo:02d}", m.group(0).strip(), y))
    for m in _BUY_GET.finditer(text):
        out.append(Fact("mua_tang", f"mua{int(m.group(1))}tang{int(m.group(2))}", m.group(0).strip()))
    return out


def _matches(f: Fact, allowed: Iterable[Fact]) -> bool:
    for a in allowed:
        if a.kind == f.kind and a.key == f.key:
            if f.kind == "ngay" and a.year and f.year and a.year != f.year:
                continue  # cùng ngày tháng nhưng khác năm
            return True
    return False


def strings_of(obj: Any) -> list[str]:
    """Mọi chuỗi trong một cấu trúc JSON (bỏ qua khóa)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings_of(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in strings_of(v)]
    return []


def _json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_SRT_SKIP = re.compile(r"^\s*(\d+|\d\d:\d\d:\d\d[,.]\d+\s*-->.*)\s*$")


def load_displayed(edit_dir: Path, project_dir: Path | None, srt: Path | None = None) -> dict[str, list[str]]:
    """Chữ sẽ hiện trên video, gom theo nguồn. Nguồn nào chưa có file thì bỏ qua."""
    out: dict[str, list[str]] = {}
    edl = _json(Path(edit_dir) / "edl.json")
    if isinstance(edl, dict):
        t = strings_of(edl.get("graphics")) + [str(g.get("card", "")) for g in edl.get("gaps", []) if isinstance(g, dict)]
        if t:
            out["đồ họa trong danh sách cảnh (edl.json)"] = t
    if project_dir:
        ov = _json(Path(project_dir) / "assets" / "overlays.json")
        if isinstance(ov, dict) and ov.get("overlays"):
            out["đồ họa đã dựng (overlays.json)"] = strings_of(ov["overlays"])
        caps = _json(Path(project_dir) / "assets" / "captions.json")
        if caps:
            t = strings_of(caps.get("groups") if isinstance(caps, dict) else [c.get("text", "") if isinstance(c, dict) else "" for c in caps])
            if t:
                out["phụ đề (captions.json)"] = t
    if srt and Path(srt).exists():
        lines = [ln for ln in Path(srt).read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip() and not _SRT_SKIP.match(ln)]
        if lines:
            out["phụ đề (.srt)"] = lines
    return out


def load_truth(brief_text: str, edit_dir: Path, brand_json: Path | None) -> list[str]:
    """Nguồn "đúng": brief người giao viết, brief đã trích, thương hiệu đã duyệt."""
    texts = [brief_text or ""]
    texts += strings_of(_json(Path(edit_dir) / "brief.json"))
    if brand_json:
        texts += strings_of(_json(brand_json))
    return texts


@dataclass
class FactCheck:
    findings: list[S.CriticFinding]
    notes: list[str]


def check(*, truth_texts: list[str], displayed: dict[str, list[str]], script_lines: list[str], brief: dict | None,
          vt: S.VideoType | None) -> FactCheck:
    findings: list[S.CriticFinding] = []
    notes: list[str] = []
    allowed = [f for t in truth_texts for f in extract_facts(t)]
    seen: set[tuple[str, str, str]] = set()

    def add(sev: str, desc: str, hint: str) -> None:
        if (sev, desc, hint) in seen:
            return
        seen.add((sev, desc, hint))
        findings.append(S.CriticFinding(severity=sev, desc=desc, fix_hint=hint, layer=1))

    shown_facts: list[Fact] = []
    for where, texts in displayed.items():
        for t in texts:
            for f in extract_facts(t):
                shown_facts.append(f)
                if not _matches(f, allowed):
                    add("nang", f"[Đối chiếu brief] {KIND_LABEL[f.kind]} '{f.text}' hiện trên video ({where}) nhưng KHÔNG có trong brief",
                        "Sửa cho khớp brief hoặc bỏ khỏi video; nếu con số đúng thì người giao bổ sung vào brief")
    for line in script_lines:
        for f in extract_facts(line):
            if not _matches(f, allowed):
                add("vua", f"[Đối chiếu brief] {KIND_LABEL[f.kind]} '{f.text}' có trong lời kịch bản nhưng không có trong brief",
                    "Kiểm tra với người giao trước khi để lên màn hình")
    if not displayed:
        notes.append("Chưa có phụ đề/đồ họa để đối chiếu giá, phần trăm, ngày với brief (sẽ đối chiếu ở các bước sau).")
    else:
        b = brief or {}
        if b.get("show_price") is False:
            for f in shown_facts:
                if f.kind == "gia":
                    add("nang", f"[Đối chiếu brief] Brief nói KHÔNG hiện giá nhưng video có giá '{f.text}'", "Bỏ giá khỏi phụ đề/đồ họa")
        need: list[tuple[str, str]] = []
        req = vt.required_brief if vt else []
        if "offer_text" in req:
            need.append(("offer_text", "ưu đãi"))
        if "offer_deadline" in req:
            need.append(("offer_deadline", "hạn ưu đãi"))
        if b.get("price_text") and (b.get("show_price") is True or "price_text" in req):
            need.append(("price_text", "giá"))
        for key, label in need:
            for f in extract_facts(str(b.get(key) or "")):
                if not _matches(f, shown_facts):
                    add("vua", f"[Đối chiếu brief] Brief ghi {label} '{f.text}' nhưng không thấy trên phụ đề/đồ họa",
                        "Thêm vào đồ họa/phụ đề nếu cần hiện, hoặc xác nhận không cần hiện")
    return FactCheck(findings, notes)
