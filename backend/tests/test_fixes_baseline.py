import json

from app.fixes.baseline import Baseline, capture_baseline
from app.fixes.models import Fix, FixKind, FixStatus
from tests.conftest import FakeFetcher, page

O = "https://x.test"


def test_capture_reads_the_five_known_paths_with_get_only():
    f = FakeFetcher({f"{O}/": page(f"{O}/", "<html>hi</html>")})
    b = capture_baseline(O, f)
    assert [u for u, _ in f.calls] == [f"{O}/", f"{O}/llms.txt", f"{O}/openapi.json",
                                      f"{O}/swagger.json", f"{O}/.well-known/openapi.json"]
    assert b.body("/") == "<html>hi</html>" and b.body("/llms.txt") is None


def test_body_is_none_unless_status_is_200():
    f = FakeFetcher({f"{O}/": page(f"{O}/", "x", status=500)})
    assert capture_baseline(O, f).body("/") is None


def test_spec_returns_the_first_openapi_document_only():
    spec = {"openapi": "3.0.0", "paths": {}}
    f = FakeFetcher({f"{O}/swagger.json": page(f"{O}/swagger.json", json.dumps(spec)),
                     f"{O}/.well-known/openapi.json": page(f"{O}/.well-known/openapi.json", json.dumps({"openapi": "9"}))})
    b = capture_baseline(O, f)
    assert b.spec() == ("/swagger.json", spec)


def test_non_spec_json_and_invalid_json_are_ignored():
    f = FakeFetcher({f"{O}/openapi.json": page(f"{O}/openapi.json", '{"hello": 1}'),
                     f"{O}/swagger.json": page(f"{O}/swagger.json", "{oops"),
                     f"{O}/.well-known/openapi.json": page(f"{O}/.well-known/openapi.json", "[1, 2]")})
    assert capture_baseline(O, f).spec() is None


def test_baseline_is_immutable():
    b = Baseline(O, {})
    try:
        b.origin = "x"
        assert False
    except Exception as e:
        assert e.__class__.__name__ == "FrozenInstanceError"


def test_a_failed_request_is_recorded_as_not_present():
    f = FakeFetcher({f"{O}/": page(f"{O}/", "", status=0)})
    b = capture_baseline(O, f)
    assert b.body("/") is None and b.pages["/"][0] == 0


def test_fix_model_defaults():
    x = Fix(id="llms_txt", kind=FixKind.LLMS_TXT, path="/llms.txt", filename="llms.txt", description="d",
            content="c", diff="", finding_id="llms_txt")
    assert x.status is FixStatus.PROPOSED and x.approved_sha256 == ""


# --- I6: the baseline is the audit's own pages, never a second fetch
def test_recording_fetcher_remembers_pages_and_only_calls_get():
    from app.fixes.baseline import RecordingFetcher
    inner = FakeFetcher({f"{O}/": page(f"{O}/", "<html>hi</html>")})
    r = RecordingFetcher(inner)
    assert r.get(f"{O}/").body == "<html>hi</html>" and r.get(f"{O}/llms.txt").status == 404
    assert [u for u, _ in inner.calls] == [f"{O}/", f"{O}/llms.txt"] and set(r.pages) == {f"{O}/", f"{O}/llms.txt"}


def test_from_recorder_uses_what_the_audit_saw_and_requests_nothing():
    from app.fixes.baseline import RecordingFetcher
    inner = FakeFetcher({f"{O}/": page(f"{O}/", "home"), f"{O}/llms.txt": page(f"{O}/llms.txt", "", status=0),
                         f"{O}/openapi.json": page(f"{O}/openapi.json", "", status=503)})
    r = RecordingFetcher(inner)
    for path in ("/", "/llms.txt", "/openapi.json"):
        r.get(O + path)
    calls = len(inner.calls)
    b = Baseline.from_recorder(O, r)
    assert len(inner.calls) == calls                           # nothing was requested again
    assert b.body("/") == "home" and b.body("/llms.txt") is None and b.body("/openapi.json") is None
    assert "/llms.txt" not in b.pages and b.pages["/openapi.json"] == (503, "")      # unreachable is absent, not retried
    assert "/swagger.json" not in b.pages and b.available


def test_an_unavailable_baseline_proposes_nothing():
    from app.audit import audit
    from app.fixes.propose import propose_fixes
    b = Baseline.unavailable(O)
    p = propose_fixes(audit(O, FakeFetcher({})), b)
    assert not b.available and p.fixes == [] and {s.reason for s in p.skipped} == {"baseline unavailable"}
