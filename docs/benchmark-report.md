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

## Audit-check accuracy is separate

The current benchmark labels specify the three agent tasks; they do not contain expected outcomes for
the audit checks, and task-run files do not contain audit findings. Therefore this report explicitly
marks audit-check accuracy as **not measured**. To report per-check agreement and false-positive rates,
first preregister expected pass/fail labels for every audit check and save the corresponding audit
findings for each site and snapshot. Do not infer those labels from task results.

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
- a snapshot name that has no directory (a typo), unless both names are missing, which is the "no results yet" report.

Missing repeats are shown as incomplete samples and never counted as failures. A missing git commit on a run is a warning,
not a failure. The command refuses to write over an existing run file, refuses to write anywhere inside the results
directory or to a symlink, and refuses before/after names that are the same snapshot (also when they differ only by case).
