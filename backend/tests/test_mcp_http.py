"""The MCP endpoint over HTTP. The MCP server and its session manager are created inside each lifespan run, so the
real `main.app` can be started any number of times; most tests here still build a small app of their own so they can
choose the allowed hosts."""
import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.client import Client

from app import main
from app.mcp_server import DEFAULT_ALLOWED_HOSTS, DEFAULT_ALLOWED_ORIGINS, install_mcp, mcp_lifespan
from tests.mcp_helpers import error_json, run

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def fresh_app(**kw):
    app = FastAPI(lifespan=mcp_lifespan(main, **kw))
    install_mcp(app)
    return app


def test_the_real_app_serves_mcp_at_exactly_slash_mcp_as_one_route_and_keeps_every_rest_route():
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert "/audit" in paths and "/report/{rid}/fixes" in paths and "/report/{rid}/fixes/{fid}/export" in paths
    assert "/mcp" in paths and paths.index("/mcp") > paths.index("/report/{rid}/fixes/{fid}/export")
    assert not any(type(r).__name__ == "Mount" for r in main.app.routes)       # a mount at "/" swallowed REST errors


def test_the_real_app_can_be_started_any_number_of_times_in_one_process():
    for _ in range(2):
        with TestClient(main.app, base_url="http://localhost:8000") as c:
            assert c.get("/ownership-token", params={"domain": "x.test"}).status_code == 200
            ok = c.post("/mcp", headers=HEADERS, json=INIT)
            assert ok.status_code == 200 and "serverInfo" in ok.text


def test_the_mcp_endpoint_without_a_running_lifespan_is_a_503_not_a_crash():
    c = TestClient(main.app, base_url="http://localhost:8000", raise_server_exceptions=False)
    r = c.post("/mcp", headers=HEADERS, json=INIT)
    assert r.status_code == 503 and "not running" in r.json()["detail"]
    assert c.get("/ownership-token", params={"domain": "x.test"}).status_code == 200      # REST is unaffected


def test_a_trailing_slash_redirects_like_every_other_route_while_the_exact_path_does_not():
    with TestClient(main.app, base_url="http://localhost:8000", follow_redirects=False) as c:
        r = c.post("/mcp/", headers=HEADERS, json=INIT)
        assert r.status_code == 307 and r.headers["location"].endswith("/mcp")
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 200


def test_initialize_works_for_a_local_host_and_is_refused_for_a_foreign_host():
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        ok = c.post("/mcp", headers=HEADERS, json=INIT)
        assert ok.status_code == 200 and "serverInfo" in ok.text
    with TestClient(fresh_app(), base_url="http://evil.test") as c:
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 421


def test_the_endpoint_is_exactly_slash_mcp_with_no_redirect():
    with TestClient(fresh_app(), base_url="http://localhost:8000", follow_redirects=False) as c:
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 200


def test_a_foreign_origin_is_refused():
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        r = c.post("/mcp", headers={**HEADERS, "Origin": "http://evil.test"}, json=INIT)
        assert r.status_code in (403, 421)
        ok = c.post("/mcp", headers={**HEADERS, "Origin": "http://localhost:3000"}, json=INIT)
        assert ok.status_code == 200


def test_allowed_hosts_are_replaced_by_the_callers_list():
    with TestClient(fresh_app(allowed_hosts=["agentready.internal:*"]), base_url="http://agentready.internal:8000") as c:
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 200
    with TestClient(fresh_app(allowed_hosts=["agentready.internal:*"]), base_url="http://localhost:8000") as c:
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 421


def test_environment_variables_replace_the_defaults(monkeypatch):
    monkeypatch.setenv("AGENTREADY_MCP_ALLOWED_HOSTS", "a.internal:*, b.internal:*")
    with TestClient(fresh_app(), base_url="http://b.internal:8000") as c:
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 200
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:          # replaced, not extended
        assert c.post("/mcp", headers=HEADERS, json=INIT).status_code == 421
    assert DEFAULT_ALLOWED_HOSTS == ["localhost:*", "127.0.0.1:*", "[::1]:*"]
    assert DEFAULT_ALLOWED_ORIGINS == ["http://localhost:*", "http://127.0.0.1:*"]


def padded_init(size):
    body = json.loads(json.dumps(INIT))
    body["params"]["clientInfo"]["name"] = "x" * size
    return body


def test_oversized_bodies_are_refused_but_a_normal_one_just_under_the_limit_is_not():
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        small = c.post("/mcp", headers=HEADERS, json=padded_init(60000))      # a valid request under 64 KB
        assert small.status_code == 200
        big = c.post("/mcp", headers=HEADERS, json=padded_init(70000))        # the same request over 64 KB
        assert big.status_code == 413


def test_unknown_paths_still_404_and_rest_still_works():
    c = TestClient(main.app)
    assert c.get("/report/" + "0" * 32).status_code == 404
    assert c.get("/definitely-not-a-route").json() == {"detail": "Not Found"}
    assert c.get("/ownership-token", params={"domain": "x.test"}).status_code == 200


class Live:
    """A real uvicorn server on an ephemeral localhost port, in a thread."""

    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        assert self.server.started, "uvicorn did not start"
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def test_a_real_mcp_client_talks_to_a_real_server_over_http():
    with Live(fresh_app()) as live:
        async def go():
            async with Client(f"http://127.0.0.1:{live.port}/mcp") as c:
                names = sorted(t.name for t in (await c.list_tools()).tools)
                missing = await c.call_tool("get_report", {"report_id": "0" * 32})
                return names, missing
        names, missing = run(go())
    assert names == ["audit_site", "get_report", "propose_fixes"]
    assert error_json(missing)["error"] == "not_found"


def test_a_real_mcp_client_talks_to_the_real_app_with_its_own_lifespan():
    """The production wiring: main.app, its lifespan, /mcp. (A mutation that forgot to wire the lifespan survived
    every test that built its own app.)"""
    with Live(main.app) as live:
        async def go():
            async with Client(f"http://127.0.0.1:{live.port}/mcp") as c:
                return sorted(t.name for t in (await c.list_tools()).tools)
        assert run(go()) == ["audit_site", "get_report", "propose_fixes"]
