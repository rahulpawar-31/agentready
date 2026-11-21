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


# --- Phase 1 review fixes ---------------------------------------------------
import pytest


def robots(body):
    return run({f"{B}/robots.txt": page(f"{B}/robots.txt", body)})


def test_a_robots_server_error_is_unreachable_not_a_pass():
    r = run({f"{B}/robots.txt": page(f"{B}/robots.txt", status=503)})["robots_ai"]
    assert r.status is Status.UNREACHABLE and not r.passed


def test_a_byte_order_mark_does_not_hide_a_rule():
    assert not robots("﻿User-agent: GPTBot\nDisallow: /\n")["robots_ai"].passed


def test_sitemap_crawl_delay_and_host_lines_do_not_split_a_group():
    body = "User-agent: GPTBot\nSitemap: https://x.test/s.xml\nUser-agent: CCBot\nDisallow: /\n"
    assert "gptbot" in robots(body)["robots_ai"].evidence and "ccbot" in robots(body)["robots_ai"].evidence
    body = "User-agent: GPTBot\nCrawl-delay: 5\nHost: x.test\nUser-agent: ClaudeBot\nDisallow: /\n"
    ev = robots(body)["robots_ai"].evidence
    assert "gptbot" in ev and "claudebot" in ev


@pytest.mark.parametrize("agent,rule", [("anthropic-ai", "/"), ("claude-web", "/"), ("GPTBot", "/*"),
                                        ("*", "/*")])
def test_more_bots_and_the_slash_star_rule_are_recognised(agent, rule):
    r = robots(f"User-agent: {agent}\nDisallow: {rule}\n")["robots_ai"]
    assert not r.passed and r.status is Status.DETECTED


def test_a_clean_robots_file_is_a_medium_confidence_pass_that_names_what_was_looked_for():
    r = robots("User-agent: *\nDisallow: /private\n")["robots_ai"]
    assert r.passed and r.confidence is Confidence.MEDIUM
    assert "no Disallow: / rule found for the listed AI crawlers" in r.evidence and "gptbot" in r.evidence


def test_a_commented_sitemap_line_is_not_a_sitemap():
    assert not robots("# Sitemap: https://x.test/s.xml\nUser-agent: *\n")["sitemap"].passed
    assert robots("  sitemap : https://x.test/s.xml\n")["sitemap"].passed


def test_a_sitemap_file_must_look_like_a_sitemap():
    shell = page(f"{B}/sitemap.xml", "<!doctype html><html><body>app</body></html>")
    assert not run({f"{B}/sitemap.xml": shell})["sitemap"].passed
    for body in ("<?xml version='1.0'?><URLSET xmlns='x'/>", "<sitemapindex/>"):
        assert run({f"{B}/sitemap.xml": page(f"{B}/sitemap.xml", body)})["sitemap"].passed


def test_a_server_error_sitemap_does_not_hide_a_real_robots_sitemap_line():
    r = run({f"{B}/robots.txt": page(f"{B}/robots.txt", "Sitemap: https://x.test/s.xml"),
             f"{B}/sitemap.xml": page(f"{B}/sitemap.xml", status=503)})["sitemap"]
    assert r.passed and r.status is Status.DETECTED


@pytest.mark.parametrize("body,headers", [("<!doctype html><html></html>", {}), ("  \n<html>", {}),
                                          ("# Site", {"content-type": "text/html; charset=utf-8"})])
def test_an_html_catch_all_is_not_llms_txt(body, headers):
    r = run({f"{B}/llms.txt": page(f"{B}/llms.txt", body, headers=headers)})["llms_txt"]
    assert not r.passed and r.status is Status.NOT_DETECTED


def test_a_plain_text_llms_txt_passes_with_a_text_content_type():
    r = run({f"{B}/llms.txt": page(f"{B}/llms.txt", "# Site\n> hi", headers={"content-type": "text/plain"})})
    assert r["llms_txt"].passed


@pytest.mark.parametrize("script", [
    '<script type="Application/LD+JSON ">{"@type":"Organization"}</script>',
    '<script type="application/ld+json">{"@context":"https://schema.org"}</script>',
    '<script type="application/ld+json">[{"@type":"Thing"}]</script>'])
def test_json_ld_is_matched_case_insensitively_and_by_content(script):
    assert run({f"{B}/": page(f"{B}/", script)})["json_ld"].passed


@pytest.mark.parametrize("data", ["{}", "1", "[]", '"text"', '{"name":"x"}', "null"])
def test_json_that_is_not_json_ld_does_not_count(data):
    html = f'<script type="application/ld+json">{data}</script>'
    assert not run({f"{B}/": page(f"{B}/", html)})["json_ld"].passed


def test_deeply_nested_json_ld_does_not_crash_the_audit():
    html = '<script type="application/ld+json">' + "[" * 200000 + "</script>"
    assert not run({f"{B}/": page(f"{B}/", html)})["json_ld"].passed
