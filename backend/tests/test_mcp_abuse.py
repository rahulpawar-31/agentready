"""Availability and abuse limits on the MCP tools (review findings I3 and I4): at most four audits run at once, one
audit per host at a time with a pause between starts, and the throttle runs before the verification probe."""
import asyncio
import time

import httpx
import pytest
from mcp.client import Client

from app import main
from app.mcp_server import (AUDIT_SLOTS, AUDIT_WAIT_SECONDS, DEFAULT_HOST_INTERVAL, MAX_THROTTLED_HOSTS,
                            HostThrottle, Throttled, build_server)
from tests.conftest import page, public_resolver
from tests.mcp_helpers import B, call, error_json, pages_for, result_json, run, setup_site


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _clean_stores():
    yield
    for store in (main._reports, main._baselines, main._proposals):
        store.clear()


def fake_audit(seconds=0.0, fail=False, started=None):
    def fake(req, txt, resolver):
        if started is not None:
            started.append(req.url)
        time.sleep(seconds)
        if fail:
            raise RuntimeError("boom with a secret path /Users/x")
        return {"id": "a" * 32, "site": req.url, "score": 1, "category_scores": {}, "findings": [],
                "unscored": 0, "score_status": "scored"}
    return fake


# --- the throttle on its own ----------------------------------------------------------------------------------
def test_the_defaults_are_the_documented_ones():
    assert DEFAULT_HOST_INTERVAL == 5.0 and AUDIT_SLOTS == 4 and AUDIT_WAIT_SECONDS == 2.0
    assert MAX_THROTTLED_HOSTS == 1000


def test_a_host_cannot_start_again_before_the_interval_has_passed():
    clock = Clock()
    t = HostThrottle(clock, interval=5.0)
    t.finish("x.test", t.begin("x.test"))
    with pytest.raises(Throttled) as e:
        t.begin("x.test")
    assert e.value.retry_after == pytest.approx(5.0)
    clock.t += 4
    with pytest.raises(Throttled) as e:
        t.begin("x.test")
    assert e.value.retry_after == pytest.approx(1.0, abs=0.11)
    clock.t += 1
    t.begin("x.test")                                                    # exactly five seconds after the start


def test_only_one_audit_per_host_can_be_in_flight_however_long_it_runs():
    clock = Clock()
    t = HostThrottle(clock, interval=5.0)
    t.begin("x.test")
    clock.t += 1000
    with pytest.raises(Throttled):
        t.begin("x.test")


def test_hosts_are_throttled_independently():
    t = HostThrottle(Clock(), interval=5.0)
    t.begin("a.test")
    t.begin("b.test")
    t.begin("a.test:8443")


def test_a_cancelled_start_does_not_count_as_a_start():
    t = HostThrottle(Clock(), interval=5.0)
    reservation = t.begin("x.test")
    t.cancel("x.test", reservation)
    t.begin("x.test")


def test_an_interval_of_zero_only_enforces_one_at_a_time():
    t = HostThrottle(Clock(), interval=0)
    r = t.begin("x.test")
    with pytest.raises(Throttled):
        t.begin("x.test")
    t.finish("x.test", r)
    t.begin("x.test")


def test_the_table_of_hosts_is_bounded_and_forgets_the_oldest_first():
    clock = Clock()
    t = HostThrottle(clock, interval=5.0, max_hosts=3)
    for h in ("a.test", "b.test", "c.test", "d.test"):
        t.finish(h, t.begin(h))
        clock.t += 0.1
    assert t.tracked() == 3
    t.begin("a.test")                                                    # forgotten: allowed at once
    with pytest.raises(Throttled):
        t.begin("d.test")                                                # remembered: still throttled


# --- through the tool -------------------------------------------------------------------------------------------
def test_a_second_audit_of_the_same_host_through_mcp_is_rate_limited_then_allowed_again(monkeypatch):
    clock = Clock()
    server = build_server(main, clock=clock)
    setup_site(monkeypatch, pages_for())
    assert not call(server, "audit_site", {"url": B}).is_error
    err = error_json(call(server, "audit_site", {"url": B}))
    assert err["error"] == "rate_limited" and 0 < err["retry_after_seconds"] <= 5
    assert not call(server, "get_report", {"report_id": "a" * 32}).content[0].text == ""     # other tools unaffected
    clock.t += 5
    assert not call(server, "audit_site", {"url": B}).is_error


def test_the_real_default_is_five_seconds(monkeypatch):
    server = build_server(main)
    setup_site(monkeypatch, pages_for())
    assert not call(server, "audit_site", {"url": B}).is_error
    assert error_json(call(server, "audit_site", {"url": B}))["error"] == "rate_limited"


def test_the_same_host_in_another_spelling_is_the_same_host(monkeypatch):
    server = build_server(main, clock=Clock())
    setup_site(monkeypatch, pages_for())
    assert not call(server, "audit_site", {"url": "https://x.test/"}).is_error
    assert error_json(call(server, "audit_site", {"url": "https://X.TEST/some/path?q=1"}))["error"] == "rate_limited"


