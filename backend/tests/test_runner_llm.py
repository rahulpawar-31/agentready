from types import SimpleNamespace as NS

import pytest

from app.runner.llm import DEFAULT_MODEL, AnthropicClient, LLMResponse
from tests.runner_fakes import ScriptedLLM, tool


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def client_with(content, usage=NS(input_tokens=11, output_tokens=7)):
    messages = FakeMessages(NS(content=content, usage=usage))
    return AnthropicClient(client=NS(messages=messages)), messages


def test_text_and_tool_use_blocks_are_converted(monkeypatch):
    monkeypatch.delenv("AGENTREADY_MODEL", raising=False)
    c, m = client_with([NS(type="text", text="Looking."),
                        NS(type="tool_use", id="t1", name="fetch_page", input={"url": "/"})])
    r = c.complete("sys", [{"role": "user", "content": "go"}], [{"name": "fetch_page"}])
    assert r.text == "Looking." and r.input_tokens == 11 and r.output_tokens == 7
    assert [(t.id, t.name, t.input) for t in r.tool_calls] == [("t1", "fetch_page", {"url": "/"})]
    assert r.content == ({"type": "text", "text": "Looking."},
                         {"type": "tool_use", "id": "t1", "name": "fetch_page", "input": {"url": "/"}})
    assert m.kwargs["model"] == DEFAULT_MODEL and m.kwargs["system"] == "sys"
    assert "temperature" not in m.kwargs


def test_model_comes_from_argument_or_environment(monkeypatch):
    monkeypatch.setenv("AGENTREADY_MODEL", "some-model")
    c, m = client_with([])
    c.complete("s", [], [])
    assert m.kwargs["model"] == "some-model"
    c2 = AnthropicClient(model="explicit", client=NS(messages=FakeMessages(NS(content=[], usage=None))))
    assert c2.model == "explicit"


def test_missing_usage_is_zero():
    c, _ = client_with([], usage=None)
    r = c.complete("s", [], [])
    assert (r.input_tokens, r.output_tokens) == (0, 0)


def test_scripted_llm_follows_its_script_and_records_messages():
    llm = ScriptedLLM([[tool("fetch_page", url="/")], "done", RuntimeError("boom")])
    msgs = [{"role": "user", "content": "go"}]
    r1 = llm.complete("s", msgs, [])
    assert r1.tool_calls[0].name == "fetch_page" and r1.content[0]["type"] == "tool_use"
    r2 = llm.complete("s", msgs, [])
    assert r2.text == "done" and not r2.tool_calls
    with pytest.raises(RuntimeError):
        llm.complete("s", msgs, [])
    with pytest.raises(AssertionError):
        llm.complete("s", msgs, [])
    assert len(llm.seen_messages) == 4 and llm.calls == 4     # four calls were made, the last one hit the exhausted script


def test_a_real_client_is_not_needed_to_import_the_module():
    import app.runner.llm as mod
    assert "anthropic" not in mod.__dict__
