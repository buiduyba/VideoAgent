"""Sổ job (SQLite): ai giao, trạng thái gì. Trạng thái chi tiết từng bước nằm ở checkpointer của LangGraph."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

STATUSES = ("queued", "running", "waiting_info", "waiting_approval", "blocked", "done", "cancelled")
_COLS = ("requester_id", "requester_name", "brand", "video_type", "footage_dir", "status", "stage", "note", "updated_at")


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._c()) as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY, requester_id TEXT, requester_name TEXT, brand TEXT,
                video_type TEXT, footage_dir TEXT, status TEXT, stage TEXT, note TEXT,
                created_at TEXT, updated_at TEXT)"""
            )
            cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
            if "alerted_at" not in cols:  # sổ cũ chưa có cột báo-đứng: thêm vào, không mất dữ liệu
                c.execute("ALTER TABLE jobs ADD COLUMN alerted_at TEXT")
            c.commit()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _next_seq(c: sqlite3.Connection, prefix: str) -> int:
        """Số thứ tự kế tiếp = số lớn nhất đã dùng + 1 (không đếm số dòng: xóa một job giữa chừng thì đếm sẽ ra mã đã có).
        So tiền tố bằng substr thay vì LIKE: "_" và "%" trong LIKE khớp ký tự bất kỳ nên mã hai thương hiệu sẽ lẫn nhau."""
        n = 0
        for (jid,) in c.execute("SELECT job_id FROM jobs WHERE substr(job_id,1,?)=?", (len(prefix), prefix)):
            tail = jid[len(prefix):]
            if tail.isdigit():
                n = max(n, int(tail))
        return n + 1

    def next_id(self, brand: str) -> str:
        """Mã job kế tiếp để XEM TRƯỚC. Muốn tạo job thì dùng create_next: lấy mã rồi ghi qua hai lệnh rời nhau sẽ trùng khi có người giao cùng lúc."""
        prefix = f"{datetime.now().strftime('%Y%m%d')}-{brand}-"
        with closing(self._c()) as c:
            return f"{prefix}{self._next_seq(c, prefix):02d}"

    @staticmethod
    def _insert(c: sqlite3.Connection, job_id: str, **f) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        c.execute(
            "INSERT INTO jobs(job_id,requester_id,requester_name,brand,video_type,footage_dir,status,stage,note,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, str(f.get("requester_id", "")), f.get("requester_name", ""), f.get("brand", ""),
             f.get("video_type", ""), f.get("footage_dir", ""), f.get("status", "queued"),
             f.get("stage", "new"), f.get("note", ""), now, now),
        )

    def create(self, job_id: str, **f) -> None:
        """Tạo job với mã cho trước (mã trùng thì IntegrityError). Giao việc mới dùng create_next."""
        with closing(self._c()) as c:
            self._insert(c, job_id, **f)
            c.commit()

    def create_next(self, slug: str, **f) -> str:
        """Cấp mã mới VÀ ghi job trong MỘT giao dịch: BEGIN IMMEDIATE giữ khóa ghi từ lúc đọc số thứ tự tới lúc ghi,
        nên nhiều luồng hoặc nhiều tiến trình (bot, dòng lệnh) cùng giao cũng không bao giờ ra hai mã giống nhau.
        Luồng đến sau chờ tới `timeout` (30 giây) rồi mới báo lỗi, thực tế vài mili giây.
        `slug`: phần thương hiệu trong mã job (chỉ chữ không dấu); cột brand lưu giá trị trong f["brand"], không có thì lấy slug."""
        prefix = f"{datetime.now().strftime('%Y%m%d')}-{slug}-"
        with closing(self._c()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                job_id = f"{prefix}{self._next_seq(c, prefix):02d}"
                self._insert(c, job_id, **{"brand": slug, **f})
                c.commit()
            except BaseException:
                c.rollback()
                raise
        return job_id

    def update(self, job_id: str, **f) -> None:
        f = {k: v for k, v in f.items() if k in _COLS}
        f["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sets = ",".join(f"{k}=?" for k in f)
        with closing(self._c()) as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", (*f.values(), job_id))
            c.commit()

    def get(self, job_id: str) -> dict | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _footage_key(p: str | None) -> str:
        return os.path.normcase(os.path.normpath(p or "")) if p else ""

    def same_footage(self, job_id: str) -> list[dict]:
        """Các job KHÁC đang chạy/chờ trên cùng thư mục footage với job này (so đường dẫn đã chuẩn hóa: hoa thường, dấu gạch).
        Dùng để cảnh báo người giao; không chặn gì vì cùng footage vẫn có thể là video khác (khác thương hiệu, khác dạng)."""
        me = self.get(job_id)
        key = self._footage_key(me and me.get("footage_dir"))
        if not key:
            return []
        return [j for j in self.list(None, active_only=True) if j["job_id"] != job_id and self._footage_key(j.get("footage_dir")) == key]

    def stalled(self, minutes: float, now: datetime | None = None) -> list[dict]:
        """Job đang chạy/xếp hàng mà không có cập nhật nào quá `minutes` phút và CHƯA được báo cho mốc cập nhật này.
        Job chờ người (duyệt/bổ sung) hoặc đã bị dừng-có-báo (blocked) không tính là đứng."""
        now = now or datetime.now()
        out = []
        with closing(self._c()) as c:
            rows = c.execute("SELECT * FROM jobs WHERE status IN ('queued','running')").fetchall()
        for r in rows:
            r = dict(r)
            try:
                idle = (now - datetime.fromisoformat(r["updated_at"])).total_seconds() / 60
            except (TypeError, ValueError):
                continue
            if idle >= minutes and r.get("alerted_at") != r["updated_at"]:
                r["idle_minutes"] = int(idle)
                out.append(r)
        return out

    def mark_alerted(self, job_id: str) -> None:
        with closing(self._c()) as c:
            c.execute("UPDATE jobs SET alerted_at=updated_at WHERE job_id=?", (job_id,))
            c.commit()

    def list(self, requester_id: str | None = None, active_only: bool = True) -> list[dict]:
        q, args = "SELECT * FROM jobs", []
        cond = []
        if requester_id is not None:
            cond.append("requester_id=?")
            args.append(str(requester_id))
        if active_only:
            cond.append("status NOT IN ('done','cancelled')")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY created_at DESC LIMIT 50"
        with closing(self._c()) as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]
