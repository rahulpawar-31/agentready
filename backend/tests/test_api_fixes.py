import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.fetch import CachedFetcher
from app.fixes.secrets import find_secrets
from tests.conftest import FakeFetcher, page, public_resolver

B = "https://x.test"
TEXT = "<p>" + "word " * 80 + "</p>"
SPEC = {"openapi": "3.0.0", "info": {"title": "Acme API", "version": "1"},
        "paths": {"/products": {"get": {"operationId": "listProducts", "summary": "List products"}}}}


def site_html(host="x.test"):
    token = main.make_token(host, main.SECRET)
    return ('<html>\n<head>\n<title>Acme Shoes</title>\n<meta name="description" content="Handmade shoes.">\n'
            f'<meta name="agentready-verification" content="{token}">\n</head>\n<body>\n'
            f'<a href="/shop">Shop</a>\n{TEXT}\n</body>\n</html>\n')


def pages_for(extra=None, home=None):
    pages = {f"{B}/": page(f"{B}/", home or site_html())}
    for path, body in (extra or {}).items():
        pages[f"{B}{path}"] = page(f"{B}{path}", body)
    return pages


def client(monkeypatch, pages, *, audit_fetcher=None):
    probe = FakeFetcher(pages)
    audit_side = audit_fetcher or probe
    monkeypatch.setattr(main, "build_probe_fetcher", lambda host, **kw: probe)
    monkeypatch.setattr(main, "build_audit_fetcher", lambda host, snapshot="default", **kw: audit_side)
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    main.app.dependency_overrides[main.get_resolver] = lambda: public_resolver
    return TestClient(main.app), audit_side


def audited(monkeypatch, extra=None, home=None):
    c, fake = client(monkeypatch, pages_for(extra, home))
    body = c.post("/audit", json={"url": B}).json()
    return c, fake, body["id"], body


@pytest.fixture(autouse=True)
def _clean_stores():
    yield
    for store in (main._reports, main._baselines, main._proposals):
        store.clear()


def test_audit_response_is_unchanged_and_the_baseline_comes_from_the_cache(monkeypatch, tmp_path):
    underlying = FakeFetcher(pages_for({"/openapi.json": json.dumps(SPEC)}))
    cached = CachedFetcher(underlying, tmp_path)
    c, _ = client(monkeypatch, pages_for({"/openapi.json": json.dumps(SPEC)}), audit_fetcher=cached)
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "site", "score", "category_scores", "findings", "unscored", "score_status"}
    fetched = [u for u, _ in underlying.calls]
    assert len(fetched) == len(set(fetched)) == 9                  # the audit's nine paths; the baseline added none
    baseline = main._baselines[body["id"]]
    assert baseline.body("/").startswith("<html>") and baseline.spec()[0] == "/openapi.json"


