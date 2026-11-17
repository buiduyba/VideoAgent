"""API: tạo lần chạy, phát sự kiện realtime (SSE), nhận quyết định duyệt."""
import asyncio
import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import llm
from .graph import graph
from .tracker import AGENTS, REG, Tracker

app = FastAPI(title="Agent Hub")
STATIC = Path(__file__).resolve().parent / "static"


class RunReq(BaseModel):
    brief: str


class ApproveReq(BaseModel):
    approved: bool = True


def _drive(tr: Tracker, payload):
    """Chạy đồ thị trong luồng riêng; dừng tại interrupt() chờ người duyệt."""
    cfg = {"configurable": {"thread_id": tr.run_id}}
    try:
        for _ in graph.stream(payload, cfg, stream_mode="updates"):
            pass
        if not graph.get_state(cfg).next and tr.status == "running":
            tr.set_run("done")
    except Exception as e:  # noqa: BLE001
        tr.emit("supervisor", "failed", None, "Lỗi", log=f"{type(e).__name__}: {e}")
        tr.set_run("error")


@app.get("/api/meta")
def meta():
    return {"mode": llm.mode(), "agents": [{"id": i, "name": n, "role": r} for i, n, r in AGENTS]}


@app.post("/api/runs")
def create_run(req: RunReq):
    if not req.brief.strip():
        raise HTTPException(400, "Brief trống")
    tr = Tracker(req.brief.strip())
    threading.Thread(target=_drive, args=(tr, {"brief": tr.brief}), daemon=True).start()
    return {"run_id": tr.run_id}


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, req: ApproveReq):
    tr = REG.get(run_id)
    if not tr:
        raise HTTPException(404, "Không có lần chạy này")
    if tr.status != "waiting_approval":
        raise HTTPException(409, "Lần chạy không ở trạng thái chờ duyệt")
    threading.Thread(target=_drive, args=(tr, Command(resume={"approved": req.approved})),
                     daemon=True).start()
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str):
    tr = REG.get(run_id)
    if not tr:
        raise HTTPException(404, "Không có lần chạy này")

    async def gen():
        i = 0
        while True:
            while i < len(tr.events):
                yield f"data: {json.dumps(tr.events[i], ensure_ascii=False)}\n\n"
                i += 1
            if tr.finished and i >= len(tr.events):
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
