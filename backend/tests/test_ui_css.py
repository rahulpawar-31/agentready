"""Look and accessibility rules for app/ui/app.css that can be checked by reading it (the layout itself needs a real
browser and is on the manual checklist in docs/ui.md): contrast ratios computed from the tokens in light and dark,
which rules use the strong border, no strike-through on disabled buttons, forced-colours and print rules, and every
chip class the views can produce having a style."""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "app" / "ui"
CSS = (UI / "app.css").read_text(encoding="utf-8")
RENDER = (UI / "render.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")


def block(text, start):
    """The text between the braces that open at `start` (a string such as ':root {')."""
    i = text.index(start) + len(start)
    depth = 1
    j = i
    while depth:
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        j += 1
    return text[i:j - 1]


def tokens(body):
    return {m.group(1): m.group(2) for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", body)}


LIGHT = tokens(block(CSS, ":root {"))
DARK = {**LIGHT, **tokens(block(block(CSS, "@media (prefers-color-scheme: dark) {"), ":root {"))}
THEMES = {"light": LIGHT, "dark": DARK}


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def rule(selector):
    """The declarations of the first rule whose selector list contains `selector` exactly."""
    for m in re.finditer(r"(?m)^([^{}@]+)\{([^{}]*)\}", CSS):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            return m.group(2)
    raise AssertionError(f"no rule for {selector}")


TEXT_PAIRS = [("fg", "bg"), ("fg", "surface"), ("fg", "tint"), ("fg", "accent-tint"), ("muted", "surface"), ("muted", "bg"),
              ("muted", "tint"), ("accent-text", "accent")]
BOUNDARY_PAIRS = [("border-strong", "surface"), ("border-strong", "bg"), ("border-strong", "tint"), ("focus", "bg"), ("focus", "surface"),
                  ("focus", "tint"), ("accent", "tint"), ("accent", "surface")]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_text_pairs_have_at_least_4_5_to_1(theme, fg, bg):
    t = THEMES[theme]
    assert ratio(t[fg], t[bg]) >= 4.5, f"{theme}: {fg} on {bg} is {ratio(t[fg], t[bg]):.2f}"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("a,b", BOUNDARY_PAIRS)
def test_boundaries_and_graphics_have_at_least_3_to_1(theme, a, b):
    t = THEMES[theme]
    assert ratio(t[a], t[b]) >= 3.0, f"{theme}: {a} against {b} is {ratio(t[a], t[b]):.2f}"


@pytest.mark.parametrize("selector", ['input[type="text"]', ".btn-secondary", ".chip", "progress"])
def test_the_strong_border_is_used_where_a_boundary_is_the_only_cue(selector):
    assert "var(--border-strong)" in rule(selector), selector


def test_disabled_buttons_are_dimmed_not_struck_through():
    body = rule(".btn[disabled]")
    assert "line-through" not in body and "opacity" in body and "cursor: not-allowed" in body


def test_every_chip_class_the_views_can_make_has_a_style():
    used = set(re.findall(r"chip-[a-z-]+", RENDER))
    assert {"chip-not-detected", "chip-confidence", "chip-proposed"} <= used
    for name in used:
        assert re.search(rf"\.{re.escape(name)}\b", CSS), f"no style for .{name}"


def test_each_finding_row_has_a_visible_expand_cue_that_does_not_depend_on_the_native_marker():
    assert 'class: "finding-toggle"' in RENDER or "finding-toggle" in RENDER
    assert re.search(r"\.finding\[open\]\s+\.toggle-closed\s*\{[^}]*display:\s*none", CSS)
    assert re.search(r"\.finding:not\(\[open\]\)\s+\.toggle-open\s*\{[^}]*display:\s*none", CSS)
    assert "Show evidence" in RENDER and "Hide evidence" in RENDER


def test_forced_colours_keeps_progress_chips_diffs_buttons_and_focus_visible():
    body = block(CSS, "@media (forced-colors: active) {")
    for needle in ["progress", ".chip", ".diff-add", ".btn", ":focus-visible", "Highlight", "CanvasText"]:
        assert needle in body, needle


def test_print_rules_show_whole_diffs_and_drop_the_buttons():
    body = block(CSS, "@media print {")
    assert ".actions" in body and "display: none" in body
    assert ".diff" in body and "max-height: none" in body and "overflow: visible" in body


def test_the_page_asks_for_no_favicon_file():
    assert '<link rel="icon" href="data:,">' in HTML
