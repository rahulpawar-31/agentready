"""Adding the MCP endpoint must not change how the REST API answers errors (review finding I1).

The expected values are the ones `origin/main` (before the MCP server existed) returned: they are hard-coded here on
purpose, so this test fails if the MCP endpoint ever swallows a route again (for example by a mount at "/")."""
import pytest
from fastapi.testclient import TestClient

from app import main

NOT_FOUND = '{"detail":"Not Found"}'
NOT_ALLOWED = '{"detail":"Method Not Allowed"}'

# (method, path, status, allow header, location path, body)
CASES = [
    ("GET", "/audit", 405, "POST", None, NOT_ALLOWED),
    ("PUT", "/audit", 405, "POST", None, NOT_ALLOWED),
    ("HEAD", "/audit", 405, "POST", None, ""),
    ("OPTIONS", "/audit", 405, "POST", None, NOT_ALLOWED),
    ("DELETE", "/report/abc", 405, "GET", None, NOT_ALLOWED),
    ("POST", "/report/abc", 405, "GET", None, NOT_ALLOWED),
    ("OPTIONS", "/report/abc", 405, "GET", None, NOT_ALLOWED),
    ("HEAD", "/report/abc", 405, "GET", None, ""),
    ("GET", "/report/abc/fixes/x/approve", 405, "POST", None, NOT_ALLOWED),
    ("POST", "/audit/", 307, None, "/audit", ""),
    ("GET", "/report/abc/", 307, None, "/report/abc", ""),
    ("GET", "/ownership-token/", 307, None, "/ownership-token", ""),
    ("GET", "/definitely-not-a-route", 404, None, None, NOT_FOUND),
    ("GET", "/", 404, None, None, NOT_FOUND),
    ("GET", "/report//", 404, None, None, NOT_FOUND),
    ("GET", "//ownership-token", 404, None, None, NOT_FOUND),
    ("GET", "/mcp/x", 404, None, None, NOT_FOUND),
]


@pytest.mark.parametrize("method,path,status,allow,location,body", CASES)
def test_rest_error_paths_answer_exactly_as_they_did_before_the_mcp_endpoint(method, path, status, allow,
                                                                              location, body):
    with TestClient(main.app, base_url="http://localhost:8000", follow_redirects=False) as c:
        r = c.request(method, path)
    assert r.status_code == status
    assert r.headers.get("allow") == allow
    assert (r.headers.get("location") or "").removeprefix("http://localhost:8000") == (location or "")
    assert r.text == body
    if body:
        assert r.headers["content-type"].startswith("application/json")


def test_the_docs_and_the_openapi_document_are_still_served_and_do_not_list_the_mcp_endpoint():
    with TestClient(main.app, base_url="http://localhost:8000") as c:
        assert c.get("/docs").status_code == 200
        spec = c.get("/openapi.json").json()
    assert "/audit" in spec["paths"] and "/mcp" not in spec["paths"]
