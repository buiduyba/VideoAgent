"""Dashboard chạy bằng DỮ LIỆU GIẢ để xem giao diện (không đọc sổ job, checkpoint hay chi phí thật, không gọi AI).

Chạy:  python tools/demo_dashboard.py [--port 8766]   rồi mở http://127.0.0.1:8766
Phục vụ đúng file src/vidagents/web/index.html và trả JSON cùng khuôn với src/vidagents/dashboard.py.
Job "20261120-oillan-01" tự chạy qua 11 mốc (mỗi ~5 giây, dừng ở hai cổng duyệt rồi tự "duyệt") để thấy đèn lồng di chuyển;
các job còn lại đứng yên ở những trạng thái khác nhau. Mở /set?job=<mã>&n=0..11&s=active|waiting|stopped để ghim một job vào mốc bất kỳ.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

INDEX = Path(__file__).resolve().parent.parent / "src" / "vidagents" / "web" / "index.html"
MILES = [("intake", "Nhận job", "orchestrator"), ("extract_brief", "Đọc brief", "orchestrator"), ("footage", "Xem footage", "footage"),
         ("script", "Viết kịch bản", "script"), ("duyet_1", "Người duyệt 1", "human"), ("edit_build", "Dựng bản đầu", "edit"),
         ("critic_preview", "Critic xem trước", "critic"), ("duyet_2", "Người duyệt 2", "human"), ("render", "Render", "edit"),
         ("critic_final", "Critic bản cuối", "critic"), ("deliver", "Giao kết quả", "orchestrator")]
AGENTS = (("orchestrator", "Điều phối"), ("script", "Script"), ("footage", "Footage"), ("edit", "Edit"), ("critic", "Critic"))
GATES = {"duyet_1", "duyet_2"}
USD = {"orchestrator": 0.02, "script": 0.14, "footage": 0.06, "edit": 0.09, "critic": 0.08}
CALLS = {"orchestrator": 2, "script": 3, "footage": 2, "edit": 3, "critic": 2}
STATUS = {"active": "running", "waiting": "waiting_approval", "stopped": "blocked"}

# (mã job, thương hiệu, dạng video, người giao, mốc n, trạng thái, ghi chú) — n = số mốc đã xong
STATIC = [
    ("20261120-foellie-02", "foellie", "hoi-cho-ban-hang", "Lan", 6, "active", ""),
    ("20261120-oillan-05", "oillan", "koc-review", "Huy", 1, "waiting", "thiếu thông tin: KOC đã đồng ý đăng chưa?"),
    ("20261119-oillan-04", "oillan", "su-kien-highlight", "Duy", 8, "stopped", "chạm trần chi phí AI của job"),
    ("20261119-pure-03", "pure", "koc-review", "Lan", 11, "active", ""),
]
LIVE = ("20261120-oillan-01", "oillan", "koc-review", "Duy")
START = time.time()
PINNED: dict[str, tuple[int, str]] = {}


def live_state() -> tuple[int, str]:
    """Job chính: mỗi 5 giây sang một mốc; ở cổng duyệt thì 'chờ người' một nhịp; xong thì nghỉ 2 nhịp rồi lặp lại."""
    tick = int((time.time() - START) // 5) % (len(MILES) + 1 + 2 + 2)
    n = min(tick, len(MILES))
    if n >= len(MILES):
        return len(MILES), "active"
    return n, ("waiting" if MILES[n][0] in GATES and (time.time() - START) % 5 > 1.5 else "active")


def build(job_id, brand, vtype, who, n, s, note, now):
    n = max(0, min(len(MILES), n))
    done = n >= len(MILES)
    status = "done" if done else STATUS[s]
    if not done and s == "waiting" and MILES[n][0] == "extract_brief":
        status = "waiting_info"
    miles = []
    for i, (k, label, agent) in enumerate(MILES):
        st = "done" if (i < n or done) else ("pending" if i > n else s)
        miles.append({"key": k, "label": label, "agent": agent, "state": st})
    cur = MILES[n][2] if not done else None
    steps = []
    for i in range(min(n, len(MILES))):
        k, label, agent = MILES[i]
        if k in GATES:
            continue
        steps.append({"name": k, "label": label, "agent": agent, "status": "ok",
                      "at": (now - timedelta(minutes=(n - i) * 3)).isoformat(timespec="seconds")})
    agents = []
    for aid, name in AGENTS:
        mine = [x for x in steps if x["agent"] == aid]
        state = "active" if (status == "running" and cur == aid) else ("done" if mine else "idle")
        agents.append({"id": aid, "name": name, "state": state, "steps": len(mine),
                       "last": mine[-1]["label"] if mine else "", "last_at": mine[-1]["at"] if mine else "", "skill_version": "demo",
                       "calls": CALLS[aid] if mine else 0, "usd": USD[aid] if mine else 0.0})
    usd = round(sum(a["usd"] for a in agents), 4)
    findings = []
    if n >= 7 or (job_id.endswith("-02") and n >= 7):
        findings = [{"severity": "vua", "at": 12.4, "desc": "Phụ đề dài hơn 2 giây so với lời, cần ngắt sớm hơn"}]
    if job_id.endswith("-02"):
        findings = [{"severity": "nang", "at": 8.0, "desc": "Giá trên màn hình (299k) lệch giá trong brief (319k)"},
                    {"severity": "vua", "at": 21.5, "desc": "Chữ chồng lên logo ở góc phải"}]
    waiting_for = ""
    if status == "waiting_info":
        waiting_for = "info"
    elif status == "waiting_approval":
        waiting_for = {"duyet_1": "approve_1", "duyet_2": "approve_2"}.get(MILES[n][0], "approve")
    return {
        "job_id": job_id, "brand": brand, "video_type": vtype, "requester": who, "status": status, "stage": MILES[min(n, 10)][0],
        "note": note, "created_at": (now - timedelta(hours=2)).isoformat(timespec="seconds"), "updated_at": now.isoformat(timespec="seconds"),
        "stalled": False, "progress": round(100 * n / len(MILES)), "mode": "B", "active": cur, "waiting_for": waiting_for, "escalation": None,
        "milestones": miles, "agents": agents, "steps": steps,
        "critic": {"stage": "preview" if n < 9 else "final", "heavy": sum(f["severity"] == "nang" for f in findings),
                   "mid": sum(f["severity"] == "vua" for f in findings), "findings": findings},
        "rounds": {"critic_fix": 1 if findings else 0, "critic_max": 2, "human": 0, "human_max": 3, "info": 1 if status == "waiting_info" else 0, "info_max": 2},
        "cost": {"usd": usd, "calls": sum(a["calls"] for a in agents), "cap": 2.0, "input_tokens": int(usd * 90000), "output_tokens": int(usd * 18000),
                 "unpriced_models": []},
        "outputs": {"video": r"\\NAS\video\demo\final.mp4"} if done else {}, "checkpoint_error": None,
    }


def all_jobs():
    now = datetime.now().replace(second=0, microsecond=0)  # làm tròn tới phút để dữ liệu không đổi mỗi 2 giây (giống job thật)
    out = []
    n, s = PINNED.get(LIVE[0]) or live_state()
    out.append(build(LIVE[0], LIVE[1], LIVE[2], LIVE[3], n, s, "", now))
    for jid, brand, vtype, who, n, s, note in STATIC:
        n, s = PINNED.get(jid, (n, s))
        out.append(build(jid, brand, vtype, who, n, s, note, now))
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(INDEX.read_bytes(), "text/html")
        if u.path == "/set":
            q = parse_qs(u.query)
            PINNED[q.get("job", [LIVE[0]])[0]] = (int(q.get("n", ["0"])[0]), q.get("s", ["active"])[0])
            return self._send("ok", "text/plain")
        if u.path == "/api/overview":
            jobs = all_jobs()
            counts: dict[str, int] = {}
            for j in jobs:
                counts[j["status"]] = counts.get(j["status"], 0) + 1
            brief = [{k: j[k] for k in ("job_id", "brand", "video_type", "requester", "status", "stage", "progress", "active", "updated_at",
                                          "stalled", "waiting_for")} | {"usd": j["cost"]["usd"], "cap": j["cost"]["cap"]} for j in jobs]
            return self._send({"generated_at": datetime.now().isoformat(timespec="seconds"), "counts": counts, "jobs": brief,
                               "render_slots": {"busy": 1, "total": 2}, "total_usd": round(sum(j["cost"]["usd"] for j in jobs), 4), "stall_minutes": 60})
        if u.path.startswith("/api/jobs/"):
            jid = u.path.rsplit("/", 1)[1]
            j = next((x for x in all_jobs() if x["job_id"] == jid), None)
            return self._send(j if j else {"error": "not found"}, code=200 if j else 404)
        self._send("not found", "text/plain", 404)


def main() -> None:
    import sys
    for stream in (sys.stdout, sys.stderr):  # console Windows hay là cp1252, in tiếng Việt sẽ lỗi nếu không ép UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8766)
    a = p.parse_args()
    print(f"Dashboard DỮ LIỆU GIẢ: http://127.0.0.1:{a.port}  (Ctrl+C để dừng)")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
