"""propose_fixes: pure (no fetcher, no I/O). Only valid, secret-free fixes are returned; every other kind is
listed as skipped with a reason. A reason never contains a secret value."""
import re

from ..models import Report, Status
from .baseline import Baseline
from .diff import make_diff
from .generate import build_json_ld, build_llms_txt, build_mcp_stub, json_ld_snippet
from .models import Fix, FixKind, NotEnoughContent, Proposal, SecretInSiteText, Skipped
from .secrets import added_lines, find_secrets
from .validate import validate_json_ld_snippet, validate_llms_txt, validate_mcp_stub

DIFF_MAX_CHARS = 100_000        # a diff the owner cannot realistically review is not presented
MAX_PAGE_CHARS = 500_000        # a home page beyond this is not processed (bounds the work on hostile pages)
MAX_PAGE_TAGS = 100_000
EXCERPT_CONTEXT = 200           # characters shown either side of the insertion point in an excerpt diff
LONG_LINE_CHARS = 2000          # a line longer than this is not reviewable as a diff: show an excerpt instead

# Text a browser does not treat as markup: comments, and the raw-text elements script, style, textarea and title.
# An unclosed one runs to the end of the page, as it does in a browser. The first </head> outside them is real.
_SKIP_OR_HEAD_CLOSE = re.compile(
    r"<!--.*?-->|<!--.*\Z"
    r"|<(script|style|textarea|title)\b[^>]*>(?:.*?</\1\s*>|.*\Z)"
    r"|(</head\s*>)", re.I | re.S)


def _insertion(html: str, snippet: str):
    """(position, inserted text) for the snippet, or None when the page has no real </head>. When </head> starts its
    own line the snippet is added as whole lines; otherwise the line is split at </head>."""
    for m in _SKIP_OR_HEAD_CLOSE.finditer(html):
        if m.group(2) is None:
            continue
        start = m.start(2)
        line_start = html.rfind("\n", 0, start) + 1
        if not html[line_start:start].strip():
            return line_start, snippet
        return start, "\n" + snippet
    return None


def insert_before_head_close(html: str, snippet: str):
    """`html` with `snippet` inserted before the first real </head> (not one inside a comment, script, style,
    textarea or title), or None when there is none."""
    found = _insertion(html, snippet)
    if found is None:
        return None
    position, inserted = found
    return html[:position] + inserted + html[position:]


def _json_ld_diff(home: str, snippet: str):
    """(diff, kind) for the home page patch, or None without a real </head>. A page with a very long line is shown
    as an excerpt around the insertion point instead: the whole page would not be reviewable."""
    found = _insertion(home, snippet)
    if found is None:
        return None
    position, inserted = found
    if max(map(len, home.split("\n"))) <= LONG_LINE_CHARS:
        full = make_diff("index.html", home, home[:position] + inserted + home[position:])
        if len(full) <= DIFF_MAX_CHARS:
            return full, "full"
    start, end = max(0, position - EXCERPT_CONTEXT), min(len(home), position + EXCERPT_CONTEXT)
    before = home[start:end]
    after = home[start:position] + inserted + home[position:end]
    return make_diff("index.html (excerpt)", before, after), "excerpt"


def _finding(report: Report, check_id: str):
    return next((f for f in report.findings if f.check_id == check_id), None)


def _problem(content: str, diff: str, problems: list, added: str):
    """Everything presented or exported is scanned, including existing context and removed diff lines."""
    if problems:
        return f"failed validation: {problems[0]}"
    labels = find_secrets(content) or find_secrets(diff) or find_secrets(added)
    if labels:
        return f"contains a secret-like value ({labels[0]})"
    if len(diff) > DIFF_MAX_CHARS:
        return "the diff is too large to review"
    return ""


def _finish(fix: Fix, problems: list, existing: str = ""):
    fix.added = added_lines(fix.diff, existing)
    reason = _problem(fix.content, fix.diff, problems, fix.added)
    if reason:
        return None, Skipped(fix.kind, reason)
    labels = find_secrets(existing) if existing else []
    if labels:
        fix.warning = f"the existing page contains a secret-like value ({labels[0]}); AgentReady did not copy it"
    return fix, None


def _is_html(body) -> bool:
    return isinstance(body, str) and body.lstrip()[:200].lower().startswith(("<!doctype", "<html"))


