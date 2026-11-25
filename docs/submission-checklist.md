# Submission checklist

Dates (IST, from the PRD schedule): feature freeze **1 Dec 2026**; evaluation and polish 2 to 3 Dec; demo recording and the
clean-checkout run **4 Dec**; blockers and **submit on 5 Dec**; the window closes **6 Dec 23:59**.

## Owner only (nothing here can be done by the tooling)

- [ ] Provide `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` with `AGENTREADY_MODEL=gemini-3.5-flash-lite` (see docs/benchmark.md), in your own environment (never commit it) and install the runner extra: `pip install -e '.[runner]'`.
- [ ] Choose the benchmark sites (start with five) and verify control of each: the meta tag or the DNS TXT record from `GET /ownership-token?domain=<host>`.
- [ ] For any site you do not own, get **written permission** and record where it is kept in `permission_note` in `benchmark/sites.json`. An after run also needs permission to change or deploy.
- [ ] For CountryStateCity or MakeMySiteLive, get the employer's written OK first, or leave them out.
- [ ] Ask in the Homingo Discord whether pre-existing work is allowed.
- [ ] Write each site's label by hand and commit it (`benchmark/labels/<host>.json`), then commit `benchmark/criteria.json` if it changed. Do this before any run.
- [ ] Register the sites in `benchmark/sites.json` and run `python -m app.runner.preflight --check-control` until nothing is blocked.
- [ ] Optional, for audit-check accuracy: write `audit_checks` in each label **before** looking at any audit of that site, commit it, then run `python -m app.runner.audit_cli` for `before` and `after` (see [benchmark.md](benchmark.md)). Without it the report says "not measured".
- [ ] Run the before runs, the fixes and deploys, the after runs, and the report. See [benchmark.md](benchmark.md).
- [ ] Commit the results under `benchmark/results/` and the report (`benchmark/evaluation.md`, `benchmark/evaluation.json`).
- [ ] Record the 3-minute video following [demo-script.md](demo-script.md).
- [ ] Re-check the A1 quotation in the README against the live Homingo problem-statements page. If it differs, **remove the quotation** instead of rewording it.
- [ ] Make the GitHub repository public and add the video link to the README.
- [ ] Submit on 5 Dec.

## Already automated (run these yourself before submitting)

- [ ] `cd backend && pytest` passes.
- [ ] `cd backend && node --test tests/ui/*.test.mjs` passes (needs Node 20 or newer).
- [ ] `python scripts/verify_checkout.py --ref <your pushed branch or commit>` passes on a fresh clone.
- [ ] `python -m app.runner.report --before before --after after` runs with no "Data issues".
- [ ] The README says "Not yet measured" only until the report exists; then link or copy the real report, with its sample-size line.
- [ ] Open the web page in a real browser once and go through the checklist at the end of [ui.md](ui.md): the interface was tested only with a fake page, not in a browser.
- [ ] Connect an MCP client once (`claude mcp add --transport http agentready http://localhost:8000/mcp`): it was tested with the official Python client, not with Claude Code. See [mcp.md](mcp.md).

## Do not claim

- Results for sites that were not tested, or any effect on agents in general.
- Audit-check accuracy (there are no hand-labelled audit outcomes).
- An effect of the OpenAPI-to-MCP stub (it is not counted).
- That the project is complete or secure: it is an MVP with the limits listed in the README.
