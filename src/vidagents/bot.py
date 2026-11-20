"""Bot Telegram (python-telegram-bot v21+). Mỗi người trong danh sách cho phép giao việc; chỉ người giao được duyệt job của mình.

Mỗi người phải nhắn riêng cho bot một lần (/start) thì bot mới gửi tin riêng cho họ được.
CHƯA ĐƯỢC CHẠY THỬ với Telegram thật (không có token trong môi trường dựng khung).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .config import Config

HELP = (
    "Lệnh:\n"
    "/video \"<thư mục footage>\" <loại video> <thương hiệu>   bot gửi phiếu brief điền sẵn; điền rồi gửi lại\n"
    "/phieu \"<thư mục footage>\" <loại video> <thương hiệu>   chỉ lấy phiếu brief, chưa giao việc\n"
    "/duyet <job>   duyệt bước đang chờ\n"
    "/sua <job> <góp ý> [chedo=A|B|C|S]   yêu cầu sửa\n"
    "/chinhtay <job>   (ở Duyệt 2) chỉnh tay bằng VideoFac, không tốn AI\n"
    "/xong <job>   chỉnh tay và xuất xong, bot nhận bản xuất rồi giao\n"
    "/bosung <job> <nội dung>   trả lời khi hệ hỏi thêm thông tin\n"
    "/chapnhan <job>   đi tiếp dù còn cảnh báo\n"
    "/baihoc <job> 1,3 | ghim 1 | bo   chọn bài học đáng nhớ\n"
    "/huy <job>   hủy job\n"
    "/trangthai [job]   xem tiến độ và chi phí AI\n"
    "/nangtran <job> <USD>   nâng trần chi phí AI của job rồi chạy tiếp (khi job dừng vì chạm trần)\n"
    "/tieptuc <job>   chạy tiếp job bị dừng / gửi lại yêu cầu duyệt\n"
    "/homnay   hôm nay dựng video nào (bot cũng tự hỏi lúc 8:30 và 21:00 nếu đã bật)\n"
    "/loai   danh sách loại video\n"
    "/ping   kiểm tra bot còn chạy + việc đang có\n"
    "Quản trị viên: /themnguoi <ID> [tên]  ·  /xoanguoi <ID>  ·  /danhsach  ·  /themloai (thêm dạng video mới bằng nút bấm)"
)
_TOK = re.compile(r'"([^"]+)"|(\S+)')


def parse_video_cmd(text: str) -> tuple[str, str, str, str] | None:
    """'/video "D:\\a b" koc-review femfresh\\n<brief...>' -> (thư mục, loại, thương hiệu, brief)."""
    first, _, brief = text.partition("\n")
    toks = [a or b for a, b in _TOK.findall(first)][1:]
    if len(toks) < 3:
        return None
    return toks[0], toks[1], toks[2], brief.strip()


def syntax_help(types: Sequence[str], brands: Sequence[str]) -> str:
    return ("Cách giao việc: nhắn dòng đầu rồi bot gửi PHIẾU BRIEF điền sẵn, anh/chị điền và gửi lại:\n"
            "/video \"<đường dẫn THƯ MỤC chứa clip>\" <loại video> <thương hiệu>\n\n"
            "Ví dụ:\n"
            "/video \"Z:\\0. Test ây ai\\KOC Nhật Hải\" koc-review oillan\n\n"
            "Lấy đường dẫn: chuột phải vào thư mục > Copy as path.\n"
            "Đã có sẵn brief viết tay thì xuống dòng và dán ngay sau dòng /video (bot nhờ AI trích ô, có thể hỏi lại).\n"
            f"Loại video: {', '.join(types)}\n"
            f"Thương hiệu: {', '.join(brands) or '(chưa có)'}")


def _brand_json(cfg: Config, brand: str) -> dict:
    import json

    try:
        return json.loads((cfg.projects_root / "_brands" / brand / "brand.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def plan_video_request(cfg: Config, types: dict, text: str) -> dict:
    """Quyết định cho một tin /video hoặc /phieu, không đụng Telegram (để test được).
    -> {"reply": <tin gửi lại>}  hoặc  {"start": {tham số cho runner.start}}. Mọi lỗi chặn trước khi tạo job, không tốn tiền."""
    from . import phieu
    from .nodes import brand_kits, check_request

    help_ = syntax_help(sorted(types), brand_kits(cfg))
    parsed = parse_video_cmd(text)
    if not parsed:
        return {"reply": help_}
    folder, vtype, brand, brief = parsed
    vtype, brand = vtype.lower(), brand.lower()
    errs = check_request(cfg, types, folder, vtype, brand)
    if errs:
        return {"reply": "Chưa giao được việc (chưa tạo job, chưa tốn tiền):\n- " + "\n- ".join(errs) + "\n\n" + help_}
    vt = types[vtype]
    cmd = text.split(None, 1)[0].lower().lstrip("/").split("@")[0]
    if cmd == "phieu" or not brief:
        return {"reply": phieu.build_form(vt, brand, _brand_json(cfg, brand), folder, phieu.footage_line(folder))}
    if phieu.is_form(text, vt):
        values = phieu.parse_form(text, vt)
        miss = phieu.missing(values, vt)
        if miss:
            return {"reply": "Phiếu còn thiếu (chưa tạo job, chưa tốn tiền). Điền thêm rồi gửi lại cả tin:\n- " + "\n- ".join(miss)}
        return {"start": {"footage_dir": folder, "video_type": vtype, "brand": brand,
                          "brief_text": phieu.canonical_text(values, vt),
                          "brief_prefill": phieu.to_brief(values, vt, brand).model_dump()}}
    return {"start": {"footage_dir": folder, "video_type": vtype, "brand": brand, "brief_text": brief}}


def ping_text(jobs, busy: int, slots: int, uid: str, is_admin: bool) -> str:
    """Trả lời /ping: bot còn sống không + việc đang có. Admin thấy job của mọi người, người khác chỉ thấy job của mình."""
    rows = jobs.list(None if is_admin else uid, active_only=True)
    running = [r for r in rows if r["status"] in ("queued", "running")]
    waiting = [r for r in rows if r["status"] in ("waiting_approval", "waiting_info")]
    blocked = [r for r in rows if r["status"] == "blocked"]
    who = "tất cả" if is_admin else "của bạn"
    line = f"Bot đang chạy ✓  ({datetime.now():%H:%M %d/%m})\nJob {who}: {len(running)} đang chạy · {len(waiting)} chờ duyệt/bổ sung · {len(blocked)} bị dừng"
    line += f"\nSuất render: {busy}/{slots}"
    for r in blocked[:3]:
        line += f"\n⚠ {r['job_id']} bị dừng: gõ /tieptuc {r['job_id']} sau khi xử lý nguyên nhân"
    return line + "\nGiao việc: /video  ·  Xem lệnh: /start"


def status_view(job: dict, meter=None) -> str:
    """Màn hình tiến độ một job (nút 'Xem tiến độ' / hỏi đáp): ở đâu, chờ ai làm gì, tốn bao nhiêu."""
    from . import ui

    icon = ui._STATUS_ICON.get(job["status"], "•")
    lines = [f"{icon} {job['job_id']}", f"{ui.waiting_text(job).capitalize()}.", f"Loại: {job['video_type']} · Thương hiệu: {job['brand']}"]
    if meter:
        t = meter.job_total(job["job_id"])
        if t["calls"]:
            cap = meter.cap_for(job["job_id"])
            lines.append(f"Chi phí AI: ~${t['usd']:.2f}" + (f" / trần ${cap:.2f}" if cap else "") + f" · {t['calls']} lần gọi")
    if job["status"] == "blocked" and job.get("note"):
        lines.append("Lý do dừng: " + job["note"][:200])
    return "\n".join(lines)


def cost_text(jobs, meter, uid: str, is_admin: bool, chat_spent: float = 0.0, chat_limit: float | None = None) -> str:
    """Tổng hợp chi phí AI các job gần đây (người dùng: của mình; admin: của tất cả) + chi phí hỏi đáp hôm nay."""
    rows = jobs.list(None if is_admin else uid, active_only=False)[:10]
    if not rows:
        return "Chưa có job nào."
    lines, total = ["💰 CHI PHÍ AI (10 job gần nhất)"], 0.0
    for r in rows:
        t = meter.job_total(r["job_id"]) if meter else {"usd": 0.0, "calls": 0}
        total += t["usd"]
        lines.append(f"• {r['job_id']}: ~${t['usd']:.2f} ({ui_status(r['status'])})")
    lines.append(f"Tổng: ~${total:.2f}")
    if chat_spent:
        lines.append(f"Hỏi đáp hôm nay: ~${chat_spent:.4f}" + (f" / hạn mức ${chat_limit:.2f}" if chat_limit else ""))
    lines.append("Chưa gồm phí nhận dạng lời (Scribe) tính riêng theo phút âm thanh.")
    return "\n".join(lines)


def ui_status(status: str) -> str:
    return _STATUS_VN.get(status, status)


_STATUS_VI = {"queued": "đang xếp hàng", "running": "đang chạy", "waiting_info": "chờ thông tin", "waiting_approval": "chờ duyệt", "blocked": "bị dừng"}


def same_footage_note(rows: Sequence[dict]) -> str:
    """Cảnh báo kèm tin "Đã nhận" khi thư mục footage đang có job khác. Không chặn: cùng footage vẫn có thể là video khác (thương hiệu, dạng khác)."""
    if not rows:
        return ""
    lines = [f"· {j['job_id']} ({j.get('requester_name') or 'người khác'}, {j.get('video_type', '')}/{j.get('brand', '')}, "
             f"{_STATUS_VI.get(j.get('status', ''), j.get('status', ''))})" for j in rows[:5]]
    more = f"\n… và {len(rows) - 5} job nữa" if len(rows) > 5 else ""
    return ("\n\n⚠ Thư mục footage này đang có job khác:\n" + "\n".join(lines) + more +
            "\nJob mới vẫn được nhận (có thể là video khác). Mỗi job chép riêng footage về máy chung; bản nhận dạng lời được dùng chung: "
            "job đến sau chờ job trước nhận dạng xong rồi dùng lại, không tính tiền Scribe lần hai. Giao trùng nhầm thì /huy job mới.")


def unknown_command_text(text: str) -> str:
    cmd = (text or "").split(None, 1)[0] if (text or "").strip() else "?"
    return (f"Bot không có lệnh {cmd}.\nGõ /start để xem các lệnh, /ping để kiểm tra bot còn chạy, "
            "/video để giao việc dựng video, /trangthai để xem job.")


# Lệnh trả lời một yêu cầu của hệ thống chỉ có nghĩa khi job đang chờ đúng việc đó. Gõ nhầm mã job (vd. job hôm qua đã xong)
# thì không được đẩy tin vào job ấy rồi báo "Đã ghi nhận".
RESUME_STATUS = {"approve": ("waiting_approval",), "revise": ("waiting_approval",), "continue": ("waiting_approval",), "answer": ("waiting_info",)}
RESUME_CMD = {"approve": "/duyet", "revise": "/sua", "continue": "/chapnhan", "answer": "/bosung", "cancel": "/huy"}
_STATUS_VN = {"queued": "đang xếp hàng", "running": "đang chạy", "waiting_info": "đang chờ thông tin", "waiting_approval": "đang chờ duyệt",
              "blocked": "đang bị dừng", "done": "đã xong", "cancelled": "đã hủy"}


def resume_block(job: dict, action: str, candidates: Sequence[str] = ()) -> str:
    """'' nếu lệnh hợp lệ với trạng thái hiện tại của job; không thì câu báo lý do và lệnh nên dùng (không đụng job).
    candidates: các job của người này đang chờ đúng việc đó, để gợi ý khi gõ nhầm mã."""
    jid, st, cmd = job["job_id"], job["status"], RESUME_CMD.get(action, "/" + action)
    if action == "cancel":
        if st not in ("done", "cancelled"):
            return ""
        return f"Job {jid} {_STATUS_VN[st]}, không còn gì để hủy."
    if st in RESUME_STATUS.get(action, ()):
        return ""
    hint = {"waiting_info": f"Job này đang chờ thông tin: /bosung {jid} <nội dung>",
            "waiting_approval": f"Job này đang chờ duyệt: /duyet {jid}  hoặc  /sua {jid} <góp ý>",
            "blocked": f"Job này bị dừng: /tieptuc {jid} sau khi xử lý nguyên nhân",
            "done": "Job này đã xong, không cần trả lời gì thêm.", "cancelled": "Job này đã hủy.",
            }.get(st, "Job này đang chạy, chưa cần bạn làm gì.")
    msg = f"Chưa gửi gì cả: job {jid} {_STATUS_VN.get(st, st)}, không có việc nào cần {cmd}.\n{hint}"
    if candidates:
        msg += f"\nJob của bạn đang chờ {cmd}: " + ", ".join(candidates) + " (kiểm tra lại mã job)"
    return msg


def parse_cap(args: Sequence[str]) -> float | None:
    """'8' hoặc '$8,5' -> 8.0 / 8.5. Không hợp lệ hoặc <= 0 -> None."""
    if not args:
        return None
    try:
        v = float(args[0].lstrip("$").replace(",", "."))
    except ValueError:
        return None
    return v if v > 0 else None


def status_line(r: dict, meter=None) -> str:
    line = f"{r['job_id']} · {r['status']} · {r['stage']} · {r['video_type']}"
    if meter:
        t = meter.job_total(r["job_id"])
        if t["calls"]:
            cap = meter.cap_for(r["job_id"])
            line += f" · AI ~${t['usd']:.2f}" + (f"/${cap:.2f}" if cap else "")
    return line


def chunks(text: str, n: int = 3800) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


class TelegramNotifier:
    def __init__(self, cfg: Config):
        self.cfg, self.app, self.loop = cfg, None, None

    def attach(self, app, loop) -> None:
        self.app, self.loop = app, loop

    def _call(self, coro, timeout: float = 60) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    @staticmethod
    def markup(buttons):
        """Danh sách nút (ui.Keyboard) -> InlineKeyboardMarkup của Telegram; không có nút thì None."""
        if not buttons:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in buttons])

    def send(self, user_id: str, text: str, files: Sequence[str] = (), buttons=None) -> None:
        if not self.app:
            return
        parts = chunks(text)
        for i, c in enumerate(parts):
            # nút đặt ở tin CUỐI cùng của đoạn chữ (tin dài bị cắt nhiều khúc thì nút nằm ở khúc cuối, sát chỗ người dùng đọc xong)
            kw = {"reply_markup": self.markup(buttons)} if (buttons and i == len(parts) - 1) else {}
            self._call(self.app.bot.send_message(chat_id=int(user_id), text=c, **kw))  # không dùng parse_mode: tránh chèn định dạng
        for f in files:
            p = Path(f)
            if not p.is_file():
                continue
            if p.stat().st_size > self.cfg.max_file_send_mb * 1_000_000:
                self._call(self.app.bot.send_message(chat_id=int(user_id), text=f"(file quá lớn để gửi qua Telegram, nằm trên máy dựng: {p})"))
                continue

            async def _doc(path=p):
                with open(path, "rb") as fh:
                    # mặc định của thư viện chỉ chờ vài chục giây khi tải file lên: mạng yếu là báo TimedOut dù file chỉ vài MB
                    await self.app.bot.send_document(chat_id=int(user_id), document=fh, filename=path.name, read_timeout=600,
                                                     write_timeout=600, connect_timeout=30, pool_timeout=30)

            try:
                self._call(_doc(), timeout=900)
            except Exception as e:  # noqa: BLE001 - gửi file lỗi KHÔNG được làm dừng job (kết quả đã nằm trên NAS): báo đường dẫn thay thế
                self._call(self.app.bot.send_message(
                    chat_id=int(user_id), text=f"(không gửi được file {p.name} qua Telegram: {e.__class__.__name__}. File nằm tại: {p})"))


def run_bot(cfg: Config, runner, deps, notifier: TelegramNotifier) -> None:
    import os

    from telegram import Update
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

    token = os.environ.get(cfg.telegram_token_env)
    if not token:
        raise SystemExit(f"Thiếu biến môi trường {cfg.telegram_token_env}")
    admins = set(cfg.admin_ids)
    # nhật ký ra stderr (bộ canh ghi vào C:\VideoProjects\_jobs\logs\bot-*.log.err). httpx ghi một dòng mỗi lần hỏi Telegram: chỉ lấy cảnh báo.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("vidagents.bot")

    async def guard(update: Update) -> str | None:
        uid = str(update.effective_user.id)
        if uid not in set(cfg.allowed_user_ids) | admins:   # đọc danh sách MỖI LẦN: /themnguoi có hiệu lực ngay
            # (chưa có quyền thì bot chỉ trả ID của chính họ, không lộ ai khác trong danh sách)
            await update.message.reply_text("Bạn chưa có quyền dùng bot này. Nhờ quản trị thêm ID Telegram của bạn: " + uid)
            return None
        return uid

    def own_job(uid: str, args: list[str], allow_admin: bool = False) -> tuple[dict | None, str]:
        if not args:
            return None, "Thiếu mã job. Ví dụ: /duyet 20261106-femfresh-01"
        job = deps.jobs.get(args[0])
        if not job:
            return None, f"Không có job {args[0]}"
        if job["requester_id"] != uid and not (allow_admin and uid in admins):
            return None, "Chỉ người giao việc mới thao tác được job này."
        return job, ""

    from . import daily as dailymod
    from . import typewiz, ui, wizard
    from .chat import ChatAssistant

    def dup_note(jid: str) -> str:
        """Cảnh báo trùng footage (rỗng nếu không trùng). Lỗi đọc sổ không được làm mất tin "Đã nhận"."""
        try:
            return same_footage_note(deps.jobs.same_footage(jid))
        except Exception:  # noqa: BLE001
            return ""

    assistant = ChatAssistant(cfg, deps)
    markup = notifier.markup
    daily = dailymod.Daily(cfg, deps.jobs, dailymod.DailyStore(cfg.jobs_db), deps.types, getattr(deps, "meter", None))

    def capture_profile(wz_state, start: dict) -> None:
        """Giao việc bằng phiếu thì nhớ phiếu làm HỒ SƠ BRIEF của KOC đó: các lần sau hỏi hàng ngày chỉ cần chọn giá và ưu đãi."""
        try:
            if wz_state and wz_state.get("values"):
                project = wizard._project_of(start["footage_dir"], Path(cfg.footage_root))
                daily.store.save_profile(project, start["video_type"], start["brand"], dict(wz_state["values"]), start["footage_dir"])
        except Exception:  # noqa: BLE001 - lưu hồ sơ lỗi không được làm mất tin "Đã nhận"
            log.exception("lưu hồ sơ brief lỗi")

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/start và /menu: menu nút bấm. Người chưa được cấp quyền thì nhận lại ID của họ (để gửi cho quản trị)."""
        uid = await guard(update)
        if not uid:
            return
        name = (update.effective_user.first_name or "").strip()
        await update.message.reply_text(f"Xin chào{' ' + name if name else ''}! Chọn việc cần làm bên dưới, hoặc cứ nhắn câu hỏi bình thường "
                                        "(ví dụ: \"job của tôi tới đâu rồi?\"). Gõ /menu bất cứ lúc nào để mở lại menu.",
                                        reply_markup=markup(ui.main_menu(uid in admins)))

    async def start_typewiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Thêm dạng video bằng nút (chỉ admin; người gọi phải kiểm trước). Trạng thái ở user_data["tw"], tách với "wz" của giao việc."""
        ctx.user_data.pop("wz", None)
        ctx.user_data.pop("await", None)
        st, r = typewiz.start(deps.types)
        ctx.user_data["tw"] = st
        await update.effective_message.reply_text(r.text, reply_markup=markup(r.kb))

    async def themloai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        if uid not in admins:
            await update.message.reply_text("Lệnh này chỉ dành cho quản trị viên. Cần thêm dạng video thì nhắn quản trị viên. Gõ /loai để xem các dạng đang có.")
            return
        await start_typewiz(update, ctx)

    async def send_daily(update: Update, uid: str) -> None:
        """/homnay và nút menu: hỏi ngay bây giờ (cùng luồng với lượt hỏi tự động 8:30 và 21:00)."""
        st = await asyncio.to_thread(daily.manual_session, uid)
        r = daily.pick_view(st)
        daily.store.save_session(uid, st)
        await update.effective_message.reply_text(r.text, reply_markup=markup(r.kb))

    async def open_wizard_at(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: str, folder: str) -> None:
        """Mở phiếu giao việc (luồng /video) tại một thư mục; folder rỗng thì từ màn chọn thư mục."""
        ctx.user_data.pop("tw", None)
        daily.cancel_text(uid)
        recent = list(dict.fromkeys(j["footage_dir"] for j in deps.jobs.list(uid, active_only=False) if j.get("footage_dir")))[:3]
        past = [{"footage_dir": j.get("footage_dir", ""), "video_type": j.get("video_type", ""), "brand": j.get("brand", "")}
                for j in deps.jobs.list(uid, active_only=False) if j.get("status") != "cancelled"][:20]
        st = wizard.new_state(cfg, recent, past)
        ctx.user_data["wz"] = st
        r = await asyncio.to_thread(wizard._pick_folder, st, cfg, folder, deps.types) if folder else await asyncio.to_thread(wizard.folder_view, st, cfg)
        await update.effective_message.reply_text(r.text, reply_markup=markup(r.kb))

    async def homnay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        await send_daily(update, uid)

    async def start_wizard(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: str):
        ctx.user_data.pop("tw", None)
        daily.cancel_text(uid)
        recent = list(dict.fromkeys(j["footage_dir"] for j in deps.jobs.list(uid, active_only=False) if j.get("footage_dir")))[:3]
        past = [{"footage_dir": j.get("footage_dir", ""), "video_type": j.get("video_type", ""), "brand": j.get("brand", "")}
                for j in deps.jobs.list(uid, active_only=False) if j.get("status") != "cancelled"][:20]
        st = wizard.new_state(cfg, recent, past)
        ctx.user_data["wz"] = st
        r = await asyncio.to_thread(wizard.folder_view, st, cfg)
        await update.effective_message.reply_text(r.text, reply_markup=markup(r.kb))

    async def video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        if len((update.message.text or "").split()) <= 1:   # /video không kèm gì: giao việc bằng nút bấm thay vì bắt gõ đường dẫn
            await start_wizard(update, ctx, uid)
            return
        # kiểm ngay, trước khi tạo job: sai thì báo cách sửa, không tạo job, không tốn tiền. Chưa có brief thì gửi phiếu điền sẵn.
        plan = await asyncio.to_thread(plan_video_request, cfg, deps.types, update.message.text or "")
        if "reply" in plan:
            await update.message.reply_text(plan["reply"][:4000])
            return
        name = update.effective_user.full_name or uid
        jid = runner.start(requester_id=uid, requester_name=name, **plan["start"])
        await update.message.reply_text(f"Đã nhận, mã job: {jid}" + dup_note(jid))

    async def act(uid: str, job_id: str, action: str, note: str = "", mode: str | None = None, arg: str = "") -> tuple[str, bool]:
        """Làm một việc với job. Dùng chung cho lệnh gõ và nút bấm (một nơi kiểm quyền + trạng thái).
        -> (câu trả lời cho người dùng, True nếu việc đã xong hoặc bị từ chối hẳn: nút trên tin cũ nên bỏ đi; False: giữ nút để thử lại)."""
        from . import handedit as H

        if action in ("approve", "revise", "continue", "answer", "cancel"):
            job, err = own_job(uid, [job_id], allow_admin=(action == "cancel"))
            if err:
                return err, True
            wrong = resume_block(job, action, [r["job_id"] for r in deps.jobs.list(uid, active_only=True) if r["status"] in RESUME_STATUS.get(action, ())])
            if wrong:
                return wrong, True
            runner.resume(job["job_id"], {"action": action, "by": uid, "note": note, "mode": mode})
            return "Đã ghi nhận.", True
        job, err = own_job(uid, [job_id], allow_admin=(action == "resume"))   # tiền và duyệt: chỉ người giao việc; chạy tiếp: cả admin
        if err:
            return err, True
        if action == "resume":
            runner.continue_(job["job_id"])
            return "Đang chạy tiếp.", True
        if action == "handedit":
            if job["status"] != "waiting_approval" or job["stage"] not in ("duyet_2", "hand_review"):
                return "Chỉ dùng được khi job đang chờ Duyệt 2 (đã có bản xem trước) hoặc sau khi soi lỗi bản chỉnh tay.", True
            why = H.available(cfg)
            if why:
                return f"Chưa chỉnh tay được: {why}", True
            runner.resume(job["job_id"], {"action": "handedit", "by": uid})
            return "Đang đóng gói dự án lên NAS cho VideoFac (vài phút nếu file lớn). Xong bot sẽ gửi hướng dẫn mở.", True
        if action == "done":
            if job["status"] != "waiting_approval" or job["stage"] != "hand_edit":
                return "Job này chưa ở bước chờ chỉnh tay (hoặc bot còn đang đóng gói). Bấm \"Xem tiến độ\" hoặc gõ /trangthai.", True
            ex = await asyncio.to_thread(H.find_export, cfg, H.pack_dir(job["footage_dir"], job["job_id"]))
            if ex.problems:
                return ("Chưa nhận được bản xuất (job vẫn chờ, chưa tốn tiền):\n- " + "\n- ".join(ex.problems)
                        + "\nXuất xong trong VideoFac thì bấm lại nút hoặc gõ /xong " + job["job_id"]), False
            runner.resume(job["job_id"], {"action": "done", "by": uid})
            return f"Đã nhận bản xuất: {ex.info}. Đang tạo bản nhẹ và soi lỗi bản cuối, rồi giao kết quả.", True
        if action == "cap":
            cap = parse_cap([arg])
            if cap is None:
                return "Cú pháp: /nangtran <job> <số USD>, ví dụ /nangtran 20261106-femfresh-01 8", True
            old = deps.meter.cap_for(job["job_id"])
            if old is not None and cap <= old:
                return f"Trần mới phải lớn hơn trần hiện tại (${old:.2f}).", True
            deps.meter.set_cap(job["job_id"], cap)
            if job["status"] == "blocked":
                runner.continue_(job["job_id"])
                return f"Đã nâng trần lên ${cap:.2f} và chạy tiếp.", True
            return f"Đã nâng trần lên ${cap:.2f}.", True
        return "Việc không hợp lệ.", True

    def resumer(action: str, need_text: bool = False, allow_admin: bool = False):
        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            uid = await guard(update)
            if not uid:
                return
            if not ctx.args:
                await update.message.reply_text("Thiếu mã job. Ví dụ: /duyet 20261106-femfresh-01")
                return
            text = " ".join(ctx.args[1:]).strip()
            mode = None
            m = re.search(r"chedo=([ABCSabcs])", text)
            if m:
                mode, text = m.group(1).upper(), re.sub(r"chedo=[ABCSabcs]", "", text).strip()
            if need_text and not text:
                job, err = own_job(uid, ctx.args, allow_admin)   # báo lỗi mã job/quyền trước, rồi mới nhắc thiếu nội dung
                await update.message.reply_text(err or "Thiếu nội dung.")
                return
            msg, _ = await act(uid, ctx.args[0], action, note=text, mode=mode)
            await update.message.reply_text(msg)
        return handler

    def by_job_arg(action: str):
        """Lệnh gõ dạng `/lệnh <mã job>`: gọi act() như nút bấm (một nơi kiểm quyền và trạng thái)."""
        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            uid = await guard(update)
            if not uid:
                return
            if not ctx.args:
                await update.message.reply_text("Thiếu mã job. Ví dụ: /xong 20261106-femfresh-01")
                return
            msg, _ = await act(uid, ctx.args[0], action)
            await update.message.reply_text(msg)
        return handler

    chinhtay, xong, tieptuc = by_job_arg("handedit"), by_job_arg("done"), by_job_arg("resume")   # /chinhtay /xong /tieptuc

    # ───── quản lý người dùng (chỉ admin) ─────
    async def admin_guard(update: Update) -> str | None:
        uid = await guard(update)
        if uid and uid not in admins:
            await update.message.reply_text("Lệnh này chỉ dành cho quản trị viên.")
            return None
        return uid

    def known_name(i: str) -> str | None:
        rows = deps.jobs.list(requester_id=i, active_only=False)
        return rows[0].get("requester_name") if rows else None

    async def themnguoi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import access

        uid = await admin_guard(update)
        if not uid:
            return
        if not ctx.args:
            await update.message.reply_text("Cú pháp: /themnguoi <ID Telegram> [tên gọi]\nVí dụ: /themnguoi 5234517033 Hoa\n"
                                            "ID: người đó nhắn /start cho bot, bot trả về ID của họ.")
            return
        try:
            msg = await asyncio.to_thread(access.add_user, cfg, ctx.args[0], uid, " ".join(ctx.args[1:]))
        except access.AccessError as e:
            await update.message.reply_text(str(e))
            return
        # báo cho người mới (chỉ được nếu họ đã từng nhắn /start cho bot; không thì Telegram từ chối)
        try:
            await asyncio.to_thread(notifier.send, ctx.args[0].strip(), "Bạn đã được thêm vào danh sách giao việc. Gõ /start để xem các lệnh, "
                                    "hoặc /video để giao việc dựng video.")
        except Exception:  # noqa: BLE001
            msg += "\n(Chưa nhắn được cho họ: họ cần nhắn /start cho bot một lần trước.)"
        await update.message.reply_text(msg)

    async def xoanguoi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import access

        uid = await admin_guard(update)
        if not uid:
            return
        if not ctx.args:
            await update.message.reply_text("Cú pháp: /xoanguoi <ID Telegram>")
            return
        try:
            active = len(deps.jobs.list(requester_id=ctx.args[0].strip(), active_only=True))
            msg = await asyncio.to_thread(access.remove_user, cfg, ctx.args[0], uid, active)
        except access.AccessError as e:
            await update.message.reply_text(str(e))
            return
        await update.message.reply_text(msg)

    async def danhsach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import access

        if not await admin_guard(update):
            return
        await update.message.reply_text(access.render_list(cfg, known_name))

    async def baihoc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        job, err = own_job(uid, ctx.args)
        if err:
            await update.message.reply_text(err)
            return
        rest = " ".join(ctx.args[1:]).lower()
        if rest.strip() in ("bo", "bỏ", ""):
            payload = {"action": "skip", "by": uid}
        else:
            pins = [int(x) for x in re.findall(r"\d+", rest.partition("ghim")[2])] if "ghim" in rest else []
            ids = [int(x) for x in re.findall(r"\d+", rest.partition("ghim")[0])]
            payload = {"action": "select", "by": uid, "ids": ids + pins, "pin": pins}
        runner.resume(job["job_id"], payload)
        await update.message.reply_text("Đã ghi nhận.")

    async def trangthai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        if ctx.args:
            job, err = own_job(uid, ctx.args, allow_admin=True)
            rows = [job] if job else []
            if err:
                await update.message.reply_text(err)
                return
        else:
            rows = deps.jobs.list(None if uid in admins else uid)
        if not rows:
            await update.message.reply_text("Không có job đang chạy.")
            return
        busy = deps.tools.locks.busy()
        lines = [status_line(r, deps.meter) for r in rows]
        await update.message.reply_text("\n".join(lines) + f"\n(suất render đang dùng: {busy}/{cfg.render_slots})")

    async def nangtran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        if len(ctx.args) < 2:
            await update.message.reply_text("Cú pháp: /nangtran <job> <số USD>, ví dụ /nangtran 20261106-femfresh-01 8")
            return
        msg, _ = await act(uid, ctx.args[0], "cap", arg=ctx.args[1])   # tiền: chỉ người giao việc quyết, quản trị không nâng hộ
        await update.message.reply_text(msg)

    async def loai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text("\n".join(f"{t.id} · {t.name_vi} · chế độ đề xuất {t.suggested_mode}" for t in deps.types.values()))

    async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        await update.message.reply_text(ping_text(deps.jobs, deps.tools.locks.busy(), cfg.render_slots, uid, uid in admins))

    async def unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Lệnh không có: luôn trả lời (im lặng làm người dùng tưởng bot hỏng)."""
        if not update.message or not await guard(update):
            return
        await update.message.reply_text(unknown_command_text(update.message.text or ""), reply_markup=markup(ui.main_menu(str(update.effective_user.id) in admins)))

    async def reply_with(q, text: str, kb=None, edit: bool = False) -> None:
        """Gửi chữ + nút; edit=True thì sửa ngay tin có nút vừa bấm (đỡ đầy khung chat), lỗi 'không đổi' thì bỏ qua."""
        if edit:
            try:
                await q.edit_message_text(text, reply_markup=markup(kb))
                return
            except Exception:  # noqa: BLE001 - tin quá cũ / nội dung không đổi: gửi tin mới
                pass
        await q.message.reply_text(text, reply_markup=markup(kb))

    def job_buttons(job: dict | None):
        from . import handedit as H

        if not job:
            return []
        return ui.job_keyboard(job, cap=deps.meter.cap_for(job["job_id"]) if deps.meter else None, can_handedit=not H.available(cfg))

    async def finish_wizard(q, ctx, r, uid: str) -> None:
        """Kết quả một bước wizard: có `start` thì tạo job, hết thì xóa trạng thái; còn lại sửa tin hiện tại sang bước mới."""
        if r.start:
            wz_state = ctx.user_data.pop("wz", None)
            jid = runner.start(requester_id=uid, requester_name=q.from_user.full_name or uid, **r.start)
            capture_profile(wz_state, r.start)
            await reply_with(q, f"✅ Đã nhận, mã job: {jid}\nBot sẽ nhắn khi cần bạn duyệt hoặc trả lời. Bấm nút bên dưới để xem tiến độ bất cứ lúc nào." + dup_note(jid),
                             ui.jobs_keyboard([deps.jobs.get(jid)]), edit=True)
            return
        if r.end:
            ctx.user_data.pop("wz", None)
        await reply_with(q, r.text, r.kb, edit=True)
        if not r.end:   # nhớ tin đang hiện bảng phiếu: chữ người dùng gõ sau đó cập nhật TẠI CHỖ tin này
            ctx.user_data["wz_msg"] = (q.message.chat_id, q.message.message_id)

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = str(q.from_user.id)
        if uid not in set(cfg.allowed_user_ids) | admins:
            await q.answer("Bạn chưa có quyền dùng bot này.", show_alert=True)
            return
        try:
            await q.answer()   # tắt vòng xoay trên nút
        except Exception as e:  # noqa: BLE001 - nút quá cũ ("query is too old") không được chặn việc xử lý: vẫn làm việc rồi trả lời bằng tin mới
            log.warning("answer_callback_query lỗi (%s): %s", e.__class__.__name__, str(e)[:120])
        parsed = ui.parse_cb(q.data or "")
        if not parsed:
            return
        kind, code, ref, arg = parsed
        if kind == "w":   # giao việc bằng nút: ref là đối số của bước
            st = ctx.user_data.get("wz")
            if not st:
                await reply_with(q, "Phiên giao việc đã hết hạn. Gõ /menu rồi chọn \"Giao việc mới\".")
                return
            r = await asyncio.to_thread(wizard.on_callback, st, cfg, deps.types, code, "" if ref == "-" else ref)
            await finish_wizard(q, ctx, r, uid)
            return
        if kind == "d":   # hỏi hàng ngày "hôm nay dựng video nào": trạng thái nằm trong SQLite (bền qua khởi động lại)
            dst = daily.store.load_session(uid)
            if not dst:
                await reply_with(q, dailymod.EXPIRED)
                return
            r = await asyncio.to_thread(daily.on_callback, dst, code, ref)
            if r.save:
                daily.store.save_session(uid, dst)
            if r.wizard is not None:
                await reply_with(q, r.text)
                await open_wizard_at(update, ctx, uid, r.wizard)
                return
            if r.starts or r.failed or r.firsts:
                started, notes = [], {}
                for s in r.starts:   # giao tuần tự trong vòng lặp sự kiện: mã job được cấp nguyên tử, không trùng
                    jid = runner.start(requester_id=uid, requester_name=q.from_user.full_name or uid, **s["args"])
                    daily.store.save_profile(s["project"], s["vtype"], s["brand"], s["values"], s["folder"])
                    started.append((s, jid))
                    same = deps.jobs.same_footage(jid)
                    if same:
                        notes[jid] = f" ⚠ cùng thư mục với {same[0]['job_id']}"
                await reply_with(q, daily.done_text(started, r.failed, r.firsts, notes), ui.jobs_keyboard([deps.jobs.get(j) for _s, j in started]), edit=True)
                for it in r.firsts:
                    p = daily.first_time_prompt(dst, it)
                    await q.message.reply_text(p.text, reply_markup=markup(p.kb))
                return
            await reply_with(q, r.text, r.kb, edit=r.text != dailymod.EXPIRED)
            return
        if kind == "t":   # thêm dạng video bằng nút: chỉ quản trị viên (đọc danh sách admin mỗi lần bấm)
            if uid not in admins:
                await reply_with(q, "Chỉ quản trị viên được thêm dạng video.")
                return
            tw = ctx.user_data.get("tw")
            if not tw:
                await reply_with(q, typewiz.EXPIRED)
                return
            r = typewiz.on_callback(tw, cfg, deps.types, code, "" if ref == "-" else ref)
            if r.end:
                ctx.user_data.pop("tw", None)
            await reply_with(q, r.text, r.kb, edit=r.text != typewiz.EXPIRED_BTN)   # nút cũ: báo hết hạn bằng tin mới, không sửa đè tin đang hiện bước khác
            return
        if kind == "m":   # menu
            if code == "today":
                await send_daily(update, uid)
            elif code == "addtype" and uid in admins:
                await start_typewiz(update, ctx)
            elif code == "new":
                await start_wizard(update, ctx, uid)
            elif code == "jobs":
                rows = deps.jobs.list(None if uid in admins else uid, active_only=False)[:8]
                await reply_with(q, "📋 JOB GẦN ĐÂY (bấm để xem tiến độ)" if rows else "Chưa có job nào. Bấm \"Giao việc mới\" trong /menu.", ui.jobs_keyboard(rows))
            elif code == "cost":
                await reply_with(q, cost_text(deps.jobs, deps.meter, uid, uid in admins, assistant.spent_today(uid), cfg.chat_daily_usd))
            elif code == "ping":
                await reply_with(q, ping_text(deps.jobs, deps.tools.locks.busy(), cfg.render_slots, uid, uid in admins))
            elif code == "help":
                await reply_with(q, HELP)
            elif code == "users" and uid in admins:
                from . import access

                await reply_with(q, access.render_list(cfg, known_name))
            return
        # kind == "j": thao tác với một job (ref là mã job)
        action = ui.ACTIONS.get(code)
        job = deps.jobs.get(ref)
        if code == "st":
            if not job or (job["requester_id"] != uid and uid not in admins):
                await reply_with(q, f"Không có job {ref}.")
            else:
                await reply_with(q, status_view(job, deps.meter), job_buttons(job))
            return
        if code == "no":   # hủy là việc không đảo ngược được: hỏi lại bằng chính bàn phím này
            await q.edit_message_reply_markup(reply_markup=markup(ui.confirm_cancel_keyboard(ref)))
            return
        if code == "nc":
            await q.edit_message_reply_markup(reply_markup=markup(job_buttons(job)))
            return
        if code in ("rv", "bs"):   # cần chữ của người dùng: hỏi rồi chờ tin nhắn kế tiếp
            _job, err = own_job(uid, [ref])
            wrong = err or resume_block(_job, action)
            if wrong:
                await reply_with(q, wrong)
                return
            ctx.user_data["await"] = {"action": action, "job": ref, "ts": time.time()}
            await reply_with(q, "✏️ Gõ góp ý sửa của bạn rồi gửi (hoặc /huybo để bỏ)." if action == "revise" else
                             "💬 Gõ nội dung bổ sung rồi gửi (hoặc /huybo để bỏ).")
            return
        if code == "xc":
            msg, drop = await act(uid, ref, "cancel")
        elif action:
            msg, drop = await act(uid, ref, action, arg=arg)
        else:
            return
        if drop:
            try:
                await q.edit_message_reply_markup(reply_markup=None)   # nút của tin này đã dùng xong
            except Exception:  # noqa: BLE001
                pass
        await q.message.reply_text(msg)

    async def huybo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        had = ctx.user_data.pop("await", None) or ctx.user_data.pop("wz", None) or ctx.user_data.pop("tw", None)
        ctx.user_data.pop("wz", None)
        ctx.user_data.pop("tw", None)
        await update.message.reply_text("Đã bỏ." if had else "Không có gì đang chờ bạn trả lời.", reply_markup=markup(ui.main_menu(str(update.effective_user.id) in admins)))

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Chữ thường (không phải lệnh): trả lời cho bước đang chờ (góp ý, bổ sung, ô của phiếu) hoặc hỏi đáp tự do."""
        if not update.message:
            return
        uid = await guard(update)
        if not uid:
            return
        text = (update.message.text or "").strip()
        wait = ctx.user_data.get("await")
        if wait and time.time() - wait["ts"] < 10 * 60:   # câu trả lời cho nút "Sửa"/"Bổ sung" vừa bấm
            ctx.user_data.pop("await", None)
            msg, _ = await act(uid, wait["job"], wait["action"], note=text)
            await update.message.reply_text(msg)
            return
        ctx.user_data.pop("await", None)
        tw = ctx.user_data.get("tw")
        if tw:   # đang thêm dạng video: chữ là tên, mã, số giây, ô riêng hoặc ghi chú tùy bước
            if uid not in admins:
                ctx.user_data.pop("tw", None)
            else:
                r = typewiz.on_text(tw, cfg, deps.types, text)
                if r is not None:
                    if r.end:
                        ctx.user_data.pop("tw", None)
                    await update.message.reply_text(r.text, reply_markup=markup(r.kb))
                    return
        dst = daily.store.load_session(uid)   # đang ở bước "gõ giá/ưu đãi mới" của lượt hỏi hàng ngày?
        if dst and dst.get("stage") == "text" and daily.text_fresh(dst):
            dr = daily.on_text(dst, text)
            if dr is not None:
                daily.store.save_session(uid, dst)
                await update.message.reply_text(dr.text, reply_markup=markup(dr.kb))
                return
        st = ctx.user_data.get("wz")
        if st:
            r = await asyncio.to_thread(wizard.on_text, st, cfg, deps.types, text)
            if r is not None:
                if r.start:
                    ctx.user_data.pop("wz", None)
                    jid = runner.start(requester_id=uid, requester_name=update.effective_user.full_name or uid, **r.start)
                    capture_profile(st, r.start)
                    await update.message.reply_text(f"✅ Đã nhận, mã job: {jid}" + dup_note(jid), reply_markup=markup(ui.jobs_keyboard([deps.jobs.get(jid)])))
                    return
                if r.panel and ctx.user_data.get("wz_msg"):   # bảng phiếu: sửa tại chỗ tin bảng thay vì gửi tin mới (đỡ đầy khung chat)
                    cid, mid = ctx.user_data["wz_msg"]
                    try:
                        await ctx.bot.edit_message_text(chat_id=cid, message_id=mid, text=r.text, reply_markup=markup(r.kb))
                        return
                    except Exception:  # noqa: BLE001 - tin quá cũ hoặc nội dung không đổi: gửi tin mới
                        pass
                await update.message.reply_text(r.text, reply_markup=markup(r.kb))
                return
        await update.effective_chat.send_action("typing")
        ans = await asyncio.to_thread(assistant.ask, uid, uid in admins, text)
        kb = []
        if ans.job_ids:   # câu trả lời nói về một job: kèm luôn nút thao tác của job đó
            kb = job_buttons(deps.jobs.get(ans.job_ids[-1])) if (deps.jobs.get(ans.job_ids[-1]) or {}).get("requester_id") == uid else []
        kb = kb + [[("📋 Menu", "m|jobs|-")]]
        await update.message.reply_text(ans.text, reply_markup=markup(kb))

    async def on_init(app):
        notifier.attach(app, asyncio.get_running_loop())
        try:   # job đang chạy dở lúc bot tắt/khởi động lại: đánh dấu dừng và báo ngay (chỉ chạy được SAU khi attach, vì phải gửi tin)
            stopped = await asyncio.to_thread(runner.recover_interrupted)
            if stopped:
                log.warning("đã đánh dấu dừng %d job đang chạy dở lúc khởi động lại: %s", len(stopped), ", ".join(stopped))
        except Exception:  # noqa: BLE001 - phục hồi lỗi không được chặn bot khởi động
            log.exception("recover_interrupted lỗi")
        if cfg.daily_enabled:   # lượt hỏi tự động (mặc định 8:30 và 21:00), chỉ chạy được SAU khi attach vì phải gửi tin
            dailymod.start_daily_thread(daily, lambda uid, text, kb: notifier.send(uid, text, buttons=kb), log=lambda m: log.info(m))
        try:   # danh sách lệnh hiện khi người dùng gõ "/" (đỡ phải nhớ tên lệnh)
            from telegram import BotCommand

            await app.bot.set_my_commands([BotCommand("menu", "Mở menu nút bấm"), BotCommand("video", "Giao việc dựng video mới"),
                                           BotCommand("trangthai", "Xem tiến độ các job"), BotCommand("ping", "Kiểm tra bot còn chạy"),
                                           BotCommand("homnay", "Hôm nay dựng video nào"), BotCommand("loai", "Các loại video"), BotCommand("help", "Danh sách lệnh")])
        except Exception:  # noqa: BLE001 - không đặt được danh sách lệnh thì bot vẫn chạy bình thường
            pass

    async def trace(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Ghi nhật ký MỌI thao tác bot nhận được (ai, nút/lệnh nào; chữ thường chỉ ghi độ dài, không ghi nội dung)."""
        u = update.effective_user
        if update.callback_query:
            log.info("nhận NÚT %s từ %s", update.callback_query.data, u.id if u else "?")
        elif update.message:
            t = update.message.text or ""
            log.info("nhận %s từ %s", t.split()[0][:30] if t.startswith("/") else f"chữ ({len(t)} ký tự)", u.id if u else "?")

    async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
        """Lỗi trong lúc xử lý: ghi nhật ký đầy đủ, báo người dùng NGAY (không im lặng) và báo quản trị."""
        err = ctx.error
        log.error("Lỗi khi xử lý %s", getattr(update, "to_dict", lambda: update)(), exc_info=err)
        try:
            msg = getattr(update, "effective_message", None)
            if msg is not None:
                await msg.reply_text("⚠ Bot gặp lỗi khi xử lý yêu cầu này (đã ghi nhật ký và báo quản trị). Bạn thử lại sau ít phút, hoặc gõ /menu.")
        except Exception:  # noqa: BLE001
            pass
        for a in list(admins)[:1]:
            try:   # gửi thẳng bằng bot (không qua notifier.send: nó chờ vòng lặp này nên sẽ tự khóa)
                await ctx.bot.send_message(chat_id=int(a), text=f"⚠ Lỗi bot: {err.__class__.__name__}: {str(err)[:300]}")
            except Exception:  # noqa: BLE001
                pass

    app = Application.builder().token(token).post_init(on_init).build()
    app.add_handler(TypeHandler(Update, trace), group=-1)
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("huybo", huybo))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(CommandHandler("video", video))
    app.add_handler(CommandHandler("phieu", video))
    app.add_handler(CommandHandler("duyet", resumer("approve")))
    app.add_handler(CommandHandler("sua", resumer("revise", need_text=True)))
    app.add_handler(CommandHandler("bosung", resumer("answer", need_text=True)))
    app.add_handler(CommandHandler("chapnhan", resumer("continue")))
    app.add_handler(CommandHandler("huy", resumer("cancel", allow_admin=True)))
    app.add_handler(CommandHandler("themnguoi", themnguoi))
    app.add_handler(CommandHandler("xoanguoi", xoanguoi))
    app.add_handler(CommandHandler("danhsach", danhsach))
    app.add_handler(CommandHandler("chinhtay", chinhtay))
    app.add_handler(CommandHandler("xong", xong))
    app.add_handler(CommandHandler("baihoc", baihoc))
    app.add_handler(CommandHandler("tieptuc", tieptuc))
    app.add_handler(CommandHandler("trangthai", trangthai))
    app.add_handler(CommandHandler("nangtran", nangtran))
    app.add_handler(CommandHandler("loai", loai))
    app.add_handler(CommandHandler("themloai", themloai))
    app.add_handler(CommandHandler("homnay", homnay))
    app.add_handler(CommandHandler("ping", ping))
    # cuối cùng (các lệnh trên khớp trước nên không bị chặn): chữ thường -> bước đang chờ hoặc hỏi đáp; lệnh không có -> gợi ý
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    # Telegram nhớ danh sách loại cập nhật của lần lấy tin trước (có thể chỉ "message" nếu từng có chương trình khác
    # dùng chung token). Nếu thiếu callback_query thì mọi nút bấm biến mất mà không có lỗi nào. Luôn nói rõ: lấy tất cả.
    app.run_polling(allowed_updates=Update.ALL_TYPES)
