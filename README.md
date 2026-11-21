# AgentReady

A Lighthouse for AI agents: AgentReady takes a website URL, checks that you control it, tests whether an AI agent can find and retrieve information on it, audits how ready it is for agents, scores the result with evidence for every finding, and proposes fixes for the owner to approve.

## The problem it interprets (Homingo problem A1)

The official wording, as shown on the Homingo Problem statements page and checked against a screenshot on 21 Nov 2026:

> "We want the tools that make agents first-class users: interfaces, identity, permissions, payments, memory, sandboxes and testing built for agents rather than people."

AgentReady is our interpretation of A1. It covers interfaces, identity, permissions and testing; it does not address payments, memory or sandboxes. Re-check the quotation against the live page before submission, and remove it if it differs.

## Limits

- The MVP sees only the static HTML its own guarded fetch layer receives (no rendering), and the checks say so in their evidence.
- A host that resolves to a private, loopback, link-local or reserved address cannot be audited (so a local development server cannot be audited; use a public staging host).

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Running the API

The API refuses to start without a secret, because a guessable default would make every ownership token predictable.

```bash
cd backend
export AGENTREADY_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app.main:app --port 8000
```

For local development only, `AGENTREADY_DEV=1` uses a fixed development secret instead (the tests set it).
Other settings: `AGENTREADY_CACHE` (cache directory, default `.cache`).

Sites on a non-default port can only be verified with the meta tag (inside `<head>` on the home page), because
a DNS TXT record proves control of a name, not of a service on a port.

## Running the benchmark tasks (Plan 2)

The task runner needs the `runner` extra and an API key in your environment (never commit it):

    pip install -e '.[runner]'
    export ANTHROPIC_API_KEY=...            # read by the SDK; AgentReady never stores or logs it
    export AGENTREADY_SECRET=...            # see "Running the API"

Before any run, commit `benchmark/criteria.json` and a label for the site (`benchmark/labels/<site>.json`,
format in `benchmark/labels/README.md`), prove control of the site, then:

    cd backend && python -m app.runner.cli run --label ../benchmark/labels/<site>.json --snapshot before --repeats 3

Use a different `--snapshot` for the run against the changed site ("after"). Results are written to
`benchmark/results/<snapshot>/<site>/<task>-<n>.json` and a valid result is never overwritten. A run that is invalid
(the model call failed, a bug in the runner, a cut-off or refused model turn, or a network failure in a run that did
not succeed) is marked INVALID; rerun the same command in the same snapshot: valid results are kept, each invalid one
is moved aside to `<task>-<n>.invalid-<k>.json` (kept, reported separately, never deleted) and only those runs are
repeated. If the API rejects the key the run stops after the first attempt. The runner refuses to start unless the
criteria and the label are committed and byte for byte unchanged (a symlink, a staged-only change or an
`assume-unchanged` edit does not count), and every result records the criteria hash, the label hash, the git commit,
a hash of the whole harness (tools, prompts, task wording, limits) that must equal `harness_sha256` in
`criteria.json`, the guard's rate limit and request budget, and the model that actually answered. `--repeats` is
capped at 10, and the label file must be named after the site's host. The runner measures discovery and retrieval
only, with a fixed toolset and a fixed step limit; results describe only the sites tested.

### Known limitations

- Text hidden with CSS (`display:none`, off-screen) is shown to the model like any other text, because the runner
  reads the static HTML and does not render it. A human visitor would not see that text; the model does.
- A quote check proves the quote is in the text the agent was shown, not that it states the offer: set a
  `quote_pattern` in the label when that matters.
