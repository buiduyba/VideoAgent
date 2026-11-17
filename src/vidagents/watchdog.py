"""Báo khi video đứng: job đang chạy/xếp hàng mà quá `stall_minutes` phút không có tiến triển.

Mỗi mốc "đứng" chỉ báo MỘT lần (khi job nhúc nhích lại rồi đứng lần nữa thì báo lại), không spam.
Hai cách chạy:
- trong bot: luồng nền (start_watchdog_thread). GIỚI HẠN: nếu chính bot chết thì luồng này cũng chết, không tự báo được.
- ngoài bot: `vidagents watchdog --once` (HTTP thẳng tới Telegram, không cần bot sống) gọi từ Task Scheduler mỗi 15 phút.
  Cách này mới bắt được trường hợp bot/ máy treo, nếu máy chạy nó còn sống.
CHƯA ĐƯỢC CHẠY THỬ với Telegram thật.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .config import Config
from .locks import SlotLock

STAGE_VI = {"new": "mới nhận", "intake": "nhận job", "brief": "đọc brief", "footage": "xem footage", "script": "viết kịch bản",
            "build": "dựng", "fix": "sửa theo critic", "render": "render", "duyet_2_ok": "chuẩn bị render"}


def _reachable(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def stall_message(cfg: Config, job: dict) -> str:
    stage = STAGE_VI.get(job.get("stage") or "", job.get("stage") or "?")
    busy = SlotLock(cfg.locks_dir, cfg.render_slots).busy()
    lines = [f"[{job['job_id']}] ⚠ Video có vẻ ĐỨNG: ở bước '{stage}' ({job['status']}), {job['idle_minutes']} phút không có tiến triển.",
             "Chưa chắc là hỏng: có thể đang render lâu hoặc xếp hàng chờ máy. Những chỗ nên xem:"]
    lines.append(f"- Suất render đang dùng: {busy}/{cfg.render_slots}" + (" (đã kín, job có thể đang xếp hàng)" if busy >= cfg.render_slots else ""))
    lines.append("- Ổ footage/NAS: " + ("truy cập được" if _reachable(cfg.footage_root) else "KHÔNG truy cập được (kiểm tra ổ mạng)"))
    lines.append(f"- Máy dựng còn bật và bot còn chạy không? Nếu job báo kẹt khóa render: `vidagents unlock`; muốn chạy tiếp: /tieptuc {job['job_id']}")
    return "\n".join(lines)


def check_stalled(cfg: Config, jobs, send: Callable[[str, str], None], now: datetime | None = None) -> list[str]:
    """Báo cho người giao + admin. Trả về danh sách job vừa báo. Gửi lỗi thì KHÔNG đánh dấu đã báo (lần sau thử lại)."""
    done: list[str] = []
    for job in jobs.stalled(cfg.stall_minutes, now):
        msg = stall_message(cfg, job)
        targets = list(dict.fromkeys([str(job["requester_id"])] + [str(a) for a in cfg.admin_ids]))
        sent = False
        for t in targets:
            if not t:
                continue
            try:
                send(t, msg)
                sent = True
            except Exception as e:  # noqa: BLE001 - một người nhận lỗi không được làm hỏng cả vòng quét
                print(f"[canh] gửi cho {t} lỗi: {type(e).__name__}")
        if sent:
            jobs.mark_alerted(job["job_id"])
            done.append(job["job_id"])
    return done


def start_watchdog_thread(cfg: Config, jobs, notifier, log: Callable[[str], None] = print) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                n = check_stalled(cfg, jobs, lambda uid, text: notifier.send(uid, text))
                if n:
                    log(f"[canh] đã báo job đứng: {', '.join(n)}")
            except Exception as e:  # noqa: BLE001
                log(f"[canh] lỗi: {e}")
            time.sleep(max(30, cfg.watchdog_every_s))

    t = threading.Thread(target=loop, name="vidagents-watchdog", daemon=True)
    t.start()
    return t


class HttpNotifier:
    """Gửi Telegram bằng HTTP thuần (thư viện chuẩn), để chạy được khi bot không chạy. Token chỉ đọc từ biến môi trường."""

    def __init__(self, token: str):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, user_id: str, text: str, files: Sequence[str] = ()) -> None:
        data = json.dumps({"chat_id": int(user_id), "text": text[:3800]}).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram trả lỗi HTTP {e.code}") from None  # không đưa URL (có token) vào thông báo lỗi
        except urllib.error.URLError as e:
            raise RuntimeError(f"Không kết nối được Telegram: {e.reason}") from None


def http_notifier_from_env(cfg: Config) -> HttpNotifier:
    token = os.environ.get(cfg.telegram_token_env)
    if not token:
        raise SystemExit(f"Thiếu biến môi trường {cfg.telegram_token_env}")
    return HttpNotifier(token)
