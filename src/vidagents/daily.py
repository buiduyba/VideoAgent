"""Hỏi hàng ngày "hôm nay bạn muốn dựng video nào?" (đến mọi người được phép chat với bot, đúng giờ đã đặt, mặc định 8:30 và 21:00).

Luồng: bot nhắn danh sách thư mục footage CHƯA dựng (dạng `<thư mục gốc>/<KOC>/<thư mục theo ngày>/<clip>`, mới nhất trước) -> người nhận bấm chọn
(mỗi thư mục chọn = một video) -> với mỗi thư mục: chọn giá/ưu đãi hôm nay -> xác nhận (ước tính giờ xong, chi phí, số liệu hôm nay) -> giao job.
Người nhận câu hỏi CHÍNH LÀ người giao việc, nên cũng là người duyệt (đúng quy tắc hiện có: chỉ người giao mới duyệt job của mình).

Giữ nguyên các nguyên tắc của hệ thống:
- Giá, ưu đãi, hạn ưu đãi KHÔNG bao giờ tự điền: mỗi lần giao người dùng phải chọn "dùng lại như lần trước" / "không ưu đãi" / "gõ mới"
  (dùng lại bị chặn nếu hạn ưu đãi đã qua). Phần còn lại của brief (sản phẩm, lợi ích, nơi mua...) là HỒ SƠ BRIEF theo KOC, bot lưu từ lần giao
  đầu bằng phiếu và dùng lại các lần sau.
- Hai cổng duyệt giữ nguyên, bot không tự đăng gì. "Giao việc" ở đây chỉ là tạo job như /video.
- Chưa có trần số job/chi phí mỗi ngày (sẽ đề xuất sau khi chạy thực tế): bot ghi nhận và hiện số liệu hôm nay trong màn xác nhận; đặt
  `daily.max_jobs_per_day` / `daily.budget_usd_per_day` trong config thì chặn thật.

Logic thuần (không dùng thư viện telegram), trạng thái mỗi người lưu trong SQLite (bền qua khởi động lại; nút trên tin cũ báo hết hạn).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

from . import phieu, wizard
from .config import Config

EXPIRED = "Lượt hỏi này đã cũ (bạn đã sang bước khác hoặc có lượt hỏi mới). Gõ /homnay để làm lại."
PER_PROJECT = 3          # mỗi KOC hiện tối đa chừng này thư mục chưa dựng (mới nhất trước)
LABEL_MAX = 46
ACTIVE = ("queued", "running", "waiting_info", "waiting_approval", "blocked")
_DL = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?")


# ───────────────────────── tiện ích ─────────────────────────
def norm_path(p: str | None) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def parse_hhmm(s: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s).strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return int(m[1]), int(m[2])


def slot_key(day: date, hhmm: str) -> str:
    return f"{day:%Y-%m-%d}@{hhmm}"


def due_slots(cfg: Config, now: datetime) -> list[tuple[str, str]]:
    """Các mốc hỏi đang đến hạn: hôm nay đúng thứ đã bật, đã qua giờ, chưa quá `daily_grace_min` phút (khởi động trễ thì hỏi bù, không hỏi quá muộn)."""
    if now.weekday() not in cfg.daily_days:
        return []
    out = []
    for hhmm in cfg.daily_times:
        t = parse_hhmm(hhmm)
        if not t:
            continue
        at = datetime.combine(now.date(), dtime(*t))
        if at <= now <= at + timedelta(minutes=cfg.daily_grace_min):
            out.append((slot_key(now.date(), f"{t[0]:02d}:{t[1]:02d}"), f"{t[0]:02d}:{t[1]:02d}"))
    return out


def greeting(hhmm: str) -> str:
    h = (parse_hhmm(hhmm) or (12, 0))[0]
    return "Chào buổi sáng" if h < 11 else "Chào buổi trưa" if h < 14 else "Chào buổi chiều" if h < 18 else "Chào buổi tối"


def deadline_passed(text: str | None, today: date) -> bool:
    """Hạn ưu đãi (chữ người dùng ghi, ví dụ "30/11" hoặc "đến 30/11/2026") đã qua chưa. Không đọc được ngày thì coi là CHƯA qua (không chặn oan)."""
    m = _DL.search(text or "")
    if not m:
        return False
    d, mo, y = int(m[1]), int(m[2]), m[3]
    try:
        if y:
            dt = date((int(y) + 2000) if len(y) == 2 else int(y), mo, d)
        else:
            dt = date(today.year, mo, d)
            if (today - dt).days > 200:    # ghi "05/01" mà hôm nay cuối tháng 12: ý là năm sau
                dt = date(today.year + 1, mo, d)
    except ValueError:
        return False
    return dt < today


def _rank(name: str, path: Path) -> float:
    d = wizard._date_of(name)
    if d:
        return datetime.combine(d, dtime()).timestamp()
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def project_key(project: str) -> str:
    """Khóa hồ sơ theo KOC: bỏ khác biệt hoa/thường và dạng dấu. Cần vì tên thư mục hiện nguyên bản ("KOC Nhật Hải") còn phiếu giao việc
    suy ra tên dự án đã hạ chữ thường trên Windows ("koc nhật hải"); NAS của máy khác còn có thể lưu dấu tiếng Việt ở dạng tách rời."""
    return unicodedata.normalize("NFC", project or "").casefold()


def project_name(folder: str, root: Path) -> str:
    """Tên KOC (thư mục cấp một dưới thư mục gốc) giữ nguyên chữ hoa/thường để hiện cho người dùng; "" nếu nằm ngoài thư mục gốc."""
    try:
        parts = Path(folder).relative_to(root).parts
    except ValueError:
        return ""
    return parts[0] if parts else ""


def _short(s: str, n: int = LABEL_MAX) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


# ───────────────────────── kho lưu ─────────────────────────
class DailyStore:
    """Hồ sơ brief theo KOC, các lượt hỏi đã gửi (không hỏi trùng) và phiên của từng người. Dùng chung tệp sổ job."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._c()) as c:
            c.execute("CREATE TABLE IF NOT EXISTS brief_profiles(project TEXT, vtype TEXT, brand TEXT, values_json TEXT, folder TEXT, updated_at TEXT, PRIMARY KEY(project, vtype, brand))")
            if "seq" not in {r[1] for r in c.execute("PRAGMA table_info(brief_profiles)")}:   # số thứ tự lưu: "mới nhất" theo thứ tự lưu, không phụ thuộc độ phân giải đồng hồ
                c.execute("ALTER TABLE brief_profiles ADD COLUMN seq INTEGER DEFAULT 0")
            c.execute("CREATE TABLE IF NOT EXISTS daily_asks(slot TEXT, user_id TEXT, sent_at TEXT, PRIMARY KEY(slot, user_id))")
            c.execute("CREATE TABLE IF NOT EXISTS daily_sessions(user_id TEXT PRIMARY KEY, gen INTEGER, state TEXT, updated_at TEXT)")
            c.commit()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # hồ sơ brief
    def save_profile(self, project: str, vtype: str, brand: str, values: dict, folder: str = "") -> None:
        if not project or not values:
            return
        project = project_key(project)
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._c()) as c:
            nxt = "(SELECT COALESCE(MAX(seq), 0) + 1 FROM brief_profiles)"
            c.execute("INSERT INTO brief_profiles(project,vtype,brand,values_json,folder,updated_at,seq) VALUES(?,?,?,?,?,?," + nxt + ") "
                      "ON CONFLICT(project,vtype,brand) DO UPDATE SET values_json=excluded.values_json, folder=excluded.folder, updated_at=excluded.updated_at, seq=" + nxt,
                      (project, vtype, brand, json.dumps(values, ensure_ascii=False), folder, now))
            c.commit()

    def profile(self, project: str) -> dict | None:
        """Hồ sơ dùng gần nhất của KOC này: {vtype, brand, values, folder, updated_at}."""
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM brief_profiles WHERE project=? ORDER BY seq DESC, updated_at DESC LIMIT 1", (project_key(project),)).fetchone()
        if not r:
            return None
        return {"vtype": r["vtype"], "brand": r["brand"], "values": json.loads(r["values_json"]), "folder": r["folder"], "updated_at": r["updated_at"]}

    # lượt hỏi
    def was_asked(self, slot: str, uid: str) -> bool:
        with closing(self._c()) as c:
            return c.execute("SELECT 1 FROM daily_asks WHERE slot=? AND user_id=?", (slot, str(uid))).fetchone() is not None

    def mark_asked(self, slot: str, uid: str) -> None:
        with closing(self._c()) as c:
            c.execute("INSERT OR IGNORE INTO daily_asks(slot,user_id,sent_at) VALUES(?,?,?)", (slot, str(uid), datetime.now().isoformat(timespec="seconds")))
            c.commit()

    # phiên
    def next_gen(self, uid: str) -> int:
        with closing(self._c()) as c:
            r = c.execute("SELECT gen FROM daily_sessions WHERE user_id=?", (str(uid),)).fetchone()
        return (r["gen"] if r else 0) + 1

    def save_session(self, uid: str, state: dict) -> None:
        state["saved_at"] = datetime.now().isoformat(timespec="seconds")
        with closing(self._c()) as c:
            c.execute("INSERT INTO daily_sessions(user_id,gen,state,updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET gen=excluded.gen, state=excluded.state, updated_at=excluded.updated_at",
                      (str(uid), int(state.get("gen", 0)), json.dumps(state, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
            c.commit()

    def load_session(self, uid: str) -> dict | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT state FROM daily_sessions WHERE user_id=?", (str(uid),)).fetchone()
        return json.loads(r["state"]) if r else None

    def sessions(self) -> list[tuple[str, dict]]:
        with closing(self._c()) as c:
            rows = c.execute("SELECT user_id, state FROM daily_sessions").fetchall()
        return [(r["user_id"], json.loads(r["state"])) for r in rows]


# ───────────────────────── kết quả một bước ─────────────────────────
@dataclass
class DResult:
    text: str
    kb: list = field(default_factory=list)
    end: bool = False                                   # phiên kết thúc (không còn gì để bấm)
    starts: list = field(default_factory=list)          # job cần giao: [{"args": <tham số runner.start>, "values", "project", "name", "folder", "item"}]
    failed: list = field(default_factory=list)          # [(mục, lý do)] không giao được
    firsts: list = field(default_factory=list)          # mục chưa có hồ sơ brief: bot gửi nút mở phiếu
    wizard: str | None = None                           # thư mục cần mở phiếu giao việc
    save: bool = True                                   # bot có cần lưu phiên lại không


class Daily:
    def __init__(self, cfg: Config, jobs, store: DailyStore, types: dict, meter=None, now: Callable[[], datetime] = datetime.now):
        self.cfg, self.jobs, self.store, self.types, self.meter, self.now = cfg, jobs, store, types, meter, now

    # ── nguồn dữ liệu ──
    def recipients(self) -> list[str]:
        """Mọi người được phép chat với bot (người giao việc và quản trị viên). Đọc danh sách MỖI LẦN: /themnguoi có hiệu lực ngay."""
        return list(dict.fromkeys(str(x) for x in list(self.cfg.allowed_user_ids) + list(self.cfg.admin_ids) if x))

    def candidates(self) -> tuple[list[dict], list[str]]:
        """(các thư mục chưa dựng, các dòng "đang có job" để người dùng biết). Chưa dựng = chưa có job nào (khác đã hủy) trên đúng thư mục đó."""
        root = Path(self.cfg.footage_root)
        rows = self.jobs.list(None, active_only=False)
        built = {norm_path(j.get("footage_dir")) for j in rows if j["status"] != "cancelled"}
        out: list[dict] = []
        for proj in wizard.list_dirs(root):
            pdir, items = root / proj, []
            subs = wizard.list_dirs(pdir)
            for name in subs:
                path = pdir / name
                n = wizard._clips(path)
                if n and norm_path(str(path)) not in built:
                    items.append({"project": proj, "folder": str(path), "name": name, "clips": n, "rank": _rank(name, path)})
            if not subs and wizard._clips(pdir) and norm_path(str(pdir)) not in built:   # clip nằm thẳng trong thư mục KOC
                items.append({"project": proj, "folder": str(pdir), "name": "(thư mục gốc)", "clips": wizard._clips(pdir), "rank": _rank(proj, pdir)})
            items.sort(key=lambda c: -c["rank"])
            out += items[:PER_PROJECT]
        out.sort(key=lambda c: -c["rank"])
        active = [f"{project_name(j['footage_dir'], root) or '?'} · {Path(j['footage_dir']).name} ({j.get('requester_name') or j.get('requester_id')})"
                  for j in rows if j["status"] in ACTIVE and j.get("footage_dir")][:5]
        return out[:max(1, self.cfg.daily_max_candidates)], active

    def open_line(self, uid: str) -> str:
        mine = self.jobs.list(str(uid), active_only=True)
        wait = sum(1 for j in mine if j["status"] in ("waiting_approval", "waiting_info"))
        stop = sum(1 for j in mine if j["status"] == "blocked")
        if not wait and not stop:
            return ""
        parts = ([f"{wait} chờ bạn duyệt/trả lời"] if wait else []) + ([f"{stop} bị dừng"] if stop else [])
        return f"Bạn đang có {wait + stop} việc cần xử lý ({', '.join(parts)}): gõ /trangthai."

    def today_stats(self) -> tuple[int, float]:
        today = self.now().strftime("%Y-%m-%d")
        ids = [j["job_id"] for j in self.jobs.list(None, active_only=False) if str(j.get("created_at", "")).startswith(today) and j["status"] != "cancelled"]
        usd = sum(self.meter.job_total(i)["usd"] for i in ids) if self.meter else 0.0
        return len(ids), usd

    def avg_cost(self) -> tuple[float | None, int]:
        if not self.meter:
            return None, 0
        done = [j for j in self.jobs.list(None, active_only=False) if j["status"] == "done"]
        done.sort(key=lambda j: j.get("updated_at", ""), reverse=True)
        vals = [t["usd"] for t in (self.meter.job_total(j["job_id"]) for j in done[:10]) if t["calls"]]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    def eta(self, n_new: int) -> tuple[int, int, datetime]:
        busy = sum(1 for j in self.jobs.list(None, active_only=True) if j["status"] in ("queued", "running"))
        minutes = (busy + n_new) * self.cfg.heavy_minutes_per_job / max(1, self.cfg.render_slots)
        return busy, round(minutes), self.now() + timedelta(minutes=minutes)

    # ── phiên ──
    def build_session(self, uid: str, slot: str, hhmm: str, cands: list[dict] | None = None, active: list[str] | None = None) -> dict:
        if cands is None:
            cands, active = self.candidates()
        return {"gen": self.store.next_gen(uid), "uid": str(uid), "slot": slot, "hhmm": hhmm, "stage": "pick", "cands": cands, "active": active or [],
                "sel": [], "items": [], "i": 0, "remind_at": None, "open": self.open_line(uid)}

    def manual_session(self, uid: str) -> dict:
        now = self.now()
        return self.build_session(uid, f"{now:%Y-%m-%d}@manual-{now:%H%M}", f"{now:%H:%M}")

    @staticmethod
    def _b(code: str, st: dict, arg: str = "") -> str:
        return f"d|{code}|{st['gen']}.{arg}"

    # ── màn hình ──
    def pick_view(self, st: dict, note: str = "") -> DResult:
        st["stage"] = "pick"
        lines = ([note, ""] if note else []) + [f"{greeting(st['hhmm'])}! Bạn muốn dựng video nào?"]
        if st.get("open"):
            lines.append(st["open"])
        kb = []
        if st["cands"]:
            lines += ["", "Thư mục footage chưa dựng (mới nhất trước). Bấm để chọn, mỗi thư mục chọn là một video:"]
            for i, c in enumerate(st["cands"]):
                mark = "✅" if i in st["sel"] else "⬜"
                kb.append([(_short(f"{mark} {c['project']} · {c['name']} · {c['clips']} clip"), self._b("t", st, str(i)))])
        else:
            lines += ["", f"Chưa thấy thư mục footage nào mới chưa dựng trong {self.cfg.footage_root}. Có footage mới thì bấm \"Thư mục khác\" để chọn tay."]
        if st.get("active"):
            lines += ["", "Đang có job trên các thư mục:"] + [f"· {a}" for a in st["active"]]
        kb.append([("➡️ Tiếp", self._b("go", st)), ("📂 Thư mục khác", self._b("oth", st))])
        kb.append([("🚫 Hôm nay không dựng", self._b("off", st)), ("⏰ Nhắc lại sau 1 giờ", self._b("snz", st))])
        return DResult("\n".join(lines), kb)

    def _items_with_profile(self, st: dict) -> list[int]:
        return [n for n, it in enumerate(st["items"]) if it["has_profile"] and not it["skip"]]

    def _values(self, it: dict) -> dict:
        """Giá trị phiếu cho hôm nay: hồ sơ brief + lựa chọn giá/ưu đãi. Tên video/đợt không mang sang (mỗi lần mỗi khác)."""
        v = {k: x for k, x in it["values"].items() if k != "name"}
        if it["price"] == "none":
            v.pop("offer", None)
            v.pop("deadline", None)
        elif it["price"] == "new":
            for k in ("price", "offer", "deadline"):
                v.pop(k, None)
            v.update(it.get("new", {}))
        return v

    def item_view(self, st: dict, note: str = "") -> DResult:
        st["stage"] = "item"
        todo = self._items_with_profile(st)
        it = st["items"][st["i"]]
        k = todo.index(st["i"]) + 1 if st["i"] in todo else 1
        vt = self.types[it["vtype"]]
        v = it["values"]
        price, offer, deadline = v.get("price", ""), v.get("offer", ""), v.get("deadline", "")
        expired = bool((offer or deadline) and deadline_passed(deadline, self.now().date()))
        lines = ([note, ""] if note else []) + [f"Video {k}/{len(todo)}: {it['project']} · {it['name']} ({it['clips']} clip)", f"Loại: {vt.name_vi} · Thương hiệu: {it['brand']}", "",
                 "Giá và ưu đãi lần trước (bot không tự điền cho hôm nay, bạn chọn):",
                 f"· Giá: {price or '(chưa có)'}", f"· Ưu đãi: {offer or 'không'}", f"· Hạn: {deadline or '-'}"]
        if expired:
            lines.append(f"⚠ Ưu đãi lần trước đã hết hạn ({deadline}), không dùng lại được.")
        kb = []
        if price and not expired:
            kb.append([("✅ Dùng lại như trên", self._b("pc", st, "same"))])
        if price:
            kb.append([("🏷 Không ưu đãi (giữ giá)", self._b("pc", st, "none"))])
        kb.append([("✏️ Gõ giá/ưu đãi mới", self._b("pc", st, "new"))])
        kb.append([("⏭ Bỏ video này", self._b("skip", st)), ("↩️ Chọn lại", self._b("bk", st))])
        return DResult("\n".join(lines), kb)

    def text_prompt(self, st: dict, note: str = "") -> DResult:
        st["stage"] = "text"
        it = st["items"][st["i"]]
        lines = ([note, ""] if note else []) + [f"✏️ {it['project']} · {it['name']}: gõ giá và ưu đãi HÔM NAY, mỗi dòng một mục (bỏ dòng nào không có):",
                 "Giá còn áp dụng: Có, giá 199.000đ", "Ưu đãi: giảm 10%", "Hạn ưu đãi: 30/11", "",
                 "Mục nào không ghi thì coi là KHÔNG có (không lấy lại của lần trước)."]
        return DResult("\n".join(lines), [[("↩️ Quay lại", self._b("pb", st)), ("⏭ Bỏ video này", self._b("skip", st))]])

    def confirm_view(self, st: dict, note: str = "") -> DResult:
        st["stage"] = "confirm"
        ready = [it for it in st["items"] if it["has_profile"] and not it["skip"] and it["price"]]
        firsts = [it for it in st["items"] if not it["has_profile"] and not it["skip"]]
        lines = ([note, ""] if note else []) + [f"Kế hoạch: {len(ready)} video"] if ready else ([note, ""] if note else []) + ["Chưa có video nào sẵn sàng giao."]
        for it in ready:
            v = self._values(it)
            lines.append(f"• {it['project']} · {it['name']}: {self.types[it['vtype']].name_vi}, {it['brand']}, giá {v.get('price', '?')}"
                         + (f", ưu đãi {v['offer']}" + (f" đến {v['deadline']}" if v.get("deadline") else "") if v.get("offer") else ", không ưu đãi"))
        for it in firsts:
            lines.append(f"• {it['project']} · {it['name']}: CHƯA có hồ sơ brief, bot sẽ mở phiếu điền lần đầu sau khi giao các video trên.")
        blocked = False
        if ready:
            busy, minutes, end = self.eta(len(ready))
            lines += ["", f"Ước tính dựng: đang có {busy} job chạy, thêm {len(ready)} video, khoảng {self.cfg.heavy_minutes_per_job:g} phút nặng mỗi video trên "
                          f"{max(1, self.cfg.render_slots)} suất render, nên xong khoảng {end:%H:%M} nếu không phải chờ ai duyệt.",
                      f"Việc của bạn: duyệt kịch bản {len(ready)} lần, rồi duyệt bản xem trước {len(ready)} lần. Bot không tự đăng gì."]
            avg, n = self.avg_cost()
            lines.append(f"Chi phí AI ước tính: khoảng ${avg * len(ready):.2f} (trung bình {n} job xong gần nhất)." if avg is not None else
                         "Chi phí AI ước tính: chưa đủ dữ liệu (chưa có job nào xong có ghi chi phí).")
            jobs_today, usd_today = self.today_stats()
            lines.append(f"Hôm nay bot đã nhận {jobs_today} job, AI ~${usd_today:.2f}. "
                         + ("Chưa đặt trần mỗi ngày (đang ghi nhận để đề xuất)." if self.cfg.daily_max_jobs is None and self.cfg.daily_budget_usd is None else
                            f"Trần: {self.cfg.daily_max_jobs if self.cfg.daily_max_jobs is not None else 'chưa đặt'} job/ngày, "
                            f"{('$%g' % self.cfg.daily_budget_usd) if self.cfg.daily_budget_usd is not None else 'chưa đặt'}/ngày."))
            if self.cfg.daily_max_jobs is not None and jobs_today + len(ready) > self.cfg.daily_max_jobs:
                blocked = True
                lines.append(f"⛔ Vượt trần {self.cfg.daily_max_jobs} job/ngày: bỏ bớt {jobs_today + len(ready) - self.cfg.daily_max_jobs} video rồi bấm lại.")
            if self.cfg.daily_budget_usd is not None and usd_today >= self.cfg.daily_budget_usd:
                blocked = True
                lines.append(f"⛔ Hôm nay đã tiêu ${usd_today:.2f}, chạm trần ${self.cfg.daily_budget_usd:g}/ngày.")
        kb = []
        if (ready or firsts) and not blocked:
            kb.append([("🚀 Bắt đầu dựng", self._b("st", st))])
        kb.append([("↩️ Sửa lại", self._b("bk", st)), ("❌ Hủy", self._b("x", st))])
        return DResult("\n".join(lines), kb)

    # ── điều khiển ──
    def _next(self, st: dict, note: str = "") -> DResult:
        todo = [n for n in self._items_with_profile(st) if st["items"][n]["price"] is None]
        if todo:
            st["i"] = todo[0]
            return self.item_view(st, note)
        return self.confirm_view(st, note)

    def on_callback(self, st: dict, code: str, arg: str) -> DResult:
        gen, _, rest = arg.partition(".")
        if gen != str(st.get("gen")):
            return DResult(EXPIRED, save=False)
        stage = st.get("stage")
        if code == "wz":   # mở phiếu cho thư mục chưa có hồ sơ: còn dùng được cả sau khi đã giao (stage done)
            try:
                it = st["items"][int(rest)]
            except (ValueError, IndexError):
                return DResult(EXPIRED, save=False)
            return DResult(f"Mở phiếu giao việc cho {it['project']} · {it['name']}.", wizard=it["folder"], save=False)
        if code == "x":
            st["stage"] = "off"
            return DResult("Đã hủy lượt hỏi này, chưa tạo job nào. Gõ /homnay khi cần làm lại.", end=True)
        if code == "off":
            st["stage"] = "off"
            return DResult("Đã ghi: lần này không dựng. Bot sẽ hỏi lại ở mốc kế tiếp.", end=True)
        if code == "snz":
            if stage != "pick":
                return DResult(EXPIRED, save=False)
            st["remind_at"] = (self.now() + timedelta(hours=1)).isoformat(timespec="seconds")
            return DResult(f"⏰ Bot sẽ nhắc lại lúc {self.now() + timedelta(hours=1):%H:%M}.")
        if code == "oth":
            return DResult("Mở phiếu giao việc để chọn thư mục khác.", wizard="", save=False)
        if code == "bk":
            return self.pick_view(st) if stage in ("item", "confirm", "text") else DResult(EXPIRED, save=False)
        if code == "t" and stage == "pick":
            try:
                i = int(rest)
                st["cands"][i]
            except (ValueError, IndexError):
                return DResult(EXPIRED, save=False)
            st["sel"] = [x for x in st["sel"] if x != i] if i in st["sel"] else sorted(st["sel"] + [i])
            return self.pick_view(st)
        if code == "go" and stage == "pick":
            if not st["sel"]:
                return self.pick_view(st, "⚠ Chưa chọn thư mục nào. Bấm vào thư mục cần dựng rồi bấm Tiếp.")
            return self._start_items(st)
        if code == "pc" and stage == "item":
            it = st["items"][st["i"]]
            if rest not in ("same", "none", "new"):
                return DResult(EXPIRED, save=False)
            if rest == "same" and (not it["values"].get("price") or deadline_passed(it["values"].get("deadline"), self.now().date()) and (it["values"].get("offer") or it["values"].get("deadline"))):
                return self.item_view(st, "⚠ Không dùng lại được (thiếu giá hoặc ưu đãi đã hết hạn). Chọn cách khác.")
            if rest == "none" and not it["values"].get("price"):
                return self.item_view(st, "⚠ Lần trước chưa có giá, hãy gõ giá mới.")
            it["price"] = rest
            if rest == "new":
                return self.text_prompt(st)
            return self._next(st)
        if code == "pb" and stage == "text":
            return self.item_view(st)
        if code == "skip" and stage in ("item", "text"):
            st["items"][st["i"]]["skip"] = True
            return self._next(st)
        if code == "st" and stage == "confirm":
            return self._finish(st)
        return DResult(EXPIRED, save=False)

    def _start_items(self, st: dict) -> DResult:
        from .nodes import brand_kits

        kits = set(brand_kits(self.cfg))
        items = []
        for idx in st["sel"]:
            c = st["cands"][idx]
            prof = self.store.profile(c["project"])
            it = {"cand": idx, "project": c["project"], "folder": c["folder"], "name": c["name"], "clips": c["clips"], "has_profile": False, "skip": False, "price": None, "values": {}}
            if prof and prof["vtype"] in self.types and prof["brand"] in kits:
                it.update(has_profile=True, vtype=prof["vtype"], brand=prof["brand"], values=prof["values"])
            items.append(it)
        st["items"], st["i"] = items, 0
        return self._next(st)

    def on_text(self, st: dict, text: str) -> DResult | None:
        """Chữ người dùng gõ: chỉ có nghĩa khi đang ở bước "Gõ giá/ưu đãi mới". None = không chờ chữ."""
        if st.get("stage") != "text":
            return None
        it = st["items"][st["i"]]
        vt = self.types[it["vtype"]]
        got = phieu.parse_form(text, vt, skip_first=False)
        got = {k: v.strip() for k, v in got.items() if k in ("price", "offer", "deadline") and v.strip()}
        if "price" not in got:
            return self.text_prompt(st, "⚠ Chưa đọc được dòng giá. Ghi theo mẫu dưới đây (mỗi mục một dòng, có dấu hai chấm).")
        show, price = phieu.parse_price(got["price"])
        if show is None or (show and not price):
            return self.text_prompt(st, "⚠ Giá chưa rõ: ghi 'Không' hoặc 'Có, giá <số tiền>', ví dụ: Có, giá 199.000đ.")
        if got.get("deadline") and deadline_passed(got["deadline"], self.now().date()):
            return self.text_prompt(st, f"⚠ Hạn ưu đãi {got['deadline']} đã qua. Ghi hạn còn hiệu lực, hoặc bỏ dòng ưu đãi và hạn nếu hôm nay không có.")
        if got.get("deadline") and not got.get("offer"):
            return self.text_prompt(st, "⚠ Có hạn ưu đãi thì phải ghi nội dung ưu đãi.")
        it["new"] = got
        return self._next(st)

    def _finish(self, st: dict) -> DResult:
        from .nodes import check_request

        starts, failed, firsts = [], [], []
        for n, it in enumerate(st["items"]):
            if it["skip"]:
                continue
            if not it["has_profile"]:
                firsts.append({**it, "idx": n})
                continue
            if not it["price"]:
                failed.append((it, "chưa chọn giá và ưu đãi"))
                continue
            vt, values = self.types[it["vtype"]], self._values(it)
            miss = phieu.missing(values, vt)
            if miss:
                failed.append((it, "phiếu còn thiếu: " + "; ".join(miss)))
                continue
            errs = check_request(self.cfg, self.types, it["folder"], it["vtype"], it["brand"])
            if errs:
                failed.append((it, "; ".join(errs)))
                continue
            starts.append({"item": n, "project": it["project"], "name": it["name"], "folder": it["folder"], "values": values, "vtype": it["vtype"], "brand": it["brand"],
                           "args": {"footage_dir": it["folder"], "video_type": it["vtype"], "brand": it["brand"], "brief_text": phieu.canonical_text(values, vt),
                                    "brief_prefill": phieu.to_brief(values, vt, it["brand"]).model_dump()}})
        st["stage"] = "done"
        return DResult("Đang giao việc...", end=True, starts=starts, failed=failed, firsts=firsts)

    def done_text(self, started: list[tuple[dict, str]], failed: list, firsts: list, notes: dict | None = None) -> str:
        notes = notes or {}
        lines = []
        if started:
            lines.append(f"✅ Đã giao {len(started)} job:")
            for s, jid in started:
                lines.append(f"· {jid} ({s['project']} · {s['name']})" + notes.get(jid, ""))
            lines.append("Bot sẽ nhắn khi cần bạn duyệt kịch bản. Gõ /trangthai để xem tiến độ.")
        if failed:
            lines += ["", "⚠ Chưa giao được (chưa tạo job, chưa tốn tiền):"] + [f"· {it['project']} · {it['name']}: {why}" for it, why in failed]
        if firsts:
            lines += ["", f"📝 {len(firsts)} thư mục chưa có hồ sơ brief: điền phiếu lần đầu ở các tin dưới đây (từ lần sau chỉ cần chọn giá và ưu đãi)."]
        return "\n".join(lines) or "Không có video nào được giao."

    def first_time_prompt(self, st: dict, it: dict) -> DResult:
        return DResult(f"📝 {it['project']} · {it['name']} ({it['clips']} clip): chưa có hồ sơ brief. Bấm để điền phiếu lần đầu.",
                       [[("📝 Điền phiếu", self._b("wz", st, str(it["idx"])))]], save=False)

    def cancel_text(self, uid: str) -> None:
        """Người dùng sang việc khác (mở phiếu giao việc...): bước đang chờ chữ giá/ưu đãi thôi nhận chữ, tránh nuốt nhầm tin nhắn kế tiếp."""
        st = self.store.load_session(uid)
        if st and st.get("stage") == "text":
            st["stage"] = "pick"
            self.store.save_session(uid, st)

    def text_fresh(self, st: dict, minutes: int = 30) -> bool:
        try:
            return (self.now() - datetime.fromisoformat(st.get("saved_at", ""))).total_seconds() < minutes * 60
        except ValueError:
            return False

    # ── chạy theo giờ ──
    def run_due(self, send: Callable[[str, str, list], None], now: datetime | None = None, log: Callable[[str], None] = print) -> int:
        """Gửi câu hỏi cho mọi người ở các mốc đến hạn (mỗi người mỗi mốc đúng MỘT lần, bền qua khởi động lại) và nhắc lại các lượt hẹn nhắc.
        Gửi lỗi cho một người thì chưa đánh dấu (vòng sau thử lại), không ảnh hưởng người khác. Trả về số tin đã gửi."""
        now = now or self.now()
        sent, cache = 0, None
        for slot, hhmm in due_slots(self.cfg, now):
            for uid in self.recipients():
                if self.store.was_asked(slot, uid):
                    continue
                try:
                    if cache is None:   # quét NAS một lần cho cả đợt (chậm), dùng chung cho mọi người
                        cache = self.candidates()
                    st = self.build_session(uid, slot, hhmm, *cache)
                    self.store.save_session(uid, st)
                    r = self.pick_view(st)
                    self.store.save_session(uid, st)
                    send(uid, r.text, r.kb)
                    self.store.mark_asked(slot, uid)
                    sent += 1
                except Exception as e:  # noqa: BLE001
                    log(f"[hỏi] gửi cho {uid} lỗi: {type(e).__name__}: {e}")
        stamp = now.isoformat(timespec="seconds")
        for uid, st in self.store.sessions():
            ra = st.get("remind_at")
            if st.get("stage") == "pick" and ra and ra <= stamp:
                try:
                    st["remind_at"] = None
                    r = self.pick_view(st, "⏰ Nhắc lại theo hẹn.")
                    self.store.save_session(uid, st)
                    send(uid, r.text, r.kb)
                    sent += 1
                except Exception as e:  # noqa: BLE001
                    log(f"[hỏi] nhắc {uid} lỗi: {type(e).__name__}: {e}")
        return sent


def start_daily_thread(daily: Daily, send: Callable[[str, str, list], None], every_s: float = 30.0, log: Callable[[str], None] = print) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                n = daily.run_due(send, log=log)
                if n:
                    log(f"[hỏi] đã gửi {n} lượt hỏi")
            except Exception as e:  # noqa: BLE001
                log(f"[hỏi] lỗi: {type(e).__name__}: {e}")
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="vidagents-daily", daemon=True)
    t.start()
    return t
