"""config.json lưu bằng Notepad/PowerShell thường có BOM ở đầu: bot vẫn phải đọc được (lỗi thật đã gặp khi sửa config bằng PowerShell)."""
import json

from conftest import REPO
from vidagents.config import Config


def test_config_with_utf8_bom_loads(tmp_path):
    raw = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
    raw["footage_root"] = "//192.168.1.200/Disk4/Xưởng Video"
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    assert str(Config.load(p).footage_root).replace("\\", "/").endswith("Disk4/Xưởng Video")