def test_the_throttle_runs_before_the_verification_probe_so_unverified_hosts_cannot_be_probed_in_a_loop(monkeypatch):
    server = build_server(main, clock=Clock())
    fake = setup_site(monkeypatch, {f"{B}/": page(f"{B}/", "<html></html>")})         # control not proven
    resolved = []
    main.app.dependency_overrides[main.get_resolver] = lambda: (lambda host: resolved.append(host) or ["93.184.216.34"])
    assert error_json(call(server, "audit_site", {"url": B}))["error"] == "control_not_proven"
    assert len(fake.calls) == 1 and len(resolved) == 1
    for _ in range(5):
        assert error_json(call(server, "audit_site", {"url": B}))["error"] == "rate_limited"
    assert len(fake.calls) == 1 and len(resolved) == 1                               # no probe, no DNS


def test_invalid_requests_make_no_request_and_do_not_use_up_the_hosts_turn(monkeypatch):
    server = build_server(main, clock=Clock())
    fake = setup_site(monkeypatch, pages_for())
    for _ in range(3):
        assert error_json(call(server, "audit_site", {"url": "not a url"}))["error"] == "invalid_request"
        assert error_json(call(server, "audit_site", {"url": B, "snapshot": "../x"}))["error"] == "invalid_request"
    assert fake.calls == []
    assert not call(server, "audit_site", {"url": B}).is_error


def test_two_audits_of_one_host_at_the_same_time_run_only_one(monkeypatch):
    setup_site(monkeypatch, pages_for())
    started = []
    monkeypatch.setattr(main, "post_audit", fake_audit(0.5, started=started))
    server = build_server(main, host_interval=0)

    async def go():
        async with Client(server) as c:
            return await asyncio.gather(c.call_tool("audit_site", {"url": B}), c.call_tool("audit_site", {"url": B}))
    a, b = run(go())
    assert sorted([a.is_error, b.is_error]) == [False, True] and len(started) == 1
    assert error_json(a if a.is_error else b)["error"] == "rate_limited"


def test_a_failed_audit_gives_the_host_its_turn_back(monkeypatch):
    setup_site(monkeypatch, pages_for())
    monkeypatch.setattr(main, "post_audit", fake_audit(fail=True))
    server = build_server(main, host_interval=0)
    for _ in range(3):
        err = error_json(call(server, "audit_site", {"url": B}))
        assert err["error"] == "failed" and "secret path" not in str(err)


# --- the bounded audit executor -----------------------------------------------------------------------------------
def test_more_audits_than_slots_get_busy_after_a_short_wait_and_slots_are_released(monkeypatch):
    setup_site(monkeypatch, pages_for())
    started = []
    monkeypatch.setattr(main, "post_audit", fake_audit(1.2, started=started))
    server = build_server(main, host_interval=0, audit_slots=4, audit_wait=0.3)

    async def go():
        async with Client(server) as c:
            results = await asyncio.gather(*[c.call_tool("audit_site", {"url": f"https://h{i}.test"})
                                             for i in range(6)])
            again = await c.call_tool("audit_site", {"url": "https://later.test"})
            return results, again
    results, again = run(go())
    errors = [r for r in results if r.is_error]
    assert len(errors) == 2 and len(started) == 4 + 1
    assert all(error_json(r) == {"error": "busy", "message": "Too many audits are running; retry shortly."}
               for r in errors)
    assert not again.is_error                                                           # the slots came back


def test_a_busy_refusal_does_not_use_up_the_hosts_turn(monkeypatch):
    setup_site(monkeypatch, pages_for())
    monkeypatch.setattr(main, "post_audit", fake_audit(0.8))
    clock = Clock()
    server = build_server(main, clock=clock, audit_slots=1, audit_wait=0.1)

    async def go():
        async with Client(server) as c:
            first = asyncio.create_task(c.call_tool("audit_site", {"url": "https://one.test"}))
            await asyncio.sleep(0.1)
            busy = await c.call_tool("audit_site", {"url": "https://two.test"})
            await first
            retry = await c.call_tool("audit_site", {"url": "https://two.test"})
            return busy, retry
    busy, retry = run(go())
    assert error_json(busy)["error"] == "busy" and not retry.is_error                    # not rate_limited


def test_slow_audits_never_starve_rest_or_the_other_tools(monkeypatch):
    """Review finding I3: with 48 concurrent slow audits the 40-thread default pool was exhausted and a REST call took
    seconds. Now at most four audits hold a thread. One event loop serves the MCP calls and the REST call here, so
    they share anyio's default thread limiter exactly as in production."""
    setup_site(monkeypatch, pages_for())
    monkeypatch.setattr(main, "post_audit", fake_audit(2.0))
    server = build_server(main, host_interval=0, audit_wait=1.0)

    async def go():
        async with Client(server) as c:
            audits = [asyncio.create_task(c.call_tool("audit_site", {"url": f"https://h{i}.test"})) for i in range(48)]
            await asyncio.sleep(0.4)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as h:
                start = time.monotonic()
                rest = await h.get("/ownership-token", params={"domain": "x.test"})
                rest_seconds = time.monotonic() - start
            start = time.monotonic()
            report = await c.call_tool("get_report", {"report_id": "a" * 32})
            mcp_seconds = time.monotonic() - start
            await asyncio.gather(*audits)
            return rest, rest_seconds, report, mcp_seconds
    rest, rest_seconds, report, mcp_seconds = run(go())
    assert rest.status_code == 200 and rest_seconds < 0.5
    assert error_json(report)["error"] == "not_found" and mcp_seconds < 0.5
