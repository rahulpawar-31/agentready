from app.runner.judge import Answer, judge
from app.runner.labels import TaskLabel

ORIGIN = "https://x.test"
MAIN = TaskLabel(urls=("x.test/pricing",))
FORM = TaskLabel(urls=("x.test/contact",), required_fields=(("email",), ("message", "your message")))
DOCS = TaskLabel(urls=("x.test/docs",))
NODOCS = TaskLabel(none_exists=True)
SEEN = {"x.test/pricing": "Plans start at $19 per month. Cancel any time."}


VISITED = frozenset({"https://x.test/pricing", "https://x.test/contact", "https://x.test/docs"})


def j(task, answer, label, seen=None, visited=None, looked=True):
    return judge(task, answer, label, ORIGIN, seen if seen is not None else SEEN,
                 visited=VISITED if visited is None else visited, looked=looked)


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


def test_main_info_requires_the_answer_page_to_have_been_fetched():
    assert j("main_info", Answer(url="/pricing", quote="Plans start at $19 per month"), MAIN,
             visited=frozenset()).reason == "not_visited"


def test_answer_scheme_must_be_http_or_https_and_match_a_fetched_url():
    assert j("main_info", Answer(url="ftp://x.test/pricing", quote="Plans start at $19 per month"), MAIN).reason == "wrong_page"
    assert j("main_info", Answer(url="http://x.test/pricing", quote="Plans start at $19 per month"), MAIN).reason == "not_visited"


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
    assert j("docs_api", Answer(gave_up="not_found"), DOCS).reason == "no_answer"


def test_answer_without_url_is_no_answer():
    assert j("contact_form", Answer(fields=("email",)), FORM).reason == "no_answer"


# --- review fixes ------------------------------------------------------------------
def test_c1_docs_and_contact_guesses_without_visiting_the_page_fail():
    assert j("docs_api", Answer(url="/docs"), DOCS, visited=frozenset()).reason == "not_visited"
    assert j("contact_form", Answer(url="/contact", fields=("email", "message")), FORM,
             visited=frozenset()).reason == "not_visited"
    assert j("docs_api", Answer(url="/docs"), DOCS, visited=frozenset({"https://x.test/docs"})).success


def test_c1_none_exists_needs_at_least_one_look():
    assert j("docs_api", Answer(none_exists=True), NODOCS, visited=frozenset(), looked=False).reason == "not_investigated"
    assert j("docs_api", Answer(none_exists=True), NODOCS, looked=True).success


def test_c2_quote_pattern_must_match_when_the_label_sets_one():
    lab = TaskLabel(urls=("x.test/pricing",), quote_pattern=r"\$\d+")
    assert j("main_info", Answer(url="/pricing", quote="Plans start at $19 per month"), lab).success
    boiler = {"x.test/pricing": "Copyright 2020 Acme. Plans from $19"}
    v = j("main_info", Answer(url="/pricing", quote="Copyright 2020 Acme"), lab, seen=boiler)
    assert (v.success, v.reason) == (False, "quote_pattern_mismatch")


def test_c3_seen_text_is_a_list_of_everything_shown_and_the_quote_may_be_in_any():
    seen = {"x.test/pricing": ["Plans start at $19 per month", '{"name": "Acme"}']}
    assert j("main_info", Answer(url="/pricing", quote="Plans start at $19 per month"), MAIN, seen=seen).success
    assert j("main_info", Answer(url="/pricing", quote='{"name": "Acme"}'), MAIN, seen=seen).success


def test_c5_m1_unparseable_urls_are_wrong_page_not_a_crash_and_spaces_are_stripped():
    for bad in ("http://[bad", "https://x.test:99999999/a", "http://[::1"):
        assert j("docs_api", Answer(url=bad), DOCS).reason in ("wrong_page", "off_host")
    assert j("docs_api", Answer(url="  /docs\n"), DOCS).success


def test_i7_not_found_counts_as_a_pass_when_the_label_says_none_exists():
    assert j("docs_api", Answer(gave_up="not_found"), NODOCS).success
    assert j("docs_api", Answer(gave_up="not_found"), NODOCS, visited=frozenset(), looked=False).reason == "not_investigated"
    assert j("docs_api", Answer(gave_up="not_found"), DOCS).reason == "no_answer"
    assert j("main_info", Answer(gave_up="not_found"), MAIN).reason == "no_answer"


def test_m3_explicit_default_port_is_the_same_host():
    assert j("docs_api", Answer(url="https://x.test:443/docs"), DOCS).success
    assert j("docs_api", Answer(url="https://x.test:8443/docs"), DOCS).reason == "off_host"
