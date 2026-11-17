"""Nạp kỹ năng của từng agent từ agents/<tên>/SKILL.md (+ bài học). Đổi file markdown = đổi hành vi, không cần sửa code."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import Config
from .guardrails import HARD_RULES_VI
from .lessons import LessonStore

AGENTS = ("orchestrator", "script", "footage", "edit", "critic")
_FRONT = re.compile(r"\A---\n.*?\n---\n", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


class SkillLoader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def dir(self, agent: str) -> Path:
        return self.cfg.agents_dir / agent

    def text(self, agent: str) -> str:
        p = self.dir(agent) / "SKILL.md"
        if not p.exists():
            return ""
        txt = _FRONT.sub("", p.read_text(encoding="utf-8"))
        return _COMMENT.sub("", txt).strip()  # chú thích HTML là ghi chú cho người, không gửi cho AI

    def version(self, agent: str) -> str:
        """Dấu vân tay của skill (SKILL.md + tools.json) để ghi vào nhật ký job và so sánh khi chạy bộ mẫu."""
        h = hashlib.sha1()
        for name in ("SKILL.md", "tools.json"):
            p = self.dir(agent) / name
            if p.exists():
                h.update(p.read_bytes())
        return h.hexdigest()[:10]

    def store(self, agent: str) -> LessonStore:
        return LessonStore(self.dir(agent), agent)

    def system_prompt(self, agent: str, state: dict) -> str:
        parts = [HARD_RULES_VI, f"# KỸ NĂNG CỦA AGENT '{agent}'\n{self.text(agent) or '(chưa có SKILL.md)'}"]
        digest = self.store(agent).digest(state.get("video_type"), state.get("brand"))
        if digest:
            parts.append(f"# BÀI HỌC ĐÃ ĐƯỢC DUYỆT (áp dụng)\n{digest}")
        parts.append(f"(phiên bản skill: {self.version(agent)})")
        return "\n\n".join(parts)
