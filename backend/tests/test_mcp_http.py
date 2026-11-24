"""The MCP endpoint over HTTP. The MCP server and its session manager are created inside each lifespan run, so the
real `main.app` can be started any number of times; most tests here still build a small app of their own so they can
choose the allowed hosts."""
import json
import threading
import time
from pathlib import Path

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
    assert DEFAULT_ALLOWED_ORIGINS == ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]


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


def test_the_server_is_stateless_so_anonymous_initialises_cannot_pile_up_sessions():
    """Review finding M1: every initialize created a session (about 36 KB each, up to 10,000) that anyone could open.
    The tools need no state between requests, so the transport keeps none."""
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        r = c.post("/mcp", headers=HEADERS, json=INIT)
        assert r.status_code == 200 and "mcp-session-id" not in {k.lower() for k in r.headers}
        listed = c.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listed.status_code == 200 and "audit_site" in listed.text


# --- exact Host and Origin matching (review finding M2) and the other transport findings (M8, M10, M11) -----------
def post(c, host=None, origin=None):
    headers = dict(HEADERS)
    if host is not None:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    return c.post("/mcp", headers=headers, json=INIT).status_code


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost:1", "localhost:65535"])
def test_the_default_hosts_are_accepted(host):
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        assert post(c, host=host) == 200


@pytest.mark.parametrize("host", [
    "localhost:8000@evil.test", "localhost:8000.evil.test", "localhost:evil.test", "localhost", "LOCALHOST:8000",
    "localhost.evil.test:8000", "evil.test", "localhost:", "localhost:8000:9", "localhost:123456", "127.0.0.1",
    "localhost:8000 evil.test", "[::1]", "localhost:80a", "localhost:0", "localhost:00080", "localhost:65536",
    "localhost:99999", "127.0.0.1:0", "[::1]:65536"])
def test_host_confusions_are_refused(host):
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        assert post(c, host=host) == 421


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:5173", "http://[::1]:3000"])
def test_the_default_origins_are_accepted(origin):
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        assert post(c, origin=origin) == 200


@pytest.mark.parametrize("origin", [
    "http://localhost:3000@evil.test", "http://localhost:3000.evil.test", "null", "http://localhost", "https://localhost:3000",
    "http://LOCALHOST:3000", "http://evil.test", "http://localhost:3000/", "http://localhost:3000/x", "", "localhost:3000",
    "http://localhost:0", "http://localhost:65536", "http://localhost:00080", "http://[::1]:99999"])
def test_origin_confusions_are_refused(origin):
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        assert post(c, origin=origin) in (403, 421)


def test_the_origin_environment_variable_replaces_the_default_origins(monkeypatch):
    monkeypatch.setenv("AGENTREADY_MCP_ALLOWED_ORIGINS", "https://agentready.example.com, http://localhost:*")
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        assert post(c, origin="https://agentready.example.com") == 200
        assert post(c, origin="https://agentready.example.com.evil.test") == 403
        assert post(c, origin="http://localhost:3000") == 200
        assert post(c, origin="http://127.0.0.1:3000") == 403                      # replaced, not extended


def test_the_origin_argument_replaces_the_default_origins():
    with TestClient(fresh_app(allowed_origins=["https://ui.internal"]), base_url="http://localhost:8000") as c:
        assert post(c, origin="https://ui.internal") == 200
        assert post(c, origin="http://localhost:3000") == 403


def test_a_host_entry_without_a_port_matches_exactly_that_host_header():
    """Deployments on ports 80 or 443 send `Host: name` with no port, so they list the bare name."""
    app = fresh_app(allowed_hosts=["agentready.example.com"])
    with TestClient(app, base_url="http://agentready.example.com") as c:
        assert post(c, host="agentready.example.com") == 200
        assert post(c, host="agentready.example.com:8000") == 421
        assert post(c, host="agentready.example.com.evil.test") == 421


def test_a_wildcard_port_entry_means_any_numeric_port_on_exactly_that_name():
    app = fresh_app(allowed_hosts=["agentready.internal:*"])
    with TestClient(app, base_url="http://agentready.internal:8000") as c:
        assert post(c, host="agentready.internal:9") == 200
        assert post(c, host="agentready.internal:8000@evil.test") == 421
        assert post(c, host="agentready.internal") == 421


def test_two_host_headers_are_refused():
    with TestClient(fresh_app(), base_url="http://localhost:8000") as c:
        r = c.post("/mcp", headers=[("Host", "localhost:8000"), ("Host", "evil.test"), *HEADERS.items()], json=INIT)
        assert r.status_code in (400, 421)


def test_the_default_lists_cover_the_ipv6_loopback_for_origins_too():
    assert DEFAULT_ALLOWED_HOSTS == ["localhost:*", "127.0.0.1:*", "[::1]:*"]
    assert DEFAULT_ALLOWED_ORIGINS == ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]


def test_the_documented_run_command_binds_to_localhost_only():
    import uvicorn
    assert uvicorn.Config("app.main:app").host == "127.0.0.1"
    docs = (Path(__file__).resolve().parents[2] / "docs" / "mcp.md").read_text()
    commands = [line for line in docs.splitlines() if "uvicorn app.main:app" in line and line.startswith("    ")]
    assert commands, "docs/mcp.md must show the run command"
    assert all("--host" not in line for line in commands), commands          # the documented command keeps the default


def test_the_sdk_dependency_is_pinned_to_the_versions_that_were_tested():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"mcp>=2.3,<2.5"' in text


# --- no server-to-client stream (found when the reviewer's d4 script was rerun on the final code) -----------------
def gateway_call(method, host="localhost:8000", extra_headers=()):
    """Drive the real gateway through a running lifespan without a socket. The request never sends a disconnect, so a
    response that never ends (an SSE stream) shows up as a timeout instead of a hung test."""
    import asyncio
    from app.mcp_server import McpGateway

    async def go():
        app = fresh_app()
        async with app.router.lifespan_context(app):
            sent = []

            async def receive():
                await asyncio.sleep(3600)

            async def send(message):
                sent.append(message)
            scope = {"type": "http", "method": method, "path": "/mcp", "raw_path": b"/mcp", "query_string": b"",
                     "root_path": "", "scheme": "http", "http_version": "1.1", "server": ("localhost", 8000),
                     "client": ("127.0.0.1", 1), "app": app, "state": {},
                     "headers": [(b"host", host.encode()), (b"accept", b"text/event-stream"), *extra_headers]}
            try:
                await asyncio.wait_for(McpGateway()(scope, receive, send), timeout=3)
            except asyncio.TimeoutError:
                return "never ends", {}
            start = next(m for m in sent if m["type"] == "http.response.start")
            return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}
    return asyncio.run(go())


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"])
def test_only_post_is_served_and_nothing_can_hold_a_stream_open(method):
    """The server is stateless and never sends anything on its own, so it has no use for the optional GET stream.
    An unauthenticated caller must not be able to hold connections open with it."""
    status, headers = gateway_call(method)
    assert status == 405, status
    assert headers["allow"] == "POST"


def test_a_foreign_host_is_still_refused_before_the_method_is_looked_at():
    assert gateway_call("GET", host="evil.test")[0] == 421
    assert gateway_call("GET", host="localhost:8000@evil.test")[0] == 421
