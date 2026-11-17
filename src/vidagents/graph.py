"""Lớp nối LangGraph (mỏng). Toàn bộ logic nằm ở nodes.py + spec.py nên test được không cần LangGraph.

CHƯA ĐƯỢC CHẠY THỬ với LangGraph thật trong môi trường đã dựng khung (không cài được gói). Chạy `vidagents demo` để kiểm chứng.
"""
from __future__ import annotations

from pathlib import Path

from .nodes import Deps, make_nodes
from .spec import END, ENTRY, build_spec
from .state import JobState


def open_checkpointer(path: Path):
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


def build_graph(deps: Deps, checkpointer):
    from langgraph.graph import END as LG_END
    from langgraph.graph import START, StateGraph

    nodes = make_nodes(deps)
    plain, cond = build_spec(deps.cfg)
    g = StateGraph(JobState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, ENTRY)
    to = lambda t: LG_END if t == END else t  # noqa: E731
    for src, dst in plain.items():
        g.add_edge(src, to(dst))
    for src, (router, targets) in cond.items():
        g.add_conditional_edges(src, router, {t: to(t) for t in targets})
    return g.compile(checkpointer=checkpointer)


def production_interrupt():
    from langgraph.types import interrupt

    return interrupt


def pending_interrupts(graph, config: dict) -> tuple[list[dict], bool]:
    """(các yêu cầu đang chờ người trả lời, còn bước phải chạy không)."""
    snap = graph.get_state(config)
    out = []
    for t in getattr(snap, "tasks", ()) or ():
        for i in getattr(t, "interrupts", ()) or ():
            out.append(i.value)
    return out, bool(getattr(snap, "next", ()))
