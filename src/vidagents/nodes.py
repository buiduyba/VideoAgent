"""Các node của graph. Mỗi node = một lớp mỏng: đọc skill -> gọi AI hoặc script -> kiểm khuôn -> ghi file/trạng thái.

Quy ước quan trọng (LangGraph chạy lại node từ đầu khi tiếp tục sau interrupt):
- Node "cổng duyệt" (ask_info, approve_1, approve_2, escalate, lesson_gate) KHÔNG có tác dụng phụ trước lệnh interrupt.
- Node nặng (footage, edit_*, render, critic) không chứa interrupt.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from . import factcheck as FC
from . import handedit as H
from . import projectassets as PA
from . import schemas as S
from . import workspace as W
from .config import Config
from .guardrails import wrap_data
from .jobs import JobStore
from .llm import LLM
from .notify import Notifier
from .skills import SkillLoader
from .tools import ToolRunner, duration_of, extract_frames, probe_footage
from .usage import UsageMeter, job_context

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")


@dataclass
class Deps:
    cfg: Config
    llm: LLM
    tools: ToolRunner
    notifier: Notifier
    jobs: JobStore
    skills: SkillLoader
    types: dict
    interrupt: Callable[[dict], Any]  # sản xuất: langgraph.types.interrupt; test: hàng đợi giả
    now: Callable[[], datetime] = datetime.now
    meter: UsageMeter | None = None  # đếm token/tiền AI theo job + trần chi phí


# ───────────────────────── tiện ích file ─────────────────────────
def _read(path: Path, limit: int = 20000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _versioned_write(path: Path, text: str) -> None:
    """Ghi file, bản cũ đổi thành <tên>-vN (giống quy ước edl-vN.json của skill)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        n = 1
        while path.with_name(f"{path.stem}-v{n}{path.suffix}").exists():
            n += 1
        path.rename(path.with_name(f"{path.stem}-v{n}{path.suffix}"))
    path.write_text(text, encoding="utf-8")


def render_script_md(doc: S.ScriptDoc, state: dict, vt: S.VideoType) -> str:
    out = [f"# Kịch bản / chiến lược · {vt.name_vi}", "",
           f"Chế độ: {state.get('mode')} ({state.get('mode_reason', '')})",
           f"Độ dài ước tính: {doc.estimated_seconds or '?'} s · Phong cách: {doc.style or 'mặc định'} {doc.style_reason}", "",
           doc.strategy_vi, "", "| # | ý | nguồn | thời điểm | lời | lý do |", "|---|---|---|---|---|---|"]
    for r in doc.rows:
        out.append(f"| {r.n} | {r.idea} | {r.source} | {r.time} | {r.line} | {r.reason} |")
    if doc.external_costs:
        out += ["", "## Chi phí ngoài"] + [f"- {c.what}: {c.amount_text}" for c in doc.external_costs]
    if doc.ad_risks:
        out += ["", "## Rủi ro quảng cáo"] + [f"- {x}" for x in doc.ad_risks]
    if doc.need_info:
        out += ["", "## CẦN HỎI"] + [f"- {x}" for x in doc.need_info]
    return "\n".join(out)


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def brand_kits(cfg: Config) -> list[str]:
    """Thương hiệu đã có bộ nhận diện (`<projects_root>/_brands/<tên>/brand.json`)."""
    d = cfg.projects_root / "_brands"
    try:
        return sorted(p.parent.name for p in d.glob("*/brand.json") if SLUG.match(p.parent.name))
    except OSError:
        return []


def check_request(cfg: Config, types, footage_dir: str, video_type: str, brand: str) -> list[str]:
    """Kiểm yêu cầu giao việc TRƯỚC khi tạo job (bot gọi ngay khi nhận tin; intake gọi lại để chắc).
    Trả danh sách lỗi viết cho người giao việc đọc, kèm gợi ý sửa. Rỗng = hợp lệ. Không đọc nội dung file, không tốn tiền."""
    errs: list[str] = []
    p = Path(footage_dir)
    if p.is_file() or (not p.exists() and p.suffix == "" and any(p.with_suffix(e).is_file() for e in (".MP4", ".mp4", ".MOV", ".mov"))):
        errs.append(f"'{p.name}' là một FILE video, không phải thư mục. Gửi thư mục chứa các clip, ví dụ: \"{p.parent}\" "
                    f"({W.media_summary(p.parent, depth=1) or 'không thấy media'})")
    elif not p.is_dir():
        hint = f" Thư mục cha \"{p.parent}\" có: {W.media_summary(p.parent, depth=1) or 'không thấy media'}." if p.parent.is_dir() else ""
        errs.append(f"Không tìm thấy thư mục footage \"{footage_dir}\".{hint}")
    else:
        try:
            if not _is_under(p.resolve(strict=True), cfg.footage_root.resolve()):
                errs.append(f"Thư mục footage nằm ngoài khu vực cho phép của máy (chỉ nhận thư mục trong {cfg.footage_root})")
        except OSError:
            errs.append(f"Không đọc được thư mục footage \"{footage_dir}\"")
    kits = brand_kits(cfg)
    if not SLUG.match(brand or ""):
        errs.append(f"Thương hiệu '{brand}' không hợp lệ: chỉ chữ thường không dấu, số, gạch nối. Đang có: {', '.join(kits) or '(chưa có)'}")
    elif brand not in kits:
        errs.append(f"Chưa có bộ nhận diện thương hiệu '{brand}' (logo, màu, nhạc, chữ cấm) trong {cfg.projects_root / '_brands'}. "
                    f"Đang có: {', '.join(kits) or '(chưa có)'}. Nhờ người quản trị tạo trước rồi giao lại.")
    if video_type not in types:
        errs.append(f"Loại video '{video_type}' không có. Chọn một: " + ", ".join(sorted(types)))
    return errs


def critic_summary(findings: list[dict]) -> str:
    heavy = [f for f in findings if f.get("severity") == "nang"]
    mid = [f for f in findings if f.get("severity") == "vua"]
    lines = [f"Critic: {len(heavy)} lỗi nặng · {len(mid)} lỗi vừa"]
    for f in (heavy + mid)[:5]:
        at = f" (giây {f['at_seconds']})" if f.get("at_seconds") is not None else ""
        tag = "NẶNG" if f.get("severity") == "nang" else "vừa"
        lines.append(f"  - [{tag}]{at} {f.get('desc', '')}")
    return "\n".join(lines)


