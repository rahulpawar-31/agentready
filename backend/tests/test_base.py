from app.checks.base import clip, finding
from app.models import Category, Confidence, Status
from tests.conftest import page


def test_clip_limits_length_and_replaces_control_characters():
    assert len(clip("a" * 200)) == 80
    assert clip("a\nb\x00c\td") == "a b c d"
    assert clip("fine") == "fine"


def test_a_server_error_is_unreachable_with_low_confidence_and_says_so():
    f = finding("x", Category.SAFETY, page("https://x.test/", status=503), passed=True, weight=3,
                check="c", evidence="should not be used", status=Status.DETECTED, confidence=Confidence.HIGH)
    assert f.status is Status.UNREACHABLE and f.confidence is Confidence.LOW and not f.passed
    assert f.evidence == "Server error 503"


def test_404_is_still_a_real_observation():
    f = finding("x", Category.SAFETY, page("https://x.test/", status=404), passed=True, weight=3,
                check="c", evidence="e", status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM)
    assert f.status is Status.NOT_DETECTED and f.passed
