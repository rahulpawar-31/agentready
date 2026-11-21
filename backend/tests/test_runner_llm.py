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


# --- review fixes ------------------------------------------------------------------
def test_i10_stop_reason_and_the_answering_model_are_recorded():
    messages = FakeMessages(NS(content=[NS(type="text", text="hi")], usage=None, stop_reason="max_tokens",
                               model="claude-sonnet-5-5-20261101"))
    r = AnthropicClient(client=NS(messages=messages)).complete("s", [], [])
    assert (r.stop_reason, r.model) == ("max_tokens", "claude-sonnet-5-5-20261101")


def test_i10_unknown_blocks_such_as_thinking_are_replayed_not_dropped():
    class Thinking:
        type = "thinking"

        def model_dump(self, **kw):
            assert kw == {"exclude_none": True}
            return {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    c, _ = client_with([Thinking(), NS(type="tool_use", id="t1", name="fetch_page", input={"url": "/"})])
    r = c.complete("s", [], [])
    assert r.content[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert [t.id for t in r.tool_calls] == ["t1"]


def test_i10_a_block_that_cannot_be_replayed_raises_explicitly():
    c, _ = client_with([NS(type="mystery")])
    with pytest.raises(ValueError, match="unsupported content block type: mystery"):
        c.complete("s", [], [])


def test_i10_an_unreplayable_block_makes_the_run_an_llm_error_with_the_reason_recorded():
    from app.runner.agent import run_task
    from app.runner.labels import TaskLabel
    from app.runner.tasks import TASKS
    from tests.conftest import FakeFetcher
    c, _ = client_with([NS(type="mystery")])
    r = run_task(task=TASKS["docs_api"], label=TaskLabel(urls=("x.test/docs",)), origin="https://x.test",
                 fetcher=FakeFetcher({}), llm=c)
    assert (r.valid, r.reason) == (False, "llm_error") and "mystery" in r.error
