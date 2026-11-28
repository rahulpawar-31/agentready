"""Regressions from the codebase review; HTTP is synthetic, including the pinned-address transport."""
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.checks import identity
from app.checks.base import Site
from app.fetch import CachedFetcher, GuardedFetcher, HttpxFetcher
from app.fixes.baseline import Baseline, RecordingFetcher, capture_baseline
from app.fixes.models import Fix, FixKind
from app.fixes.propose import propose_fixes
from app.fixes.queue import FixQueue, NotApproved
from app.models import Page, Status
from app.runner.tools import ToolBox
from app.scoring import is_scored, score
from tests.conftest import FakeFetcher, public_resolver

ORIGIN = "https://x.test"
SECRET = "synthetic-review-secret"
HOME = ('<html><head><title>Acme Widgets</title>\n'
        '<meta name="description" content="We build useful widgets.">\n'
        '<!-- DB_PASSWORD=' + SECRET + ' -->\n'
        '</head><body><p>' + 'Useful widgets ' * 30 + '</p></body></html>')


@pytest.mark.parametrize("name,args", [("fetch_page", {"url": "/help"}), ("read_llms_txt", {}),
                                       ("read_json_ld", {"url": "/help"})])
@pytest.mark.parametrize("sensitive", ["/.env", "/.git/HEAD", "/%252egit/config"])
def test_runner_blocks_sensitive_redirect_hops_before_requesting_them(tmp_path, name, args, sensitive):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path in ("/help", "/llms.txt"):
            return httpx.Response(302, headers={"location": sensitive})
        # Redirecting back to a public URL must not make the intervening sensitive hop acceptable.
        return httpx.Response(302, headers={"location": "/public"}, text=SECRET)

    plain = HttpxFetcher(httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False))
    guarded = GuardedFetcher(plain, "x.test", resolver=public_resolver, min_interval=0)
    box = ToolBox(CachedFetcher(guarded, tmp_path), ORIGIN, 10)
    result = box.call(name, args)

    assert result.is_error and SECRET not in result.text
    assert len(requests) == guarded.requests_used == 1
    assert box.blocked == 1 and box.network_errors == 0
    assert not box.seen and not box.visited
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("name,args,target", [("fetch_page", {"url": "/help"}, "/help"),
                                             ("read_llms_txt", {}, "/llms.txt"),
                                             ("read_json_ld", {"url": "/help"}, "/help")])
def test_runner_rejects_sensitive_final_responses_from_injected_fetchers(name, args, target):
    body = '<script type="application/ld+json">' + json.dumps({"token": SECRET}) + '</script>'
    fake = FakeFetcher({ORIGIN + target: Page(ORIGIN + "/.env", 200, {}, body + SECRET)})
    box = ToolBox(fake, ORIGIN, 10)
    result = box.call(name, args)
    assert result.is_error and SECRET not in result.text
    assert not box.seen and not box.visited and box.blocked == 1


def test_runner_does_not_expose_sensitive_final_url_from_an_existing_cache_entry(tmp_path):
    plain = FakeFetcher({})
    guarded = GuardedFetcher(plain, "x.test", resolver=public_resolver, min_interval=0)
    cached = CachedFetcher(guarded, tmp_path)
    cached._path(ORIGIN + "/help", False).write_text(json.dumps(asdict(
        Page(ORIGIN + "/.env", 200, {}, "DB_PASSWORD=" + SECRET))))
    box = ToolBox(cached, ORIGIN, 10)
    result = box.call("fetch_page", {"url": "/help"})
    assert result.is_error and SECRET not in result.text and not box.seen
    assert plain.calls == [] and guarded.requests_used == 0


@pytest.mark.parametrize("path", ["/", "/llms.txt", *identity.SPEC_PATHS])
def test_both_baseline_capture_paths_discard_sensitive_final_responses(path):
    secret_page = Page(ORIGIN + "/.env", 200, {}, HOME)
    fake = FakeFetcher({ORIGIN + path: secret_page})
    baseline = capture_baseline(ORIGIN, fake)
    assert baseline.body(path) is None and SECRET not in json.dumps(baseline.pages)
    # Also defend against an existing or independently built recorder.
    recorder = SimpleNamespace(pages={ORIGIN + path: secret_page})
    assert Baseline.from_recorder(ORIGIN, recorder).body(path) is None


