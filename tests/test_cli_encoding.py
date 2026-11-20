"""Chạy nền, đầu ra bị chuyển vào file: main() phải ép UTF-8 (lỗi thật đã gặp: dashboard sập vì cp1252 khi in tiếng Việt)."""
import sys


def test_main_reconfigures_stdout_and_stderr_to_utf8(monkeypatch):
    from vidagents import cli

    calls = []

    class Stream:
        def reconfigure(self, **kw):
            calls.append(kw)

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Stream())
    monkeypatch.setattr(sys, "stderr", Stream())
    try:
        cli.main(["doctor", "--config", "khong-co.json"])
    except BaseException:  # noqa: BLE001 - chỉ cần main chạm tới reconfigure trước khi làm việc khác
        pass
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2   # cả stdout lẫn stderr
