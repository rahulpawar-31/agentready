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


def test_a_secret_only_in_the_home_page_context_is_a_warning_not_a_block():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    p, _ = run({"/": HOME_ML.replace("<body>", f"<body>\n{secret}")})
    ld = next(x for x in p.fixes if x.kind is FixKind.JSON_LD)
    assert "aws access key" in ld.warning and secret not in ld.warning and secret not in ld.content
    assert all(find_secrets(x.content) == [] for x in p.fixes)


def test_a_page_whose_diff_would_be_too_large_gets_an_excerpt_not_a_skip():
    big = "<html><head><title>T</title><meta name='description' content='d'></head><body>" + "x " * 100_000 + "</body></html>"
    p, _ = run({"/": big})
    ld = next(x for x in p.fixes if x.kind is FixKind.JSON_LD)
    assert ld.diff_kind == "excerpt" and len(ld.diff) < 5000


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


def test_proposals_are_deterministic():
    a, f = run({"/": HOME})
    report = audit(O, f)
    b = propose_fixes(report, capture_baseline(O, f))
    c = propose_fixes(report, capture_baseline(O, f))
    assert [(x.id, x.content, x.diff) for x in a.fixes] == [(x.id, x.content, x.diff) for x in b.fixes] == \
           [(x.id, x.content, x.diff) for x in c.fixes]
    assert [(s.kind, s.reason) for s in a.skipped] == [(s.kind, s.reason) for s in b.skipped]


def test_proposing_makes_no_request_and_writes_no_file(monkeypatch):
    import builtins
    import socket
    f = FakeFetcher({f"{O}/": page(f"{O}/", HOME), f"{O}/openapi.json": page(
        f"{O}/openapi.json", json.dumps({"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "A"}}}}))})
    report = audit(O, f)
    baseline = capture_baseline(O, f)

    def forbidden(*args, **kwargs):
        raise AssertionError("propose_fixes touched the network or a file")

    real_open = builtins.open
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(builtins, "open", lambda file, mode="r", *a, **k: forbidden() if any(x in mode for x in "wax+")
                        else real_open(file, mode, *a, **k))
    calls = len(f.calls)
    p = propose_fixes(report, baseline)
    assert [x.id for x in p.fixes] == ["llms_txt", "json_ld", "mcp_stub"] and len(f.calls) == calls


# --- Group B: secrets are checked in the text a site supplies and in what is generated, not in page context
def _g(n):
    return ("a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0" * 3)[:n]


def home_with(description="Handmade shoes.", title="Acme Shoes", link_text="Shop", extra_body=""):
    return (f'<html><head><title>{title}</title><meta name="description" content="{description}"></head>'
            f'<body><a href="/shop">{link_text}</a>{extra_body}<p>' + "word " * 80 + "</p></body></html>")


def test_b_a_secret_in_the_description_skips_both_fixes_and_never_appears():
    secret = "whsec_" + _g(20)
    p, _ = run({"/": home_with(description=f"STRIPE_SECRET_KEY={secret}")})
    reasons = kinds(p)[1]
    assert p.fixes == [] and "secret" in reasons[FixKind.LLMS_TXT] and "secret" in reasons[FixKind.JSON_LD]
    assert secret not in "".join(reasons.values())


def test_b_a_provider_key_in_free_text_is_caught_even_after_markdown_cleaning_would_change_it():
    key = "sk_" + "live_" + _g(24)                    # the Markdown clean-up removes underscores: scan the raw text
    p, _ = run({"/": home_with(title=f"Pay with {key} now")})
    assert p.fixes == [] and all("secret" in r for r in kinds(p)[1].values() if "OpenAPI" not in r)
    assert key not in "".join(r for r in kinds(p)[1].values())


def test_b_a_secret_in_link_text_drops_that_link_but_keeps_the_fix():
    key = "AIza" + _g(35)
    p, _ = run({"/": home_with(link_text=key, extra_body='<a href="/about">About us</a>')})
    fix = next(x for x in p.fixes if x.kind is FixKind.LLMS_TXT)
    assert "About us" in fix.content and key not in fix.content and key not in fix.diff


def test_b_a_secret_in_a_link_path_drops_that_link():
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    p, _ = run({"/": home_with(extra_body=f'<a href="/reset/{key}">Reset</a>')})
    fix = next(x for x in p.fixes if x.kind is FixKind.LLMS_TXT)
    assert "Reset" not in fix.content and key not in fix.content


def test_b_a_secret_in_an_openapi_summary_skips_only_the_stub():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "key " + "sk_" + "live_" + _g(24)}}}}
    p, _ = run({"/": home_with(), "/openapi.json": json.dumps(spec)})
    assert FixKind.MCP_STUB not in kinds(p)[0] and "secret" in kinds(p)[1][FixKind.MCP_STUB]
    assert [x.kind for x in p.fixes] == [FixKind.LLMS_TXT, FixKind.JSON_LD]


def test_i1_a_public_jwt_in_the_page_does_not_block_either_fix_and_is_flagged_as_a_warning():
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJyb2xlIjoiYW5vbiJ9." + "c2lnbmF0dXJlMTIzNDU2"
    p, _ = run({"/": home_with(extra_body=f"<script>var supabaseKey = '{jwt}';</script>")})
    assert [x.kind for x in p.fixes] == [FixKind.LLMS_TXT, FixKind.JSON_LD]
    ld = p.fixes[1]
    assert "jwt" in ld.warning and "did not copy it" in ld.warning and jwt not in ld.warning
    assert jwt in ld.diff                                    # the page's own line is shown as context, as before
    assert p.fixes[0].warning == ""


def test_i1_an_html_soft_404_at_llms_txt_is_not_a_real_llms_txt():
    shell = "<!DOCTYPE html><html><head><title>App</title></head><body><div id=root></div></body></html>"
    p, _ = run({"/": home_with(), "/llms.txt": shell})
    fix = next(x for x in p.fixes if x.kind is FixKind.LLMS_TXT)
    assert fix.diff.startswith("--- /dev/null") and "HTML page" in fix.note and "<div" not in fix.diff


def test_i1_a_real_existing_llms_txt_is_still_left_alone():
    p, _ = run({"/": home_with(), "/llms.txt": "# Mine\n> mine\n"})
    assert FixKind.LLMS_TXT not in kinds(p)[0] and "already present" in kinds(p)[1][FixKind.LLMS_TXT]
