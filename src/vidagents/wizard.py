"""Giao việc bằng nút bấm trên Telegram: chọn thư mục footage -> loại video -> thương hiệu -> điền BẢNG PHIẾU brief ngay trong chat
(một tin nhắn hiện mọi ô, bấm ô để điền, cập nhật tại chỗ) -> giao. Thay cho việc gõ `/video "<đường dẫn>" <loại> <thương hiệu>`
rồi copy phiếu, điền, gửi lại.

Logic thuần (không dùng thư viện telegram) để test được. Trạng thái của một người nằm trong một dict `st` (bot giữ trong user_data).
Mỗi bước trả về Result: chữ + nút để gửi, và khi xong thì tham số cho `runner.start`. Nút trên tin cũ (đã qua bước) báo "hết hạn", không làm gì.
Tạo job vẫn đi qua đúng các kiểm tra cũ (check_request, phieu.missing), nên không có đường tắt nào bỏ qua kiểm tra.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import phieu, projectassets
from . import schemas as S
from .config import Config

PAGE = 10          # thư mục mỗi trang (2 cột x 5 hàng: gọn, không phải cuộn)
COLS = 2
LABEL_MAX = 24     # độ dài chữ trên một nút (nút 2 cột hiện được chừng này)
_HIDE = ("edit", "system volume information", "$recycle.bin", "recycler")
# trả lời nhanh bằng nút cho các ô hay gặp (gõ tay vẫn được)
QUICK = {"goal": ["Quảng cáo bán hàng", "Recap sự kiện"], "price": ["Không"], "consent": ["Có", "Chưa"], "benefits": ["Theo lời người nói"],
         "style": ["ban-nhanh", "muot", "trend"], "music": ["A tươi", "B ấm"], "color": ["tươi + mịn da", "giữ tông da", "tự nhiên"]}
EXPIRED = "Nút này đã hết hạn (bạn đã sang bước khác). Gõ /menu để làm lại."


@dataclass
class Result:
    text: str
    kb: list = field(default_factory=list)
    start: dict | None = None    # có = giao việc ngay: tham số cho runner.start
    end: bool = False            # kết thúc wizard, bot xóa trạng thái
    panel: bool = False          # kết quả là BẢNG PHIẾU: khi đây là trả lời cho chữ người dùng gõ, bot sửa tại chỗ tin bảng thay vì gửi tin mới


def wb(code: str, arg: str = "") -> str:
    return "|".join(["w", code, arg or "-"])


def new_state(cfg: Config, recent: list[str] | None = None, past: list[dict] | None = None) -> dict:
    """past: các việc đã giao của người này, mới nhất trước ({footage_dir, video_type, brand}); dùng cho nút "Như lần trước"."""
    return {"stage": "folder", "cwd": str(cfg.footage_root), "page": 0, "items": [], "q": "", "recent": list(recent or [])[:3], "folder": "",
            "vtype": "", "brand": "", "types": [], "brands": [], "values": {}, "need": [], "i": 0, "past": list(past or [])[:20], "again": None}


def _under(p: Path, root: Path) -> bool:
    try:
        rp, rr = p.resolve(strict=True), root.resolve()
    except OSError:
        return False
    return rp == rr or rr in rp.parents


def _fold(s: str) -> str:
    """Bỏ dấu + hoa thường để tìm "nhat hai" ra "Nhật Hải" (người dùng hay gõ không dấu)."""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


_DATES = (re.compile(r"(?<!\d)(\d{4})[-._ ](\d{1,2})[-._ ](\d{1,2})(?!\d)"), "ymd"), (re.compile(r"(?<!\d)(\d{1,2})[-._ ](\d{1,2})[-._ ](\d{4})(?!\d)"), "dmy"), \
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "ymd")


def _date_of(name: str) -> date | None:
    for rx, order in _DATES:
        m = rx.search(name)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
        try:
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def sort_names(names: list[str]) -> list[str]:
    """Thư mục đặt theo ngày (2026-11-20, 20.11.2026...) thì mới nhất lên đầu; còn lại theo tên. Hỗn hợp: có ngày lên trước."""
    dated = {n: _date_of(n) for n in names}
    if sum(1 for v in dated.values() if v) * 2 >= max(1, len(names)):      # đa số là thư mục theo ngày
        return sorted(names, key=lambda n: (dated[n] is None, -(dated[n].toordinal() if dated[n] else 0), _fold(n)))
    return sorted(names, key=_fold)


def list_dirs(cwd: Path, q: str = "") -> list[str]:
    try:
        # giấu thư mục kết quả (edit) và thư mục tài nguyên của project (logo, ảnh sản phẩm, video AI): không phải thư mục để giao job
        names = [p.name for p in cwd.iterdir() if p.is_dir() and not p.name.startswith((".", "$", "#")) and p.name.lower() not in _HIDE
                 and not projectassets.asset_kind(p.name)]
    except OSError:
        return []
    if q:
        fq = _fold(q)
        names = [n for n in names if fq in _fold(n)]
    return sort_names(names)


def _clips(p: str | Path) -> int:
    return len(S.clips_in(p))


def _short(name: str, n: int = LABEL_MAX) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def _crumb(cwd: Path, root: Path) -> str:
    try:
        parts = list(cwd.relative_to(root).parts)
    except ValueError:
        parts = [cwd.name]
    if len(parts) > 3:
        parts = ["…"] + parts[-3:]          # đường dài: chỉ giữ 3 cấp cuối cho gọn
    return " › ".join([root.name or str(root)] + parts)


def _rows(buttons: list, cols: int = COLS) -> list[list]:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


# ───────────────────────── bước 1: thư mục ─────────────────────────
def folder_view(st: dict, cfg: Config, note: str = "") -> Result:
    cwd, root = Path(st["cwd"]), Path(cfg.footage_root)
    q = st.get("q", "")
    everything = list_dirs(cwd)
    items = list_dirs(cwd, q) if q else everything
    st["items"] = items
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    st["page"] = min(max(st["page"], 0), pages - 1)
    chunk = items[st["page"] * PAGE:(st["page"] + 1) * PAGE]
    n_here = _clips(cwd)
    lines = ([note] if note else []) + ["🎬 GIAO VIỆC MỚI · bước 1/4: chọn thư mục footage", f"📍 {_crumb(cwd, root)}"]
    if n_here:
        lines.append(f"🎞 Thư mục này có {n_here} video. Bấm \"Chọn thư mục này\" nếu đúng.")
    if q:
        lines.append(f"🔎 Đang lọc \"{q}\": {len(items)}/{len(everything)} thư mục")
    elif everything:
        lines.append(f"📁 {len(everything)} thư mục con" + (f" · trang {st['page'] + 1}/{pages}" if pages > 1 else "") + " · 🎞 = có video ngay trong đó")
    else:
        lines.append("(không có thư mục con)" if not n_here else "")
    buttons = []
    for i, name in enumerate(chunk):
        n = _clips(cwd / name)
        label = f"🎞 {_short(name, LABEL_MAX - 5)} ({n})" if n else f"📁 {_short(name)}"
        buttons.append((label, wb("d", str(st["page"] * PAGE + i))))
    kb: list = _rows(buttons)
    nav = []
    if cwd != root:
        nav.append(("⬆️ Lên", wb("u")))
    if st["page"] > 0:
        nav.append(("◀ Trước", wb("pg", str(st["page"] - 1))))
    if st["page"] < pages - 1:
        nav.append((f"Sau ▶ {st['page'] + 1}/{pages}", wb("pg", str(st["page"] + 1))))
    if nav:
        kb.append(nav)
    if n_here:
        kb.append([(f"✅ Chọn thư mục này ({n_here} video)", wb("s"))])
    if cwd == root and not q:
        for i, r in enumerate(st.get("recent", [])):
            kb.append([(f"🕘 {_short(Path(r).name, 40)}", wb("r", str(i)))])
    tools = []
    if q:
        tools.append(("✖ Bỏ lọc", wb("fx")))
    elif len(everything) > PAGE:
        tools.append(("🔎 Tìm theo tên", wb("fs")))     # danh sách dài: gõ vài chữ thay vì lật trang
    tools += [("✏️ Dán đường dẫn", wb("tp")), ("❌ Hủy", wb("x"))]
    kb.append(tools)
    return Result("\n".join(x for x in lines if x), kb)


def _pick_folder(st: dict, cfg: Config, folder: str, types: dict) -> Result:
    p = Path(folder)
    st["stage"] = "folder"      # thất bại thì về màn hình chọn thư mục (các nút ở đó mới còn hiệu lực)
    if not p.is_dir() or not _under(p, Path(cfg.footage_root)):
        return folder_view(st, cfg, f"⚠ Không dùng được thư mục này (phải nằm trong {cfg.footage_root}).")
    n = _clips(p)
    if not n:
        return folder_view(st, cfg, f"⚠ Thư mục \"{p.name}\" không có clip video ở cấp trên cùng. Chọn thư mục chứa trực tiếp các clip.")
    st["folder"], st["stage"] = str(p), "type"
    st["again"] = last_choice(st, cfg, types, str(p))
    return type_view(st, types)


# ───────────────────────── bước 2: loại video, bước 3: thương hiệu ─────────────────────────
def _project_of(folder: str, root: Path) -> str:
    """Tên thư mục cấp một dưới Xưởng Video (= dự án, ví dụ "KOC Nhật Hải"); "" nếu không nằm trong thư mục gốc."""
    try:
        rel = Path(os.path.normcase(str(folder))).relative_to(Path(os.path.normcase(str(root))))
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""


def last_choice(st: dict, cfg: Config, types: dict, folder: str) -> tuple[str, str] | None:
    """(loại video, thương hiệu) của việc gần nhất cùng dự án với thư mục này; không có thì của việc gần nhất bất kỳ. Chỉ nhận lựa chọn
    còn hợp lệ (loại và thương hiệu vẫn tồn tại). None = chưa có lần trước."""
    from .nodes import brand_kits

    root, kits = Path(cfg.footage_root), set(brand_kits(cfg))
    proj = _project_of(folder, root)
    ok = [j for j in st.get("past", []) if j.get("video_type") in types and j.get("brand") in kits]
    same = [j for j in ok if proj and _project_of(j.get("footage_dir", ""), root) == proj]
    pick = (same or ok or [None])[0]
    return (pick["video_type"], pick["brand"]) if pick else None


def type_view(st: dict, types: dict) -> Result:
    ids = sorted(types)
    st["types"] = ids
    kb = []
    again = st.get("again")
    if again and again[0] in types:
        kb.append([(f"⚡ Như lần trước: {types[again[0]].name_vi} · {again[1]}"[:60], wb("pv"))])
    kb += [[(types[i].name_vi[:40], wb("y", str(n)))] for n, i in enumerate(ids)]
    kb.append([("↩️ Chọn lại thư mục", wb("bk")), ("❌ Hủy", wb("x"))])
    return Result(f"Thư mục: {Path(st['folder']).name} ({_clips(st['folder'])} video)\n\nBước 2/4: chọn loại video", kb)


def brand_view(st: dict, cfg: Config) -> Result:
    from .nodes import brand_kits

    kits = brand_kits(cfg)
    st["brands"] = kits
    kb = []
    for n, b in enumerate(kits):
        warn = " ⚠" if phieu.brand_gaps(phieu.load_brand(cfg, b)) else ""
        kb.append([(f"{b}{warn}", wb("b", str(n)))])
    kb.append([("↩️ Chọn lại loại video", wb("bk")), ("❌ Hủy", wb("x"))])
    note = "\n⚠ = bộ thương hiệu còn thiếu logo/tagline/chữ cấm (vẫn dựng được)." if any(" ⚠" in lbl for row in kb for lbl, _ in row) else ""
    return Result(f"Bước 3/4: chọn thương hiệu{note}", kb)


# ───────────────────────── bước 4: BẢNG PHIẾU điền ngay trong chat ─────────────────────────
# Một tin nhắn duy nhất hiện mọi ô của phiếu (✅ đã điền, ⬜ chưa); bấm một ô để điền/sửa, bảng cập nhật tại chỗ. Gõ cả nhiều dòng
# "Nhãn: giá trị" một lần cũng được (như phiếu cũ). Ô bắt buộc (*) đứng trước; ô tùy chọn gập lại cho gọn.
def _vt(st: dict, types: dict) -> S.VideoType:
    return types[st["vtype"]]


def _fields(st: dict, types: dict) -> dict:
    return {f.key: f for f in phieu.fields_for(_vt(st, types))}


def _preview(v: str, n: int = 44) -> str:
    v = " ".join(str(v).split())
    return v if len(v) <= n else v[:n - 1] + "…"


def _start_form(st: dict, cfg: Config, types: dict) -> Result:
    vt = _vt(st, types)
    pre = phieu.prefill(vt, phieu.load_brand(cfg, st["brand"]))
    st["values"] = {k: v for k, (v, _src) in pre.items() if v}
    fs = phieu.fields_for(vt)
    st["keys"] = [f.key for f in fs if f.req] + [f.key for f in fs if not f.req]
    st.update(stage="form", show_opt=False, edit="")
    return form_view(st, cfg, types)


def form_view(st: dict, cfg: Config, types: dict, note: str = "") -> Result:
    vt, fs = _vt(st, types), _fields(st, types)
    req = [k for k in st["keys"] if fs[k].req]
    opt = [k for k in st["keys"] if not fs[k].req]
    miss = phieu.missing(st["values"], vt)
    done = sum(1 for k in req if st["values"].get(k))
    try:
        found = projectassets.collect(cfg, st["folder"])
    except OSError:
        found = projectassets.Assets()
    lines = ([note, ""] if note else []) + [f"📝 PHIẾU BRIEF · {vt.name_vi} · {st['brand']}",
                                          f"📁 {Path(st['folder']).name} ({_clips(st['folder'])} video) · Tài nguyên project: "
                                          + (found.summary() if found.any() else "không có"),
                                          f"Bắt buộc: {done}/{len(req)}" + (" · ✅ đủ, có thể giao" if not miss else ""), ""]
    for k in req + [k for k in opt if st["values"].get(k)]:
        v = st["values"].get(k)
        lines.append(f"{'✅' if v else '⬜'} {fs[k].label}{' *' if fs[k].req else ''}: {_preview(v) if v else '—'}")
    gaps = phieu.brand_gaps(phieu.load_brand(cfg, st["brand"]))
    if found.logo:
        gaps = [g for g in gaps if not g.startswith("logo")]       # logo của project bù cho thương hiệu chưa có logo
    if gaps:
        lines.append("\n⚠ Bộ thương hiệu còn thiếu: " + "; ".join(gaps))
    lines.append("\nBấm một ô để điền hoặc sửa. Hoặc dán nhiều dòng \"Nhãn: giá trị\" một lần. Giá, ưu đãi, lợi ích chỉ lấy đúng những gì bạn ghi.")

    def btn(k: str):
        f = fs[k]
        return (f"{'✅' if st['values'].get(k) else '⬜'} {_short(f.label, LABEL_MAX - 4)}{' *' if f.req else ''}", wb("f", str(st["keys"].index(k))))

    kb = _rows([btn(k) for k in req])
    if st.get("show_opt"):
        kb += _rows([btn(k) for k in opt])
        kb.append([("➖ Ẩn mục tùy chọn", wb("o"))])
    elif opt:
        kb.append([(f"➕ Mục tùy chọn ({sum(1 for k in opt if st['values'].get(k))}/{len(opt)} đã điền)", wb("o"))])
    kb.append([("🚀 Giao việc ngay" if not miss else f"🚀 Giao việc (còn thiếu {len(miss)})", wb("go"))])
    kb.append([("↩️ Đổi loại/thương hiệu", wb("bk")), ("🔁 Làm lại", wb("re")), ("❌ Hủy", wb("x"))])
    return Result("\n".join(lines), kb, panel=True)


def edit_view(st: dict, types: dict, note: str = "") -> Result:
    k = st["edit"]
    f = _fields(st, types)[k]
    cur = st["values"].get(k)
    text = (([note, ""] if note else []) + [f"✏️ {f.label}{' *' if f.req else ''}"] + ([f"({f.hint})"] if f.hint else [])
            + ([f"Hiện tại: {cur}"] if cur else []) + ["", "Gõ giá trị rồi gửi" + (", hoặc bấm một lựa chọn:" if QUICK.get(k) else ":")])
    kb = _rows([(q, wb("q", str(n))) for n, q in enumerate(QUICK.get(k, []))])
    if cur and not f.req:
        kb.append([("🗑 Xóa giá trị", wb("cl"))])
    kb.append([("↩️ Quay lại phiếu", wb("fb"))])
    return Result("\n".join(text), kb, panel=True)


def _bad_value(key: str, value: str) -> str:
    if key == "price":
        show, price = phieu.parse_price(value)
        if show is None:
            return "Ghi 'Không' hoặc 'Có, giá <số tiền>' (ví dụ: Có, giá 199.000đ)."
        if show and not price:
            return "Có giá thì ghi rõ số tiền, ví dụ: Có, giá 199.000đ."
    return ""


def _apply(st: dict, cfg: Config, types: dict, value: str) -> Result:
    """Gán giá trị cho ô đang sửa. Giá trị sai (vd. giá không rõ) thì ở lại màn hình sửa và nói rõ cần gì."""
    k = st["edit"]
    bad = _bad_value(k, value)
    if bad:
        return edit_view(st, types, "⚠ " + bad)
    label = _fields(st, types)[k].label
    st["values"][k] = value.strip()
    st.update(stage="form", edit="")
    return form_view(st, cfg, types, f"✅ Đã lưu: {label}")


# ───────────────────────── điều khiển ─────────────────────────
def on_callback(st: dict, cfg: Config, types: dict, code: str, arg: str) -> Result:
    stage = st.get("stage")
    if code == "x":
        return Result("Đã hủy, chưa tạo job nào, chưa tốn tiền.", end=True)
    if code == "re":
        st.update(new_state(cfg, st.get("recent"), st.get("past")))
        return folder_view(st, cfg)
    if code == "bk":
        if stage == "type":
            st["stage"] = "folder"
            return folder_view(st, cfg)
        if stage in ("brand", "form", "edit"):
            st["stage"] = "type"
            return type_view(st, types)
        return Result(EXPIRED)
    if stage == "folder":
        if code == "d":
            try:
                name = st["items"][int(arg)]
            except (ValueError, IndexError):
                return Result(EXPIRED)
            st["cwd"], st["page"], st["q"] = str(Path(st["cwd"]) / name), 0, ""      # vào thư mục con: bỏ bộ lọc cũ
            return folder_view(st, cfg)
        if code == "u":
            parent = Path(st["cwd"]).parent
            st["cwd"], st["page"], st["q"] = (str(parent) if _under(parent, Path(cfg.footage_root)) else str(cfg.footage_root)), 0, ""
            return folder_view(st, cfg)
        if code == "fs":
            st["stage"] = "search"
            return Result("🔎 Gõ vài chữ của tên thư mục (có dấu hay không dấu đều được), ví dụ: nhat hai",
                          [[("↩️ Quay lại", wb("bk2")), ("❌ Hủy", wb("x"))]])
        if code == "fx":
            st["q"], st["page"] = "", 0
            return folder_view(st, cfg)
        if code == "pg":
            st["page"] = int(arg) if arg.isdigit() else 0
            return folder_view(st, cfg)
        if code == "s":
            return _pick_folder(st, cfg, st["cwd"], types)
        if code == "r":
            try:
                return _pick_folder(st, cfg, st["recent"][int(arg)], types)
            except (ValueError, IndexError):
                return Result(EXPIRED)
        if code == "tp":
            st["stage"] = "path"
            return Result("Gõ (hoặc dán) đường dẫn THƯ MỤC chứa các clip. Lấy đường dẫn: chuột phải vào thư mục > Copy as path.",
                          [[("↩️ Quay lại", wb("bk2")), ("❌ Hủy", wb("x"))]])
    if stage in ("path", "search") and code == "bk2":
        st["stage"] = "folder"
        return folder_view(st, cfg)
    if stage == "type" and code == "pv":
        again = st.get("again")
        if not again or again[0] not in types:
            return Result(EXPIRED)
        st["vtype"], st["brand"] = again
        return _start_form(st, cfg, types)
    if stage == "type" and code == "y":
        try:
            st["vtype"] = st["types"][int(arg)]
        except (ValueError, IndexError):
            return Result(EXPIRED)
        st["stage"] = "brand"
        return brand_view(st, cfg)
    if stage == "brand" and code == "b":
        try:
            st["brand"] = st["brands"][int(arg)]
        except (ValueError, IndexError):
            return Result(EXPIRED)
        return _start_form(st, cfg, types)
    if stage == "form":
        if code == "f":
            try:
                st["edit"] = st["keys"][int(arg)]
            except (ValueError, IndexError):
                return Result(EXPIRED)
            st["stage"] = "edit"
            return edit_view(st, types)
        if code in ("o", "fb2"):
            if code == "o":
                st["show_opt"] = not st.get("show_opt")
            return form_view(st, cfg, types)
        if code == "go":
            return _finish(st, cfg, types)
    if stage == "edit":
        if code == "q":
            try:
                return _apply(st, cfg, types, QUICK[st["edit"]][int(arg)])
            except (ValueError, IndexError, KeyError):
                return Result(EXPIRED)
        if code == "cl":
            st["values"].pop(st["edit"], None)
            st.update(stage="form", edit="")
            return form_view(st, cfg, types, "🗑 Đã xóa giá trị.")
        if code == "fb":
            st.update(stage="form", edit="")
            return form_view(st, cfg, types)
    return Result(EXPIRED)


def on_text(st: dict, cfg: Config, types: dict, text: str) -> Result | None:
    """Chữ người dùng gõ khi wizard đang chờ chữ (đường dẫn, tên thư mục, giá trị một ô, hoặc nhiều dòng của phiếu). None = không chờ chữ."""
    stage = st.get("stage")
    if stage == "path":
        return _pick_folder(st, cfg, text.strip().strip('"').strip(), types)
    if stage == "search":
        st["q"], st["page"], st["stage"] = text.strip()[:60], 0, "folder"
        r = folder_view(st, cfg)
        return Result(r.text if st["items"] else f"Không có thư mục nào khớp \"{st['q']}\".\n\n" + r.text, r.kb)
    if stage == "edit":
        return _apply(st, cfg, types, text)
    if stage == "form":
        vt, fs = _vt(st, types), _fields(st, types)
        got = phieu.parse_form(text, vt, skip_first=False)
        if not got:
            return form_view(st, cfg, types, "Chưa đọc được mục nào. Bấm một ô để điền, hoặc dán nhiều dòng dạng \"Nhãn: giá trị\".")
        bad = {k: _bad_value(k, v) for k, v in got.items() if _bad_value(k, v)}
        ok = {k: v for k, v in got.items() if k not in bad}
        st["values"].update(ok)
        if any(not fs[k].req for k in ok):
            st["show_opt"] = True
        msg = f"✅ Đã cập nhật {len(ok)} mục: " + ", ".join(fs[k].label for k in ok) if ok else ""
        if bad:
            msg += ("\n" if msg else "") + "\n".join(f"⚠ {fs[k].label}: {why}" for k, why in bad.items())
        return form_view(st, cfg, types, msg)
    return None


def _finish(st: dict, cfg: Config, types: dict) -> Result:
    from .nodes import check_request

    vt = _vt(st, types)
    miss = phieu.missing(st["values"], vt)
    if miss:
        return form_view(st, cfg, types, "⚠ Còn thiếu (chưa tạo job): " + "; ".join(miss))
    errs = check_request(cfg, types, st["folder"], st["vtype"], st["brand"])
    if errs:
        return Result("Chưa giao được việc (chưa tạo job, chưa tốn tiền):\n- " + "\n- ".join(errs),
                      [[("↩️ Quay lại phiếu", wb("fb2")), ("🔁 Làm lại", wb("re")), ("❌ Hủy", wb("x"))]])
    start = {"footage_dir": st["folder"], "video_type": st["vtype"], "brand": st["brand"],
             "brief_text": phieu.canonical_text(st["values"], vt),
             "brief_prefill": phieu.to_brief(st["values"], vt, st["brand"]).model_dump()}
    return Result("Đang giao việc...", start=start, end=True)
