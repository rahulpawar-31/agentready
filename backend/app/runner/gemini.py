"""A thin adapter over the Google Gemini generateContent REST API, so the runner can use a free Gemini key.

It speaks the same LLMClient protocol as AnthropicClient: the agent loop, the tools, the prompts, the limits and the
judge are unchanged, so the harness hash (and the pre-registered criteria) do not change. Which model answered is
recorded with every run, and the report refuses to compare runs made with different models.

The key is read from GEMINI_API_KEY once, sent only in the x-goog-api-key header (never in a URL, so it cannot end up
in an error message or a log line), and removed from any error text. No temperature is set, so run-to-run variance is
real. The model's own content parts are replayed unchanged on the next turn, so thought signatures survive.
"""
import copy
import os
import re
import time

import httpx

from .llm import MAX_TOKENS, LLMResponse, ToolCall

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY_ENV = "GEMINI_API_KEY"
MODEL_PATTERN = re.compile(r"^gemini-[a-z0-9][a-z0-9.-]{0,63}$")
MIN_INTERVAL_ENV = "AGENTREADY_LLM_MIN_INTERVAL"
DEFAULT_MIN_INTERVAL = 4.0        # seconds between model calls: keeps a free key under about 15 requests a minute
MAX_RETRIES = 4                   # extra attempts after a 429, 500, 503 or a network error
MAX_WAIT = 90.0                   # a longer requested wait (a daily quota) is not waited for: the batch stops instead
RETRY_STATUSES = {429, 500, 503}
ERROR_CLIP = 200

REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "LANGUAGE"}
MODEL_FAILURES = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS"}   # the model's own mistake
HARNESS_FAULTS = {"MISSING_THOUGHT_SIGNATURE", "MALFORMED_RESPONSE"}                       # our replay was wrong
_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


class GeminiAPIError(Exception):
    """Any failed call. Its text always starts with 'Error code: <status>' when the API answered."""


class GeminiAuthenticationError(GeminiAPIError):
    """The key was rejected. The name contains 'AuthenticationError', which makes the runner stop the batch."""


class GeminiRateLimitError(GeminiAPIError):
    """The rate limit or the daily quota is used up even after waiting. The runner stops the batch."""


def is_gemini_model(model: str) -> bool:
    return isinstance(model, str) and model.startswith("gemini-")


