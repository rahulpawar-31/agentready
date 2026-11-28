# Fixes: what AgentReady proposes, and what it never does

AgentReady turns an audit report into up to three proposed fixes. The owner reviews each one as a diff, approves
the ones they want, and then downloads or copies them. AgentReady never deploys a fix and never writes to a site.

## The three fixes

| Fix id | What it is | Proposed when |
|---|---|---|
| `llms_txt` | A short Markdown `llms.txt` (title, one-line summary, up to 10 on-site links), built from the home page. | The audit did not find a non-empty `/llms.txt`, or `/llms.txt` returns an HTML page (a soft 404, noted on the fix). |
| `json_ld` | A schema.org `WebSite` JSON-LD `<script>` snippet (name, URL, description), built from the home page. | The audit found no valid JSON-LD on the home page, and the page has a real `</head>` to patch. |
| `mcp_stub` | A read-only Python stub with one tool per GET operation in the site's OpenAPI document. | An OpenAPI document was found at a standard path and has at least one GET operation. |

Any kind that is not proposed is listed under `skipped` with a reason (for example "llms.txt is already present",
"page too large to process", "contains a secret-like value (stripe key)", "could not be generated"). A reason never
contains the value that caused it.

## How they are made

- **No network and no model.** Generation reads only the pages the audit itself fetched (the "baseline"). `POST
  /audit` wraps the audit's fetcher in a recorder and builds the baseline from the pages the audit saw, so building
  it makes no request at all. A path the audit could not reach (a failed request, status 0) is simply absent, and
  one that answered with an error status (for example a 503) gives no fix: it is never fetched again. Generation is
  deterministic: the same pages give the same fixes. Nothing is invented: only what the page says is used.
- **Never a 500.** A hostile or unusual page or OpenAPI document (a deeply nested JSON document, an OpenAPI 3.1
  `type` list, tens of thousands of nested links) cannot make the endpoint fail: any problem is reported as every
  kind skipped with the reason "could not be generated", and the report is kept. A home page over 500,000
  characters or 100,000 tags is skipped as "page too large to process", and at most the first 2,000 links on a page
  are examined. A proposal runs outside the lock that guards the stores, so a slow page for one report does not
  delay another.
- **Site text is untrusted.** Titles, descriptions, link text and OpenAPI summaries are cleaned before use:
  invisible characters (zero-width, bidirectional controls, byte-order marks) are removed, control characters and
  unusual spaces become one ordinary space, text is clipped. In `llms.txt` the characters that could start a link,
  image, emphasis, code span or HTML tag (square brackets, parentheses, angle brackets, backticks, asterisks,
  underscores, exclamation marks and tildes) and bare web addresses, `www.` hosts and email
  addresses are removed, so Markdown cannot turn site text into a link. A link is kept only when its scheme, host
  and port are exactly those of the verified site (an explicit `:443` or `:80` counts as the bare host; an `http`
  link on an `https` site is dropped, never rewritten); query strings and fragments are dropped, because they can
  carry session tokens. In the JSON-LD snippet `<`, `>` and `&` are escaped, so the text cannot close the script
  tag. In the stub, spec text is embedded as an escaped string literal, never as code, and `BASE_URL` is always the
  verified origin, never a `servers` URL from the spec. The page title is the `<title>` inside `<head>`, not one
  inside an inline SVG.
- **Secrets.** Text from the site is checked for secret-like values as the site wrote it, before any clean-up.
  The generated content and the entire displayed/exported diff, including existing context and removed lines, are
  checked again. The scanner recognises private key blocks, AWS, GitHub, Slack, Stripe, Google, Twilio, SendGrid and
  npm credentials, `sk-` keys, JWTs, `NAME=value` and `"name": "value"` assignments for names containing password,
  secret, token, key or credential, Bearer and Basic authorization headers, and passwords inside URLs, also after URL
  decoding and with invisible characters removed. It is a heuristic and not a guarantee: other formats can pass.
  A fix whose content or diff looks like it contains a secret is skipped; a link whose text or address looks like a
  secret is left out. This is conservative: even a public JWT in diff context blocks that patch. Secret-like text
  elsewhere on the existing page, outside the diff, carries a warning without quoting the value. Export scans the
  entire diff again. Responses resolving to `/.env` or `/.git/` are excluded from fix baselines. Scanners return
  labels only, never values.