def propose_fixes(report: Report, baseline: Baseline) -> Proposal:
    origin, fixes, skipped = baseline.origin, [], []
    if not baseline.available:
        return Proposal([], [Skipped(kind, "baseline unavailable") for kind in FixKind])

    def add(result):
        fix, skip = result
        if fix:
            fixes.append(fix)
        else:
            skipped.append(skip)

    home = baseline.body("/")
    too_big = home is not None and (len(home) > MAX_PAGE_CHARS or home.count("<") > MAX_PAGE_TAGS)

    # llms.txt
    f = _finding(report, "llms_txt")
    existing_llms = baseline.body("/llms.txt")
    soft_404 = _is_html(existing_llms)                  # an HTML page served at /llms.txt is not an llms.txt file
    if f is not None and f.passed and not soft_404:
        skipped.append(Skipped(FixKind.LLMS_TXT, "llms.txt is already present"))
    elif f is not None and f.status is Status.UNREACHABLE:
        skipped.append(Skipped(FixKind.LLMS_TXT, "the check could not reach /llms.txt, so it is not known whether one exists"))
    elif home is None:
        skipped.append(Skipped(FixKind.LLMS_TXT, "the home page could not be fetched"))
    elif too_big:
        skipped.append(Skipped(FixKind.LLMS_TXT, "page too large to process"))
    else:
        try:
            text = build_llms_txt(origin, home)
        except NotEnoughContent:
            skipped.append(Skipped(FixKind.LLMS_TXT, "not enough content on the home page"))
        except SecretInSiteText as e:
            skipped.append(Skipped(FixKind.LLMS_TXT, f"contains a secret-like value ({e.label})"))
        else:
            fix = Fix("llms_txt", FixKind.LLMS_TXT, "/llms.txt", "llms.txt",
                      "Add /llms.txt, a short Markdown summary of the site for AI agents, built from the home page's "
                      "title, description and on-site links.",
                      text, make_diff("llms.txt", None if soft_404 else existing_llms, text), "llms_txt")
            if soft_404:
                fix.note = ("/llms.txt currently returns an HTML page (a soft 404), not an llms.txt file; "
                            "this adds a real one.")
            add(_finish(fix, validate_llms_txt(text, origin), "" if soft_404 else (existing_llms or "")))

    # JSON-LD
    f = _finding(report, "json_ld")
    if f is not None and f.passed:
        skipped.append(Skipped(FixKind.JSON_LD, "valid JSON-LD is already present"))
    elif home is None:
        skipped.append(Skipped(FixKind.JSON_LD, "the home page could not be fetched"))
    elif too_big:
        skipped.append(Skipped(FixKind.JSON_LD, "page too large to process"))
    else:
        try:
            snippet = json_ld_snippet(build_json_ld(origin, home))
        except NotEnoughContent:
            skipped.append(Skipped(FixKind.JSON_LD, "not enough content on the home page"))
        except SecretInSiteText as e:
            skipped.append(Skipped(FixKind.JSON_LD, f"contains a secret-like value ({e.label})"))
        else:
            shown = _json_ld_diff(home, snippet)
            if shown is None:
                skipped.append(Skipped(FixKind.JSON_LD, "no </head> in the home page HTML to patch"))
            else:
                diff, kind = shown
                fix = Fix("json_ld", FixKind.JSON_LD, "/", "jsonld-snippet.html",
                          "Add a schema.org WebSite JSON-LD snippet to the home page head, built from the home page "
                          "title and description.",
                          snippet, diff, "json_ld", diff_kind=kind)
                if kind == "excerpt":
                    fix.note = ("The page has a very long line, so the diff shows only the text around the insertion "
                                "point (an excerpt), not the whole page.")
                add(_finish(fix, validate_json_ld_snippet(snippet), home))

    # OpenAPI -> MCP stub
    found = baseline.spec()
    if found is None:
        skipped.append(Skipped(FixKind.MCP_STUB, "no OpenAPI document was found"))
    else:
        spec_path, spec = found
        try:
            source = build_mcp_stub(origin, spec)
        except NotEnoughContent:
            skipped.append(Skipped(FixKind.MCP_STUB, "the OpenAPI document has no GET operations"))
        except SecretInSiteText as e:
            skipped.append(Skipped(FixKind.MCP_STUB, f"contains a secret-like value ({e.label})"))
        else:
            fix = Fix("mcp_stub", FixKind.MCP_STUB, spec_path, "mcp_server_stub.py",
                      f"A read-only MCP tool stub for the GET operations in the OpenAPI document at {spec_path}.",
                      source, make_diff("mcp_server_stub.py", None, source), "openapi_public")
            add(_finish(fix, validate_mcp_stub(source, spec, origin)))

    return Proposal(fixes, skipped)
