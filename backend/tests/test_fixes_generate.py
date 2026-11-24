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


# --- Group E of the Plan 3 review: invisible characters, Markdown, bare links, title choice, on-site links
def _page(title="Acme", description="", body=""):
    meta = f'<meta name="description" content="{description}">' if description else ""
    return f"<html><head><title>{title}</title>{meta}</head><body>{body}</body></html>"


def test_e_bidi_zero_width_and_bom_characters_are_removed_from_both_outputs():
    nasty = "abc‮evil⁦x​‍﻿z"
    page = _page(title=nasty, description="d" + nasty, body=f'<a href="/a">link{nasty}</a>')
    txt = build_llms_txt(O, page)
    assert not any(ch in txt for ch in "‮⁦​‍﻿") and txt.startswith("# abcevilxz\n")
    ld = json_ld_snippet(build_json_ld(O, page))
    assert "\\u202e" not in ld and "‮" not in ld and "abcevilxz" in ld
    assert validate_llms_txt(txt, O) == []


def test_e_unusual_spaces_become_ordinary_spaces():
    txt = build_llms_txt(O, _page(title="a b　c d", description="x"))
    assert txt.startswith("# a b c d\n")


def test_e_bare_urls_and_emails_in_free_text_are_removed_so_markdown_cannot_auto_link_them():
    page = _page(title="Visit www.evil.test or https://evil.test/p now", description="Mail help@evil.test or see http://evil.test/x",
                 body='<a href="/a">![x](https://evil.test/track.png)</a><a href="/b">go www.evil.test</a>')
    txt = build_llms_txt(O, page)
    assert "evil.test" not in txt and "@" not in txt and "http://" not in txt.replace("https://x.test", "")
    assert txt.startswith("# Visit or now\n")
    assert validate_llms_txt(txt, O) == []


def test_e_markdown_emphasis_backticks_and_bangs_are_removed_from_free_text():
    page = _page(title="*bold* `code` _under_ ~~strike~~ Wow!", description="*a* _b_ `c` ~d~ !e")
    lines = build_llms_txt(O, page).splitlines()
    for line in (lines[0], lines[2]):
        assert not any(ch in line[2:] for ch in "*`_~!"), line


def test_e_the_title_comes_from_the_head_not_from_an_inline_svg():
    page = '<html><head><title>Real name</title></head><body><svg><title>Icon label</title></svg><a href="/a">A</a></body></html>'
    assert build_llms_txt(O, page).startswith("# Real name\n")
    assert build_json_ld(O, page)["name"] == "Real name"
    svg_first = '<body><svg><title>Icon label</title></svg><h1>x</h1><title>Page title</title><a href="/a">A</a></body>'
    assert build_json_ld(O, svg_first)["name"] == "Page title"
    with pytest.raises(NotEnoughContent):
        build_json_ld(O, '<body><svg><title>Icon label</title></svg></body>')


@pytest.mark.parametrize("origin,href,expected", [
    ("https://x.test", "http://x.test/a", None),                        # an http link under an https site is not rewritten
    ("https://x.test", "/a", "https://x.test/a"),
    ("https://x.test:443", "/a", "https://x.test:443/a"),               # an explicit default port is the same site
    ("https://x.test:443", "https://x.test/a", "https://x.test:443/a"),
    ("https://x.test", "https://x.test:443/a", "https://x.test/a"),
    ("https://x.test", "https://x.test:8443/a", None),
    ("http://x.test:80", "/a", "http://x.test:80/a"),
    ("http://x.test", "http://x.test:80/a", "http://x.test/a"),
    ("https://x.test", "https://user:pw@x.test/a", None),
    ("https://x.test", "https://x.test.evil.test/a", None),
    ("https://x.test", "//evil.test/a", None),
    ("https://x.test", "https://X.TEST/a?s=1#f", "https://x.test/a"),
])
def test_e_links_must_be_the_same_scheme_host_and_port_as_the_verified_origin(origin, href, expected):
    from app.fixes.generate import _site_link
    assert _site_link(origin, href) == expected


def test_e_a_site_with_an_explicit_default_port_keeps_its_links_in_llms_txt():
    page = _page(description="d", body='<a href="/shop">Shop</a><a href="https://x.test/about">About</a>')
    txt = build_llms_txt("https://x.test:443", page)
    assert "- [Shop](https://x.test:443/shop)" in txt and "- [About](https://x.test:443/about)" in txt
    assert validate_llms_txt(txt, "https://x.test:443") == []


def test_e_the_validator_rejects_a_link_with_another_scheme():
    assert validate_llms_txt("# A\n\n## Pages\n- [x](http://x.test/p)\n", O) != []
