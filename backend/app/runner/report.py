"""Summarise preregistered AgentReady task runs without changing or deleting result files.

Run from ``backend`` with ``python -m app.runner.report --before before --after after``.

The report only reads ``benchmark/results``. It refuses to write its own output anywhere inside that tree (a report
saved there would be read back as a result on the next run). Text taken from result files is clipped and stripped of
control characters, and Markdown-active characters are removed when it is rendered.
"""
import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .labels import load_label
from .results import load_invalid_runs, load_runs
from .tasks import TASK_IDS

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COMPARE_CONFIG = ("criteria_sha256", "harness_sha256", "label_sha256", "model", "preregistered", "max_turns",
                  "request_budget", "rate_limit_seconds", "step_limit", "toolset_version")
# Fields that must have exactly one value inside one snapshot. (git_head is listed but may differ: the commit moves
# whenever a new site label is committed between runs; the harness hash is what must stay fixed.)
PROVENANCE_FIELDS = ("criteria_sha256", "harness_sha256", "model", "git_head")
MUST_BE_UNIFORM = ("criteria_sha256", "harness_sha256", "model")
SMALL_SAMPLE = 30
TEXT_CLIP = 200
MCP_STUB_NOTE = "The MCP stub is not counted in task success (FR-6.4)."


def _text(value, limit: int = TEXT_CLIP) -> str:
    """Single-line, control-character-free, clipped text for anything that came from a result file."""
    out = []
    for ch in str(value):
        cat = unicodedata.category(ch)
        out.append(" " if cat in ("Cc", "Zl", "Zp", "Zs") else "" if cat == "Cf" else ch)
    text = re.sub(r" {2,}", " ", "".join(out)).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


_MD_ACTIVE = str.maketrans({"[": "", "]": "", "(": "", ")": "", "<": "", ">": "", "`": "", "|": "/", "*": "", "\\": "/",
                            "#": ""})


def _md(value) -> str:
    """Text safe to place on a Markdown line: no links, HTML, code spans, emphasis, headings or table cells."""
    return _text(value).translate(_MD_ACTIVE)


def _labels(root: Path) -> tuple[dict[str, dict], list[str]]:
    labels, problems = {}, []
    for path in sorted(Path(root).glob("*.json")):
        if path.name == "EXAMPLE.json.example":
            continue
        try:
            label = load_label(path, check_name=True)
        except (OSError, ValueError) as exc:
            problems.append(f"label {_text(path.name, 80)}: {_text(exc)}")
            continue
        labels[label.host] = {"site": label.site, "host": label.host}
    return labels, problems


