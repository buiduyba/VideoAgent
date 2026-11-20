"""Hỏi đáp tự do: chỉ đọc, đúng phạm vi người hỏi, có hạn mức, không im lặng khi lỗi."""
import json
from types import SimpleNamespace

from conftest import JOB, make_env
from vidagents.chat import SYSTEM, TOOLS, ChatAssistant


class FakeClient:
    """Trả lời theo kịch bản: mỗi phần tử là danh sách khối nội dung (text / tool_use) của một lần gọi API."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        blocks, stop = self.script.pop(0)
        return SimpleNamespace(content=blocks, stop_reason=stop, usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                                                         cache_read_input_tokens=0, cache_creation_input_tokens=0))


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, inp, id="tu1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=inp)


def assistant(tmp_path, script, **cfg):
    e = make_env(tmp_path, **cfg)
    client = FakeClient(script)
    return e, ChatAssistant(e.cfg, e.deps, client=client), client


def test_tool_loop_answers_from_real_job_data_and_returns_the_job_for_buttons(tmp_path):
    e, bot, client = assistant(tmp_path, [([tool("chi_tiet_job", {"ma_job": JOB})], "tool_use"), ([text("Job đang chạy, chưa cần bạn làm gì.")], "end_turn")])
    e.deps.jobs.update(JOB, status="running", stage="script")
    a = bot.ask("1001", False, "job của tôi tới đâu rồi?")
    assert a.text == "Job đang chạy, chưa cần bạn làm gì." and a.job_ids == [JOB]
    second = client.calls[1]["messages"]                                       # kết quả công cụ được đưa lại cho model
    result = json.loads(second[-1]["content"][0]["content"])
    assert result["ma_job"] == JOB and result["trang_thai"] == "running" and "viết kịch bản" in result["dang_cho"]


def test_assistant_has_no_tool_that_can_change_anything(tmp_path):
    names = {t["name"] for t in TOOLS}
    assert names == {"danh_sach_job", "chi_tiet_job", "loai_video_va_thuong_hieu", "tinh_trang_he_thong"}
    assert "CHỈ ĐỌC" in SYSTEM and "không thể giao việc, duyệt, sửa, hủy" in SYSTEM


def test_user_cannot_read_another_users_job_but_admin_can(tmp_path):
    e, bot, client = assistant(tmp_path, [])
    looked = []
    other = json.loads(bot.run_tool("chi_tiet_job", {"ma_job": JOB}, uid="2002", is_admin=False, looked=looked))   # job thuộc 1001
    assert "loi" in other and looked == []
    mine = json.loads(bot.run_tool("chi_tiet_job", {"ma_job": JOB}, uid="1001", is_admin=False, looked=looked))
    assert mine["ma_job"] == JOB and looked == [JOB]
    adm = json.loads(bot.run_tool("chi_tiet_job", {"ma_job": JOB}, uid="9", is_admin=True, looked=[]))
    assert adm["ma_job"] == JOB
    listing_other = json.loads(bot.run_tool("danh_sach_job", {"ca_job_da_xong": True}, uid="2002", is_admin=False, looked=[]))
    assert listing_other["so_job"] == 0                                         # không lộ job của người khác trong danh sách
    assert json.loads(bot.run_tool("danh_sach_job", {}, uid="9", is_admin=True, looked=[]))["so_job"] == 1


def test_unknown_tool_and_tool_errors_do_not_crash(tmp_path):
    e, bot, _ = assistant(tmp_path, [])
    assert "Không có công cụ" in json.loads(bot.run_tool("xoa_job", {"ma_job": JOB}, "1001", False, []))["loi"]
    assert "loi" in json.loads(bot.run_tool("chi_tiet_job", {}, "1001", False, []))


def test_usage_is_metered_per_user_per_day_and_daily_cap_blocks(tmp_path):
    # giá Haiku 5.5: 1000 token vào ($0,10/triệu) + 200 ra ($0,50/triệu) = $0,0002 mỗi lần
    e, bot, client = assistant(tmp_path, [([text("ok")], "end_turn")] * 3, chat_daily_usd=0.0002)
    first = bot.ask("1001", False, "xin chào")
    assert first.text == "ok"
    assert abs(bot.spent_today("1001") - 0.0002) < 1e-9 and bot.spent_today("2002") == 0
    blocked = bot.ask("1001", False, "hỏi nữa")
    assert "hết hạn mức hỏi đáp" in blocked.text and len(client.calls) == 1   # không gọi API nữa
    assert bot.ask("2002", False, "tôi hỏi được").text == "ok"                  # người khác không bị ảnh hưởng


def test_conversation_memory_keeps_text_only_and_expires(tmp_path):
    clock = [1000.0]
    e = make_env(tmp_path)
    client = FakeClient([([text("câu trả lời 1")], "end_turn"), ([text("câu trả lời 2")], "end_turn"), ([text("câu trả lời 3")], "end_turn")])
    bot = ChatAssistant(e.cfg, e.deps, client=client, now=lambda: clock[0])
    bot.ask("1001", False, "câu hỏi 1")
    clock[0] += 60
    bot.ask("1001", False, "câu hỏi 2")
    assert [m["content"] for m in client.calls[1]["messages"]] == ["câu hỏi 1", "câu trả lời 1", "câu hỏi 2"]   # nhớ lượt trước
    clock[0] += 3600                                                                                              # quá hạn nhớ
    bot.ask("1001", False, "câu hỏi 3")
    assert [m["content"] for m in client.calls[2]["messages"]] == ["câu hỏi 3"]


def test_api_failure_is_reported_not_swallowed(tmp_path):
    e = make_env(tmp_path)

    class Boom:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("credit balance is too low")))

    a = ChatAssistant(e.cfg, e.deps, client=Boom()).ask("1001", False, "hi")
    assert "tạm thời không dùng được" in a.text and "/menu" in a.text


def test_default_model_is_cheap_and_effort_low(tmp_path):
    e, bot, client = assistant(tmp_path, [([text("ok")], "end_turn")])
    bot.ask("1001", False, "hi")
    kw = client.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and kw["output_config"] == {"effort": "low"} and kw["tool_choice"] == {"type": "auto"}
    bot.cfg.models["chat"] = "claude-sonnet-5-5"                      # đổi model qua config
    client.script.append(([text("ok")], "end_turn"))
    bot.ask("1001", False, "hi")
    assert client.calls[1]["model"] == "claude-sonnet-5-5"
