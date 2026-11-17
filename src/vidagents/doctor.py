"""`vidagents doctor`: kiểm tra cấu hình và cho biết skill nào đã khai báo lệnh cho (chế độ × hành động) nào.

Dùng sau mỗi lần thả skill mới vào để biết còn thiếu gì, trước khi chạy job thật.
"""
from __future__ import annotations

import json
import os

from .config import Config
from .schemas import MODES, load_video_types
from .skills import AGENTS

# (agent, action) cần có để một chế độ chạy được trọn vẹn; 'optional' = có thì tốt, không có vẫn chạy
REQUIRED = [("footage", "prep"), ("edit", "build"), ("edit", "render"), ("critic", "critic_final")]
OPTIONAL = [("edit", "rebuild"), ("edit", "preview"), ("critic", "critic_preview")]


def doctor(cfg: Config) -> list[str]:
    out: list[str] = []
    ok = lambda b: "OK " if b else "THIẾU"  # noqa: E731
    out.append("== Đường dẫn ==")
    for name in ("footage_root", "projects_root", "agents_dir", "video_types_dir"):
        p = getattr(cfg, name)
        out.append(f"{ok(p.exists())} {name}: {p}")
    out.append(f"{ok(bool(cfg.skill_dir) and cfg.skill_dir.exists())} skill_dir (biến {{SKILL}}): {cfg.skill_dir}")
    out.append("== Thư mục làm việc từng job (bản chép footage) ==")
    wp = cfg.work_path
    out.append(f"{ok(wp.exists())} work_root: {wp}  (giữ {cfg.work_keep_hours:g} giờ sau khi xong)")
    try:
        from .workspace import free_bytes

        out.append(f"{ok(free_bytes(wp) > cfg.work_reserve_gb * 1024 ** 3)} ổ đĩa còn trống: {free_bytes(wp) / 1024 ** 3:.1f} GB "
                   f"(cần dự phòng {cfg.work_reserve_gb:g} GB + dung lượng footage mỗi job)")
    except OSError:
        out.append("THIẾU không đọc được dung lượng ổ đĩa của work_root")
    if str(wp).startswith("\\\\"):
        out.append("CẢNH BÁO work_root đang trên mạng (UNC). Nên đặt ở ổ nội bộ để cắt/render nhanh.")
    out.append("== Khóa bí mật (chỉ báo có/không, không in giá trị) ==")
    out.append(f"{ok(bool(os.environ.get(cfg.anthropic_key_env)))} {cfg.anthropic_key_env}")
    out.append(f"{ok(bool(os.environ.get(cfg.telegram_token_env)))} {cfg.telegram_token_env}")
    out.append(f"{ok(bool(cfg.allowed_user_ids))} danh sách người được giao việc: {len(cfg.allowed_user_ids)} người")
    out.append("== Agent ==")
    for a in AGENTS:
        d = cfg.agents_dir / a
        skill = (d / "SKILL.md")
        tools_ok = True
        if (d / "tools.json").exists():
            try:
                json.loads((d / "tools.json").read_text(encoding="utf-8"))
            except ValueError:
                tools_ok = False
        placeholder = skill.exists() and "PLACEHOLDER" in skill.read_text(encoding="utf-8")
        out.append(f"{ok(skill.exists())} {a}: SKILL.md{' (đang là bản tạm)' if placeholder else ''}"
                   f"{'' if tools_ok else ' · tools.json HỎNG'}")
    out.append("== Chi phí AI, cảnh báo, xem trước ==")
    cap = cfg.max_cost_per_job_usd
    out.append(f"{ok(cap is not None)} trần chi phí mỗi job: " + (f"${cap:g}" if cap is not None else "chưa đặt (max_cost_per_job_usd) — job có thể tốn không giới hạn"))
    from .usage import DEFAULT_PRICING

    prices = {**DEFAULT_PRICING, **cfg.pricing}
    models = {cfg.model_for(a) for a in ("orchestrator", "script", "footage", "edit", "critic")}
    for m in sorted(models):
        out.append(f"{ok(m in prices)} bảng giá cho model {m}" + ("" if m in cfg.pricing else "  (đang dùng giá mặc định THAM KHẢO, chưa đối chiếu trang giá chính thức)" if m in prices else "  (thiếu giá: chỉ đếm token)"))
    out.append(f"     báo video đứng sau {cfg.stall_minutes} phút; luồng nền quét mỗi {cfg.watchdog_every_s}s (chạy thêm `vidagents watchdog` từ Task Scheduler để bắt cả lúc bot chết)")
    out.append(f"{ok((cfg.adapters_dir / 'preview_b.py').exists())} adapters/preview_b.py · preview_quality: " + (repr(cfg.preview_quality) if cfg.preview_quality else "(trống: không gửi --quality)"))
    out.append("== Lệnh đã khai báo theo chế độ (✔ có · ✘ chưa) ==")
    head = "agent.action".ljust(34) + "".join(m.ljust(4) for m in MODES)
    out.append(head)
    for agent, action in REQUIRED + OPTIONAL:
        man = {}
        p = cfg.agents_dir / agent / "tools.json"
        if p.exists():
            try:
                man = json.loads(p.read_text(encoding="utf-8")).get("actions", {}).get(action, {})
            except ValueError:
                pass
        row = f"{agent}.{action}{'' if (agent, action) in REQUIRED else ' (tùy chọn)'}".ljust(34)
        row += "".join(("✔" if man.get(m) else "✘").ljust(4) for m in MODES)
        out.append(row)
    out.append("== Dạng video ==")
    try:
        for vt in load_video_types(cfg.video_types_dir).values():
            out.append(f"  {vt.id.ljust(22)} chế độ đề xuất: {vt.suggested_mode.ljust(4)} ({vt.status})")
    except Exception as e:  # noqa: BLE001
        out.append(f"THIẾU không đọc được dạng video: {e}")
    return out