def test_propose_then_review_then_approve_then_export(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    r = c.post(f"/report/{rid}/fixes")
    assert r.status_code == 200
    body = r.json()
    assert {f["id"] for f in body["fixes"]} == {"llms_txt", "json_ld"}
    assert {s["kind"] for s in body["skipped"]} == {"mcp_stub"} and body["skipped"][0]["reason"]
    assert all(f["diff"] and f["status"] == "proposed" and "content" not in f for f in body["fixes"])
    assert '"content"' not in r.text
    assert c.get(f"/report/{rid}/fixes/llms_txt/export").status_code == 409                # not approved yet
    approved = c.post(f"/report/{rid}/fixes/llms_txt/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved" and "content" not in approved.json()
    e = c.get(f"/report/{rid}/fixes/llms_txt/export")
    assert e.status_code == 200
    data = e.json()
    assert data["filename"] == "llms.txt" and data["content"].startswith("# Acme Shoes") and data["diff"]
    assert set(data) == {"filename", "content", "diff", "sha256", "note"} and len(data["sha256"]) == 64
    assert c.get(f"/report/{rid}/fixes/json_ld/export").status_code == 409                # the other fix is still unapproved
    assert c.get(f"/report/{rid}/fixes").json()["fixes"][0]["status"] == "approved"


def test_the_409_says_what_to_do(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    c.post(f"/report/{rid}/fixes")
    r = c.get(f"/report/{rid}/fixes/llms_txt/export")
    assert r.status_code == 409 and "Approve this fix before exporting it" in r.json()["detail"]


def test_proposing_twice_never_resets_a_decision(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    c.post(f"/report/{rid}/fixes")
    c.post(f"/report/{rid}/fixes/llms_txt/approve")
    c.post(f"/report/{rid}/fixes/json_ld/reject")
    again = c.post(f"/report/{rid}/fixes").json()
    states = {f["id"]: f["status"] for f in again["fixes"]}
    assert states == {"llms_txt": "approved", "json_ld": "rejected"}
    assert c.get(f"/report/{rid}/fixes/llms_txt/export").status_code == 200


def test_reject_after_approve_blocks_export_and_reapprove_is_409(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    c.post(f"/report/{rid}/fixes")
    c.post(f"/report/{rid}/fixes/llms_txt/approve")
    assert c.post(f"/report/{rid}/fixes/llms_txt/reject").json()["status"] == "rejected"
    assert c.get(f"/report/{rid}/fixes/llms_txt/export").status_code == 409
    r = c.post(f"/report/{rid}/fixes/llms_txt/approve")
    assert r.status_code == 409 and "propose again" in r.json()["detail"]


def test_unknown_report_or_fix_is_404_and_get_before_post_is_404(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    assert c.post("/report/nope/fixes").status_code == 404
    assert c.get("/report/nope/fixes").status_code == 404
    assert c.get(f"/report/{rid}/fixes").status_code == 404 and "No fixes proposed yet" in c.get(f"/report/{rid}/fixes").json()["detail"]
    for verb, path in (("post", "approve"), ("post", "reject"), ("get", "export")):
        assert getattr(c, verb)(f"/report/{rid}/fixes/llms_txt/{path}").status_code == 404      # nothing proposed yet
    c.post(f"/report/{rid}/fixes")
    for verb, path in (("post", "approve"), ("post", "reject"), ("get", "export")):
        assert getattr(c, verb)(f"/report/{rid}/fixes/mcp_stub/{path}").status_code == 404      # skipped, so unknown
        assert getattr(c, verb)(f"/report/{rid}/fixes/nope/{path}").status_code == 404


def test_an_unverified_site_gets_no_report_and_so_no_fixes(monkeypatch):
    c, fake = client(monkeypatch, {})
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 403
    assert main._reports == {} and main._baselines == {} and main._proposals == {}
    assert c.post("/report/anything/fixes").status_code == 404


def test_every_export_of_a_realistic_site_passes_the_secret_scan(monkeypatch):
    c, _, rid, _ = audited(monkeypatch, extra={"/openapi.json": json.dumps(SPEC)})
    body = c.post(f"/report/{rid}/fixes").json()
    assert {f["id"] for f in body["fixes"]} == {"llms_txt", "json_ld", "mcp_stub"}
    for f in body["fixes"]:
        c.post(f"/report/{rid}/fixes/{f['id']}/approve")
        e = c.get(f"/report/{rid}/fixes/{f['id']}/export").json()
        assert find_secrets(e["content"]) == [] and find_secrets(e["diff"]) == []
        assert e["filename"] in {"llms.txt", "jsonld-snippet.html", "mcp_server_stub.py"}
    stub = c.get(f"/report/{rid}/fixes/mcp_stub/export").json()
    assert 'BASE_URL = "https://x.test"' in stub["content"] and "listProducts" in stub["content"]
    assert "template" in c.get(f"/report/{rid}/fixes/json_ld/export").json()["note"]


def test_no_fix_endpoint_makes_a_request_to_the_site(monkeypatch):
    c, fake, rid, _ = audited(monkeypatch, extra={"/openapi.json": json.dumps(SPEC)})
    before = len(fake.calls)
    c.post(f"/report/{rid}/fixes")
    for fid in ("llms_txt", "json_ld", "mcp_stub"):
        c.post(f"/report/{rid}/fixes/{fid}/approve")
        c.get(f"/report/{rid}/fixes/{fid}/export")
        c.post(f"/report/{rid}/fixes/{fid}/reject")
    c.get(f"/report/{rid}/fixes")
    assert len(fake.calls) == before


def test_old_reports_are_evicted_together_with_their_baselines_and_fixes(monkeypatch):
    monkeypatch.setattr(main, "MAX_REPORTS", 1)
    c, _, first, _ = audited(monkeypatch)
    c.post(f"/report/{first}/fixes")
    second = c.post("/audit", json={"url": B}).json()["id"]
    assert first not in main._reports and first not in main._baselines and first not in main._proposals
    assert c.get(f"/report/{first}/fixes").status_code == 404 and c.post(f"/report/{first}/fixes").status_code == 404
    assert c.post(f"/report/{second}/fixes").status_code == 200


def test_site_text_in_the_diff_is_returned_as_data_not_markup(monkeypatch):
    home = site_html().replace("Acme Shoes", "&lt;img src=x onerror=alert(1)&gt;")
    c, _, rid, _ = audited(monkeypatch, home=home)
    r = c.post(f"/report/{rid}/fixes")
    assert r.headers["content-type"].startswith("application/json")
    assert "<img" not in r.text and "<script>alert" not in r.text           # site text never arrives as raw markup
    diffs = {f["id"]: f["diff"] for f in r.json()["fixes"]}
    assert "\\u003cimg" in diffs["json_ld"] and "onerror" in diffs["json_ld"]
