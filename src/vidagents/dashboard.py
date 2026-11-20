"""Dashboard xem job đang chạy: chỉ ĐỌC (sổ job, checkpoint LangGraph, bảng chi phí), không có đường nào ghi hay duyệt.

Duyệt/hủy vẫn chỉ qua Telegram (nguyên tắc 6): dashboard không có quyền đó, nên không cần đăng nhập, mặc định chỉ nghe 127.0.0.1.
Không thêm thư viện: dùng http.server của Python. Trình duyệt hỏi lại mỗi ~2 giây (không phải đẩy realtime).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .config import Config
from .jobs import JobStore
from .usage import UsageMeter

WEB = Path(__file__).resolve().parent / "web"
_JOB_ID = re.compile(r"^[\w.\-]{1,80}$")

AGENTS = (  # (id, tên hiển thị)
    ("orchestrator", "Điều phối"), ("script", "Script"), ("footage", "Footage"), ("edit", "Edit"), ("critic", "Critic"),
)
STEP_AGENT = {
    "intake": "orchestrator", "extract_brief": "orchestrator", "footage": "footage", "script": "script",
    "edit_build": "edit", "edit_fix": "edit", "render": "edit", "critic_preview": "critic", "critic_final": "critic",
    "deliver": "orchestrator", "lessons": "orchestrator", "hand_edit": "edit", "hand_edit_done": "edit",
}
STEP_LABEL = {
    "hand_edit": "Đóng gói chỉnh tay (VideoFac)", "hand_edit_done": "Nhận bản chỉnh tay",
    "intake": "Nhận job", "extract_brief": "Đọc brief", "footage": "Xem footage", "script": "Viết kịch bản",
    "edit_build": "Dựng bản đầu", "edit_fix": "Sửa lỗi", "critic_preview": "Critic: bản xem trước", "render": "Render",
    "critic_final": "Critic: bản cuối", "deliver": "Giao kết quả", "lessons": "Rút bài học",
}
# (khóa, nhãn, tác nhân, cách biết đã xong: tên bước trong state.steps hoặc khóa trong state.approvals)
MILESTONES = (
    ("intake", "Nhận job", "orchestrator"), ("extract_brief", "Đọc brief", "orchestrator"),
    ("footage", "Xem footage", "footage"), ("script", "Viết kịch bản", "script"),
    ("duyet_1", "Người duyệt 1", "human"), ("edit_build", "Dựng bản đầu", "edit"),
    ("critic_preview", "Critic xem trước", "critic"), ("duyet_2", "Người duyệt 2", "human"),
    ("render", "Render", "edit"), ("critic_final", "Critic bản cuối", "critic"), ("deliver", "Giao kết quả", "orchestrator"),
)
GATES = ("duyet_1", "duyet_2")
STATUS_LIVE = ("queued", "running")


def _next_agent(last: str | None, st: dict) -> str | None:
    """Ai làm việc kế tiếp, suy từ bước vừa xong. 'human' = đang chờ người."""
    if not last:
        return "orchestrator"
    if last == "extract_brief":
        return "human" if st.get("info_request") else "footage"
    if last == "script":
        return "human"
    if last == "critic_preview":
        return "edit" if st.get("critic_has_heavy") else "human"
    if last == "lessons":
        return None
    return {"intake": "orchestrator", "footage": "script", "edit_build": "critic", "edit_fix": "critic", "render": "critic",
            "critic_final": "orchestrator", "deliver": "orchestrator", "hand_edit": "human",
            "hand_edit_done": "critic"}.get(last)


def _waiting_for(status: str, st: dict, first_open: str | None) -> str:
    """Job đang chờ người làm gì: escalate (vượt giới hạn, cần quyết) > info (thiếu thông tin) > approve_1/approve_2 > approve."""
    if status not in ("waiting_approval", "waiting_info"):
        return ""
    if st.get("escalation"):
        return "escalate"
    if status == "waiting_info":
        return "info"
    return {"duyet_1": "approve_1", "duyet_2": "approve_2"}.get(first_open or "", "approve")


class Reader:
    """Gom dữ liệu cho giao diện. Không giữ trạng thái riêng, nên chạy song song với bot mà không đụng nhau."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.jobs = JobStore(cfg.jobs_db)
        self.meter = UsageMeter(cfg.jobs_db, cfg.pricing, cfg.max_cost_per_job_usd)
        self._lock = threading.Lock()
        self._saver = None

    # ── checkpoint ──
    def _checkpoint_values(self, job_id: str) -> tuple[dict, str | None]:
        """(state của job, lỗi nếu không đọc được). Không đọc được thì vẫn hiển thị theo sổ job."""
        if not Path(self.cfg.checkpoint_db).exists():
            return {}, None
        try:
            with self._lock:
                if self._saver is None:
                    from langgraph.checkpoint.sqlite import SqliteSaver

                    self._saver = SqliteSaver(sqlite3.connect(str(self.cfg.checkpoint_db), check_same_thread=False, timeout=30))
                t = self._saver.get_tuple({"configurable": {"thread_id": job_id}})
            return (dict(t.checkpoint.get("channel_values") or {}) if t else {}), None
        except Exception as e:  # noqa: BLE001 - dashboard không được làm hỏng job, chỉ báo là không đọc được
            return {}, f"{type(e).__name__}: {str(e)[:160]}"

    # ── một job ──
    def job(self, job_id: str, stalled: set[str] | None = None) -> dict | None:
        row = self.jobs.get(job_id)
        if not row:
            return None
        st, err = self._checkpoint_values(job_id)
        steps = list(st.get("steps") or [])
        approvals = st.get("approvals") or {}
        seen = {s.get("name") for s in steps}
        status = row["status"]
        live = status in STATUS_LIVE
        last = steps[-1].get("name") if steps else None
        active = _next_agent(last, st) if live else ("human" if status in ("waiting_approval", "waiting_info") else None)

        reached = [(k in seen) if k not in GATES else bool(approvals.get(k)) for k, _l, _a in MILESTONES]
        if status == "done":
            reached = [True] * len(MILESTONES)
        first_open = next((i for i, r in enumerate(reached) if not r), None)
        miles = []
        for i, (k, label, agent) in enumerate(MILESTONES):
            if reached[i]:
                state = "done"
            elif i == first_open and live:
                state = "active"
            elif i == first_open and status in ("waiting_approval", "waiting_info"):
                state = "waiting"
            elif i == first_open and status in ("blocked", "cancelled"):
                state = "stopped"
            else:
                state = "pending"
            miles.append({"key": k, "label": label, "agent": agent, "state": state})

        cost = self.meter.job_total(job_id)
        by_agent = cost["by_agent"]
        agents = []
        for aid, name in AGENTS:
            mine = [s for s in steps if STEP_AGENT.get(s.get("name")) == aid]
            lastmine = mine[-1] if mine else None
            if live and active == aid:
                state = "active"
            elif lastmine:
                state = "done"
            else:
                state = "idle"
            agents.append({
                "id": aid, "name": name, "state": state, "steps": len(mine),
                "last": (STEP_LABEL.get(lastmine["name"], lastmine["name"]) + (" (không đổi)" if lastmine.get("status") == "no_change" else "")) if lastmine else "",
                "last_at": lastmine.get("at", "") if lastmine else "",
                "skill_version": (lastmine or {}).get("skill_version", ""),
                "calls": by_agent.get(aid, {}).get("calls", 0), "usd": round(by_agent.get(aid, {}).get("usd", 0.0), 4),
            })

        findings = list(st.get("critic_findings") or [])
        done_n = sum(reached)
        return {
            "job_id": job_id, "brand": row["brand"], "video_type": row["video_type"], "requester": row["requester_name"],
            "status": status, "stage": row["stage"], "note": row["note"] or "", "created_at": row["created_at"],
            "updated_at": row["updated_at"], "stalled": bool(stalled and job_id in stalled),
            "progress": round(100 * done_n / len(MILESTONES)), "mode": st.get("mode", ""), "active": active,
            "waiting_for": _waiting_for(status, st, MILESTONES[first_open][0] if first_open is not None else None),
            "escalation": st.get("escalation") or None,
            "milestones": miles, "agents": agents,
            "steps": [{"name": s.get("name"), "label": STEP_LABEL.get(s.get("name"), s.get("name")),
                       "agent": STEP_AGENT.get(s.get("name"), ""), "status": s.get("status", "ok"), "at": s.get("at", "")}
                      for s in steps][-60:],
            "critic": {"stage": st.get("critic_stage", ""), "heavy": sum(f.get("severity") == "nang" for f in findings),
                       "mid": sum(f.get("severity") == "vua" for f in findings),
                       "findings": [{"severity": f.get("severity"), "at": f.get("at_seconds"), "desc": f.get("desc", "")} for f in findings[:12]]},
            "rounds": {"critic_fix": st.get("critic_fix_rounds", 0), "critic_max": self.cfg.max_critic_rounds,
                       "human": st.get("human_revisions", 0), "human_max": self.cfg.max_revisions,
                       "info": st.get("info_rounds", 0), "info_max": self.cfg.max_info_rounds},
            "cost": {"usd": round(cost["usd"], 4), "calls": cost["calls"], "cap": self.meter.cap_for(job_id),
                     "input_tokens": cost["input_tokens"], "output_tokens": cost["output_tokens"],
                     "unpriced_models": cost["unpriced_models"]},
            "outputs": {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in (st.get("outputs") or {}).items()},
            "checkpoint_error": err,
        }

    # ── tổng quan ──
    def overview(self) -> dict:
        rows = self.jobs.list(active_only=False)
        stalled = {j["job_id"] for j in self.jobs.stalled(self.cfg.stall_minutes)}
        views = [v for v in (self.job(r["job_id"], stalled) for r in rows) if v]
        counts: dict[str, int] = {}
        for v in views:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        brief = [{k: v[k] for k in ("job_id", "brand", "video_type", "requester", "status", "stage", "progress", "active",
                                      "updated_at", "stalled", "waiting_for")} | {"usd": v["cost"]["usd"], "cap": v["cost"]["cap"]}
                 for v in views]
        slots = None
        try:
            from .locks import SlotLock

            slots = {"busy": SlotLock(self.cfg.locks_dir, self.cfg.render_slots).busy(), "total": self.cfg.render_slots}
        except Exception:  # noqa: BLE001
            pass
        return {"generated_at": datetime.now().isoformat(timespec="seconds"), "counts": counts, "jobs": brief,
                "render_slots": slots, "total_usd": round(sum(v["cost"]["usd"] for v in views), 4),
                "stall_minutes": self.cfg.stall_minutes}


