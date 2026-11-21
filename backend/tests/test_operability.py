from app.checks import operability
from app.checks.base import Site
from app.models import Confidence, Status
from tests.conftest import FakeFetcher, page

B = "https://x.test"
LONG = "<p>" + "word " * 80 + "</p>"


def run(body, status=200):
    f = FakeFetcher({f"{B}/": page(f"{B}/", body, status)})
    return {x.check_id: x for x in operability.run(Site(B, f))}


def test_static_text_is_detected_but_js_dependence_is_not_ruled_out():
    r = run(LONG)["no_js_content"]
    assert r.passed and r.status is Status.DETECTED
    assert "not assessed" in r.evidence


def test_js_shell_is_not_detected_and_not_called_js_dependent():
    shell = '<div id="root"></div><script>' + "x" * 500 + "</script>"
    r = run(shell)["no_js_content"]
    assert not r.passed and r.status is Status.NOT_DETECTED and r.confidence is Confidence.MEDIUM
    assert "may" in r.evidence


def test_unlabelled_input_fails_and_names_it():
    r = run(LONG + '<form><input name="email" type="text"></form>')["labelled_forms"]
    assert not r.passed and "email" in r.evidence and r.status is Status.DETECTED


def test_label_for_aria_and_wrapping_label_all_pass():
    html = LONG + (
        '<form><label for="a">A</label><input id="a" name="a">'
        '<input name="b" aria-label="B">'
        '<label>C <input name="c"></label>'
        '<input type="hidden" name="h"><input type="submit"></form>'
    )
    r = run(html)["labelled_forms"]
    assert r.passed and r.status is Status.NOT_DETECTED and r.confidence is Confidence.MEDIUM


def test_captcha_marker_is_detected_high_confidence():
    r = run(LONG + '<div class="g-recaptcha"></div>')["no_captcha"]
    assert not r.passed and r.status is Status.DETECTED and r.confidence is Confidence.HIGH


def test_no_captcha_marker_is_only_not_detected_with_low_confidence():
    r = run(LONG)["no_captcha"]
    assert r.passed and r.status is Status.NOT_DETECTED and r.confidence is Confidence.LOW
    assert "no captcha" not in r.evidence.lower() and "not detected" in r.evidence.lower()


def test_login_wall_by_status_or_password_field_else_not_detected():
    assert not run(LONG, status=401)["no_login_wall"].passed
    assert not run(LONG + '<input type="password" name="p">')["no_login_wall"].passed
    r = run(LONG)["no_login_wall"]
    assert r.passed and r.status is Status.NOT_DETECTED and "elsewhere" in r.evidence


# --- Phase 1 review fixes ---------------------------------------------------
import pytest


def test_a_server_error_home_page_is_not_scored_as_the_real_page():
    for r in run(LONG, status=503).values():
        assert r.status is Status.UNREACHABLE and not r.passed


@pytest.mark.parametrize("tag", ['<input type="PASSWORD" name="p">', '<input type=" password " name="p">',
                                 '<INPUT TYPE="Password">'])
def test_password_fields_are_found_whatever_the_case(tag):
    r = run(LONG + tag)["no_login_wall"]
    assert not r.passed and r.status is Status.DETECTED


def test_input_types_are_compared_case_insensitively_and_trimmed():
    html = LONG + '<form><input type=" HIDDEN " name="h"><input type="Submit"><input type="text" name="t"></form>'
    r = run(html)["labelled_forms"]
    assert not r.passed and "t" in r.evidence and "h" not in r.evidence.split(": ")[1].split(", ")


def test_a_whitespace_only_aria_label_is_not_a_label():
    r = run(LONG + '<form><input name="a" aria-label="   "></form>')["labelled_forms"]
    assert not r.passed and "a" in r.evidence


@pytest.mark.parametrize("snippet", ["<!-- g-recaptcha -->", "<p>we do not use h-captcha or cf-turnstile</p>",
                                     "<script>var g-recaptcha = 1</script>"])
def test_captcha_words_in_comments_prose_or_script_bodies_are_not_markers(snippet):
    r = run(LONG + snippet)["no_captcha"]
    assert r.passed and r.status is Status.NOT_DETECTED


@pytest.mark.parametrize("snippet", ['<div class="G-Recaptcha"></div>', '<div id="h-captcha"></div>',
                                     '<script src="https://www.google.com/recaptcha/api.js"></script>',
                                     '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
                                     '<div data-x="cf-turnstile"></div>'])
def test_captcha_markers_in_attributes_are_found(snippet):
    r = run(LONG + snippet)["no_captcha"]
    assert not r.passed and r.status is Status.DETECTED and r.confidence is Confidence.HIGH


def test_site_controlled_field_names_are_clipped_and_cleaned_in_evidence():
    r = run(LONG + '<form><input name="' + "a" * 200 + '\nb"></form>')["labelled_forms"]
    assert "a" * 81 not in r.evidence and "\n" not in r.evidence
