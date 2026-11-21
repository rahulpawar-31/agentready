# Fixes: what AgentReady proposes, and what it never does

AgentReady turns an audit report into up to three proposed fixes. The owner reviews each one as a diff, approves
the ones they want, and then downloads or copies them. AgentReady never deploys a fix and never writes to a site.

## The three fixes

| Fix id | What it is | Proposed when |
|---|---|---|
| `llms_txt` | A short Markdown `llms.txt` (title, one-line summary, up to 10 on-site links), built from the home page. | The audit did not find a non-empty `/llms.txt`. |
| `json_ld` | A schema.org `WebSite` JSON-LD `<script>` snippet (name, URL, description), built from the home page. | The audit found no valid JSON-LD on the home page, and the page has a `</head>` to patch. |
| `mcp_stub` | A read-only Python stub with one tool per GET operation in the site's OpenAPI document. | An OpenAPI document was found at a standard path and has at least one GET operation. |

Any kind that is not proposed is listed under `skipped` with a reason (for example "llms.txt is already present").

## How they are made

- **No network and no model.** Generation reads only the pages the audit already fetched (the "baseline",
  captured from the audit's cache snapshot). It is deterministic: the same pages give the same fixes. Nothing is
  invented: only what the page says is used.
- **Site text is untrusted.** Titles, descriptions, link text and OpenAPI summaries are cleaned (control characters
  removed, one line, clipped) before use. In `llms.txt` the characters `[ ] ( ) < >` are removed, links must point to
  the verified host, and query strings and fragments are dropped (they can carry session tokens). In the JSON-LD
  snippet `<`, `>` and `&` are escaped so the text cannot close the script tag. In the stub, spec text is embedded as
  an escaped string literal, never as code, and `BASE_URL` is always the verified origin, never a `servers` URL from
  the spec.
- **Validated before anyone sees it.** `llms.txt` must have the right structure and only on-site links. The JSON-LD
  must be one `application/ld+json` script that parses and has `@context` and `@type`. The stub must parse, run no
  code at import, match the spec's GET operations, and equal the generator's output exactly. An output that fails,
  or contains a secret-like value (API keys, tokens, private keys, `KEY=value` lines, passwords in URLs), is never
  presented; it appears under `skipped` with a reason that never contains the value.
- **Diffs.** Each fix is shown as a unified diff against the baseline: a new file for `llms.txt` and the stub, and
  the home page HTML with the snippet inserted before `</head>` for JSON-LD. The JSON-LD diff is against the HTML as
  fetched, which may come from a template: apply the snippet to your template.

## The approval model

1. `POST /report/{id}/fixes` proposes the fixes (once per report; calling it again never resets a decision).
2. The owner reviews each diff (`GET /report/{id}/fixes`).
3. `POST /report/{id}/fixes/{fix}/approve` (or `.../reject`). **Approval exports nothing.** A rejected fix cannot be
   approved again; rejecting an approved fix revokes the approval.
4. `GET /report/{id}/fixes/{fix}/export` returns the file only for an approved fix. It checks again that the content
   is unchanged since approval, passes validation, and contains no secret-like value. Otherwise it answers 409.
5. The owner deploys the file themselves. The code that builds fixes imports no network, file or process library
   (a test enforces this), so no code path can write to a site.

The MVP has no user accounts. The report id (a random 128-bit value, returned only to the requester who proved
control of the site) is the credential for approving and exporting. The queue is held in memory with the reports
(the latest 200) and is lost on restart.

## Limits

- The stub is read-only (GET) and is not counted in the benchmark's task success.
- Parameters given as `$ref` in the OpenAPI document are not followed; operations with unusual paths are skipped.
- A UI or MCP client must escape every site-derived string (diff, description) before display.
