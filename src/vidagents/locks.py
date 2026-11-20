"""Khóa "suất nặng": máy chung chỉ cho N việc nặng (cắt 4K, render) chạy cùng lúc. Khóa bằng file nên bền qua tiến trình."""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


log = logging.getLogger(__name__)


class LockTimeout(Exception):
    pass


class SlotLock:
    def __init__(self, directory: Path, slots: int = 1, stale_s: int = 4 * 3600, poll_s: float = 2.0, name: str = "render-slot", tick_s: float = 60.0):
        self.name = name
        self.tick_s = tick_s  # trong lúc CHỜ suất, cứ chừng này giây gọi on_tick một lần (nhịp "còn sống" cho sổ job)
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.slots = max(1, slots)
        self.stale_s = stale_s
        self.poll_s = poll_s
        self.release_retries = 60  # tối đa ~3 giây thử xóa lại khi Windows từ chối
        self.release_wait_s = 0.05

    def _path(self, i: int) -> Path:
        return self.dir / f"{self.name}-{i}.lock"

    def _try(self, i: int, owner: str) -> bool:
        p = self._path(i)
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            # Windows: file khóa đang chờ xóa (hoặc có luồng khác đang mở) báo PermissionError thay vì FileExistsError.
            # Coi như suất đang bận, vòng sau thử lại; nhánh dưới đo tuổi file để thu hồi khóa quá hạn.
            try:
                age = time.time() - float(json.loads(p.read_text(encoding="utf-8")).get("ts", 0))
            except Exception:
                # File vừa được tạo (còn rỗng, chủ khóa chưa kịp ghi) hoặc hỏng: đo tuổi bằng giờ sửa file,
                # TUYỆT ĐỐI không coi là bỏ ngay, nếu không hai việc sẽ cùng chiếm một suất.
                try:
                    age = time.time() - p.stat().st_mtime
                except OSError:
                    return False  # file vừa biến mất: vòng sau thử lại
            if age > self.stale_s:
                try:
                    p.unlink()
                except OSError:
                    return False
                return self._try(i, owner)
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"owner": owner, "ts": time.time(), "pid": os.getpid()}, f)
        return True

    def busy(self) -> int:
        return sum(1 for i in range(self.slots) if self._path(i).exists())

    def acquire(self, owner: str, timeout: float | None = None, on_wait: Callable[[int], None] | None = None,
                on_tick: Callable[[], None] | None = None) -> int:
        start = time.time()
        told = False
        last_tick = start
        while True:
            for i in range(self.slots):
                if self._try(i, owner):
                    return i
            if on_wait and not told:
                on_wait(self.busy())
                told = True
            if timeout is not None and time.time() - start >= timeout:
                raise LockTimeout(f"Chờ suất render quá {int(timeout)} giây")
            if on_tick and time.time() - last_tick >= self.tick_s:
                last_tick = time.time()
                try:
                    on_tick()
                except Exception:  # noqa: BLE001 - nhịp lỗi (vd. sổ job đang bận) không được làm hỏng việc chờ
                    log.debug("on_tick lỗi khi chờ suất", exc_info=True)
            time.sleep(self.poll_s)

    def release(self, slot: int, owner: str) -> bool:
        """Trả khóa. Trả True nếu khóa không còn của mình nữa (đã xóa, hoặc đã bị thu hồi), False nếu xóa không được.

        Windows không cho xóa file đang bị luồng/tiến trình khác mở (người chờ đang đọc file khóa để đo tuổi),
        nên thử lại vài lần thay vì bỏ qua: bỏ qua im lặng làm khóa bị bỏ rơi và cả hàng chờ đứng cho tới khi quá hạn."""
        p = self._path(slot)
        err: Exception | None = None
        for _ in range(self.release_retries):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("owner") != owner:
                    return True  # không còn là khóa của mình (đã quá hạn và bị người khác thu hồi)
                p.unlink()
                return True
            except FileNotFoundError:
                return True
            except (OSError, ValueError) as e:  # PermissionError khi file đang bị mở, ValueError khi nội dung đang ghi dở
                err = e
                time.sleep(self.release_wait_s)
        log.warning("Không trả được khóa %s (chủ %s): %r. Khóa sẽ tự hết hạn sau %ss, hoặc chạy `vidagents unlock`.",
                    p.name, owner, err, self.stale_s)
        return False

    @contextmanager
    def hold(self, owner: str, timeout: float | None = None, on_wait: Callable[[int], None] | None = None,
             on_tick: Callable[[], None] | None = None) -> Iterator[int]:
        slot = self.acquire(owner, timeout, on_wait, on_tick)
        try:
            yield slot
        finally:
            self.release(slot, owner)

    def force_unlock(self) -> int:
        n = 0
        for i in range(self.slots):
            try:
                self._path(i).unlink()
                n += 1
            except OSError:
                pass
        return n
