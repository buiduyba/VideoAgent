"""Đồ thị LangGraph: Supervisor -> Researcher -> Writer -> Video -> Reviewer (-> sửa) -> Duyệt -> Publisher."""
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import llm
from .tracker import REG

VAULT = Path(os.getenv("VAULT_DIR", Path(__file__).resolve().parent.parent / "vault"))
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
MAX_REVISIONS = 2
DEFAULT_BANNED = ["số 1", "tốt nhất", "cam kết 100%"]


class State(TypedDict, total=False):
    brief: str
    plan: list[str]
    angles: str
    script: str
    video: str
    review: dict
    revisions: int
    approved: bool


def _tr(config):
    return REG[config["configurable"]["thread_id"]]


def _vault_read(rel: str):
    p = VAULT / rel
    return p.read_text(encoding="utf-8") if p.exists() else None


def banned_words() -> list[str]:
    """Từ cấm lấy từ Obsidian (SOP/tu-cam.txt, mỗi dòng một từ); không có thì dùng mặc định."""
    txt = _vault_read("SOP/tu-cam.txt")
    if not txt:
        return DEFAULT_BANNED
    return [w.strip().lower() for w in txt.splitlines() if w.strip()]


# ---------------------------------------------------------------- nodes
def supervisor(state: State, config):
    tr = _tr(config)
    tr.emit("supervisor", "running", 15, "Phân tích brief", "Claude",
            log="Nhận brief: " + state["brief"][:90])
    mock = ("Tìm 3 góc nội dung từ xu hướng\nViết kịch bản video 45 giây\n"
            "Dựng video dọc 9:16\nKiểm duyệt từ cấm và độ dài\nDuyệt và đăng Reels, TikTok")
    text, tok = llm.ask(
        "Bạn là điều phối viên marketing. Chia việc ngắn gọn.",
        f"Brief: {state['brief']}\nLập kế hoạch đúng 5 tác vụ, mỗi dòng một tác vụ, không đánh số.",
        mock)
    plan = [ln.strip("-• 0123456789.").strip() for ln in text.splitlines() if ln.strip()][:6]
    tr.emit("supervisor", "done", 100, f"Đã chia {len(plan)} tác vụ", tokens=tok,
            log="Giao việc cho 5 agent",
            artifact={"title": "Kế hoạch", "text": "\n".join(f"{i+1}. {p}" for i, p in enumerate(plan))})
    return {"plan": plan, "revisions": 0}


def researcher(state: State, config):
    tr = _tr(config)
    tr.emit("researcher", "running", 10, "Đọc SOP trong vault", "Obsidian")
    sop = _vault_read("SOP/hoi-cho.md")
    tr.emit("researcher", progress=35,
            log="Đọc SOP/hoi-cho.md" if sop else "Không thấy SOP/hoi-cho.md, dùng mặc định")
    tr.emit("researcher", progress=45, task="Tìm góc nội dung", tool="Claude")
    mock = ("1. Cận cảnh sản phẩm trong 3 giây đầu\n2. Khách trải nghiệm thật tại gian hàng\n"
            "3. Ưu đãi chỉ có tại hội chợ")
    text, tok = llm.ask(
        "Bạn là chuyên viên nghiên cứu nội dung marketing.",
        f"Brief: {state['brief']}\nSOP:\n{sop or '(không có)'}\nĐề xuất 3 góc nội dung ngắn.", mock)
    tr.emit("researcher", "done", 100, "Chọn 3 góc nội dung", tokens=tok,
            log="Chọn 3 góc phù hợp", artifact={"title": "3 góc nội dung", "text": text})
    return {"angles": text}


