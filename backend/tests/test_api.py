import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from tests.conftest import FakeFetcher, page, public_resolver

B = "https://x.test"
BACKEND = Path(__file__).resolve().parent.parent
HEAD = '<html><head><meta name="agentready-verification" content="{t}"></head><body>{b}</body></html>'
TEXT = "<p>" + "word " * 80 + "</p>"


def client(monkeypatch, pages, *, resolver=None, txt=None):
    fake = FakeFetcher(pages)
    monkeypatch.setattr(main, "build_probe_fetcher", lambda host, **kw: fake)
    monkeypatch.setattr(main, "build_audit_fetcher", lambda host, snapshot="default", **kw: fake)
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (txt or (lambda name: []))
    main.app.dependency_overrides[main.get_resolver] = lambda: (resolver or public_resolver)
    return TestClient(main.app), fake


def verified_home(host="x.test"):
    return HEAD.format(t=main.make_token(host, main.SECRET), b=TEXT)


def test_audit_without_proof_is_403_with_instructions_and_audits_nothing(monkeypatch):
    c, fake = client(monkeypatch, {})
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 403
    assert "agentready-verification" in r.json()["detail"]["meta_tag"]
    assert fake.calls == [(f"{B}/", False)]          # meta-tag probe, no redirect: one request, to the root page


def test_audit_runs_when_meta_tag_present_and_report_is_retrievable(monkeypatch):
    c, _ = client(monkeypatch, {f"{B}/": page(f"{B}/", verified_home())})
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["score"] <= 100 and body["findings"] and "unscored" in body
    assert body["score_status"] == "scored"
    assert c.get(f"/report/{body['id']}").json()["site"] == B


ALL_NINE = ["/", "/robots.txt", "/sitemap.xml", "/llms.txt", "/openapi.json", "/swagger.json",
            "/.well-known/openapi.json", "/.env", "/.git/HEAD"]


def test_an_audit_where_nothing_could_be_fetched_is_marked_not_scored(monkeypatch):
    c, _ = client(monkeypatch, {f"{B}/": page(f"{B}/", verified_home())})     # the probe sees the home page
    dead = FakeFetcher({f"{B}{p}": page(f"{B}{p}", status=0) for p in ALL_NINE})
    monkeypatch.setattr(main, "build_audit_fetcher", lambda host, snapshot="default", **kw: dead)
    body = c.post("/audit", json={"url": B}).json()
    assert body["score"] == 0 and body["score_status"] == "not_scored" and body["unscored"] == 13


def test_ownership_token_endpoint_gives_both_methods(monkeypatch):
    c, _ = client(monkeypatch, {})
    r = c.get("/ownership-token", params={"domain": "x.test"}).json()
    assert r["dns_txt_name"] == "_agentready.x.test" and r["token"] in r["meta_tag"]


def test_ownership_token_for_a_port_has_its_own_token_and_no_dns_instructions(monkeypatch):
    c, _ = client(monkeypatch, {})
    plain = c.get("/ownership-token", params={"domain": "example.com"}).json()
    port = c.get("/ownership-token", params={"domain": "Example.com:8443"}).json()
    assert port["token"] != plain["token"] and port["dns_txt_name"] is None
    assert port["domain"] == "example.com:8443"
    assert c.get("/ownership-token", params={"domain": "user:pw@example.com"}).status_code == 422


def test_unknown_report_is_404_and_bad_url_is_422(monkeypatch):
    c, _ = client(monkeypatch, {})
    assert c.get("/report/nope").status_code == 404
    assert c.post("/audit", json={"url": "not a url"}).status_code == 422


@pytest.mark.parametrize("url", ["https://../", "https://user:pw@example.com/", "https://example.com:abc/",
                                 "https://example.com:99999/", "https://bücher.test/", "ftp://x.test",
                                 "http://127.0.0.1:8000"])
def test_unsafe_urls_are_422_never_500_and_make_no_request(monkeypatch, url):
    c, fake = client(monkeypatch, {}, resolver=lambda h: ["127.0.0.1"] if h == "127.0.0.1" else ["93.184.216.34"])
    r = c.post("/audit", json={"url": url})
    assert r.status_code == 422 and fake.calls == []
    assert "pw" not in r.text


@pytest.mark.parametrize("answers", [["10.0.0.5"], ["93.184.216.34", "10.0.0.5"], []])
def test_a_host_that_cannot_be_audited_is_422_with_no_instructions_and_no_probe(monkeypatch, answers):
    c, fake = client(monkeypatch, {}, resolver=lambda h: answers)
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 422 and fake.calls == []
    assert r.json()["detail"] == "host does not resolve to a public address and cannot be audited"
    assert "token" not in r.text and "meta" not in r.text.lower()