- **Validated before anyone sees it.** `llms.txt` must have the right structure and only on-site links. The JSON-LD
  must be one `application/ld+json` script that parses and has `@context` and `@type`. The stub must parse, run no
  code at import, match the spec's GET operations, and its code is inspected: every call, name and import in every
  function must be one the stub is expected to use, and the one request must be `Request(url, method="GET")` with a
  URL that starts from `BASE_URL` (it must also equal the generator's output exactly). An output that fails is never
  presented; it appears under `skipped`.
- **Diffs.** Each fix is shown as a unified diff against the baseline: a new file for `llms.txt` and the stub, and
  the home page HTML with the snippet inserted before the first real `</head>` for JSON-LD (a `</head>` inside a
  comment, `<script>`, `<style>`, `<textarea>` or `<title>` is ignored). Diffs split lines on `\n` only and mark a
  missing final newline in the standard way, so they apply with `patch` (tested with LF, CRLF, BOM, U+2028, U+0085,
  form feed, lone CR, no trailing newline and `</HEAD >`). Pages that are not UTF-8 are decoded with replacement
  characters, so the displayed diff of such a page is best effort. When the home page has a line longer than 2,000
  characters (a minified page) or the whole diff would be very large, the diff is an **excerpt**: it shows only the
  snippet and about 200 characters of context either side (`diff_kind: "excerpt"`, with a note), not the whole page.
  The JSON-LD diff is against the HTML as fetched, which may come from a template: apply the snippet to your
  template. For a new file (`llms.txt`, the stub) the diff already shows the whole file: the owner reads it before
  approving, by design.
- **The stub.** It does one GET against the verified origin per call, with path values URL-encoded. Redirects are
  refused (a redirect response raises an error; nothing is requested from another host or scheme), the response is
  read up to 1 MB, a path value of `.` or `..` and values that are lists, objects or null are refused before any
  request, and spec paths with dot segments (also percent-encoded) are not turned into tools. It is a stub: wire it
  into your own MCP SDK and review it first. It is not counted in the benchmark's task success.

## The approval model

1. `POST /report/{id}/fixes` proposes the fixes (once per report; calling it again never resets a decision).
2. The owner reviews each diff (`GET /report/{id}/fixes`). The review shows the diff, its kind, any `warning` and
   `note`, and a hash, with no separate `content` field.
3. `POST /report/{id}/fixes/{fix}/approve` (or `.../reject`). **Approval exports nothing.** A rejected fix cannot be
   approved again (the only way back is a new audit); rejecting an approved fix revokes the approval.
4. `GET /report/{id}/fixes/{fix}/export` returns the file only for an approved fix. It checks again that the content
   is unchanged since approval, passes validation against the OpenAPI document captured at audit time, and contains
   no secret-like value. Otherwise it answers 409. The export, a download or copy action, is the part that is only
   possible after approval; the diff a reviewer reads before approving already shows the content of a new file.
5. The owner deploys the file themselves. The code that builds fixes imports only a short allowlist of standard
   libraries and parsers (a test enforces this, and bans dynamic-import, file and process names), and the endpoints
   build no fetcher, so no code path in the fix feature writes to a site. That test is a tripwire for review, not
   a sandbox. Tests also run the endpoints with the network, fetcher construction and file writes disabled.

The MVP has no user accounts. The ownership token is available to anyone through `GET /ownership-token` (the
meta tag or DNS record proves control of the site, not who asks), so a report id is only issued to a requester after
the proof is in place. **The report id is the only credential for approving and exporting.** It appears in URLs and
may end up in access logs and browser history, so treat it like a secret. Per-user accounts are out of scope. The
queue is held in memory with the reports (the latest 200, evicted together) and is lost on restart; a report evicted
while a proposal is being made gets a 404 and leaves nothing behind.

## Limits

- The stub is read-only (GET) and is not counted in the benchmark's task success.
- Parameters given as `$ref` in the OpenAPI document are not followed; operations with unusual paths are skipped.
- Export validates against the baseline captured at audit time, not against the current site.
- A UI or MCP client must escape every site-derived string (diff, description, warning, note) before display.