# ───────────────────────── máy chủ ─────────────────────────
def make_server(reader: Reader, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # im lặng, đỡ rối màn hình bot
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api/overview":
                    return self._json(200, reader.overview())
                if path.startswith("/api/jobs/"):
                    job_id = unquote(path[len("/api/jobs/"):])  # trình duyệt mã hóa %xx (mã job cũ có thể có dấu)
                    if not _JOB_ID.match(job_id):
                        return self._json(400, {"error": "Mã job không hợp lệ"})
                    v = reader.job(job_id)
                    return self._json(200, v) if v else self._json(404, {"error": "Không có job này"})
                return self._json(404, {"error": "Không có trang này"})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})

        def do_POST(self):  # noqa: N802 - cố ý không có: dashboard chỉ đọc
            try:   # đọc hết thân yêu cầu trước khi trả lời: không đọc thì Windows ngắt kết nối và máy khách đôi khi chỉ thấy lỗi ngắt, không thấy 405
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 65536:
                    self.rfile.read(n)
            except (ValueError, OSError):
                pass
            self.close_connection = True
            self._json(405, {"error": "Dashboard chỉ xem. Duyệt/hủy qua Telegram."})

        do_PUT = do_DELETE = do_PATCH = do_POST

    return ThreadingHTTPServer((host, port), Handler)
