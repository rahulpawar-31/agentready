from dataclasses import asdict, dataclass, field

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


def run_task(*, task: Task, label: TaskLabel, origin: str, fetcher, llm: LLMClient,
             step_limit: int = STEP_LIMIT, max_turns: int = MAX_TURNS) -> RunResult:
    box = ToolBox(fetcher, origin, step_limit)
    system = system_prompt(box.host, step_limit)
    messages = [{"role": "user", "content": task.instruction.replace("{site}", box.origin)}]
    turns = in_tok = out_tok = 0
    valid = True
    try:
        while turns < max_turns:
            resp = llm.complete(system, messages, TOOL_SPECS)
            turns += 1
            in_tok += resp.input_tokens
            out_tok += resp.output_tokens
            messages.append({"role": "assistant", "content": list(resp.content)})
            if not resp.tool_calls:
                break
            results = []
            for call in resp.tool_calls:
                r = box.call(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": r.text, "is_error": r.is_error})
            messages.append({"role": "user", "content": results})
            if box.answer is not None:
                break
    except Exception:
        valid = False

    if not valid:
        success, reason = False, "llm_error"
    elif box.answer is None:
        success = False
        if box.steps >= step_limit:
            reason = "step_limit"
        elif box.blocked:
            reason = "blocked"
        elif turns >= max_turns:
            reason = "turn_limit"
        else:
            reason = "no_answer"
    else:
        verdict = judge(task.id, box.answer, label, box.origin, box.seen)
        success, reason = verdict.success, verdict.reason
    return RunResult(task.id, success, reason, valid, asdict(box.answer) if box.answer else None,
                     box.steps, turns, list(box.fetched), box.offsite, box.blocked, in_tok, out_tok)
