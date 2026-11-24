"""Summarise preregistered AgentReady task runs without changing or deleting result files.

Run from ``backend`` with ``python -m app.runner.report --before before --after after``.

The report only reads ``benchmark/results``. It refuses to write its own output anywhere inside that tree (a report
saved there would be read back as a result on the next run). Text taken from result files is clipped and stripped of
control characters, and Markdown-active characters are removed when it is rendered.
"""
import argparse
import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .labels import load_label
from .results import read_run_files, site_slug
from .tasks import TASK_IDS

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COMPARE_CONFIG = ("criteria_sha256", "harness_sha256", "label_sha256", "model", "preregistered", "max_turns",
                  "request_budget", "rate_limit_seconds", "step_limit", "toolset_version")
# Fields that must have exactly one value inside one snapshot. (git_head is listed but may differ: the commit moves
# whenever a new site label is committed between runs; the harness hash is what must stay fixed.)
PROVENANCE_FIELDS = ("criteria_sha256", "harness_sha256", "model", "git_head", "preregistered")
MUST_BE_UNIFORM = ("criteria_sha256", "harness_sha256", "model", "answered_model")
# A valid run without every one of these cannot be trusted or paired: absent must never equal absent.
REQUIRED_CONFIG = ("criteria_sha256", "harness_sha256", "label_sha256", "model", "step_limit", "max_turns",
                   "toolset_version", "snapshot")
FILE_NAME = re.compile(r"^(?P<task>[a-z_]+)-(?P<repeat>[0-9]+)(?:\.invalid-[0-9]+)?\.json$")
SMALL_SAMPLE = 30          # independent units (sites, and site-task cells): repeats of one cell are not independent
TEXT_CLIP = 200
GENERATED_BY = "app.runner.report"
MARKDOWN_MARKER = "<!-- agentready-report -->"
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


_RISKY = re.compile(r"(?i)(https?://\S+|www\.\S+|(?<![\w`])@\w[\w.-]*)")


def _md(value) -> str:
    """Text safe to place on a Markdown line: no links, HTML, code spans, emphasis, headings or table cells. Bare
    URLs and @mentions that survive are wrapped in backticks, so a renderer neither links them nor notifies anyone
    when the report is pasted into a pull request."""
    return _RISKY.sub(lambda m: f"`{m.group(0)}`", _text(value).translate(_MD_ACTIVE))


def _labels(root: Path) -> tuple[dict[str, dict], list[str]]:
    labels, problems = {}, []
    for path in sorted(Path(root).glob("*.json")):
        if path.name == "EXAMPLE.json.example":
            continue
        try:
            label = load_label(path, check_name=True)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError, RecursionError) as exc:
            problems.append(f"label {_text(path.name, 80)}: {_text(exc if isinstance(exc, ValueError) else 'not readable')}")
            continue
        labels[label.host] = {"site": label.site, "host": label.host, "sha256": digest}
    return labels, problems


def _criteria(path) -> tuple[str | None, str | None, str | None]:
    """(sha256 of the current criteria file, its harness_sha256, a problem). A None path skips the check."""
    if path is None:
        return None, None, None
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None, None, "the criteria file could not be read, so the runs' criteria hashes were not checked"
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        data = None
    harness = data.get("harness_sha256") if isinstance(data, dict) else None
    return hashlib.sha256(raw).hexdigest(), harness if isinstance(harness, str) else None, None


def _registry(path) -> tuple[set[str] | None, list[str]]:
    """The hosts that may have an after run, from benchmark/sites.json. None means there is no registry to enforce."""
    if path is None or not Path(path).exists():
        return None, []
    from .preflight import load_sites                      # imported here: preflight imports the runner CLI
    sites, problems = load_sites(path)
    return {s.host for s in sites if s.after_eligible}, [f"site registry: {_text(p)}" for p in problems]


def _short(digest) -> str:
    return _text(digest, 8)


def _transitions(pairs) -> dict:
    return {
        "fail_to_pass": sum(1 for _, a, b in pairs if not a and b),
        "pass_to_fail": sum(1 for _, a, b in pairs if a and not b),
        "pass_to_pass": sum(1 for _, a, b in pairs if a and b),
        "fail_to_fail": sum(1 for _, a, b in pairs if not a and not b),
    }