def min_interval_from_env(env=os.environ) -> float:
    raw = env.get(MIN_INTERVAL_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_MIN_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{MIN_INTERVAL_ENV} must be a number of seconds") from None
    if not 0 <= value <= 600:
        raise ValueError(f"{MIN_INTERVAL_ENV} must be between 0 and 600 seconds")
    return value


def to_tools(tools: list) -> list:
    decls = []
    for t in tools:
        d = {"name": t["name"], "description": t.get("description", "")}
        schema = t.get("input_schema") or {}
        if schema.get("properties"):              # Gemini rejects an object schema with no properties
            d["parameters"] = copy.deepcopy(schema)
        decls.append(d)
    return [{"functionDeclarations": decls}]


def to_contents(messages: list) -> list:
    """Anthropic-shaped conversation -> Gemini contents. An assistant turn made by this adapter carries the model's
    original parts in a 'gemini_content' block; those are replayed byte for byte (thought signatures included)."""
    calls: dict[str, tuple[str, str | None]] = {}        # our tool_use id -> (function name, Gemini's own id)
    out = []
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
            continue
        if role == "assistant":
            raw = None
            for b in content:
                if b.get("type") == "tool_use":
                    calls[b["id"]] = (b["name"], b.get("gemini_id"))
                elif b.get("type") == "gemini_content":
                    raw = b
            if raw is not None:
                parts = copy.deepcopy(raw["parts"])
            else:
                parts = []
                for b in content:
                    if b.get("type") == "text" and b.get("text"):
                        parts.append({"text": b["text"]})
                    elif b.get("type") == "tool_use":
                        parts.append({"functionCall": {"name": b["name"], "args": b.get("input") or {}}})
            out.append({"role": "model", "parts": parts})
            continue
        parts = []
        for b in content:
            if b.get("type") == "tool_result":
                if b["tool_use_id"] not in calls:
                    raise ValueError("a tool result answers a call that is not in the conversation")
                name, gid = calls[b["tool_use_id"]]
                key = "error" if b.get("is_error") else "content"
                fr = {"name": name, "response": {key: b.get("content", "")}}
                if gid:
                    fr["id"] = gid
                parts.append({"functionResponse": fr})
            elif b.get("type") == "text":
                parts.append({"text": b["text"]})
        out.append({"role": "user", "parts": parts})
    return out


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        after = resp.headers.get("retry-after")
        if after:
            try:
                return max(0.0, float(after))
            except ValueError:
                pass
        try:
            for d in (resp.json().get("error") or {}).get("details") or []:
                m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", "")))
                if m:
                    return float(m.group(1))
        except Exception:
            pass
    return 5.0 * (2 ** attempt)                  # 5, 10, 20, 40 seconds


class GeminiClient:
    def __init__(self, model: str, *, api_key: str | None = None, client: httpx.Client | None = None,
                 min_interval: float | None = None, max_tokens: int | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        if not MODEL_PATTERN.fullmatch(model or ""):
            raise ValueError("the Gemini model name must look like gemini-<version>, for example gemini-3.5-flash-lite")
        key = api_key if api_key is not None else os.environ.get(KEY_ENV, "")
        if not key.strip():
            raise RuntimeError(f"{KEY_ENV} is not set")
        self.model = model
        self.max_tokens = max_tokens or MAX_TOKENS
        self._key = key.strip()
        self._client = client or httpx.Client(timeout=60, follow_redirects=False)
        self._min = min_interval_from_env() if min_interval is None else min_interval
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        self._seq = 0

    def __repr__(self) -> str:                   # never show the key
        return f"GeminiClient(model={self.model!r})"

    def _clean(self, text: str) -> str:
        text = text.replace(self._key, "[redacted]") if self._key else text
        text = re.sub(r"\s+", " ", _GOOGLE_KEY.sub("[redacted]", text)).strip()
        return text if len(text) <= ERROR_CLIP else text[:ERROR_CLIP].rstrip() + " ...[truncated]"

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _error(self, resp: httpx.Response) -> GeminiAPIError:
        try:
            err = resp.json().get("error") or {}
            status, message = str(err.get("status", "")), str(err.get("message", ""))
        except Exception:
            status, message = "", resp.text
        text = self._clean(f"Error code: {resp.status_code} {status}: {message}")
        if resp.status_code in (401, 403) or (resp.status_code == 400 and "api key" in message.lower()):
            return GeminiAuthenticationError(text)
        if resp.status_code == 429:
            return GeminiRateLimitError(text)
        return GeminiAPIError(text)

    def _post(self, body: dict) -> dict:
        url = f"{API_BASE}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._key, "content-type": "application/json"}
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self._client.post(url, json=body, headers=headers)
            except httpx.TransportError as e:
                if attempt == MAX_RETRIES:
                    raise GeminiAPIError(self._clean(f"network error: {type(e).__name__}")) from None
                self._sleep(_retry_delay(None, attempt))
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    raise GeminiAPIError("Error code: 200: the response was not JSON") from None
            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if delay <= MAX_WAIT:
                    self._sleep(delay)
                    continue
            raise self._error(resp)
        raise GeminiAPIError("unreachable")       # pragma: no cover

    def complete(self, system: str, messages: list, tools: list) -> LLMResponse:
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": to_contents(messages),
                "generationConfig": {"maxOutputTokens": self.max_tokens}}
        if tools:
            body["tools"] = to_tools(tools)
        data = self._post(body)
        answered = str(data.get("modelVersion") or self.model)
        usage = data.get("usageMetadata") or {}
        in_tok = int(usage.get("promptTokenCount") or 0)
        out_tok = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
        candidates = data.get("candidates") or []
        if not candidates:
            if (data.get("promptFeedback") or {}).get("blockReason"):
                return LLMResponse("", (), (), in_tok, out_tok, "refusal", answered)
            raise GeminiAPIError("Error code: 200: the response had no candidates")
        cand = candidates[0]
        finish = str(cand.get("finishReason") or "")
        if finish in HARNESS_FAULTS:
            raise GeminiAPIError(f"Error code: 200: finishReason {finish}")
        parts = (cand.get("content") or {}).get("parts") or []
        if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts):
            raise GeminiAPIError("Error code: 200: malformed content parts")
        texts, calls, blocks = [], [], []
        for p in parts:
            if isinstance(p.get("text"), str) and not p.get("thought"):
                texts.append(p["text"])
            fc = p.get("functionCall")
            if isinstance(fc, dict):
                name, args = fc.get("name"), fc.get("args") or {}
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise GeminiAPIError("Error code: 200: malformed function call")
                self._seq += 1
                call_id = f"gemini_{self._seq}"
                calls.append(ToolCall(call_id, name, dict(args)))
                blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": dict(args),
                               "gemini_id": fc.get("id") if isinstance(fc.get("id"), str) else None})
        if finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in REFUSALS:
            stop = "refusal"
        elif finish in MODEL_FAILURES:
            stop = finish.lower()
        elif finish in ("STOP", ""):
            stop = "tool_use" if calls else "end_turn"
        elif not parts:
            raise GeminiAPIError(f"Error code: 200: finishReason {finish} with no content")
        else:
            stop = finish.lower()
        content = ({"type": "gemini_content", "parts": copy.deepcopy(parts)}, *blocks)
        return LLMResponse(" ".join(texts), tuple(calls), content, in_tok, out_tok, stop, answered)
