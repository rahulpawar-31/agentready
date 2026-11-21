import copy
import itertools

from app.runner.llm import LLMResponse, ToolCall

_ids = itertools.count(1)


def tool(name: str, **args) -> ToolCall:
    return ToolCall(f"toolu_{next(_ids)}", name, args)


class ScriptedLLM:
    """Replays a script. Each item: a list of ToolCall, a string (a reply with no tool calls), or an
    Exception to raise. Records a deep copy of the messages it was given on every call."""

    def __init__(self, script):
        self._script = list(script)
        self.seen_messages = []
        self.calls = 0

    def complete(self, system, messages, tools):
        self.seen_messages.append(copy.deepcopy(messages))
        self.calls += 1
        if not self._script:
            raise AssertionError("ScriptedLLM was called more often than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return LLMResponse(item, (), ({"type": "text", "text": item},), 10, 5)
        content = tuple({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in item)
        return LLMResponse("", tuple(item), content, 10, 5)
