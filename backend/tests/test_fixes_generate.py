import re

import pytest

from app.fixes.generate import build_json_ld, build_llms_txt, json_ld_snippet
from app.fixes.models import NotEnoughContent
from app.fixes.validate import validate_json_ld_snippet, validate_llms_txt

O = "https://x.test"
HOME = ('<html><head><title>Acme  Shoes</title><meta name="description" content="Handmade shoes, shipped worldwide.">'
        '</head><body><nav><a href="/shop">Shop</a> <a href="/about">About us</a>'
        ' <a href="https://other.test/x">Elsewhere</a> <a href="mailto:a@b.c">Mail</a>'
        ' <a href="/shop#top">Shop again</a> <a href="/shop?session=abc123">Shop q</a>'
        ' <a href="http://[bad">Bad</a> <a href="/empty"><img src="x.png"></a></nav></body></html>')


def link_targets(text):
    return re.findall(r"\]\(([^)]*)\)", text)


def test_llms_txt_has_title_summary_and_only_on_site_links():
    t = build_llms_txt(O, HOME)
    assert t.startswith("# Acme Shoes\n") and "> Handmade shoes, shipped worldwide." in t
    assert "- [Shop](https://x.test/shop)" in t and "- [About us](https://x.test/about)" in t
    assert "other.test" not in t and "mailto" not in t and "abc123" not in t and "#top" not in t
    assert t.count("https://x.test/shop)") == 1 and "/empty" not in t
    assert t.endswith("\n") and not t.endswith("\n\n")
    assert validate_llms_txt(t, O) == []


def test_llms_txt_caps_links_and_clips_fields():
    links = "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(100))
    t = build_llms_txt(O, f"<title>{'T' * 500}</title>{links}")
    assert sum(1 for l in t.splitlines() if l.startswith("- [")) == 10
    assert len(t.splitlines()[0]) <= 202
    assert validate_llms_txt(t, O) == []


def test_site_text_cannot_inject_headings_or_links():
    evil = "<title>Evil\n# Injected [x](https://evil.test)</title><meta name='description' content='ok\n## Fake'>"
    t = build_llms_txt(O, evil + '<a href="/a">A](https://evil.test/z)</a>')
    first = t.splitlines()[0]
    assert [l for l in t.splitlines() if l.startswith("# ")] == [first]
    assert [l for l in t.splitlines() if l.startswith("## ")] == ["## Pages"]   # the injected "## Fake" stays inside the quote line
    assert "](https://evil.test" not in t
    assert link_targets(t) == ["https://x.test/a"]                       # the only link target is on the verified host
    assert validate_llms_txt(t, O) == []


def test_link_urls_with_spaces_or_parentheses_stay_on_one_valid_line():
    t = build_llms_txt(O, '<title>T</title><a href="/a b(c)">Odd</a>')
    assert len(link_targets(t)) == 1 and " " not in link_targets(t)[0] and ")" not in link_targets(t)[0]
    assert validate_llms_txt(t, O) == []


def test_missing_title_or_content_is_refused():
    with pytest.raises(NotEnoughContent):
        build_llms_txt(O, "<html><body><a href='/a'>A</a></body></html>")
    with pytest.raises(NotEnoughContent):
        build_llms_txt(O, "<title>Only a title</title>")
    with pytest.raises(NotEnoughContent):
        build_json_ld(O, "<html><body>no title</body></html>")


@pytest.mark.parametrize("text", [
    "no heading\n", "# A\n# B\n", "# A\n\n- [x](https://evil.test/p)\n", "# A\n\nfree text line\n",
    "# A\n" + "- [x](https://x.test/p)\n" * 2000, "# A\x00\n", "# A\n\n> see [x](https://evil.test/p)\n",
    "# A\n\n### deep heading\n",
])
def test_llms_validator_rejects_bad_files(text):
    assert validate_llms_txt(text, O) != []


def test_json_ld_uses_only_what_the_page_says():
    assert build_json_ld(O, HOME) == {"@context": "https://schema.org", "@type": "WebSite", "name": "Acme Shoes",
                                      "url": O, "description": "Handmade shoes, shipped worldwide."}
    assert "description" not in build_json_ld(O, "<title>T</title>")


def test_snippet_cannot_be_broken_out_of():
    home = HOME.replace("Acme  Shoes", "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;")
    data = build_json_ld(O, home)
    assert data["name"] == "</script><script>alert(1)</script>"            # the data keeps the text; the snippet escapes it
    s = json_ld_snippet(data)
    assert s.count("</script>") == 1 and "<script>alert" not in s and validate_json_ld_snippet(s) == []
    direct = json_ld_snippet({"@context": "https://schema.org", "@type": "WebSite", "name": "a</script>&b "})
    assert direct.count("</script>") == 1 and "&b" not in direct and " " not in direct
    assert validate_json_ld_snippet(direct) == []


GOOD = '<script type="application/ld+json">{"@context": "https://schema.org", "@type": "WebSite"}</script>'


def test_a_good_snippet_validates():
    assert validate_json_ld_snippet(GOOD) == []


@pytest.mark.parametrize("snippet", [
    "", "<script>{}</script>", '<script type="application/ld+json">{oops}</script>',
    '<script type="application/ld+json">{"@type": "WebSite"}</script>',
    '<script type="application/ld+json">{"@context": "http://evil.test", "@type": "WebSite"}</script>',
    GOOD + "<script>x()</script>",
    GOOD + "<p>extra</p>",
    '<script type="application/ld+json">[1, 2]</script>',
])
def test_json_ld_validator_rejects_bad_snippets(snippet):
    assert validate_json_ld_snippet(snippet) != []