def _load_snapshot(root: Path, snapshot: str, labels: dict[str, dict], repeats: int) -> tuple[dict, list[str]]:
    problems: list[str] = []
    runs_by_key = {}
    invalid_by_key = defaultdict(list)
    raw_runs = load_runs(root, snapshot, problems)
    invalid_runs = load_invalid_runs(root, snapshot, problems)
    problems = [_text(p) for p in problems]

    def accept(row: dict, invalid_file: bool) -> None:
        site, task, repeat = row.get("site"), row.get("task"), row.get("repeat")
        result, config = row.get("result"), row.get("config")
        rel = f"{_text(site, 60)}/{_text(task, 30)}#{_text(repeat, 10)}"
        if not isinstance(site, str):
            problems.append(f"{snapshot}/{rel}: site must be a string")
            return
        if site not in labels:
            problems.append(f"{snapshot}/{rel}: no committed site label")
            return
        if not isinstance(task, str) or task not in TASK_IDS:
            problems.append(f"{snapshot}/{rel}: invalid task or repeat number")
            return
        if not isinstance(repeat, int) or isinstance(repeat, bool) or not 1 <= repeat <= repeats:
            problems.append(f"{snapshot}/{rel}: invalid task or repeat number")
            return
        if not isinstance(result, dict) or not isinstance(config, dict):
            problems.append(f"{snapshot}/{rel}: missing result or configuration")
            return
        if result.get("task_id") not in (None, task):
            problems.append(f"{snapshot}/{rel}: the recorded task_id does not match the file's task")
            return
        recorded = config.get("snapshot")
        if recorded not in (None, snapshot):
            problems.append(f"{snapshot}/{rel}: this run was recorded for snapshot '{_text(recorded, 64)}', "
                            f"not '{snapshot}' (a result file was copied between snapshots)")
            return
        key = (site, task, repeat)
        if invalid_file or result.get("valid") is not True:
            invalid_by_key[key].append({"reason": _text(result.get("reason") or "invalid run"),
                                        "error": _text(result.get("error") or "")})
            return
        if not isinstance(result.get("success"), bool):
            problems.append(f"{snapshot}/{rel}: success is not a boolean")
            return
        if key in runs_by_key:
            problems.append(f"{snapshot}/{rel}: duplicate valid result")
            return
        runs_by_key[key] = {"result": result, "config": config}

    for row in raw_runs:
        if isinstance(row, dict):
            accept(row, invalid_file=False)
    for row in invalid_runs:
        if isinstance(row, dict):
            accept(row, invalid_file=True)

    expected_sites = sorted(labels)
    site_tasks = {}
    for site in expected_sites:
        site_tasks[site] = {}
        for task in TASK_IDS:
            entries = [runs_by_key.get((site, task, repeat)) for repeat in range(1, repeats + 1)]
            present = [entry for entry in entries if entry is not None]
            success_count = sum(bool(entry["result"]["success"]) for entry in present)
            values = [100.0 if entry["result"]["success"] else 0.0 for entry in present]
            reasons = []
            for repeat, entry in enumerate(entries, start=1):
                if entry is not None and not entry["result"]["success"]:
                    reasons.append({"repeat": repeat, "reason": _text(entry["result"].get("reason") or "failed")})
            invalid_count = sum(len(invalid_by_key.get((site, task, repeat), []))
                                for repeat in range(1, repeats + 1))
            spread = max(values) - min(values) if len(values) >= 2 else None
            site_tasks[site][task] = {
                "valid_runs": len(present), "expected_runs": repeats, "successes": success_count,
                "success_rate_pct": round(100 * success_count / len(present), 1) if present else None,
                "repeat_spread_pp": spread, "invalid_runs": invalid_count,
                "missing_repeats": [i for i, entry in enumerate(entries, start=1) if entry is None],
                "failures": reasons,
            }

    valid = list(runs_by_key.values())
    invalid_count = sum(len(rows) for rows in invalid_by_key.values())
    successes = sum(bool(item["result"]["success"]) for item in valid)

    provenance = {}
    for name in PROVENANCE_FIELDS:
        values = sorted({_text(item["config"].get(name), 80) for item in valid if item["config"].get(name) not in (None, "")})
        provenance[name] = values
        if name in MUST_BE_UNIFORM and len(values) > 1:
            problems.append(f"{snapshot}: more than one value in {name} among the valid runs ({', '.join(values)})")

    summary = {
        "snapshot": snapshot,
        "labelled_sites": len(labels),
        "sites_with_valid_runs": len({site for site, _, _ in runs_by_key}),
        "valid_runs": len(valid), "successes": successes,
        "success_rate_pct": round(100 * successes / len(valid), 1) if valid else None,
        "invalid_runs": invalid_count,
        "failures": [{"site": site, "task": task, "repeat": repeat,
                      "reason": _text(item["result"].get("reason") or "failed")}
                     for (site, task, repeat), item in sorted(runs_by_key.items())
                     if not item["result"]["success"]],
        "invalid": [{"site": site, "task": task, "repeat": repeat, **item}
                    for (site, task, repeat), entries in sorted(invalid_by_key.items()) for item in entries],
        "provenance": provenance,
        "sites": site_tasks,
        "_valid_by_key": runs_by_key,
        "_invalid_by_key": invalid_by_key,
    }
    return summary, problems


