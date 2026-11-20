"""Phiếu brief điền sẵn (như phiếu brief của skill dựng video): bot gửi phiếu, người giao điền, gửi lại.

Vì sao có file này:
- Hỏi đủ một lần thay vì 2-3 lượt /bosung qua lại; ô thương hiệu đã biết thì điền sẵn và ghi rõ nguồn.
- Đọc phiếu bằng luật cố định (không gọi AI): không tốn tiền, không suy diễn giá/ưu đãi/lợi ích.
- Nội dung đưa cho các agent là bản chuẩn hóa từ giá trị đã đọc, KHÔNG phải nguyên văn tin nhắn: gợi ý và chú thích nguồn
  trong phiếu không lọt vào "nguồn sự thật" mà bước kiểm số liệu dùng để đối chiếu.
- Giá, ưu đãi, hạn ưu đãi, lợi ích KHÔNG bao giờ điền sẵn (hết hạn, đổi theo đợt): người giao tự ghi.

Định dạng một dòng: `Nhãn * (gợi ý): giá trị [nguồn]`. Chỉ cần giữ nguyên phần nhãn; gợi ý trong ngoặc và * có thể bỏ.
Gợi ý không được chứa dấu hai chấm hay chữ số (dấu hai chấm tách nhãn, chữ số có thể bị hiểu là số liệu).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import schemas as S


@dataclass(frozen=True)
class Field:
    key: str          # khóa nội bộ
    label: str        # nhãn (phần dùng để nhận ra dòng)
    section: str
    req: bool = False
    hint: str = ""
    to: str = "note"  # thuộc tính của BriefExtract, "extra.<k>" hoặc "note" (chỉ nằm trong nội dung brief)


SECTIONS = {"A": "A. Sản phẩm và lợi ích", "B": "B. Ưu đãi và nơi mua", "C": "C. Định hướng video", "D": "D. Riêng cho dạng video này"}

BASE: list[Field] = [
    Field("product", "Sản phẩm chính", "A", True, "tên đúng trên bao bì", "product"),
    Field("benefits", "Lợi ích được phép nói", "A", True, "đúng câu chữ thương hiệu đã duyệt, hoặc ghi - theo lời người nói", "benefits_allowed"),
    Field("price", "Giá còn áp dụng", "A", True, "Có, giá <số>đ / Không", "price"),
    Field("offer", "Ưu đãi", "B", False, "nếu có", "offer_text"),
    Field("deadline", "Hạn ưu đãi", "B", False, "đến ngày nào", "offer_deadline"),
    Field("where", "Nơi mua", "B", True, "hotline / fanpage / link / cửa hàng, ít nhất một kênh", "where_to_buy"),
    Field("cta", "Câu CTA muốn dùng", "B"),
    Field("goal", "Mục tiêu", "C", True, "quảng cáo bán hàng / recap sự kiện / khác", "goal"),
    Field("name", "Tên video / đợt", "C"),
    Field("duration", "Độ dài mong muốn", "C", False, "giây", "duration_seconds"),
    Field("style", "Phong cách dựng", "C", False, "ban-nhanh cắt nhanh / muot nhẹ nhàng / trend nhịp gấp; bỏ trống = hệ thống đề xuất"),
    Field("music", "Nhạc", "C", False, "A tươi / B ấm / file riêng có giấy phép thương mại"),
    Field("color", "Kiểu màu", "C", False, "tươi + mịn da / giữ tông da / tự nhiên"),
    Field("keep", "Câu bắt buộc giữ", "C", False, "nhiều câu thì ngăn bằng dấu ;", "must_keep"),
    Field("drop", "Câu bỏ", "C"),
    Field("avoid", "Câu chữ KHÔNG được dùng", "C", False, "cam kết công dụng, so sánh, xếp hạng số một... ngăn bằng dấu ;", "must_avoid"),
    Field("other_brands", "Thương hiệu khác cần tránh lộ trong khung", "C", False, "ngăn bằng dấu ;", "other_brands_to_avoid"),
    Field("people", "Người cần ghi tên", "C"),
    Field("approver", "Người duyệt cuối + hạn giao", "C"),
]
CONSENT = Field("consent", "Người nói đã đồng ý xuất hiện và đăng video chưa", "D", True, "Có / Chưa", "extra.consent")

_ANNOT = re.compile(r"\s*\[(?:từ|mặc định|gợi ý)[^\]]*\]\s*$", re.I)
_BLANK = {"", "…", "...", "…..", "......"}


def norm(s: str) -> str:
    """Chuẩn hóa nhãn để so khớp: bỏ gợi ý trong ngoặc, dấu *, dấu câu, hoa thường."""
    s = re.sub(r"\([^)]*\)", " ", s).replace("*", " ")
    s = re.sub(r"[\W_]+", " ", s)
    return " ".join(s.casefold().split())


def _split_label(text: str) -> tuple[str, str]:
    """'Lời dẫn: (a) chữ / (b) giọng AI' -> ('Lời dẫn', '(a) chữ / (b) giọng AI'); nhãn hiển thị không chứa dấu hai chấm."""
    base = re.split(r"\s*[:(]", text, maxsplit=1)[0].strip()
    hint = text[len(base):].strip(" :")
    if hint.startswith("(") and hint.endswith(")") and hint.count("(") == 1:
        hint = hint[1:-1]
    hint = re.sub(r"\(([^()]{1,3})\)", r"\1.", hint)   # '(a) chữ / (b) giọng' -> 'a. chữ / b. giọng'
    # ngoặc còn lại và dấu hai chấm sẽ làm hỏng việc nhận nhãn (hint nằm giữa nhãn và dấu hai chấm)
    return base, hint.replace("(", "").replace(")", "").replace(":", " -")


def fields_for(vt: S.VideoType) -> list[Field]:
    """Các ô của phiếu cho một dạng video: ô chung + ô bắt buộc riêng của dạng + xác nhận người nói (dạng có người nói trước máy)."""
    need = set(vt.required_brief)
    out: list[Field] = []
    for f in BASE:
        if f.to in need:  # dạng này bắt buộc ô đó (vd. hội chợ: ưu đãi + hạn ưu đãi)
            f = Field(f.key, f.label, f.section, True, f.hint, f.to)
        out.append(f)
    extras = [k for k in vt.required_brief if k.startswith("extra.")]
    if vt.suggested_mode in ("B", "auto") and "extra.consent" not in extras:
        out.append(CONSENT)
    for k in extras:
        label, hint = _split_label(vt.labels.get(k) or k.removeprefix("extra."))
        out.append(Field(k.removeprefix("extra."), label, "D", True, hint, k))
    return out


# ───────────────────────── tạo phiếu ─────────────────────────
GRADE_VI = {"tuoi_min": "tươi + mịn da", "giu_tong_da": "giữ tông da", "tu_nhien": "tự nhiên"}


def prefill(vt: S.VideoType, brand: dict) -> dict[str, tuple[str, str]]:
    """Giá trị điền sẵn: {khóa: (giá trị, nguồn)}. Chỉ lấy từ thương hiệu đã duyệt và mặc định của dạng video."""
    out: dict[str, tuple[str, str]] = {}
    src = "từ thương hiệu"
    if str(brand.get("stores") or "").strip():
        out["where"] = (str(brand["stores"]).strip(), src)
    avoid = [str(x) for x in brand.get("claims_avoid") or [] if str(x).strip()]
    if avoid:
        out["avoid"] = ("; ".join(avoid), src)
    if vt.target_seconds:
        out["duration"] = (f"{vt.target_seconds} giây", "mặc định của dạng video")
    music = str((brand.get("music") or {}).get("default") or "")
    if music:
        stem = Path(music).stem
        out["music"] = ({"a_tuoi": "A tươi", "b_am": "B ấm"}.get(stem, stem), "mặc định thương hiệu")
    if brand.get("grade"):
        out["color"] = (GRADE_VI.get(str(brand["grade"]), str(brand["grade"])), "mặc định thương hiệu")
    return out


def footage_line(folder: str, limit: int = 8) -> str:
    """'5 video (4,9 GB): C7345.MP4, ...' để người giao thấy bot sẽ dùng đúng thư mục."""
    from . import workspace as W

    p = Path(folder)
    try:
        files = sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in W._MEDIA["video"])
    except OSError:
        return ""
    if not files:
        return W.media_summary(p, depth=2)
    size = sum(f.stat().st_size for f in files)
    names = ", ".join(f.name for f in files[:limit]) + (f" và {len(files) - limit} file nữa" if len(files) > limit else "")
    return f"{len(files)} video ({W._human(size)}): {names}"


def load_brand(cfg: Config, brand: str) -> dict:
    """brand.json của thương hiệu ({} nếu không đọc được)."""
    try:
        return json.loads((cfg.projects_root / "_brands" / brand / "brand.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def brand_gaps(brand: dict) -> list[str]:
    """Những gì bộ thương hiệu còn thiếu và hậu quả. Thiếu thì video vẫn dựng được nhưng bị critic bắt lỗi hoặc kém chuyên nghiệp."""
    gaps = []
    if not brand.get("logo"):
        gaps.append("logo (end card dùng chữ tên thương hiệu thay logo)")
    if not str(brand.get("tagline") or "").strip():
        gaps.append("câu định vị (end card không có dòng này)")
    if not brand.get("claims_avoid"):
        gaps.append("danh sách câu chữ cấm (critic không chặn được cam kết rủi ro, anh/chị nhớ ghi ở ô 'Câu chữ KHÔNG được dùng')")
    return gaps


def build_form(vt: S.VideoType, brand_slug: str, brand: dict, folder: str, footage: str = "") -> str:
    fields = fields_for(vt)
    pre = prefill(vt, brand)
    lines = [f'/video "{folder}" {vt.id} {brand_slug}',
             f"PHIẾU BRIEF · {vt.name_vi} · thương hiệu {brand_slug}"]
    if footage:
        lines.append(f"Footage: {footage}")
    if (gaps := brand_gaps(brand)):
        lines.append(f"⚠ Bộ thương hiệu '{brand_slug}' còn thiếu: " + "; ".join(gaps) + ". Vẫn dựng được; nên nhờ quản trị bổ sung.")
    lines += ["Cách dùng: copy cả tin này, điền sau dấu hai chấm của từng dòng, rồi gửi lại. Mục có * là bắt buộc.",
              "Chỉ ghi điều thương hiệu đã xác nhận. Hệ thống không tự viết thêm giá, ưu đãi, công dụng.",
              "Ô có [từ ...] đã điền sẵn: sửa nếu sai (xóa cả phần trong ngoặc vuông cũng được)."]
    last = ""
    for f in fields:
        if f.section != last:
            lines += ["", SECTIONS[f.section]]
            last = f.section
        label = f"{f.label}{' *' if f.req else ''}{f' ({f.hint})' if f.hint else ''}"
        val, source = pre.get(f.key, ("", ""))
        lines.append(f"{label}: {val}{f' [{source}]' if source else ''}".rstrip() if val else f"{label}: ")
    return "\n".join(lines)


# ───────────────────────── đọc phiếu ─────────────────────────
def _index(fields: list[Field]) -> dict[str, Field]:
    return {norm(f.label): f for f in fields}


def parse_form(text: str, vt: S.VideoType, skip_first: bool = True) -> dict[str, str]:
    """Đọc các dòng 'Nhãn: giá trị' của phiếu. Giá trị nhiều dòng thì nối lại. Ô trống / còn nguyên '…' thì bỏ.
    skip_first: bỏ dòng đầu (là lệnh /video) khi đọc cả tin phiếu; False khi đọc chữ người dùng gõ rời (bước thêm thông tin trong bot)."""
    idx = _index(fields_for(vt))
    values: dict[str, str] = {}
    cur: Field | None = None
    for raw in (text.splitlines()[1:] if skip_first else text.splitlines()):
        line = raw.strip()
        if not line:
            cur = None
            continue
        if re.match(r"^[A-F]\.\s", line):
            cur = None
            continue
        m = re.match(r"^[•\-–·*\s]*([^:]{1,160}):(.*)$", line)
        if m:
            f = idx.get(norm(m.group(1)))
            cur = f
            if f:
                values[f.key] = _ANNOT.sub("", m.group(2)).strip()
            continue
        if cur is not None:  # dòng tiếp của giá trị nhiều dòng
            values[cur.key] = f"{values.get(cur.key, '')} {_ANNOT.sub('', line).strip()}".strip()
    return {k: v for k, v in values.items() if v not in _BLANK}


def is_form(text: str, vt: S.VideoType) -> bool:
    """Có phải phiếu không (ít nhất 3 dòng có nhãn của phiếu)? Brief viết tự do thì đi đường cũ (AI trích)."""
    idx = _index(fields_for(vt))
    hits = 0
    for line in text.splitlines()[1:]:
        m = re.match(r"^[•\-–·*\s]*([^:]{1,160}):", line.strip())
        if m and norm(m.group(1)) in idx:
            hits += 1
    return hits >= 3


_PRICE_YES = re.compile(r"^\s*(có|co)\b[\s,:.\-]*(?:(?:giá|gia)\b[\s,:.\-]*)?(.*)$", re.I)
_PRICE_NO = re.compile(r"^\s*(không|khong|ko|chưa|bỏ giá)\b", re.I)


def parse_price(v: str) -> tuple[bool | None, str | None]:
    """'Có, giá 199.000đ' -> (True, '199.000đ'); 'Không' -> (False, None); không hiểu -> (None, None)."""
    if _PRICE_NO.match(v or ""):
        return False, None
    m = _PRICE_YES.match(v or "")
    if m:
        return True, (m.group(2).strip() or None)
    return None, None


def missing(values: dict[str, str], vt: S.VideoType) -> list[str]:
    """Nhãn các ô bắt buộc còn thiếu (hoặc giá trị không hiểu được)."""
    out = []
    for f in fields_for(vt):
        if not f.req:
            continue
        v = values.get(f.key, "")
        if not v:
            out.append(f"{f.label} ({f.hint})" if f.hint else f.label)
        elif f.to == "price":
            show, price = parse_price(v)
            if show is None:
                out.append("Giá còn áp dụng: ghi 'Có, giá ...đ' hoặc 'Không'")
            elif show and not price:
                out.append("Giá còn áp dụng: có giá thì ghi rõ số tiền ('Có, giá ...đ')")
    return out


def _items(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n|]", v) if x.strip()]


def _seconds(v: str) -> int | None:
    m = re.search(r"\d+(?:[.,]\d+)?", v)
    if not m:
        return None
    n = float(m.group(0).replace(",", "."))
    return int(round(n * 60)) if re.search(r"ph[uú]t|min", v, re.I) else int(round(n))


def to_brief(values: dict[str, str], vt: S.VideoType, brand_slug: str) -> S.BriefExtract:
    b = S.BriefExtract(brand=brand_slug)
    for f in fields_for(vt):
        v = values.get(f.key)
        if not v or f.to == "note":
            continue
        if f.to == "price":
            b.show_price, b.price_text = parse_price(v)
        elif f.to == "duration_seconds":
            b.duration_seconds = _seconds(v)
        elif f.to in ("must_keep", "must_avoid", "other_brands_to_avoid"):
            setattr(b, f.to, _items(v))
        elif f.to.startswith("extra."):
            b.extra[f.to.removeprefix("extra.")] = v
        else:
            setattr(b, f.to, v)
    return b


def canonical_text(values: dict[str, str], vt: S.VideoType) -> str:
    """Nội dung brief đưa cho các agent: chỉ nhãn + giá trị đã đọc, không có gợi ý hay chú thích nguồn."""
    lines = [f"Brief của người giao việc, lấy từ phiếu: {vt.name_vi}"]
    lines += [f"{f.label}: {values[f.key]}" for f in fields_for(vt) if values.get(f.key)]
    return "\n".join(lines)
