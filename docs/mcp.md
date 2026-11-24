# AgentReady MCP server

AgentReady exposes three tools over MCP so an AI agent can audit a site and ask for fix proposals. The tools call the
same functions as the REST API, so they go through the same proof-of-control gate, the same guarded fetcher and the
same in-memory stores. The MCP server runs inside the same process as the REST API: an agent and the site owner see
the same reports and the same fix queues.

## Run and connect

From `backend/`:

    AGENTREADY_SECRET=choose-a-long-random-value uvicorn app.main:app --port 8000

The MCP endpoint is `http://localhost:8000/mcp` (streamable HTTP). Any MCP client that speaks streamable HTTP can
connect.

The endpoint is one ordinary route added after the REST routes, not a mount, so every other path and every
wrong-method or trailing-slash request is answered by the REST API exactly as it was before MCP existed (405 with an
`Allow` header, 307 redirects, the JSON 404). `/mcp/` therefore redirects (307) to `/mcp`, like `/audit/` does.
The MCP server and its session manager are created when the application starts (its lifespan) and are rebuilt every
time it starts, so the app can be started more than once in a process (tests do). A request to `/mcp` while the
lifespan is not running gets a 503 with a short JSON message. For Claude Code the command is:

    claude mcp add --transport http agentready http://localhost:8000/mcp

That command has **not** been run against a real Claude Code install for this project (it edits the user's Claude
configuration). What was tested: the official MCP Python client, over real HTTP, against a real uvicorn server
(`tests/test_mcp_http.py` and the end-to-end run described in the pull request).

## The tools

| Tool | What it does |
|---|---|
| `audit_site(url, snapshot="default")` | Audits a site. **Requires proof of control** (a DNS TXT record or a meta tag the owner adds). An unverified site gets a tool error with code `control_not_proven` and the verification instructions, exactly the information the REST API puts in its 403. Returns the score, the findings (URL, check, evidence, confidence, status) and a `report_id`. Takes about 10 seconds. |
| `get_report(report_id)` | Returns a stored report. Read-only. |
| `propose_fixes(report_id)` | Proposes an `llms.txt`, a JSON-LD snippet and an OpenAPI-to-MCP stub (when the site has an OpenAPI document), each with a diff, plus what was skipped and why. Safe to call again: it never resets a decision. Each fix has a `status` (`proposed`, `approved`, `rejected`) that shows the owner's decision. |

Errors from our code are JSON with a fixed `error` code: `control_not_proven`, `invalid_request`, `not_found` or
`failed` (a fixed message; exception text is never sent to the client). The SDK sends them as
`Error executing tool <name>: {json}`, so parse from the first `{`. Errors raised by the SDK itself before our code runs
(an argument of the wrong type or a missing argument, an unknown tool name) are the SDK's plain text, not our JSON; they
echo only the caller's own input.

## What an agent cannot do

There is **no tool to approve, reject or export a fix**, on purpose. Approval is the site owner's decision, made in
the web interface or the REST API (`POST /report/{id}/fixes/{fix}/approve`, then `GET .../export`). An agent that
could approve its own proposals would remove the human step that FR-6.3 exists for. A test pins the tool list to the
three names above.

What this does and does not hide: no tool result has a `content` key, and the content hash is left out of the MCP
fix summaries. But the **diff of a new file is the file**: for `llms.txt` and the MCP stub every line is a `+` line, so
an agent can reconstruct the text from the diff (up to the 8,000-byte clip; the full diff is in the REST API). That is
by design, because the owner has to review exactly what would be exported. The gate controls the **export** (the
download or copy action in the REST API), which MCP does not offer at all. It does not stop an agent from reading what
was proposed.

## Safety model

- **Same gate.** `audit_site` calls `post_audit`, the REST route function, with the same dependencies. A host that
  resolves to a non-public address is refused before any request; an unverified site gets only the verification probe.
- **No new network path.** The tools add no fetching of their own. Site requests go through the existing guard (verified
  host only, redirects rechecked, pinned public addresses, rate limit, request budget). `get_report` and
  `propose_fixes` make no site requests.
- **Site text is untrusted.** Every string in a result can come from, or be derived from, the audited website: the
  evidence, but also the `url` of a finding (a redirect `Location` header from the site ends up there), the check text,
  fix descriptions and diffs. Every result carries `untrusted_content_notice` saying so. Every string field is clipped
  to 300 bytes (UTF-8, never half a character) and a diff to 8,000 bytes (`diff_truncated: true`; the full diff is in
  the REST API). The whole result is capped at 60,000 bytes: past that, finding evidence is dropped first, then lists
  are shortened, and `result_truncated: true` is set. The tool descriptions and the server instructions say the same.
  Clipping bounds the size; it cannot stop a short injection from fitting in 300 bytes. This reduces, but cannot
  remove, the risk that a hostile site tries to instruct an agent through the data it reports: an agent client should
  still treat these fields as data.
- **Host and origin checks (DNS-rebinding protection) are on.** By default only `localhost:*`, `127.0.0.1:*` and
  `[::1]:*` are accepted as `Host`, and `http://localhost:*` and `http://127.0.0.1:*` as `Origin`. Set
  `AGENTREADY_MCP_ALLOWED_HOSTS` and `AGENTREADY_MCP_ALLOWED_ORIGINS` (comma separated) to replace those lists when you
  serve the app under another name. Request bodies are limited to 64 KB.
- **Same trust level as `POST /audit`.** There are no accounts in the MVP. Anyone who can reach the endpoint can ask for
  an audit of a site whose control has been proven (the ownership token is public by design: it is only useful to
  someone who can edit the site). Keep the server on localhost, or put your own authentication in front of it.
- **The `report_id` is a credential.** Whoever holds it can read the report and call `propose_fixes`; with the REST API
  they could also approve and export. It is returned to the calling agent and appears in URLs and logs of the REST API.
  Treat it like a secret.
- **A slow audit does not block anything else.** The blocking work runs in a worker thread.

## Limits

- Reports and fix queues are in memory, shared with the REST API, and lost on restart. At most 200 reports are kept.
- One process: the MCP endpoint and the REST API share their stores because they run together. Running the MCP server as a
  separate process (for example over stdio) would give it an empty store, so it is not offered.
- The MCP tools return the same data as the REST API, clipped. They do not stream progress.
