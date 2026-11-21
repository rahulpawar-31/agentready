from app.runner.agent import run_task
from app.runner.labels import TaskLabel
from app.runner.tasks import TASKS
from tests.conftest import FakeFetcher, page
from tests.runner_fakes import ScriptedLLM, tool

O = "https://x.test"
PRICING = "<html><title>Pricing</title><p>Plans start at $19 per month. Cancel any time.</p></html>"
MAIN = TaskLabel(urls=("x.test/pricing",))
FORM = TaskLabel(urls=("x.test/contact",), required_fields=(("email",),))
PAGES = {f"{O}/pricing": page(f"{O}/pricing", PRICING)}


def run(script, label=MAIN, pages=None, task="main_info", **kw):
    fetcher = FakeFetcher(PAGES if pages is None else pages)
    llm = ScriptedLLM(script)
    result = run_task(task=TASKS[task], label=label, origin=O, fetcher=fetcher, llm=llm, **kw)
    return result, fetcher, llm


def test_success_path():
    r, f, llm = run([[tool("fetch_page", url="/pricing")],
                     [tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")]])
    assert (r.success, r.reason, r.valid) == (True, "ok", True)
    assert r.steps_used == 1 and r.turns == 2 and r.fetched_urls == [f"{O}/pricing"]
    assert r.input_tokens == 20 and r.output_tokens == 10
    assert r.answer["url"] == "/pricing"


def test_hallucinated_quote_fails():
    r, *_ = run([[tool("submit_answer", url="/pricing", quote="Plans start at $5 per month")]])
    assert (r.success, r.reason) == (False, "quote_not_on_page")        # never fetched, so nothing was shown


def test_tool_results_go_back_to_the_model_with_matching_ids():
    call = tool("fetch_page", url="/pricing")
    _, _, llm = run([[call], [tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")]])
    second_turn = llm.seen_messages[1]
    assert second_turn[-1]["role"] == "user"
    block = second_turn[-1]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == call.id and "Plans start" in block["content"]
    assert second_turn[-2]["role"] == "assistant"


def test_step_limit_without_an_answer():
    script = [[tool("fetch_page", url=f"/p{i}")] for i in range(6)] + ["I could not find it."]
    r, f, _ = run(script, step_limit=3)
    assert (r.success, r.reason) == (False, "step_limit")
    assert r.steps_used == 3 and len(f.calls) == 3                      # the guard-side fetcher was never asked a 4th time


def test_answer_after_the_limit_is_judged_normally():
    r, f, _ = run([[tool("fetch_page", url="/pricing")], [tool("fetch_page", url="/other")],
                   [tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")]], step_limit=1)
    assert (r.success, r.reason) == (True, "ok") and len(f.calls) == 1


def test_offsite_fetch_never_reaches_the_fetcher():
    r, f, _ = run([[tool("fetch_page", url="https://evil.test/")], [tool("submit_answer", gave_up="not_found")]])
    assert f.calls == [] and r.off_site_attempts == 1 and r.reason == "no_answer"


def test_blocked_run():
    r, *_ = run([[tool("fetch_page", url="/pricing")], [tool("submit_answer", gave_up="blocked")]],
                pages={f"{O}/pricing": page(f"{O}/pricing", "forbidden", status=403)})
    assert (r.success, r.reason, r.blocked_count) == (False, "blocked", 1)


def test_blocked_fetches_without_an_answer_are_reported_as_blocked():
    r, *_ = run([[tool("fetch_page", url="/pricing")], "I give up."],
                pages={f"{O}/pricing": page(f"{O}/pricing", "no", status=403)})
    assert r.reason == "blocked"


def test_model_stops_without_submitting():
    r, *_ = run(["I do not know."])
    assert (r.success, r.reason, r.valid, r.turns) == (False, "no_answer", True, 1)


def test_turn_limit():
    r, *_ = run([[tool("fetch_page", url="/pricing")], [tool("fetch_page", url="/pricing")], [tool("fetch_page", url="/pricing")]],
                max_turns=2)
    assert (r.success, r.reason, r.turns) == (False, "turn_limit", 2)


def test_llm_failure_makes_the_run_invalid_not_a_site_failure():
    r, *_ = run([RuntimeError("rate limited")])
    assert (r.success, r.valid, r.reason) == (False, False, "llm_error")


def test_contact_form_task_end_to_end():
    html = '<form action="/s"><label for="e">Email</label><input id="e" name="email"></form>'
    r, *_ = run([[tool("fetch_page", url="/contact")], [tool("submit_answer", url="/contact", fields=["email"])]],
                label=FORM, task="contact_form", pages={f"{O}/contact": page(f"{O}/contact", html)})
    assert r.success


def test_multiple_tool_calls_in_one_turn_are_all_answered():
    a, b = tool("fetch_page", url="/pricing"), tool("read_llms_txt")
    r, _, llm = run([[a, b], [tool("submit_answer", gave_up="not_found")]])
    ids = [blk["tool_use_id"] for blk in llm.seen_messages[1][-1]["content"]]
    assert ids == [a.id, b.id] and r.steps_used == 2
