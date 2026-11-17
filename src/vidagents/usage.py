"""Đếm token và tiền AI theo từng job, có trần chi phí mỗi job.

- Số liệu ghi vào cùng file SQLite với sổ job (bảng `usage`), nên còn nguyên sau khi tắt máy.
- Giá lấy từ `pricing` (USD cho mỗi 1 triệu token). Giá mặc định bên dưới là số THAM KHẢO từ một trang tổng hợp bên thứ ba
  (cập nhật 4/11/2026), KHÔNG phải trang giá chính thức: đối chiếu và ghi đè trong config trước khi dùng để tính ngân sách.
- Model chưa có giá thì KHÔNG đoán: vẫn đếm token, ô tiền để trống và báo rõ là chưa có giá.
- Trần chi phí (`max_cost_per_job_usd`, hoặc trần riêng của job qua /nangtran): chạm trần thì dừng job để hỏi người giao.
"""
from __future__ import annotations

import contextvars
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

# USD / 1 triệu token. in = đầu vào, out = đầu ra, cache_read = đọc từ bộ nhớ đệm, cache_write = ghi vào bộ nhớ đệm (5 phút).
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.5},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}

_job: contextvars.ContextVar[str | None] = contextvars.ContextVar("vidagents_job", default=None)


class CostCapExceeded(Exception):
    """Chi phí AI của job đã chạm trần. Job dừng lại hỏi người giao, không tự chạy tiếp."""

    def __init__(self, job_id: str, spent: float, cap: float):
        super().__init__(f"Chi phí AI của job {job_id} đã tới ${spent:.2f}, chạm trần ${cap:.2f}")
        self.job_id, self.spent, self.cap = job_id, spent, cap


@contextmanager
def job_context(job_id: str | None) -> Iterator[None]:
    """Mọi lời gọi AI trong khối này được tính cho job này."""
    tok = _job.set(job_id)
    try:
        yield
    finally:
        _job.reset(tok)


def current_job() -> str | None:
    return _job.get()


def price_usd(pricing: dict, model: str, tin: int, tout: int, cache_read: int = 0, cache_write: int = 0) -> float | None:
    p = pricing.get(model)
    if not p:
        return None
    return (tin * p["in"] + tout * p["out"] + cache_read * p.get("cache_read", p["in"])
            + cache_write * p.get("cache_write", p["in"])) / 1_000_000


class UsageMeter:
    def __init__(self, db_path: Path, pricing: dict | None = None, default_cap_usd: float | None = None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pricing = {**DEFAULT_PRICING, **(pricing or {})}
        self.default_cap = default_cap_usd
        with closing(self._c()) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS usage(
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, agent TEXT, model TEXT,
                input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                usd REAL, at TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS usage_job ON usage(job_id)")
            c.execute("CREATE TABLE IF NOT EXISTS caps(job_id TEXT PRIMARY KEY, cap_usd REAL)")
            c.commit()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # ── ghi ──
    def record(self, agent: str, model: str, tin: int, tout: int, cache_read: int = 0, cache_write: int = 0,
               job_id: str | None = None) -> float | None:
        job_id = job_id or current_job()
        usd = price_usd(self.pricing, model, tin, tout, cache_read, cache_write)
        if not job_id:  # ngoài job (vd. thử tay): không có chỗ ghi
            return usd
        with closing(self._c()) as c:
            c.execute("INSERT INTO usage(job_id,agent,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,usd,at)"
                      " VALUES(?,?,?,?,?,?,?,?,?)",
                      (job_id, agent, model, tin, tout, cache_read, cache_write, usd, datetime.now().isoformat(timespec="seconds")))
            c.commit()
        return usd

    # ── đọc ──
    def job_total(self, job_id: str) -> dict:
        with closing(self._c()) as c:
            rows = c.execute("SELECT agent, model, input_tokens i, output_tokens o, cache_read_tokens cr, cache_write_tokens cw, usd "
                             "FROM usage WHERE job_id=?", (job_id,)).fetchall()
        tot = {"job_id": job_id, "calls": len(rows), "input_tokens": 0, "output_tokens": 0, "usd": 0.0,
               "unpriced_models": [], "by_agent": {}}
        for r in rows:
            tot["input_tokens"] += r["i"] + r["cr"] + r["cw"]
            tot["output_tokens"] += r["o"]
            a = tot["by_agent"].setdefault(r["agent"], {"calls": 0, "usd": 0.0})
            a["calls"] += 1
            if r["usd"] is None:
                if r["model"] not in tot["unpriced_models"]:
                    tot["unpriced_models"].append(r["model"])
            else:
                tot["usd"] += r["usd"]
                a["usd"] += r["usd"]
        return tot

    def cap_for(self, job_id: str) -> float | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT cap_usd FROM caps WHERE job_id=?", (job_id,)).fetchone()
        return r["cap_usd"] if r else self.default_cap

    def set_cap(self, job_id: str, usd: float) -> None:
        if usd <= 0:
            raise ValueError("Trần phải lớn hơn 0")
        with closing(self._c()) as c:
            c.execute("INSERT INTO caps(job_id,cap_usd) VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET cap_usd=excluded.cap_usd",
                      (job_id, float(usd)))
            c.commit()

    def check(self, job_id: str | None = None) -> None:
        """Gọi TRƯỚC mỗi lần gọi AI: đã chạm trần thì dừng."""
        job_id = job_id or current_job()
        if not job_id:
            return
        cap = self.cap_for(job_id)
        if cap is None:
            return
        spent = self.job_total(job_id)["usd"]
        if spent >= cap:
            raise CostCapExceeded(job_id, spent, cap)

    def summary_line(self, job_id: str) -> str:
        t = self.job_total(job_id)
        if not t["calls"]:
            return ""
        money = f"~${t['usd']:.2f}" if not t["unpriced_models"] else f"~${t['usd']:.2f} (chưa có giá cho {', '.join(t['unpriced_models'])}, chưa tính)"
        return f"Chi phí AI: {money} · {t['input_tokens']:,} token vào, {t['output_tokens']:,} token ra, {t['calls']} lượt gọi"

    def totals_by_job(self, limit: int = 50) -> list[dict]:
        with closing(self._c()) as c:
            ids = [r[0] for r in c.execute("SELECT job_id FROM usage GROUP BY job_id ORDER BY MAX(id) DESC LIMIT ?", (limit,))]
        return [self.job_total(j) for j in ids]
