import json

import httpx
import pytest

from app.runner import gemini
from app.runner.agent import describe, run_task
from app.runner.gemini import (GeminiAPIError, GeminiAuthenticationError, GeminiClient, GeminiRateLimitError,
                               to_contents, to_tools)
from app.runner.labels import TaskLabel
from app.runner.llm import MAX_TOKENS, AnthropicClient, make_client
from app.runner.results import harness_sha256
from app.runner.tasks import TASKS
from app.runner.tools import TOOL_SPECS
from tests.conftest import FakeFetcher, page

KEY = "AIzaSyTESTKEY0123456789abcdefghijklmno"
MODEL = "gemini-3.5-flash-lite"


class Server:
    """A scripted Gemini endpoint. Each item is a dict (a 200 JSON body), an httpx.Response, or an exception."""

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    def body(self, i=-1):
        return json.loads(self.requests[i].content)


def client(script, **kw):
    srv = Server(script)
    slept = []
    c = GeminiClient(MODEL, api_key=KEY, client=httpx.Client(transport=httpx.MockTransport(srv)),
                     min_interval=0, sleep=slept.append, **kw)
    return c, srv, slept


def reply(parts, finish="STOP", **extra):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5},
            "modelVersion": "gemini-3.5-flash-lite-001", **extra}


def call(name, sig=None, gid=None, **args):
    fc = {"name": name, "args": args}
    if gid:
        fc["id"] = gid
    part = {"functionCall": fc}
    if sig:
        part["thoughtSignature"] = sig
    return part


def error(status, code, message, details=None, headers=None):
    body = {"error": {"code": status, "status": code, "message": message, "details": details or []}}
    return httpx.Response(status, json=body, headers=headers or {})


# ---- the request -----------------------------------------------------------------------------------------
def test_the_key_goes_only_in_the_header_and_the_request_has_the_expected_shape():
    c, srv, _ = client([reply([{"text": "hi"}])])
    r = c.complete("sys", [{"role": "user", "content": "go"}], TOOL_SPECS)
    req = srv.requests[0]
    assert req.method == "POST" and req.headers["x-goog-api-key"] == KEY
    assert str(req.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    assert KEY not in str(req.url)
    body = srv.body()
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "go"}]}]
    assert body["generationConfig"] == {"maxOutputTokens": MAX_TOKENS}
    assert "temperature" not in json.dumps(body)
    names = [d["name"] for d in body["tools"][0]["functionDeclarations"]]
    assert names == [t["name"] for t in TOOL_SPECS]
    assert (r.text, r.stop_reason, r.model, r.input_tokens, r.output_tokens) == (
        "hi", "end_turn", "gemini-3.5-flash-lite-001", 100, 25)


def test_tool_schemas_are_passed_through_and_an_empty_schema_is_left_out():
    decls = {d["name"]: d for d in to_tools(TOOL_SPECS)[0]["functionDeclarations"]}
    assert "parameters" not in decls["read_llms_txt"]
    assert decls["fetch_page"]["parameters"] == TOOL_SPECS[0]["input_schema"]
    assert decls["submit_answer"]["parameters"]["properties"]["gave_up"]["enum"] == ["blocked", "not_found"]


def test_the_harness_hash_does_not_depend_on_the_provider(monkeypatch):
    before = harness_sha256()
    monkeypatch.setenv("AGENTREADY_MODEL", MODEL)
    assert harness_sha256() == before


# ---- the conversation ---------------------------------------------------------------------------------------
def test_tool_calls_get_unique_ids_and_the_model_parts_are_replayed_unchanged_with_signatures():
    parts = [{"text": "thinking...", "thought": True, "thoughtSignature": "SIG-A"},
             call("fetch_page", sig="SIG-B", gid="g-1", url="/")]
    c, srv, _ = client([reply(parts), reply([{"text": "done"}])])
    msgs = [{"role": "user", "content": "go"}]
    r = c.complete("s", msgs, TOOL_SPECS)
    assert r.text == "" and r.stop_reason == "tool_use"              # a thought is never the answer text
    assert [(t.name, t.input) for t in r.tool_calls] == [("fetch_page", {"url": "/"})]
    msgs.append({"role": "assistant", "content": list(r.content)})
    msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": r.tool_calls[0].id,
                                              "content": "page text", "is_error": False}]})
    c.complete("s", msgs, TOOL_SPECS)
    sent = srv.body()["contents"]
    assert sent[1] == {"role": "model", "parts": parts}               # byte for byte, signatures included
    assert sent[2] == {"role": "user", "parts": [{"functionResponse": {
        "name": "fetch_page", "id": "g-1", "response": {"content": "page text"}}}]}


