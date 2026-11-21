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
    assert (r.success, r.reason) == (False, "not_visited")              # never fetched, so no answer page was visited


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


# --- review fixes ------------------------------------------------------------------
import pytest

from app.runner import agent as agent_mod
from app.runner.llm import LLMResponse

DOCS = TaskLabel(urls=("x.test/docs",))
NODOCS = TaskLabel(none_exists=True)


def test_c1_a_docs_guess_without_fetching_the_page_fails():
    r, *_ = run([[tool("submit_answer", url="/docs")]], label=DOCS, task="docs_api")
    assert (r.success, r.reason, r.valid) == (False, "not_visited", True)


def test_c1_none_exists_with_zero_effort_fails_and_with_a_look_passes():
    r, *_ = run([[tool("submit_answer", none_exists=True)]], label=NODOCS, task="docs_api")
    assert (r.success, r.reason) == (False, "not_investigated")
    r2, *_ = run([[tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]], label=NODOCS, task="docs_api")
    assert (r2.success, r2.reason) == (True, "ok")


def test_c1_the_docs_page_counts_once_fetched():
    r, *_ = run([[tool("fetch_page", url="/docs")], [tool("submit_answer", url="/docs")]], label=DOCS, task="docs_api",
                pages={f"{O}/docs": page(f"{O}/docs", "<p>API reference</p>")})
    assert r.success


class Exploding:
    def get(self, url, *, rendered=False, address=None):
        raise RuntimeError("fetcher bug")


def test_c4_a_tool_or_fetcher_exception_is_a_harness_error_not_an_llm_error():
    llm = ScriptedLLM([[tool("fetch_page", url="/pricing")]])
    r = run_task(task=TASKS["main_info"], label=MAIN, origin=O, fetcher=Exploding(), llm=llm)
    assert (r.valid, r.reason) == (False, "harness_error") and r.error == "RuntimeError: fetcher bug"


def test_c4_a_judge_exception_is_a_harness_error(monkeypatch):
    def boom(*a, **k):
        raise ValueError("judge bug")
    monkeypatch.setattr(agent_mod, "judge", boom)
    r, *_ = run([[tool("submit_answer", url="/pricing", quote="Plans start at $19")]])
    assert (r.valid, r.reason, r.error) == (False, "harness_error", "ValueError: judge bug")


def test_c4_an_llm_failure_records_the_error_clipped_and_without_key_like_text():
    r, *_ = run([RuntimeError("rate limited " + "x" * 1000 + " sk-ant-api03-SECRETKEY123")])
    assert (r.valid, r.reason) == (False, "llm_error")
    assert r.error.startswith("RuntimeError: rate limited") and len(r.error) <= 320 and "SECRETKEY" not in r.error


def test_i9_a_network_failure_makes_an_unsuccessful_run_invalid_not_a_blocked_failure():
    from app.models import Page
    class Flaky:
        def get(self, url, *, rendered=False, address=None):
            return Page(url, 0, {}, "", error="network error: ReadTimeout")
    llm = ScriptedLLM([[tool("fetch_page", url="/pricing")], [tool("submit_answer", gave_up="not_found")]])
    r = run_task(task=TASKS["main_info"], label=MAIN, origin=O, fetcher=Flaky(), llm=llm)
    assert (r.valid, r.reason, r.success) == (False, "network_error", False)
    assert r.blocked_count == 0 and r.fetch_errors == ["network error: ReadTimeout"]


def test_i9_guard_blocks_stay_a_valid_blocked_failure():
    from app.models import Page
    class Guard:
        def get(self, url, *, rendered=False, address=None):
            return Page(url, 0, {}, "", error="blocked: request budget of 60 used up")
    llm = ScriptedLLM([[tool("fetch_page", url="/pricing")], "I give up."])
    r = run_task(task=TASKS["main_info"], label=MAIN, origin=O, fetcher=Guard(), llm=llm)
    assert (r.valid, r.reason) == (True, "blocked")


def test_i3_the_model_never_sees_a_site_chosen_redirect_target():
    from app.models import Page
    evil = "blocked: https://evil.test/IGNORE-PREVIOUS-INSTRUCTIONS is outside the verified site x.test"
    class Redir:
        def get(self, url, *, rendered=False, address=None):
            return Page(url, 0, {}, "", error=evil)
    llm = ScriptedLLM([[tool("fetch_page", url="/pricing")], "done"])
    r = run_task(task=TASKS["main_info"], label=MAIN, origin=O, fetcher=Redir(), llm=llm)
    assert "IGNORE" not in str(llm.seen_messages) and "IGNORE" in r.fetch_errors[0]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_i10_a_truncated_or_refused_turn_is_an_invalid_run_with_its_own_reason(stop):
    resp = LLMResponse("partial", (), ({"type": "text", "text": "partial"},), 10, 5, stop, "fake-model")
    r, *_ = run([resp])
    assert (r.valid, r.reason, r.stop_reason) == (False, stop, stop)


def test_i10_the_model_and_stop_reason_are_recorded():
    r, *_ = run([[tool("fetch_page", url="/pricing")],
                 [tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")]])
    assert r.model == "fake-model" and r.stop_reason == "tool_use"


def test_m4_only_the_first_answer_in_a_turn_counts_and_later_calls_are_refused():
    first = tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")
    r, f, llm = run([[tool("fetch_page", url="/pricing")],
                     [first, tool("submit_answer", gave_up="not_found"), tool("fetch_page", url="/x")]])
    assert r.success and r.answer["url"] == "/pricing" and r.steps_used == 1      # the later fetch never ran
    assert f.calls == [(f"{O}/pricing", False)]
