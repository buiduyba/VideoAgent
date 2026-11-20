"""Chạy lệnh của skill. Lệnh khai báo trong agents/<tên>/tools.json (đi cùng skill), KHÔNG viết cứng trong code.

An toàn: không dùng shell (danh sách tham số), chỉ thay các biến đã biết, thiếu biến thì báo lỗi.
Bước "heavy" giữ một suất render; bước có "requires" chỉ chạy khi đã có duyệt tương ứng (nguyên tắc 6).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .brand import brand_lock
from .config import Config
from .locks import LockTimeout, SlotLock


class ToolError(Exception):
    pass


class Blocked(ToolError):
    """Thiếu duyệt cần thiết trước khi chạy bước này."""


_LIST_TOKEN = re.compile(r"\{\*([A-Za-z_]\w*)\}")
_VAR = re.compile(r"\{([A-Za-z_]\w*)\}")


def subst(token: str, variables: dict) -> list[str]:
    m = _LIST_TOKEN.fullmatch(token)
    if m:
        val = variables.get(m.group(1))
        if not isinstance(val, list):
            raise ToolError(f"Biến danh sách {{*{m.group(1)}}} chưa có")
        return [str(x) for x in val]

    def rep(mm: re.Match) -> str:
        name = mm.group(1)
        if name not in variables:
            raise ToolError(f"Biến chưa biết trong tools.json: {{{name}}}")
        val = variables[name]
        if isinstance(val, list) or val in (None, ""):
            raise ToolError(f"Biến {{{name}}} chưa có giá trị (kiểm tra config)")
        return str(val)

    return [_VAR.sub(rep, token)]


@dataclass
class RunResult:
    steps: list[dict] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    reports: list[str] = field(default_factory=list)
    skipped: bool = False  # skill chưa khai báo lệnh cho (action, mode) này
    soft_failed: list[dict] = field(default_factory=list)  # bước có cờ "soft" bị hỏng: {name, why}


class ToolRunner:
    def __init__(self, cfg: Config, locks: SlotLock):
        self.cfg = cfg
        self.locks = locks
        self._cache: dict[str, dict] = {}

    # ── manifest ──
    def manifest(self, agent: str) -> dict:
        if agent not in self._cache:
            p = self.cfg.agents_dir / agent / "tools.json"
            self._cache[agent] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return self._cache[agent]

    def steps_for(self, agent: str, action: str, mode: str) -> list[dict]:
        return list(self.manifest(agent).get("actions", {}).get(action, {}).get(mode, []))

    def has(self, agent: str, action: str, mode: str) -> bool:
        return bool(self.steps_for(agent, action, mode))

    # ── chạy ──
    def run(self, agent: str, action: str, mode: str, variables: dict, *, cwd: Path, approvals: dict,
            owner: str, on_wait: Callable[[int], None] | None = None) -> RunResult:
        steps = self.steps_for(agent, action, mode)
        res = RunResult(skipped=not steps)
        Path(cwd).mkdir(parents=True, exist_ok=True)
        for st in steps:
            try:
                self._step(agent, action, st, variables, cwd=Path(cwd), approvals=approvals, owner=owner, on_wait=on_wait, res=res)
            except Blocked:
                raise  # thiếu duyệt không bao giờ được coi là "lỗi nhẹ"
            except ToolError as e:
                if not st.get("soft"):
                    raise
                # bước "soft" (vd bản xem trước): hỏng thì ghi lại để báo, job vẫn đi tiếp bằng các bước khác
                res.soft_failed.append({"name": st["name"], "why": str(e)[:500]})
        return res

    def _step(self, agent: str, action: str, st: dict, variables: dict, *, cwd: Path, approvals: dict, owner: str,
              on_wait: Callable[[int], None] | None, res: RunResult) -> None:
        req = st.get("requires")
        if req and not approvals.get(req):
            raise Blocked(f"Bước '{st['name']}' cần duyệt '{req}' trước")
        cmd = [t for tok in st["cmd"] for t in subst(tok, variables)]
        attempts = 2 if st.get("retry", True) else 1
        code, out = 1, ""
        for n in range(attempts):
            try:
                with ExitStack() as stack:
                    # thứ tự cố định: khóa thương hiệu (ngoài) -> suất render (trong), tránh khóa chéo giữa các job
                    if st.get("brand_lock"):
                        stack.enter_context(brand_lock(self.cfg, str(variables.get("brand", "")), owner))
                    if st.get("heavy"):
                        stack.enter_context(self.locks.hold(owner, timeout=self.cfg.lock_timeout_s, on_wait=on_wait))
                    code, out = self._exec(st, cmd, cwd, variables)
            except LockTimeout as e:
                raise ToolError(str(e)) from e
            self._log(cwd, agent, action, st["name"], n + 1, code, out)
            if code == 0:
                break
        if code != 0:
            raise ToolError(f"Lệnh '{st['name']}' lỗi (mã {code}): {out[-600:]}")
        for key, tmpl in (st.get("outputs") or {}).items():
            path = subst(tmpl, variables)[0]
            if not Path(path).exists() or (Path(path).is_file() and Path(path).stat().st_size == 0):
                if not st.get("optional"):
                    raise ToolError(f"Bước '{st['name']}' xong nhưng thiếu đầu ra '{key}': {path}")
            else:
                res.outputs[key] = path
        for tmpl in st.get("report_files") or []:
            path = subst(tmpl, variables)[0]
            if Path(path).exists():
                res.reports.append(path)
        res.steps.append({"name": st["name"], "code": code})

    def _exec(self, step: dict, cmd: list[str], cwd: Path, variables: dict) -> tuple[int, str]:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            # stdin=DEVNULL: lệnh con không bao giờ được chờ người gõ phím (vd. npx hỏi "Ok to proceed? (y)" khi cài gói lần đầu,
            # treo cả job vì bot chạy nền, không có ai trước màn hình). Không có stdin thì npx tự cài và đi tiếp.
            p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                               errors="replace", timeout=int(step.get("timeout", 3600)), env=env)
        except subprocess.TimeoutExpired:
            return 124, "hết thời gian chờ"
        except FileNotFoundError as e:
            return 127, f"không tìm thấy chương trình: {e}"
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    @staticmethod
    def _log(cwd: Path, agent: str, action: str, step: str, attempt: int, code: int, out: str) -> None:
        try:
            d = cwd / "logs"
            d.mkdir(exist_ok=True)
            (d / f"{agent}-{action}-{step}.log").write_text(f"lần {attempt} · mã {code}\n{out[-4000:]}", encoding="utf-8")
        except OSError:
            pass


# ───────────────── tiện ích dùng chung, không phụ thuộc skill ─────────────────
_MEDIA = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".wav", ".mp3", ".m4a"}


def probe_footage(cfg: Config, footage_dir: Path, limit: int = 200) -> list[dict]:
    """Kiểm kê nhanh bằng ffprobe (không đọc nội dung). Bỏ qua thư mục edit."""
    out: list[dict] = []
    base = Path(footage_dir)
    for p in sorted(base.rglob("*")):
        if len(out) >= limit:
            break
        if "edit" in p.relative_to(base).parts[:-1] or not p.is_file() or p.suffix.lower() not in _MEDIA:
            continue
        item = {"file": str(p.relative_to(base)), "size_mb": round(p.stat().st_size / 1e6, 1)}
        try:
            r = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                                "-of", "json", str(p)], capture_output=True, text=True, timeout=60)
            info = json.loads(r.stdout or "{}")
            item["duration_s"] = round(float(info.get("format", {}).get("duration", 0)), 1)
            v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            if v:
                item["width"], item["height"] = v.get("width"), v.get("height")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            item["probe"] = "không đọc được"
        out.append(item)
    return out


def duration_of(cfg: Config, video: Path) -> float | None:
    try:
        r = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def extract_frames(cfg: Config, video: Path, times: list[float], outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i, t in enumerate(times):
        dst = outdir / f"khung_{i:02d}_{t:.1f}s.jpg"
        try:
            subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=540:-2", str(dst)], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if dst.exists() and dst.stat().st_size > 0:
            out.append(dst)
    return out