def test_an_error_tool_result_is_sent_as_an_error_and_calls_without_a_gemini_id_get_none():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x1", "name": "fetch_page",
                                               "input": {"url": "/a"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x1", "content": "blocked",
                                          "is_error": True}]}]
    out = to_contents(msgs)
    assert out[1] == {"role": "model", "parts": [{"functionCall": {"name": "fetch_page", "args": {"url": "/a"}}}]}
    assert out[2]["parts"] == [{"functionResponse": {"name": "fetch_page", "response": {"error": "blocked"}}}]


def test_a_tool_result_for_an_unknown_call_is_refused():
    with pytest.raises(ValueError):
        to_contents([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "nope", "content": ""}]}])


def test_ids_stay_unique_across_turns_and_runs():
    c, _, _ = client([reply([call("fetch_page", url="/")]), reply([call("fetch_page", url="/")])])
    a = c.complete("s", [{"role": "user", "content": "go"}], TOOL_SPECS).tool_calls[0].id
    b = c.complete("s", [{"role": "user", "content": "go"}], TOOL_SPECS).tool_calls[0].id
    assert a != b


# ---- finish reasons -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("finish,stop", [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"),
                                         ("PROHIBITED_CONTENT", "refusal"), ("RECITATION", "refusal"),
                                         ("MALFORMED_FUNCTION_CALL", "malformed_function_call")])
def test_finish_reasons_map_to_the_runner_stop_reasons(finish, stop):
    c, _, _ = client([reply([{"text": "x"}], finish=finish)])
    assert c.complete("s", [{"role": "user", "content": "go"}], []).stop_reason == stop


@pytest.mark.parametrize("finish", ["MISSING_THOUGHT_SIGNATURE", "MALFORMED_RESPONSE"])
def test_a_replay_fault_is_an_error_so_the_run_is_invalid_not_a_failure(finish):
    c, _, _ = client([reply([{"text": "x"}], finish=finish)])
    with pytest.raises(GeminiAPIError):
        c.complete("s", [{"role": "user", "content": "go"}], [])


