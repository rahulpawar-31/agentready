# AgentReady, Plan 6: Evaluation Tooling, Pre-flight, Docs and a Clean-Checkout Check (revision 1)

> Written following the `writing-plans` skill (superpowers), by hand. Intended repo path: `docs/plans/2026-11-24-plan-6-evaluation-and-release.md`.
> Base: `main` after PRs #1 to #7. **Note:** PR #6 ("Plan 6: add benchmark evaluation report") was merged to `main` by the owner from another session, with `backend/app/runner/report.py` (257 lines), a README paragraph, `docs/benchmark-report.md` and a small change to `results.py`, **and no tests**. This plan treats that code as unreviewed: it gets tests, an adversarial review and fixes before anything is built on it. Branch: `watcher/agentready-evaluation-release`.

> **For agentic workers:** execute task by task, test first. Steps use checkbox (`- [ ]`) syntax. Git: stay on the branch, stage only the files you changed (never `git add -A`), never push to main, open the PR with `gh pr create --base main` (ready, not draft). Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Push with `git push origin HEAD:refs/heads/watcher/agentready-evaluation-release`, only when the preceding test command passed. **Do not merge** (the orchestrating session decides). **No browser tools.**

**Goal:** Everything needed to run the benchmark and ship the project that can be built without the owner's real sites, API key and video: a tested and hardened results report, a machine-checkable pre-flight that says exactly what the owner still has to supply, the benchmark protocol, the demo script, the submission checklist, an honest README, and a clean-checkout verification script.

**Architecture:** `app/runner/report.py` (exists) gets tests and fixes; a new `app/runner/preflight.py` reads `benchmark/sites.json` and checks readiness without network by default; documentation lives in `docs/`; `scripts/verify_checkout.py` clones a ref into a temporary directory, installs, tests, and smoke-runs the server. Nothing here adds a site-touching capability: the only network use is the optional `--check-control` in pre-flight (the same verification the audit already performs) and the clean-checkout script's `pip install`.

**Tech Stack:** Python 3.13 (repo venv), stdlib only for the new modules (`argparse`, `json`, `pathlib`, `subprocess`, `tempfile`), pytest. No new dependency.

