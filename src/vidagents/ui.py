"""Nút bấm Telegram (không phụ thuộc thư viện telegram, để test được): bàn phím theo trạng thái job + menu chính.

Một nút = (nhãn, callback_data). callback_data tối đa 64 byte (giới hạn của Telegram), dạng `j|<mã hành động>|<mã job>[|<đối số>]`.
Mã job dài quá thì nút đó bị bỏ (người dùng vẫn dùng được lệnh gõ tay): không bao giờ cắt mã job vì sẽ trỏ nhầm job.

Quyền và trạng thái KHÔNG kiểm ở đây: bot kiểm lại mỗi lần bấm (nút trên tin cũ có thể đã hết hạn).
"""
from __future__ import annotations

Button = tuple[str, str]
Keyboard = list[list[Button]]

# mã hành động -> (tên hành động trong payload của đồ thị, lệnh gõ tay tương ứng)
ACTIONS = {
    "ok": "approve",       # /duyet
    "rv": "revise",        # /sua (hỏi nội dung sửa)
    "no": "cancel",        # /huy (hỏi lại cho chắc)
    "xc": "cancel!",       # xác nhận hủy
    "nc": "keep",          # không hủy nữa: hiện lại bàn phím
    "ct": "continue",      # /chapnhan
    "bs": "answer",        # /bosung (hỏi nội dung)
    "hp": "handedit",      # /chinhtay
    "xg": "done",          # /xong
    "tt": "resume",        # /tieptuc
    "cp": "cap",           # /nangtran <job> <USD>
    "st": "status",        # /trangthai <job>
}
MAX_CB = 64


def cb(code: str, job_id: str, arg: str = "") -> str | None:
    data = "|".join(["j", code, job_id] + ([arg] if arg else []))
    return data if len(data.encode("utf-8")) <= MAX_CB else None


def parse_cb(data: str) -> tuple[str, str, str, str] | None:
    """'j|ok|<job>[|arg]' -> ('j', 'ok', job, arg)."""
    parts = (data or "").split("|")
    if len(parts) < 3 or len(parts) > 4 or parts[0] not in ("j", "m", "w", "t", "d"):
        return None
    return parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else ""


def _row(job_id: str, *items: tuple[str, str, str]) -> list[Button]:
    out = []
    for label, code, arg in items:
        data = cb(code, job_id, arg)
        if data:
            out.append((label, data))
    return out


def next_cap(cap: float | None) -> float | None:
    """Trần gợi ý khi chạm trần: cộng 1 USD (làm tròn gọn). Người dùng thấy số tiền ngay trên nút trước khi bấm."""
    if cap is None:
        return None
    return round(cap + 1.0, 2)


def job_keyboard(job: dict | None, cap: float | None = None, can_handedit: bool = True) -> Keyboard:
    """Các nút hợp với trạng thái hiện tại của job. Rỗng = không có gì để bấm (job xong/hủy)."""
    if not job:
        return []
    jid, st, stage = job["job_id"], job["status"], job.get("stage") or ""
    rows: Keyboard = []
    if st == "waiting_approval":
        if stage == "duyet_scribe":
            rows = [_row(jid, ("✅ Đồng ý chi phí nhận dạng lời", "ok", "")), _row(jid, ("❌ Hủy", "no", ""))]
        elif stage == "duyet_1":
            rows = [_row(jid, ("✅ Duyệt kịch bản", "ok", "")), _row(jid, ("✏️ Sửa kịch bản", "rv", ""), ("❌ Hủy", "no", ""))]
        elif stage == "duyet_2":
            second = [("✏️ Sửa bằng AI", "rv", "")] + ([("🛠 Chỉnh tay (VideoFac)", "hp", "")] if can_handedit else [])
            rows = [_row(jid, ("✅ Duyệt và render bản cuối", "ok", "")), _row(jid, *second), _row(jid, ("❌ Hủy", "no", ""))]
        elif stage == "hand_edit":
            rows = [_row(jid, ("✅ Đã chỉnh và xuất xong", "xg", "")), _row(jid, ("❌ Hủy", "no", ""))]
        elif stage == "hand_review":
            rows = [_row(jid, ("✅ Giao bản này", "ok", "")), _row(jid, ("🛠 Chỉnh lại trong VideoFac", "hp", ""), ("❌ Hủy", "no", ""))]
        elif stage == "escalate":
            rows = [_row(jid, ("➡️ Chấp nhận, đi tiếp", "ct", "")), _row(jid, ("❌ Hủy", "no", ""))]
        else:
            rows = [_row(jid, ("✅ Duyệt", "ok", "")), _row(jid, ("✏️ Sửa", "rv", ""), ("❌ Hủy", "no", ""))]
    elif st == "waiting_info":
        rows = [_row(jid, ("💬 Trả lời bổ sung", "bs", "")), _row(jid, ("❌ Hủy", "no", ""))]
    elif st == "blocked":
        rows = [_row(jid, ("▶️ Chạy tiếp", "tt", ""))]
        nxt = next_cap(cap)
        if nxt and "CostCapExceeded" in (job.get("note") or ""):   # runner ghi tên lỗi vào note khi job dừng vì chạm trần
            rows.append(_row(jid, (f"⬆️ Nâng trần lên ${nxt:g} và chạy tiếp", "cp", f"{nxt:g}")))
        rows.append(_row(jid, ("❌ Hủy", "no", "")))
    elif st in ("queued", "running"):
        rows = [_row(jid, ("📊 Xem tiến độ", "st", ""))]
    return [r for r in rows if r]


