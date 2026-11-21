import json

from app.audit import audit
from app.fixes.baseline import capture_baseline
from app.fixes.models import FixKind
from app.fixes.propose import insert_before_head_close, propose_fixes
from app.fixes.secrets import find_secrets
from tests.conftest import FakeFetcher, page

O = "https://x.test"
HOME = ('<html><head><title>Acme Shoes</title><meta name="description" content="Handmade shoes.">'
        '</head><body><a href="/shop">Shop</a>' + "<p>" + "word " * 80 + "</p></body></html>")
HOME_ML = ('<html>\n<head>\n  <title>Acme Shoes</title>\n  <meta name="description" content="Handmade shoes.">\n'
           '</head>\n<body>\n<a href="/shop">Shop</a>\n<p>' + "word " * 80 + "</p>\n</body>\n</html>\n")


def run(pages):
    served = {}
    for path, value in pages.items():
        body, status = value if isinstance(value, tuple) else (value, 200)
        served[f"{O}{path}"] = page(f"{O}{path}", body, status)
    f = FakeFetcher(served)
    report = audit(O, f)
    return propose_fixes(report, capture_baseline(O, f)), f


def kinds(p):
    return [x.kind for x in p.fixes], {s.kind: s.reason for s in p.skipped}


def test_a_bare_site_gets_llms_txt_and_json_ld_and_a_reason_for_the_stub():
    p, _ = run({"/": HOME})
    got, skipped = kinds(p)
    assert got == [FixKind.LLMS_TXT, FixKind.JSON_LD] and "OpenAPI" in skipped[FixKind.MCP_STUB]
    assert all(x.diff and x.content and x.finding_id for x in p.fixes)
    assert p.fixes[0].diff.startswith("--- /dev/null") and p.fixes[0].path == "/llms.txt"
    assert [x.id for x in p.fixes] == ["llms_txt", "json_ld"] and all(x.status.value == "proposed" for x in p.fixes)


def test_existing_llms_txt_and_json_ld_are_not_replaced():
    home = HOME.replace("</head>", '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite"}</script></head>')
    p, _ = run({"/": home, "/llms.txt": "# Mine\n> mine\n"})
    assert p.fixes == [] and {s.kind for s in p.skipped} == {FixKind.LLMS_TXT, FixKind.JSON_LD, FixKind.MCP_STUB}
    assert "already present" in kinds(p)[1][FixKind.LLMS_TXT]


def test_json_ld_diff_inserts_the_snippet_before_the_head_close_and_removes_nothing():
    p, _ = run({"/": HOME_ML})
    d = next(x for x in p.fixes if x.kind is FixKind.JSON_LD).diff.splitlines()
    assert '+<script type="application/ld+json">' in d and " </head>" in d          # </head> stays as a context line
    assert not [l for l in d if l.startswith("-") and not l.startswith("---")]
    assert d[0] == "--- a/index.html" and d[1] == "+++ b/index.html"


def test_minified_html_is_split_at_the_head_close_and_the_diff_replaces_that_one_line():
    p, _ = run({"/": HOME})
    fix = next(x for x in p.fixes if x.kind is FixKind.JSON_LD)
    patched = insert_before_head_close(HOME, fix.content)
    i = HOME.index("</head>")
    assert patched == HOME[:i] + "\n" + fix.content + HOME[i:]
    assert any(l.startswith("-") and "<title>" in l for l in fix.diff.splitlines())      # the single long line is shown replaced


def test_insert_before_head_close_handles_case_and_missing_head():
    assert insert_before_head_close("<HEAD>\n</HEAD>\n", "S\n") == "<HEAD>\nS\n</HEAD>\n"
    assert insert_before_head_close("<body>no head</body>", "S\n") is None
    assert insert_before_head_close("<head></head><head></head>", "S\n").count("S") == 1


def test_home_without_head_close_skips_json_ld_with_a_reason():
    p, _ = run({"/": "<title>T</title><meta name='description' content='d'><body>" + "x " * 200})
    assert "no </head>" in kinds(p)[1][FixKind.JSON_LD]


