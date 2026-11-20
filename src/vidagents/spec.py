"""Sơ đồ luồng dưới dạng dữ liệu thuần (không phụ thuộc LangGraph) để test được và chuyển thành graph ở graph.py.

intake → extract_brief ⇄ ask_info → scribe_gate → footage → script ⇄ ask_info → approve_1 → edit_build → critic ⇄ edit_fix
       → approve_2 → render → critic(final) → deliver → lessons → lesson_gate
       (approve_2 → /chinhtay → hand_edit → hand_edit_wait ⏸ /xong → hand_edit_done → critic(final, bỏ lớp 1 của skill)
        → deliver; có lỗi nặng thì hand_edit_review ⏸ → /duyet giao | /chinhtay chỉnh lại | /huy: nhánh chỉnh tay bằng VideoFac)
Mọi chỗ vượt giới hạn vòng lặp đều đi qua `escalate` (hỏi người giao), không tự quyết.
"""
from __future__ import annotations

from typing import Callable

from .config import Config

END = "__end__"
ENTRY = "intake"
VALID_RESUME = ("approve_2", "deliver", "edit_build", "render")


def build_spec(cfg: Config) -> tuple[dict[str, str], dict[str, tuple[Callable[[dict], str], list[str]]]]:
    """Trả (cạnh cố định, cạnh có điều kiện: tên -> (hàm chọn, danh sách đích có thể))."""

    def cancelled(s: dict) -> bool:
        return s.get("gate_result") == "cancel" or bool(s.get("cancelled"))

    def after_intake(s):
        return "cancel_end" if s.get("cancelled") else "extract_brief"

    def after_extract(s):
        if not s.get("info_request"):
            return "scribe_gate"
        return "cancel_end" if s.get("info_rounds", 0) >= cfg.max_info_rounds else "ask_info"

    def after_ask_info(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "ignored":
            return "ask_info"
        return s.get("after_info") or "extract_brief"

    def after_script(s):
        if s.get("info_request") and s.get("info_rounds", 0) < cfg.max_info_rounds:
            return "ask_info"
        return "approve_1"

    def after_approve_1(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "ignored":
            return "approve_1"
        if s.get("escalation"):
            return "escalate"
        if g == "approve":
            return "edit_build"
        return "scribe_gate" if s.get("mode_changed") else "script"

    def after_critic(s):
        if s.get("hand_edited"):   # bản chỉnh tay: có lỗi nặng thì hỏi người, không có AI tự sửa EDL
            return "hand_edit_review" if s.get("critic_has_heavy") else "deliver"
        if s.get("escalation"):
            return "escalate"
        if s.get("critic_has_heavy"):
            return "edit_fix"
        return "approve_2" if s.get("critic_stage") == "preview" else "deliver"

    def after_edit_fix(s):
        return "escalate" if s.get("escalation") else "critic"

    def after_approve_2(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "ignored":
            return "approve_2"
        if s.get("escalation"):
            return "escalate"
        if g == "handedit":
            return "hand_edit"
        return "render" if g == "approve" else "edit_fix"

    def after_hand_edit_wait(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        return "hand_edit_done" if g == "done" else "hand_edit_wait"

    def after_hand_edit_review(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "approve":
            return "deliver"
        return "hand_edit_wait" if g == "handedit" else "hand_edit_review"

    def after_escalate(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "ignored":
            return "escalate"
        to = s.get("resume_to")
        return to if to in VALID_RESUME else "approve_2"

    def after_scribe_gate(s):
        g = s.get("gate_result")
        if g == "cancel":
            return "cancel_end"
        if g == "ignored":
            return "scribe_gate"
        return "footage"

    def after_lessons(s):
        return "lesson_gate" if s.get("lesson_items") else END

    plain = {
        "footage": "script", "edit_build": "critic", "render": "critic", "deliver": "lessons",
        "hand_edit": "hand_edit_wait", "hand_edit_done": "critic",
        "lesson_gate": END, "cancel_end": END,
    }
    cond = {
        "intake": (after_intake, ["cancel_end", "extract_brief"]),
        "extract_brief": (after_extract, ["scribe_gate", "ask_info", "cancel_end"]),
        "scribe_gate": (after_scribe_gate, ["cancel_end", "scribe_gate", "footage"]),
        "ask_info": (after_ask_info, ["cancel_end", "ask_info", "extract_brief", "script"]),
        "script": (after_script, ["ask_info", "approve_1"]),
        "approve_1": (after_approve_1, ["cancel_end", "approve_1", "escalate", "edit_build", "scribe_gate", "script"]),
        "critic": (after_critic, ["escalate", "edit_fix", "approve_2", "deliver", "hand_edit_review"]),
        "edit_fix": (after_edit_fix, ["escalate", "critic"]),
        "approve_2": (after_approve_2, ["cancel_end", "approve_2", "escalate", "render", "edit_fix", "hand_edit"]),
        "hand_edit_wait": (after_hand_edit_wait, ["cancel_end", "hand_edit_wait", "hand_edit_done"]),
        "hand_edit_review": (after_hand_edit_review, ["cancel_end", "hand_edit_review", "deliver", "hand_edit_wait"]),
        "escalate": (after_escalate, ["cancel_end", "escalate", *VALID_RESUME]),
        "lessons": (after_lessons, ["lesson_gate", END]),
    }
    return plain, cond
