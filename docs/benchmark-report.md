# Benchmark evaluation report

The task-runner CLI stores one immutable JSON file per site, task, repeat and snapshot under
`benchmark/results/`. After recording the before and after runs, generate an evaluation summary from
the committed site labels:

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

## Audit-check accuracy is separate

The current benchmark labels specify the three agent tasks; they do not contain expected outcomes for
the audit checks, and task-run files do not contain audit findings. Therefore this report explicitly
marks audit-check accuracy as **not measured**. To report per-check agreement and false-positive rates,
first preregister expected pass/fail labels for every audit check and save the corresponding audit
findings for each site and snapshot. Do not infer those labels from task results.

The command exits nonzero when it finds malformed result files, results without a corresponding site
label, or duplicate valid runs. Missing repeats and missing snapshots are shown in the report as
incomplete samples rather than silently treated as failures.
