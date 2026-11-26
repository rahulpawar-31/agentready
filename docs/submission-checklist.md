# Submission checklist

Dates (IST, from the PRD schedule): feature freeze **1 Dec 2026**; evaluation and polish 2 to 3 Dec; demo recording and the
clean-checkout run **4 Dec**; blockers and **submit on 5 Dec**; the window closes **6 Dec 23:59**.

## Benchmark and submission

- [x] Provide `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` with `AGENTREADY_MODEL=gemini-3.5-flash-lite` (see docs/benchmark.md), in your own environment (never commit it) and install the runner extra: `pip install -e '.[runner]'`. Gemini access passed the live integration check on 26 Nov 2026; the Anthropic SDK is also installed.
- [ ] Choose the benchmark sites (start with five) and verify control of each: the meta tag or the DNS TXT record from `GET /ownership-token?domain=<host>`.
- [ ] For any site you do not own, get **written permission** and record where it is kept in `permission_note` in `benchmark/sites.json`. An after run also needs permission to change or deploy.
- [ ] For CountryStateCity or MakeMySiteLive, get the employer's written OK first, or leave them out.
- [ ] Ask in the Homingo Discord whether pre-existing work is allowed.
- [ ] Write each site's label by hand and commit it (`benchmark/labels/<host>.json`), then commit `benchmark/criteria.json` if it changed. Do this before any run.
- [ ] Register the sites in `benchmark/sites.json` and run `python -m app.runner.preflight --check-control` until nothing is blocked.
- [ ] Optional, for audit-check accuracy: write `audit_checks` in each label **before** looking at any audit of that site, commit it, record the `before` audit before deployment, then record `after` once the updated files are live (see [benchmark.md](benchmark.md)). Without it the report says "not measured".
- [ ] Run the before runs, the fixes and deploys, the after runs, and the report. See [benchmark.md](benchmark.md).
- [ ] Commit the results under `benchmark/results/` and the report (`benchmark/evaluation.md`, `benchmark/evaluation.json`).
- [ ] Record the 3-minute video following [demo-script.md](demo-script.md).
- [x] Re-check the A1 quotation in the README against the live Homingo problem-statements page. It matched the [official page](https://hackathon.homingo.co.in/problem-statements) rendered in Chrome on 26 Nov 2026. If a later check differs, **remove the quotation** instead of rewording it.
- [x] Make the GitHub repository public. GitHub metadata confirmed public visibility on 26 Nov 2026.
- [ ] Add the recorded demo video link to the README.
- [ ] Submit on 5 Dec.

## Already automated (run these yourself before submitting)

- [x] `cd backend && pytest` passes: 1,298 passed, 3 live tests skipped on 26 Nov 2026; the Gemini live test passed separately.
- [x] `cd backend && node --test tests/ui/*.test.mjs` passes: 123 passed on 26 Nov 2026 (needs Node 20 or newer).
- [ ] `python scripts/verify_checkout.py --ref <your pushed branch or commit>` passes on a fresh clone.
- [ ] `python -m app.runner.report --before before --after after` runs with no "Data issues".
- [ ] The README says "Not yet measured" only until the report exists; then link or copy the real report, with its sample-size line.
- [x] Run the UI flow in Chrome with synthetic site responses: ownership, findings, proposals, approval, download, copy, revoke, reload and fresh re-audit passed on 26 Nov 2026. Light/dark layouts at 360 px and 1440 px, forced-colours emulation and print output were checked.
- [ ] Finish the real-site, Firefox, Safari and native Windows high-contrast checks in [ui.md](ui.md).
- [x] Connect an MCP client once: Claude Code 2.1.332 added a temporary local MCP configuration and its health check reported Connected on 26 Nov 2026. See [mcp.md](mcp.md).
- [ ] Optional: exercise model-driven tools through Claude Code after signing in; only its connection health check has been confirmed.

## Do not claim

- Results for sites that were not tested, or any effect on agents in general.
- Audit-check accuracy (there are no hand-labelled audit outcomes).
- An effect of the OpenAPI-to-MCP stub (it is not counted).
- That the project is complete or secure: it is an MVP with the limits listed in the README.