_WAITING = {"duyet_scribe": "chờ bạn đồng ý chi phí nhận dạng lời (Scribe)", "duyet_1": "chờ bạn duyệt kịch bản",
            "duyet_2": "chờ bạn duyệt bản dựng: duyệt để render, sửa bằng AI (tốn tiền mỗi vòng) hoặc chỉnh tay bằng VideoFac",
            "hand_edit": "chờ người chỉnh tay trong VideoFac xuất xong rồi báo đã xong",
            "hand_review": "critic bản chỉnh tay thấy lỗi nặng: chờ bạn quyết (giao, chỉnh lại hoặc hủy)",
            "escalate": "vượt giới hạn tự sửa của hệ thống: chờ bạn quyết (chấp nhận đi tiếp hoặc hủy)"}
_RUNNING_STAGE = {"new": "mới nhận", "intake": "nhận job", "brief": "đọc brief", "footage": "xem footage", "script": "viết kịch bản",
                  "build": "dựng bản đầu", "fix": "sửa theo critic", "render": "render bản cuối", "duyet_1_ok": "chuẩn bị dựng",
                  "duyet_2_ok": "chuẩn bị render", "duyet_scribe_ok": "chuẩn bị đọc footage", "hand_edit": "đóng gói chỉnh tay",
                  "hand_edit_done": "soi lỗi bản chỉnh tay", "critic_preview": "soi lỗi bản xem trước", "critic_final": "soi lỗi bản cuối"}


def waiting_text(job: dict) -> str:
    """Một câu: job này đang ở đâu và đang chờ ai làm gì. Dùng cho trả lời hỏi đáp và màn hình tiến độ."""
    st, stage = job["status"], job.get("stage") or ""
    if st == "waiting_approval":
        return _WAITING.get(stage, "chờ bạn duyệt")
    if st == "waiting_info":
        return "chờ bạn trả lời thêm thông tin (bổ sung)"
    if st == "blocked":
        return "đang bị dừng do lỗi hoặc chạm trần chi phí AI; chạy tiếp được sau khi xử lý nguyên nhân"
    if st in ("queued", "running"):
        return f"đang chạy, giai đoạn: {_RUNNING_STAGE.get(stage, stage or 'đang khởi động')}; chưa cần bạn làm gì"
    return {"done": "đã xong", "cancelled": "đã hủy"}.get(st, st)


def confirm_cancel_keyboard(job_id: str) -> Keyboard:
    return [r for r in [_row(job_id, ("⚠️ Chắc chắn hủy job", "xc", ""), ("↩️ Không hủy", "nc", ""))] if r]


# ───────────────────────── menu chính ─────────────────────────
def main_menu(is_admin: bool) -> Keyboard:
    rows: Keyboard = [[("🎬 Giao việc mới", "m|new|-"), ("📅 Hôm nay dựng gì", "m|today|-")],
                      [("📋 Job của tôi", "m|jobs|-"), ("💰 Chi phí", "m|cost|-")],
                      [("🏓 Kiểm tra bot", "m|ping|-"), ("📖 Danh sách lệnh", "m|help|-")]]
    if is_admin:
        rows.append([("👥 Người dùng", "m|users|-"), ("➕ Thêm dạng video", "m|addtype|-")])
    return rows


def jobs_keyboard(jobs: list[dict]) -> Keyboard:
    """Mỗi job một nút (bấm để xem tiến độ + các nút của job đó). Tối đa 8 job gần nhất."""
    rows: Keyboard = []
    for j in [x for x in jobs if x][:8]:   # bỏ job không đọc được (vừa tạo, chưa kịp ghi sổ)
        data = cb("st", j["job_id"])
        if data:
            rows.append([(f"{_STATUS_ICON.get(j['status'], '•')} {j['job_id']}", data)])
    return rows


_STATUS_ICON = {"queued": "⏳", "running": "⚙️", "waiting_approval": "🔔", "waiting_info": "💬", "blocked": "⚠️", "done": "✅", "cancelled": "✖️"}
