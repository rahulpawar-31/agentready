from app.checks import discoverability
from app.checks.base import Site
from app.models import Confidence, Status
from tests.conftest import FakeFetcher, page

B = "https://x.test"


def run(pages):
    return {f.check_id: f for f in discoverability.run(Site(B, FakeFetcher(pages)))}


def test_blocked_ai_bot_fails_and_names_the_bot():
    robots = "User-agent: GPTBot\nDisallow: /\n"
    r = run({f"{B}/robots.txt": page(f"{B}/robots.txt", robots)})["robots_ai"]
    assert r.passed is False and "gptbot" in r.evidence.lower() and r.status is Status.DETECTED


def test_wildcard_disallow_blocks_ai_bots_too():
    robots = "User-agent: *\nDisallow: /\n"
    assert run({f"{B}/robots.txt": page(f"{B}/robots.txt", robots)})["robots_ai"].passed is False


def test_missing_robots_passes_with_medium_confidence_and_says_so():
    r = run({})["robots_ai"]
    assert r.passed is True and r.status is Status.NOT_DETECTED and r.confidence is Confidence.MEDIUM
    assert "404" in r.evidence


def test_every_finding_has_url_check_evidence_and_confidence():
    for f in run({}).values():
        assert f.url.startswith(B) and f.check and f.evidence and f.confidence


def test_sitemap_found_via_robots_or_file_else_not_detected_medium():
    r1 = run({f"{B}/robots.txt": page(f"{B}/robots.txt", "Sitemap: https://x.test/s.xml")})["sitemap"]
    r2 = run({f"{B}/sitemap.xml": page(f"{B}/sitemap.xml", "<urlset/>")})["sitemap"]
    none = run({})["sitemap"]
    assert r1.passed and r2.passed and not none.passed
    assert none.status is Status.NOT_DETECTED and none.confidence is Confidence.MEDIUM


def test_llms_txt_needs_nonempty_200():
    ok = run({f"{B}/llms.txt": page(f"{B}/llms.txt", "# Site\n> summary")})["llms_txt"]
    empty = run({f"{B}/llms.txt": page(f"{B}/llms.txt", "  ")})["llms_txt"]
    assert ok.passed and ok.status is Status.DETECTED
    assert not empty.passed and empty.status is Status.NOT_DETECTED


def test_json_ld_requires_valid_json_and_not_detected_is_medium_confidence():
    good = '<script type="application/ld+json">{"@type":"Organization"}</script>'
    bad = '<script type="application/ld+json">{oops}</script>'
    assert run({f"{B}/": page(f"{B}/", good)})["json_ld"].passed
    r = run({f"{B}/": page(f"{B}/", bad)})["json_ld"]
    assert not r.passed and r.confidence is Confidence.MEDIUM and "home page" in r.evidence


def test_unreachable_page_is_unreachable_low_confidence_and_not_passed():
    r = run({f"{B}/robots.txt": page(f"{B}/robots.txt", status=0)})["robots_ai"]
    assert r.status is Status.UNREACHABLE and r.confidence is Confidence.LOW and not r.passed
