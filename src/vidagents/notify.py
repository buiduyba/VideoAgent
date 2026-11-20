from __future__ import annotations

from typing import Protocol, Sequence

Keyboard = list[list[tuple[str, str]]]   # nút bấm: [[(nhãn, callback_data), ...], ...]; xem ui.py


class Notifier(Protocol):
    def send(self, user_id: str, text: str, files: Sequence[str] = (), buttons: Keyboard | None = None) -> None: ...


class ConsoleNotifier:
    def send(self, user_id: str, text: str, files: Sequence[str] = (), buttons: Keyboard | None = None) -> None:
        print(f"\n--- gửi cho {user_id} ---\n{text}")
        for f in files:
            print(f"    (đính kèm: {f})")
        for row in buttons or []:
            print("    [nút] " + "  |  ".join(label for label, _ in row))


class RecordingNotifier:
    """Dùng trong test: ghi lại mọi tin nhắn (và bàn phím nút bấm đi kèm trong `keyboards`, song song với `messages`)."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, list[str]]] = []
        self.keyboards: list[Keyboard] = []

    def send(self, user_id: str, text: str, files: Sequence[str] = (), buttons: Keyboard | None = None) -> None:
        self.messages.append((str(user_id), text, [str(f) for f in files]))
        self.keyboards.append(buttons or [])
