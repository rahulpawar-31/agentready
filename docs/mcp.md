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
| `audit_site(url, snapshot="default")` | Audits a site. **Requires proof of control** (a DNS TXT record or a meta tag the owner adds). An unverified site gets a tool error with code `control_not_proven` and the verification instructions, exactly the information the REST API puts in its 403. Returns the score, the findings (URL, check, evidence, confidence, status) and a `report_id`. Takes about 10 seconds. At most one audit per host at a time and at least 5 seconds between starts (`rate_limited`), at most four audits running at once (`busy`). |
| `get_report(report_id)` | Returns a stored report. Read-only. |
| `propose_fixes(report_id)` | Proposes an `llms.txt`, a JSON-LD snippet and an OpenAPI-to-MCP stub (when the site has an OpenAPI document), each with a diff, plus what was skipped and why. Safe to call again: it never resets a decision. Each fix has a `status` (`proposed`, `approved`, `rejected`) that shows the owner's decision. |

Errors from our code are JSON with a fixed `error` code: `control_not_proven`, `invalid_request`, `not_found`, `busy`,
`rate_limited` (with `retry_after_seconds`) or `failed` (a fixed message; exception text is never sent to the client). The SDK sends them as
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
  resolves to a non-public address is refused before any request. An unverified site gets only the verification probe,
  and that probe goes to the **requested** host before control is proven: one DNS TXT lookup, one DNS resolve and one GET
  of its home page. Once control is proven, the audit's requests go only to that verified host.
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
- **Host and origin checks (DNS-rebinding protection) are on, and exact.** AgentReady checks the `Host` header (exactly
  one is required) and, when one is sent, the `Origin` header **before** the SDK's own check, and the SDK's check stays
  on as well. The SDK matches by prefix (`localhost:8000@evil.test` would pass it); ours matches the whole value. By
  default `Host` must be `localhost`, `127.0.0.1` or `[::1]` followed by `:` and a numeric port (1 to 65535, no leading zero), and an
  `Origin`, if sent, must be `http://` plus one of those names and a numeric port. Uppercase names, a missing port,
  `Origin: null`, paths, and user information are refused (421 for `Host`, 403 for `Origin`). To serve the app under
  another name set `AGENTREADY_MCP_ALLOWED_HOSTS` and `AGENTREADY_MCP_ALLOWED_ORIGINS` (comma separated; they
  **replace** the defaults). An entry ending in `:*` means exactly that name plus any numeric port; any other entry
  must match the whole header exactly. A deployment on port 80 or 443 sends `Host: name` with no port, so list the bare
  name (`AGENTREADY_MCP_ALLOWED_HOSTS=agentready.example.com`) and, if a browser client is used, the origin
  (`AGENTREADY_MCP_ALLOWED_ORIGINS=https://agentready.example.com`). Request bodies are limited to 64 KB.
- **Bound to localhost by default.** `uvicorn app.main:app --port 8000` listens on 127.0.0.1 only (a test checks
  uvicorn's default). The run command above keeps that; passing `--host 0.0.0.0` is a choice to expose the endpoint,
  and then the Host/Origin lists and your own authentication in front of it are what protect it.
- **Logs do not carry audit data.** Building the server leaves the root logger exactly as it was (the SDK would
  otherwise switch it to INFO and add a handler), and the `mcp`, `httpx` and `httpcore` loggers are held at WARNING, so
  the ownership token in a refused audit's error and the request paths a site chooses (a redirect `Location`) are not
  written to INFO logs, even if you turn INFO on for your own loggers. Our own warning on an unexpected failure logs only
  the exception class name. WARNING and above from those libraries are still logged.
- **Same trust level as `POST /audit`.** There are no accounts in the MVP. Anyone who can reach the endpoint can ask for
  an audit of a site whose control has been proven (the ownership token is public by design: it is only useful to
  someone who can edit the site). Keep the server on localhost, or put your own authentication in front of it.
- **The `report_id` is a credential.** Whoever holds it can read the report and call `propose_fixes`; with the REST API
  they could also approve and export. It is returned to the calling agent and appears in URLs and logs of the REST API.
  Treat it like a secret.
- **Audits are limited so an agent cannot hammer a site or starve the server.**
  - *Per host:* at most one audit in flight per host (normalised host and port) and at least 5 seconds between two starts
    of the same host; a call inside that window gets `rate_limited` with `retry_after_seconds`. The throttle is applied
    **before** the verification probe, so an agent cannot use unverified hosts to loop probes: a refused call makes no
    DNS lookup and no request. Calls with an invalid URL or snapshot make no request and do not use a host's turn. The
    table of hosts is bounded (1,000; the oldest are forgotten). This limit is in the MCP adapter only; `POST /audit`
    behaves as before.
  - *Concurrency:* at most four audits run at once. A fifth call waits up to 2 seconds for a slot and then gets `busy`
    (and does not use up its host's turn). Audits wait for their slot on the event loop, not in a thread, so the shared
    worker-thread pool that every REST route and the other tools use stays free (a test runs 48 slow audits and checks a
    REST call and `get_report` stay fast).
  - *Not limited:* a global rate across all hosts beyond these, and the number of snapshot names (see Limits).
- **No sessions are kept.** The transport is stateless: every request stands alone, so nobody can open thousands of
  anonymous sessions to use up memory, and a server restart loses nothing an agent holds (only the in-memory reports,
  see Limits).
  `/mcp` answers `POST` only: `GET` (the optional server-to-client event stream, which the SDK would keep open until the
  client leaves), `DELETE`, `PUT`, `HEAD` and `OPTIONS` get `405` with `Allow: POST`, so an unauthenticated caller cannot
  hold connections open. (The Host and Origin check runs first, so a foreign host still gets 421.)

## Limits

- Reports and fix queues are in memory, shared with the REST API, and lost on restart. At most 200 reports are kept.
- One process: the MCP endpoint and the REST API share their stores because they run together. Running the MCP server as a
  separate process (for example over stdio) would give it an empty store, so it is not offered.
- The MCP tools return the same data as the REST API, clipped. They do not stream progress.
- Deferred, by decision: a cap on the number of snapshot directories; a global rate limit across all hosts beyond the
  per-host throttle and the four-audit limit (and nothing is shared between processes: the limits are per process); the
  SDK's INFO traceback for a client's own bad prompt name (the server has no prompts, and the line is at INFO, which
  is off).
- Each new `snapshot` name creates its own on-disk cache directory, and there is no limit on how many (a cap is
  deferred). Re-auditing the **same** snapshot returns the cached pages, not a fresh look at the site: after the owner
  deploys a fix, audit again with a **new** snapshot name (for example `after`), otherwise the report describes the old
  pages.
