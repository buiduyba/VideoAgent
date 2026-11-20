"""doctor nhận cả hai cách đăng nhập Claude API: biến môi trường chứa key, hoặc `ant auth login` (profile trên đĩa)."""
from conftest import REPO
from vidagents.config import Config
from vidagents.doctor import doctor


def claude_line(cfg):
    return next(l for l in doctor(cfg) if "Claude API" in l)


def test_doctor_claude_auth_three_cases(tmp_path, monkeypatch):
    cfg = Config.load(REPO / "config.example.json")
    monkeypatch.delenv(cfg.anthropic_key_env, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))
    assert claude_line(cfg).startswith("THIẾU") and "ant auth login" in claude_line(cfg)

    (tmp_path / "anthropic" / "credentials").mkdir(parents=True)
    (tmp_path / "anthropic" / "credentials" / "default.json").write_text("{}", encoding="utf-8")
    assert claude_line(cfg).startswith("OK") and "ant auth login" in claude_line(cfg)

    monkeypatch.setenv(cfg.anthropic_key_env, "khong-phai-key-that")
    assert claude_line(cfg).startswith("OK") and cfg.anthropic_key_env in claude_line(cfg)