def test_recorder_returns_security_probe_to_checks_without_retaining_its_body():
    fake = FakeFetcher({ORIGIN + "/": Page(ORIGIN + "/.env", 200, {}, HOME),
                        ORIGIN + "/.env": Page(ORIGIN + "/.env", 200, {}, HOME)})
    recorder = RecordingFetcher(fake)
    assert SECRET in recorder.get(ORIGIN + "/").body
    assert SECRET in recorder.get(ORIGIN + "/.env").body
    assert not recorder.pages


def test_api_root_redirect_to_env_cannot_create_a_sensitive_fix_diff(monkeypatch, tmp_path):
    def respond(request):
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/.env"})
        if request.url.path == "/.env":
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
        return httpx.Response(404)

    monkeypatch.setattr(main, "new_http_client", lambda: httpx.Client(
        transport=httpx.MockTransport(respond), follow_redirects=False))
    monkeypatch.setattr(main, "GUARD_OPTIONS", {"min_interval": 0})
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path)
    token = main.make_token("x.test", main.SECRET)
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: lambda host: ["agentready-verify=" + token]
    client = TestClient(main.app)
    audited = client.post("/audit", json={"url": ORIGIN})
    assert audited.status_code == 200 and SECRET not in audited.text
    rid = audited.json()["id"]
    assert main._baselines[rid].body("/") is None
    response = client.post(f"/report/{rid}/fixes")
    assert response.status_code == 200 and SECRET not in response.text
    assert not response.json()["fixes"]
    assert not any(SECRET in path.read_text() for path in tmp_path.rglob("*.json"))


def test_proposals_never_present_secret_values_in_existing_diff_context():
    from app.audit import audit
    fake = FakeFetcher({ORIGIN + "/": Page(ORIGIN + "/", 200, {}, HOME)})
    proposal = propose_fixes(audit(ORIGIN, fake), capture_baseline(ORIGIN, fake))
    assert FixKind.JSON_LD not in {fix.kind for fix in proposal.fixes}
    assert SECRET not in json.dumps(asdict(proposal), default=str)


@pytest.mark.parametrize("prefix", [" ", "-", "+"])
def test_export_rechecks_entire_diff_including_removed_and_context_lines(prefix):
    fix = Fix("llms_txt", FixKind.LLMS_TXT, "/llms.txt", "llms.txt", "Add summary", "# Acme\n> Widgets\n",
              "--- a/llms.txt\n+++ b/llms.txt\n@@ -1 +1 @@\n+# Acme\n", "llms_txt")
    queue = FixQueue([fix], {FixKind.LLMS_TXT: lambda content: []})
    queue.approve(fix.id)
    fix.diff += prefix + "DB_PASSWORD=" + SECRET + "\n"
    fix.added = "# Acme"  # A stale generated-lines cache must not bypass export validation.
    with pytest.raises(NotApproved, match="secret-like"):
        queue.export(fix.id)


@pytest.mark.parametrize("failed_path", identity.SPEC_PATHS)
@pytest.mark.parametrize("status", [0, 503])
def test_incomplete_openapi_observation_is_unscored_regardless_of_failed_path(failed_path, status):
    fake = FakeFetcher({ORIGIN + failed_path: Page(ORIGIN + failed_path, status, {}, "")})
    findings = identity.run(Site(ORIGIN, fake))
    assert all(f.status is Status.UNREACHABLE and f.url == ORIGIN + failed_path for f in findings)
    assert not is_scored(findings)
    independent_pass = replace(findings[0], status=Status.DETECTED, passed=True, weight=1)
    assert score([*findings, independent_pass]) == 100


def test_valid_openapi_document_still_passes_when_an_alternative_path_is_unreachable():
    spec = {"openapi": "3.0.0", "components": {"securitySchemes": {"key": {"type": "apiKey"}}}}
    fake = FakeFetcher({ORIGIN + "/openapi.json": Page(ORIGIN + "/openapi.json", 503, {}, ""),
                        ORIGIN + "/swagger.json": Page(ORIGIN + "/swagger.json", 200, {}, json.dumps(spec))})
    findings = identity.run(Site(ORIGIN, fake))
    assert all(f.passed and f.url == ORIGIN + "/swagger.json" for f in findings)
