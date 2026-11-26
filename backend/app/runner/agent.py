import re
from dataclasses import asdict, dataclass, field

from ..fetch import request_log_for
from .judge import judge
from .labels import TaskLabel
from .llm import LLMClient
from .tasks import MAX_TURNS, STEP_LIMIT, Task, system_prompt
from .tools import TOOL_SPECS, ToolBox


@dataclass
class RunResult:
    task_id: str
    success: bool
    reason: str
    valid: bool
    answer: dict | None
    steps_used: int
    turns: int
    fetched_urls: list = field(default_factory=list)
    off_site_attempts: int = 0
    blocked_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    network_errors: int = 0
    fetch_errors: list = field(default_factory=list)   # clipped guard/network detail, never shown to the model
    error: str = ""                                    # for an invalid run: "ExceptionType: message", clipped
    stop_reason: str = ""                              # the last model turn's stop reason
    model: str = ""                                    # the model that actually answered
    request_log: list | None = None
    verification_request_log: list | None = None


ERROR_CLIP = 300
_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_-]{6,}|AIza[0-9A-Za-z_-]{20,}")   # Anthropic and Google key shapes


def describe(e: BaseException) -> str:
    """'ExceptionType: message', clipped, with anything that looks like an API key removed."""
    text = _KEY_LIKE.sub("[redacted]", f"{type(e).__name__}: {e}")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= ERROR_CLIP else text[:ERROR_CLIP].rstrip() + " ...[truncated]"


def run_task(*, task: Task, label: TaskLabel, origin: str, fetcher, llm: LLMClient,
             step_limit: int = STEP_LIMIT, max_turns: int = MAX_TURNS) -> RunResult:
    box = ToolBox(fetcher, origin, step_limit)
    system = system_prompt(box.host, step_limit)
    messages = [{"role": "user", "content": task.instruction.replace("{site}", box.origin)}]
    turns = in_tok = out_tok = 0
    invalid: str | None = None          # the reason a run is invalid, when it is
    error = stop_reason = model = ""
    while turns < max_turns:
        try:
            resp = llm.complete(system, messages, TOOL_SPECS)
        except Exception as e:                      # only a failed model call is an LLM error
            invalid, error = "llm_error", describe(e)
            break
        turns += 1
        in_tok += resp.input_tokens
        out_tok += resp.output_tokens
        stop_reason, model = resp.stop_reason or stop_reason, resp.model or model
        if resp.stop_reason in ("max_tokens", "refusal"):
            invalid = resp.stop_reason
            break
        messages.append({"role": "assistant", "content": list(resp.content)})
        if not resp.tool_calls:
            break
        try:
            results = []
            for call in resp.tool_calls:
                r = box.call(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": r.text, "is_error": r.is_error})
        except Exception as e:                      # a bug in our own tools or fetcher: never blame the model
            invalid, error = "harness_error", describe(e)
            break
        messages.append({"role": "user", "content": results})
        if box.answer is not None:
            break

    success, reason = False, ""
    if invalid is None and box.answer is not None:
        try:
            verdict = judge(task.id, box.answer, label, box.origin, box.seen, visited=box.visited, looked=box.looked)
            success, reason = verdict.success, verdict.reason
        except Exception as e:
            invalid, error = "harness_error", describe(e)
    elif invalid is None:
        if box.steps >= step_limit:
            reason = "step_limit"
        elif box.blocked:
            reason = "blocked"
        elif turns >= max_turns:
            reason = "turn_limit"
        else:
            reason = "no_answer"
    if invalid is None and not success and box.network_errors:
        invalid = "network_error"                   # a failed request may be why the run failed: rerun it
        error = box.fetch_errors[0] if box.fetch_errors else "request failed"
    if invalid is not None:
        success, reason = False, invalid
    return RunResult(task.id, success, reason, invalid is None, asdict(box.answer) if box.answer else None,
                     box.steps, turns, list(box.fetched), box.offsite, box.blocked, in_tok, out_tok,
                     box.network_errors, list(box.fetch_errors), error, stop_reason, model,
                     request_log=request_log_for(fetcher))
