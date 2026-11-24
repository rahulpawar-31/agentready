"""Group C of the Plan 3 review: the JSON-LD diff is faithful (it applies with a real `patch`), the snippet goes
into the real </head>, and a minified page still gets a reviewable excerpt diff."""
import shutil
import subprocess

import pytest

from app.fixes.baseline import Baseline
from app.fixes.diff import make_diff
from app.fixes.models import FixKind
from app.fixes.propose import insert_before_head_close, propose_fixes
from app.models import Report

O = "https://x.test"
EMPTY = Report(O, 0, {}, [], 0)
SNIP = '<script type="application/ld+json">\n{"@context": "https://schema.org"}\n</script>\n'
PAGE = "<html>\n<head>\n<title>T</title>\n</head>\n<body>x</body>\n</html>\n"

needs_patch = pytest.mark.skipif(shutil.which("patch") is None, reason="needs the patch tool")


def apply_diff(tmp_path, old: str, diff: str) -> bytes:
    (tmp_path / "index.html").write_bytes(old.encode("utf-8"))
    (tmp_path / "d.patch").write_bytes(diff.encode("utf-8"))
    r = subprocess.run(["patch", "-p1", "--binary", "-i", "d.patch"], cwd=tmp_path, capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return (tmp_path / "index.html").read_bytes()


CASES = {
    "lf": PAGE,
    "crlf": PAGE.replace("\n", "\r\n"),
    "bom": "﻿" + PAGE,
    "u2028": PAGE.replace("<title>T", "<title>a b"),
    "u0085": PAGE.replace("<title>T", "<title>a\x85b"),
    "formfeed": PAGE.replace("<title>T", "<title>a\x0cb"),
    "lone_cr": PAGE.replace("<title>T", "<title>a\rb"),
    "no_trailing_newline": PAGE.rstrip("\n"),
    "one_line": "<html><head><title>T</title></head><body>x</body></html>",
    "one_line_no_newline_upper": "<HTML><HEAD><TITLE>T</TITLE></HEAD ><BODY>x</BODY></HTML>",
    "head_close_upper_own_line": "<HEAD>\n<TITLE>T</TITLE>\n</HEAD >\n<BODY>x</BODY>\n",
}


@needs_patch
@pytest.mark.parametrize("name", CASES)
def test_i3_the_diff_applies_with_a_real_patch_and_gives_the_expected_page(tmp_path, name):
    old = CASES[name]
    new = insert_before_head_close(old, SNIP)
    assert new is not None and SNIP in new
    result = apply_diff(tmp_path, old, make_diff("index.html", old, new))
    assert result == new.encode("utf-8")


def test_i3_a_missing_trailing_newline_is_marked_the_standard_way():
    d = make_diff("a.txt", "one\ntwo", "one\ntwo\nthree\n")
    assert "\\ No newline at end of file" in d.splitlines()
    assert make_diff("a.txt", None, "x").splitlines()[-1] == "\\ No newline at end of file"


def test_i3_a_diff_never_splits_on_unicode_line_separators():
    d = make_diff("a.txt", "a b\n", "a b\nc\n")
    assert d.splitlines(True)[-1] != "+c\n" or "a b" in d             # the separator stays inside one line
    assert " a b\n" in d and "+c\n" in d


@pytest.mark.parametrize("html,expected_inside", [
    ("<html><head><!-- old </head> --><title>T</title></head><body></body></html>", True),
    ('<html><head><script>var s="</head>";</script><title>T</title></head><body></body></html>', True),
    ("<html><head><style>/* </head> */</style><title>T</title></head><body></body></html>", True),
    ("<html><head><textarea></head></textarea><title>T</title></head><body></body></html>", True),
    ("<html><head><title>a</head>b</title></head><body></body></html>", True),
])
def test_i3_a_head_close_inside_a_comment_script_style_textarea_or_title_is_ignored(html, expected_inside):
    out = insert_before_head_close(html, SNIP)
    assert out is not None
    real = html.rindex("</head>")                       # in every case here the last </head> is the real one
    assert out == html[:real] + "\n" + SNIP + html[real:]


@pytest.mark.parametrize("html", [
    "<html><head><!-- </head> --><title>T</title><body></body></html>",
    "<html><head><script>x = '</head>'</script><title>T</title><body></body></html>",
    "<html><head><!-- unclosed </head><title>T</title></head>",
    "<html><head><script>never closed </head>",
    "<body>no head at all</body>",
])
def test_i3_no_real_head_close_means_no_insertion(html):
    assert insert_before_head_close(html, SNIP) is None


def test_i3_the_insertion_is_linear_on_a_large_page():
    import time
    html = "<html><head>" + "<!-- c -->" * 20_000 + "<title>T</title></head><body></body></html>"
    started = time.perf_counter()
    assert insert_before_head_close(html, SNIP) is not None
    assert time.perf_counter() - started < 2


def long_home(filler, newline=False):
    sep = "\n" if newline else ""
    return ('<html><head><title>Acme</title><meta name="description" content="Handmade shoes.">' + sep + "</head><body>"
            + sep + filler + "</body></html>")


def jsonld(html):
    p = propose_fixes(EMPTY, Baseline(O, {"/": (200, html)}))
    return next((f for f in p.fixes if f.kind is FixKind.JSON_LD), None), p


def test_i2_a_500kb_minified_page_still_gets_a_reviewable_json_ld_fix():
    html = long_home("<div>word </div>" * 29_000)                       # one line of about 465,000 characters
    assert len(html) > 450_000 and "\n" not in html
    fix, p = jsonld(html)
    assert fix is not None and fix.diff_kind == "excerpt" and 0 < len(fix.diff) < 3000
    assert '+<script type="application/ld+json">' in fix.diff.splitlines()
    assert "index.html (excerpt)" in fix.diff.splitlines()[0]


def test_i2_the_excerpt_shows_only_real_text_from_around_the_insertion_point():
    html = long_home("<div>word </div>" * 29_000)
    fix, _ = jsonld(html)
    old_side = "".join(l[1:] + "\n" for l in fix.diff.splitlines()[2:] if l[:1] in " -" and not l.startswith("@@"))
    new_side = "".join(l[1:] + "\n" for l in fix.diff.splitlines()[2:] if l[:1] in " +" and not l.startswith("@@"))
    flat_old = old_side.replace("\n", "")
    assert flat_old and flat_old in html                                  # nothing is invented on the old side
    assert fix.content.strip().splitlines()[0] in new_side and "</head>" in new_side
    assert len(flat_old) <= 420


def test_i2_a_page_with_a_single_long_line_gets_an_excerpt_and_a_normal_page_a_full_diff():
    longline = long_home("x" * 2500, newline=True)
    assert jsonld(longline)[0].diff_kind == "excerpt"                       # a 2,500 character line is too long to review
    page = "<html>\n<head>\n<title>Acme</title>\n</head>\n<body>x</body>\n</html>\n"
    fix, _ = jsonld(page)
    assert fix.diff_kind == "full" and fix.diff.startswith("--- a/index.html\n")
    fix2, _ = jsonld("<html><head><title>Acme</title>" + "<p>" + "w" * 2500 + "</p></head><body></body></html>")
    assert fix2.diff_kind == "excerpt"


def test_i2_an_excerpt_is_still_scanned_for_secrets_the_fix_would_add():
    fix, _ = jsonld(long_home("<div>word </div>" * 29_000))
    from app.fixes.secrets import find_secrets
    assert find_secrets(fix.added) == [] and fix.added.strip()
