"""Static rules for backend/app/ui (Plan 5): no way to turn site text into markup, no inline script or style, a closed
set of tokens, and the wording the PRD requires. These are scans of the source; the behaviour is tested in tests/ui."""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
FILES = sorted(p for p in UI.iterdir() if p.is_file())
JS = [p for p in FILES if p.suffix == ".js"]

BANNED_JS = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"",
             "setTimeout('", "setTimeout(`", "setInterval(\"", "setInterval('", "setInterval(`", "srcdoc", "javascript:",
             ".write(", "postMessage", "localStorage", "indexedDB", "XMLHttpRequest", "WebSocket", "EventSource",
             "sendBeacon", "importScripts", "document.cookie", "location.href =", "location.assign", "location.replace",
             "window.open", ".src =", "setAttribute(\"on", "setAttribute('on"]
SPACING = {4, 8, 16, 24, 32, 48}
FONT_SIZES = {14, 16, 18, 20, 24, 48}
LITERAL = re.compile(r"""(["'`])((?:\\.|(?!\1).)*)\1""", re.S)
BANNED_WORDS = ("absent", "no captcha", "passed", "secure", "missing")


def read(p):
    return p.read_text(encoding="utf-8")


def test_the_ui_directory_has_the_expected_files_and_nothing_executable_elsewhere():
    names = {p.name for p in FILES}
    assert {"index.html", "app.css", "app.js"} <= names
    for p in FILES:
        assert not p.is_symlink(), p.name
        assert p.suffix in {".js", ".css", ".html", ".json"}, p.name


@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_no_banned_api_in_js(path):
    src = read(path)
    for bad in BANNED_JS:
        assert bad not in src, f"{path.name} contains {bad}"
    assert not re.search(r"import\(\s*[^'\"\s]", src), f"{path.name} has a non-literal dynamic import"
    assert not re.search(r"\.(on[a-z]+)\s*=(?!=)", src), f"{path.name} assigns an on* handler property"


def test_session_storage_is_used_only_by_the_state_module():
    for p in JS:
        if p.name != "state.js":
            assert "sessionStorage" not in read(p), p.name


def test_index_html_has_no_inline_script_style_or_handler_and_one_module_script():
    html = read(UI / "index.html")
    scripts = re.findall(r"<script\b[^>]*>.*?</script>", html, re.S | re.I)
    assert scripts == ['<script type="module" src="/ui/app.js"></script>']
    assert len(re.findall(r"<script", html, re.I)) == 1
    assert not re.search(r"<style", html, re.I)
    assert not re.search(r"\sstyle\s*=", html, re.I)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    assert not re.search(r"https?://", html, re.I)
    assert len(re.findall(r"<h1\b", html, re.I)) == 1
    assert re.search(r"<html lang=\"en\"", html)


def test_css_has_no_imports_or_remote_urls_and_only_the_scale_for_spacing_and_type():
    css = read(UI / "app.css")
    assert "@import" not in css and not re.search(r"url\(\s*['\"]?\s*(https?:)?//", css)
    for m in re.finditer(r"--space-\d+\s*:\s*(\d+)px", css):
        assert int(m.group(1)) in SPACING, m.group(0)
    for m in re.finditer(r"(?:^|[;{\s])((?:padding|margin)[a-z-]*|gap|row-gap|column-gap)\s*:\s*([^;}]+)", css):
        for n in re.findall(r"(\d+(?:\.\d+)?)px", m.group(2)):
            assert float(n) in SPACING, f"{m.group(1)}: {m.group(2)}"
    for m in re.finditer(r"font-size\s*:\s*([^;}]+)", css):
        for n in re.findall(r"(\d+(?:\.\d+)?)px", m.group(1)):
            assert float(n) in FONT_SIZES, m.group(0)
        assert "rem" not in m.group(1) and "em" not in m.group(1), m.group(0)
    for m in re.finditer(r"--text-[a-z0-9-]+\s*:\s*(\d+)px", css):
        assert int(m.group(1)) in FONT_SIZES, m.group(0)


def test_the_words_the_prd_forbids_never_appear_in_visible_strings():
    texts = []
    for p in JS:
        texts += [m.group(2) for m in LITERAL.finditer(read(p))]
    texts.append(re.sub(r"<[^>]+>", " ", read(UI / "index.html")))
    for t in texts:
        low = t.lower()
        for word in BANNED_WORDS:
            assert word not in low, f"visible string contains '{word}': {t[:80]!r}"
