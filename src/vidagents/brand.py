"""Khóa và sửa an toàn thư viện thương hiệu (`_brands/<brand>/brand.json`).

Vì sao: nhiều job cùng thương hiệu chạy song song; skill/bài học có thể thêm từ sửa phụ đề, từ cấm vào brand.json.
Hai job cùng ghi một lúc thì một bên mất chỉnh sửa, hoặc file hỏng nửa chừng.
- brand_lock: chỉ MỘT job tại một thời điểm được động vào thương hiệu đó (khóa bằng file như khóa render).
- update_brand: đọc -> sửa -> lưu bản sao lưu -> ghi file tạm rồi đổi tên (không bao giờ để file ghi dở).
Bước nào trong tools.json có cờ "brand_lock": true sẽ chạy trong khóa này (mặc định KHÔNG bật bước nào: chỉ bật khi biết bước đó ghi vào brand.json).
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .config import Config
from .locks import SlotLock

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
KEEP_BACKUPS = 5


def brand_path(cfg: Config, brand: str) -> Path:
    if not _SLUG.match(brand or ""):
        raise ValueError(f"Tên thương hiệu không hợp lệ: {brand!r}")
    return cfg.projects_root / "_brands" / brand / "brand.json"


@contextmanager
def brand_lock(cfg: Config, brand: str, owner: str, timeout: float | None = None) -> Iterator[int]:
    brand_path(cfg, brand)  # chỉ để kiểm tên (chặn ký tự lạ vào tên file khóa)
    lock = SlotLock(cfg.locks_dir, 1, cfg.lock_stale_s, poll_s=0.1, name=f"brand-{brand}")
    with lock.hold(owner, timeout=cfg.lock_timeout_s if timeout is None else timeout) as slot:
        yield slot


def update_brand(cfg: Config, brand: str, mutate: Callable[[dict], dict | None], owner: str) -> dict:
    """Sửa brand.json trong khóa. `mutate` nhận dict hiện tại, sửa tại chỗ hoặc trả dict mới. Trả về nội dung đã lưu."""
    p = brand_path(cfg, brand)
    with brand_lock(cfg, brand, owner):
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        new = mutate(cur)
        new = cur if new is None else new
        text = json.dumps(new, ensure_ascii=False, indent=2)
        json.loads(text)  # chắc chắn ghi ra được JSON hợp lệ
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            bak = p.with_name(f"brand.json.bak-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")
            bak.write_bytes(p.read_bytes())
            old = sorted(p.parent.glob("brand.json.bak-*"))
            for b in old[:-KEEP_BACKUPS]:
                b.unlink(missing_ok=True)
        tmp = p.with_name("brand.json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)  # đổi tên là thao tác nguyên khối: người đọc thấy bản cũ hoặc bản mới, không thấy bản dở
        return new
