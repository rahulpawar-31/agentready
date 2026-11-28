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


# --- Phase 1 review fixes ---------------------------------------------------
import pytest


def raw(path, body):
    return {f"{B}{path}": page(f"{B}{path}", body)}


@pytest.mark.parametrize("body", [
    '{"openapi":"3","components":null}', '{"openapi":"3","components":"x"}',
    '{"openapi":"3","components":{"securitySchemes":["a"]}}',
    '{"openapi":"3","components":{"securitySchemes":{"a":"x"}}}',
    '{"openapi":"3","components":{"securitySchemes":{"a":null,"b":[1]}}}',
    '{"swagger":"2","securityDefinitions":"nope"}', "[" * 200000])
def test_hostile_or_odd_documents_do_not_crash_the_check(body):
    r = {f.check_id: f for f in identity.run(Site(B, FakeFetcher(raw("/openapi.json", body))))}
    assert set(r) == {"openapi_public", "auth_declared"} and not r["auth_declared"].passed


def test_scheme_types_in_evidence_are_clipped_and_cleaned():
    spec = {"openapi": "3", "components": {"securitySchemes": {"k": {"type": "x\n" + "y" * 300}}}}
    r = {f.check_id: f for f in identity.run(Site(B, FakeFetcher(raw("/openapi.json", json.dumps(spec)))))}
    assert r["auth_declared"].passed and "y" * 81 not in r["auth_declared"].evidence
    assert "\n" not in r["auth_declared"].evidence


@pytest.mark.parametrize("status", [0, 503])
def test_if_some_standard_paths_could_not_be_fetched_the_combined_check_is_unreachable(status):
    pages = {f"{B}/swagger.json": page(f"{B}/swagger.json", status=status)}
    r = {f.check_id: f for f in identity.run(Site(B, FakeFetcher(pages)))}["openapi_public"]
    assert not r.passed and r.confidence is Confidence.LOW
    assert r.status is Status.UNREACHABLE and r.url == f"{B}/swagger.json"
    expected = "Could not fetch" if status == 0 else "Server error 503"
    assert expected in r.evidence


def test_when_every_path_answered_absence_keeps_medium_confidence():
    r = {f.check_id: f for f in identity.run(Site(B, FakeFetcher({})))}["openapi_public"]
    assert r.confidence is Confidence.MEDIUM and "could not be fetched" not in r.evidence
