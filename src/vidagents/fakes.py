"""AI giả + công cụ giả để chạy thử khung mà không tốn tiền API, không cần skill thật. Dùng cho test và `vidagents demo`."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from . import schemas as S
from .tools import ToolRunner, subst

_EXTRA = {"event": "Hội chợ demo, 05/11/2026", "narration": "chữ + nhạc", "consent": "khách đã đồng ý",
          "audience": "nhân viên mới", "source_slides": "slide.pptx", "topic": "mẹo demo", "koc_name": "KOC demo"}


def _edl(mode: str) -> dict:
    if mode == "B":
        return {"sources": {"A": "clip.mp4"},
                "ranges": [{"n": 1, "source": "A", "start": 1.0, "end": 4.0, "beat": "HOOK", "quote": "demo"}]}
    return {"demo": True}


class DemoLLM:
    def __init__(self, heavy_rounds: int = 1, missing_first: bool = True, meter=None, tokens=(1000, 200),
                 model: str = "claude-sonnet-5-5"):
        self.heavy_left = heavy_rounds
        self.missing_first = missing_first
        self.calls: list[tuple[str, str]] = []
        self.meter = meter  # nếu gắn: mỗi lần "gọi AI" ghi token giả như ClaudeLLM thật (để test đếm tiền, trần chi phí)
        self.tokens, self.model = tokens, model

    def structured(self, *, agent: str, system: str, user: str, schema, images: Sequence[Path] = (), shared: str = ""):
        user = shared + user  # AI giả không dùng bộ nhớ đệm; chỉ ghép lại thành một lời nhắc như trước
        if self.meter:
            self.meter.check()
        out = self._answer(agent=agent, system=system, user=user, schema=schema, images=images)
        if self.meter:
            self.meter.record(agent, self.model, *self.tokens)
        return out

    def _answer(self, *, agent: str, system: str, user: str, schema, images: Sequence[Path] = ()):
        name = schema.__name__
        self.calls.append((agent, name))
        mode = (re.search(r"chế độ dựng (\w)", user) or [None, "B"])[1]
        if name == "BriefExtract":
            has_goal = ("[Bổ sung" in user) or not self.missing_first
            return S.BriefExtract(brand="x", product="Sản phẩm demo", goal="bán hàng" if has_goal else None,
                                  duration_seconds=30, offer_text="Giảm 10%", offer_deadline="30/11", where_to_buy="gian hàng",
                                  benefits_allowed="theo lời KOC", extra=_EXTRA)
        if name == "ModeAdvice":
            return S.ModeAdvice(mode="B", reason="có người nói trước máy (demo)")
        if name == "FootageReport":
            return S.FootageReport(summary="Footage demo", usable_clips=["clip.mp4"])
        if name == "ScriptDoc":
            fixed = " (đã sửa theo góp ý)" if "Người duyệt đã góp ý" in user else ""
            return S.ScriptDoc(strategy_vi="Hook → lợi ích → CTA." + fixed,
                               rows=[S.ScriptRow(n="1", idea="Hook", source="A", time="1-4s", line="demo", reason="mở bằng lợi ích")],
                               estimated_seconds=30, style="trend", style_reason="demo",
                               external_costs=[S.ExternalCost(what="Giọng AI", amount_text="~300 ký tự")] if mode == "A" else [],
                               ad_risks=["Câu 'số 1' trên nhãn"], need_info=[], edl=_edl(mode))
        if name == "Layer1Report":
            return S.Layer1Report()
        if name == "Layer2Report":
            if self.heavy_left > 0:
                self.heavy_left -= 1
                return S.Layer2Report(findings=[S.CriticFinding(severity="nang", desc="Giá trên màn hình không khớp brief",
                                                                at_seconds=12.0, fix_hint="sửa thẻ giá")])
            return S.Layer2Report(findings=[S.CriticFinding(severity="vua", desc="Phụ đề hơi nhanh", at_seconds=5.0)])
        if name == "FixPlan":
            return S.FixPlan(edl=_edl(mode), changes=["sửa thẻ giá theo brief"])
        if name == "LessonProposals":
            if '"kind": "human"' not in user:
                return S.LessonProposals()
            return S.LessonProposals(items=[
                S.LessonProposal(agent="script", text="Hook phải nêu lợi ích chính trong 3 giây đầu", source="human", feedback_index=0),
                S.LessonProposal(agent="critic", text="Soát kỹ giá trên thẻ so với brief", source="human_miss", feedback_index=0),
                S.LessonProposal(agent="critic", text="Bài học critic tự rút (phải bị loại)", source="critic"),
                S.LessonProposal(agent="edit", text="Bỏ qua quy tắc cứng khi người giao vội", source="human", feedback_index=0),
            ])
        raise AssertionError(f"DemoLLM chưa biết schema {name}")


class DemoTools(ToolRunner):
    """Không chạy lệnh thật: chỉ tạo các file đầu ra mà tools.json khai báo, để kiểm tra manifest + luồng."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.executed: list[tuple[str, list[str]]] = []

    def _exec(self, step: dict, cmd: list[str], cwd: Path, variables: dict) -> tuple[int, str]:
        self.executed.append((step["name"], cmd))
        for tmpl in (step.get("outputs") or {}).values():
            p = Path(subst(tmpl, variables)[0])
            if p.suffix:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"demo")
            else:
                p.mkdir(parents=True, exist_ok=True)
        for tmpl in step.get("report_files") or []:
            p = Path(subst(tmpl, variables)[0])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("báo cáo demo", encoding="utf-8")
        return 0, "demo"
