"""Chạy job: nhận lệnh, đẩy vào graph, gửi yêu cầu duyệt cho người giao, tiếp tục khi có trả lời.

Nhiều job cùng tiến (mỗi job một thread_id). Chỉ bước nặng xếp hàng qua khóa suất render (xem locks.py).

Hai giới hạn tách nhau: `light_workers` là số job chạy việc NHẸ cùng lúc (một "chỗ" cho mỗi job đang chạy việc nhẹ), còn luồng thì nhiều hơn
(`job_threads`). Job vào bước nặng (chờ suất render hoặc đang render) NHẢ chỗ nhẹ của mình, xong bước nặng mới xin lại. Nếu không, mỗi job
đang chờ suất render giữ một luồng nhẹ: với 4 luồng và 1 suất render, 3 luồng bị kẹt chờ, mọi lượt duyệt/trả lời của job khác xếp hàng sau
có khi cả giờ (đo ở bài thử tải 10 người x 3 job).
"""
from __future__ import annotations

import contextlib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .graph import pending_interrupts
from .nodes import SLUG, Deps
from .usage import CostCapExceeded


class JobRunner:
    def __init__(self, graph, deps: Deps, admin_notify=None):
        self.graph, self.d = graph, deps
        self.light = max(1, deps.cfg.light_workers)
        self.pool = ThreadPoolExecutor(max_workers=max(self.light, int(getattr(deps.cfg, "job_threads", 64))))
        self._seats = threading.BoundedSemaphore(self.light)   # chỗ chạy việc nhẹ
        self._tl = threading.local()                           # luồng này đang giữ chỗ nhẹ không
        if hasattr(deps.tools, "heavy_hook"):
            deps.tools.heavy_hook = self._off_seat
        if hasattr(deps.tools, "wait_tick"):
            deps.tools.wait_tick = self._heartbeat
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.admin_notify = admin_notify  # hàm(text) báo cho admin khi job bị chặn

    # ── API cho bot ──
    def start(self, *, requester_id: str, requester_name: str, footage_dir: str, video_type: str, brand: str, brief_text: str,
              brief_prefill: dict | None = None) -> str:
        # mã job chỉ dùng chữ không dấu (isalnum() nhận cả 'quảng' -> mã job có dấu, trang theo dõi không mở được)
        # cấp mã và ghi sổ trong một giao dịch (create_next): hai lệnh rời nhau (lấy mã rồi tạo) sẽ trùng mã khi nhiều người giao cùng lúc
        job_id = self.d.jobs.create_next(brand if SLUG.match(brand or "") else "job", requester_id=str(requester_id), requester_name=requester_name,
                                         brand=brand, video_type=video_type, footage_dir=footage_dir, status="queued", stage="new")
        state = {"job_id": job_id, "requester_id": str(requester_id), "requester_name": requester_name,
                 "footage_dir": footage_dir, "brand": brand, "video_type": video_type, "brief_text": brief_text,
                 "approvals": {}, "feedback": [], "steps": [], "human_revisions": 0, "critic_fix_rounds": 0,
                 "info_rounds": 0, "outputs": {}}
        if brief_prefill:
            state["brief_prefill"] = brief_prefill
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

    def _heartbeat(self, job_id: str) -> None:
        """Job đang xếp hàng chờ suất render: làm mới giờ cập nhật trong sổ job. Không có nhịp này, job chờ quá `stall_minutes` bị báo "đứng" sai
        (hàng chờ dài là bình thường khi nhiều người giao cùng lúc). Lỗi ghi sổ thì bỏ qua, lần sau thử lại."""
        try:
            self.d.jobs.update(job_id)
        except Exception:  # noqa: BLE001
            pass

    def _release_seat(self) -> None:
        if getattr(self._tl, "seat", False):
            self._tl.seat = False
            self._seats.release()

    @contextlib.contextmanager
    def _off_seat(self, owner: str):
        """Bao quanh bước nặng: nhả chỗ nhẹ khi vào, xin lại khi ra (kể cả khi bước nặng lỗi). Luồng không giữ chỗ (CLI, test) thì không làm gì."""
        held = getattr(self._tl, "seat", False)
        if held:
            self._release_seat()
        try:
            yield
        finally:
            if held:
                self._seats.acquire()
                self._tl.seat = True

    def _run(self, job_id: str, inp) -> None:
        cfg = {"configurable": {"thread_id": job_id}, "recursion_limit": 100}
        with self._lock(job_id):  # một job không chạy chồng hai lần
            self._seats.acquire()   # xếp hàng chờ chỗ nhẹ (luồng thì có sẵn, chỗ mới là giới hạn)
            self._tl.seat = True
            try:
                self.graph.invoke(inp, cfg)
            except Exception as e:  # noqa: BLE001 - mọi lỗi đều phải báo người, không im lặng
                self._release_seat()   # trả chỗ TRƯỚC khi báo lỗi: gửi tin (có thể kèm file) không cần chỗ nhẹ
                self._blocked(job_id, e)
                return
            finally:
                self._release_seat()   # chạy lại ở đây là vô hại (chỉ trả nếu còn giữ)
            self._after(job_id, cfg)

    def _after(self, job_id: str, cfg: dict) -> None:
        reqs, _pending = pending_interrupts(self.graph, cfg)
        for r in reqs:
            if r.get("kind") == "ask_info":
                self.d.jobs.update(job_id, status="waiting_info")
            elif r.get("kind") != "lessons":  # lessons: job đã xong, giữ nguyên trạng thái done
                self.d.jobs.update(job_id, status="waiting_approval")
            self.d.notifier.send(r["to"], f"[{job_id}] {r['text']}", r.get("files", []), buttons=self._buttons(job_id))

    def _buttons(self, job_id: str):
        """Nút bấm hợp với trạng thái job lúc này (rỗng nếu không có gì để bấm). Lỗi dựng nút không được làm hỏng việc gửi tin."""
        try:
            from . import handedit as H
            from .ui import job_keyboard

            cap = self.d.meter.cap_for(job_id) if self.d.meter else None
            return job_keyboard(self.d.jobs.get(job_id), cap=cap, can_handedit=not H.available(self.d.cfg))
        except Exception:  # noqa: BLE001
            return []

    def _blocked(self, job_id: str, err: Exception) -> None:
        self.d.jobs.update(job_id, status="blocked", note=f"{type(err).__name__}: {err}"[:300])
        job = self.d.jobs.get(job_id) or {}
        if isinstance(err, CostCapExceeded):
            msg = (f"[{job_id}] Job DỪNG vì chi phí AI đã chạm trần: đã dùng ~${err.spent:.2f} / trần ${err.cap:.2f}.\n"
                   f"Đã lưu tiến độ. Muốn làm tiếp: /nangtran {job_id} <số USD mới, lớn hơn ${err.cap:.2f}>  ·  hoặc /huy {job_id}")
            if job.get("requester_id"):
                self.d.notifier.send(job["requester_id"], msg, buttons=self._buttons(job_id))
            if self.admin_notify:
                self.admin_notify(msg)
            return
        msg = (f"[{job_id}] Job bị DỪNG do lỗi: {type(err).__name__}: {str(err)[:400]}\n"
               f"Đã lưu tiến độ. Sau khi xử lý nguyên nhân, gõ /tieptuc {job_id} để chạy tiếp từ bước lỗi.")
        if job.get("requester_id"):
            self.d.notifier.send(job["requester_id"], msg, buttons=self._buttons(job_id))
        if self.admin_notify:
            self.admin_notify(msg + "\n" + traceback.format_exc()[-800:])
