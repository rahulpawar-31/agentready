# AgentReady

A Lighthouse for AI agents: AgentReady takes a website URL, checks that you control it, tests whether an AI agent can find and retrieve information on it, audits how ready it is for agents, scores the result with evidence for every finding, and proposes fixes for the owner to approve.

## The problem it interprets (Homingo problem A1)

The wording, transcribed from the Homingo problem statement on 21 Nov 2026:

> "We want the tools that make agents first-class users: interfaces, identity, permissions, payments, memory, sandboxes and testing built for agents rather than people."

Re-check the quotation against the live page before submission, and remove it (do not reword it) if it differs.

AgentReady is our interpretation of A1. It covers interfaces, identity, permissions and testing; it does not address payments, memory or sandboxes.

## What it does

1. **Proves control first.** You add a meta tag or a DNS TXT record to the site. Nothing else is requested until that passes.
2. **Audits four areas** from the site's own pages: discoverability, operability, identity and permissions, safety. Every finding shows its URL, the check, what was observed and a confidence level. A check that finds nothing says "not detected", never "absent".
3. **Scores 0 to 100.** Pages that could not be reached are marked unreachable and left out of the score.
4. **Proposes fixes** (an `llms.txt`, a JSON-LD snippet, an OpenAPI-to-MCP stub). Each is validated and shown as a diff. The owner approves a fix, then downloads or copies the file and deploys it. AgentReady never deploys or writes to a site.

A task runner (one LLM agent, a fixed toolset) also tries three tasks on a site, judged by code against hand labels: find the main info, the contact form and the docs. It measures discovery and retrieval only.

## Safety model

- Proof of control comes before any audit; a host that resolves to a private or reserved address cannot be audited.
- All fetching goes through one guard: the verified host only, redirects re-checked, the connection pinned to a checked public address, read-only GETs, a rate limit and a request budget.
- Fixes are exported only after the owner approves them; approving exports nothing; exported files are scanned for secret-like values.
- Text taken from a site is untrusted: the web page shows it as text only, and the MCP server labels and clips it.
- An agent using the MCP server can audit and propose but cannot approve or export a fix.
- Secrets are never printed or stored by the tooling; the ownership token is derived from `AGENTREADY_SECRET`.

## Quick start

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Run the API and the web page (the API refuses to start without a secret, because a guessable default would make every ownership token predictable):

```bash
cd backend
export AGENTREADY_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/`: paste a URL, prove control, read the score, review the fixes as diffs, approve. The server listens on 127.0.0.1 only. For local development only, `AGENTREADY_DEV=1` uses a fixed development secret (the tests set it). `AGENTREADY_CACHE` sets the cache directory (default `.cache`).

Sites on a non-default port can only be verified with the meta tag (inside `<head>` on the home page), because a DNS TXT record proves control of a name, not of a service on a port.

**MCP.** The same server exposes `audit_site`, `get_report` and `propose_fixes` at `http://localhost:8000/mcp`. It was tested with the official MCP Python client over real HTTP; it has not been tested with Claude Code. See [docs/mcp.md](docs/mcp.md).

The page's unit tests need Node 20 or newer: `cd backend && node --test tests/ui/*.test.mjs`. The page itself was tested with a fake page, not in a real browser: [docs/ui.md](docs/ui.md) has a checklist to go through by eye.

## Running the benchmark

The task runner needs the `runner` extra and an API key in your environment (never commit it). The whole procedure, the rules for which sites may be used and what may be claimed are in [docs/benchmark.md](docs/benchmark.md). In short:

Run the API server in a **separate shell** from these commands. In the shell that runs them, export the **same value** of `AGENTREADY_SECRET` that the API server was started with (the ownership token is derived from it); without it the commands stop with one sentence naming it.

```bash
cd backend
pip install -e '.[runner]'
export AGENTREADY_SECRET=<the same value the API server uses>
export ANTHROPIC_API_KEY=...            # read by the SDK; AgentReady never stores or logs it
python -m app.runner.preflight --check-control    # lists what is still missing
python -m app.runner.cli run --label ../benchmark/labels/<host>.json --snapshot before --repeats 3
python -m app.runner.report --before before --after after --output ../benchmark/evaluation.md
```