def _paired(before: dict, after: dict) -> dict:
    left, right = before["_valid_by_key"], after["_valid_by_key"]
    pairs, config_mismatches = [], []
    for key in sorted(left.keys() & right.keys()):
        a, b = left[key], right[key]
        changed = [name for name in COMPARE_CONFIG if a["config"].get(name) != b["config"].get(name)]
        if changed:
            config_mismatches.append({"site": key[0], "task": key[1], "repeat": key[2], "fields": changed})
            continue
        pairs.append((key, bool(a["result"]["success"]), bool(b["result"]["success"])))
    n = len(pairs)
    before_success = sum(a for _, a, _ in pairs)
    after_success = sum(b for _, _, b in pairs)
    transitions = {
        "fail_to_pass": sum(1 for _, a, b in pairs if not a and b),
        "pass_to_fail": sum(1 for _, a, b in pairs if a and not b),
        "pass_to_pass": sum(1 for _, a, b in pairs if a and b),
        "fail_to_fail": sum(1 for _, a, b in pairs if not a and not b),
    }
    return {
        "valid_paired_runs": n,
        "before_successes": before_success,
        "after_successes": after_success,
        "before_success_rate_pct": round(100 * before_success / n, 1) if n else None,
        "after_success_rate_pct": round(100 * after_success / n, 1) if n else None,
        "change_pp": round(100 * (after_success - before_success) / n, 1) if n else None,
        "transitions": transitions,
        "config_mismatches_excluded": config_mismatches,
        "unpaired_before_runs": len(left) - n - len(config_mismatches),
        "unpaired_after_runs": len(right) - n - len(config_mismatches),
    }


def build_report(results: Path, labels_dir: Path, before_name: str, after_name: str, repeats: int = 3) -> dict:
    results, labels_dir = Path(results), Path(labels_dir)
    labels, label_problems = _labels(labels_dir)
    before, before_problems = _load_snapshot(results, before_name, labels, repeats)
    after, after_problems = _load_snapshot(results, after_name, labels, repeats)
    paired = _paired(before, after)
    for snapshot in (before, after):
        snapshot.pop("_valid_by_key", None)
        snapshot.pop("_invalid_by_key", None)
    recorded = (before["valid_runs"] + before["invalid_runs"] + after["valid_runs"] + after["invalid_runs"]) > 0
    return {
        "title": "AgentReady benchmark evaluation",
        "repeats_expected": repeats,
        "results_recorded": recorded,
        "small_sample": {"threshold": SMALL_SAMPLE,
                         "is_small": min(before["valid_runs"], after["valid_runs"]) < SMALL_SAMPLE
                         or paired["valid_paired_runs"] < SMALL_SAMPLE},
        "before": before,
        "after": after,
        "paired_comparison": paired,
        "audit_check_accuracy": {
            "status": "not measured",
            "reason": "The current benchmark labels and task-run files do not contain hand-labelled audit-check outcomes and audit findings.",
        },
        "problems": label_problems + before_problems + after_problems,
        "limitations": [
            "Task success describes only the labelled sites and recorded runs.",
            "Invalid model or harness runs are excluded from success-rate denominators and counted separately.",
            "Paired change includes only matching site, task and repeat records with matching runner configuration.",
            "Audit-check accuracy is not calculated until labelled audit outcomes and corresponding audit findings are recorded.",
            MCP_STUB_NOTE,
        ],
    }