def writer(state: State, config):
    tr = _tr(config)
    n = state.get("revisions", 0)
    issues = state.get("review", {}).get("issues", [])
    task = f"Viết kịch bản bản {n + 1}" if n == 0 else f"Sửa kịch bản theo góp ý (lần {n})"
    tr.emit("writer", "running", 10, task, "Claude",
            log="Nhận 3 góc từ Researcher" if n == 0 else "Sửa theo: " + "; ".join(issues))
    if n == 0:  # bản nháp đầu cố tình có từ cấm để thấy vòng sửa chạy khi mô phỏng
        mock = ("Cảnh 1: Cận cảnh sản phẩm, lời dẫn: 'Sản phẩm số 1 mà ai cũng muốn thử'.\n"
                "Cảnh 2: Khách trải nghiệm tại gian hàng, nụ cười thật.\n"
                "Cảnh 3: Ưu đãi hội chợ, chốt: 'Chất lượng tốt nhất, đến ngay hôm nay'.")
    else:
        mock = ("Cảnh 1: Cận cảnh sản phẩm, lời dẫn: 'Sản phẩm được khách ghé gian hàng chọn nhiều nhất'.\n"
                "Cảnh 2: Khách trải nghiệm tại gian hàng, nụ cười thật.\n"
                "Cảnh 3: Ưu đãi chỉ có tại hội chợ, ghé gian hàng hôm nay.")
    tr.emit("writer", progress=45)
    extra = f"\nBản trước bị góp ý: {issues}. Sửa lại." if issues else ""
    text, tok = llm.ask(
        "Bạn là copywriter video ngắn. Viết 3 cảnh, tiếng Việt, tổng khoảng 45 giây.",
        f"Brief: {state['brief']}\nGóc nội dung:\n{state.get('angles','')}{extra}", mock)
    tr.emit("writer", "done", 100, f"Xong kịch bản bản {n + 1}", tokens=tok,
            log="Chốt kịch bản", artifact={"title": f"Kịch bản bản {n + 1}", "text": text})
    return {"script": text}


def video(state: State, config):
    tr = _tr(config)
    n = state.get("revisions", 0) + 1
    steps = ["Chấm cảnh và cắt theo beat", "Ghép phụ đề, cân màu", f"Render bản nhẹ v{n}"]
    tr.emit("video", "running", 5, steps[0], "HyperFrames")
    for i, s in enumerate(steps):
        tr.emit("video", progress=int((i + 1) / len(steps) * 90) if i < 2 else 90, task=s, log=s)
        time.sleep(1.0)  # chỗ này sau sẽ gọi pipeline dựng video thật
    f = f"out/video_v{n}.mp4"
    tr.emit("video", "done", 100, f"Đã render v{n}", log=f"Xuất {f} (mô phỏng, chưa dựng thật)",
            artifact={"title": f"Video v{n}", "text": f"{f}\n(mô phỏng: nối pipeline dựng thật ở bước sau)"})
    return {"video": f}


def reviewer(state: State, config):
    tr = _tr(config)
    tr.emit("reviewer", "running", 20, "Soi từ cấm", "Obsidian",
            log="Nạp từ cấm từ SOP/tu-cam.txt" if _vault_read("SOP/tu-cam.txt")
            else "Không thấy SOP/tu-cam.txt, dùng danh sách từ cấm mặc định")
    time.sleep(0.8)
    script = state.get("script", "")
    issues = [f"Có từ cấm '{w}'" for w in banned_words() if w in script.lower()]
    tr.emit("reviewer", progress=60, task="Kiểm độ dài", tool="Python")
    words = len(script.split())
    if words > 140:
        issues.append(f"Kịch bản {words} từ, quá dài cho 45 giây")
    time.sleep(0.6)
    n = state.get("revisions", 0)
    if issues and n < MAX_REVISIONS:
        tr.emit("reviewer", "rework", 0, "Yêu cầu Writer sửa lại", tool="Claude",
                log="Không đạt: " + "; ".join(issues))
        for a, label in (("writer", "Chờ sửa"), ("video", "Chờ bản sửa")):
            tr.emit(a, "pending", 0, label)
        return {"review": {"ok": False, "final": False, "issues": issues}, "revisions": n + 1}
    note = "Đạt" if not issues else "Hết lượt sửa, chuyển cho người xem: " + "; ".join(issues)
    tr.emit("reviewer", "done", 100, note, log=note,
            artifact={"title": "Kết quả kiểm duyệt", "text": note})
    return {"review": {"ok": not issues, "final": True, "issues": issues}}


