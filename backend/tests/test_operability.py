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
