"""Kho bài học riêng của từng agent (agents/<tên>/lessons.jsonl).

Luật (giữ nguyên tinh thần learn.py của skill dựng video):
- Agent chỉ ĐỀ XUẤT; người giao việc duyệt rồi mới vào kho.
- Chỉ bài học lặp lại >= 2 lần hoặc được ghim mới thành quy tắc mặc định.
- critic chỉ học từ lỗi mà người duyệt bắt được còn critic bỏ sót (source = human_miss), để giữ tính độc lập.
- Đề xuất có vẻ cố đổi quy tắc cứng / bỏ bước duyệt bị loại.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .guardrails import is_suspicious


@dataclass
class Lesson:
    id: str
    text: str
    scope: str = "chung"
    kind: str = "quy_tac"
    count: int = 1
    pinned: bool = False
    source: str = "human"
    created: str = ""
    last_seen: str = ""
    video: str = ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


class LessonStore:
    def __init__(self, agent_dir: Path, agent: str):
        self.agent = agent
        self.active_path = Path(agent_dir) / "lessons.jsonl"
        self.pending_path = Path(agent_dir) / "lessons_pending.jsonl"

    # ── đọc/ghi ──
    @staticmethod
    def _read(path: Path) -> list[Lesson]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Lesson(**json.loads(line)))
        return out

    @staticmethod
    def _write(path: Path, items: list[Lesson]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(asdict(i), ensure_ascii=False) + "\n" for i in items), encoding="utf-8")

    def _next_id(self) -> str:
        nums = []
        for l in self._read(self.active_path) + self._read(self.pending_path):
            m = re.search(r"-(\d+)$", l.id)
            if m:
                nums.append(int(m.group(1)))
        return f"{self.agent}-{max(nums, default=0) + 1}"

    # ── đề xuất / duyệt ──
    def propose(self, text: str, scope: str = "chung", kind: str = "quy_tac", source: str = "human",
                video: str = "") -> Lesson | None:
        text = (text or "").strip()
        if not text or len(text) > 600:
            return None
        if self.agent == "critic" and source != "human_miss":
            return None
        if is_suspicious(text):
            return None
        now = datetime.now().isoformat(timespec="seconds")
        les = Lesson(id=self._next_id(), text=text, scope=scope, kind=kind, source=source,
                     created=now, last_seen=now, video=video)
        pend = self._read(self.pending_path)
        pend.append(les)
        self._write(self.pending_path, pend)
        return les

    def pending(self) -> list[Lesson]:
        return self._read(self.pending_path)

    def approve(self, ids: list[str], pin_ids: list[str] | None = None) -> list[Lesson]:
        pin_ids = set(pin_ids or [])
        pend, keep, approved = self._read(self.pending_path), [], []
        active = self._read(self.active_path)
        now = datetime.now().isoformat(timespec="seconds")
        for les in pend:
            if les.id not in ids:
                keep.append(les)
                continue
            twin = next((a for a in active if a.scope == les.scope and _norm(a.text) == _norm(les.text)), None)
            if twin:
                twin.count += 1
                twin.last_seen = now
                twin.pinned = twin.pinned or les.id in pin_ids
                approved.append(twin)
            else:
                les.pinned = les.id in pin_ids
                les.last_seen = now
                active.append(les)
                approved.append(les)
        self._write(self.active_path, active)
        self._write(self.pending_path, keep)
        return approved

    def reject(self, ids: list[str]) -> None:
        self._write(self.pending_path, [l for l in self._read(self.pending_path) if l.id not in ids])

    # ── dùng ──
    def active(self, video_type: str | None = None, brand: str | None = None) -> list[Lesson]:
        out = []
        for l in self._read(self.active_path):
            if not (l.pinned or l.count >= 2):
                continue
            if l.scope == "chung" or l.scope == f"dang:{video_type}" or l.scope == f"brand:{brand}":
                out.append(l)
        return out

    def digest(self, video_type: str | None = None, brand: str | None = None) -> str:
        rows = self.active(video_type, brand)
        if not rows:
            return ""
        return "\n".join(f"- [{l.scope}] {l.text}" for l in rows)
