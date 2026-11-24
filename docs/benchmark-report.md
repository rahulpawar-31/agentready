# Benchmark evaluation report

The task-runner CLI stores one immutable JSON file per site, task, repeat and snapshot under
`benchmark/results/`. After recording before and after task runs and audit snapshots, generate the
evaluation summary from the committed site labels:

```bash
cd backend
python -m app.runner.report --before before --after after
python -m app.runner.report --before before --after after --format json --output ../benchmark/results/evaluation.json
```

The report shows task success rates and sample sizes for each snapshot, every valid failed task and
its reason, invalid runs excluded from the denominator, and the per-site/task spread across repeats.
It also reports a paired before/after change using only matching site, task and repeat records whose
criteria, label and runner configuration match. Sites with before-only results remain visible in the
snapshot summary but do not enter the paired comparison.

Results are summarized as recorded. The report never turns an invalid model or harness run into a task
failure, overwrites run files, or claims the measurements generalize beyond the labelled sites.

## Audit-check accuracy

Each site label also carries an `audit_checks` map with all 13 check IDs. A value of `true` means a
human's preregistered expected outcome is that the check passes; `false` means it should fail. Set
these labels before running an audit and commit the label file. The example booleans are illustrative
only and must be replaced after independent inspection.

Run one owner-verified audit per site and snapshot:

```bash
cd backend
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

Each saved audit includes the SHA-256 of its committed site label. If a label changes after collection,
the report excludes the audit and reports a data problem.

The report exits nonzero when it finds malformed task or audit result files, results without a
corresponding site label, changed labels, or duplicate runs. Missing task repeats, audit reports and
check findings are shown as incomplete samples rather than silently treated as failures. It refuses
to write over an existing run file, and refuses before/after names that resolve to the same snapshot
directory.
