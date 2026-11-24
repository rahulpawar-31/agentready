# Benchmark protocol

This is the procedure for measuring whether an AI agent can find and read information on a site before and after the
owner applies AgentReady's approved fixes. **Nothing in the repository is a result of this benchmark until you run it.**
The numbers come only from recorded runs in `benchmark/results/`, through `python -m app.runner.report`.

## What is measured, and what is not

Three tasks per site, judged by code against a hand label written beforehand (never by the model):

| Task | Success means |
|---|---|
| `main_info` | the agent fetched the labelled page and quoted text that is on it |
| `contact_form` | the agent fetched the labelled contact page and named every required field |
| `docs_api` | the agent fetched the labelled docs or API page, or looked and correctly said none exists |

This measures **discovery and retrieval** of information on the sites tested. It does not measure whether an agent can
safely act on a site, and it says nothing about sites that were not tested. The full success and failure wording is in
`benchmark/criteria.json`, which is pre-registered: the runner refuses to start unless it is committed and unmodified.

Not counted: the OpenAPI-to-MCP stub (FR-6.4). It is validated and can be shown in the demo, but the runner has no MCP
client for it, so the results table excludes it. Audit-check accuracy is **not measured** either, because there are no
hand-labelled expected outcomes for the audit checks; the report says so and never infers them from task results.

## Which sites may be used

1. Only sites you control, or have **written permission** to test. Record the permission in `permission_note` in
   `benchmark/sites.json` (a pointer to where the written OK is kept, not the OK itself).
2. Only sites that are verified (proof of control) are run. A host that resolves to a private or reserved address
   cannot be audited, so use a public staging host, not a local server.
3. A site gets an **after** run only if the team controls it, or the written permission covers changing or deploying to
   it (`after_eligible`, with `after_method` staging or live). Every other site is reported as before-only.
4. Employer sites (the PRD names CountryStateCity and MakeMySiteLive) need the employer's written OK first, recorded in
   `permission_note`. Ask in the Homingo Discord whether pre-existing work is allowed.
5. Start with five sites. Expand toward twenty only if authorization, labelling and caching stay manageable.

## Steps for one site

All commands run from the repository root unless a `cd` is shown.

1. **Register the site** in `benchmark/sites.json` (see `benchmark/sites.example.json`). The file starts empty.
2. **Write the label** by hand after looking at the site yourself: `benchmark/labels/<host>.json`, format in
   `benchmark/labels/README.md`. Commit the label. It must be committed before any run.
3. **Prove control**: start the API, then add the meta tag (inside `<head>` on the home page) or the DNS TXT record that
   `GET /ownership-token?domain=<host>` (or the web page's step 2) shows you.

       cd backend
       export AGENTREADY_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
       uvicorn app.main:app --port 8000

   Keep the same `AGENTREADY_SECRET` for the whole benchmark: the ownership token is derived from it.
4. **Run the pre-flight**. It lists exactly what is still missing and never prints a secret.

       cd backend
       export ANTHROPIC_API_KEY=...       # never commit it
       pip install -e '.[runner]'
       python -m app.runner.preflight                    # offline: makes no network request
       python -m app.runner.preflight --check-control    # also verifies ownership of each listed site

   Exit code 0 means nothing blocks the run. Warnings (for example "1 of the target 5 sites") need a look.
5. **Before runs**, three repeats:

       cd backend && python -m app.runner.cli run --label ../benchmark/labels/<host>.json --snapshot before --repeats 3

   A run whose model call failed or whose tools broke is marked **invalid**, kept, and rerun with the same command
   (valid results are never overwritten). Invalid runs are reported separately and never counted as failures.
6. **Propose fixes**: audit the site in the web page (or `POST /audit`), then "Propose fixes". Review each diff.
7. **Approve, download, deploy**: the owner approves a fix, then downloads or copies the file and **deploys it
   themselves**. Approving changes nothing on the site, and AgentReady never deploys or writes to a site.
8. **Wait until the live site serves the new files**, then re-audit with a fresh snapshot (the page's "Re-audit with
   fresh pages", or `POST /audit` with `"snapshot": "after"`) to see the new score.
9. **After runs**, only for sites that are `after_eligible`, with a different snapshot name so the cache is cold:

       cd backend && python -m app.runner.cli run --label ../benchmark/labels/<host>.json --snapshot after --repeats 3

   The audit and the runner keep their cached pages in the same place for the same snapshot name. If you re-audited with
   `"snapshot": "after"` (step 8) after the deploy, the after runs reuse those pages; with any other snapshot name
   (such as the page's `rerun-...` names) the runner fetches its own. Either way, only create the `after` cache after
   the new files are live, because a cached page is never refetched inside its snapshot. Use the same label, the same
   model and the same limits as the before runs (they are recorded and compared).
10. **Report**, written outside `benchmark/results/` (the command refuses to write inside it, because a report saved there
    would be read back as a result):

        cd backend
        python -m app.runner.report --before before --after after --output ../benchmark/evaluation.md
        python -m app.runner.report --before before --after after --format json --output ../benchmark/evaluation.json

## What the report shows

Per snapshot: valid runs and successes with the number of independent units behind them ("N sites, M site-task cells,
K runs"), a per-task table, every failure and its reason, invalid runs (excluded from the denominator and listed), the
spread across the repeats, and the provenance (criteria hash, harness hash, model, answering model, pre-registration and
commit found in the runs). Then a paired before/after comparison that uses only runs of the same site, task and repeat
whose criteria, label, harness, model, answering model, limits and guard settings match, with the counts of fail to pass,
pass to fail, pass to pass and fail to fail overall, per task and per site. Before-only sites stay visible but are not
paired. The report flags, and never pairs, runs that were not pre-registered or whose criteria or label changed after the
run, and it refuses an `after` result for a site that is not `after_eligible` in `benchmark/sites.json`. While fewer than 30
sites or 30 site-task cells stand behind the results it says "Small sample: treat differences as indicative only"
(repeats of one cell are not independent samples). It computes no statistical test.

## What you may and may not claim

- You may publish the whole report, including failures, invalid runs and the small-sample note.
- Claim results only for the sites tested, only for the three tasks, and only as measured.
- Do not generalise to other sites, do not claim audit-check accuracy without hand-labelled audit outcomes, and do not
  count the MCP stub. A change on a handful of sites is an observation, not proof.

## Cost and time

Model calls are about: sites x 3 tasks x repeats, once for the before snapshot and once more for each after-eligible
site. Each run is limited to 10 page fetches and 24 model turns. Check your own provider pricing; no price is assumed here.
The guard allows one request per half second and at most 60 requests per run.

## If something goes wrong

- *Pre-flight says a label is not committed*: commit it (the runner compares the file with the blob in `HEAD`).
- *The criteria hash no longer matches*: a tool, prompt, task wording or limit changed. Regenerate and commit
  `benchmark/criteria.json` **before any run**; never change it between the before and after runs.
- *Control is not proven*: the token is derived from `AGENTREADY_SECRET`; use the same secret that was used when you
  added the tag or record.
- *A host is refused as non-public*: audit a public staging host instead.
