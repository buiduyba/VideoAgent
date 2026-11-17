from __future__ import annotations

from typing import Protocol, Sequence


class Notifier(Protocol):
    def send(self, user_id: str, text: str, files: Sequence[str] = ()) -> None: ...


class ConsoleNotifier:
    def send(self, user_id: str, text: str, files: Sequence[str] = ()) -> None:
        print(f"\n--- gửi cho {user_id} ---\n{text}")
        for f in files:
            print(f"    (đính kèm: {f})")


class RecordingNotifier:
    """Dùng trong test: ghi lại mọi tin nhắn."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, list[str]]] = []

    def send(self, user_id: str, text: str, files: Sequence[str] = ()) -> None:
        self.messages.append((str(user_id), text, [str(f) for f in files]))
