import pytest
from app.audit import audit, origin, parse_url
from tests.conftest import FakeFetcher, page

B = "https://x.test"


def test_audit_runs_all_four_categories_with_evidence_and_never_leaves_the_site():
    f = FakeFetcher({})
    rep = audit(f"{B}/some/path?q=1", f)
    cats = {x.category.value for x in rep.findings}
    assert cats == {"discoverability", "operability", "identity", "safety"}
    assert rep.site == B and 0 <= rep.score <= 100 and rep.unscored == 0
    assert all(x.evidence and x.check and x.url and x.confidence for x in rep.findings)
    assert all(url.startswith(B) for url, _ in f.calls)


def test_unreachable_pages_are_counted_as_unscored():
    rep = audit(B, FakeFetcher({f"{B}/": page(f"{B}/", status=0)}))
    assert rep.unscored > 0


def test_audit_rejects_non_http_urls():
    with pytest.raises(ValueError):
        audit("ftp://x.test", FakeFetcher({}))


EXPECTED_PATHS = {"/", "/robots.txt", "/sitemap.xml", "/llms.txt", "/openapi.json", "/swagger.json",
                  "/.well-known/openapi.json", "/.env", "/.git/HEAD"}


def test_an_audit_requests_exactly_the_nine_fixed_urls_and_nothing_else():
    f = FakeFetcher({})
    audit(B, f)
    assert {url for url, _ in f.calls} == {B + p for p in EXPECTED_PATHS}


def test_score_status_says_whether_anything_was_scored():
    assert audit(B, FakeFetcher({})).score_status == "scored"
    dead = audit(B, FakeFetcher({f"{B}{p}": page(f"{B}{p}", status=0) for p in EXPECTED_PATHS}))
    assert dead.score == 0 and dead.score_status == "not_scored" and dead.unscored == 13


def test_origin_normalises_scheme_host_and_keeps_the_port():
    assert origin("HTTPS://Example.COM:8443/x?y=1") == "https://example.com:8443"
    assert origin("http://[2606:2800:220:1::1]:8080/") == "http://[2606:2800:220:1::1]:8080"
    ref = parse_url("https://Example.com:8443/")
    assert (ref.hostname, ref.port, ref.netloc) == ("example.com", 8443, "example.com:8443")
    assert ref.dns_name == "_agentready.example.com" and ref.dns_allowed is False
    assert parse_url("https://example.com/").dns_allowed is True
    assert parse_url("http://example.com:80/").dns_allowed is True
    assert parse_url("http://93.184.216.34/").dns_allowed is False     # no DNS proof for an IP literal


@pytest.mark.parametrize("url,fragment", [
    ("https://user:pw@example.com/", "user information"),
    ("https://example.com:abc/", "port"),
    ("https://example.com:99999/", "port"),
    ("https://example.com:0/", "port"),
    ("https://b\u00fccher.test/", "internationalised"),
    ("https://../", "host"),
    ("https:///path", "http"),
    ("https://exa mple.com/", "host"),
    ("https://-bad.test/", "host"),
    ("ftp://x.test", "http"),
    ("not a url", "http"),
])
def test_origin_rejects_unsafe_urls_without_echoing_them(url, fragment):
    with pytest.raises(ValueError) as e:
        origin(url)
    assert fragment in str(e.value) and "pw" not in str(e.value)