def test_403_for_a_default_port_site_names_a_valid_dns_record(monkeypatch):
    c, _ = client(monkeypatch, {})
    d = c.post("/audit", json={"url": "https://Example.com/"}).json()["detail"]
    assert d["dns_txt_name"] == "_agentready.example.com" and d["dns_txt_value"].endswith(d["token"])


def test_403_for_a_port_site_offers_only_the_meta_tag(monkeypatch):
    c, _ = client(monkeypatch, {})
    r = c.post("/audit", json={"url": "https://example.com:8443/"})
    d = r.json()["detail"]
    assert r.status_code == 403 and d["dns_txt_name"] is None and "agentready-verification" in d["meta_tag"]
    assert "8443" not in (d["dns_txt_name"] or "")


def test_dns_proof_counts_for_a_default_port_but_not_for_a_port(monkeypatch):
    token = main.make_token("example.com", main.SECRET)
    c, _ = client(monkeypatch, {}, txt=lambda name: [f"agentready-verify={token}"] if name == "_agentready.example.com" else [])
    assert c.post("/audit", json={"url": "https://example.com/"}).status_code == 200
    port_token = main.make_token("example.com:8443", main.SECRET)
    c, _ = client(monkeypatch, {}, txt=lambda name: [f"agentready-verify={port_token}", f"agentready-verify={token}"])
    assert c.post("/audit", json={"url": "https://example.com:8443/"}).status_code == 403


@pytest.mark.parametrize("scheme,port,expected", [
    ("http", 80, 200), ("https", 443, 200), ("http", 443, 403), ("https", 80, 403),
])
def test_dns_proof_requires_the_default_port_for_the_requested_scheme(monkeypatch, scheme, port, expected):
    host = f"example.com:{port}"
    origin = f"{scheme}://{host}"
    token = main.make_token(host, main.SECRET)
    lookups = []

    def txt(name):
        lookups.append(name)
        return [f"agentready-verify={token}"]

    c, fake = client(monkeypatch, {}, txt=txt)
    response = c.post("/audit", json={"url": origin})

    assert response.status_code == expected
    if expected == 403:
        assert lookups == []
        assert fake.calls == [(f"{origin}/", False)]
        assert response.json()["detail"]["dns_txt_name"] is None
    else:
        assert lookups == ["_agentready.example.com"]


def test_dns_verification_makes_no_probe_request(monkeypatch):
    token = main.make_token("example.com", main.SECRET)
    probe, audit_f = FakeFetcher({}), FakeFetcher({})
    monkeypatch.setattr(main, "build_probe_fetcher", lambda host, **kw: probe)
    monkeypatch.setattr(main, "build_audit_fetcher", lambda host, snapshot="default", **kw: audit_f)
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda n: [f"agentready-verify={token}"])
    main.app.dependency_overrides[main.get_resolver] = lambda: public_resolver
    assert TestClient(main.app).post("/audit", json={"url": "https://example.com"}).status_code == 200
    assert probe.calls == []


@pytest.mark.parametrize("snapshot", ["../x", "a b", "", "x" * 65, "a/b", "..", "a.b"])
def test_a_bad_snapshot_is_422(monkeypatch, snapshot):
    c, fake = client(monkeypatch, {})
    assert c.post("/audit", json={"url": B, "snapshot": snapshot}).status_code == 422 and fake.calls == []


def test_only_the_newest_reports_are_kept(monkeypatch):
    monkeypatch.setattr(main, "MAX_REPORTS", 2)
    c, _ = client(monkeypatch, {f"{B}/": page(f"{B}/", verified_home())})
    ids = [c.post("/audit", json={"url": B}).json()["id"] for _ in range(3)]
    assert c.get(f"/report/{ids[0]}").status_code == 404
    assert c.get(f"/report/{ids[1]}").status_code == 200 and c.get(f"/report/{ids[2]}").status_code == 200


# --- fail-closed secret -----------------------------------------------------
def _import_main(**env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("AGENTREADY_SECRET", "AGENTREADY_DEV")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", "import app.main"], cwd=BACKEND, env=env,
                          capture_output=True, text=True)


def test_main_refuses_to_start_without_a_secret_or_dev_mode():
    r = _import_main()
    assert r.returncode != 0 and "AGENTREADY_SECRET" in r.stderr


def test_main_starts_with_a_secret_or_in_dev_mode():
    assert _import_main(AGENTREADY_SECRET="s3cret").returncode == 0
    assert _import_main(AGENTREADY_DEV="1").returncode == 0