def to_markdown(report: dict) -> str:
    lines = ["# AgentReady benchmark evaluation", "",
             f"Expected repeats per site and task: {report['repeats_expected']}.", ""]
    if not report.get("results_recorded", True):
        lines += ["No results recorded yet. Run the benchmark first (see docs/benchmark.md); nothing below is a measurement.", ""]
    for label in ("before", "after"):
        snap = report[label]
        rate = "not available" if snap["success_rate_pct"] is None else f"{snap['success_rate_pct']}%"
        lines += [f"## {label.title()} ({_md(snap['snapshot'])})", "",
                  f"- Task success: {snap['successes']}/{snap['valid_runs']} valid runs ({rate})",
                  f"- Sites with results: {snap['sites_with_valid_runs']}/{snap['labelled_sites']}",
                  f"- Invalid runs excluded: {snap['invalid_runs']}", "",
                  "| Site | Task | Success | Repeat spread | Valid/expected | Invalid |",
                  "|---|---|---:|---:|---:|---:|"]
        for site, tasks in snap["sites"].items():
            for task, item in tasks.items():
                pct = "—" if item["success_rate_pct"] is None else f"{item['success_rate_pct']}%"
                spread = "—" if item["repeat_spread_pp"] is None else f"{item['repeat_spread_pp']:.0f} pp"
                lines.append(f"| {site} | {task} | {pct} | {spread} | {item['valid_runs']}/{item['expected_runs']} | {item['invalid_runs']} |")
        if snap["failures"]:
            lines += ["", "Failures:"]
            lines.extend(f"- {x['site']} / {x['task']} repeat {x['repeat']}: {_md(x['reason'])}"
                         for x in snap["failures"])
        if snap["invalid"]:
            lines += ["", "Invalid runs (excluded from success rate):"]
            lines.extend(f"- {x['site']} / {x['task']} repeat {x['repeat']}: {_md(x['reason'])}"
                         + (f" ({_md(x['error'])})" if x["error"] else "") for x in snap["invalid"])
        lines.append("")
    lines += ["## Provenance", ""]
    for label in ("before", "after"):
        prov = report[label].get("provenance", {})
        parts = [f"{name}: {', '.join(_md(v) for v in prov.get(name, [])) or 'none recorded'}"
                 for name in PROVENANCE_FIELDS]
        lines.append(f"- {label} ({_md(report[label]['snapshot'])}): " + "; ".join(parts))
    lines.append("")
    pair = report["paired_comparison"]
    before_pct = "—" if pair["before_success_rate_pct"] is None else f"{pair['before_success_rate_pct']}%"
    after_pct = "—" if pair["after_success_rate_pct"] is None else f"{pair['after_success_rate_pct']}%"
    change = "—" if pair["change_pp"] is None else f"{pair['change_pp']:+.1f} pp"
    t = pair["transitions"]
    lines += ["## Paired comparison", "",
              f"Same site/task/repeat runs: {pair['valid_paired_runs']}; before {before_pct}; after {after_pct}; change {change}.",
              f"Per pair: fail to pass {t['fail_to_pass']}, pass to fail {t['pass_to_fail']}, "
              f"pass to pass {t['pass_to_pass']}, fail to fail {t['fail_to_fail']}.",
              f"Excluded for a different configuration: {len(pair['config_mismatches_excluded'])}; "
              f"before-only runs: {pair['unpaired_before_runs']}; after-only runs: {pair['unpaired_after_runs']}.", ""]
    small = report.get("small_sample", {})
    lines += ["## Sample size", "",
              f"Valid runs: before {report['before']['valid_runs']}, after {report['after']['valid_runs']}, "
              f"paired {pair['valid_paired_runs']}."]
    if small.get("is_small", True):
        lines.append(f"Small sample: treat differences as indicative only (fewer than {small.get('threshold', SMALL_SAMPLE)} runs).")
    lines += ["No statistical test is computed, and nothing here applies beyond the sites tested.", ""]
    lines += ["## Audit check accuracy", "", report["audit_check_accuracy"]["status"],
              "", report["audit_check_accuracy"]["reason"], "", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    if report["problems"]:
        lines += ["", "## Data issues", ""]
        lines.extend(f"- {_md(item)}" for item in report["problems"])
    return "\n".join(lines).rstrip() + "\n"


def _refuse_output(parser, output: Path, results_root: Path) -> Path:
    """Where the report may be written: never a symlink, never inside the results tree."""
    if os.path.islink(output):
        parser.error("--output must not be a symlink")
    resolved = output.resolve()
    root = results_root.resolve()
    if resolved == root or root in resolved.parents:
        parser.error("--output must be outside the results directory (a report saved there would be read as a result)")
    return resolved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Summarise AgentReady before/after benchmark task runs.")
    parser.add_argument("--before", required=True, help="before snapshot directory name")
    parser.add_argument("--after", required=True, help="after snapshot directory name")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--results", type=Path, default=REPO_ROOT / "benchmark" / "results")
    parser.add_argument("--labels", type=Path, default=REPO_ROOT / "benchmark" / "labels")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="write the report here (outside --results); defaults to stdout")
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be between 1 and 10")
    if not SNAPSHOT.fullmatch(args.before) or not SNAPSHOT.fullmatch(args.after):
        parser.error("snapshot names must match [A-Za-z0-9_-]{1,64}")
    if args.before.casefold() == args.after.casefold() or \
            (args.results / args.before).resolve() == (args.results / args.after).resolve():
        parser.error("--before and --after must name different snapshots")
    output_path = _refuse_output(parser, args.output, args.results) if args.output else None
    report = build_report(args.results, args.labels, args.before, args.after, args.repeats)
    output = (json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n" if args.format == "json"
              else to_markdown(report))
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
