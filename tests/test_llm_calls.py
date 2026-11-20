"""ClaudeLLM không được ép công cụ (tool_choice "tool"/"any" bị Sonnet 5.5 từ chối với 400), và phải thử lại khi model không gọi công cụ."""
from types import SimpleNamespace

import pytest

from conftest import make_env
from vidagents import schemas as S
from vidagents.llm import ClaudeLLM
from vidagents.schemas import SchemaError


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def reply(*blocks):
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                                      cache_read_input_tokens=0, cache_creation_input_tokens=0))


def tool_use(data):
    return SimpleNamespace(type="tool_use", name="tra_ket_qua", input=data)


def llm_with(tmp_path, replies):
    e = make_env(tmp_path)
    llm = ClaudeLLM(e.cfg)
    fake = FakeMessages(replies)
    llm._client = SimpleNamespace(messages=fake)
    return llm, fake


def test_never_forces_tool_choice_and_asks_for_the_tool_in_prompt(tmp_path):
    llm, fake = llm_with(tmp_path, [reply(tool_use({"product": "X"}))])
    out = llm.structured(agent="orchestrator", system="s", user="trích brief", schema=S.BriefExtract)
    assert out.product == "X"
    kw = fake.calls[0]
    assert kw["tool_choice"] == {"type": "auto"}            # "tool"/"any" -> 400 trên model mới
    assert "tra_ket_qua" in kw["messages"][0]["content"][-1]["text"]
    assert kw["max_tokens"] >= 16000                          # suy nghĩ của model nằm trong max_tokens


def test_effort_cache_marks_and_shared_block(tmp_path):
    e = make_env(tmp_path, efforts={"footage": "low", "script": "medium"})
    llm = ClaudeLLM(e.cfg)
    fake = FakeMessages([reply(tool_use({"product": "X"})), reply(tool_use({"product": "X"})), reply(tool_use({"product": "X"}))])
    llm._client = SimpleNamespace(messages=fake)
    llm.structured(agent="footage", system="SYS", user="u", schema=S.BriefExtract)
    llm.structured(agent="script", system="SYS", shared="PHẦN CHUNG", user="u", schema=S.BriefExtract)
    llm.structured(agent="critic", system="SYS", user="u", schema=S.BriefExtract)      # không khai báo mức -> không gửi output_config
    a, b, c = fake.calls
    assert a["output_config"] == {"effort": "low"} and b["output_config"] == {"effort": "medium"} and "output_config" not in c
    assert a["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]   # lời dặn agent được lưu đệm
    assert len(a["messages"][0]["content"]) == 1                    # không có phần chung -> một khối
    blocks = b["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "PHẦN CHUNG", "cache_control": {"type": "ephemeral"}}   # phần chung lưu đệm, đứng TRƯỚC
    assert "cache_control" not in blocks[-1] and blocks[-1]["text"].startswith("u")                       # phần đổi theo lần gọi không lưu


def test_effort_validation_rejects_unknown_level():
    import pytest as _p
    from vidagents.config import _efforts
    assert _efforts({"_ghi_chu": "x", "script": "medium", "critic": ""}) == {"script": "medium"}
    with _p.raises(ValueError):
        _efforts({"script": "cao"})


def test_retries_once_when_model_answers_in_text_instead_of_calling_the_tool(tmp_path):
    llm, fake = llm_with(tmp_path, [reply(SimpleNamespace(type="text", text="xin chào")), reply(tool_use({"product": "Y"}))])
    assert llm.structured(agent="orchestrator", system="s", user="u", schema=S.BriefExtract).product == "Y"
    assert len(fake.calls) == 2 and "Lần trước không trả kết quả theo khuôn" in fake.calls[1]["messages"][0]["content"][-1]["text"]


def test_gives_up_after_two_bad_answers(tmp_path):
    llm, fake = llm_with(tmp_path, [reply(), reply()])
    with pytest.raises(SchemaError):
        llm.structured(agent="orchestrator", system="s", user="u", schema=S.BriefExtract)
    assert len(fake.calls) == 2
