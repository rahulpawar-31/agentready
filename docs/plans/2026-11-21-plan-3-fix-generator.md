# AgentReady, Plan 3: Fix Generator, Validation, Diff, Approval Queue and Export (revision 1)

> Written following the `writing-plans` skill (superpowers), by hand. Intended repo path: `docs/plans/2026-11-21-plan-3-fix-generator.md`.
> Plans 1 (auditor, merged as PR #1) and 2 (task runner, PR #2) are the base. This plan does not need the runner: it needs only Plan 1's `Report`, the audit's cache snapshot and the guard. Branch from `main`: `watcher/agentready-fix-generator`.

> **For agentic workers:** execute task by task, test first. Steps use checkbox (`- [ ]`) syntax. Git: stay on the branch, stage only the files you changed, never `git add -A`, never merge, never push to main. Open the PR with `gh pr create` (ready, not draft). Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

**Goal:** From an audit report plus the pages the audit saw, propose three fixes (`llms.txt`, a JSON-LD snippet, an OpenAPI-to-MCP stub), validate every output before anyone sees it, show each as a diff against the baseline, hold them in an approval queue, and let the owner export one only after approving it.

**Architecture:** A new package `app/fixes/`. `baseline.py` captures what the audit's snapshot saw (cache hits, no new requests). `generate.py` builds the three outputs from that baseline only, deterministically, with no model call and no network. `validate.py` and `secrets.py` check every output. `diff.py` makes unified diffs. `propose.py` ties them together and returns only valid fixes (invalid ones are listed as skipped, with a reason). `queue.py` is the state machine (proposed → approved or rejected) and the only place an export is built. `main.py` gets endpoints. Nothing in `app/fixes/` imports a network, file-writing, subprocess or mail library: a test enforces it.

**Tech Stack:** Python 3.12, FastAPI, beautifulsoup4, stdlib `difflib`, `ast`, `hashlib`, `json`. No new dependency.

**Spec:** AgentReady PRD (Claude Doc, https://claude.ai/artifact/2CLFRC7VzK9iRbN5daPmUd), requirements FR-6.1 to FR-6.4, and the Safety table.

## Global Constraints (copied from the PRD and Plans 1 and 2)
- FR-6.1: generate `llms.txt`, JSON-LD and an OpenAPI-to-MCP stub, and validate each output before it is shown. **An invalid output is never presented.**
- FR-6.2: show every fix as a diff against the baseline captured in the before snapshot. The UI and the MCP response include the diff.
- FR-6.3: export is enabled only after the owner approves a fix. The owner approves first, then explicitly downloads or copies the patch. **Approval itself exports nothing, and the app never deploys the patch. The app never writes to a site.** Exported files contain no secrets or live credentials. Acceptance: no code path writes to a target site, no export is possible for an unapproved fix, and a test scans exported files for secret-like values.
- FR-6.4: the OpenAPI-to-MCP stub is validated and shown in the demo, but not counted in task success. Nothing in this plan changes the runner or its results table.
- No GitHub PR feature in the MVP (stretch only).
- Fix generation makes **no network request**: it reads the baseline the audit already fetched. Fix content comes only from that site's own pages, with no invented facts and no model call.
- Text taken from a site (title, description, link text, OpenAPI summaries) is untrusted. It is clipped, stripped of control characters and neutralised before it is placed in any output, and it can never create a link to another host, close a `<script>` tag or add code.
- Absence is never claimed. Evidence wording stays "not detected".
- The test count in each task is a floor, not a target: report the real number.

## Decisions made here (flag to the owner)
1. **Deterministic generators, no LLM.** Reproducible, testable, cannot invent facts. An LLM-written `llms.txt` is a stretch.
2. **Who may approve.** The MVP has no accounts. The report id (a random uuid4 hex that only the verified requester receives) is the bearer for approving and exporting. State this in `docs/fixes.md`. Per-user accounts are out of scope.
3. **Fix id = the fix kind** (`llms_txt`, `json_ld`, `mcp_stub`), unique within a report.
4. **JSON-LD is delivered as a snippet plus a diff against the home page HTML as fetched.** That HTML may come from a template, so the owner applies the snippet to the template. The wording in the export says so.
5. **In-memory store**, bounded like `_reports` (200), evicted together. A restart loses queues; acceptable for the MVP, stated in `docs/fixes.md`.
6. **Approval can be revoked** (`reject` after `approve` returns it to rejected and disables export). **A rejected fix cannot be re-approved**; re-running `POST /report/{id}/fixes` never resets a decision.
7. Clip constants: `FIELD_CLIP = 200`, `DESC_CLIP = 300`, `LINK_TEXT_CLIP = 80`, `LLMS_MAX_LINKS = 10`, `LLMS_MAX_BYTES = 20_000`, `MCP_MAX_TOOLS = 20`, `STUB_MAX_BYTES = 60_000`.

## File structure
```
backend/app/fixes/__init__.py
backend/app/fixes/models.py       # FixKind, FixStatus, Fix, Skipped, Proposal, ExportBundle, NotEnoughContent
backend/app/fixes/baseline.py     # Baseline, capture_baseline
backend/app/fixes/secrets.py      # find_secrets
backend/app/fixes/diff.py         # make_diff
backend/app/fixes/generate.py     # build_llms_txt, build_json_ld, json_ld_snippet, build_mcp_stub, tools_from_stub
backend/app/fixes/validate.py     # validate_llms_txt, validate_json_ld_snippet, validate_mcp_stub
backend/app/fixes/propose.py      # propose_fixes(report, baseline) -> Proposal
backend/app/fixes/queue.py        # FixQueue, NotApproved, UnknownFix
backend/app/main.py               # modify: capture baseline in POST /audit; 5 fix endpoints
docs/fixes.md                     # what the fixes are, limits, the approval model (do NOT edit README.md: PR #2 also edits it)
backend/tests/test_fixes_*.py     # one per module, plus test_fixes_no_write_path.py and test_api_fixes.py
```

---

### Task 1: Secret scanner and diff helper

**Files:** Create `backend/app/fixes/__init__.py` (empty), `secrets.py`, `diff.py`. Test: `backend/tests/test_fixes_secrets_diff.py`.

**Interfaces:**
- Produces: `find_secrets(text: str) -> list[str]` returns **labels only** (for example `"aws access key"`), never the matched value. Labels, each from one pattern: `private key block`, `aws access key`, `github token`, `api key (sk-)`, `slack token`, `jwt`, `secret assignment` (an `UPPER_CASE` name containing PASSWORD, SECRET, TOKEN or KEY, then `=` or `:`, then 6 or more non-space characters, line-anchored), `bearer token` (`Authorization: Bearer` plus 16 or more characters), `password in url` (`scheme://user:pass@`). Sorted, de-duplicated.
- Produces: `make_diff(path: str, old: str | None, new: str) -> str`: unified diff, headers `--- a/<path>` and `+++ b/<path>`; when `old is None` the first header is `--- /dev/null`. Lines without a trailing newline get one for display.

- [ ] **Step 1: Write the failing tests**
```python
import pytest
from app.fixes.diff import make_diff
from app.fixes.secrets import find_secrets

SAMPLES = {
    "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc",
    "aws access key": "id " + "AKIA" + "ABCDEFGHIJKLMNOP",
    "github token": "ghp_" + "a" * 36,
    "api key (sk-)": "sk-" + "a1B2" * 8,
    "slack token": "xoxb-" + "1234567890-abcdefghij",
    "jwt": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0." + "abcdef",
    "secret assignment": "DB_PASSWORD=hunter22",
    "bearer token": "Authorization: Bearer " + "a" * 24,
    "password in url": "https://user:pa55word@x.test/",
}


@pytest.mark.parametrize("label,text", SAMPLES.items())
def test_each_secret_kind_is_found_and_its_value_is_never_returned(label, text):
    found = find_secrets("prefix\n" + text + "\nsuffix")
    assert label in found
    joined = " ".join(found)
    assert text not in joined and "hunter22" not in joined and "pa55word" not in joined


def test_ordinary_site_text_has_no_findings():
    assert find_secrets("# Acme\n> Shoes. Price key: see the table.\n- [Shop](https://x.test/shop)") == []
    assert find_secrets("The API key is optional; send your token in the header.") == []


def test_new_file_diff_uses_dev_null():
    d = make_diff("llms.txt", None, "# Acme\n")
    lines = d.splitlines()
    assert lines[0] == "--- /dev/null" and lines[1] == "+++ b/llms.txt" and "+# Acme" in lines


def test_modified_file_diff_shows_removed_and_added_lines():
    d = make_diff("index.html", "<head>\n</head>\n", "<head>\n<x/>\n</head>\n")
    assert d.startswith("--- a/index.html\n+++ b/index.html") and "+<x/>" in d.splitlines()


def test_missing_trailing_newline_is_normalised():
    assert make_diff("a", None, "no newline").endswith("\n")
    assert make_diff("a", "same\n", "same\n") == ""
```
- [ ] **Step 2:** Run `cd backend && .venv/bin/python -m pytest tests/test_fixes_secrets_diff.py -q`. Expected: FAIL (`ModuleNotFoundError: app.fixes`).
- [ ] **Step 3: Implement** the compiled-regex table in `secrets.py` (module-level, `re.compile`, line-anchored `(?m)` where stated) and `make_diff` with `difflib.unified_diff(old_lines, new_lines, fromfile, tofile)` (`lineterm=""`, then join with `\n` and end with `\n`; return `""` when there are no changes).
- [ ] **Step 4:** Run: expected all pass. Commit `feat(fixes): secret scanner and unified diff helper`.

---

### Task 2: Models and baseline capture

**Files:** Create `models.py`, `baseline.py`. Test: `backend/tests/test_fixes_baseline.py`.

**Interfaces:**
- Produces (`models.py`):
  - `FixKind(str, Enum)`: `LLMS_TXT="llms_txt"`, `JSON_LD="json_ld"`, `MCP_STUB="mcp_stub"`.
  - `FixStatus(str, Enum)`: `PROPOSED="proposed"`, `APPROVED="approved"`, `REJECTED="rejected"`.
  - `Fix` (mutable dataclass): `id: str`, `kind: FixKind`, `path: str` (the site path the file belongs at or the page it patches), `filename: str` (download name), `description: str`, `content: str`, `diff: str`, `finding_id: str`, `status: FixStatus = PROPOSED`, `approved_sha256: str = ""`.
  - `Skipped(kind: FixKind, reason: str)`; `Proposal(fixes: list[Fix], skipped: list[Skipped])`; `ExportBundle(fix_id, filename, content, diff, sha256, note)`.
  - `class NotEnoughContent(ValueError)`.
- Produces (`baseline.py`): `Baseline(origin: str, pages: dict[str, tuple[int, str]])` frozen dataclass with `body(path) -> str | None` (the body if status is 200, else `None`) and `spec() -> tuple[str, dict] | None` (the first of `/openapi.json`, `/swagger.json`, `/.well-known/openapi.json` whose body parses as a JSON object with an `openapi` or `swagger` key; return the path and the parsed dict); `capture_baseline(origin: str, fetcher) -> Baseline` calls `fetcher.get` for `origin + path` for `/`, `/llms.txt` and the three spec paths, in that order, and stores `(status, body)`. It uses the same `Fetcher` the audit used, so every call is a cache hit after an audit. It only calls `get`.

- [ ] **Step 1: Write the failing tests**
```python
import json
from app.fixes.baseline import Baseline, capture_baseline
from tests.conftest import FakeFetcher, page

O = "https://x.test"


def test_capture_reads_the_five_known_paths_with_get_only():
    f = FakeFetcher({f"{O}/": page(f"{O}/", "<html>hi</html>")})
    b = capture_baseline(O, f)
    assert [u for u, _ in f.calls] == [f"{O}/", f"{O}/llms.txt", f"{O}/openapi.json",
                                      f"{O}/swagger.json", f"{O}/.well-known/openapi.json"]
    assert b.body("/") == "<html>hi</html>" and b.body("/llms.txt") is None


def test_body_is_none_unless_status_is_200():
    f = FakeFetcher({f"{O}/": page(f"{O}/", "x", status=500)})
    assert capture_baseline(O, f).body("/") is None


def test_spec_returns_the_first_openapi_document_only():
    spec = {"openapi": "3.0.0", "paths": {}}
    f = FakeFetcher({f"{O}/swagger.json": page(f"{O}/swagger.json", json.dumps(spec)),
                     f"{O}/.well-known/openapi.json": page(f"{O}/.well-known/openapi.json", json.dumps({"openapi": "9"}))})
    b = capture_baseline(O, f)
    assert b.spec() == ("/swagger.json", spec)


def test_non_spec_json_and_invalid_json_are_ignored():
    f = FakeFetcher({f"{O}/openapi.json": page(f"{O}/openapi.json", '{"hello": 1}'),
                     f"{O}/swagger.json": page(f"{O}/swagger.json", "{oops")})
    assert capture_baseline(O, f).spec() is None


def test_baseline_is_immutable():
    b = Baseline(O, {})
    try:
        b.origin = "x"
        assert False
    except Exception as e:
        assert e.__class__.__name__ == "FrozenInstanceError"
```
- [ ] **Step 2:** Run, expect FAIL. **Step 3:** implement. **Step 4:** pass, commit `feat(fixes): models and baseline capture`.

---

### Task 3: `llms.txt` and JSON-LD generators and validators

**Files:** Create `generate.py` (part 1), `validate.py` (part 1). Test: `backend/tests/test_fixes_generate.py`.

**Interfaces:**
- Produces: `build_llms_txt(origin: str, home_html: str) -> str`; `build_json_ld(origin: str, home_html: str) -> dict`; `json_ld_snippet(data: dict) -> str`; `validate_llms_txt(text: str, origin: str) -> list[str]`; `validate_json_ld_snippet(snippet: str) -> list[str]` (each returns a list of problems, empty means valid). Both builders raise `NotEnoughContent` when the page lacks a usable `<title>`.
- Rules for `build_llms_txt` (llmstxt.org shape):
  1. `# <title>` (text of `<title>`, whitespace collapsed, markup characters `[]()<>` and control characters removed, clipped to `FIELD_CLIP`).
  2. If the page has `<meta name="description">` (or `og:description`): a blank line then `> <description>` (cleaned, clipped to `DESC_CLIP`, one line).
  3. If there are links: a blank line, `## Pages`, then up to `LLMS_MAX_LINKS` lines `- [<text>](<url>)`. Links come from `<a href>` in document order. Resolve with `urljoin(origin + "/", href)` inside `try/except ValueError` (a bad href is skipped). Keep only http or https URLs whose netloc equals the origin's netloc. **Drop the fragment and the query string** (queries can carry session tokens). De-duplicate by URL. Link text is cleaned and clipped to `LINK_TEXT_CLIP`; a link whose cleaned text is empty is skipped.
  4. The file ends with exactly one `\n`.
  5. If there is neither a description nor any link, raise `NotEnoughContent`.
- Rules for `build_json_ld`: `{"@context": "https://schema.org", "@type": "WebSite", "name": <title>, "url": <origin>, "description": <description>}`; the `description` key only when the page has one. No other field is ever added.
- `json_ld_snippet`: `<script type="application/ld+json">\n` + `json.dumps(data, indent=2, ensure_ascii=False)` with every `<` replaced by `<`, `>` by `>` and `&` by `&` + `\n</script>\n`.
- `validate_llms_txt` problems: no first-line `# `; more than one line starting with `# ` (an H1 injected later); size over `LLMS_MAX_BYTES`; a Markdown link `](` to a URL not on the origin's host; a control character; a non-blank line that is none of H1, `> `, `## `, `- [` link line.
- `validate_json_ld_snippet` problems: not exactly one `<script type="application/ld+json">` element, extra `</script>`, JSON that does not parse, missing `@context` or `@type`, `@context` not an `https://schema.org` URL.

- [ ] **Step 1: Write the failing tests**
```python
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
    assert [l for l in t.splitlines() if l.startswith("# ")] == [t.splitlines()[0]]
    assert "](https://evil.test" not in t and "evil.test" not in "".join(l for l in t.splitlines() if l.startswith("- ["))
    assert validate_llms_txt(t, O) == []


def test_missing_title_or_content_is_refused():
    with pytest.raises(NotEnoughContent):
        build_llms_txt(O, "<html><body><a href='/a'>A</a></body></html>")
    with pytest.raises(NotEnoughContent):
        build_llms_txt(O, "<title>Only a title</title>")


@pytest.mark.parametrize("text", [
    "no heading\n", "# A\n# B\n", "# A\n\n- [x](https://evil.test/p)\n", "# A\n\nfree text line\n",
    "# A\n" + "- [x](https://x.test/p)\n" * 2000, "# A\x00\n",
])
def test_llms_validator_rejects_bad_files(text):
    assert validate_llms_txt(text, O) != []


def test_json_ld_uses_only_what_the_page_says():
    assert build_json_ld(O, HOME) == {"@context": "https://schema.org", "@type": "WebSite", "name": "Acme Shoes",
                                      "url": O, "description": "Handmade shoes, shipped worldwide."}
    assert "description" not in build_json_ld(O, "<title>T</title>")


def test_snippet_cannot_be_broken_out_of():
    home = HOME.replace("Acme  Shoes", "</script><script>alert(1)</script>")
    s = json_ld_snippet(build_json_ld(O, home))
    assert s.count("</script>") == 1 and "<script>alert" not in s and validate_json_ld_snippet(s) == []


@pytest.mark.parametrize("snippet", [
    "", "<script>{}</script>", '<script type="application/ld+json">{oops}</script>',
    '<script type="application/ld+json">{"@type": "WebSite"}</script>',
    '<script type="application/ld+json">{"@context": "http://evil.test", "@type": "WebSite"}</script>',
    '<script type="application/ld+json">{"@context": "https://schema.org", "@type": "X"}</script><script>x()</script>',
])
def test_json_ld_validator_rejects_bad_snippets(snippet):
    assert validate_json_ld_snippet(snippet) != []
```
- [ ] **Steps 2–4:** run, expect FAIL; implement with BeautifulSoup (`html.parser`) for title, meta and anchors; run to pass; commit `feat(fixes): llms.txt and JSON-LD generators with validators`.

---

### Task 4: OpenAPI-to-MCP stub generator and validator

**Files:** Extend `generate.py` and `validate.py`. Test: `backend/tests/test_fixes_mcp_stub.py`.

**Interfaces:**
- Produces: `build_mcp_stub(origin: str, spec: dict) -> str`; `tools_from_stub(source: str) -> list[dict]`; `validate_mcp_stub(source: str, spec: dict, origin: str) -> list[str]`.
- Tools: one per **GET** operation in `spec["paths"]` (other methods are ignored: the stub is read-only). Tool dict: `name`, `description` (operation `summary`, else `description`, else the path; cleaned and clipped to `DESC_CLIP`), `method` (`"GET"`), `path`, `path_params` (names), `query_params` (names), `input_schema` (`{"type": "object", "properties": {param: {"type": <schema type or "string">}}, "required": [path params and required query params]}`). Name: the `operationId` reduced to `[A-Za-z0-9_]` and clipped to 64, else `get_` plus the path with every run of non-alphanumeric characters replaced by `_` and edge underscores removed (`/products/{id}` gives `get_products_id`); a repeated name gets `_2`, `_3`. At most `MCP_MAX_TOOLS`. Raise `NotEnoughContent` when there are no GET operations.
- Source layout: a docstring; `import json`, `import urllib.parse`, `import urllib.request`; `BASE_URL = <json.dumps(origin)>` (**always the verified origin, never a `servers` URL from the spec**); `TOOLS = json.loads(<repr of the json.dumps string>)` (data is embedded as an escaped string literal so no spec text is ever interpolated as code); `_BY_NAME`; and a fixed `call_tool(name, arguments)` function that performs a single read-only `GET` with `urllib.request.Request(url, method="GET")`, path parameters inserted with `urllib.parse.quote(..., safe="")`, query parameters through `urllib.parse.urlencode`, a 15 second timeout, and only for names in `_BY_NAME`. A closing comment explains that the file is a stub to wire into the owner's MCP SDK. Nothing runs at import time beyond constants. Size limit `STUB_MAX_BYTES`.
- `validate_mcp_stub` problems: not valid Python (`ast.parse`); a top-level statement other than docstring, import, assignment or function definition; an import other than `json`, `urllib.parse`, `urllib.request`; `TOOLS` not recoverable by `tools_from_stub`; any tool whose `method` is not `GET`, whose name is not an identifier, whose `(path, name)` does not match a GET operation in `spec`, or that is missing from or extra to the spec's GET operations (up to the cap); `BASE_URL` not equal to `origin`; and **the source is not byte-for-byte what `build_mcp_stub(origin, spec)` produces** (tamper check).

- [ ] **Step 1: Write the failing tests**
```python
import ast
import pytest
from app.fixes.generate import build_mcp_stub, tools_from_stub
from app.fixes.models import NotEnoughContent
from app.fixes.validate import validate_mcp_stub

O = "https://x.test"
SPEC = {"openapi": "3.0.0", "info": {"title": "Acme API", "version": "1"},
        "servers": [{"url": "https://evil.test/v1"}],
        "paths": {
            "/products": {"get": {"operationId": "listProducts", "summary": "List products",
                                  "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}]},
                          "post": {"operationId": "createProduct"}},
            "/products/{id}": {"get": {"summary": "Get one product",
                                       "parameters": [{"name": "id", "in": "path", "required": True,
                                                       "schema": {"type": "string"}}]},
                               "delete": {}}}}


def test_only_get_operations_become_tools_with_stable_names():
    tools = tools_from_stub(build_mcp_stub(O, SPEC))
    assert [t["name"] for t in tools] == ["listProducts", "get_products_id"]
    assert all(t["method"] == "GET" for t in tools)
    assert tools[0]["query_params"] == ["limit"] and tools[1]["path_params"] == ["id"]
    assert tools[1]["input_schema"]["required"] == ["id"]


def test_base_url_is_the_verified_origin_never_the_spec_server():
    src = build_mcp_stub(O, SPEC)
    assert 'BASE_URL = "https://x.test"' in src and "evil.test" not in src


def test_stub_is_valid_python_with_no_code_run_at_import():
    src = build_mcp_stub(O, SPEC)
    tree = ast.parse(src)
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.Expr)
    assert all(isinstance(n, allowed) for n in tree.body)
    assert not any(isinstance(n, ast.Expr) and not isinstance(n.value, ast.Constant) for n in tree.body)
    assert validate_mcp_stub(src, SPEC, O) == []


def test_site_text_in_the_spec_cannot_become_code():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {
        "operationId": 'x"; import os; os.system("boom") #', "summary": '"""\nimport os\nos.system("boom")\n"""'}}}}
    src = build_mcp_stub(O, spec)
    assert not any(isinstance(n, ast.Attribute) and n.attr == "system" for n in ast.walk(ast.parse(src)))
    t = tools_from_stub(src)[0]
    assert t["name"].isidentifier() and "os.system" in t["description"]
    assert validate_mcp_stub(src, spec, O) == []


def test_tool_count_is_capped_and_duplicates_get_suffixes():
    spec = {"openapi": "3.0.0", "paths": {f"/p{i}": {"get": {"operationId": "same"}} for i in range(30)}}
    names = [t["name"] for t in tools_from_stub(build_mcp_stub(O, spec))]
    assert len(names) == 20 and names[0] == "same" and names[1] == "same_2"


def test_no_get_operations_is_refused():
    with pytest.raises(NotEnoughContent):
        build_mcp_stub(O, {"openapi": "3.0.0", "paths": {"/a": {"post": {}}}})


def test_validator_catches_tampering_and_mismatch():
    src = build_mcp_stub(O, SPEC)
    assert validate_mcp_stub(src.replace("GET", "POST"), SPEC, O) != []
    assert validate_mcp_stub(src + "\nimport os\nos.system('x')\n", SPEC, O) != []
    assert validate_mcp_stub(src.replace("https://x.test", "https://evil.test", 1), SPEC, O) != []
    assert validate_mcp_stub("def broken(:\n", SPEC, O) != []
    other = {"openapi": "3.0.0", "paths": {"/z": {"get": {}}}}
    assert validate_mcp_stub(src, other, O) != []
```
- [ ] **Steps 2–4:** run, expect FAIL; implement; run to pass; commit `feat(fixes): OpenAPI to MCP stub generator and validator`.

---

### Task 5: `propose_fixes`

**Files:** Create `propose.py`. Test: `backend/tests/test_fixes_propose.py`.

**Interfaces:**
- Consumes: `Report` (Plan 1), `Baseline`, generators, validators, `find_secrets`, `make_diff`.
- Produces: `propose_fixes(report: Report, baseline: Baseline) -> Proposal`. **Pure: no fetcher parameter, no I/O.** Order of kinds: `llms_txt`, `json_ld`, `mcp_stub`.
- Rules per kind (a kind either yields a `Fix` or a `Skipped` with a reason; never both, never an invalid fix):
  - `llms_txt` (finding id `llms_txt`): skip `"llms.txt is already present"` if that finding passed; skip `"the home page could not be fetched"` if `baseline.body("/")` is `None`; `NotEnoughContent` becomes skip `"not enough content on the home page"`. Fix: `path="/llms.txt"`, `filename="llms.txt"`, diff `make_diff("llms.txt", baseline.body("/llms.txt"), content)`.
  - `json_ld` (finding id `json_ld`): skip when that finding passed (`"valid JSON-LD is already present"`), when the home page is missing, on `NotEnoughContent`, or when the home HTML has no `</head>` (`"no </head> in the home page HTML to patch"`). Fix: `path="/"`, `filename="jsonld-snippet.html"`, `content` is the snippet, `diff` is `make_diff("index.html", home_html, home_html_with_snippet_inserted_before_the_first_case_insensitive_</head>)`.
  - `mcp_stub` (finding id `openapi_public`): skip `"no OpenAPI document was found"` when `baseline.spec()` is `None` (and when the finding did not pass); `NotEnoughContent` becomes `"the OpenAPI document has no GET operations"`. Fix: `path` is the spec path, `filename="mcp_server_stub.py"`, diff is a new file.
  - Every generated content goes through its validator **and** `find_secrets`. Any problem becomes a skip with reason `"failed validation: <first problem>"` or `"contains a secret-like value (<label>)"`. The reason never contains the secret value.
- `Fix.id` is `kind.value`; `description` is one plain sentence per kind; `finding_id` is the audit finding the fix answers.

- [ ] **Step 1: Write the failing tests** (use the real `audit()` with `FakeFetcher` so the report is realistic)
```python
import json
from app.audit import audit
from app.fixes.baseline import capture_baseline
from app.fixes.generate import build_llms_txt
from app.fixes.models import FixKind
from app.fixes.propose import propose_fixes
from tests.conftest import FakeFetcher, page

O = "https://x.test"
HOME = ('<html><head><title>Acme Shoes</title><meta name="description" content="Handmade shoes.">'
        '</head><body><a href="/shop">Shop</a>' + "<p>" + "word " * 80 + "</p></body></html>")


def run(pages):
    f = FakeFetcher({f"{O}{p}": page(f"{O}{p}", b) for p, b in pages.items()})
    report = audit(O, f)
    return propose_fixes(report, capture_baseline(O, f)), f


def kinds(p):
    return [x.kind for x in p.fixes], {s.kind: s.reason for s in p.skipped}


def test_a_bare_site_gets_llms_txt_and_json_ld_and_a_reason_for_the_stub():
    p, _ = run({"/": HOME})
    got, skipped = kinds(p)
    assert got == [FixKind.LLMS_TXT, FixKind.JSON_LD] and "OpenAPI" in skipped[FixKind.MCP_STUB]
    assert all(x.diff and x.content and x.finding_id for x in p.fixes)
    assert p.fixes[0].diff.startswith("--- /dev/null")


def test_existing_llms_txt_and_json_ld_are_not_replaced():
    home = HOME.replace("</head>", '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite"}</script></head>')
    p, _ = run({"/": home, "/llms.txt": "# Mine\n> mine\n"})
    assert p.fixes == [] and {s.kind for s in p.skipped} >= {FixKind.LLMS_TXT, FixKind.JSON_LD}


def test_json_ld_diff_inserts_before_head_close_and_leaves_the_rest():
    p, _ = run({"/": HOME})
    d = next(x for x in p.fixes if x.kind is FixKind.JSON_LD).diff
    assert '+<script type="application/ld+json">' in d and " </head>" in d or "</head>" in d
    assert not any(l.startswith("-") and not l.startswith("---") for l in d.splitlines())


def test_home_without_head_close_skips_json_ld_with_a_reason():
    p, _ = run({"/": "<title>T</title><meta name='description' content='d'><body>" + "x " * 200})
    assert "no </head>" in kinds(p)[1][FixKind.JSON_LD]


def test_an_openapi_document_gets_a_stub():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "A"}}}}
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    stub = next(x for x in p.fixes if x.kind is FixKind.MCP_STUB)
    assert stub.path == "/openapi.json" and stub.filename == "mcp_server_stub.py" and stub.diff.startswith("--- /dev/null")


def test_a_secret_in_site_text_skips_the_fix_and_the_value_never_appears():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": f"key {secret}"}}}}
    p, _ = run({"/": HOME, "/openapi.json": json.dumps(spec)})
    assert FixKind.MCP_STUB not in kinds(p)[0]
    reason = kinds(p)[1][FixKind.MCP_STUB]
    assert "secret" in reason and secret not in reason
    assert secret not in "".join(x.content + x.diff for x in p.fixes)


def test_an_invalid_output_is_never_presented(monkeypatch):
    import app.fixes.propose as mod
    monkeypatch.setattr(mod, "build_llms_txt", lambda origin, html: "garbage without heading\n")
    p, _ = run({"/": HOME})
    assert FixKind.LLMS_TXT not in kinds(p)[0] and kinds(p)[1][FixKind.LLMS_TXT].startswith("failed validation")


def test_unreachable_home_skips_everything_that_needs_it():
    p, _ = run({})
    assert p.fixes == [] and all(s.reason for s in p.skipped)


def test_proposals_are_deterministic_and_make_no_requests():
    a, f = run({"/": HOME})
    n = len(f.calls)
    b = propose_fixes(audit(O, f), capture_baseline(O, f))
    assert [(x.id, x.content, x.diff) for x in a.fixes] == [(x.id, x.content, x.diff) for x in b.fixes]
```
(The `propose_fixes` module must import the generators by name, as `from .generate import build_llms_txt, ...`, so the monkeypatch test works.)
- [ ] **Steps 2–4:** run, expect FAIL; implement; run to pass; commit `feat(fixes): propose valid fixes with diffs, skip the rest with reasons`.

---

### Task 6: Approval queue, export gate and the no-write-path test

**Files:** Create `queue.py`. Tests: `backend/tests/test_fixes_queue.py`, `backend/tests/test_fixes_no_write_path.py`.

**Interfaces:**
- Produces: `FixQueue(fixes: list[Fix])` with `list() -> list[Fix]`, `get(fix_id) -> Fix` (raises `UnknownFix`), `approve(fix_id) -> Fix`, `reject(fix_id) -> Fix`, `export(fix_id) -> ExportBundle`; exceptions `UnknownFix(KeyError)`, `NotApproved(Exception)`, `InvalidTransition(Exception)`; `summary(fix) -> dict` (id, kind, path, filename, description, finding_id, status, diff, sha256 of the content) **with no `content` key**.
- State machine: `proposed → approved` (records `approved_sha256 = sha256(content)`), `proposed → rejected`, `approved → rejected` (revokes: clears `approved_sha256`). `approve` on an approved fix is a no-op that returns it; `approve` on a rejected fix raises `InvalidTransition`; `reject` on a rejected fix is a no-op.
- `export(fix_id)` raises `NotApproved` unless **all** hold: status is approved; `sha256(content) == approved_sha256`; the content passes the validator for its kind again (the queue is given a `validators` mapping at construction, `{FixKind: callable(content) -> list[str]}`, so `queue.py` imports no generator); `find_secrets(content) == []`. Otherwise it returns `ExportBundle(fix_id, filename, content, diff, sha256, note)`. `note` states: "Review before you deploy. AgentReady has not changed your site." For `json_ld` add: "Apply the snippet to your page template; the diff is against the HTML as fetched."
- Nothing in `queue.py` or any other `app/fixes` file imports `httpx`, `requests`, `urllib.request`, `urllib3`, `http.client`, `socket`, `ftplib`, `smtplib`, `subprocess`, `shutil`, `os`, or calls the builtin `open`, `Path.write_text`, `Path.write_bytes` or `os.system`. (Exception: none. The generated stub text contains `urllib.request` only inside a string literal.)

- [ ] **Step 1: Write the failing tests**
```python
import pytest
from app.fixes.models import Fix, FixKind, FixStatus
from app.fixes.queue import FixQueue, InvalidTransition, NotApproved, UnknownFix, summary


def fix(kind=FixKind.LLMS_TXT, content="# Acme\n> hi\n"):
    return Fix(id=kind.value, kind=kind, path="/llms.txt", filename="llms.txt", description="d",
               content=content, diff="--- /dev/null\n+++ b/llms.txt\n+# Acme\n", finding_id="llms_txt")


def queue(*fixes, validators=None):
    return FixQueue(list(fixes) or [fix()], validators or {FixKind.LLMS_TXT: lambda c: [], FixKind.JSON_LD: lambda c: [],
                                                          FixKind.MCP_STUB: lambda c: []})


def test_a_proposed_fix_cannot_be_exported():
    with pytest.raises(NotApproved):
        queue().export("llms_txt")


def test_approval_exports_nothing_and_exposes_no_content():
    q = queue()
    result = q.approve("llms_txt")
    assert result.status is FixStatus.APPROVED
    assert "content" not in summary(result) and "# Acme" not in str(summary(result).get("description", ""))
    assert summary(result)["status"] == "approved" and summary(result)["diff"]


def test_export_after_approval_returns_the_file_and_the_diff():
    q = queue()
    q.approve("llms_txt")
    b = q.export("llms_txt")
    assert (b.filename, b.content) == ("llms.txt", "# Acme\n> hi\n") and b.diff and len(b.sha256) == 64
    assert "has not changed your site" in b.note


def test_rejected_fixes_cannot_be_exported_or_reapproved():
    q = queue()
    q.reject("llms_txt")
    with pytest.raises(NotApproved):
        q.export("llms_txt")
    with pytest.raises(InvalidTransition):
        q.approve("llms_txt")


def test_revoking_an_approval_disables_export():
    q = queue()
    q.approve("llms_txt")
    q.reject("llms_txt")
    with pytest.raises(NotApproved):
        q.export("llms_txt")


def test_content_changed_after_approval_is_refused():
    q = queue()
    q.approve("llms_txt")
    q.get("llms_txt").content = "# Changed\n"
    with pytest.raises(NotApproved):
        q.export("llms_txt")


def test_export_revalidates_and_scans_for_secrets():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    q = queue(fix(content=f"# Acme\n> key {secret}\n"))
    q.approve("llms_txt")
    with pytest.raises(NotApproved):
        q.export("llms_txt")
    q2 = queue(validators={FixKind.LLMS_TXT: lambda c: ["bad"]})
    q2.approve("llms_txt")
    with pytest.raises(NotApproved):
        q2.export("llms_txt")


def test_unknown_ids_and_idempotent_transitions():
    q = queue()
    with pytest.raises(UnknownFix):
        q.approve("nope")
    q.approve("llms_txt")
    assert q.approve("llms_txt").status is FixStatus.APPROVED
    assert [f.id for f in q.list()] == ["llms_txt"]
```
`test_fixes_no_write_path.py`:
```python
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "app" / "fixes"
BANNED = {"httpx", "requests", "urllib.request", "urllib3", "http.client", "socket", "ftplib", "smtplib",
          "subprocess", "shutil", "os", "pathlib", "aiohttp", "websockets"}


def files():
    found = sorted(PKG.glob("*.py"))
    assert found, "the fixes package must exist"
    return found


def test_the_fixes_package_imports_no_network_file_or_process_library():
    for f in files():
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert n not in BANNED and n.split(".")[0] not in BANNED - {"urllib.request"}, f"{f.name} imports {n}"


def test_the_fixes_package_never_opens_or_writes_files():
    for f in files():
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                assert name not in {"open", "write_text", "write_bytes", "system", "popen", "exec", "eval", "compile"}, f"{f.name} calls {name}"
```
(`pathlib` and `os` are banned for the package; use none of them. `compile` is banned too: the validators use `ast.parse`, never `compile` or `exec`.)
- [ ] **Steps 2–4:** run, expect FAIL; implement; run to pass; commit `feat(fixes): approval queue and export gate`.

---

### Task 7: API endpoints, baseline capture in `/audit`, docs

**Files:** Modify `backend/app/main.py`. Create `docs/fixes.md`. Test: `backend/tests/test_api_fixes.py`.

**Interfaces:**
- Consumes everything above and the existing `_reports`, `build_audit_fetcher`.
- Changes to `POST /audit` (the only Phase 1 behaviour touched): after `audit(...)`, call `capture_baseline(ref.origin, fetcher)` with the **same** cached fetcher (cache hits, no new site requests for pages the audit already read) and store it in `_baselines[rid]`. Evict `_baselines` and `_queues` together with `_reports` (the same `MAX_REPORTS` loop). The response body of `POST /audit` is unchanged.
- New endpoints (all 404 for an unknown report id):
  - `POST /report/{rid}/fixes`: runs `propose_fixes(report, baseline)` once per report (a second call returns the existing queue and never resets decisions). Response: `{"fixes": [summary(f) ...], "skipped": [{"kind", "reason"}]}`. Each summary includes the diff and no `content`.
  - `GET /report/{rid}/fixes`: same body; 404 `"No fixes proposed yet"` before the POST.
  - `POST /report/{rid}/fixes/{fid}/approve` and `.../reject`: return the summary; 404 for an unknown fix; 409 for `InvalidTransition`.
  - `GET /report/{rid}/fixes/{fid}/export`: 409 with `"Approve this fix before exporting it"` when `NotApproved`; otherwise `{"filename", "content", "diff", "sha256", "note"}`.
- The validators mapping given to `FixQueue` is built from `validate_llms_txt(text, origin)`, `validate_json_ld_snippet`, `validate_mcp_stub(src, spec, origin)` (the spec comes from the baseline).
- `docs/fixes.md`: what each fix is and why; that generation reads only the audit's pages and uses no model; the approval model (bearer report id, in-memory, restart loses queues); that approval exports nothing and the app never deploys; that the UI must escape every site-derived string (diff, description) before display; the JSON-LD "apply to your template" caveat; the stub is read-only GET and not counted in task success.

- [ ] **Step 1: Write the failing tests** (follow the fixture pattern in `tests/test_api.py` for faking the fetcher, the TXT lookup and the resolver; verify control with the meta tag the same way)
```python
# Setup: monkeypatch build_audit_fetcher / build_probe_fetcher as test_api.py does, serve HOME with the
# verification meta tag, call POST /audit, keep rid.

def test_audit_response_is_unchanged_and_baseline_capture_uses_the_cache(...):
    # the audit's fetcher is a CachedFetcher over a counting fake: after POST /audit the underlying call
    # count must not grow by more than the 5 baseline paths that were never cached (404s are cached too)
    ...

def test_propose_then_review_then_approve_then_export(...):
    r = client.post(f"/report/{rid}/fixes").json()
    assert {f["id"] for f in r["fixes"]} == {"llms_txt", "json_ld"} and all("diff" in f and "content" not in f for f in r["fixes"])
    assert client.get(f"/report/{rid}/fixes/llms_txt/export").status_code == 409          # not approved
    assert client.post(f"/report/{rid}/fixes/llms_txt/approve").json()["status"] == "approved"
    e = client.get(f"/report/{rid}/fixes/llms_txt/export").json()
    assert e["filename"] == "llms.txt" and e["content"].startswith("# ") and e["diff"]
    assert client.get(f"/report/{rid}/fixes/json_ld/export").status_code == 409          # other fix still unapproved

def test_propose_twice_never_resets_a_decision(...)
def test_reject_after_approve_blocks_export_and_reapprove_is_409(...)
def test_unknown_report_or_fix_is_404_and_get_before_post_is_404(...)
def test_an_unverified_site_still_gets_no_report_and_so_no_fixes(...)   # POST /audit 403, so no rid exists
def test_exported_content_passes_the_secret_scan_for_every_fix_of_a_realistic_site(...)
def test_no_endpoint_makes_a_request_to_the_site(...)                  # count fake fetcher calls before and after POST /fixes and approve/export: unchanged
```
Write each test fully against the pattern in `tests/test_api.py` (the stubs above name the behaviour; the implementer completes them with real assertions and must not leave `...` bodies).
- [ ] **Steps 2–4:** run, expect FAIL; implement; run the whole suite (all earlier tests must still pass); commit `feat(fixes): fix endpoints and baseline capture`.
- [ ] **Step 5: Real check (no mocks of the fix code).** Start the real app with `AGENTREADY_DEV=1`, point it at a local static site served by a real HTTP server **only if** the guard is relaxed by the existing test hooks; otherwise run `propose_fixes` and the queue on the HTML of a real public page fetched once with `curl` (for example `https://example.com/`), saved in the scratchpad, and print each fix, its diff and the export. Confirm that the exported files pass `find_secrets`, that `llms.txt` links stay on the host, and that an unapproved export is refused. Report what you ran.
- [ ] **Step 6:** `git push -u origin watcher/agentready-fix-generator` and open the PR with `gh pr create --base main` (ready, not draft). Do not merge.

---

## Self-review (against the PRD and the writing-plans checklist)
- **Spec coverage:** FR-6.1 (generate + validate, invalid never presented): Tasks 3, 4, 5 (`test_an_invalid_output_is_never_presented`). FR-6.2 (diff against the baseline from the before snapshot): Task 2 (baseline from the audit's cached fetcher), Task 5 (diffs), Task 7 (diff in every response). FR-6.3 (approve then export, approval exports nothing, no deploy, no write path, secret scan): Task 6 (`test_approval_exports_nothing...`, `test_a_proposed_fix_cannot_be_exported`, the AST no-write-path test, the export-time secret scan), Task 7 (409 on unapproved). FR-6.4 (stub validated, not counted): Task 4 validation; nothing touches the runner or results.
- **Boundary:** generation makes no request; baseline capture reuses the guarded, cached fetcher; the package cannot import a network library (test).
- **Injection:** site text is cleaned, links are restricted to the verified host, queries and fragments are dropped, `</script>` cannot close the snippet, spec text is embedded as an escaped string literal and the stub's `BASE_URL` is the verified origin (Tasks 3 and 4 tests).
- **Placeholder scan:** Task 7's API tests are named with their key assertions and the implementer must complete them; no TBD. Implementation code is left to the tests plus the rules; this plan gives the contract, which earlier reviews showed is where the real defects hide.
- **Type consistency:** `Fix`, `FixKind`, `FixStatus`, `Skipped`, `Proposal`, `ExportBundle` (Task 2) are used unchanged in Tasks 5 to 7. `validate_llms_txt(text, origin)`, `validate_json_ld_snippet(snippet)`, `validate_mcp_stub(source, spec, origin)` signatures match their callers in Tasks 5 and 7.
- **Known risks:** the JSON-LD diff is against fetched HTML, not a template; the bearer-report-id model has no accounts; the in-memory store loses queues on restart; the `/audit` capture adds up to 5 cache-hit reads (404s are cached, 5xx and failures are refetched within the same guard budget).

## Decisions to confirm
1. Deterministic generators, no LLM (assumed).
2. Bearer report id as the approval credential for the MVP (assumed).
3. Whether the owner also wants a "download all approved" action. Not in this plan.

---

## Corrections found while building (the code and tests follow these, not the text above)
1. `test_site_text_cannot_inject_headings_or_links`: asserting `"evil.test"` is absent from the link lines was wrong, because harmless plain link text can contain it. The test checks link *targets* (only `https://x.test/a`) and that the only `## ` line is `## Pages`.
2. `test_snippet_cannot_be_broken_out_of`: a raw `</script>` inside `<title>` is parsed as a tag by `html.parser`, so the title is entity-encoded in the test, and a second test calls `json_ld_snippet` directly with `</script>`, `&` and U+2028 in the data.
3. `test_json_ld_diff_inserts_before_head_close_and_leaves_the_rest`: the `and ... or ...` assertion could never fail, and "no removed lines" cannot hold for one-line (minified) HTML. The clean-insert test uses a multi-line page; a separate test covers minified HTML, where the one long line is shown replaced.
4. `test_fixes_no_write_path.py`: banning any call named `compile` flagged `re.compile`; builtins (`open`, `exec`, `eval`, `compile`, `__import__`) are now banned by bare name and file or process methods by attribute. `from urllib import request` also slipped past the import check; imported names are now joined with their module. The checker has its own test.
5. Additions beyond the plan: an `/llms.txt` that could not be fetched is skipped with "could not reach" instead of being proposed as missing; `find_secrets` also scans the diff (it carries lines of the site's own HTML), at proposal and at export; a diff over 100,000 characters is skipped ("too large to review"); `insert_before_head_close` puts the snippet on its own lines when `</head>` starts a line and splits the line otherwise; link URLs are percent-encoded so a Markdown link cannot be broken by spaces or parentheses and are rewritten to the origin's scheme; OpenAPI paths that are not plain URL paths and `$ref` parameters are skipped.
