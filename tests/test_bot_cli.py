import importlib
import sys

from conftest import REPO

from vidagents.bot import chunks, parse_video_cmd


def test_parse_video_cmd_handles_quotes_spaces_and_brief():
    r = parse_video_cmd('/video "D:\\Source\\Inhouse KOC\\raw" koc-review femfresh\nGiá 99K\nƯu đãi đến 30/11')
    assert r == ("D:\\Source\\Inhouse KOC\\raw", "koc-review", "femfresh", "Giá 99K\nƯu đãi đến 30/11")
    assert parse_video_cmd("/video D:\\a koc-review x")[:3] == ("D:\\a", "koc-review", "x")
    assert parse_video_cmd("/video chỉ-một-tham-số") is None


def test_chunks_respect_telegram_limit():
    parts = chunks("a" * 9000)
    assert all(len(p) <= 3800 for p in parts) and "".join(parts) == "a" * 9000


def test_all_modules_import_without_optional_dependencies():
    # langgraph/telegram/anthropic chỉ được nạp muộn, nên import module không được kéo theo chúng
    for m in ("vidagents.bot", "vidagents.cli", "vidagents.graph", "vidagents.runner", "vidagents.llm", "vidagents.app"):
        importlib.import_module(m)
    assert "langgraph" not in sys.modules and "telegram" not in sys.modules
