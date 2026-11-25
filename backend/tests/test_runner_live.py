import os

import pytest

from app.fetch import CachedFetcher, GuardedFetcher, HttpxFetcher
from app.runner.tools import ToolBox

live = pytest.mark.skipif(os.environ.get("AGENTREADY_LIVE") != "1", reason="set AGENTREADY_LIVE=1 to run live tests")


@live
@pytest.mark.live
def test_toolbox_over_the_real_guard_fetches_a_real_public_page(tmp_path):
    guarded = GuardedFetcher(HttpxFetcher(), "example.com")
    box = ToolBox(CachedFetcher(guarded, tmp_path), "https://example.com", 3)
    r = box.call("fetch_page", {"url": "/"})
    assert not r.is_error and "Example Domain" in r.text
    assert box.call("fetch_page", {"url": "https://evil.test/"}).is_error and box.offsite == 1
    assert guarded.requests_used == 1                      # the off-site call never reached the network
    box.call("fetch_page", {"url": "/"})                    # served from the cache
    assert guarded.requests_used == 1


@live
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_one_real_model_call_completes_a_tool_loop(tmp_path):
    from app.runner.agent import run_task
    from app.runner.labels import TaskLabel
    from app.runner.llm import AnthropicClient
    from app.runner.tasks import TASKS
    guarded = GuardedFetcher(HttpxFetcher(), "example.com")
    run = run_task(task=TASKS["docs_api"], label=TaskLabel(none_exists=True), origin="https://example.com",
                   fetcher=CachedFetcher(guarded, tmp_path), llm=AnthropicClient(), step_limit=4, max_turns=8)
    assert run.valid and run.turns >= 1 and run.steps_used <= 4        # the outcome itself is not asserted


@live
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="needs GEMINI_API_KEY")
def test_one_real_gemini_call_completes_a_tool_loop(tmp_path):
    """Run by hand once with a real key: AGENTREADY_LIVE=1 GEMINI_API_KEY=... pytest -q -k gemini tests/test_runner_live.py"""
    from app.runner.agent import run_task
    from app.runner.gemini import GeminiClient
    from app.runner.labels import TaskLabel
    from app.runner.tasks import TASKS
    model = os.environ.get("AGENTREADY_MODEL", "gemini-3.5-flash-lite")
    guarded = GuardedFetcher(HttpxFetcher(), "example.com")
    run = run_task(task=TASKS["docs_api"], label=TaskLabel(none_exists=True), origin="https://example.com",
                   fetcher=CachedFetcher(guarded, tmp_path), llm=GeminiClient(model), step_limit=4, max_turns=8)
    assert run.valid, run.error or run.reason          # a valid run means the request shape and replay worked
    assert run.turns >= 1 and run.steps_used <= 4 and run.model.startswith("gemini-")