def _counts(keys) -> dict:
    keys = list(keys)
    return {"sites": len({k[0] for k in keys}), "cells": len({(k[0], k[1]) for k in keys}), "runs": len(keys)}


def _load_snapshot(root: Path, snapshot: str, labels: dict[str, dict], repeats: int, check: dict) -> tuple[dict, list[str]]:
    """`check` carries what the runs are verified against: criteria_sha, criteria_harness, eligible (hosts allowed an
    after run, or None) and is_after."""
    problems: list[str] = []
    runs_by_key = {}
    invalid_by_key = defaultdict(list)
    files = read_run_files(root, snapshot, problems)
    problems = [_text(p) for p in problems]

    def accept(rel_path: Path, row: dict, invalid_file: bool) -> None:
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
        parts = rel_path.parts
        named = FILE_NAME.fullmatch(parts[-1]) if len(parts) == 2 else None
        if named is None:
            problems.append(f"{snapshot}/{rel}: unexpected result file {_text(rel_path.as_posix(), 100)} "
                            f"(expected <site>/<task>-<repeat>.json)")
            return
        if parts[0] != site_slug(site) or named["task"] != task or int(named["repeat"]) != repeat:
            problems.append(f"{snapshot}/{rel}: the file {_text(rel_path.as_posix(), 100)} does not match its recorded "
                            f"site, task and repeat (a file was moved or edited)")
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
        valid_flag = result.get("valid")
        if not isinstance(valid_flag, bool):
            problems.append(f"{snapshot}/{rel}: 'valid' is missing or not true/false")
            return
        key = (site, task, repeat)
        if invalid_file or valid_flag is False:
            invalid_by_key[key].append({"reason": _text(result.get("reason") or "invalid run"),
                                        "error": _text(result.get("error") or "")})
            return
        if not isinstance(result.get("success"), bool):
            problems.append(f"{snapshot}/{rel}: success is not a boolean")
            return
        if key in runs_by_key:
            problems.append(f"{snapshot}/{rel}: duplicate valid result")
            return
        flaws = []
        missing = [name for name in REQUIRED_CONFIG if config.get(name) in (None, "")]
        if missing:
            flaws.append(f"missing config field(s): {', '.join(missing)}")
        if config.get("preregistered") is not True:
            flaws.append("not pre-registered (made with --allow-uncommitted, or before the pre-registration check)")
        crit = config.get("criteria_sha256")
        if check["criteria_sha"] and crit not in (None, "") and crit != check["criteria_sha"]:
            flaws.append(f"criteria hash {_short(crit)} differs from the current benchmark/criteria.json "
                         f"{_short(check['criteria_sha'])}")
        harness = config.get("harness_sha256")
        if check["criteria_harness"] and harness not in (None, "") and harness != check["criteria_harness"]:
            flaws.append(f"harness hash {_short(harness)} differs from the harness hash in the criteria file "
                         f"{_short(check['criteria_harness'])}")
        label_hash = config.get("label_sha256")
        if label_hash not in (None, "") and label_hash != labels[site]["sha256"]:
            flaws.append(f"label hash {_short(label_hash)} differs from the current label file for {_text(site, 60)} "
                         f"{_short(labels[site]['sha256'])} (the label was edited after the run)")
        if check["is_after"] and check["eligible"] is not None and site not in check["eligible"]:
            flaws.append("this site is not after_eligible in benchmark/sites.json, so it may not have an after run")
        for flaw in flaws:
            problems.append(f"{snapshot}/{rel}: {flaw}")
        runs_by_key[key] = {"result": result, "config": config, "flawed": bool(flaws)}

    for rel_path, row, invalid_file in files:
        accept(rel_path, row, invalid_file)

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

    per_task = {}
    for task in TASK_IDS:
        mine = [item for (site, t, _), item in runs_by_key.items() if t == task]
        wins = sum(bool(item["result"]["success"]) for item in mine)
        per_task[task] = {"valid_runs": len(mine), "successes": wins,
                          "success_rate_pct": round(100 * wins / len(mine), 1) if mine else None}

    getters = {
        "answered_model": lambda item: item["result"].get("model"),
        "preregistered": lambda item: item["config"].get("preregistered"),
    }
    provenance = {}
    for name in PROVENANCE_FIELDS + ("answered_model",):
        get = getters.get(name, lambda item, name=name: item["config"].get(name))
        values = sorted({_text(get(item), 80) for item in valid if get(item) not in (None, "")})
        provenance[name] = values
        if name in MUST_BE_UNIFORM and len(values) > 1:
            problems.append(f"{snapshot}: more than one value in {name} among the valid runs ({', '.join(values)})")
    warnings = []
    no_head = sum(1 for item in valid if item["config"].get("git_head") in (None, ""))
    if no_head:
        warnings.append(f"{snapshot}: {no_head} valid run(s) have no git_head recorded, so they cannot be tied to a commit")

    summary = {
        "snapshot": snapshot,
        "labelled_sites": len(labels),
        "sites_with_valid_runs": len({site for site, _, _ in runs_by_key}),
        "valid_runs": len(valid), "successes": successes,
        "success_rate_pct": round(100 * successes / len(valid), 1) if valid else None,
        "invalid_runs": invalid_count,
        "counts": _counts(runs_by_key),
        "per_task": per_task,
        "failures": [{"site": site, "task": task, "repeat": repeat,
                      "reason": _text(item["result"].get("reason") or "failed")}
                     for (site, task, repeat), item in sorted(runs_by_key.items())
                     if not item["result"]["success"]],
        "invalid": [{"site": site, "task": task, "repeat": repeat, **item}
                    for (site, task, repeat), entries in sorted(invalid_by_key.items()) for item in entries],
        "provenance": provenance,
        "warnings": warnings,
        "sites": site_tasks,
        "_valid_by_key": runs_by_key,
        "_invalid_by_key": invalid_by_key,
    }
    return summary, problems


