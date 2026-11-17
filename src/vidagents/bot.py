"""Bot Telegram (python-telegram-bot v21+). Mỗi người trong danh sách cho phép giao việc; chỉ người giao được duyệt job của mình.

Mỗi người phải nhắn riêng cho bot một lần (/start) thì bot mới gửi tin riêng cho họ được.
CHƯA ĐƯỢC CHẠY THỬ với Telegram thật (không có token trong môi trường dựng khung).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Sequence

from .config import Config

HELP = (
    "Lệnh:\n"
    "/video \"<thư mục footage>\" <loại video> <thương hiệu>  (xuống dòng rồi dán brief)\n"
    "/duyet <job>   duyệt bước đang chờ\n"
    "/sua <job> <góp ý> [chedo=A|B|C|S]   yêu cầu sửa\n"
    "/bosung <job> <nội dung>   trả lời khi hệ hỏi thêm thông tin\n"
    "/chapnhan <job>   đi tiếp dù còn cảnh báo\n"
    "/baihoc <job> 1,3 | ghim 1 | bo   chọn bài học đáng nhớ\n"
    "/huy <job>   hủy job\n"
    "/trangthai [job]   xem tiến độ và chi phí AI\n"
    "/nangtran <job> <USD>   nâng trần chi phí AI của job rồi chạy tiếp (khi job dừng vì chạm trần)\n"
    "/tieptuc <job>   chạy tiếp job bị dừng / gửi lại yêu cầu duyệt\n"
    "/loai   danh sách loại video"
)
_TOK = re.compile(r'"([^"]+)"|(\S+)')


def parse_video_cmd(text: str) -> tuple[str, str, str, str] | None:
    """'/video "D:\\a b" koc-review femfresh\\n<brief...>' -> (thư mục, loại, thương hiệu, brief)."""
    first, _, brief = text.partition("\n")
    toks = [a or b for a, b in _TOK.findall(first)][1:]
    if len(toks) < 3:
        return None
    return toks[0], toks[1], toks[2], brief.strip()


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

    def _call(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)

    def send(self, user_id: str, text: str, files: Sequence[str] = ()) -> None:
        if not self.app:
            return
        for c in chunks(text):
            self._call(self.app.bot.send_message(chat_id=int(user_id), text=c))  # không dùng parse_mode: tránh chèn định dạng
        for f in files:
            p = Path(f)
            if not p.is_file():
                continue
            if p.stat().st_size > self.cfg.max_file_send_mb * 1_000_000:
                self._call(self.app.bot.send_message(chat_id=int(user_id), text=f"(file quá lớn để gửi qua Telegram, nằm trên máy dựng: {p})"))
                continue

            async def _doc(path=p):
                with open(path, "rb") as fh:
                    await self.app.bot.send_document(chat_id=int(user_id), document=fh, filename=path.name)

            self._call(_doc())


def run_bot(cfg: Config, runner, deps, notifier: TelegramNotifier) -> None:
    import os

    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    token = os.environ.get(cfg.telegram_token_env)
    if not token:
        raise SystemExit(f"Thiếu biến môi trường {cfg.telegram_token_env}")
    allowed = set(cfg.allowed_user_ids) | set(cfg.admin_ids)
    admins = set(cfg.admin_ids)

    async def guard(update: Update) -> str | None:
        uid = str(update.effective_user.id)
        if uid not in allowed:
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

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Xin chào. " + HELP)

    async def video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        parsed = parse_video_cmd(update.message.text or "")
        if not parsed:
            await update.message.reply_text("Cú pháp: /video \"<thư mục footage>\" <loại video> <thương hiệu>\n<brief ở các dòng sau>\n(xem loại video: /loai)")
            return
        folder, vtype, brand, brief = parsed
        name = update.effective_user.full_name or uid
        jid = runner.start(requester_id=uid, requester_name=name, footage_dir=folder, video_type=vtype,
                           brand=brand.lower(), brief_text=brief)
        await update.message.reply_text(f"Đã nhận, mã job: {jid}")

    def resumer(action: str, need_text: bool = False, allow_admin: bool = False):
        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            uid = await guard(update)
            if not uid:
                return
            job, err = own_job(uid, ctx.args, allow_admin)
            if err:
                await update.message.reply_text(err)
                return
            text = " ".join(ctx.args[1:]).strip()
            mode = None
            m = re.search(r"chedo=([ABCSabcs])", text)
            if m:
                mode, text = m.group(1).upper(), re.sub(r"chedo=[ABCSabcs]", "", text).strip()
            if need_text and not text:
                await update.message.reply_text("Thiếu nội dung.")
                return
            runner.resume(job["job_id"], {"action": action, "by": uid, "note": text, "mode": mode})
            await update.message.reply_text("Đã ghi nhận.")
        return handler

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

    async def tieptuc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = await guard(update)
        if not uid:
            return
        job, err = own_job(uid, ctx.args, allow_admin=True)
        if err:
            await update.message.reply_text(err)
            return
        runner.continue_(job["job_id"])
        await update.message.reply_text("Đang chạy tiếp.")

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
        job, err = own_job(uid, ctx.args)  # tiền: chỉ người giao việc quyết, quản trị không nâng hộ
        if err:
            await update.message.reply_text(err)
            return
        cap = parse_cap(ctx.args[1:])
        if cap is None:
            await update.message.reply_text("Cú pháp: /nangtran <job> <số USD>, ví dụ /nangtran 20261106-femfresh-01 8")
            return
        old = deps.meter.cap_for(job["job_id"])
        if old is not None and cap <= old:
            await update.message.reply_text(f"Trần mới phải lớn hơn trần hiện tại (${old:.2f}).")
            return
        deps.meter.set_cap(job["job_id"], cap)
        if job["status"] == "blocked":
            runner.continue_(job["job_id"])
            await update.message.reply_text(f"Đã nâng trần lên ${cap:.2f} và chạy tiếp.")
        else:
            await update.message.reply_text(f"Đã nâng trần lên ${cap:.2f}.")

    async def loai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text("\n".join(f"{t.id} · {t.name_vi} · chế độ đề xuất {t.suggested_mode}" for t in deps.types.values()))

    async def on_init(app):
        notifier.attach(app, asyncio.get_running_loop())

    app = Application.builder().token(token).post_init(on_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("video", video))
    app.add_handler(CommandHandler("duyet", resumer("approve")))
    app.add_handler(CommandHandler("sua", resumer("revise", need_text=True)))
    app.add_handler(CommandHandler("bosung", resumer("answer", need_text=True)))
    app.add_handler(CommandHandler("chapnhan", resumer("continue")))
    app.add_handler(CommandHandler("huy", resumer("cancel", allow_admin=True)))
    app.add_handler(CommandHandler("baihoc", baihoc))
    app.add_handler(CommandHandler("tieptuc", tieptuc))
    app.add_handler(CommandHandler("trangthai", trangthai))
    app.add_handler(CommandHandler("nangtran", nangtran))
    app.add_handler(CommandHandler("loai", loai))
    app.run_polling()
