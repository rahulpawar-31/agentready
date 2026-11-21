import json
from app.checks import identity
from app.checks.base import Site
from app.models import Confidence, Status
from tests.conftest import FakeFetcher, page

B = "https://x.test"


def run(path=None, spec=None):
    pages = {}
    if path:
        pages[f"{B}{path}"] = page(f"{B}{path}", json.dumps(spec))
    return {f.check_id: f for f in identity.run(Site(B, FakeFetcher(pages)))}


def test_no_spec_is_not_detected_not_absent():
    r = run()
    assert not r["openapi_public"].passed and r["openapi_public"].status is Status.NOT_DETECTED
    assert r["openapi_public"].confidence is Confidence.MEDIUM and "elsewhere" in r["openapi_public"].evidence
    assert not r["auth_declared"].passed and r["auth_declared"].confidence is Confidence.LOW


def test_spec_without_security_passes_public_fails_auth():
    r = run("/openapi.json", {"openapi": "3.0.0", "paths": {}})
    assert r["openapi_public"].passed and r["openapi_public"].url == f"{B}/openapi.json"
    assert not r["auth_declared"].passed


def test_spec_with_security_scheme_passes_both():
    spec = {"openapi": "3.0.0", "components": {"securitySchemes": {"k": {"type": "apiKey"}}}}
    r = run("/.well-known/openapi.json", spec)
    assert r["openapi_public"].passed and r["auth_declared"].passed and "apiKey" in r["auth_declared"].evidence


def test_non_openapi_json_is_ignored():
    assert not run("/openapi.json", {"hello": "world"})["openapi_public"].passed