def test_an_openapi_document_gets_a_stub():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "A"}}}}
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    stub = next(x for x in p.fixes if x.kind is FixKind.MCP_STUB)
    assert stub.path == "/openapi.json" and stub.filename == "mcp_server_stub.py" and stub.diff.startswith("--- /dev/null")
    assert stub.finding_id == "openapi_public"


def test_a_spec_with_no_get_operations_is_skipped_with_a_reason():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"post": {}}}}
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    assert "no GET operations" in kinds(p)[1][FixKind.MCP_STUB]


def test_a_secret_in_site_text_skips_the_fix_and_the_value_never_appears():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": f"key {secret}"}}}}
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    assert FixKind.MCP_STUB not in kinds(p)[0]
    reason = kinds(p)[1][FixKind.MCP_STUB]
    assert "secret" in reason and secret not in reason
    assert secret not in "".join(x.content + x.diff for x in p.fixes) + "".join(s.reason for s in p.skipped)


def test_a_secret_in_the_home_page_html_that_the_diff_would_show_skips_json_ld():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    p, _ = run({"/": HOME_ML.replace("<body>", f"<body>\n{secret}")})
    reason = kinds(p)[1][FixKind.JSON_LD]                  # the diff's context lines would show the secret
    assert FixKind.JSON_LD not in kinds(p)[0] and "secret" in reason and secret not in reason
    assert all(find_secrets(x.content + x.diff) == [] for x in p.fixes)


def test_an_oversized_diff_is_skipped():
    big = "<html><head><title>T</title><meta name='description' content='d'></head><body>" + "x " * 100_000 + "</body></html>"
    p, _ = run({"/": big})
    assert "too large" in kinds(p)[1][FixKind.JSON_LD]


def test_an_invalid_output_is_never_presented(monkeypatch):
    import app.fixes.propose as mod
    monkeypatch.setattr(mod, "build_llms_txt", lambda origin, html: "garbage without heading\n")
    p, _ = run({"/": HOME})
    assert FixKind.LLMS_TXT not in kinds(p)[0] and kinds(p)[1][FixKind.LLMS_TXT].startswith("failed validation")


def test_invalid_json_ld_and_stub_are_never_presented_either(monkeypatch):
    import app.fixes.propose as mod
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {}}}}
    monkeypatch.setattr(mod, "json_ld_snippet", lambda data: "<script>x()</script>\n")
    monkeypatch.setattr(mod, "build_mcp_stub", lambda origin, s: "def broken(:\n")
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    assert [x.kind for x in p.fixes] == [FixKind.LLMS_TXT]
    assert all(r.startswith("failed validation") for k, r in kinds(p)[1].items() if k is not FixKind.LLMS_TXT)


def test_unreachable_home_skips_everything_that_needs_it():
    p, _ = run({})
    assert p.fixes == [] and all(s.reason for s in p.skipped) and len(p.skipped) == 3
    p2, _ = run({"/": ("", 0)})
    assert p2.fixes == []


def test_an_llms_txt_that_could_not_be_checked_is_not_proposed_as_missing():
    p, _ = run({"/": HOME, "/llms.txt": ("", 0)})
    assert FixKind.LLMS_TXT not in kinds(p)[0] and "could not" in kinds(p)[1][FixKind.LLMS_TXT]


def test_proposals_are_deterministic_and_make_no_requests():
    a, f = run({"/": HOME})
    n = len(f.calls)
    report = audit(O, f)
    n_after_audit = len(f.calls)
    b = propose_fixes(report, capture_baseline(O, f))
    c = propose_fixes(report, capture_baseline(O, f))
    assert [(x.id, x.content, x.diff) for x in a.fixes] == [(x.id, x.content, x.diff) for x in b.fixes] == \
           [(x.id, x.content, x.diff) for x in c.fixes]
    base = capture_baseline(O, f)
    calls = len(f.calls)
    propose_fixes(report, base)
    assert len(f.calls) == calls        # propose_fixes itself takes no fetcher and makes no request
