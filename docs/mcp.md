# AgentReady MCP server

AgentReady exposes three tools over MCP so an AI agent can audit a site and ask for fix proposals. The tools call the
same functions as the REST API, so they go through the same proof-of-control gate, the same guarded fetcher and the
same in-memory stores. The MCP server runs inside the same process as the REST API: an agent and the site owner see
the same reports and the same fix queues.

## Run and connect

From `backend/`:

    AGENTREADY_SECRET=choose-a-long-random-value uvicorn app.main:app --port 8000

The MCP endpoint is `http://localhost:8000/mcp` (streamable HTTP, exactly `/mcp`, no redirect). Any MCP client that
speaks streamable HTTP can connect. For Claude Code the command is:

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

Errors are JSON with a fixed `error` code: `control_not_proven`, `invalid_request`, `not_found` or `failed` (a fixed
message; exception text is never sent to the client).

## What an agent cannot do

There is **no tool to approve, reject or export a fix**, on purpose. Approval is the site owner's decision, made in
the web interface or the REST API (`POST /report/{id}/fixes/{fix}/approve`, then `GET .../export`). An agent that
could approve its own proposals would remove the human step that FR-6.3 exists for. A test pins the tool list to the
three names above, and no tool result contains a `content` key in its JSON (the fix file itself is only available through the REST export).

Note that a new file's diff shows the whole file, because the owner needs to review it. The diff is the review
material; the gated step is the export (download or copy), and that is not available through MCP at all.

## Safety model

- **Same gate.** `audit_site` calls `post_audit`, the REST route function, with the same dependencies. A host that
  resolves to a non-public address is refused before any request; an unverified site gets only the verification probe.
- **No new network path.** The tools add no fetching of their own. Site requests go through the existing guard (verified
  host only, redirects rechecked, pinned public addresses, rate limit, request budget). `get_report` and
  `propose_fixes` make no site requests.
- **Site text is untrusted.** Evidence strings and diffs contain text taken from the audited website. Every result
  carries `untrusted_content_notice`, evidence is clipped to 500 characters and each diff to 8,000 characters (the
  fix then has `diff_truncated: true`; the full diff is in the REST API). The tool descriptions and the server
  instructions say the same. This reduces, but cannot remove, the risk that a hostile site tries to instruct an agent
  through the data it reports: an agent client should still treat these fields as data.
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
