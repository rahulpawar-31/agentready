import pytest
from app.audit import audit
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