# --- real wiring: the real guard, cache and HTTP client over a mock transport
SITE_HEADERS = {"strict-transport-security": "max-age=1", "x-content-type-options": "nosniff",
                "content-security-policy": "default-src 'self'"}


def real_client(monkeypatch, tmp_path, *, verified=True):
    seen = []
    home = verified_home() if verified else HEAD.format(t="nope", b=TEXT)
    site = {"/": (200, SITE_HEADERS, home), "/.env": (200, {}, "DB_PASSWORD=hunter2\n"),
            "/.git/HEAD": (200, {}, "ref: refs/heads/main\n")}
    def handler(req):
        seen.append((req.headers["host"], req.url.host, req.url.path))
        status, headers, body = site.get(req.url.path, (404, {}, ""))
        return httpx.Response(status, headers=headers, text=body)
    monkeypatch.setattr(main, "new_http_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False))
    monkeypatch.setattr(main, "GUARD_OPTIONS", {"sleep": lambda s: None})
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path / "cache")
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    main.app.dependency_overrides[main.get_resolver] = lambda: public_resolver
    return TestClient(main.app), seen


def test_real_wiring_unverified_makes_one_request_and_creates_nothing(monkeypatch, tmp_path):
    c, seen = real_client(monkeypatch, tmp_path, verified=False)
    assert c.post("/audit", json={"url": B}).status_code == 403
    assert seen == [("x.test", "93.184.216.34", "/")]
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_real_wiring_meta_verification_uses_the_requested_scheme(monkeypatch, tmp_path, scheme):
    host = "x.test:8080"
    origin = f"{scheme}://{host}"
    seen = []

    def handler(req):
        seen.append((req.url.scheme, req.headers["host"]))
        if req.url.scheme != scheme:
            raise httpx.ConnectError("service does not support this scheme")
        return httpx.Response(200, text=verified_home(host))

    monkeypatch.setattr(main, "new_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "GUARD_OPTIONS", {"sleep": lambda s: None})
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path / "cache")
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    main.app.dependency_overrides[main.get_resolver] = lambda: public_resolver

    response = TestClient(main.app).post("/audit", json={"url": origin})

    assert response.status_code == 200
    assert response.json()["site"] == origin
    assert seen and all(request == (scheme, host) for request in seen)


def test_real_wiring_audit_uses_nine_urls_pins_the_address_and_caches_per_snapshot(monkeypatch, tmp_path):
    c, seen = real_client(monkeypatch, tmp_path)
    r = c.post("/audit", json={"url": B})
    assert r.status_code == 200
    assert len(seen) == 10 and len({p for _, _, p in seen}) == 9          # probe + 9 audit requests
    assert {h for h, _, _ in seen} == {"x.test"} and {ip for _, ip, _ in seen} == {"93.184.216.34"}
    dirs = [d.name for d in (tmp_path / "cache").iterdir()]
    assert len(dirs) == 1 and len(dirs[0]) == 16 and all(ch in "0123456789abcdef" for ch in dirs[0])
    cached = [p.read_text() for p in (tmp_path / "cache").rglob("*.json")]
    assert cached and not any("hunter2" in t or "refs/heads" in t for t in cached)   # secrets never hit disk
    assert "hunter2" not in r.text and "DB_PASSWORD" not in r.text
    before = len(seen)
    c.post("/audit", json={"url": B})                                         # same snapshot: cache hits
    assert [p for _, _, p in seen[before:]] == ["/", "/.env", "/.git/HEAD"]    # probe + the two never-cached paths
    before = len(seen)
    c.post("/audit", json={"url": B, "snapshot": "after"})                     # new snapshot: fetched fresh
    assert len(seen) - before == 10
    assert sorted(p.name for p in (tmp_path / "cache" / dirs[0]).iterdir()) == ["after", "default"]


def test_real_wiring_hostile_url_creates_no_directory(monkeypatch, tmp_path):
    c, seen = real_client(monkeypatch, tmp_path)
    assert c.post("/audit", json={"url": "https://../"}).status_code == 422
    assert seen == [] and not (tmp_path / "cache").exists() and not (tmp_path / "default").exists()


def test_cache_directories_do_not_collide_for_similar_hosts():
    a = main.cache_dir_for("c.example:8080", "default")
    b = main.cache_dir_for("c.example_8080", "default")
    assert a != b and a.parent.parent == main.CACHE_DIR and a.parent.name != "c.example_8080"
    with pytest.raises(ValueError):
        main.cache_dir_for("x.test", "../x")
