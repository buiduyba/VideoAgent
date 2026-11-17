"""Gọi Claude. Không có ANTHROPIC_API_KEY thì chạy chế độ mô phỏng (trả văn bản mẫu)."""
import os
import random
import time

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def live() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def mode() -> str:
    return f"Claude ({MODEL})" if live() else "Mô phỏng (chưa có API key)"


def ask(system: str, prompt: str, mock: str, max_tokens: int = 900) -> tuple[str, int]:
    """Trả (văn bản, số token). `mock` là câu trả lời dùng khi chạy mô phỏng."""
    if not live():
        time.sleep(random.uniform(0.9, 1.5))
        return mock, 120 + len(mock) // 3
    from anthropic import Anthropic

    client = Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, msg.usage.input_tokens + msg.usage.output_tokens
