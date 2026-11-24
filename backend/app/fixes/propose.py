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
MAX_PAGE_TAGS = 50_000
_HEAD_CLOSE = re.compile(r"</head\s*>", re.I)


def insert_before_head_close(html: str, snippet: str):
    """`html` with `snippet` inserted before the first </head>, or None when there is none. When </head> starts its
    own line the snippet is added as whole lines; otherwise the line is split at </head>."""
    m = _HEAD_CLOSE.search(html)
    if m is None:
        return None
    line_start = html.rfind("\n", 0, m.start()) + 1
    if not html[line_start:m.start()].strip():
        return html[:line_start] + snippet + html[line_start:]
    return html[:m.start()] + "\n" + snippet + html[m.start():]


def _finding(report: Report, check_id: str):
    return next((f for f in report.findings if f.check_id == check_id), None)


def _problem(content: str, diff: str, problems: list, added: str):
    """Only what AgentReady generates is checked for secrets: the content and the diff's added lines. The existing
    page shown as context is the owner's own and is not part of the output."""
    if problems:
        return f"failed validation: {problems[0]}"
    labels = find_secrets(content) or find_secrets(added)
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
            patched = insert_before_head_close(home, snippet)
            if patched is None:
                skipped.append(Skipped(FixKind.JSON_LD, "no </head> in the home page HTML to patch"))
            else:
                fix = Fix("json_ld", FixKind.JSON_LD, "/", "jsonld-snippet.html",
                          "Add a schema.org WebSite JSON-LD snippet to the home page head, built from the home page "
                          "title and description.",
                          snippet, make_diff("index.html", home, patched), "json_ld")
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