**Spec:** PRD (https://claude.ai/artifact/2CLFRC7VzK9iRbN5daPmUd): the Evaluation plan (pre-written criteria; 5 verified sites, expand toward 20 only if authorization/labelling/caching stay manageable; identical tasks before and after on separate snapshots; only sites the team controls or has written permission to change get an "after" run; 3 repeats; report sample size, failures, variance and limitations; no generalising; audit-check accuracy only if hand labels exist), the Schedule/release checklist, FR-6.4 (the MCP stub is excluded from the results table by default), and the Safety table.

## Global Constraints
- **No fabricated results.** No number about agent performance or audit accuracy appears in any file unless it comes from a recorded run in `benchmark/results/`. Until a benchmark has been run, README and docs say "not yet measured" and point to the command that produces the numbers. A test scans README and `docs/` for a percent figure next to the words "success", "improvement" or "accuracy" and fails unless it is inside a clearly marked example block.
- **Claim boundary (PRD):** results describe discovery and retrieval on the sites tested only. Wording like "agents can use your site" or "improves agent success in general" is banned in README/docs (a test checks a small banned-phrase list).
- **After-runs only for eligible sites:** a site is `after_eligible` only if `owner_type` is `team` or it has a `permission_note` recording written permission to change it; employer sites (the PRD names CountryStateCity and MakeMySiteLive) require the written OK, recorded in `permission_note`.
- **Provenance:** every figure in a report is traceable to run files; the report refuses to pair runs whose criteria, harness, label, model, step/turn limits or guard settings differ (already implemented; now tested).
- **The MCP stub is not counted** in task success (FR-6.4); the report and docs state it.
- The owner's secrets: the pre-flight only checks that `ANTHROPIC_API_KEY` is set (presence), never prints or stores it.
- The test count in each task is a floor, not a target: report the real number.

## Decisions made here (flag to the owner)
1. **Treat merged PR #6 as unreviewed** and put it through test, review and fix inside this plan (it is on `main` without tests).
2. **`benchmark/sites.json` is the single registry** of benchmark sites (committed, starts empty). The pre-flight and docs read it; the runner CLI is not changed.
3. **Pre-flight is offline by default**; `--check-control` performs the real ownership verification for each listed site.
4. **No offline "demo mode" that relaxes the address guard** is added: a local site can never be audited, by design. Rehearse the demo on a real team-controlled host.
5. The demo video, the real benchmark runs, the live A1 wording check and the public repo/video links are **owner tasks**; the checklist tracks them.

## File structure
```
backend/app/runner/report.py        # modify: fixes from tests/review; provenance section; small-sample note
backend/app/runner/preflight.py     # new: sites registry loader + readiness checks + CLI
benchmark/sites.json                # new: {"version": 1, "sites": []}
benchmark/sites.example.json        # new: documented example entries (not real hosts: use example hostnames)
backend/tests/test_runner_report.py       # tests for report.py
backend/tests/test_runner_preflight.py    # tests for preflight.py
backend/tests/test_docs_honesty.py        # banned phrases, no fabricated numbers, links exist
scripts/verify_checkout.py          # clone, install, test, smoke-run
backend/tests/test_verify_checkout.py     # unit tests for the script's helpers
docs/benchmark.md                   # protocol, step by step
docs/demo-script.md                 # the 3-minute video script
docs/submission-checklist.md        # release checklist with owner-only items
README.md                           # rewrite: concise, honest
```

---

### Task 1: Tests and hardening for the merged `report.py`

**Files:** Create `backend/tests/test_runner_report.py`. Modify `backend/app/runner/report.py` (and `results.py` only if a bug is found there).

**Interfaces (existing, do not rename):** `build_report(results: Path, labels_dir: Path, before_name: str, after_name: str, repeats: int = 3) -> dict`, `to_markdown(report) -> str`, `main(argv) -> int` (exit 1 when `problems` is non-empty). Result files are written by `app.runner.results.save_run`; use it (with a tmp results root and tmp label files created through `load_label`-valid JSON, as `tests/test_runner_results.py` and `tests/runner_fakes.py` show) to build fixtures, so the tests exercise the real file format.

**Behaviours to pin with tests (write them first; fix `report.py` where one fails):**
1. Success rate uses only valid runs; invalid files (`*.invalid-<k>.json`, `valid: false`) are listed and excluded from numerator and denominator; a task with 0 valid runs shows `—` and never `0%`.
2. Missing repeats are shown as incomplete (`valid/expected`), never counted as failures.
3. Paired comparison: pairs only identical (site, task, repeat) keys; runs with different `criteria_sha256`, `harness_sha256`, `label_sha256`, `model`, limits or guard settings are excluded and listed under `config_mismatches_excluded`; before-only sites do not enter the pair; `unpaired_*` counts are right.
4. Repeat spread (max minus min of 0/100 values) for 3 repeats with 2 successes is `100 pp`; with 1 valid run it is `—`.
5. Problems cause exit code 1 and appear under "Data issues": a result for a site with no label; a malformed JSON file; duplicate valid runs for one key; a repeat outside `1..repeats`; `success` not a boolean; a label file that fails `load_label`.
6. The command never creates, modifies or deletes any file under `--results` (snapshot the directory tree and bytes before and after); `--output` refuses an existing run file path, refuses a path **inside `--results`** at all (decision: reports go outside the results tree, for example `benchmark/evaluation.json`, so a rerun of the CLI can never see them as results), and refuses symlinks as output.
7. `--before` equal to `--after`, or two names resolving to one directory (case-insensitive file systems!), is refused; snapshot names must match the server pattern.
8. Unknown fields in result files (hostile content: huge strings, newlines, `|` and backticks in `reason`/`error`/site names) never break the Markdown table: values are clipped to 200 characters and `|`, backticks and newlines are escaped (test that a reason containing `|\n` does not add a table row).
9. Determinism: the same inputs give byte-identical Markdown and JSON (sorted keys in JSON; sites sorted).
10. **New content to add (fix first, then test):** a "Provenance" section listing, per snapshot, the distinct `criteria_sha256`, `harness_sha256`, `model`, `git_head` values found in the valid runs (more than one value in a snapshot is a data issue); a "Sample size" sentence: when valid runs per snapshot are fewer than 30 the report says "Small sample: treat differences as indicative only" and never prints a significance claim; the paired section prints the counts of pairs that were `fail→pass`, `pass→fail`, `pass→pass`, `fail→fail` (so a net change is visible with its components); an explicit line "The MCP stub is not counted in task success (FR-6.4)"; the MCP-stub exclusion is stated even when no stub was proposed.
11. Empty `benchmark/results/` (no snapshot directory) gives a report with `valid_runs: 0`, rates `null`, and exit code 0 (nothing malformed), saying "no results recorded yet", not a traceback.

- [ ] **Step 1:** write `tests/test_runner_report.py` with one test per behaviour above (use `pytest.mark.parametrize` for the hostile-text cases), run it, and record which fail on the unmodified code (expected: 6 partly, 8, 10, probably 7 and 11).
- [ ] **Step 2:** fix `report.py` until all pass; keep its public function names and the JSON keys (`before`, `after`, `paired_comparison`, `audit_check_accuracy`, `problems`, `limitations`) stable; add keys, never rename.
- [ ] **Step 3:** run the whole suite; commit `test(report): pin the merged evaluation report with tests and harden it`. List in the PR every defect found in the merged code.

---

### Task 2: Site registry and pre-flight

**Files:** Create `benchmark/sites.json`, `benchmark/sites.example.json`, `backend/app/runner/preflight.py`. Test: `backend/tests/test_runner_preflight.py`.

**Registry format** (`benchmark/sites.json`, committed; starts as `{"version": 1, "sites": []}`):
```json
{"version": 1, "sites": [
  {"host": "demo.example.org",
   "owner_type": "team",
   "verification": "meta",
   "label": "demo.example.org.json",
   "after_eligible": true,
   "after_method": "staging",
   "permission_note": "",
   "notes": "free text"}
]}
```
Rules (`load_sites(path) -> (list[Site], list[str])` returns problems instead of raising): `host` is a valid `host[:port]` that `parse_url("https://"+host)` accepts and is not a non-public literal; unique; `owner_type` in `{team, permission}`; `permission` requires a non-empty `permission_note` (at least 10 characters, a pointer to the written OK, not the OK itself); `verification` in `{dns, meta}` (and `dns` is refused for a host with a port); `label` is a file name inside `benchmark/labels/` with no path separators; `after_eligible` true requires `after_method` in `{staging, live}`; `after_eligible` true with `owner_type: permission` requires the note to mention "change" or "deploy" (written permission to change the site, not just to test it); at most 20 sites (PRD: expand toward 20 only if manageable).

**Pre-flight** (`python -m app.runner.preflight [--check-control] [--snapshot-before before] [--snapshot-after after]`; `run_preflight(repo_root, env, deps) -> Preflight` with injectable `deps` for git, SDK import, and verification so tests need no network): each check returns `{id, status: ok|blocked|warn|skipped, message}`:
- `criteria_committed` (criteria.json is committed and unmodified: reuse `git_problems`), `harness_hash` (the criteria's `harness_sha256` equals `harness_sha256()`), `api_key_present` (env var set and non-empty; never prints it), `sdk_installed` (`import anthropic` works), `sites_registry` (loads without problems), `site_count` (0 sites: blocked "no benchmark sites registered"; fewer than 5: warn "target is 5"; more than 20: blocked), per site: `label_valid_and_committed` (exists in `benchmark/labels/`, passes `load_label(check_name=True)`, committed and unmodified), `label_names_match` (label host equals registry host), `control_proven` (only with `--check-control`: uses the same `verify_ownership` path as the CLI through an injected callable; otherwise `skipped`: "not checked: run with --check-control"), `after_plan` (for eligible sites: `warn` if no label exists... and a reminder that the after run needs a different snapshot name), `results_dirs_free` (the `before`/`after` snapshot directories for the host do not already hold valid results unless the owner passes `--allow-existing`), and a global `after_eligible_count` (0: warn "no site can have an after run; the demo and the before/after comparison need at least one").
- Exit code 0 only when nothing is `blocked`; warnings are printed. Output is a plain-text checklist and `--format json`. **It never prints secrets, never makes a model call, never writes files.** The default run makes no network request.

- [ ] **Step 1: Write the failing tests** (`tests/test_runner_preflight.py`): registry validation cases (each rule above, including `permission` without note, `dns` with a port, path separators in `label`, duplicate hosts, 21 sites, bad JSON, wrong version); pre-flight with an empty registry (blocked, exit 1); a valid registry with fakes for git/SDK/env (ok); missing API key (blocked) without the value appearing in the output even when set to `sk-ant-SECRET` (assert the secret string never occurs in stdout/JSON); a site whose label is uncommitted or modified (blocked); label host mismatch; `--check-control` calls the injected verifier exactly once per site and a failed verification blocks while a skipped one does not; existing results dir blocks unless `--allow-existing`; JSON output parses and has the same statuses as text; the module makes no network call by default (monkeypatch `socket.socket.connect` and `socket.getaddrinfo` to raise during the default run).
- [ ] **Steps 2–4:** run, FAIL, implement, pass; add `sites.json` and `sites.example.json` (example hosts use the reserved `example.org`/`example.net` names and say in a `_comment` key that they are not real); commit `feat(benchmark): site registry and an offline pre-flight`.

---

### Task 3: Documentation and an honest README

**Files:** Create `docs/benchmark.md`, `docs/demo-script.md`, `docs/submission-checklist.md`, `backend/tests/test_docs_honesty.py`. Modify `README.md` (rewrite; keep every existing correct instruction: setup, running the API, the runner section, the report section, limitations).

**`docs/benchmark.md`** (protocol, exact commands, copy-pasteable): what is measured (three tasks, discovery and retrieval only), the pre-registration rule (criteria and labels committed before any run), eligibility (team-owned or written permission; employer sites need the written OK noted in `sites.json`), the sequence for one site: register in `sites.json` → write and commit the label → prove control (meta tag or DNS TXT) → `python -m app.runner.preflight --check-control` → `before` runs (`--repeats 3`) → audit the site and propose fixes (UI or API) → owner approves, downloads and **deploys the file themselves** → wait until the live site serves it, then re-audit with a new snapshot (UI "Re-audit with fresh pages" or `POST /audit` with `"snapshot": "after"`) → `after` runs with `--snapshot after` → `python -m app.runner.report --before before --after after --format json --output ../benchmark/evaluation.json` and the Markdown version; what to publish (the whole report including failures, invalid runs, small-sample note); what must never be claimed (generalisation, audit-check accuracy without hand labels, MCP-stub effect); how invalid runs are handled; cost and time estimate formula (sites × 3 tasks × 3 repeats × runs ≈ model calls; steps limit 10, turn limit 24; no dollar figures invented: say "check your own pricing"); the before-only sites (reported, no after run).

**`docs/demo-script.md`:** a 3-minute script as a table (time, screen, what to do, what to say) following FR-8.1 exactly: 0:00 problem in one sentence (use the A1 quotation only if the live page re-check passed; else paraphrase without quoting), 0:20 paste a team-controlled URL, 0:35 verification (meta tag or TXT; show the 403 first if time allows), 1:00 score and one evidence row expanded (including a "not detected" with its confidence), 1:25 propose fixes and show a diff, 1:50 approve (say aloud "approving changes nothing on the site"), 2:05 download and show the file, 2:15 the owner deploys (cut to the deployed file), 2:25 re-audit and the changed score, 2:40 the benchmark table from `evaluation.md` with its sample size and the small-sample note (read the real numbers from the report; the script has no numbers), 2:55 the MCP line (an agent calls `audit_site`, cannot approve), close with the limits sentence. Plus a pre-recording checklist (site deployed and verified, cache snapshot names, secret set, a dry run, browser zoom, no secrets on screen, the report id not shown in the address bar because the UI never puts it there) and a "if something fails on camera" list.

**`docs/submission-checklist.md`:** checkboxes grouped by owner: *Owner only* (supply `ANTHROPIC_API_KEY`; choose and verify the five sites; written permission for employer sites; write and commit labels; run pre-flight with `--check-control`; run before/after; record the 3-minute video; re-check the A1 wording on the live Homingo page and **remove the quotation instead of rewording it** if it differs; make the repo public; add the video link; submit on 5 Dec, window closes 6 Dec 23:59 IST) and *Already automated* (links to each command: full test suite, UI tests, `scripts/verify_checkout.py`, `python -m app.runner.report`). Dates from the PRD schedule: feature freeze 1 Dec, evaluation and polish 2–3 Dec, demo recording and clean-checkout run 4 Dec, blockers and submit 5 Dec.

**README rewrite** (target under 200 lines): one-paragraph pitch; the problem (A1 quotation block kept with the re-check note); what it does (four steps); the safety model in six bullets (proof of control first, same-host guarded fetch with pinned public addresses, read-only GETs with a request budget, fixes only by owner approval and the app never deploys, site text is untrusted in UI/MCP, secrets never exported); quickstart (setup, run, open the UI at `http://127.0.0.1:8000/`, connect an MCP client with the URL and the tested/untested status honestly stated); how to run the benchmark (link to `docs/benchmark.md`); **Results: "Not yet measured. The numbers come from `python -m app.runner.report` after the benchmark is run; see docs/benchmark.md."** with no figures; project layout; limitations (static HTML only, non-public hosts cannot be audited, in-memory state, no accounts, results only for sites tested); docs index with links to every `docs/*.md`.

**`tests/test_docs_honesty.py`:** (a) every relative link in README and `docs/*.md` resolves to a file; (b) banned phrases (case-insensitive): "agents can use your site", "improves agent success", "proven to improve", "guarantee", "100% secure", "fully secure", "no vulnerabilities", "production-ready" (the MVP is not), "absent" next to "no captcha"; (c) no `\d+(\.\d+)?%` within 40 characters of `success`, `improvement`, `accuracy` or `score` in README and docs except inside a fenced block that begins with the line `<!-- example -->`; (d) README contains the exact strings "Not yet measured" and "docs/benchmark.md"; (e) the A1 quotation, if present, appears exactly once and is followed within 3 lines by the words "Re-check"; (f) `docs/demo-script.md` contains no digit followed by `%`; (g) docs/benchmark.md mentions `--check-control`, `--snapshot after`, `permission_note` and "MCP stub".

- [ ] **Steps 1–4:** write `test_docs_honesty.py` first (FAIL against the old README and missing docs), write the docs and README, run, commit `docs: benchmark protocol, demo script, submission checklist and an honest README`.

---

### Task 4: Clean-checkout verification script

**Files:** Create `scripts/verify_checkout.py`, `backend/tests/test_verify_checkout.py`.

**Interface:** `python scripts/verify_checkout.py [--source <path-or-url>] [--ref <branch-or-sha>] [--keep] [--skip-node]`. Steps, each printing a clear PASS/FAIL line and stopping at the first FAIL with the command output tail: (1) `git clone --no-hardlinks <source> <tmp>/checkout` (default source: this repository's `origin` URL if it is a GitHub HTTPS/SSH URL, else the local path; default ref: the current branch name of the working tree's HEAD, pushed commit only: refuse with a clear message if `HEAD` is not on the remote) and `git checkout <ref>`; (2) `python3 -m venv <tmp>/venv` and `pip install -e 'backend[dev,runner]'`... use the project's documented command (`cd backend && pip install -e '.[dev]'` and the `runner` extra if it still exists in `pyproject.toml`); (3) `pytest -q` in the clone with `AGENTREADY_DEV=1`; (4) `node --test backend/tests/ui/*.test.mjs` unless `--skip-node` or node is missing (then WARN "UI tests not run"); (5) start the app with `AGENTREADY_SECRET=<random hex>` (not `DEV`) via `python -m uvicorn app.main:app --host 127.0.0.1 --port <free port>` from `backend/`, wait for readiness, then check with stdlib `urllib`: `GET /` 200 with the CSP header; `GET /ownership-token?domain=example.org` 200 JSON; `GET /audit` 405; `POST /mcp` with a JSON-RPC `initialize` and `Host: 127.0.0.1:<port>` returns 200 and lists three tools after `tools/list` (use the SDK client or raw JSON-RPC: choose raw JSON-RPC over `urllib` to keep the script dependency-free; the stateless server needs no session id); then terminate the server; (6) check `git status --porcelain` in the clone is empty after the whole run (tests must not dirty the tree) and that no `__pycache__`, `.cache`, `.venv` or `benchmark/results/*` file is tracked. Prints a final table of steps with durations; exit code 0 only when every non-warn step passed. Never touches the working repo; temp dir removed unless `--keep`; no network except `git clone` of the chosen source and `pip install`.

**Unit tests** (no network, no real clone): the command-plan builder returns the expected commands for a given source/ref; it refuses a dirty or unpushed HEAD with a clear message (inject a fake `git` runner); `pick_free_port` returns an unused port; the readiness waiter times out cleanly; the JSON-RPC helper builds the right requests; the report table formats FAIL rows; a failing step stops the run and returns exit code 1 (inject fake steps).

- [ ] **Steps 1–3:** tests first (FAIL), implement, pass, commit `feat(scripts): clean-checkout verification`.
- [ ] **Step 4: Run it for real** (this needs network for `git clone` and `pip install`; that is allowed here): push the branch, then `python scripts/verify_checkout.py --ref watcher/agentready-evaluation-release` and paste the table into the PR. If it finds a real problem in the repository (a missing file, a test depending on the working tree, an undeclared dependency), fix it in this PR with a test.

---

### Task 5: Real checks and PR

- [ ] Run the full pytest suite and the Node UI tests; run `python -m app.runner.preflight` against the committed empty registry and paste the output (expected: blocked with "no benchmark sites registered" and the missing API key); run it against a scratch registry in the scratchpad with one fake entry and a scratch label to show the passing and the blocked paths (offline); run `python -m app.runner.report --before before --after after` against an empty results tree and against a scratchpad results tree built with `save_run` (3 sites × 3 tasks × 3 repeats before, 2 sites after, a few invalid runs, one config mismatch) and paste the Markdown; confirm the report never wrote into the results tree. State clearly that **no real benchmark was run** (no API key, no verified sites) and that nothing in the repository contains agent-performance numbers.
- [ ] Commit the plan copy under `docs/plans/`; push; open the PR (`gh pr create --base main`): body lists what was built, the defects found in the merged PR #6 code, what was really tested, what was not, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-review (against the PRD and the writing-plans checklist)
- **Evaluation plan coverage:** pre-registration (existing; pre-flight checks it), 5 sites expanding toward 20 (registry limits and `site_count`), identical tasks before/after on separate snapshots (docs/benchmark.md; the report's pairing rules, now tested), after-runs only for eligible sites (registry rules + `after_plan`), 3 repeats (default), report sample size/failures/variance/limitations (Task 1 items 1 to 4, 10), no generalising (honesty tests), audit-check accuracy only with hand labels (the report keeps "not measured"), MCP stub excluded (stated in the report and docs).
- **Release checklist coverage:** A1 re-check with "remove, don't reword" (checklist + docs test e), clean-checkout run (Task 4), README with claims bounded (Task 3), demo script following FR-8.1 (Task 3), repo/video links (owner items).
- **Honesty:** README says "Not yet measured"; the test fails if a performance percentage appears outside an `<!-- example -->` block; no real benchmark is run by this plan and the PR says so.
- **Merged-PR risk:** report.py is treated as unreviewed code (Task 1), and a later review cycle covers it.
- **Placeholder scan:** none; owner-only items are explicit tasks, not placeholders.
- **Type consistency:** `build_report`, `to_markdown`, `main`, `load_sites`, `run_preflight`, `Site`, `Preflight` and the script's functions are named once and reused.
- **Known risks:** owner inputs (API key, sites, labels, permission, video) gate the real benchmark and the submission; the clean-checkout script needs network; pre-flight's `--check-control` touches real sites only when the owner asks.

## Decisions to confirm
1. Reports are written outside `benchmark/results/` (refused inside it).
2. `after_eligible` for `permission` sites needs a note that mentions permission to change or deploy.
3. The README's Results section stays "Not yet measured" until the owner runs the benchmark.
