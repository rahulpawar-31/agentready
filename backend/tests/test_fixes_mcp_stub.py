import ast
import io
import urllib.request

import pytest

from app.fixes.generate import build_mcp_stub, tools_from_stub
from app.fixes.models import NotEnoughContent
from app.fixes.validate import validate_mcp_stub

O = "https://x.test"
SPEC = {"openapi": "3.0.0", "info": {"title": "Acme API", "version": "1"},
        "servers": [{"url": "https://evil.test/v1"}],
        "paths": {
            "/products": {"get": {"operationId": "listProducts", "summary": "List products",
                                  "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}]},
                          "post": {"operationId": "createProduct"}},
            "/products/{id}": {"get": {"summary": "Get one product",
                                       "parameters": [{"name": "id", "in": "path", "required": True,
                                                       "schema": {"type": "string"}}]},
                               "delete": {}}}}


def test_only_get_operations_become_tools_with_stable_names():
    tools = tools_from_stub(build_mcp_stub(O, SPEC))
    assert [t["name"] for t in tools] == ["listProducts", "get_products_id"]
    assert all(t["method"] == "GET" for t in tools)
    assert tools[0]["query_params"] == ["limit"] and tools[1]["path_params"] == ["id"]
    assert tools[1]["input_schema"]["required"] == ["id"] and tools[0]["input_schema"]["required"] == []
    assert tools[0]["input_schema"]["properties"]["limit"] == {"type": "integer"}


def test_base_url_is_the_verified_origin_never_the_spec_server():
    src = build_mcp_stub(O, SPEC)
    assert 'BASE_URL = "https://x.test"' in src and "evil.test" not in src


def test_stub_is_valid_python_with_no_code_run_at_import():
    src = build_mcp_stub(O, SPEC)
    tree = ast.parse(src)
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.ClassDef, ast.Expr)
    assert all(isinstance(n, allowed) for n in tree.body)
    assert not any(isinstance(n, ast.Expr) and not isinstance(n.value, ast.Constant) for n in tree.body)
    assert validate_mcp_stub(src, SPEC, O) == []


def test_site_text_in_the_spec_cannot_become_code():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {
        "operationId": 'x"; import os; os.system("boom") #', "summary": '"""\nimport os\nos.system("boom")\n"""'}}}}
    src = build_mcp_stub(O, spec)
    assert not any(isinstance(n, ast.Attribute) and n.attr == "system" for n in ast.walk(ast.parse(src)))
    tools = tools_from_stub(src)
    assert len(tools) == 1 and tools[0]["name"].isidentifier() and "os.system" in tools[0]["description"]
    assert validate_mcp_stub(src, spec, O) == []


def test_tool_count_is_capped_and_duplicates_get_suffixes():
    spec = {"openapi": "3.0.0", "paths": {f"/p{i}": {"get": {"operationId": "same"}} for i in range(30)}}
    names = [t["name"] for t in tools_from_stub(build_mcp_stub(O, spec))]
    assert len(names) == 20 and names[0] == "same" and names[1] == "same_2" and len(set(names)) == 20


def test_no_get_operations_is_refused():
    with pytest.raises(NotEnoughContent):
        build_mcp_stub(O, {"openapi": "3.0.0", "paths": {"/a": {"post": {}}}})


@pytest.mark.parametrize("spec", [
    {"openapi": "3.0.0", "paths": []},
    {"openapi": "3.0.0", "paths": {"/a": []}},
    {"openapi": "3.0.0", "paths": {"/a b?x=1#f": {"get": {}}}},        # a path that is not a plain URL path
    {"openapi": "3.0.0", "paths": {"nopath": {"get": {}}}},
    {"openapi": "3.0.0"},
])
def test_hostile_or_malformed_specs_are_refused_without_crashing(spec):
    with pytest.raises(NotEnoughContent):
        build_mcp_stub(O, spec)


def test_parameters_with_odd_names_or_refs_are_left_out():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"parameters": [
        {"$ref": "#/components/parameters/x"}, {"name": "bad name!", "in": "query"}, {"name": "ok", "in": "query", "required": True}]}}}}
    t = tools_from_stub(build_mcp_stub(O, spec))[0]
    assert t["query_params"] == ["ok"] and t["input_schema"]["required"] == ["ok"]


def test_the_generated_code_does_one_read_only_get_against_the_verified_origin(monkeypatch):
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            seen.append((request.get_method(), request.full_url, timeout))
            return Response(b"ok")

    handlers = []

    def fake_build_opener(*custom_handlers):
        handlers.extend(custom_handlers)
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    ns = {}
    exec(compile(build_mcp_stub(O, SPEC), "stub", "exec"), ns)          # the test (not the app) runs the stub
    assert seen == []                                                   # nothing happens at import
    assert len(handlers) == 1 and handlers[0].redirect_request(None, None, 302, "Found", {}, "https://evil.test/") is None
    assert ns["call_tool"]("get_products_id", {"id": "a/b c"}) == "ok"
    assert ns["call_tool"]("listProducts", {"limit": 3, "other": "ignored"}) == "ok"
    assert seen == [("GET", "https://x.test/products/a%2Fb%20c", 15), ("GET", "https://x.test/products?limit=3", 15)]
    with pytest.raises(KeyError):
        ns["call_tool"]("not_a_tool", {})


def test_validator_catches_tampering_and_mismatch():
    src = build_mcp_stub(O, SPEC)
    assert validate_mcp_stub(src.replace("GET", "POST"), SPEC, O) != []
    assert validate_mcp_stub(src + "\nimport os\nos.system('x')\n", SPEC, O) != []
    assert validate_mcp_stub(src.replace("https://x.test", "https://evil.test", 1), SPEC, O) != []
    assert validate_mcp_stub("def broken(:\n", SPEC, O) != []
    assert validate_mcp_stub("x = 1\nTOOLS = 2\n", SPEC, O) != []
    other = {"openapi": "3.0.0", "paths": {"/z": {"get": {}}}}
    assert validate_mcp_stub(src, other, O) != []
    assert validate_mcp_stub(src, {"openapi": "3.0.0", "paths": {}}, O) != []
