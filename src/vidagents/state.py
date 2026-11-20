"""Trạng thái một job (LangGraph state). Mỗi job = một thread_id = mã job."""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict

# Các khóa dạng "cộng dồn" (reducer). MiniRunner trong testing.py dùng cùng danh sách này.
REDUCE_ADD = ("steps", "feedback")


class JobState(TypedDict, total=False):
    # danh tính job
    job_id: str
    requester_id: str
    requester_name: str
    source_dir: str  # footage gốc trên NAS (chỉ đọc, và nhận kết quả cuối)
    work_dir: str  # bản chép riêng của job trên máy chung
    footage_dir: str  # thư mục skill làm việc (= work_dir sau intake)
    edit_dir: str
    brand: str
    video_type: str
    video_name: str
    created_date: str
    # chế độ dựng
    mode: str
    mode_reason: str
    mode_override: str
    mode_changed: bool
    style: str
    # brief + hỏi thêm thông tin
    brief_text: str
    brief: dict
    brief_prefill: dict  # brief đã đọc từ phiếu điền sẵn (khỏi gọi AI trích); chỉ dùng cho lần trích đầu tiên
    info_request: list
    after_info: str
    info_rounds: int
    # kết quả của cổng duyệt gần nhất ("approve", "revise", "cancel", "ignored", "continue", "answer")
    gate_result: str
    cancelled: bool
    cancel_reason: str
    approvals: dict
    human_revisions: int
    # critic
    critic_stage: str  # "preview" | "final"
    critic_fix_rounds: int
    critic_has_heavy: bool
    hand_edited: bool  # bản cuối do người chỉnh tay trong VideoFac (critic bỏ lớp 1 của skill, không có vòng AI tự sửa)
    critic_findings: list
    fix_source: str  # "critic" | "human"
    escalation: Optional[dict]
    resume_to: str
    # đầu ra, bài học, trạng thái
    outputs: dict
    lesson_items: list
    status: str
    stage: str
    feedback: Annotated[list, operator.add]
    steps: Annotated[list, operator.add]
