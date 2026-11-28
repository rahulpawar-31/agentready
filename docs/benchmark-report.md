# Benchmark evaluation report

The task-runner CLI stores one immutable JSON file per site, task, repeat and snapshot under
`benchmark/results/`. After recording the before and after runs (the full procedure is in
[benchmark.md](benchmark.md)), generate an evaluation summary from the committed site labels. Write the report
**outside** `benchmark/results/`: the command refuses to write inside that tree, because a report saved there would be read
back as a result on the next run.

```bash
cd backend
python -m app.runner.report --before before --after after
python -m app.runner.report --before before --after after --output ../benchmark/evaluation.md
python -m app.runner.report --before before --after after --format json --output ../benchmark/evaluation.json
```

The report shows, per snapshot, task success rates with the number of independent units behind them ("N sites, M
site-task cells, K runs"), a per-task table, every valid failed task and its reason, invalid runs excluded from the
denominator, the per-site and per-task spread across repeats, and the provenance found in the runs (criteria hash, harness
hash, model that was asked, model that answered, whether the run was pre-registered, commit). It also reports a paired
before/after change using only matching site, task and repeat records whose configuration matches, with the counts of fail
to pass, pass to fail, pass to pass and fail to fail **overall, per task and per site**, so the pooled figure is never shown
alone (the three tasks differ in difficulty). Sites with before-only results remain visible in the snapshot summary but do
not enter the paired comparison. The small-sample note ("Small sample: treat differences as indicative only") is based on
independent units: it appears while fewer than 30 sites or fewer than 30 site-task cells are behind the smallest of the
before, after and paired sets, because repeats of one cell are not independent samples. The report computes no statistical
test.

Results are summarized as recorded. The report never turns an invalid model or harness run into a task failure,
overwrites run files, or claims the measurements generalize beyond the labelled sites. The OpenAPI-to-MCP stub is not
counted in task success (FR-6.4). Text read from result files is clipped, stripped of control characters and made safe for
Markdown before it is printed.

Task results and audit reports include `request_log` and `verification_request_log`: actual outbound GETs from
the guard, including each redirect hop, with the pinned address and response status. Blocked targets and cache
hits send no request and add no entry. A batch's verification requests are attached to its first task result
only, including an invalid attempt, so summing retained results does not count verification repeatedly.
Logs contain no response bodies, headers or query values; `query_redacted` records whether a query was removed.
Each task snapshot and audit snapshot reports boundary integrity from these logs. Missing logs are explicitly
incomplete (or not measured if there are no records), never evidence of zero outbound requests. `verified`
means all recorded requests stayed on the labelled host with public pinned addresses and GET methods;
it describes the recorded runs only. Invalid attempts retained for reruns are included in request totals.

The criteria version is now 4. Its new harness hash includes the runner's sensitive-path fetch policy:
`/.env` and `/.git/` are blocked before every redirect request, and sensitive final/cache responses never reach
the model. Version 3 introduced required offer/pricing patterns and request logs. Replace legacy flat audit labels
with snapshot maps and commit the completed labels and criteria
before collecting new runs. Older results remain readable, but missing logs cannot verify boundary integrity,
and results with the old harness hash cannot be paired with new runs. No measured benchmark was collected
before this format change.

## Audit-check accuracy

Each site label also carries `audit_checks.before` and `audit_checks.after` maps, each with all 13 check IDs. A value of `true` means a
human's preregistered expected outcome is that the check passes; `false` means it should fail. Set
both snapshot maps before the first run and commit the label file. Use the expected deployed outcomes for after;
the report compares each audit against its own snapshot's map. Flat maps are rejected. The example booleans are illustrative
only and must be replaced after independent inspection.

Run one owner-verified audit per site and snapshot:

```bash
cd backend
export AGENTREADY_SECRET=<the same value the API server uses>
python -m app.runner.audit_cli --label ../benchmark/labels/example.test.json --snapshot before
```

The command proves control and uses the same guarded fetch layer as the API. It records findings
immutably under `benchmark/results/audits/<snapshot>/<host>.json`; a site cannot be overwritten in
the same snapshot. Use a new snapshot name for a fresh audit.

The report compares each reachable finding's `passed` value with its preregistered label. A predicted
readiness issue (`passed=false`) is the positive class. It reports per-check agreement and
false-positive rate, plus TP/FP/TN/FN counts, unreachable findings and missing outcomes. Unreachable
findings are excluded from the rates. A check with no labelled outcomes remains unmeasured for that
snapshot. These results describe only the labelled sites.

If none of the three standard OpenAPI paths yields a document and any path fails or returns a server error,
both OpenAPI and auth-declaration findings are unreachable and excluded, regardless of which path failed.
A valid document at another standard path still establishes the observation.

Each saved audit includes the SHA-256 of its committed site label. If a label changes after collection,
the report excludes the audit and reports a data problem.

## What makes the command exit with a failure

The command exits nonzero when it finds any of these data issues (each is listed under "Data issues", naming the run):

- a malformed or unreadable result file, a symlink inside the results tree (it is ignored), or a result whose `valid`
  flag is missing or not true/false;
- a result for a site with no committed label, or duplicate valid runs;
- a result file whose location (`<snapshot>/<site>/<task>-<repeat>.json`) does not match the site, task and repeat
  recorded inside it (a file that was moved or edited), a stray file in the wrong place, or a run recorded for a different
  snapshot (a copied file);
- a valid run that was not pre-registered (made with `--allow-uncommitted`), that lacks one of the required configuration
  fields, or whose recorded criteria hash, harness hash or label hash no longer matches the committed
  `benchmark/criteria.json` or the current label file (the label or criteria were edited after the run). Such runs are
  kept in the snapshot counts, flagged, and never paired;
- more than one criteria hash, harness hash, model or answering model inside one snapshot;
- an `after` result for a site that is not `after_eligible` in `benchmark/sites.json` (when that file exists);
- a recorded request log with an off-host URL, a non-public pinned address, a method other than GET or malformed entries;
- a snapshot name that has no directory (a typo), unless both names are missing, which is the "no results yet" report.

Missing repeats are shown as incomplete samples and never counted as failures. A missing git commit on a run is a warning,
not a failure. The command refuses to write over an existing run file, refuses to write anywhere inside the results
directory or to a symlink, and refuses before/after names that are the same snapshot (also when they differ only by case).