def _paired(before: dict, after: dict) -> dict:
    left, right = before["_valid_by_key"], after["_valid_by_key"]
    pairs, config_mismatches, provenance_excluded = [], [], []
    for key in sorted(left.keys() & right.keys()):
        a, b = left[key], right[key]
        if a["flawed"] or b["flawed"]:
            provenance_excluded.append({"site": key[0], "task": key[1], "repeat": key[2]})
            continue
        changed = [name for name in COMPARE_CONFIG if a["config"].get(name) != b["config"].get(name)]
        if a["result"].get("model") != b["result"].get("model"):
            changed.append("result.model")
        if changed:
            config_mismatches.append({"site": key[0], "task": key[1], "repeat": key[2], "fields": changed})
            continue
        pairs.append((key, bool(a["result"]["success"]), bool(b["result"]["success"])))
    n = len(pairs)
    before_success = sum(a for _, a, _ in pairs)
    after_success = sum(b for _, _, b in pairs)

    def group(index: int, names) -> dict:
        out = {}
        for name in names:
            mine = [p for p in pairs if p[0][index] == name]
            wins_a, wins_b = sum(a for _, a, _ in mine), sum(b for _, _, b in mine)
            out[name] = {"pairs": len(mine), "before_successes": wins_a, "after_successes": wins_b,
                         "transitions": _transitions(mine)}
        return out

    return {
        "valid_paired_runs": n,
        "before_successes": before_success,
        "after_successes": after_success,
        "before_success_rate_pct": round(100 * before_success / n, 1) if n else None,
        "after_success_rate_pct": round(100 * after_success / n, 1) if n else None,
        "change_pp": round(100 * (after_success - before_success) / n, 1) if n else None,
        "transitions": _transitions(pairs),
        "counts": _counts(k for k, _, _ in pairs),
        "per_task": group(1, TASK_IDS),
        "per_site": group(0, sorted({k[0] for k, _, _ in pairs})),
        "config_mismatches_excluded": config_mismatches,
        "excluded_for_provenance": provenance_excluded,
        "unpaired_before_runs": len(left.keys() - right.keys()),
        "unpaired_after_runs": len(right.keys() - left.keys()),
    }


