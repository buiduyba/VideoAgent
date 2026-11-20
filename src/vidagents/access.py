"""Quản lý danh sách người được giao việc ngay trong bot (lệnh của admin): thêm/xóa/liệt kê theo ID Telegram.

- Chỉ danh sách NGƯỜI GIAO VIỆC (`telegram.allowed_user_ids`) đổi được qua bot. Admin (`admin_ids`) chỉ đổi bằng cách sửa config.json,
  để một tài khoản Telegram bị chiếm không tự nâng quyền hay thêm admin mới.
- Đổi có hiệu lực ngay (bot đọc danh sách mỗi lần có tin) và được ghi vào config.json (ghi file tạm rồi đổi tên, không bao giờ để file dở).
- Mỗi lần đổi ghi một dòng vào `access-log.txt` cạnh sổ job: ai đổi, đổi gì, lúc nào.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from .config import Config

_LOCK = threading.Lock()
_ID = re.compile(r"^\d{5,15}$")


class AccessError(Exception):
    """Lỗi viết sẵn cho người đọc (gửi nguyên văn lại cho admin)."""


def _persist(cfg: Config) -> None:
    p = cfg.source_path
    if not p:
        raise AccessError("Bot không biết file config nằm đâu nên không ghi lại được. Chạy bot bằng `vidagents bot --config config.json`.")
    p = Path(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    tg = raw.setdefault("telegram", {})
    tg["allowed_user_ids"] = list(cfg.allowed_user_ids)
    if cfg.user_names:
        tg["user_names"] = dict(cfg.user_names)
    else:
        tg.pop("user_names", None)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _log(cfg: Config, by: str, action: str, target: str, name: str = "") -> None:
    try:
        f = Path(cfg.jobs_db).parent / "access-log.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}\tadmin={by}\t{action}\t{target}\t{name}\n")
    except OSError:
        pass  # nhật ký hỏng không được chặn việc cấp quyền


def _check_id(target: str) -> str:
    target = (target or "").strip()
    if not _ID.match(target):
        raise AccessError("ID Telegram là một dãy số (5 đến 15 chữ số), ví dụ 5234517033. Người đó nhắn /start cho bot, bot sẽ trả về ID của họ.")
    return target


def add_user(cfg: Config, target: str, by: str, name: str = "") -> str:
    target, name = _check_id(target), (name or "").strip()[:60]
    with _LOCK:
        if target in cfg.admin_ids:
            raise AccessError("ID này đã là quản trị viên (không cần thêm).")
        if target in cfg.allowed_user_ids:
            if name and cfg.user_names.get(target) != name:
                old = cfg.user_names.get(target)
                cfg.user_names[target] = name
                try:
                    _persist(cfg)
                except Exception as e:  # noqa: BLE001
                    cfg.user_names.pop(target, None) if old is None else cfg.user_names.__setitem__(target, old)
                    raise AccessError(f"Không ghi được config: {e}") from e
                _log(cfg, by, "doi-ten", target, name)
                return f"ID {target} đã có trong danh sách. Đã cập nhật tên: {name}."
            raise AccessError(f"ID {target} đã có trong danh sách.")
        cfg.allowed_user_ids.append(target)
        if name:
            cfg.user_names[target] = name
        try:
            _persist(cfg)
        except Exception as e:  # noqa: BLE001 - ghi file lỗi thì trả bộ nhớ về như cũ, để bot và file không lệch nhau
            cfg.allowed_user_ids.remove(target)
            cfg.user_names.pop(target, None)
            raise AccessError(f"Không ghi được config nên chưa thêm: {e}") from e
        _log(cfg, by, "them", target, name)
    return f"Đã thêm {target}{f' ({name})' if name else ''} vào danh sách người được giao việc. Có hiệu lực ngay, không cần bật lại bot."


def remove_user(cfg: Config, target: str, by: str, active_jobs: int = 0) -> str:
    target = _check_id(target)
    with _LOCK:
        if target in cfg.admin_ids:
            raise AccessError("Không gỡ quản trị viên qua bot. Muốn đổi admin thì sửa config.json.")
        if target not in cfg.allowed_user_ids:
            raise AccessError(f"ID {target} không có trong danh sách.")
        old_pos, old_name = cfg.allowed_user_ids.index(target), cfg.user_names.get(target)
        cfg.allowed_user_ids.remove(target)
        cfg.user_names.pop(target, None)
        try:
            _persist(cfg)
        except Exception as e:  # noqa: BLE001
            cfg.allowed_user_ids.insert(old_pos, target)
            if old_name:
                cfg.user_names[target] = old_name
            raise AccessError(f"Không ghi được config nên chưa gỡ: {e}") from e
        _log(cfg, by, "go", target, old_name or "")
    warn = (f" ⚠ Người này còn {active_jobs} job chưa xong: họ không duyệt hay sửa được nữa, anh/chị /huy hoặc /tieptuc hộ khi cần."
            if active_jobs else "")
    return f"Đã gỡ {target}{f' ({old_name})' if old_name else ''}. Có hiệu lực ngay.{warn}"


def render_list(cfg: Config, name_of=None) -> str:
    """name_of(id) -> tên đã biết từ job cũ (hoặc None); dùng khi config chưa ghi tên."""
    def label(i: str) -> str:
        n = cfg.user_names.get(i) or (name_of(i) if name_of else None)
        return f"{i}" + (f" · {n}" if n else "")

    lines = ["Quản trị viên (đổi bằng cách sửa config.json):"] + [f"  • {label(i)}" for i in cfg.admin_ids]
    others = [i for i in cfg.allowed_user_ids if i not in cfg.admin_ids]
    lines += [f"Người được giao việc ({len(others)}):"] + ([f"  • {label(i)}" for i in others] or ["  (chưa có ai ngoài quản trị viên)"])
    return "\n".join(lines)
