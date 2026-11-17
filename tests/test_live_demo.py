"""`vidagents demo --live`: các điều kiện từ chối (không gọi Claude), và luồng + trần chi phí khi dùng Claude GIẢ có ghi chi phí.
Gọi Claude thật không nằm trong bộ test (cần key, tốn tiền): chạy tay, xem README."""
import json

from conftest import REPO
from vidagents.cli import main
from vidagents.fakes import DemoLLM


def cfg_file(tmp_path, **models):
    raw = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
    raw["models"] = {"default": "claude-sonnet-5-5", **models}
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def explode(*a, **k):
    raise AssertionError("không được gọi Claude khi điều kiện chưa đủ")


def no_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "khong-co-profile"))


def test_live_without_key_or_login_refuses_before_any_call(tmp_path, monkeypatch, capsys):
    no_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr("vidagents.app.ClaudeLLM", explode)
    assert main(["demo", "--live", "--workdir", str(tmp_path / "w"), "--config", str(cfg_file(tmp_path))]) == 2
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out and "ant auth login" in out and "chưa tốn đồng nào" in out


def test_live_accepts_ant_login_profile_without_any_key(tmp_path, monkeypatch, capsys):
    no_credentials(tmp_path, monkeypatch)
    creds = tmp_path / "khong-co-profile" / "credentials"
    creds.mkdir(parents=True)
    (creds / "default.json").write_text("{}", encoding="utf-8")  # chỉ cần file tồn tại: kiểm tra này không đọc nội dung
    monkeypatch.setattr("vidagents.app.ClaudeLLM", lambda cfg, meter: DemoLLM(heavy_rounds=1, meter=meter))
    assert main(["demo", "--live", "--max-usd", "5", "--workdir", str(tmp_path / "w"), "--config", str(cfg_file(tmp_path))]) == 0
    assert "CHẠY THẬT" in capsys.readouterr().out


def test_client_uses_key_if_set_else_lets_sdk_find_the_ant_login(tmp_path, monkeypatch):
    import anthropic

    from vidagents.config import Config
    from vidagents.llm import ClaudeLLM

    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw) or object())
    cfg = Config.load(cfg_file(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "khong-phai-key-that")
    ClaudeLLM(cfg)._c()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    ClaudeLLM(cfg)._c()
    assert made == [{"api_key": "khong-phai-key-that"}, {}]  # lần 2 không truyền key: SDK tự tìm profile


def test_client_error_names_both_ways_to_log_in(tmp_path, monkeypatch):
    import anthropic
    import pytest

    from vidagents.config import Config
    from vidagents.llm import ClaudeLLM
    from vidagents.schemas import SchemaError

    def no_creds(**kw):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(anthropic, "Anthropic", no_creds)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SchemaError, match="ant auth login"):
        ClaudeLLM(Config.load(cfg_file(tmp_path)))._c()


def test_live_refuses_model_without_price_because_cap_would_be_useless(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "khong-phai-key-that")
    monkeypatch.setattr("vidagents.llm.ClaudeLLM._c", explode)
    assert main(["demo", "--live", "--workdir", str(tmp_path / "w"), "--config", str(cfg_file(tmp_path, script="model-chua-co-gia"))]) == 2
    out = capsys.readouterr().out
    assert "model-chua-co-gia" in out and "Chưa gọi Claude" in out


def fake_claude(cfg, meter):
    return DemoLLM(heavy_rounds=1, meter=meter)  # ghi chi phí như Claude thật, nhưng không gọi mạng


def test_live_flow_reports_cost_per_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "khong-phai-key-that")
    monkeypatch.setattr("vidagents.app.ClaudeLLM", fake_claude)
    code = main(["demo", "--live", "--max-usd", "5", "--workdir", str(tmp_path / "w"), "--config", str(cfg_file(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0 and "CHẠY THẬT" in out and "trần chi phí $5.00" in out
    assert "CHI PHÍ AI: $" in out and "  - script:" in out and "DEMO THÀNH CÔNG" in out


def test_live_cost_cap_stops_the_job_instead_of_spending_on(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "khong-phai-key-that")
    monkeypatch.setattr("vidagents.app.ClaudeLLM", fake_claude)
    code = main(["demo", "--live", "--max-usd", "0.001", "--workdir", str(tmp_path / "w"), "--config", str(cfg_file(tmp_path))])
    out = capsys.readouterr().out
    assert code == 1 and "DỪNG vì chạm trần chi phí" in out and "DEMO THÀNH CÔNG" not in out
