"""Theo dõi tiến độ: mọi agent chỉ phát sự kiện qua Tracker, giao diện đọc lại từ đây."""
import threading
import time
import uuid

# (id, tên hiển thị, vai trò)
AGENTS = [
    ("supervisor", "Supervisor", "Chia việc, theo dõi"),
    ("researcher", "Researcher", "Tìm xu hướng"),
    ("writer", "Writer", "Viết kịch bản"),
    ("video", "Video editor", "Dựng video"),
    ("reviewer", "Reviewer", "Kiểm duyệt"),
    ("publisher", "Publisher", "Duyệt và đăng (n8n)"),
]

REG: dict[str, "Tracker"] = {}


class Tracker:
    def __init__(self, brief: str):
        self.run_id = uuid.uuid4().hex[:8]
        self.brief = brief
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.agents = {
            a[0]: {"status": "pending", "progress": 0, "task": "Chờ", "tool": "", "tokens": 0}
            for a in AGENTS
        }
        self.status = "running"
        self.overall = 0
        self.approval_announced = False
        REG[self.run_id] = self

    def _push(self, ev: dict):
        with self.lock:
            ev["seq"] = len(self.events)
            ev["ts"] = time.strftime("%H:%M:%S")
            self.events.append(ev)

    def emit(self, agent, status=None, progress=None, task=None, tool=None,
             tokens=0, log=None, artifact=None):
        a = self.agents[agent]
        if status:
            a["status"] = status
        if progress is not None:
            a["progress"] = progress
        if task:
            a["task"] = task
        if tool:
            a["tool"] = tool
        a["tokens"] += tokens
        avg = round(sum(x["progress"] for x in self.agents.values()) / len(self.agents))
        self.overall = max(self.overall, avg)  # không tụt khi có vòng sửa
        self._push({"type": "agent", "agent": agent, **a, "log": log,
                    "artifact": artifact, "overall": self.overall})

    def set_run(self, status: str):
        self.status = status
        if status == "done":
            self.overall = 100
        self._push({"type": "run", "status": status, "overall": self.overall})

    @property
    def finished(self) -> bool:
        return self.status in ("done", "cancelled", "error")
