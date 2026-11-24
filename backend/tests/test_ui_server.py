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


# ---------------------------------------------------------------- Group D: more headers, and HEAD
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


@pytest.mark.parametrize("path", ["/", "/ui/app.js", "/ui/app.css"])
def test_the_ui_also_sends_isolation_and_permission_headers_with_exact_values(client, path):
    r = client.get(path)
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["permissions-policy"] == PERMISSIONS


def test_rest_responses_carry_none_of_the_ui_only_headers(client):
    for r in (client.get("/ownership-token", params={"domain": "x.test"}), client.get("/definitely-not-a-route"), client.get("/audit"),
              client.get("/openapi.json")):
        for header in ("cross-origin-opener-policy", "cross-origin-resource-policy", "permissions-policy", "content-security-policy"):
            assert header not in r.headers, (r.request.url.path, header)


@pytest.mark.parametrize("path", ["/", "/ui/app.js", "/ui/app.css"])
def test_head_returns_the_same_headers_as_get_and_no_body(client, path):
    got, head = client.get(path), client.head(path)
    assert head.status_code == 200 and head.content == b""
    for name in ("content-type", "content-security-policy", "x-content-type-options", "referrer-policy", "cache-control",
                 "cross-origin-opener-policy", "cross-origin-resource-policy", "permissions-policy"):
        assert head.headers[name] == got.headers[name], name
    assert head.headers["content-length"] == str(len(got.content))


@pytest.mark.parametrize("path", ["/ui/nope.js", "/ui/index.html", "/ui/%2e%2e/main.py", "/ui/APP.JS", "/ui/"])
def test_head_on_anything_that_is_not_a_listed_file_is_a_404_without_ui_headers(client, path):
    r = client.head(path)
    assert r.status_code == 404 and r.content == b"", path
    assert "content-security-policy" not in r.headers


def test_other_methods_on_ui_routes_still_say_which_methods_are_allowed(client):
    r = client.post("/")
    assert r.status_code == 405 and set(r.headers["allow"].replace(" ", "").split(",")) == {"GET", "HEAD"}


# ---------------------------------------------------------------- Group E: files are read once, and links are never followed
def test_assets_come_from_memory_and_not_from_the_disk_at_request_time(client, monkeypatch, tmp_path):
    (tmp_path / "app.js").write_text("// a different file on disk\n")
    (tmp_path / "app.css").write_text("/* different */\n")
    monkeypatch.setattr(ui_routes, "UI_DIR", tmp_path)
    served = client.get("/ui/app.js")
    assert served.status_code == 200
    assert "a different file on disk" not in served.text
    assert served.content == ui_routes.UI_FILES["app.js"][0]


def test_a_symbolic_link_in_the_ui_directory_is_never_loaded(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.js").write_text("const secret = 1;\n")
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "app.js").write_text("// real\n")
    (ui / "index.html").write_text("<h1>x</h1>")
    (ui / "evil.js").symlink_to(outside / "secret.js")
    (ui / "evil.css").symlink_to(outside)
    monkeypatch.setattr(ui_routes, "UI_DIR", ui)
    index, files = ui_routes._load()
    assert set(files) == {"app.js"} and index == b"<h1>x</h1>"


def test_a_missing_ui_directory_loads_nothing_instead_of_failing(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_routes, "UI_DIR", tmp_path / "nope")
    assert ui_routes._load() == (b"", {})


def test_only_lower_case_js_and_css_files_are_loaded_from_the_directory(monkeypatch, tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    for name in ["app.js", "app.css", "Upper.js", "page.html", "script.py", "notes.txt", "a b.js", "x.JS"]:
        (ui / name).write_text("// x\n")
    (ui / "index.html").write_text("<h1>x</h1>")
    monkeypatch.setattr(ui_routes, "UI_DIR", ui)
    index, files = ui_routes._load()
    assert set(files) == {"app.js", "app.css"}
    assert index == b"<h1>x</h1>"


def test_a_head_response_has_an_empty_body_but_the_length_of_the_real_one():
    body = b"x" * 1234
    head = ui_routes._response(body, "text/css; charset=utf-8", head=True)
    assert head.body == b"" and head.headers["content-length"] == "1234"
    get = ui_routes._response(body, "text/css; charset=utf-8")
    assert get.body == body and get.headers["content-length"] == "1234"
    for name, value in ui_routes.UI_HEADERS.items():
        assert head.headers[name.lower()] == value == get.headers[name.lower()]
