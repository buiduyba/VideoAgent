"""Hộp thư đi: tin Telegram gửi lỗi (mạng chập chờn, Telegram giới hạn tốc độ, bot chưa kết nối xong) KHÔNG được mất.

Vì sao có: trước đây `notifier.send` lỗi thì ném ngoại lệ ngay trong luồng chạy job. Hậu quả đo được bằng bài thử: (1) tin "job dừng" của
người giao lỗi thì tin cho admin cũng không bao giờ được gửi; (2) một lần Telegram chập chờn trong lúc job chạy thì chính job bị dừng
vì cái tin báo tiến độ; (3) lỗi bị nuốt im lặng, người giao cứ chờ mà không biết có tin nào đã mất.

Cách làm: ReliableNotifier bọc notifier thật. Gửi được thì xong. Gửi lỗi thì KHÔNG ném lỗi, ghi tin vào bảng `outbox` (SQLite, bền qua
khởi động lại) rồi một luồng nền gửi lại theo nhịp tăng dần (5 giây, 30 giây, 2 phút, 10 phút, rồi mỗi giờ) tới tối đa 24 giờ.
Một tin đã gửi lại thành công thì xóa khỏi bảng. Tin quá 24 giờ thì bỏ (ghi nhật ký), vì báo "job dừng" đã quá một ngày thì không còn giá trị.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Callable, Sequence

BACKOFF_S = (5, 30, 120, 600, 3600)   # sau lần thử cuối trong danh sách thì thử lại mỗi giờ
MAX_AGE_S = 24 * 3600


class Outbox:
    def __init__(self, path: Path | str, now: Callable[[], float] = time.time):
        self.path, self.now = Path(path), now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._c()) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, text TEXT, files TEXT, buttons TEXT,
                attempts INTEGER, next_at REAL, created_at REAL, last_error TEXT)""")
            c.commit()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def delay(attempts: int) -> int:
        return BACKOFF_S[min(attempts, len(BACKOFF_S) - 1)]

    def add(self, user_id: str, text: str, files: Sequence[str], buttons, error: str) -> int:
        t = self.now()
        with closing(self._c()) as c:
            cur = c.execute("INSERT INTO outbox(user_id,text,files,buttons,attempts,next_at,created_at,last_error) VALUES(?,?,?,?,?,?,?,?)",
                            (str(user_id), text, json.dumps([str(f) for f in files]), json.dumps(buttons or []), 0, t + self.delay(0), t, error[:300]))
            c.commit()
            return int(cur.lastrowid)

    def due(self, limit: int = 20) -> list[dict]:
        with closing(self._c()) as c:
            rows = c.execute("SELECT * FROM outbox WHERE next_at<=? ORDER BY id LIMIT ?", (self.now(), limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["files"], d["buttons"] = json.loads(d["files"] or "[]"), [[tuple(b) for b in row] for row in json.loads(d["buttons"] or "[]")]
            out.append(d)
        return out

    def done(self, row_id: int) -> None:
        with closing(self._c()) as c:
            c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
            c.commit()

    def failed(self, row: dict, error: str) -> bool:
        """Ghi một lần gửi lại thất bại. True nếu đã bỏ tin (quá hạn)."""
        if self.now() - row["created_at"] > MAX_AGE_S:
            self.done(row["id"])
            return True
        n = row["attempts"] + 1
        with closing(self._c()) as c:
            c.execute("UPDATE outbox SET attempts=?, next_at=?, last_error=? WHERE id=?", (n, self.now() + self.delay(n), error[:300], row["id"]))
            c.commit()
        return False

    def pending(self) -> int:
        with closing(self._c()) as c:
            return c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


class ReliableNotifier:
    """Bọc một notifier: `send` không bao giờ ném lỗi; tin gửi lỗi được giữ lại và gửi lại sau. Mọi thuộc tính khác (markup, attach...) chuyển cho notifier thật."""

    def __init__(self, inner, outbox: Outbox, log: Callable[[str], None] = print):
        self.inner, self.outbox, self.log = inner, outbox, log
        self._lock = threading.Lock()   # một lượt gửi lại tại một thời điểm (tránh gửi trùng khi luồng nền và lệnh gọi tay chạm nhau)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _ready(self) -> bool:
        """TelegramNotifier chưa gắn vào bot (app None) thì send im lặng không làm gì: coi như CHƯA gửi được để giữ tin lại."""
        return getattr(self.inner, "app", True) is not None

    def send(self, user_id: str, text: str, files: Sequence[str] = (), buttons=None) -> None:
        try:
            if not self._ready():
                raise RuntimeError("bot chưa kết nối Telegram xong")
            self.inner.send(user_id, text, files, buttons=buttons)
        except Exception as e:  # noqa: BLE001 - lỗi gửi tin không được làm hỏng việc đang chạy
            self.outbox.add(str(user_id), text, files, buttons, f"{type(e).__name__}: {e}")
            self.log(f"[tin] gửi cho {user_id} lỗi ({type(e).__name__}), đã giữ lại để gửi lại")

    def flush(self) -> int:
        """Gửi lại các tin đến hạn. Trả về số tin gửi lại thành công."""
        if not self._ready():
            return 0
        sent = 0
        with self._lock:
            for row in self.outbox.due():
                try:
                    self.inner.send(row["user_id"], row["text"], row["files"], buttons=row["buttons"] or None)
                except Exception as e:  # noqa: BLE001
                    if self.outbox.failed(row, f"{type(e).__name__}: {e}"):
                        self.log(f"[tin] bỏ tin gửi cho {row['user_id']} (quá 24 giờ không gửi được): {row['text'][:80]!r}")
                    continue
                self.outbox.done(row["id"])
                sent += 1
        return sent

    def start(self, every_s: float = 10.0) -> threading.Thread:
        def loop() -> None:
            while True:
                try:
                    n = self.flush()
                    if n:
                        self.log(f"[tin] đã gửi lại {n} tin")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[tin] lỗi khi gửi lại: {e}")
                time.sleep(every_s)
        t = threading.Thread(target=loop, name="vidagents-outbox", daemon=True)
        t.start()
        return t
