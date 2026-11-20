"""Thêm dạng video mới bằng nút bấm trên Telegram (CHỈ quản trị viên): /themloai hoặc menu "Thêm dạng video".

Sáu bước: bắt đầu từ mẫu -> tên (+ mã) -> chế độ dựng -> độ dài -> ô bắt buộc (thêm nhiều ô riêng) -> ghi chú (thêm nhiều) -> xem lại -> ghi.
Kết quả là một file `video_types/<mã>.json` đúng khuôn các dạng có sẵn, rồi nạp thẳng vào danh sách đang chạy của bot (không cần khởi động lại).

Giống wizard.py: logic thuần (không dùng thư viện telegram) để test được, trạng thái một người nằm trong dict `st` (bot giữ trong user_data["tw"]),
mỗi bước trả về wizard.Result (chữ + nút). Quyền admin do bot kiểm mỗi lần bấm/gõ; ở đây chỉ lo nội dung.

An toàn:
- Chỉ ghi khi người dùng bấm "Thêm vào hệ thống" ở màn xem lại, và kiểm lại toàn bộ lần cuối (mã, tên, chế độ, ô) trước khi ghi.
- Ghi bằng chế độ "x" (chỉ tạo mới): không bao giờ đè file dạng video có sẵn, kể cả khi hai admin cùng thêm một mã.
- Ghi xong đọc lại bằng đúng bộ nạp của hệ thống; không nạp được thì xóa file vừa ghi và báo lỗi, không để lại file hỏng làm bot không khởi động được.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

from . import phieu
from . import schemas as S
from .wizard import Result, _rows, _short

TTL_S = 30 * 60
STEPS = 6
MAX_NAME, MAX_CODE, MAX_LABEL, MAX_NOTE, MAX_NOTES_TOTAL = 80, 40, 60, 200, 800
EXPIRED = "Phiên thêm dạng video đã hết hạn. Gõ /themloai để làm lại."
EXPIRED_BTN = "Nút này đã hết hạn (bạn đã sang bước khác)."

# ô có sẵn trong hệ thống (khóa là thuộc tính của BriefExtract; "consent" là ô riêng extra.consent)
STD = (("price_text", "Giá hiển thị"), ("offer_text", "Nội dung ưu đãi"), ("offer_deadline", "Hạn ưu đãi"),
       ("where_to_buy", "Nơi mua, CTA"), ("benefits_allowed", "Lợi ích được phép ghi"), ("consent", "Người nói đã đồng ý xuất hiện"))
STD_KEYS = {k for k, _ in STD if k != "consent"}
MODES = (("B", "Người nói, KOC, booth"), ("C", "Hội chợ, gian hàng toàn cảnh"), ("A", "Thuyết minh trên clip, ảnh"),
         ("S", "Dựng từ slide"), ("auto", "AI đề xuất, người giao xác nhận"))
SECS = (15, 20, 30, 45, 60, 90, 120)
ORDER = ["name", "code", "mode", "secs", "fields", "note"]
TEXT_STAGES = ("name", "codeEdit", "secs", "custom", "note")
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}   # tên file bị Windows cấm
_CODE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_BAD_LABEL = re.compile(r"[:()*\[\]|]")   # ":" và "(" làm phiếu tách nhãn khỏi gợi ý; "*" là dấu bắt buộc; "|" phá mã nút


def tb(code: str, arg: str = "") -> str:
    """callback_data của nút: t|<mã>|<đối số hoặc ->, tối đa 64 byte (mã dạng <= 40 ký tự nên luôn vừa)."""
    return "|".join(["t", code, arg or "-"])


def slug(s: str, sep: str = "-") -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", sep, s).strip(sep)


def new_state() -> dict:
    return {"stage": "tpl", "tpl": "", "name": "", "code": "", "mode": "", "secs": None, "fields": [], "custom": [], "keep": [], "notes": [],
            "back": False, "ts": time.time()}


def _stale(st: dict, now: float | None = None) -> bool:
    return (now if now is not None else time.time()) - st.get("ts", 0) > TTL_S


def mode_ready(cfg, mode: str) -> bool:
    """Chế độ này đã có lệnh dựng khai trong agents/edit/tools.json chưa (đọc thật, nên khi chế độ A/C/S có lệnh thì tự hiện "chạy được")."""
    try:
        acts = json.loads((Path(cfg.agents_dir) / "edit" / "tools.json").read_text(encoding="utf-8")).get("actions", {}).get("build", {})
    except (OSError, ValueError):
        return False
    if mode == "auto":
        return any(bool(acts.get(m)) for m in S.MODES)
    return bool(acts.get(mode))


# ───────────────────────── kiểm tra giá trị ─────────────────────────
def suggest_code(name: str) -> str:
    c = re.sub(r"^video-", "", slug(name))[:MAX_CODE].strip("-")
    return c or "dang-moi"


def check_code(code: str, cfg, types: dict) -> str:
    """'' nếu dùng được; không thì lý do (tiếng Việt, nói cách sửa)."""
    if not _CODE.match(code):
        return "Mã chỉ gồm chữ thường không dấu, số và gạch ngang (không dấu cách), ví dụ so-sanh-doi-thu."
    if len(code) > MAX_CODE:
        return f"Mã dài quá {MAX_CODE} ký tự."
    if code in RESERVED:
        return f"Mã {code} là tên Windows không cho đặt làm tên file. Đặt mã khác."
    if code in types or (Path(cfg.video_types_dir) / f"{code}.json").exists():
        return f"Mã {code} đã có (video_types/{code}.json). Đặt mã khác."
    return ""


def check_name(name: str) -> str:
    if len(name) < 3:
        return "Tên quá ngắn, ghi ít nhất 3 ký tự."
    if len(name) > MAX_NAME:
        return f"Tên dài quá {MAX_NAME} ký tự."
    return ""


def _label_key(label: str) -> str:
    return "extra." + slug(label, "_")


def check_label(st: dict, label: str) -> str:
    """Tên ô riêng: người giao sẽ thấy nguyên văn trong phiếu brief."""
    if len(label) < 3:
        return "quá ngắn (ít nhất 3 ký tự)"
    if len(label) > MAX_LABEL:
        return f"dài quá {MAX_LABEL} ký tự"
    if _BAD_LABEL.search(label):
        return "không được chứa : ( ) [ ] * |  (phiếu dùng các dấu này để tách gợi ý)"
    key = _label_key(label)
    if len(key) < len("extra.") + 2:
        return "cần có chữ hoặc số"
    if key == "extra.consent":
        return "trùng ô \"người nói đã đồng ý xuất hiện\" có sẵn (bật ô đó ở danh sách trên)"
    if any(k == key or phieu.norm(l) == phieu.norm(label) for k, l in st["custom"]):
        return "đã có trong danh sách"
    taken = {phieu.norm(f.label) for f in phieu.BASE} | {phieu.norm(phieu.CONSENT.label)} | {phieu.norm(l) for _, l in STD}
    if phieu.norm(label) in taken:
        return "trùng với một ô có sẵn của phiếu (hãy bật ô đó ở danh sách trên)"
    return ""


def _fields_list(st: dict) -> list[tuple[str, str]]:
    """Các ô có sẵn mà dạng này cho chọn: chế độ B/auto luôn có ô 'đồng ý xuất hiện' (phieu.fields_for tự thêm) nên không cần bật."""
    return [(k, lab) for k, lab in STD if not (k == "consent" and st["mode"] in ("B", "auto"))]


# ───────────────────────── kết quả cuối ─────────────────────────
def build_dict(st: dict) -> dict:
    sel = [k for k, _ in STD if k in st["fields"]]
    rb: list[str] = []
    labels: dict[str, str] = {}
    for k in sel:
        if k == "consent":
            rb.append("extra.consent")
            labels["extra.consent"] = phieu.CONSENT.label
        else:
            rb.append(k)
    rb += [k for k in st["keep"] if k not in rb]
    for k, lab in st["custom"]:
        if k not in rb:
            rb.append(k)
        labels[k] = lab
    d: dict = {"id": st["code"], "name_vi": st["name"], "suggested_mode": st["mode"]}
    if st["secs"]:
        d["target_seconds"] = st["secs"]
    d["required_brief"] = rb
    d["labels"] = labels
    if st["notes"]:
        d["notes"] = " ".join(st["notes"])
    d["status"] = "nhap"
    return d


def validate_all(st: dict, cfg, types: dict) -> list[str]:
    errs = []
    if check_name(st["name"]):
        errs.append(check_name(st["name"]))
    if check_code(st["code"], cfg, types):
        errs.append(check_code(st["code"], cfg, types))
    if st["mode"] not in [m for m, _ in MODES]:
        errs.append("Chưa chọn chế độ dựng.")
    keys = [k for k, _ in st["custom"]]
    if len(set(keys)) != len(keys):
        errs.append("Có hai ô riêng trùng khóa.")
    if sum(len(n) for n in st["notes"]) > MAX_NOTES_TOTAL:
        errs.append(f"Ghi chú dài quá {MAX_NOTES_TOTAL} ký tự tổng cộng.")
    if not errs:
        try:
            S.VideoType.model_validate(build_dict(st))
        except Exception as e:  # noqa: BLE001 - lỗi schema nào cũng phải báo, không ghi file
            errs.append(f"Cấu hình không hợp lệ: {str(e)[:200]}")
    return errs


def commit(st: dict, cfg, types: dict) -> Result:
    errs = validate_all(st, cfg, types)
    if errs:
        return Result("⚠ Chưa ghi được:\n- " + "\n- ".join(errs), [[("↩️ Quay lại xem lại", tb("edit"))], [("❌ Hủy", tb("x"))]])
    d = build_dict(st)
    path = Path(cfg.video_types_dir) / f"{d['id']}.json"
    try:
        with open(path, "x", encoding="utf-8") as f:   # "x": chỉ tạo mới, không bao giờ đè file có sẵn
            f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        return Result(f"⚠ Vừa có người thêm mã {d['id']} trước bạn. Chưa ghi gì. Bấm Sửa lại một mục, chọn Tên và mã để đổi mã khác.",
                      [[("✏️ Sửa lại một mục", tb("edit"))], [("❌ Hủy", tb("x"))]])
    except OSError as e:
        return Result(f"⚠ Không ghi được file (chưa thêm gì): {e.__class__.__name__}: {str(e)[:150]}", [[("❌ Hủy", tb("x"))]])
    try:
        vt = S.VideoType.model_validate_json(path.read_text(encoding="utf-8"))
        if vt.id != path.stem:
            raise ValueError("id khác tên file")
    except Exception as e:  # noqa: BLE001 - file vừa ghi mà không nạp lại được: xóa ngay, không để làm hỏng lần khởi động sau
        try:
            path.unlink()
        except OSError:
            pass
        return Result(f"⚠ File vừa ghi không nạp lại được nên đã xóa (chưa thêm gì): {str(e)[:150]}", [[("❌ Hủy", tb("x"))]])
    types[vt.id] = vt   # nạp thẳng vào danh sách đang chạy: /loai, /video dùng được ngay
    warn = ""
    if vt.suggested_mode != "auto" and not mode_ready(cfg, vt.suggested_mode):
        warn = f"\n⚠ Chế độ {vt.suggested_mode} chưa có lệnh dựng trong hệ thống: dạng này đã lưu nhưng chưa dựng được cho tới khi có lệnh."
    text = (f"✅ Đã thêm dạng {vt.id}.\nĐã ghi video_types/{vt.id}.json:\n\n{json.dumps(d, ensure_ascii=False, indent=2)}\n\n"
            f"Người giao dùng được ngay (không cần khởi động lại bot): /video rồi chọn dạng này, hoặc /phieu để xem thử phiếu brief.\n"
            f"Gõ /loai để xem danh sách.{warn}")
    return Result(text, [[("➕ Thêm dạng khác", "m|addtype|-")]], end=True)


# ───────────────────────── các màn hình ─────────────────────────
def _head(n: int, title: str) -> str:
    return f"➕ THÊM DẠNG VIDEO · bước {n}/{STEPS}\n{title}"


def _cancel_row() -> list:
    return [("❌ Hủy", tb("x"))]


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join("· " + i for i in items) if items else empty


def start(types: dict) -> tuple[dict, Result]:
    st = new_state()
    return st, tpl_view(types)


def tpl_view(types: dict) -> Result:
    kb = [[("📄 Bắt đầu từ trống", tb("tpl"))]] + _rows([(_short("↳ " + i), tb("tpl", i)) for i in sorted(types)]) + [_cancel_row()]
    return Result(_head(1, "Bắt đầu từ đâu?\nLấy cấu hình của một dạng có sẵn làm nền cho nhanh (mã và tên vẫn phải đặt mới). Gõ /huybo bất cứ lúc nào để bỏ."), kb)


def name_view(st: dict, note: str = "") -> Result:
    st["stage"] = "name"
    return Result(note + _head(2, "Tên dạng video\nGõ tên bằng tiếng Việt, viết như người giao sẽ đọc. Ví dụ: Video so sánh với đối thủ"), [_cancel_row()])


def code_view(st: dict, cfg, types: dict) -> Result:
    err = check_code(st["code"], cfg, types)
    if err:   # mã gợi ý không dùng được (trùng...): bắt đổi ngay thay vì hiện nút "Dùng mã này"
        return code_edit_view(st, f"⚠ Mã gợi ý {st['code']}: {err}\n\n")
    st["stage"] = "code"
    return Result(_head(2, f"Mã dạng (tên file) sẽ là: {st['code']}\nNgười giao chọn dạng này bằng mã đó khi giao việc."),
                  [[("✅ Dùng mã này", tb("cok")), ("✏️ Đổi mã", tb("cedit"))], _cancel_row()])


def code_edit_view(st: dict, note: str = "") -> Result:
    st["stage"] = "codeEdit"
    return Result(note + "Gõ mã mới: chữ thường không dấu, số, gạch ngang. Ví dụ: so-sanh-doi-thu", [_cancel_row()])


def mode_view(st: dict, cfg) -> Result:
    st["stage"] = "mode"
    kb = []
    for m, desc in MODES:
        mark = "tùy chế độ AI chọn" if m == "auto" else ("✅ chạy được" if mode_ready(cfg, m) else "chưa có lệnh")
        kb.append([(_short(f"{m} · {desc} · {mark}", 48), tb("m", m))])
    return Result(_head(3, "Chế độ dựng đề xuất\nQuyết định dạng này dùng cách dựng nào. Người giao vẫn đổi được ở bước duyệt 1.\n"
                           "\"Chưa có lệnh\" = lưu được nhưng chưa dựng được cho tới khi hệ thống có lệnh cho chế độ đó."), kb + [_cancel_row()])


def secs_view(st: dict) -> Result:
    st["stage"] = "secs"
    btns = [(str(n), tb("s", str(n))) for n in SECS]
    kb = [btns[:4], btns[4:], [("Không đặt", tb("s", "0"))], _cancel_row()]
    return Result(_head(4, "Độ dài gợi ý (giây)\nChỉ là gợi ý cho người giao và AI. Bấm một nút hoặc gõ số giây (5 đến 600)."), kb)


def fields_view(st: dict, note: str = "") -> Result:
    st["stage"] = "fields"
    sel = set(st["fields"])
    kb = _rows([(("✅ " if k in sel else "⬜ ") + lab, tb("f", k)) for k, lab in _fields_list(st)])
    for i, (_k, lab) in enumerate(st["custom"]):
        kb.append([(_short("✅ " + lab + " (bấm để bỏ)", 44), tb("cd", str(i)))])
    kb.append([("➕ Thêm ô riêng", tb("cadd")), ("✔️ Xong", tb("fdone"))])
    kb.append(_cancel_row())
    extra = "\nChế độ này luôn có ô \"người nói đã đồng ý xuất hiện\", không cần bật." if st["mode"] in ("B", "auto") else ""
    return Result(note + _head(5, "Ô bắt buộc người giao phải điền\nLuôn có sẵn: thương hiệu, sản phẩm, mục tiêu. Bấm để bật hoặc tắt các ô dưới đây, "
                                  "và thêm bao nhiêu ô riêng tùy ý." + extra), kb)


def custom_view(st: dict, added: str = "") -> Result:
    st["stage"] = "custom"
    names = [lab for _k, lab in st["custom"]]
    head = f"✅ Đã thêm: {added}\n\n" if added else "Thêm ô riêng: gõ tên ô như người giao sẽ thấy trong phiếu. Ví dụ: Điểm so sánh chính\n\n"
    text = head + "Ô riêng hiện có:\n" + _bullets(names, "· (chưa có ô riêng nào)") + \
        "\n\nGõ tiếp tên ô khác (nhiều ô một lần thì ngăn bằng dấu ;). Đủ rồi thì bấm Xong."
    kb = [[("✔️ Xong, quay lại danh sách ô", tb("cdone"))] + ([("↩️ Bỏ ô vừa thêm", tb("cundo"))] if names else []), _cancel_row()]
    return Result(text, kb)


def note_view(st: dict, added: bool = False) -> Result:
    st["stage"] = "note"
    n = st["notes"]
    if not n:
        return Result(_head(6, "Ghi chú cho người giao (tùy chọn, thêm được nhiều ghi chú)\nHiện ở cuối phiếu, ví dụ lưu ý quy tắc riêng của dạng này. Gõ từng ghi chú một."),
                      [[("⏭ Bỏ qua", tb("nskip"))], _cancel_row()])
    text = ("✅ Đã ghi.\n\n" if added else "") + "Ghi chú hiện có:\n" + _bullets(n, "") + "\n\nGõ tiếp ghi chú khác, đủ rồi thì bấm Xong."
    return Result(text, [[("✔️ Xong", tb("ndone")), ("↩️ Xóa ghi chú vừa nhập", tb("nundo"))], _cancel_row()])


def preview_view(st: dict, cfg) -> Result:
    st["stage"] = "preview"
    mdesc = dict(MODES).get(st["mode"], "")
    lab = [l for k, l in _fields_list(st) if k in st["fields"]] + [l for _k, l in st["custom"]]
    warn = ""
    if st["mode"] != "auto" and not mode_ready(cfg, st["mode"]):
        warn = f"\n⚠ Chế độ {st['mode']} chưa có lệnh dựng trong hệ thống: dạng này được lưu nhưng chưa dựng được cho tới khi có lệnh."
    consent = "\n· Người nói đã đồng ý xuất hiện (tự có ở chế độ này)" if st["mode"] in ("B", "auto") else ""
    text = (f"XEM LẠI TRƯỚC KHI THÊM\n\n{st['name']} · {st['code']}\nChế độ đề xuất: {st['mode']} ({mdesc})\n"
            f"Độ dài gợi ý: {str(st['secs']) + ' giây' if st['secs'] else 'không đặt'}\nTrạng thái: nháp{warn}\n\n"
            f"Người giao phải điền (*):\n· Thương hiệu, sản phẩm, mục tiêu{consent}\n" + _bullets(lab, "") + ("\n" if lab else "") +
            "Ô riêng hiện ở mục D của phiếu brief.")
    if st["notes"]:
        text += "\n\nGhi chú cho người giao:\n" + _bullets(st["notes"], "")
    text += f"\n\nSẽ ghi file video_types/{st['code']}.json"
    kb = [[("✅ Thêm vào hệ thống", tb("add"))], [("✏️ Sửa lại một mục", tb("edit")), ("❌ Hủy", tb("x"))]]
    return Result(text, kb)


def edit_view(st: dict) -> Result:
    st["stage"] = "edit"
    kb = [[("Tên và mã", tb("e", "name")), ("Chế độ", tb("e", "mode"))], [("Độ dài", tb("e", "secs")), ("Ô bắt buộc", tb("e", "fields"))],
          [("Ghi chú", tb("e", "note")), ("↩️ Quay lại xem lại", tb("e", "back"))]]
    return Result("Sửa mục nào?", kb)


def _ask(st: dict, stage: str, cfg, types: dict) -> Result:
    return {"name": lambda: name_view(st), "code": lambda: code_view(st, cfg, types), "mode": lambda: mode_view(st, cfg), "secs": lambda: secs_view(st),
            "fields": lambda: fields_view(st), "note": lambda: note_view(st), "preview": lambda: preview_view(st, cfg), "edit": lambda: edit_view(st)}[stage]()


def _after(st: dict, cur: str, cfg, types: dict) -> Result:
    """Bước kế tiếp. Đang sửa một mục từ màn xem lại thì xong mục là về xem lại (riêng sửa tên thì phải qua bước mã)."""
    if st["back"] and cur != "name":
        st["back"] = False
        return preview_view(st, cfg)
    return _ask(st, ORDER[ORDER.index(cur) + 1] if cur != "note" else "preview", cfg, types)


# ───────────────────────── nút và chữ ─────────────────────────
_STAGES = {"tpl": ("tpl",), "cok": ("code",), "cedit": ("code",), "m": ("mode",), "s": ("secs",), "f": ("fields",), "cd": ("fields",),
           "cadd": ("fields",), "fdone": ("fields",), "cdone": ("custom",), "cundo": ("custom",), "nskip": ("note",), "ndone": ("note",),
           "nundo": ("note",), "add": ("preview",), "edit": ("preview",), "e": ("edit",)}


def _apply_template(st: dict, vt: S.VideoType) -> None:
    st["tpl"], st["mode"], st["secs"] = vt.id, vt.suggested_mode, vt.target_seconds
    fields, custom, keep = [], [], []
    for k in vt.required_brief:
        if k == "extra.consent":
            fields.append("consent")
        elif k in STD_KEYS:
            fields.append(k)
        elif k.startswith("extra."):
            custom.append([k, vt.labels.get(k) or k[len("extra."):].replace("_", " ")])
        else:
            keep.append(k)   # khóa lạ có sẵn trong dạng mẫu: giữ nguyên, không cho sửa ở đây
    st["fields"], st["custom"], st["keep"] = fields, custom, keep


def on_callback(st: dict, cfg, types: dict, code: str, arg: str) -> Result:
    if _stale(st):
        return Result(EXPIRED, end=True)
    st["ts"] = time.time()
    if code == "x":
        return Result("Đã hủy, chưa ghi gì vào hệ thống.", end=True)
    if code not in _STAGES or st["stage"] not in _STAGES[code]:
        return Result(EXPIRED_BTN)
    if code == "tpl":
        if arg:
            vt = types.get(arg)
            if not vt:
                return Result(EXPIRED_BTN)
            _apply_template(st, vt)
        return name_view(st)
    if code == "cok":
        return _after(st, "code", cfg, types)
    if code == "cedit":
        return code_edit_view(st)
    if code == "m":
        if arg not in dict(MODES):
            return Result(EXPIRED_BTN)
        st["mode"] = arg
        return _after(st, "mode", cfg, types)
    if code == "s":
        if not arg.isdigit():
            return Result(EXPIRED_BTN)
        st["secs"] = int(arg) or None
        return _after(st, "secs", cfg, types)
    if code == "f":
        if arg not in dict(_fields_list(st)):
            return Result(EXPIRED_BTN)
        st["fields"] = [x for x in st["fields"] if x != arg] if arg in st["fields"] else st["fields"] + [arg]
        return fields_view(st)
    if code == "cd":
        if arg.isdigit() and int(arg) < len(st["custom"]):
            st["custom"].pop(int(arg))
        return fields_view(st)
    if code == "cadd":
        return custom_view(st)
    if code == "fdone":
        return _after(st, "fields", cfg, types)
    if code == "cdone":
        return fields_view(st)
    if code == "cundo":
        if st["custom"]:
            st["custom"].pop()
        return custom_view(st)
    if code in ("nskip", "ndone"):
        return _after(st, "note", cfg, types)
    if code == "nundo":
        if st["notes"]:
            st["notes"].pop()
        return note_view(st)
    if code == "edit":
        return edit_view(st)
    if code == "e":
        if arg == "back":
            return preview_view(st, cfg)
        if arg not in ORDER or arg == "code":
            return Result(EXPIRED_BTN)
        st["back"] = True
        return _ask(st, arg, cfg, types)
    return commit(st, cfg, types)   # code == "add"


def on_text(st: dict, cfg, types: dict, text: str) -> Result | None:
    """Chữ người dùng gõ. None = bước hiện tại không cần chữ (bot cho đi tiếp việc khác)."""
    if _stale(st):
        return Result(EXPIRED, end=True)
    stage, t = st["stage"], " ".join(text.split()) if st["stage"] != "note" else text.strip()
    if stage not in TEXT_STAGES or not t:
        return None
    st["ts"] = time.time()
    if stage == "name":
        err = check_name(t)
        if err:
            return Result("⚠ " + err, [_cancel_row()])
        st["name"], st["code"] = t, suggest_code(t)
        return code_view(st, cfg, types)
    if stage == "codeEdit":
        err = check_code(t, cfg, types)
        if err:
            return Result("⚠ " + err, [_cancel_row()])
        st["code"] = t
        return _after(st, "code", cfg, types)
    if stage == "secs":
        if not (t.isdigit() and 5 <= int(t) <= 600):
            return Result("⚠ Nhập số giây từ 5 đến 600, hoặc bấm một nút ở tin trên.", [_cancel_row()])
        st["secs"] = int(t)
        return _after(st, "secs", cfg, types)
    if stage == "custom":
        ok: list[str] = []
        bad: list[str] = []
        for raw in re.split(r"[;\n]+", text):
            lab = " ".join(raw.split())
            if not lab:
                continue
            err = check_label(st, lab) if lab not in ok else "đã có trong danh sách"
            if err:
                bad.append(f"“{lab}”: {err}")
            else:
                ok.append(lab)
                st["custom"].append([_label_key(lab), lab])   # thêm ngay để ô sau trong cùng tin được kiểm trùng với ô trước
        if not ok:
            return Result("⚠ Chưa thêm được:\n- " + "\n- ".join(bad[:4]), [[("✔️ Xong, quay lại danh sách ô", tb("cdone"))], _cancel_row()])
        r = custom_view(st, "; ".join(ok))
        if bad:
            r.text += "\n\nBỏ qua:\n- " + "\n- ".join(bad[:4])
        return r
    # note
    if len(t) > MAX_NOTE:
        return Result(f"⚠ Ghi chú dài quá {MAX_NOTE} ký tự, hãy tách thành hai ghi chú.", [_cancel_row()])
    if sum(len(n) for n in st["notes"]) + len(t) > MAX_NOTES_TOTAL:
        return Result(f"⚠ Tổng ghi chú đã gần {MAX_NOTES_TOTAL} ký tự, bấm Xong để dừng.", [[("✔️ Xong", tb("ndone"))]])
    st["notes"].append(t)
    return note_view(st, added=True)