# ───────────────────────── nhà máy node ─────────────────────────
def make_nodes(d: Deps) -> dict[str, Callable[[dict], dict]]:
    cfg = d.cfg

    def note(state: dict, text: str, files=()) -> None:
        d.notifier.send(state["requester_id"], f"[{state['job_id']}] {text}", files)

    def sync(state: dict, **kw) -> None:
        d.jobs.update(state["job_id"], **kw)

    def step(state: dict, name: str, agent: str | None = None, status: str = "ok", **extra) -> dict:
        s = {"name": name, "status": status, "at": d.now().isoformat(timespec="seconds"), **extra}
        if agent:
            s["skill_version"] = d.skills.version(agent)
        return s

    def vt_of(state: dict) -> S.VideoType:
        return d.types[state["video_type"]]

    def vars_for(state: dict, agent: str, **extra) -> dict:
        style = state.get("style") or ""
        name, date = state.get("video_name", ""), state.get("created_date", "")
        v = {
            "footage": state.get("footage_dir", ""), "edit_dir": state.get("edit_dir", ""),
            "brand": state.get("brand", ""), "name": name, "date": date, "mode": state.get("mode", ""),
            "PROJECTS": str(cfg.projects_root), "SKILL": str(cfg.skill_dir or ""),
            "AGENT_DIR": str(cfg.agents_dir / agent),
            "project_dir": str(cfg.projects_root / f"{date}-{name}"),
            "style_args": ["--style", style] if style else [],
            "ADAPTERS": str(cfg.adapters_dir), "FFMPEG": cfg.ffmpeg, "FFPROBE": cfg.ffprobe,
            "quality_args": ["--quality", cfg.preview_quality] if cfg.preview_quality else [],
        }
        v.update(extra)
        return v

    def on_wait_for(state: dict):
        return lambda busy: note(state, f"Máy đang bận ({busy} việc nặng). Job đã xếp hàng chờ suất render, sẽ tự chạy khi tới lượt.")

    def ask(state: dict, kind: str, text: str, files=()) -> S.Payload | None:
        """Cổng duyệt: chỉ người giao việc (hoặc admin khi HỦY) được trả lời. Người khác -> None (bỏ qua)."""
        raw = d.interrupt({"kind": kind, "job_id": state["job_id"], "to": str(state["requester_id"]),
                           "text": text, "files": [str(f) for f in files]})
        try:
            p = S.Payload.model_validate(raw)
        except ValidationError:
            return None
        if p.by == str(state["requester_id"]):
            return p
        if p.action == "cancel" and p.by in cfg.admin_ids:
            return p
        return None

    def stamp(p: S.Payload) -> dict:
        return {"by": p.by, "at": d.now().isoformat(timespec="seconds"), "note": p.note}

    # ───────────── 1. nhận job ─────────────
    def intake(state: dict) -> dict:
        # chạy lại intake (tiếp tục sau lỗi) thì nguồn vẫn là thư mục gốc trên NAS, không phải bản chép
        footage = Path(state.get("source_dir") or state["footage_dir"])
        errs = check_request(cfg, d.types, str(footage), state.get("video_type", ""), state.get("brand", ""))
        if not errs:
            rp = footage.resolve(strict=True)
        if errs:
            note(state, "Không nhận được job: " + "; ".join(errs))
            sync(state, status="cancelled", stage="intake", note="; ".join(errs))
            return {"cancelled": True, "cancel_reason": "; ".join(errs), "status": "cancelled", "stage": "intake"}
        vt = d.types[state["video_type"]]
        # Mỗi job một thư mục làm việc riêng: không ghi gì vào footage gốc, hai job cùng footage không đè nhau
        try:
            staged = W.stage_footage(
                cfg, state["job_id"], rp,
                on_start=lambda n, size: note(state, f"Đang chép {n} file ({W._human(size)}) từ NAS về máy chung để dựng.") if n else None)
        except W.StageError as e:
            note(state, f"Không nhận được job: {e}")
            sync(state, status="cancelled", stage="intake", note=str(e)[:200])
            return {"cancelled": True, "cancel_reason": str(e), "status": "cancelled", "stage": "intake"}
        if staged.skipped_dirs:
            note(state, f"Lưu ý: bỏ qua {len(staged.skipped_dirs)} thư mục con ({', '.join(staged.skipped_dirs[:5])}). "
                        "Chỉ chép file ở cấp trên cùng của thư mục footage.")
        if staged.transcripts_reused:
            note(state, f"Dùng lại {staged.transcripts_reused} bản nhận dạng lời đã có của footage này (không gọi Scribe lại).")
        rp_src, rp = rp, staged.work_dir
        ed = rp / "edit"
        ed.mkdir(exist_ok=True)
        (ed / "brief.md").write_text(state.get("brief_text", ""), encoding="utf-8")
        try:   # tài nguyên của project (logo, ảnh sản phẩm, video AI): chép về edit\project-assets, KHÔNG nằm cấp trên cùng nên không tốn phí Scribe
            found = PA.collect(cfg, rp_src)
            if found.any():
                PA.stage(cfg, found, ed)
                note(state, f"Dùng tài nguyên của project: {found.summary()}." + "".join(f"\n⚠ {n}" for n in found.notes))
        except Exception as e:  # noqa: BLE001 - thiếu tài nguyên không được làm hỏng job: dựng như cũ, chỉ báo
            note(state, f"⚠ Không đọc được tài nguyên của project ({e.__class__.__name__}), dựng không có logo/ảnh/video AI của project.")
        seq = state["job_id"].rsplit("-", 1)[-1]
        name = state.get("video_name") or f"{state['brand']}-{vt.id}-{seq}"
        mode = state.get("mode_override") or ("" if vt.suggested_mode == "auto" else vt.suggested_mode)
        sync(state, status="running", stage="intake")
        note(state, f"Đã nhận job '{vt.name_vi}' (thương hiệu {state['brand']}). Đang đọc brief.")
        return {"source_dir": str(rp_src), "work_dir": str(rp), "footage_dir": str(rp), "edit_dir": str(ed),
                "video_name": name, "mode": mode,
                "created_date": d.now().strftime("%Y-%m-%d"), "status": "running", "stage": "intake",
                "steps": [step(state, "intake")]}

    # ───────────── 2. trích brief + hỏi thiếu ─────────────
    def extract_brief(state: dict) -> dict:
        vt = vt_of(state)
        if state.get("brief_prefill") and not state.get("info_rounds"):
            # phiếu brief điền sẵn đã được đọc bằng luật cố định lúc nhận: không gọi AI, không suy diễn
            brief = S.BriefExtract.model_validate(state["brief_prefill"])
        else:
            user = ("Trích các ô của brief từ nội dung bên dưới. Chỉ ghi điều có trong brief, ô nào không có thì để trống. "
                    "Tuyệt đối không tự điền giá, ưu đãi, lợi ích, CTA.\n"
                    f"Các ô riêng của dạng '{vt.name_vi}' đặt trong extra với khóa: "
                    f"{[k[6:] for k in vt.required_brief if k.startswith('extra.')]}\n\n"
                    + wrap_data("brief", state.get("brief_text", "")))
            brief = d.llm.structured(agent="orchestrator", system=d.skills.system_prompt("orchestrator", state),
                                     user=user, schema=S.BriefExtract)
        brief.brand = state["brand"]
        missing = S.missing_fields(brief, vt)
        S.dump_json(Path(state["edit_dir"]) / "brief.json", brief)
        return {"brief": brief.model_dump(), "info_request": missing, "after_info": "extract_brief", "stage": "brief",
                "steps": [step(state, "extract_brief", "orchestrator", missing=missing)]}

    def ask_info(state: dict) -> dict:
        vt = vt_of(state)
        req = state.get("info_request") or []
        labels = [S.label_of(k, vt) if state.get("after_info") == "extract_brief" else k for k in req]
        text = ("Cần bổ sung thông tin trước khi làm tiếp:\n" + "\n".join(f"  • {x}" for x in labels)
                + f"\n\nTrả lời: /bosung {state['job_id']} <nội dung>   ·   /huy {state['job_id']}")
        p = ask(state, "ask_info", text)
        if p is None or p.action not in ("answer", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy khi bổ sung thông tin",
                    "status": "cancelled"}
        if not p.note.strip():
            return {"gate_result": "ignored"}
        n = state.get("info_rounds", 0) + 1
        sync(state, status="running")
        return {"gate_result": "answer", "info_rounds": n, "status": "running",
                "brief_text": f"{state.get('brief_text', '')}\n\n[Bổ sung lần {n}]\n{p.note.strip()}"}

    # ───────────── 3. footage ─────────────
    # ───────────── 2b. cổng chi phí nhận dạng lời (Scribe tính tiền theo phút) ─────────────
    SCRIBE_VIDEO = (".mp4", ".mov", ".mkv", ".m4v")   # đuôi mà bước prep của skill đưa đi nhận dạng

    def scribe_need(state: dict) -> tuple[float, list[str], list[str]]:
        """(số phút phải gọi Scribe, clip cần nhận dạng, clip không đo được độ dài). Clip đã có transcript thì không tính."""
        foot, tdir = Path(state["footage_dir"]), Path(state["edit_dir"]) / "transcripts"
        mins, need, unknown = 0.0, [], []
        for f in sorted(foot.iterdir()):
            if f.is_file() and f.suffix.lower() in SCRIBE_VIDEO and not (tdir / f"{f.stem}.json").exists():
                need.append(f.name)
                dur = duration_of(cfg, f)
                if dur is None:   # không đo được: ước theo dung lượng ở 16 Mb/s (thấp hơn bitrate điện thoại/4K thường gặp -> ước dư, an toàn)
                    unknown.append(f.name)
                    dur = f.stat().st_size / 2_000_000
                mins += dur / 60
        return mins, need, unknown

    def scribe_gate(state: dict) -> dict:
        """Hỏi trước khi tốn tiền Scribe. Node cổng: chỉ đọc (ffprobe) trước interrupt, không ghi gì."""
        ap = state.get("approvals", {})
        mode = state.get("mode_override") or state.get("mode") or ""
        if ap.get("scribe_ok") or (mode and not d.tools.has("footage", "prep", mode)):
            return {"gate_result": "approve"}
        mins, need, unknown = scribe_need(state)
        limit = cfg.scribe_auto_max_minutes
        if not need or (limit is not None and mins <= limit):
            auto = {"by": "tự động", "at": d.now().isoformat(timespec="seconds"), "minutes": round(mins, 1),
                    "note": "không cần gọi Scribe" if not need else f"dưới ngưỡng {limit:g} phút"}
            return {"gate_result": "approve", "approvals": {**ap, "scribe_ok": auto}}
        jid = state["job_id"]
        lines = [f"CẦN DUYỆT CHI PHÍ NHẬN DẠNG LỜI (ElevenLabs Scribe, tính theo phút âm thanh)",
                 f"{len(need)} clip chưa có bản nhận dạng: khoảng {mins:.1f} phút"
                 + (f" (trong đó {len(unknown)} clip không đo được độ dài, ước theo dung lượng)" if unknown else "") + ".",
                 "  " + ", ".join(need[:8]) + (" …" if len(need) > 8 else "")]
        if not mode:
            lines.append("Chế độ dựng chưa chọn: nếu job dựng bằng chế độ không cần lời thì bước này không tốn gì.")
        lines += ["", f"/duyet {jid} để nhận dạng   ·   /huy {jid}"]
        sync(state, status="waiting_approval", stage="duyet_scribe")
        p = ask(state, "approval", "\n".join(lines))
        if p is None or p.action not in ("approve", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao không duyệt chi phí nhận dạng lời", "status": "cancelled"}
        sync(state, status="running", stage="duyet_scribe_ok")
        return {"gate_result": "approve", "status": "running",
                "approvals": {**ap, "scribe_ok": {**stamp(p), "minutes": round(mins, 1), "clips": need}},
                "steps": [step(state, "scribe_gate", minutes=round(mins, 1))]}

    def footage(state: dict) -> dict:
        vt, ed = vt_of(state), Path(state["edit_dir"])
        files = probe_footage(cfg, Path(state["footage_dir"]))
        S.dump_json(ed / "inventory_quick.json", files)
        sysmsg = d.skills.system_prompt("footage", state)
        mode, reason = state.get("mode_override") or state.get("mode") or "", state.get("mode_reason", "")
        if state.get("mode_override"):
            reason = "người giao chọn"
        elif mode:
            reason = f"theo dạng '{vt.name_vi}'" + (f" · {vt.mode_note}" if vt.mode_note else "")
        else:
            adv = d.llm.structured(agent="footage", system=sysmsg, schema=S.ModeAdvice,
                                   user="Chọn chế độ dựng A/B/C/S cho job này từ kiểm kê nhanh và brief.\n"
                                        + wrap_data("kiem_ke", json.dumps(files, ensure_ascii=False))
                                        + wrap_data("brief", state.get("brief_text", "")))
            mode, reason = adv.mode, adv.reason
        res = d.tools.run("footage", "prep", mode, vars_for(state, "footage"), cwd=ed, approvals=state.get("approvals", {}),
                          owner=state["job_id"], on_wait=on_wait_for(state))
        texts = "\n\n".join(f"## {Path(r).name}\n{_read(Path(r), 12000)}" for r in res.reports)
        rep = d.llm.structured(
            agent="footage", system=sysmsg, schema=S.FootageReport,
            user="Tóm tắt footage cho người viết kịch bản: clip dùng được, vấn đề chất lượng, thương hiệu khác lộ ra, "
                 "đoạn nói đáng chú ý.\n" + wrap_data("kiem_ke", json.dumps(files, ensure_ascii=False))
                 + wrap_data("bao_cao_prep", texts) + wrap_data("brief", state.get("brief_text", "")))
        if res.skipped:
            rep.issues.insert(0, f"Skill chưa khai báo lệnh chuẩn bị footage cho chế độ {mode}; chỉ có kiểm kê nhanh.")
        S.dump_json(ed / "footage_report.json", rep)
        sync(state, stage="footage")
        return {"mode": mode, "mode_reason": reason, "mode_changed": False, "stage": "footage",
                "steps": [step(state, "footage", "footage", mode=mode, prep_skipped=res.skipped)]}

    # ───────────── 4. kịch bản ─────────────
    def files_hint(state: dict) -> str:
        """Đường dẫn thật của các clip để AI ghi đúng vào edl.sources (chế độ B); nếu không, EDL ghi tên clip rồi dựng bị lỗi."""
        if state.get("mode") != "B":
            return ""
        clips = [p.as_posix() for p in S.clips_in(state["footage_dir"])]
        man = PA.load_manifest(state["edit_dir"])
        return ("\nTrong edl.sources dùng ĐÚNG các đường dẫn ở file_nguon (khóa A, B, C...). Mọi gaps phải có source là KHÓA trong sources, "
                "start là giây trong clip đó (hình nền dưới thẻ; chưa có ý thì lấy cuối đoạn trước thẻ) và seconds là số giây.\n"
                + wrap_data("file_nguon", json.dumps(clips, ensure_ascii=False))
                + (wrap_data("tai_nguyen_project", json.dumps({k: man[k] for k in ("logo", "anh_san_pham", "video_ai") if man.get(k)}, ensure_ascii=False))
                   + PA.usage_hint(man) if any(man.get(k) for k in ("logo", "anh_san_pham", "video_ai")) else ""))

    def edl_errors(state: dict, mode: str, edl: Any) -> list[str]:
        """Tự sửa lỗi máy móc của EDL (đường dẫn clip, nền thẻ) rồi kiểm khuôn + file nguồn có thật. Rỗng = dựng được."""
        if mode == "B" and isinstance(edl, dict):
            S.normalize_edl_b(edl, state["footage_dir"], state.get("brief"), PA.load_manifest(state["edit_dir"]))
        errs = S.validate_edl(mode, edl)
        if not errs and mode == "B":
            errs = S.missing_sources(edl)
        return errs

    def script(state: dict) -> dict:
        vt, ed, mode = vt_of(state), Path(state["edit_dir"]), state["mode"]
        fb = [f for f in state.get("feedback", []) if f.get("kind") == "human"]
        # shared = phần giống nhau giữa các lần gọi (gọi lại khi EDL sai khuôn, khi người duyệt góp ý...): được lưu bộ nhớ đệm
        shared = (f"Dạng video: {vt.name_vi} · độ dài gợi ý {vt.target_seconds or '?'} s · chế độ dựng {mode}.\n"
                  f"Ghi chú dạng: {vt.notes}\n"
                  + wrap_data("brief", state.get("brief_text", ""))
                  + wrap_data("brief_da_trich", _read(ed / "brief.json"))
                  + wrap_data("bao_cao_footage", _read(ed / "footage_report.json"))
                  + wrap_data("thuong_hieu", _read(cfg.projects_root / "_brands" / state["brand"] / "brand.json", 6000))
                  + files_hint(state))
        user = ""
        if fb:
            user += ("\nNgười duyệt đã góp ý, SỬA THEO các góp ý (mới nhất ở cuối):\n"
                     + wrap_data("gop_y", json.dumps([f["note"] for f in fb], ensure_ascii=False))
                     + wrap_data("ban_truoc", _read(ed / "script.json")))
        sysmsg = d.skills.system_prompt("script", state)

        def gen(extra: str = "") -> S.ScriptDoc:
            return d.llm.structured(agent="script", system=sysmsg, shared=shared, user=user + extra, schema=S.ScriptDoc)

        doc = gen()
        errs = edl_errors(state, mode, doc.edl)
        if errs:
            doc = gen("\n\nLần trước EDL sai khuôn: " + "; ".join(errs) + ". Sửa lại cho đúng khuôn.")
            errs = edl_errors(state, mode, doc.edl)
            if errs:
                raise S.SchemaError("EDL sai khuôn sau 2 lần: " + "; ".join(errs))
        style = doc.style if doc.style and SLUG.match(doc.style) else None
        _versioned_write(ed / "script.json", json.dumps(doc.model_dump(), ensure_ascii=False, indent=2))
        _versioned_write(ed / "edl.json", json.dumps(doc.edl, ensure_ascii=False, indent=2))
        (ed / "script.md").write_text(render_script_md(doc, {**state, "style": style}, vt), encoding="utf-8")
        sync(state, stage="script")
        return {"style": style or "", "info_request": doc.need_info, "after_info": "script", "stage": "script",
                "steps": [step(state, "script", "script", need_info=len(doc.need_info))]}

    # ───────────── 5. duyệt 1 ─────────────
    def approve_1(state: dict) -> dict:
        ed, vt = Path(state["edit_dir"]), vt_of(state)
        doc = S.ScriptDoc.model_validate_json(_read(ed / "script.json", 200000))
        lines = [f"DUYỆT 1 · {vt.name_vi}", f"Chế độ {state['mode']} ({state.get('mode_reason', '')}) · "
                 f"~{doc.estimated_seconds or '?'} s · phong cách: {doc.style or 'mặc định'}", "", doc.strategy_vi, ""]
        lines += [f"#{r.n} {r.idea} | {r.source} {r.time} | {r.line[:90]}" for r in doc.rows[:20]]
        if doc.external_costs:
            lines += ["", "CHI PHÍ NGOÀI (duyệt = đồng ý các khoản này):"] + [f"  • {c.what}: {c.amount_text}" for c in doc.external_costs]
        if doc.ad_risks:
            lines += ["", "RỦI RO QUẢNG CÁO:"] + [f"  • {x}" for x in doc.ad_risks]
        if state.get("info_request"):
            lines += ["", "CHƯA CÓ THÔNG TIN (không tự điền):"] + [f"  • {x}" for x in state["info_request"]]
        jid = state["job_id"]
        lines += ["", f"/duyet {jid}   ·   /sua {jid} <góp ý> [chedo=A|B|C]   ·   /huy {jid}"]
        sync(state, status="waiting_approval", stage="duyet_1")
        p = ask(state, "approval", "\n".join(lines), [ed / "script.md"])
        if p is None or p.action not in ("approve", "revise", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy ở bước duyệt 1", "status": "cancelled"}
        if p.action == "approve":
            ap = {**state.get("approvals", {}), "duyet_1": stamp(p),
                  "cost_ok": {**stamp(p), "items": [c.model_dump() for c in doc.external_costs]}}
            sync(state, status="running", stage="duyet_1_ok")
            return {"gate_result": "approve", "approvals": ap, "status": "running", "stage": "duyet_1_ok"}
        n = state.get("human_revisions", 0) + 1
        upd: dict = {"gate_result": "revise", "human_revisions": n, "fix_source": "human", "status": "running",
                     "feedback": [{"stage": "duyet_1", "by": p.by, "note": p.note, "kind": "human"}]}
        if p.mode and p.mode != state.get("mode"):
            upd.update(mode=p.mode, mode_override=p.mode, mode_changed=True)
        if n > cfg.max_revisions:
            upd["escalation"] = {"reason": f"Đã yêu cầu sửa kịch bản quá {cfg.max_revisions} lần", "on_continue": "edit_build"}
        return upd

    # ───────────── 6. dựng ─────────────
    def _build(state: dict, action: str) -> tuple[dict, dict]:
        ed = Path(state["edit_dir"])
        if state["mode"] == "B" and (ed / "edl.json").exists():
            # EDL đã duyệt nhưng có thể còn lỗi máy móc (đường dẫn clip, nền thẻ): sửa trước khi chạy lệnh dựng, ghi bản cũ thành edl-vN
            raw = (ed / "edl.json").read_text(encoding="utf-8")
            edl = json.loads(raw)
            if S.normalize_edl_b(edl, state["footage_dir"], state.get("brief"), PA.load_manifest(ed)):
                _versioned_write(ed / "edl.json", json.dumps(edl, ensure_ascii=False, indent=2))
            errs = S.validate_edl("B", edl) or S.missing_sources(edl)
            if errs:
                raise S.SchemaError("EDL chưa dựng được: " + "; ".join(errs))
        v = vars_for(state, "edit")
        res = d.tools.run("edit", action, state["mode"], v, cwd=ed, approvals=state.get("approvals", {}),
                          owner=state["job_id"], on_wait=on_wait_for(state))
        outs = dict(res.outputs)
        if v["name"]:  # bản xem trước cũ ứng với EDL cũ: không được lẫn vào lần dựng này
            (ed / f"{v['name']}-xemtruoc.mp4").unlink(missing_ok=True)
        pv = d.tools.run("edit", "preview", state["mode"], v, cwd=ed, approvals=state.get("approvals", {}),
                         owner=state["job_id"], on_wait=on_wait_for(state))
        outs.update(pv.outputs)
        merged = {**state.get("outputs", {}), **outs}
        if "preview" not in outs:
            merged.pop("preview", None)
        soft = [{"name": f["name"], "why": f["why"]} for f in res.soft_failed + pv.soft_failed]
        return merged, {"build_skipped": res.skipped, "preview_skipped": pv.skipped, "soft_failed": soft}

    def edit_build(state: dict) -> dict:
        if not state.get("approvals", {}).get("duyet_1"):
            raise PermissionError("Chưa có duyệt 1, không được dựng")
        sync(state, status="running", stage="build")
        outputs, flags = _build(state, "build")
        if flags["build_skipped"]:
            note(state, f"⚠ Skill chưa khai báo lệnh dựng cho chế độ {state['mode']}; chưa dựng được.")
        if flags["soft_failed"]:
            note(state, "⚠ Không tạo được bản xem trước (" + "; ".join(f"{f['name']}: {f['why'][:160]}" for f in flags["soft_failed"])
                 + "). Job vẫn đi tiếp; xem bằng HyperFrames Studio trên máy dựng.")
        return {"outputs": outputs, "critic_stage": "preview", "stage": "build",
                "steps": [step(state, "edit_build", "edit", **flags)]}

    def edit_fix(state: dict) -> dict:
        ed, mode = Path(state["edit_dir"]), state["mode"]
        source = state.get("fix_source", "critic")
        findings = state.get("critic_findings", []) if source == "critic" else []
        human = [f["note"] for f in state.get("feedback", [])[-3:] if f.get("kind") == "human"] if source == "human" else []
        user = ("Sửa EDL hiện tại để khắc phục các điểm dưới đây. Chỉ đổi những chỗ cần thiết; cái gì không sửa tự động được "
                "thì đưa vào unfixable.\n" + wrap_data("edl_hien_tai", _read(ed / "edl.json", 60000)) + files_hint(state)
                + wrap_data("loi_critic", json.dumps(findings, ensure_ascii=False))
                + wrap_data("gop_y_nguoi_duyet", json.dumps(human, ensure_ascii=False)))
        sysmsg = d.skills.system_prompt("edit", state)

        def gen(extra: str = "") -> S.FixPlan:
            return d.llm.structured(agent="edit", system=sysmsg, user=user + extra, schema=S.FixPlan)

        plan = gen()
        errs = edl_errors(state, mode, plan.edl)
        if errs:
            plan = gen("\n\nLần trước EDL sai khuôn: " + "; ".join(errs))
            errs = edl_errors(state, mode, plan.edl)
            if errs:
                raise S.SchemaError("EDL sau khi sửa sai khuôn: " + "; ".join(errs))
        rounds = state.get("critic_fix_rounds", 0) + (1 if source == "critic" else 0)
        stage_next = state.get("critic_stage", "preview")
        approvals = {k: v for k, v in state.get("approvals", {}).items() if k != "duyet_2"}  # sửa nội dung -> duyệt lại
        if not plan.changes:
            reason = "Không có cách sửa tự động: " + ("; ".join(plan.unfixable) or "không rõ")
            return {"critic_fix_rounds": rounds, "approvals": approvals, "stage": "edit_fix",
                    "escalation": {"reason": reason, "on_continue": "approve_2" if stage_next == "preview" else "deliver"},
                    "steps": [step(state, "edit_fix", "edit", status="no_change")]}
        _versioned_write(ed / "edl.json", json.dumps(plan.edl, ensure_ascii=False, indent=2))
        action = "rebuild" if d.tools.has("edit", "rebuild", mode) else "build"
        sync(state, status="running", stage="fix")
        outputs, flags = _build(state, action)
        if flags["soft_failed"]:
            note(state, "⚠ Không tạo được bản xem trước sau khi sửa (" + "; ".join(f"{f['name']}: {f['why'][:160]}" for f in flags["soft_failed"]) + ").")
        return {"outputs": outputs, "critic_fix_rounds": rounds, "approvals": approvals, "critic_stage": "preview",
                "stage": "edit_fix", "escalation": None,
                "steps": [step(state, "edit_fix", "edit", changes=plan.changes, unfixable=plan.unfixable, **flags)]}

    # ───────────── 7. critic (2 lớp, nguyên tắc 3) ─────────────
    def _factcheck(state: dict) -> FC.FactCheck:
        ed = Path(state["edit_dir"])
        if state.get("hand_edited"):
            # bản chỉnh tay: edl/overlays/captions của skill đã cũ (người sửa trong VideoFac), chỉ phụ đề .srt của bản xuất là chữ thật
            outs = state.get("outputs", {})
            srt = Path(outs["srt"]) if outs.get("srt") else None
            truth = FC.load_truth(state.get("brief_text", ""), ed, cfg.projects_root / "_brands" / state["brand"] / "brand.json")
            truth += FC.strings_of(state.get("brief"))
            res = FC.check(truth_texts=truth, displayed=FC.load_displayed(Path(outs.get("final", ed)).parent, None, srt), script_lines=[],
                           brief=state.get("brief"), vt=vt_of(state))
            if not srt:
                res.notes.append("Bản xuất không kèm phụ đề .srt nên chưa đối chiếu được giá/ưu đãi trong chữ.")
            return res
        v = vars_for(state, "critic")
        proj = Path(v["project_dir"])
        name = state.get("video_name", "")
        srt = next((p for p in (ed / f"{name}.srt", proj / f"{name}.srt") if name and p.exists()), None)
        truth = FC.load_truth(state.get("brief_text", ""), ed, cfg.projects_root / "_brands" / state["brand"] / "brand.json")
        truth += FC.strings_of(state.get("brief"))
        lines: list[str] = []
        try:
            lines = [r.line for r in S.ScriptDoc.model_validate_json(_read(ed / "script.json", 200000)).rows]
        except (ValidationError, ValueError):
            pass
        return FC.check(truth_texts=truth, displayed=FC.load_displayed(ed, proj if proj.exists() else None, srt),
                        script_lines=lines, brief=state.get("brief"), vt=vt_of(state))

    def critic(state: dict) -> dict:
        stage, ed = state.get("critic_stage", "preview"), Path(state["edit_dir"])
        target = state.get("outputs", {}).get("final" if stage == "final" else "preview")
        findings: list[S.CriticFinding] = []
        notes: list[str] = []
        sysmsg = d.skills.system_prompt("critic", state)
        out_dir = ed / "verify" / f"critic-{state['video_name']}-{stage}-{state.get('critic_fix_rounds', 0)}"
        if not target or not Path(target).exists():
            notes.append(f"Chưa có file {'bản render' if stage == 'final' else 'bản xem trước'} để soi nên bỏ qua critic ở bước này.")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            hand = bool(state.get("hand_edited"))
            # lớp 1: script có sẵn của skill. Không chạy trên bản chỉnh tay: critic.py đọc timeline.json của bản dựng gốc,
            # mà người đã sửa trong VideoFac nên số liệu đó sai; chạy sẽ cho báo cáo sai thay vì không có báo cáo.
            res = (d.tools.run("critic", f"critic_{stage}", state["mode"], vars_for(state, "critic", target=target,
                               critic_out=str(out_dir)), cwd=ed, approvals=state.get("approvals", {}),
                               owner=state["job_id"], on_wait=on_wait_for(state)) if not hand else None)
            if hand:
                notes.append("Lớp 1 (script critic của skill) không chạy trên bản chỉnh tay: cần dữ liệu của bản dựng gốc. "
                             "Đã chạy lớp 2 (AI xem khung hình) và đối chiếu giá/ưu đãi trong phụ đề.")
            elif res.skipped:
                notes.append("Lớp 1 (script critic) chưa được khai báo cho chế độ này.")
            elif res.soft_failed:
                notes.append("Lớp 1 (script critic) chạy lỗi, bỏ qua lớp này: " + "; ".join(f["why"][:200] for f in res.soft_failed))
            else:
                rj = out_dir / "report.json"
                parsed = None
                if rj.exists():
                    try:
                        parsed = S.CriticReport.model_validate_json(rj.read_text(encoding="utf-8"))
                    except ValidationError:
                        parsed = None
                if parsed is None:
                    parsed = d.llm.structured(
                        agent="critic", system=sysmsg, schema=S.Layer1Report,
                        user="Chuyển báo cáo của script critic thành danh sách lỗi, GIỮ NGUYÊN mức nặng/vừa của báo cáo, "
                             "không thêm lỗi mới.\n" + wrap_data("bao_cao_critic", _read(out_dir / "report.md", 20000)))
                for f in parsed.findings:
                    f.layer = 1
                    findings.append(f)
            # lớp 2: AI xem khung hình
            dur = duration_of(cfg, Path(target))
            times = [0.5, 1.5] + ([dur * q for q in (0.25, 0.5, 0.75)] + [max(dur - 1.5, 0)] if dur else [])
            frames = extract_frames(cfg, Path(target), times, out_dir / "frames")
            imgs = ([out_dir / "joins.jpg"] if (out_dir / "joins.jpg").exists() else []) + frames
            l2 = d.llm.structured(
                agent="critic", system=sysmsg, schema=S.Layer2Report, images=imgs[:8],
                user="Soi các khung hình theo brief và thương hiệu: giá/ưu đãi/CTA trên màn hình có khớp brief không, tên "
                     "và logo thương hiệu đúng không, có lộ thương hiệu cần tránh không, chữ cấm, hook giây 0-1, che mặt/sản phẩm. "
                     "Không chắc thì đặt needs_human=true, đừng đoán là đạt.\n"
                     + ("Bản này do NGƯỜI chỉnh tay trong VideoFac sau bản dựng của hệ thống; kịch bản dưới đây là bản gốc trước khi chỉnh, "
                        "nội dung có thể khác hợp lệ. Chỉ báo lỗi so với brief và thương hiệu.\n" if hand else "")
                     + wrap_data("brief", state.get("brief_text", ""))
                     + wrap_data("thuong_hieu", _read(cfg.projects_root / "_brands" / state["brand"] / "brand.json", 6000))
                     + wrap_data("kich_ban", _read(ed / "script.md", 8000)))
            for f in l2.findings:
                f.layer = 2
                findings.append(f)
            notes += l2.notes
        # đối chiếu giá / % / ngày với brief bằng chương trình: chạy cả khi chưa có file video (chỉ cần chữ trong edl/overlays/phụ đề)
        fc = _factcheck(state)
        findings += fc.findings
        notes += fc.notes
        fd = [f.model_dump() for f in findings]
        heavy = any(f.severity == "nang" for f in findings)  # tính bằng code, không để AI tự kết luận
        report = [f"# Tổng hợp critic · {stage}", critic_summary(fd), ""] + [f"- ghi chú: {n}" for n in notes]
        (out_dir.parent / f"tong-hop-{stage}.md").parent.mkdir(parents=True, exist_ok=True)
        (out_dir.parent / f"tong-hop-{stage}.md").write_text("\n".join(report), encoding="utf-8")
        upd: dict = {"critic_findings": fd, "critic_has_heavy": heavy, "critic_stage": stage, "stage": f"critic_{stage}",
                     "steps": [step(state, f"critic_{stage}", "critic", heavy=sum(f.severity == "nang" for f in findings),
                                    medium=sum(f.severity == "vua" for f in findings), notes=notes)]}
        if heavy and state.get("hand_edited"):
            pass  # bản chỉnh tay không có "AI sửa EDL" (sẽ dựng lại và mất phần người đã chỉnh): hỏi người ở hand_edit_review
        elif heavy:
            if state.get("critic_fix_rounds", 0) < cfg.max_critic_rounds:
                upd["fix_source"] = "critic"
                # người giao thấy tin này thì biết bot đang tự sửa (im lặng vài phút là bình thường), chưa cần làm gì
                note(state, f"{critic_summary(fd).splitlines()[0]}. Đang tự sửa lỗi nặng "
                            f"(vòng {state.get('critic_fix_rounds', 0) + 1}/{cfg.max_critic_rounds}), chưa cần bạn làm gì. "
                            "Sửa xong sẽ gửi bản xem trước để bạn duyệt.")
            else:
                upd["escalation"] = {"reason": f"Critic vẫn còn lỗi nặng sau {cfg.max_critic_rounds} vòng sửa (nguyên tắc 5)",
                                     "on_continue": "approve_2" if stage == "preview" else "deliver"}
        return upd

    # ───────────── 8. duyệt 2 ─────────────
    def cost_advice(state: dict) -> list[str]:
        """Khối chi phí + lựa chọn ở Duyệt 2: tiếp tục bằng AI (tốn tiền mỗi vòng) hay chỉnh tay (không tốn AI).
        Số tiền một vòng lấy từ chính các lần gọi trước của job này; chưa có thì ước thô và nói rõ là ước."""
        if not d.meter:
            return []
        jid = state["job_id"]
        t = d.meter.job_total(jid)
        if not t["calls"]:
            return []
        cap, ba = d.meter.cap_for(jid), t["by_agent"]

        def avg(agent: str) -> float | None:
            x = ba.get(agent)
            return x["usd"] / x["calls"] if x and x["calls"] else None

        e, c = avg("edit"), avg("critic")
        measured = e is not None and c is not None
        crit = c if c is not None else 0.1
        rnd = (e if e is not None else 0.1) + crit
        rough = "" if measured else " (ước thô, job này chưa có số đo)"
        spent = t["usd"]
        out = ["", "CHI PHÍ AI" + (f" (đã dùng ~${spent:.2f}" + (f" / trần ${cap:.2f})" if cap else ")")),
               f"• /duyet: render bản cuối chạy trên máy, không tốn AI; chỉ soi lỗi bản cuối ~${crit:.2f}.",
               f"• /sua <góp ý>: AI sửa rồi soi lại, mỗi vòng ~${rnd:.2f}{rough}"
               + (f", trần còn đủ ~{max(0, int((cap - spent - crit) // rnd))} vòng." if cap and rnd > 0 else "."),
               f"• /chinhtay {jid}: chỉnh tay bằng VideoFac, không tốn AI để sửa (sau /xong chỉ soi lỗi bản cuối ~${crit:.2f}). "
               "Hợp với lỗi nhỏ về chữ (phụ đề ngắt sai, lệch thời gian, đổi từ) hoặc khi bạn đã sửa vài vòng. Bot đặt gói lên NAS, "
               "người chỉnh mở bằng VideoFac, xuất xong thì /xong. Không render lại; critic bản cuối thiếu lớp soi dữ liệu dựng của skill."]
        n = state.get("human_revisions", 0)
        if n >= 2:
            out.append(f"⚠ Đã sửa {n} lần bằng AI. Lỗi còn lại nhỏ thì nên chuyển sang chỉnh tay cho rẻ và nhanh.")
        if cap and spent + rnd > cap:
            out.append(f"⚠ Một vòng sửa nữa có thể chạm trần chi phí. Cần thì nâng trần: /nangtran {jid} <USD>.")
        return out

    def approve_2(state: dict) -> dict:
        outs, jid = state.get("outputs", {}), state["job_id"]
        lines = [f"DUYỆT 2 · bản dựng '{state['video_name']}'", critic_summary(state.get("critic_findings", []))]
        if state.get("critic_has_heavy"):
            lines.append("⚠ Vẫn còn lỗi nặng chưa sửa được (bạn quyết định).")
        lines.append(f"Bản xem trước: {outs.get('preview') or 'chưa có file; xem bằng công cụ xem trước của skill trên máy dựng'}")
        lines += cost_advice(state)
        lines += ["", f"/duyet {jid} để render bản cuối   ·   /sua {jid} <góp ý>   ·   /chinhtay {jid}   ·   /huy {jid}"]
        files = [outs[k] for k in ("preview",) if outs.get(k)]
        sync(state, status="waiting_approval", stage="duyet_2")
        p = ask(state, "approval", "\n".join(lines), files)
        if p is None or p.action not in ("approve", "revise", "cancel", "handedit"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy ở bước duyệt 2", "status": "cancelled"}
        if p.action == "handedit":
            why = H.available(cfg)
            if why or not outs.get("project_dir"):
                note(state, f"Chưa chỉnh tay được: {why or 'job chưa có dự án dựng để đóng gói'} Chọn /duyet, /sua hoặc /huy.")
                return {"gate_result": "ignored"}
            return {"gate_result": "handedit"}
        if p.action == "approve":
            sync(state, status="running", stage="duyet_2_ok")
            return {"gate_result": "approve", "approvals": {**state.get("approvals", {}), "duyet_2": stamp(p)},
                    "status": "running", "stage": "duyet_2_ok"}
        n = state.get("human_revisions", 0) + 1
        upd = {"gate_result": "revise", "human_revisions": n, "fix_source": "human", "status": "running",
               "feedback": [{"stage": "duyet_2", "by": p.by, "note": p.note, "kind": "human",
                             "critic_flagged": bool(state.get("critic_findings"))}]}
        if n > cfg.max_revisions:
            upd["escalation"] = {"reason": f"Đã yêu cầu sửa bản dựng quá {cfg.max_revisions} lần", "on_continue": "render"}
        return upd

    # ───────────── 8b. chỉnh tay bằng VideoFac (nhánh của Duyệt 2) ─────────────
    def hand_edit(state: dict) -> dict:
        """Đóng gói dự án lên NAS cho người chỉnh. Lỗi (NAS, Node, VideoFac) thì job dừng và báo; /tieptuc để thử lại."""
        outs, jid = state.get("outputs", {}), state["job_id"]
        dest = H.pack_dir(state["source_dir"], jid)
        sync(state, status="running", stage="hand_edit")
        info = H.pack(cfg, project_dir=outs["project_dir"], edl=Path(state["edit_dir"]) / "edl.json", name=state["video_name"], dest=dest)
        return {"outputs": {**outs, "videofac_dir": str(dest), "videofac_cmd": info["cmd"]}, "stage": "hand_edit",
                "steps": [step(state, "hand_edit", "edit", files=info["files"], mb=round(info["bytes"] / 1048576, 1))]}

    def hand_edit_wait(state: dict) -> dict:
        """Cổng chờ người chỉnh tay xong (/xong). Chỉ đọc trước interrupt."""
        outs, jid = state.get("outputs", {}), state["job_id"]
        text = "\n".join([
            f"CHỈNH TAY BẰNG VIDEOFAC · '{state['video_name']}'",
            "Gói chỉnh tay đã đặt trên NAS (dung lượng và đường dẫn tương đối, mở được trên máy khác):",
            f"  {outs.get('videofac_dir', '?')}",
            "Người chỉnh (máy đã cài VideoFac 0.20 trở lên, đọc/ghi được thư mục trên): bấm đúp Mo-trong-VideoFac.cmd trong thư mục đó, "
            "chỉnh, bấm 'Xuất video'. Bản xuất tự về thư mục export của gói. Một người mở một lần, VideoFac tự khóa.",
            "Chi phí AI trong lúc chờ: 0. Sau /xong bot kiểm khung hình 1080x1920, tiếng, rồi soi lỗi bản cuối 1 lần (AI xem khung hình + "
            "đối chiếu giá/ưu đãi trong phụ đề, ~$0.1). Không render lại; thiếu lớp soi dữ liệu dựng của skill vì bản đã bị chỉnh tay.",
            "", f"Xuất xong nhắn: /xong {jid}   ·   bỏ job: /huy {jid}"])
        sync(state, status="waiting_approval", stage="hand_edit")
        p = ask(state, "approval", text)
        if p is None or p.action not in ("done", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy khi chỉnh tay", "status": "cancelled"}
        return {"gate_result": "done", "status": "running"}

    def hand_edit_done(state: dict) -> dict:
        """Nhận bản xuất: kiểm lại, tạo bản nhẹ, đối chiếu giá/ưu đãi trong phụ đề, rồi giao."""
        outs, ed = state.get("outputs", {}), Path(state["edit_dir"])
        ex = H.find_export(cfg, outs["videofac_dir"])
        if ex.problems:
            raise RuntimeError("Bản xuất chưa dùng được: " + " ".join(ex.problems))
        light = ed / f"{state['video_name']}-1080x1920-nhe.mp4"
        H.make_light(cfg, ex.mp4, light)
        note(state, f"Nhận bản chỉnh tay: {ex.info}. Đang soi lỗi bản cuối (AI xem khung hình + đối chiếu giá/ưu đãi trong phụ đề).")
        sync(state, status="running", stage="hand_edit_done")
        new = {**outs, "final": str(ex.mp4), "light": str(light), "hand_edited": "1"}
        if ex.srt:
            new["srt"] = str(ex.srt)
        else:
            new.pop("srt", None)
        # tiếp theo là critic bản cuối (hand_edited=True: bỏ lớp 1 của skill, giữ lớp 2 + đối chiếu phụ đề), rồi giao hoặc hỏi người
        return {"outputs": new, "hand_edited": True, "critic_stage": "final",
                "approvals": {**state.get("approvals", {}), "duyet_2": {"by": str(state["requester_id"]), "at": d.now().isoformat(timespec="seconds"),
                                                                       "note": "chỉnh tay bằng VideoFac"}},
                "stage": "hand_edit_done", "steps": [step(state, "hand_edit_done", "edit", export=ex.info)]}

    def hand_edit_review(state: dict) -> dict:
        """Critic bản chỉnh tay thấy lỗi nặng: người quyết (giao tiếp, chỉnh lại trong VideoFac, hoặc hủy). Không có vòng AI tự sửa."""
        jid = state["job_id"]
        text = "\n".join([f"SOI LỖI BẢN CHỈNH TAY · '{state['video_name']}'", critic_summary(state.get("critic_findings", [])),
                          "⚠ Còn lỗi nặng. Bản chỉnh tay không có vòng AI tự sửa (sẽ dựng lại và mất phần bạn đã chỉnh).", "",
                          f"/chinhtay {jid} để chỉnh lại trong VideoFac rồi xuất và /xong lại   ·   /duyet {jid} để giao bản này   ·   /huy {jid}"])
        sync(state, status="waiting_approval", stage="hand_review")
        p = ask(state, "approval", text, [state.get("outputs", {}).get("light")] if state.get("outputs", {}).get("light") else [])
        if p is None or p.action not in ("approve", "handedit", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy sau khi soi lỗi bản chỉnh tay", "status": "cancelled"}
        sync(state, status="running")
        return {"gate_result": p.action, "status": "running"}

    # ───────────── 9. escalate: vượt giới hạn vòng lặp -> hỏi người ─────────────
    def escalate(state: dict) -> dict:
        esc, jid = state.get("escalation") or {}, state["job_id"]
        on = esc.get("on_continue", "approve_2")
        costs: list = []
        extra = ""
        if on == "edit_build":  # chấp nhận ở đây = duyệt 1, nên phải nói rõ cả chi phí ngoài
            try:
                costs = json.loads(_read(Path(state["edit_dir"]) / "script.json", 200000)).get("external_costs", [])
            except ValueError:
                costs = []
            extra = "\nChấp nhận = DUYỆT kịch bản hiện tại để dựng" + (
                " (kể cả chi phí: " + "; ".join(f"{c['what']} {c['amount_text']}" for c in costs) + ")" if costs else "")
        elif on == "render":
            extra = "\nChấp nhận = DUYỆT bản dựng hiện tại để render bản cuối"
        text = (f"CẦN BẠN QUYẾT ĐỊNH: {esc.get('reason', '')}\n" + critic_summary(state.get("critic_findings", []))
                + extra + f"\n\n/chapnhan {jid} để đi tiếp với bản hiện tại   ·   /huy {jid}")
        sync(state, status="waiting_approval", stage="escalate")
        p = ask(state, "escalate", text)
        if p is None or p.action not in ("continue", "cancel"):
            return {"gate_result": "ignored"}
        if p.action == "cancel":
            return {"gate_result": "cancel", "cancel_reason": "Người giao hủy sau cảnh báo", "status": "cancelled"}
        ap = dict(state.get("approvals", {}))
        if on == "edit_build":
            ap["duyet_1"] = stamp(p)
            ap["cost_ok"] = {**stamp(p), "items": costs}
        elif on == "render":
            ap["duyet_2"] = stamp(p)
        sync(state, status="running")
        return {"gate_result": "continue", "resume_to": on, "escalation": None, "approvals": ap,
                "human_revisions": 0, "status": "running",
                "feedback": [{"stage": "escalate", "by": p.by, "note": "chấp nhận bản hiện tại", "kind": "decision"}]}

    # ───────────── 10. render ─────────────
    def render(state: dict) -> dict:
        ap = state.get("approvals", {})
        if not ap.get("duyet_2"):
            raise PermissionError("Chưa có duyệt 2, không được render bản cuối")
        sync(state, status="running", stage="render")
        res = d.tools.run("edit", "render", state["mode"], vars_for(state, "edit"), cwd=Path(state["edit_dir"]),
                          approvals=ap, owner=state["job_id"], on_wait=on_wait_for(state))
        if res.skipped:
            raise RuntimeError(f"Skill chưa khai báo lệnh render cho chế độ {state['mode']}")
        return {"outputs": {**state.get("outputs", {}), **res.outputs}, "critic_stage": "final", "stage": "render",
                "steps": [step(state, "render", "edit", outputs=list(res.outputs))]}

    # ───────────── 11. giao kết quả ─────────────
    def deliver(state: dict) -> dict:
        o, jid = state.get("outputs", {}), state["job_id"]
        # chép kết quả về footage gốc trên NAS (mỗi job một thư mục con nên không đè nhau); lỗi thì giữ bản làm việc
        dest, where = None, ""
        if state.get("source_dir") and state.get("work_dir"):
            try:
                dest = W.deliver_results(Path(state["source_dir"]), Path(state["work_dir"]), jid, o)
                where = f"Kết quả đã chép về: {dest}"
            except OSError as e:
                where = (f"⚠ Chưa chép được kết quả về NAS ({e.__class__.__name__}). File vẫn nằm trên máy chung tại "
                         f"{state['work_dir']}\\edit và sẽ KHÔNG bị dọn. Báo admin kiểm tra NAS rồi /tieptuc {jid}.")
        name = lambda k: Path(o[k]).name if o.get(k) else "?"  # noqa: E731
        txt = [f"XONG '{state['video_name']}'", *([where] if where else []),
               f"Bản gốc: {name('final')}", f"Bản nhẹ để đăng: {name('light')}", f"Phụ đề: {name('srt')}",
               critic_summary(state.get("critic_findings", [])),
               *([cost] if (cost := d.meter.summary_line(jid) if d.meter else "") else []),
               "Hệ thống KHÔNG tự đăng bài. Bạn kiểm tra rồi đăng."]
        if o.get("hand_edited"):
            txt.append("Bản chỉnh tay bằng VideoFac: không render lại; critic bản cuối chạy lớp AI xem khung hình + đối chiếu phụ đề, "
                       "không có lớp soi dữ liệu dựng của skill. Chữ trên thẻ đồ họa chỉ được AI xem qua khung hình, bạn xem kỹ trước khi đăng.")
        if state.get("critic_has_heavy"):
            txt.append("⚠ Bản này vẫn còn lỗi nặng theo critic, bạn đã chấp nhận.")
        if dest:
            txt.append(f"Bản chép làm việc trên máy chung giữ thêm {cfg.work_keep_hours:g} giờ rồi tự dọn.")
        note(state, "\n".join(txt), [o[k] for k in ("light", "srt") if o.get(k)])
        sync(state, status="done", stage="done")
        return {"status": "done", "stage": "done", "steps": [step(state, "deliver", delivered=bool(dest))]}

    # ───────────── 12. bài học (đề xuất, người giao duyệt) ─────────────
    def lessons(state: dict) -> dict:
        fb = state.get("feedback", [])
        if not any(f.get("kind") == "human" for f in fb) and not state.get("critic_fix_rounds"):
            return {"lesson_items": [], "stage": "lessons"}
        user = ("Rút 0-6 bài học ngắn, cụ thể, dùng lại được từ job này. Mỗi bài học gắn đúng agent. "
                "source=human nếu từ góp ý người duyệt (kèm feedback_index); critic/edit_fix nếu từ lỗi critic/việc sửa; "
                "human_miss nếu người duyệt bắt được lỗi mà critic bỏ sót (kèm feedback_index). scope: chung | dang:<id> | brand:<tên>. "
                "Không rút bài học chỉ đúng riêng cho video này.\n"
                + wrap_data("gop_y", json.dumps(fb, ensure_ascii=False))
                + wrap_data("loi_critic", json.dumps(state.get("critic_findings", []), ensure_ascii=False)))
        res = d.llm.structured(agent="orchestrator", system=d.skills.system_prompt("orchestrator", state),
                               user=user, schema=S.LessonProposals)
        items = []
        for pr in res.items[:8]:
            if pr.source in ("human", "human_miss") and not (pr.feedback_index is not None and 0 <= pr.feedback_index < len(fb)):
                continue  # phải truy được về lời người duyệt thật
            les = d.skills.store(pr.agent).propose(pr.text, pr.scope, pr.kind, pr.source, video=state["video_name"])
            if les:
                items.append({"agent": pr.agent, "id": les.id, "text": les.text, "scope": les.scope})
        return {"lesson_items": items, "stage": "lessons", "steps": [step(state, "lessons", proposed=len(items))]}

    def lesson_gate(state: dict) -> dict:
        items, jid = state.get("lesson_items", []), state["job_id"]
        lines = ["Rút được các bài học sau. Chọn bài đáng nhớ:"]
        lines += [f"  {i}. [{x['agent']} · {x['scope']}] {x['text']}" for i, x in enumerate(items, 1)]
        lines += ["", f"/baihoc {jid} 1,3   (thêm 'ghim 1' để ghim ngay)   ·   /baihoc {jid} bo   để bỏ hết"]
        p = ask(state, "lessons", "\n".join(lines))
        if p is None or p.action not in ("select", "skip"):
            return {"stage": "lessons_done"}
        chosen = [items[i - 1] for i in p.ids if 1 <= i <= len(items)] if p.action == "select" else []
        pinned = {items[i - 1]["id"] for i in p.pin if 1 <= i <= len(items)}
        for agent in {x["agent"] for x in items}:
            store = d.skills.store(agent)
            mine = [x["id"] for x in chosen if x["agent"] == agent]
            store.approve(mine, [i for i in mine if i in pinned])
            store.reject([x["id"] for x in items if x["agent"] == agent and x["id"] not in mine])
        return {"stage": "lessons_done"}

    def cancel_end(state: dict) -> dict:
        reason = state.get("cancel_reason") or (
            "Brief vẫn thiếu thông tin sau nhiều lần hỏi" if state.get("info_request") else "đã hủy")
        if state.get("stage") != "intake":  # intake đã tự báo
            note(state, f"Job đã dừng: {reason}")
        sync(state, status="cancelled", note=reason[:200])
        return {"status": "cancelled", "cancelled": True}

    raw = {
        "intake": intake, "scribe_gate": scribe_gate, "extract_brief": extract_brief, "ask_info": ask_info, "footage": footage, "script": script,
        "approve_1": approve_1, "edit_build": edit_build, "edit_fix": edit_fix, "critic": critic,
        "approve_2": approve_2, "hand_edit": hand_edit, "hand_edit_wait": hand_edit_wait, "hand_edit_done": hand_edit_done,
        "hand_edit_review": hand_edit_review,
        "escalate": escalate, "render": render, "deliver": deliver, "lessons": lessons,
        "lesson_gate": lesson_gate, "cancel_end": cancel_end,
    }

    def in_job(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        """Mọi lời gọi AI trong node được tính cho đúng job (số token/tiền ghi theo mã job)."""
        @functools.wraps(fn)
        def wrapped(state: dict) -> dict:
            with job_context(state.get("job_id")):
                return fn(state)
        return wrapped

    return {name: in_job(fn) for name, fn in raw.items()}