def test_a_blocked_prompt_is_a_refusal_and_an_empty_answer_is_an_error():
    c, _, _ = client([{"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []}])
    assert c.complete("s", [{"role": "user", "content": "go"}], []).stop_reason == "refusal"
    with pytest.raises(GeminiAPIError):
        c.complete("s", [{"role": "user", "content": "go"}], [])


def test_malformed_parts_and_calls_are_errors():
    c, _, _ = client([reply(["not a dict"]), reply([{"functionCall": {"name": 3, "args": {}}}])])
    for _ in range(2):
        with pytest.raises(GeminiAPIError):
            c.complete("s", [{"role": "user", "content": "go"}], [])


# ---- errors, retries and the key --------------------------------------------------------------------------------
def test_a_rejected_key_is_an_authentication_error_and_the_key_never_appears_in_it():
    c, _, _ = client([error(400, "INVALID_ARGUMENT", f"API key not valid: {KEY}")])
    with pytest.raises(GeminiAuthenticationError) as e:
        c.complete("s", [{"role": "user", "content": "go"}], [])
    assert KEY not in str(e.value) and "AuthenticationError" in describe(e.value)
    assert KEY not in describe(e.value) and "Error code: 400" in str(e.value)


def test_a_429_waits_the_requested_delay_then_succeeds():
    retry = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"}]
    c, srv, slept = client([error(429, "RESOURCE_EXHAUSTED", "slow down", retry), reply([{"text": "ok"}])])
    assert c.complete("s", [{"role": "user", "content": "go"}], []).text == "ok"
    assert slept == [13.0] and len(srv.requests) == 2


def test_retry_after_header_and_backoff_are_used_and_retries_are_bounded():
    c, srv, slept = client([error(503, "UNAVAILABLE", "busy", headers={"retry-after": "2"})] +
                           [error(500, "INTERNAL", "oops")] * gemini.MAX_RETRIES)
    with pytest.raises(GeminiAPIError) as e:
        c.complete("s", [{"role": "user", "content": "go"}], [])
    assert "Error code: 500" in str(e.value)
    assert slept == [2.0, 10.0, 20.0, 40.0] and len(srv.requests) == gemini.MAX_RETRIES + 1


def test_a_daily_quota_is_not_waited_for_and_stops_as_a_rate_limit_error():
    retry = [{"retryDelay": "40000s"}]
    c, srv, slept = client([error(429, "RESOURCE_EXHAUSTED", "quota per day", retry)])
    with pytest.raises(GeminiRateLimitError) as e:
        c.complete("s", [{"role": "user", "content": "go"}], [])
    assert slept == [] and len(srv.requests) == 1 and "RateLimitError" in describe(e.value)


def test_network_errors_are_retried_then_reported_without_detail():
    c, srv, slept = client([httpx.ConnectError(f"boom {KEY}")] * (gemini.MAX_RETRIES + 1))
    with pytest.raises(GeminiAPIError) as e:
        c.complete("s", [{"role": "user", "content": "go"}], [])
    assert str(e.value) == "network error: ConnectError" and len(slept) == gemini.MAX_RETRIES


def test_calls_are_spaced_by_the_minimum_interval():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s
    srv = Server([reply([{"text": "a"}]), reply([{"text": "b"}])])
    c = GeminiClient(MODEL, api_key=KEY, client=httpx.Client(transport=httpx.MockTransport(srv)), min_interval=4.0,
                     clock=lambda: t["now"], sleep=sleep)
    c.complete("s", [{"role": "user", "content": "go"}], [])
    t["now"] += 1.0
    c.complete("s", [{"role": "user", "content": "go"}], [])
    assert slept == [3.0]


def test_the_key_is_required_and_never_shown_in_repr(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiClient(MODEL)
    assert KEY not in repr(GeminiClient(MODEL, api_key=KEY))


@pytest.mark.parametrize("bad", ["gemini-", "gemini-../x", "gemini-3.5/flash", "GEMINI-3", "gemini-a?b", ""])
def test_model_names_that_could_change_the_url_are_refused(bad):
    with pytest.raises(ValueError):
        GeminiClient(bad, api_key=KEY)


@pytest.mark.parametrize("raw", ["abc", "-1", "601"])
def test_a_bad_minimum_interval_is_refused(raw):
    with pytest.raises(ValueError):
        gemini.min_interval_from_env({"AGENTREADY_LLM_MIN_INTERVAL": raw})


def test_the_default_minimum_interval_applies_when_unset():
    assert gemini.min_interval_from_env({}) == gemini.DEFAULT_MIN_INTERVAL


# ---- choosing the client -------------------------------------------------------------------------------------------
def test_make_client_picks_gemini_for_a_gemini_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("AGENTREADY_MODEL", MODEL)
    c = make_client()
    assert isinstance(c, GeminiClient) and c.model == MODEL


def test_make_client_keeps_anthropic_for_other_models(monkeypatch):
    class FakeAnthropic:
        def __init__(self, model):
            self.model = model
    monkeypatch.setattr("app.runner.llm.AnthropicClient", FakeAnthropic)
    assert make_client("claude-sonnet-5-5").model == "claude-sonnet-5-5"
    assert AnthropicClient is not FakeAnthropic


# ---- a whole run through the real agent loop -------------------------------------------------------------------------
def test_a_full_run_through_the_agent_loop_succeeds_with_the_gemini_adapter():
    B = "https://x.test"
    fetcher = FakeFetcher({f"{B}/": page(f"{B}/", "<p>Home</p>")})
    script = [reply([call("fetch_page", sig="S1", url="/")]),
              reply([call("submit_answer", sig="S2", none_exists=True)])]
    c, srv, _ = client(script)
    run = run_task(task=TASKS["docs_api"], label=TaskLabel(none_exists=True), origin=B, fetcher=fetcher, llm=c)
    assert run.valid and run.turns == 2 and run.model == "gemini-3.5-flash-lite-001"
    second = srv.body(1)["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "S1"
    assert second[2]["parts"][0]["functionResponse"]["name"] == "fetch_page"


def test_a_failed_gemini_call_makes_the_run_invalid_not_a_failure():
    c, _, _ = client([error(400, "INVALID_ARGUMENT", "bad request")])
    run = run_task(task=TASKS["docs_api"], label=TaskLabel(none_exists=True), origin="https://x.test",
                   fetcher=FakeFetcher({}), llm=c)
    assert not run.valid and run.reason == "llm_error" and "Error code: 400" in run.error