def approval(state: State, config):
    tr = _tr(config)
    if not tr.approval_announced:
        tr.approval_announced = True
        tr.emit("publisher", "approval", 60, "Chờ anh duyệt trước khi đăng", "n8n",
                log="Gói video và caption đã gửi về dashboard")
        tr.set_run("waiting_approval")
    decision = interrupt({"video": state.get("video"), "script": state.get("script", "")[:300]})
    tr.set_run("running")
    return {"approved": bool(decision.get("approved"))}


def cancel(state: State, config):
    tr = _tr(config)
    tr.emit("publisher", "failed", None, "Anh từ chối, dừng đăng", log="Không đăng bài")
    tr.set_run("cancelled")
    return {}


def publisher(state: State, config):
    tr = _tr(config)
    tr.emit("publisher", "running", 70, "Gửi sang n8n để đăng", "n8n", log="Anh đã duyệt")
    payload = {"video": state.get("video"), "script": state.get("script"), "brief": state["brief"]}
    if N8N_WEBHOOK_URL:
        req = urllib.request.Request(N8N_WEBHOOK_URL, json.dumps(payload).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        tr.emit("publisher", progress=85, log="n8n nhận webhook")
    else:
        time.sleep(1.0)
        tr.emit("publisher", progress=85, log="Chưa đặt N8N_WEBHOOK_URL, bỏ qua bước gọi n8n (mô phỏng)")
    tr.emit("publisher", progress=92, task="Ghi nhật ký vào Obsidian", tool="Obsidian")
    note = VAULT / "Runs" / f"{time.strftime('%Y-%m-%d')}-{tr.run_id}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        f"---\nrun: {tr.run_id}\nngày: {time.strftime('%Y-%m-%d %H:%M')}\ntrạng thái: đã đăng\n---\n"
        f"# {state['brief'][:60]}\n\n## Kế hoạch\n" + "\n".join(f"- {p}" for p in state.get("plan", []))
        + f"\n\n## Kịch bản\n{state.get('script','')}\n\n## Video\n{state.get('video','')}\n"
        f"\n## Kiểm duyệt\n{state.get('review',{})}\n", encoding="utf-8")
    tr.emit("publisher", "done", 100, "Đã đăng và ghi nhật ký", log=f"Ghi {note.name}",
            artifact={"title": "Nhật ký Obsidian", "text": str(note)})
    return {}


# ---------------------------------------------------------------- graph
def build():
    g = StateGraph(State)
    for name, fn in [("supervisor", supervisor), ("researcher", researcher), ("writer", writer),
                     ("video", video), ("reviewer", reviewer), ("approval", approval),
                     ("cancel", cancel), ("publisher", publisher)]:
        g.add_node(name, fn)
    g.add_edge(START, "supervisor")
    g.add_edge("supervisor", "researcher")
    g.add_edge("researcher", "writer")
    g.add_edge("writer", "video")
    g.add_edge("video", "reviewer")
    g.add_conditional_edges("reviewer", _after_review, {"writer": "writer", "approval": "approval"})
    g.add_conditional_edges("approval", lambda s: "publisher" if s.get("approved") else "cancel",
                            {"publisher": "publisher", "cancel": "cancel"})
    g.add_edge("publisher", END)
    g.add_edge("cancel", END)
    return g.compile(checkpointer=InMemorySaver())


def _after_review(state: State):
    """Quay lại Writer khi Reviewer yêu cầu sửa; hết lượt sửa thì chuyển cho người duyệt."""
    r = state.get("review", {})
    return "writer" if (not r.get("ok") and not r.get("final")) else "approval"


graph = build()
