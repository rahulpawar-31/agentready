import os
from dataclasses import dataclass
from typing import Protocol

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 1024          # per model turn; part of the harness hash


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: tuple
    content: tuple                    # assistant content blocks (plain dicts) to append to the conversation
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""             # the API's stop reason: end_turn, tool_use, max_tokens, refusal ...
    model: str = ""                   # the model that actually answered (resp.model)


class LLMClient(Protocol):
    def complete(self, system: str, messages: list, tools: list) -> LLMResponse: ...


def _pass_through(block) -> dict:
    """Any other block (thinking, redacted thinking ...) must be replayed unchanged or the next call
    can fail. Convert it to a plain dict; refuse explicitly when that is not possible."""
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    raise ValueError(f"unsupported content block type: {getattr(block, 'type', '?')}")


class AnthropicClient:
    """Thin adapter over the Anthropic Messages API. The SDK reads ANTHROPIC_API_KEY itself; the key
    is never read, logged or stored here. No temperature is set, so run-to-run variance is real."""

    def __init__(self, model: str | None = None, max_tokens: int | None = None, client=None):
        self.model = model or os.environ.get("AGENTREADY_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens or MAX_TOKENS
        if client is None:
            import anthropic                      # imported lazily: the tests need no SDK or key
            client = anthropic.Anthropic()
        self._client = client

    def complete(self, system: str, messages: list, tools: list) -> LLMResponse:
        resp = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, messages=messages, tools=tools)
        text_parts, calls, content = [], [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input)))
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
            else:
                content.append(_pass_through(block))
        usage = getattr(resp, "usage", None)
        return LLMResponse(" ".join(text_parts), tuple(calls), tuple(content),
                           getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0,
                           str(getattr(resp, "stop_reason", "") or ""), str(getattr(resp, "model", "") or ""))
