"""MiniRunner: chạy đúng nodes.py + spec.py mà KHÔNG cần LangGraph (interrupt giả bằng hàng đợi).

Kiểm được: logic từng node, định tuyến, khóa/duyệt, khuôn dữ liệu, khóa trạng thái hợp lệ.
KHÔNG kiểm được: lớp nối LangGraph (graph.py), checkpointer, tiếp tục sau khi tắt máy. Phần đó chạy `vidagents demo`.
"""
from __future__ import annotations

from .nodes import Deps, make_nodes
from .spec import END, ENTRY, build_spec
from .state import REDUCE_ADD, JobState


class NeedInput(Exception):
    def __init__(self, value):
        self.value = value


class MiniRunner:
    def __init__(self, deps: Deps):
        self.d = deps
        deps.interrupt = self._interrupt
        self.nodes = make_nodes(deps)
        self.plain, self.cond = build_spec(deps.cfg)
        self.queue: list = []
        self.trace: list[str] = []
        self.paused: str | None = None
        self.pending: dict | None = None
        self.valid_keys = set(JobState.__annotations__)

    def _interrupt(self, value):
        if self.queue:
            return self.queue.pop(0)
        raise NeedInput(value)

    def run(self, state: dict, start: str | None = None, max_steps: int = 300) -> str:
        node = start or ENTRY
        for _ in range(max_steps):
            if node == END:
                self.paused = self.pending = None
                return "done"
            self.trace.append(node)
            try:
                upd = self.nodes[node](state) or {}
            except NeedInput as e:
                self.paused, self.pending = node, e.value
                return "paused"
            bad = set(upd) - self.valid_keys
            assert not bad, f"node '{node}' trả khóa không có trong JobState: {bad}"
            for k, v in upd.items():
                state[k] = state.get(k, []) + v if k in REDUCE_ADD else v
            if node in self.plain:
                node = self.plain[node]
            else:
                router, targets = self.cond[node]
                node = router(state)
                assert node in targets, f"router của '{self.trace[-1]}' trả '{node}' ngoài danh sách {targets}"
        raise RuntimeError("Vòng lặp quá dài (có thể thiếu giới hạn vòng)")

    def resume(self, state: dict, payload: dict) -> str:
        assert self.paused, "không có gì đang chờ"
        self.queue.append(payload)
        return self.run(state, start=self.paused)
