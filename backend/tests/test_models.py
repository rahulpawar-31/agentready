from app.models import Category, Confidence, Finding, Page, Status


def test_finding_carries_check_evidence_url_confidence_and_status():
    f = Finding("llms_txt", Category.DISCOVERABILITY, True, 2, "GET /llms.txt, expect 200 with text",
                "found 12 bytes", "https://x.test/llms.txt", Confidence.HIGH, Status.DETECTED)
    assert f.category.value == "discoverability"
    assert f.confidence.value == "high" and f.status.value == "detected"
    assert f.check.startswith("GET") and f.evidence and f.url


def test_not_detected_wording_is_never_absent():
    assert Status.NOT_DETECTED.value == "not detected"


def test_page_is_immutable_and_error_defaults_empty():
    p = Page("https://x.test/", 200, {}, "hi")
    assert p.error == ""
    try:
        p.status = 500
        assert False, "expected FrozenInstanceError"
    except Exception as e:
        assert e.__class__.__name__ == "FrozenInstanceError"
