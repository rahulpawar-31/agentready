from fastapi.testclient import TestClient
from app import main
from tests.conftest import FakeFetcher, page

B = "https://x.test"


def client(monkeypatch, pages):
    fake = FakeFetcher(pages)
    monkeypatch.setattr(main, "build_fetchers", lambda host: (fake, fake))
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    return TestClient(main.app), fake


def test_audit_without_proof_is_403_with_instructions_and_audits_nothing(monkeypatch):
    c, fake = client(monkeypatch, {})
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 403
    assert "agentready-verification" in r.json()["detail"]["meta_tag"]
    assert fake.calls == [(f"{B}/", False)]          # meta-tag probe, no redirect: one request, to the root page


def test_audit_runs_when_meta_tag_present_and_report_is_retrievable(monkeypatch):
    token = main.make_token("x.test", main.SECRET)
    home = f'<meta name="agentready-verification" content="{token}">' + "<p>" + "word " * 80 + "</p>"
    c, _ = client(monkeypatch, {f"{B}/": page(f"{B}/", home)})
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["score"] <= 100 and body["findings"] and "unscored" in body
    assert c.get(f"/report/{body['id']}").json()["site"] == B


def test_ownership_token_endpoint_gives_both_methods(monkeypatch):
    c, _ = client(monkeypatch, {})
    r = c.get("/ownership-token", params={"domain": "x.test"}).json()
    assert r["dns_txt_name"] == "_agentready.x.test" and r["token"] in r["meta_tag"]


def test_unknown_report_is_404_and_bad_url_is_422(monkeypatch):
    c, _ = client(monkeypatch, {})
    assert c.get("/report/nope").status_code == 404
    assert c.post("/audit", json={"url": "not a url"}).status_code == 422