def build_report(results: Path, labels_dir: Path, before_name: str, after_name: str, repeats: int = 3,
                 criteria_path=None, sites_path=None) -> dict:
    """criteria_path: the current benchmark/criteria.json (None skips the criteria and harness hash checks).
    sites_path: benchmark/sites.json (None, or a missing file, skips the after_eligible check)."""
    results, labels_dir = Path(results), Path(labels_dir)
    labels, label_problems = _labels(labels_dir)
    criteria_sha, criteria_harness, criteria_problem = _criteria(criteria_path)
    eligible, registry_problems = _registry(sites_path)
    problems_extra = list(registry_problems)
    check = {"criteria_sha": criteria_sha, "criteria_harness": criteria_harness, "eligible": eligible}
    before, before_problems = _load_snapshot(results, before_name, labels, repeats, {**check, "is_after": False})
    after, after_problems = _load_snapshot(results, after_name, labels, repeats, {**check, "is_after": True})
    if criteria_problem and (before["valid_runs"] or after["valid_runs"]):
        problems_extra.append(criteria_problem)
    missing = [name for name in (before_name, after_name) if not (results / name).is_dir()
               and not (results / name).is_symlink()]
    if len(missing) == 1:                                        # a typo in one name; both missing is "no results yet"
        problems_extra.append(f"no such snapshot: '{_text(missing[0], 64)}' has no directory in the results tree")
    paired = _paired(before, after)
    for snapshot in (before, after):
        snapshot.pop("_valid_by_key", None)
        snapshot.pop("_invalid_by_key", None)
    recorded = (before["valid_runs"] + before["invalid_runs"] + after["valid_runs"] + after["invalid_runs"]) > 0
    units = [before["counts"], after["counts"], paired["counts"]]
    sites_n, cells_n = min(u["sites"] for u in units), min(u["cells"] for u in units)
    return {
        "title": "AgentReady benchmark evaluation",
        "generated_by": GENERATED_BY,
        "repeats_expected": repeats,
        "results_recorded": recorded,
        "small_sample": {"threshold": SMALL_SAMPLE,
                         "independent_units": {"sites": sites_n, "cells": cells_n},
                         "is_small": sites_n < SMALL_SAMPLE or cells_n < SMALL_SAMPLE},
        "before": before,
        "after": after,
        "paired_comparison": paired,
        "audit_check_accuracy": {
            "status": "not measured",
            "reason": "The current benchmark labels and task-run files do not contain hand-labelled audit-check outcomes and audit findings.",
        },
        "warnings": before["warnings"] + after["warnings"],
        "problems": label_problems + problems_extra + before_problems + after_problems,
        "limitations": [
            "Task success describes only the labelled sites and recorded runs.",
            "Invalid model or harness runs are excluded from success-rate denominators and counted separately.",
            "Paired change includes only matching site, task and repeat records with matching runner configuration.",
            "Runs that are not pre-registered, or whose hashes no longer match the committed criteria or labels, are flagged and never paired.",
            "Audit-check accuracy is not calculated until labelled audit outcomes and corresponding audit findings are recorded.",
            MCP_STUB_NOTE,
        ],
    }


def _pct(value) -> str:
    return "—" if value is None else f"{value}%"


def _transition_row(name: str, item: dict) -> str:
    t = item["transitions"]
    return (f"| {name} | {item['pairs']} | {t['fail_to_pass']} | {t['pass_to_fail']} | {t['pass_to_pass']} | "
            f"{t['fail_to_fail']} |")


