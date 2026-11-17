"""`demo --live`: lỗi từ API (hết credit...) phải in gọn và dừng job, không đổ traceback."""
import json

from conftest import REPO
from vidagents.cli import main


class BrokenClaude:
    def __init__(self, cfg, meter):
        pass

    def structured(self, **kw):
        raise RuntimeError("Your credit balance is too low to access the Anthropic API.")


def test_live_api_error_is_reported_cleanly(tmp_path, monkeypatch, capsys):
    raw = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "khong-phai-key-that")
    monkeypatch.setattr("vidagents.app.ClaudeLLM", BrokenClaude)
    code = main(["demo", "--live", "--workdir", str(tmp_path / "w"), "--config", str(cfg)])
    out = capsys.readouterr().out
    assert code == 1 and "LỖI khi gọi Claude" in out and "Nạp tiền" in out and "Traceback" not in out
