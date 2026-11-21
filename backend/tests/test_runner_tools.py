import json

from app.runner.tools import TOOL_SPECS, ToolBox
from tests.conftest import FakeFetcher, page

O = "https://x.test"
HOME = ('<html><head><title>Acme</title><script>var secret = 1;</script></head><body>'
        '<h1>Acme</h1><p>Plans start at $19 per month.</p>'
        '<a href="/pricing">Pricing</a> <a href="/contact#top">Contact us</a>'
        '<a href="https://evil.test/x">Elsewhere</a> <a href="mailto:a@b.test">Mail</a>'
        '<form action="/send" method="post"><label for="e">Your email</label><input id="e" name="email" required>'
        '<input type="hidden" name="csrf"><textarea name="message" placeholder="Message"></textarea>'
        '<input type="submit"></form></body></html>')


def box(pages, limit=3):
    f = FakeFetcher(pages)
    return ToolBox(f, O, limit), f


def data(result):
    return json.loads(result.text)


def test_fetch_page_returns_text_links_and_forms_but_not_scripts_or_offsite_links():
    b, f = box({f"{O}/": page(f"{O}/", HOME, headers={"content-type": "text/html"})})
    r = b.call("fetch_page", {"url": "/"})
    d = data(r)
    assert not r.is_error and d["title"] == "Acme" and "Plans start at $19" in d["text"]
    assert "secret" not in d["text"]
    assert [l["url"] for l in d["links"]] == [f"{O}/pricing", f"{O}/contact"]
    form = d["forms"][0]
    assert form["method"] == "post" and form["action"] == f"{O}/send"
    assert [(x["name"], x["label"], x["required"]) for x in form["fields"]] == [
        ("email", "Your email", True), ("message", "Message", False)]
    assert "untrusted" in d["note"]
    assert b.steps == 1 and f.calls == [(f"{O}/", False)]


def test_offsite_url_is_refused_without_a_request_or_a_step():
    b, f = box({})
    for url in ["https://evil.test/", "//evil.test/x", "https://user:pw@x.test/", "ftp://x.test/"]:
        r = b.call("fetch_page", {"url": url})
        assert r.is_error and "not on x.test" in r.text
    assert f.calls == [] and b.steps == 0 and b.offsite == 4


def test_step_limit_stops_fetching_but_not_answering():
    b, f = box({}, limit=2)
    b.call("fetch_page", {"url": "/a"})
    b.call("read_llms_txt", {})
    r = b.call("fetch_page", {"url": "/b"})
    assert r.is_error and "submit_answer" in r.text
    assert len(f.calls) == 2 and b.steps == 2
    assert not b.call("submit_answer", {"url": "/a"}).is_error and b.answer is not None


def test_read_llms_txt_present_and_absent():
    b, _ = box({f"{O}/llms.txt": page(f"{O}/llms.txt", "# Acme\n> Plans: https://x.test/pricing")})
    assert "# Acme" in b.call("read_llms_txt", {}).text
    b2, _ = box({})
    assert "No llms.txt" in b2.call("read_llms_txt", {}).text


def test_read_json_ld_returns_blocks_and_tolerates_bad_json():
    html = ('<script type="application/ld+json">{"@type":"Product","name":"Acme","offers":{"price":"19"}}</script>'
            '<script type="Application/LD+JSON">{oops}</script>')
    b, _ = box({f"{O}/p": page(f"{O}/p", html)})
    d = data(b.call("read_json_ld", {"url": "/p"}))
    assert len(d["blocks"]) == 1 and "Product" in d["blocks"][0]
    assert "Acme" in b.seen["x.test/p"]
    none, _ = box({f"{O}/q": page(f"{O}/q", "<p>x</p>")})
    assert "No JSON-LD" in none.call("read_json_ld", {"url": "/q"}).text


def test_blocked_pages_are_counted():
    b, _ = box({f"{O}/a": page(f"{O}/a", "no", status=403),
                f"{O}/b": page(f"{O}/b", status=0)})
    b.call("fetch_page", {"url": "/a"})
    r = b.call("fetch_page", {"url": "/b"})
    assert r.is_error and b.blocked == 1                       # 403 counts; a plain failure (status 0, no "blocked:") does not


def test_guard_blocked_request_counts_as_blocked():
    from app.models import Page
    class Blocking:
        def get(self, url, *, rendered=False, address=None):
            return Page(url, 0, {}, "", error="blocked: request budget of 60 used up")
    b = ToolBox(Blocking(), O, 3)
    b.call("fetch_page", {"url": "/a"})
    assert b.blocked == 1


def test_seen_holds_exactly_what_was_shown_and_page_text_is_clipped():
    big = "<p>" + "word " * 5000 + "TAILMARK</p>"
    b, _ = box({f"{O}/big": page(f"{O}/big", big)})
    d = data(b.call("fetch_page", {"url": "/big"}))
    assert "TAILMARK" not in d["text"] and d["text"].endswith("[truncated]")
    assert b.seen["x.test/big"] == d["text"]


def test_redirected_page_is_recorded_under_both_urls():
    b, _ = box({f"{O}/old": page(f"{O}/new", "<p>Moved here</p>")})
    b.call("fetch_page", {"url": "/old"})
    assert "x.test/old" in b.seen and "x.test/new" in b.seen


def test_submit_answer_coerces_and_validates_arguments():
    b, _ = box({})
    r = b.call("submit_answer", {"url": "/contact", "fields": "email", "gave_up": "nonsense"})
    a = b.answer
    assert not r.is_error and a.url == "/contact" and a.fields == ("email",) and a.gave_up is None
    assert b.call("nope", {}).is_error and b.call("fetch_page", "not a dict").is_error


def test_tool_specs_cover_the_fixed_toolset():
    assert [t["name"] for t in TOOL_SPECS] == ["fetch_page", "read_llms_txt", "read_json_ld", "submit_answer"]
    assert all(t["input_schema"]["type"] == "object" for t in TOOL_SPECS)
