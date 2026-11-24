import pytest
from fastapi.testclient import TestClient

from app import main
from app import ui_routes

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
NOT_FOUND = '{"detail":"Not Found"}'


@pytest.fixture
def client():
    return TestClient(main.app, follow_redirects=False)


def test_the_page_is_served_at_root_with_strict_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "<h1" in r.text


def test_assets_have_the_right_types_and_the_same_headers(client):
    js = client.get("/ui/app.js")
    css = client.get("/ui/app.css")
    assert js.status_code == css.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")
    assert css.headers["content-type"].startswith("text/css")
    for r in (js, css):
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/ui/index.html", "/ui/", "/ui", "/ui/nope.js", "/ui/%2e%2e/main.py", "/ui/..%2fmain.py",
    "/ui/%2e%2e%2fapp%2fmain.py", "/ui/APP.JS", "/ui/app.py", "/ui/.env", "/ui/%5c..%5cmain.py", "/ui//app.js",
    "/ui/app.js%00", "/ui/%00", "/ui/ui_routes.py", "/ui/__pycache__",
])
def test_unknown_or_tricky_asset_paths_are_the_normal_json_404(client, path):
    r = client.get(path)
    assert r.status_code == 404, path
    assert r.text == NOT_FOUND
    assert "content-security-policy" not in r.headers


@pytest.mark.parametrize("path", ["/ui/app.js/", "/ui/app.js%2f"])
def test_a_trailing_slash_only_redirects_and_serves_nothing(client, path):
    r = client.get(path)
    assert r.status_code == 307 and r.text == ""
    assert r.headers["location"].endswith("/ui/app.js")
    assert "content-security-policy" not in r.headers


def test_the_lookup_refuses_every_hostile_name_and_serves_only_the_listed_files():
    assert set(ui_routes.UI_FILES) >= {"app.js", "app.css"}
    for name in ["../main.py", "..", ".", "", "a/b.js", "a\\b.js", "app.js\x00", "APP.JS", "app.JS", ".env", "x.py",
                 "index.html", "app.js ", " app.js", "%2e%2e", "app.js/", "аpp.js"]:
        assert ui_routes.lookup(name) is None, repr(name)
    body, content_type = ui_routes.lookup("app.js")
    assert isinstance(body, bytes) and content_type.startswith("text/javascript")


def test_only_plain_regular_files_with_safe_names_are_loaded():
    for name in ui_routes.UI_FILES:
        assert ui_routes.NAME_PATTERN.fullmatch(name), name
    assert "index.html" not in ui_routes.UI_FILES


def test_rest_responses_are_unchanged_by_the_ui_headers(client):
    r = client.get("/ownership-token", params={"domain": "x.test"})
    assert r.status_code == 200 and "content-security-policy" not in r.headers
    r = client.get("/definitely-not-a-route")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert "content-security-policy" not in r.headers
    r = client.get("/audit")
    assert r.status_code == 405 and r.headers["allow"] == "POST" and "content-security-policy" not in r.headers


def test_only_get_is_served_on_ui_routes(client):
    for method, path in [("POST", "/"), ("PUT", "/"), ("DELETE", "/"), ("POST", "/ui/app.js"), ("PUT", "/ui/app.js"),
                         ("DELETE", "/ui/app.css")]:
        r = client.request(method, path)
        assert r.status_code == 405, (method, path)
        assert "GET" in r.headers.get("allow", "")


def test_the_ui_routes_are_not_listed_in_the_openapi_document(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths and not any(p.startswith("/ui") for p in paths)
