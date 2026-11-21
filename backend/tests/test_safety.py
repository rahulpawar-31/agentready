from app.checks import safety
from app.checks.base import Site
from app.models import Confidence, Status
from tests.conftest import FakeFetcher, page

B = "https://x.test"
GOOD = {"strict-transport-security": "max-age=1", "x-content-type-options": "nosniff",
        "content-security-policy": "default-src 'self'"}


def run(pages):
    return {f.check_id: f for f in safety.run(Site(B, FakeFetcher(pages)))}


def test_exposed_env_fails_and_never_echoes_secrets():
    r = run({f"{B}/.env": page(f"{B}/.env", "DB_PASSWORD=hunter2\n")})["env_not_exposed"]
    assert not r.passed and r.status is Status.DETECTED and r.confidence is Confidence.HIGH
    assert "hunter2" not in r.evidence and "DB_PASSWORD" not in r.evidence
    assert "Exposed path observed" in r.evidence


def test_html_200_on_env_is_not_a_leak():
    assert run({f"{B}/.env": page(f"{B}/.env", "<html>Not found</html>")})["env_not_exposed"].passed


def test_readable_git_head_is_an_unscored_observation_not_a_confirmed_vulnerability():
    r = run({f"{B}/.git/HEAD": page(f"{B}/.git/HEAD", "ref: refs/heads/main\n")})["git_not_exposed"]
    assert r.status is Status.DETECTED and not r.passed
    assert r.weight == 0 and r.confidence is Confidence.MEDIUM
    assert "readable /.git/head detected" in r.evidence.lower() and "not confirmed" in r.evidence.lower()


def test_clean_paths_pass_as_not_detected_medium_and_name_their_scope():
    r = run({})
    for cid in ("env_not_exposed", "git_not_exposed"):
        assert r[cid].passed and r[cid].status is Status.NOT_DETECTED and r[cid].confidence is Confidence.MEDIUM
        assert "other paths" in r[cid].evidence.lower()


def test_security_headers_all_three_required():
    ok = run({f"{B}/": page(f"{B}/", "x", headers=GOOD)})["security_headers"]
    bad = run({f"{B}/": page(f"{B}/", "x", headers={"x-content-type-options": "nosniff"})})["security_headers"]
    assert ok.passed and ok.confidence is Confidence.HIGH
    assert not bad.passed and "content-security-policy" in bad.evidence
