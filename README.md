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