def to_markdown(report: dict) -> str:
    lines = [MARKDOWN_MARKER, "# AgentReady benchmark evaluation", "",
             f"Expected repeats per site and task: {report['repeats_expected']}.", ""]
    if not report.get("results_recorded", True):
        lines += ["No results recorded yet. Run the benchmark first (see docs/benchmark.md); nothing below is a measurement.", ""]
    for label in ("before", "after"):
        snap = report[label]
        counts = snap["counts"]
        lines += [f"## {label.title()} ({_md(snap['snapshot'])})", "",
                  f"- Task success: {snap['successes']}/{snap['valid_runs']} valid runs ({_pct(snap['success_rate_pct']) if snap['success_rate_pct'] is not None else 'not available'})",
                  f"- Coverage: {counts['sites']} sites, {counts['cells']} site-task cells, {counts['runs']} runs",
                  f"- Sites with results: {snap['sites_with_valid_runs']}/{snap['labelled_sites']}",
                  f"- Invalid runs excluded: {snap['invalid_runs']}", "",
                  "| Task | Valid runs | Successes | Success |", "|---|---:|---:|---:|"]
        for task, item in snap["per_task"].items():
            lines.append(f"| {task} | {item['valid_runs']} | {item['successes']} | {_pct(item['success_rate_pct'])} |")
        lines += ["", "| Site | Task | Success | Repeat spread | Valid/expected | Invalid |",
                  "|---|---|---:|---:|---:|---:|"]
        for site, tasks in snap["sites"].items():
            for task, item in tasks.items():
                pct = _pct(item["success_rate_pct"])
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
                 for name in PROVENANCE_FIELDS + ("answered_model",)]
        lines.append(f"- {label} ({_md(report[label]['snapshot'])}): " + "; ".join(parts))
    lines.append("")
    pair = report["paired_comparison"]
    change = "—" if pair["change_pp"] is None else f"{pair['change_pp']:+.1f} pp"
    t = pair["transitions"]
    pc = pair["counts"]
    lines += ["## Paired comparison", "",
              f"Paired set: {pc['sites']} sites, {pc['cells']} site-task cells, {pc['runs']} runs.",
              f"Same site/task/repeat runs: {pair['valid_paired_runs']}; before {_pct(pair['before_success_rate_pct'])}; "
              f"after {_pct(pair['after_success_rate_pct'])}; change {change}. "
              f"This pooled figure mixes three tasks of different difficulty: read the per-task and per-site tables.",
              f"Per pair: fail to pass {t['fail_to_pass']}, pass to fail {t['pass_to_fail']}, "
              f"pass to pass {t['pass_to_pass']}, fail to fail {t['fail_to_fail']}.",
              f"Excluded for a different configuration: {len(pair['config_mismatches_excluded'])}; "
              f"excluded because provenance did not check out: {len(pair['excluded_for_provenance'])}; "
              f"before-only runs: {pair['unpaired_before_runs']}; after-only runs: {pair['unpaired_after_runs']}.", "",
              "### Per task", "",
              "| Task | Pairs | Fail to pass | Pass to fail | Pass to pass | Fail to fail |",
              "|---|---:|---:|---:|---:|---:|"]
    lines.extend(_transition_row(name, item) for name, item in pair["per_task"].items())
    lines += ["", "### Per site", "",
              "| Site | Pairs | Fail to pass | Pass to fail | Pass to pass | Fail to fail |",
              "|---|---:|---:|---:|---:|---:|"]
    lines.extend(_transition_row(name, item) for name, item in pair["per_site"].items())
    lines.append("")
    small = report.get("small_sample", {})
    units = small.get("independent_units", {"sites": 0, "cells": 0})
    lines += ["## Sample size", "",
              f"Valid runs: before {report['before']['valid_runs']}, after {report['after']['valid_runs']}, "
              f"paired {pair['valid_paired_runs']}. Independent units behind the smallest of these: "
              f"{units['sites']} sites and {units['cells']} site-task cells.",
              "Repeats of one site-task cell are not independent samples, and the three tasks differ in difficulty."]
    if small.get("is_small", True):
        lines.append(f"Small sample: treat differences as indicative only (fewer than {small.get('threshold', SMALL_SAMPLE)} "
                     f"independent sites or site-task cells).")
    lines += ["No statistical test is computed, and nothing here applies beyond the sites tested.", ""]
    lines += ["## Audit check accuracy", "", report["audit_check_accuracy"]["status"],
              "", report["audit_check_accuracy"]["reason"], "", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    if report.get("warnings"):
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {_md(item)}" for item in report["warnings"])
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
    parser.add_argument("--criteria", type=Path, default=REPO_ROOT / "benchmark" / "criteria.json")
    parser.add_argument("--sites", type=Path, default=REPO_ROOT / "benchmark" / "sites.json")
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
    report = build_report(args.results, args.labels, args.before, args.after, args.repeats,
                          criteria_path=args.criteria, sites_path=args.sites)
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
