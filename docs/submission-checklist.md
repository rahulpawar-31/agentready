# Submission checklist

Dates (IST, from the PRD schedule): feature freeze **1 Dec 2026**; evaluation and polish 2 to 3 Dec; demo recording and the
clean-checkout run **4 Dec**; blockers and **submit on 5 Dec**; the window closes **6 Dec 23:59**.

## Benchmark and submission

- [x] Provide `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` with `AGENTREADY_MODEL=gemini-3.5-flash-lite` (see docs/benchmark.md), in your own environment (never commit it) and install the runner extra: `pip install -e '.[runner]'`. Gemini access passed the live integration check on 26 Nov 2026; the Anthropic SDK is also installed.
- [x] Register and verify the first candidate, [Dayhand](https://personal-ai-blue.vercel.app): the ownership-only tag was deployed through [Personal-AI PR #435](https://github.com/rahulpawar-31/Personal-AI/pull/435) and real meta verification passed on 26 Nov 2026.
- [ ] Confirm suitable public task pages and expand toward the target five sites. Dayhand currently exposes a client-rendered sign-in page, with no public contact form or documentation found during inspection. Its task label is pending; a login form must not be labelled as a contact form.
- [x] Keep the current registry to the team-controlled site. For any site you do not own, get **written permission** and record where it is kept in `permission_note` in `benchmark/sites.json`. An after run also needs permission to change or deploy.
- [x] Leave CountryStateCity and MakeMySiteLive out of the current registry; get the employer's written OK before adding either.
- [ ] Ask in the Homingo Discord whether pre-existing work is allowed.
- [ ] Write each site's label by hand and commit it (`benchmark/labels/<host>.json`), then commit `benchmark/criteria.json` if it changed. Do this before any run.
- [ ] Register the sites in `benchmark/sites.json` and run `python -m app.runner.preflight --check-control` until nothing is blocked.
- [ ] Required for audit-check accuracy: independently write `audit_checks.before` and `audit_checks.after` in each label and commit both before the first run. Record the `before` audit before deployment, then record `after` once the planned files are live (see [benchmark.md](benchmark.md)). Without it the report says "not measured".
- [ ] Run the before runs, the fixes and deploys, the after runs, and the report. See [benchmark.md](benchmark.md).
- [ ] Commit the results under `benchmark/results/` and the report (`benchmark/evaluation.md`, `benchmark/evaluation.json`).
- [ ] Record the 3-minute video following [demo-script.md](demo-script.md).
- [x] Re-check the A1 quotation in the README against the live Homingo problem-statements page. It matched the [official page](https://hackathon.homingo.co.in/problem-statements) rendered in Chrome again at 14:50 UTC on 26 Nov 2026. See [release-evidence.json](release-evidence.json). Re-check just before actual submission; if a later check differs, **remove the quotation** instead of rewording it.
- [x] Make the GitHub repository public. GitHub metadata confirmed public visibility on 26 Nov 2026.
- [ ] Add the recorded demo video link to the README.
- [ ] Submit on 5 Dec.

## Already automated (run these yourself before submitting)

- [x] `cd backend && pytest` passes: 1,329 passed, 3 live tests skipped on merged commit `78a1b3a` on 26 Nov 2026; the Gemini live test passed separately earlier that day.
- [x] `cd backend && node --test tests/ui/*.test.mjs` passes: 123 passed on 26 Nov 2026 (needs Node 20 or newer).
- [x] `python scripts/verify_checkout.py --ref <your pushed branch or commit>` passes on a fresh clone: merged revision `78a1b3aa59acab038ea0a618bac9c1b96a90a1f4` passed clone, isolated installation, Python/UI tests, five server smoke checks and clean-tree checks from a local clone on 26 Nov 2026, and again cloned from the GitHub origin (the default source) on 27 Nov 2026: 1,329 Python tests passed with 3 skipped, 123 UI tests passed. Because the revision was given explicitly, the script did not run its pushed/clean precondition; the successful clone from origin shows the commit is pushed. See [release-evidence.json](release-evidence.json). Repeat for the final submission revision.
- [ ] `python -m app.runner.report --before before --after after` runs with no "Data issues".
- [ ] The README says "Not yet measured" only until the report exists; then link or copy the real report, with its sample-size line.
- [x] Run the UI flow in Chrome with synthetic site responses: ownership, findings, proposals, approval, download, copy, revoke, reload and fresh re-audit passed on 26 Nov 2026. Light/dark layouts at 360 px and 1440 px, forced-colours emulation and print output were checked.
- [x] Run the same main flow in Firefox: ownership, findings, approval, actual download, copy, revocation, reload and fresh re-audit passed on 26 Nov 2026. Default-colour and dark layouts at 360 px and 1440 px fit without page overflow; PDF printing included every evidence section and omitted action-button labels.
- [x] Run the verification, audit, findings and proposal flow in Chrome against live Dayhand with real guarded network requests and no mocks. Desktop and mobile pages rendered without page errors on 26 Nov 2026. No readiness fix was approved or deployed; preserve the baseline until task labels are settled.
- [ ] Finish real-site approval/export/deployment/re-audit, Safari and native Windows high-contrast checks in [ui.md](ui.md). Safari WebDriver is blocked until "Allow remote automation" is enabled in Safari Settings.
- [x] Connect an MCP client once: Claude Code 2.1.332 added a temporary local MCP configuration and its health check reported Connected on 26 Nov 2026. See [mcp.md](mcp.md).
- [ ] Optional: exercise model-driven tools through Claude Code after signing in; only its connection health check has been confirmed.

## Benchmark preflight status

Preflight still blocks benchmark collection: only one of the target five sites is registered, and
`benchmark/labels/personal-ai-blue.vercel.app.json` does not exist. A `--check-control` run on 26 Nov 2026 verified
Dayhand ownership. On 27 Nov 2026, at commit `4841d0b` (code identical to `78a1b3a`), preflight confirmed the Gemini
key was present and the committed criteria matched the harness. See [release-evidence.json](release-evidence.json).
No task run, readiness-fix deployment or measured demo has been performed.

Still to do before collection:

- [ ] Supply the four other controlled origins and the correct public task URLs. A sign-in form cannot supply the missing public contact-form label.
- [ ] Independently commit both audit expectation maps before running anything.
- [ ] After collection, the owner approves and explicitly exports the fixes, deploys them, and reruns the same tasks; only then can the measured demo be recorded.

## Do not claim

- Results for sites that were not tested, or any effect on agents in general.
- Audit-check accuracy (there are no hand-labelled audit outcomes).
- An effect of the OpenAPI-to-MCP stub (it is not counted).
- That the project is complete or secure: it is an MVP with the limits listed in the README.
