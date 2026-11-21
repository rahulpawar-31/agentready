from app.runner.judge import Answer, judge
from app.runner.labels import TaskLabel

ORIGIN = "https://x.test"
MAIN = TaskLabel(urls=("x.test/pricing",))
FORM = TaskLabel(urls=("x.test/contact",), required_fields=(("email",), ("message", "your message")))
DOCS = TaskLabel(urls=("x.test/docs",))
NODOCS = TaskLabel(none_exists=True)
SEEN = {"x.test/pricing": "Plans start at $19 per month. Cancel any time."}


def j(task, answer, label, seen=None):
    return judge(task, answer, label, ORIGIN, seen if seen is not None else SEEN)


def test_main_info_success_with_quote_on_the_page():
    v = j("main_info", Answer(url="https://x.test/pricing/", quote="plans start at  $19 per month"), MAIN)
    assert (v.success, v.reason) == (True, "ok")


def test_main_info_failures():
    assert j("main_info", None, MAIN).reason == "no_answer"
    assert j("main_info", Answer(url="https://x.test/about", quote="Plans start at $19"), MAIN).reason == "wrong_page"
    assert j("main_info", Answer(url="https://evil.test/pricing", quote="Plans start at $19"), MAIN).reason == "off_host"
    assert j("main_info", Answer(url="/pricing"), MAIN).reason == "no_quote"
    assert j("main_info", Answer(url="/pricing", quote="short"), MAIN).reason == "no_quote"
    assert j("main_info", Answer(url="/pricing", quote="Free forever for everyone"), MAIN).reason == "quote_not_on_page"
    assert j("main_info", Answer(url="/pricing", quote="Plans start at $19"), MAIN, seen={}).reason == "quote_not_on_page"


def test_contact_form_needs_every_required_field_in_any_accepted_name():
    ok = Answer(url="/contact", fields=("E-mail", "Your Message"))
    assert j("contact_form", Answer(url="/contact", fields=("Email", "your message")), FORM).success
    assert j("contact_form", Answer(url="/contact", fields=("email", "message", "phone")), FORM).success
    assert j("contact_form", Answer(url="/contact", fields=("email",)), FORM).reason == "missing_fields"
    assert j("contact_form", Answer(url="/home", fields=("email", "message")), FORM).reason == "wrong_page"
    assert j("contact_form", ok, FORM).success                        # "E-mail" matches "email" once punctuation is ignored


def test_field_names_ignore_case_and_punctuation():
    assert j("contact_form", Answer(url="/contact", fields=("E-Mail", "Message!")), FORM).success


def test_docs_api_url_or_none_claim():
    assert j("docs_api", Answer(url="/docs"), DOCS).success
    assert j("docs_api", Answer(url="/other"), DOCS).reason == "wrong_page"
    assert j("docs_api", Answer(none_exists=True), DOCS).reason == "wrong_none_claim"
    assert j("docs_api", Answer(none_exists=True), NODOCS).success
    assert j("docs_api", Answer(url="/docs"), NODOCS).reason == "wrong_page"
    assert j("docs_api", Answer(none_exists=True, url="/docs"), NODOCS).reason == "wrong_none_claim"


def test_none_exists_is_not_a_valid_answer_for_other_tasks():
    assert j("main_info", Answer(none_exists=True), MAIN).reason == "wrong_none_claim"


def test_giving_up():
    assert j("docs_api", Answer(gave_up="blocked"), DOCS).reason == "blocked"
    assert j("docs_api", Answer(gave_up="not_found"), DOCS).reason == "no_answer"
    assert j("docs_api", Answer(gave_up="not_found"), NODOCS).reason == "no_answer"   # an honest "not found" is not "none exists"


def test_answer_without_url_is_no_answer():
    assert j("contact_form", Answer(fields=("email",)), FORM).reason == "no_answer"
