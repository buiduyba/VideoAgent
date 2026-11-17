"""Chạy job: nhận lệnh, đẩy vào graph, gửi yêu cầu duyệt cho người giao, tiếp tục khi có trả lời.

Nhiều job cùng tiến (mỗi job một thread_id). Chỉ bước nặng xếp hàng qua khóa suất render (xem locks.py).
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .graph import pending_interrupts
from .nodes import Deps
from .usage import CostCapExceeded


class JobRunner:
    def __init__(self, graph, deps: Deps, admin_notify=None):
        self.graph, self.d = graph, deps
        self.pool = ThreadPoolExecutor(max_workers=max(1, deps.cfg.light_workers))
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.admin_notify = admin_notify  # hàm(text) báo cho admin khi job bị chặn

    # ── API cho bot ──
    def start(self, *, requester_id: str, requester_name: str, footage_dir: str, video_type: str, brand: str, brief_text: str) -> str:
        job_id = self.d.jobs.next_id(brand if brand.replace("-", "").replace("_", "").isalnum() else "job")
        self.d.jobs.create(job_id, requester_id=str(requester_id), requester_name=requester_name, brand=brand,
                           video_type=video_type, footage_dir=footage_dir, status="queued", stage="new")
        state = {"job_id": job_id, "requester_id": str(requester_id), "requester_name": requester_name,
                 "footage_dir": footage_dir, "brand": brand, "video_type": video_type, "brief_text": brief_text,
                 "approvals": {}, "feedback": [], "steps": [], "human_revisions": 0, "critic_fix_rounds": 0,
                 "info_rounds": 0, "outputs": {}}
        self.pool.submit(self._run, job_id, state)
        return job_id

    def resume(self, job_id: str, payload: dict) -> None:
        from langgraph.types import Command

        self.pool.submit(self._run, job_id, Command(resume=payload))

    def continue_(self, job_id: str) -> None:
        """Chạy tiếp job dừng giữa chừng (máy khởi động lại, lỗi tạm). Job đang chờ duyệt thì gửi lại yêu cầu duyệt."""
        self.pool.submit(self._run, job_id, None)

    # ── nội bộ ──
    def _lock(self, job_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(job_id, threading.Lock())

    def _run(self, job_id: str, inp) -> None:
        cfg = {"configurable": {"thread_id": job_id}, "recursion_limit": 100}
        with self._lock(job_id):  # một job không chạy chồng hai lần
            try:
                self.graph.invoke(inp, cfg)
            except Exception as e:  # noqa: BLE001 - mọi lỗi đều phải báo người, không im lặng
                self._blocked(job_id, e)
                return
            self._after(job_id, cfg)

    def _after(self, job_id: str, cfg: dict) -> None:
        reqs, _pending = pending_interrupts(self.graph, cfg)
        for r in reqs:
            if r.get("kind") == "ask_info":
                self.d.jobs.update(job_id, status="waiting_info")
            elif r.get("kind") != "lessons":  # lessons: job đã xong, giữ nguyên trạng thái done
                self.d.jobs.update(job_id, status="waiting_approval")
            self.d.notifier.send(r["to"], f"[{job_id}] {r['text']}", r.get("files", []))

    def _blocked(self, job_id: str, err: Exception) -> None:
        self.d.jobs.update(job_id, status="blocked", note=f"{type(err).__name__}: {err}"[:300])
        job = self.d.jobs.get(job_id) or {}
        if isinstance(err, CostCapExceeded):
            msg = (f"[{job_id}] Job DỪNG vì chi phí AI đã chạm trần: đã dùng ~${err.spent:.2f} / trần ${err.cap:.2f}.\n"
                   f"Đã lưu tiến độ. Muốn làm tiếp: /nangtran {job_id} <số USD mới, lớn hơn ${err.cap:.2f}>  ·  hoặc /huy {job_id}")
            if job.get("requester_id"):
                self.d.notifier.send(job["requester_id"], msg)
            if self.admin_notify:
                self.admin_notify(msg)
            return
        msg = (f"[{job_id}] Job bị DỪNG do lỗi: {type(err).__name__}: {str(err)[:400]}\n"
               f"Đã lưu tiến độ. Sau khi xử lý nguyên nhân, gõ /tieptuc {job_id} để chạy tiếp từ bước lỗi.")
        if job.get("requester_id"):
            self.d.notifier.send(job["requester_id"], msg)
        if self.admin_notify:
            self.admin_notify(msg + "\n" + traceback.format_exc()[-800:])