- Before any run, commit `benchmark/criteria.json` and a hand-written label for the site (format in `benchmark/labels/README.md`), register the site in `benchmark/sites.json`, and prove control of it. The runner refuses to start unless the criteria and the label are committed and byte for byte unchanged.
- Use a different `--snapshot` for the run against the changed site ("after"). Results are written to `benchmark/results/<snapshot>/<site>/<task>-<n>.json` and a valid result is never overwritten.
- A run whose model call failed or whose tools broke is marked INVALID, kept, reported separately and rerun with the same command; it is never counted as a failure. If the API rejects the key the run stops after the first attempt.
- Every result records the criteria hash, the label hash, the git commit, a hash of the whole harness (tools, prompts, task wording, limits), the guard's rate limit and request budget, and the model that answered.
- Only sites the team controls, or has written permission to change, get an after run. Other verified sites are reported before-only.

## Results

**Not yet measured.** The repository holds no benchmark results and no accuracy figure. The numbers will come only from recorded runs, through `python -m app.runner.report`, and will be published with their sample sizes, failures, invalid runs and the small-sample note; see [docs/benchmark.md](docs/benchmark.md) and [docs/benchmark-report.md](docs/benchmark-report.md). Results describe only the sites tested.

## Audit-check accuracy

Before an audit run, independently label all 13 expected check outcomes in the site's committed label file under `audit_checks` (`true` means the check should pass; `false` means it should fail). Then record each owner-verified snapshot:

```bash
cd backend
export AGENTREADY_SECRET=<the same value the API server uses>
python -m app.runner.audit_cli --label ../benchmark/labels/<host>.json --snapshot before
python -m app.runner.audit_cli --label ../benchmark/labels/<host>.json --snapshot after
```

The report shows per-check agreement and false-positive rates. A failed readiness check is the positive class; unreachable checks are excluded and counted separately. See [docs/benchmark-report.md](docs/benchmark-report.md).

## Limitations

- The MVP sees only the static HTML its own guarded fetch layer receives (no rendering), and the checks say so in their evidence.
- A host that resolves to a private, loopback, link-local or reserved address cannot be audited, so use a public staging host.
- Reports and fix queues are kept in memory in one process and are lost on restart. There are no accounts: the report id is the only credential for approving and exporting, so treat it like a secret.
- Text hidden with CSS (`display:none`, off-screen) is shown to the model like any other text, because the runner reads the static HTML and does not render it.
- A quote check proves the quote is in the text the agent was shown, not that it states the offer: set a `quote_pattern` in the label when that matters.
- The OpenAPI-to-MCP stub is validated but not counted in task success.

## Project layout

```
backend/app/          the API (fetch guard, ownership, checks, scoring, audit)
backend/app/fixes/    fix generation, validation, diffs, approval queue
backend/app/runner/   task runner, judge, results, report, pre-flight
backend/app/ui/       the one-page web interface (plain HTML, CSS and JavaScript)
backend/app/mcp_server.py   the MCP server
benchmark/            criteria, labels, the site registry, results
scripts/              clean-checkout verification
docs/                 documentation and the plans
```

## Documentation

- [docs/benchmark.md](docs/benchmark.md): the benchmark protocol, step by step.
- [docs/benchmark-report.md](docs/benchmark-report.md): what the evaluation report shows and when it fails.
- [docs/fixes.md](docs/fixes.md): the fix generator, validation and the approval model.
- [docs/mcp.md](docs/mcp.md): the MCP server, its tools and its safety limits.
- [docs/ui.md](docs/ui.md): the web page, its security rules and a manual checklist.
- [docs/demo-script.md](docs/demo-script.md): the 3-minute demo script.
- [docs/submission-checklist.md](docs/submission-checklist.md): what only the owner can do before submitting.
