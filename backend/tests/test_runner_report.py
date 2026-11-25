"""Tests for app.runner.report (merged in PR #6 without tests). Fixtures are written through the real
results.save_run so the real file format is exercised."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.runner import report as rep
from app.runner.agent import RunResult
from app.runner.labels import AUDIT_CHECK_IDS
from app.runner.results import config_dict, harness_sha256, move_aside, save_run
from app.runner.tasks import TASK_IDS

HOSTS = ("a.test", "b.test", "c.test")


def write_label(labels: Path, host: str) -> Path:
    labels.mkdir(parents=True, exist_ok=True)
    path = labels / f"{host}.json"
    path.write_text(json.dumps({
        "site": f"https://{host}",
        "tasks": {
            "main_info": {"urls": [f"https://{host}/p"]},
            "contact_form": {"urls": [f"https://{host}/c"], "required_fields": ["email"]},
            "docs_api": {"none_exists": True},
        }}))
    return path


_CTX: dict = {}        # set by the env fixture: the real hashes of the tmp labels and criteria file


def cfg(snapshot="before", host=None, **over) -> dict:
    """A config like the runner's, with the REAL hashes of this test's label and criteria files."""
    base = config_dict(model="m", snapshot=snapshot, criteria_sha256=_CTX.get("criteria_sha", "c" * 64),
                       label_sha256=_CTX.get("labels", {}).get(host, "l" * 64), git_head="h" * 40,
                       preregistered=True, rate_limit_seconds=0.5, request_budget=60)
    base.update(over)
    return base


def run_result(task, success, valid=True, reason=None, error=""):
    return RunResult(task, success, reason or ("ok" if success else "wrong_page"), valid, None, 2, 3, error=error)


def put(results, snapshot, host, task, repeat, success, *, valid=True, config=None, invalid_file=False,
        reason=None, error=""):
    config = cfg(snapshot, host) if config is None else config
    path = save_run(results, snapshot, host, repeat, run_result(task, success, valid, reason, error), config)
    if invalid_file:
        move_aside(path)
    return path


def fill(results, snapshot, hosts=HOSTS, successes=None, repeats=3, config=None):
    """All tasks, all repeats; `successes(host, task, repeat) -> bool`."""
    for host in hosts:
        for task in TASK_IDS:
            for repeat in range(1, repeats + 1):
                ok = True if successes is None else successes(host, task, repeat)
                put(results, snapshot, host, task, repeat, ok, config=config)


def write_criteria(path: Path) -> str:
    path.write_text(json.dumps({"version": 2, "harness_sha256": harness_sha256()}))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path):
    labels, results = tmp_path / "labels", tmp_path / "results"
    results.mkdir()
    for h in HOSTS:
        write_label(labels, h)
    _CTX.clear()
    _CTX["criteria_sha"] = write_criteria(tmp_path / "criteria.json")
    _CTX["labels"] = {h: hashlib.sha256((labels / f"{h}.json").read_bytes()).hexdigest() for h in HOSTS}
    return labels, results


def criteria_of(env) -> Path:
    return env[0].parent / "criteria.json"


def extra(env, *more):
    """The CLI flags every main() call in these tests needs: the tmp criteria file and no registry."""
    return ["--criteria", str(criteria_of(env)), "--sites", str(env[0].parent / "no-registry.json"), *more]


def build(env, before="before", after="after", repeats=3):
    labels, results = env
    return rep.build_report(results, labels, before, after, repeats, criteria_path=criteria_of(env))


def snapshot_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# 1 -------------------------------------------------------------------------------------------------------
def test_success_rate_uses_only_valid_runs_and_invalid_runs_are_listed_and_excluded(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True)
    put(results, "before", "a.test", "main_info", 2, False)
    put(results, "before", "a.test", "main_info", 3, True, valid=False, reason="llm_error", error="RuntimeError: boom")
    put(results, "before", "a.test", "contact_form", 1, True, invalid_file=True, reason="harness_error")
    snap = build(env)["before"]
    item = snap["sites"]["a.test"]["main_info"]
    assert (item["valid_runs"], item["successes"], item["success_rate_pct"]) == (2, 1, 50.0)
    assert item["invalid_runs"] == 1 and item["missing_repeats"] == [3]
    assert snap["valid_runs"] == 2 and snap["successes"] == 1 and snap["success_rate_pct"] == 50.0
    assert snap["invalid_runs"] == 2
    assert {(x["task"], x["reason"]) for x in snap["invalid"]} == {("main_info", "llm_error"),
                                                                    ("contact_form", "harness_error")}


# 2 -------------------------------------------------------------------------------------------------------
def test_a_task_without_valid_runs_shows_a_dash_never_zero_percent(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False, valid=False, reason="llm_error")
    r = build(env)
    item = r["before"]["sites"]["a.test"]["main_info"]
    assert item["success_rate_pct"] is None and item["valid_runs"] == 0
    md = rep.to_markdown(r)
    row = next(l for l in md.splitlines() if l.startswith("| a.test | main_info |"))
    assert "—" in row and "0%" not in row
    assert r["before"]["success_rate_pct"] is None


# 3 -------------------------------------------------------------------------------------------------------
def test_missing_repeats_are_incomplete_not_failures(env):
    _, results = env
    put(results, "before", "a.test", "docs_api", 1, True)
    item = build(env)["before"]["sites"]["a.test"]["docs_api"]
    assert item["valid_runs"] == 1 and item["expected_runs"] == 3 and item["missing_repeats"] == [2, 3]
    assert item["success_rate_pct"] == 100.0 and item["failures"] == []
    row = next(l for l in rep.to_markdown(build(env)).splitlines() if l.startswith("| a.test | docs_api |"))
    assert "1/3" in row


# 4 -------------------------------------------------------------------------------------------------------
def test_paired_comparison_pairs_only_identical_keys_and_counts_transitions(env):
    _, results = env
    fill(results, "before", hosts=("a.test", "b.test"), successes=lambda h, t, r: r != 1)
    fill(results, "after", hosts=("a.test",), successes=lambda h, t, r: r != 2)
    p = build(env)["paired_comparison"]
    assert p["valid_paired_runs"] == 9                             # only a.test, 3 tasks x 3 repeats
    assert p["before_successes"] == 6 and p["after_successes"] == 6 and p["change_pp"] == 0.0
    assert p["transitions"] == {"fail_to_pass": 3, "pass_to_fail": 3, "pass_to_pass": 3, "fail_to_fail": 0}
    assert p["unpaired_before_runs"] == 9 and p["unpaired_after_runs"] == 0       # b.test is before-only


@pytest.mark.parametrize("field,value", [
    ("model", "other"), ("step_limit", 5), ("max_turns", 3), ("request_budget", 10), ("rate_limit_seconds", 1.0),
    ("toolset_version", "2"),
])
def test_runs_with_a_different_configuration_are_never_paired(env, field, value):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False)
    put(results, "after", "a.test", "main_info", 1, True, config=cfg("after", "a.test", **{field: value}))
    p = build(env)["paired_comparison"]
    assert p["valid_paired_runs"] == 0 and p["change_pp"] is None
    assert p["config_mismatches_excluded"] == [{"site": "a.test", "task": "main_info", "repeat": 1, "fields": [field]}]
    assert p["unpaired_before_runs"] == 0 and p["unpaired_after_runs"] == 0


@pytest.mark.parametrize("field,value", [
    ("criteria_sha256", "x" * 64), ("harness_sha256", "y" * 64), ("label_sha256", "z" * 64), ("preregistered", False),
])
def test_runs_whose_provenance_does_not_check_out_are_flagged_and_never_paired(env, field, value):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False)
    put(results, "after", "a.test", "main_info", 1, True, config=cfg("after", "a.test", **{field: value}))
    r = build(env)
    p = r["paired_comparison"]
    assert p["valid_paired_runs"] == 0 and p["change_pp"] is None
    assert p["excluded_for_provenance"] == [{"site": "a.test", "task": "main_info", "repeat": 1}]
    assert any("after/a.test/main_info#1" in x for x in r["problems"])
    assert rep.main(["--before", "before", "--after", "after", "--results", str(env[1]), "--labels", str(env[0]),
                     *extra(env)]) == 1


def test_a_changed_git_head_alone_does_not_block_pairing(env):
    """The commit moves when a new label is committed between runs; the harness hash is what must match."""
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False)
    put(results, "after", "a.test", "main_info", 1, True, config=cfg("after", "a.test", git_head="9" * 40))
    assert build(env)["paired_comparison"]["valid_paired_runs"] == 1


# 5 -------------------------------------------------------------------------------------------------------
def test_repeat_spread_is_max_minus_min_and_dash_for_one_valid_run(env):
    _, results = env
    for repeat, ok in ((1, True), (2, True), (3, False)):
        put(results, "before", "a.test", "main_info", repeat, ok)
    put(results, "before", "a.test", "docs_api", 1, True)
    sites = build(env)["before"]["sites"]["a.test"]
    assert sites["main_info"]["repeat_spread_pp"] == 100.0 and sites["docs_api"]["repeat_spread_pp"] is None
    md = rep.to_markdown(build(env))
    assert "| a.test | main_info | 66.7% | 100 pp | 3/3 | 0 |" in md
    assert "| a.test | docs_api | 100.0% | — | 1/3 | 0 |" in md


# 6 -------------------------------------------------------------------------------------------------------
def problems_for(env, mutate):
    labels, results = env
    mutate(labels, results)
    r = rep.build_report(results, labels, "before", "after", 3, criteria_path=criteria_of(env))
    return r["problems"], rep.main(["--before", "before", "--after", "after", "--results", str(results),
                                    "--labels", str(labels), *extra(env)])


def test_a_result_for_a_site_with_no_label_is_a_problem_and_exit_1(env):
    problems, code = problems_for(env, lambda l, r: put(r, "before", "zzz.test", "main_info", 1, True))
    assert any("no committed site label" in p for p in problems) and code == 1


def test_a_malformed_result_file_is_a_problem_and_exit_1(env):
    def mutate(l, r):
        put(r, "before", "a.test", "main_info", 1, True)
        (r / "before" / "a.test" / "docs_api-1.json").write_text("{nope")
    problems, code = problems_for(env, mutate)
    assert any("not readable" in p for p in problems) and code == 1


def test_a_stray_copy_of_a_run_file_beside_it_is_a_problem_not_a_second_result(env):
    def mutate(l, r):
        path = put(r, "before", "a.test", "main_info", 1, True)
        dup = path.with_name("main_info-1.copy.json")
        dup.write_text(path.read_text())
    problems, code = problems_for(env, mutate)
    assert any("unexpected result file" in p for p in problems) and code == 1


@pytest.mark.parametrize("repeat", [0, 4, -1, 99])
def test_a_repeat_outside_the_expected_range_is_a_problem(env, repeat):
    def mutate(l, r):
        path = put(r, "before", "a.test", "main_info", 1, True)
        data = json.loads(path.read_text())
        data["repeat"] = repeat
        path.write_text(json.dumps(data))
    problems, code = problems_for(env, mutate)
    assert any("invalid task or repeat number" in p for p in problems) and code == 1


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_a_success_that_is_not_a_boolean_is_a_problem(env, bad):
    def mutate(l, r):
        path = put(r, "before", "a.test", "main_info", 1, True)
        data = json.loads(path.read_text())
        data["result"]["success"] = bad
        path.write_text(json.dumps(data))
    problems, code = problems_for(env, mutate)
    assert any("success is not a boolean" in p for p in problems) and code == 1


def test_a_label_that_fails_to_load_is_a_problem(env):
    problems, code = problems_for(env, lambda l, r: (l / "bad.test.json").write_text("{}"))
    assert any(p.startswith("label bad.test.json") for p in problems) and code == 1


def test_a_run_file_copied_into_another_snapshot_is_flagged(env):
    """A before run copied into the after directory would otherwise be pooled silently."""
    def mutate(l, r):
        put(r, "after", "a.test", "main_info", 1, True, config=cfg("before"))     # config says it was a before run
    problems, code = problems_for(env, mutate)
    assert any("recorded for snapshot 'before'" in p for p in problems) and code == 1


def test_a_valid_looking_run_whose_task_differs_from_its_file_is_flagged(env):
    def mutate(l, r):
        path = put(r, "before", "a.test", "main_info", 1, True)
        data = json.loads(path.read_text())
        data["result"]["task_id"] = "docs_api"
        path.write_text(json.dumps(data))
    problems, code = problems_for(env, mutate)
    assert any("does not match" in p for p in problems) and code == 1


# 7 -------------------------------------------------------------------------------------------------------
def test_the_command_never_creates_changes_or_deletes_files_under_results(env, tmp_path):
    labels, results = env
    put(results, "before", "a.test", "main_info", 1, True, valid=False, invalid_file=True, reason="llm_error")
    fill(results, "before")                      # the invalid run was moved aside, so repeat 1 is free again
    fill(results, "after")
    before = snapshot_tree(results)
    (tmp_path / "out").mkdir()
    out = tmp_path / "out" / "evaluation.json"
    code = rep.main(["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels),
                     *extra(env), "--format", "json", "--output", str(out)])
    assert code == 0 and snapshot_tree(results) == before and out.exists()
    rep.main(["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels),
              *extra(env)])
    assert snapshot_tree(results) == before


def args(env, *more):
    labels, results = env
    return ["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels),
            *extra(env), *more]


def test_output_may_not_replace_a_run_file(env):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, True)
    original = path.read_bytes()
    with pytest.raises(SystemExit):
        rep.main(args(env, "--output", str(path)))
    assert path.read_bytes() == original


def test_output_may_not_be_written_anywhere_inside_the_results_tree(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    for target in (results / "evaluation.json", results / "before" / "evaluation.json",
                   results / "new" / "dir" / "report.md"):
        with pytest.raises(SystemExit):
            rep.main(args(env, "--output", str(target)))
        assert not target.exists()
    assert not (results / "new").exists()


def test_output_may_not_be_a_symlink(env, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    link = tmp_path / "link.md"
    link.symlink_to(victim)
    with pytest.raises(SystemExit):
        rep.main(args(env, "--output", str(link)))
    assert victim.read_text() == "keep me"


# 8 -------------------------------------------------------------------------------------------------------
def test_before_and_after_must_be_different_snapshots_even_by_case(env):
    for before, after in (("same", "same"), ("Before", "before"), ("AFTER", "after")):
        with pytest.raises(SystemExit):
            rep.main(["--before", before, "--after", after, "--results", str(env[1]), "--labels", str(env[0]),
                      *extra(env)])


@pytest.mark.parametrize("name", ["", "a b", "../x", "x/y", "x" * 65, "ünï", "a.b"])
def test_snapshot_names_must_match_the_server_pattern(env, name):
    with pytest.raises(SystemExit):
        rep.main(["--before", name, "--after", "after", "--results", str(env[1]), "--labels", str(env[0]),
                  *extra(env)])


def test_repeats_must_be_between_one_and_ten(env):
    for n in ("0", "11", "-1"):
        with pytest.raises(SystemExit):
            rep.main(args(env, "--repeats", n))


# 9 -------------------------------------------------------------------------------------------------------
HOSTILE = ["a|b\n## Injected heading\n- [x](https://evil.test)", "`code` | | |\r\n| fake | row |", "x" * 5000,
           "<script>alert(1)</script>", "‮⁦bidi"]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_in_results_never_adds_rows_headings_or_unbounded_lines(env, text):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False, reason=text)
    put(results, "before", "a.test", "contact_form", 1, True, valid=False, reason=text, error=text)
    md = rep.to_markdown(build(env))
    headings = [l for l in md.splitlines() if l.startswith("#")]
    allowed = {"# AgentReady benchmark evaluation", "## Before (before)", "## After (after)", "## Provenance",
               "## Paired comparison", "### Per task", "### Per site", "### Before (before)", "### After (after)",
               "## Sample size", "## Audit check accuracy",
               "## Limitations", "## Warnings", "## Data issues"}
    assert set(headings) <= allowed and "## Injected heading" not in md
    rows = [l for l in md.splitlines() if l.startswith("|")]
    allowed_rows = ("| Site", "| Task", "| Check", "|---", "| a.test", "| b.test", "| c.test", "| main_info",
                    "| contact_form", "| docs_api", *(f"| {check}" for check in AUDIT_CHECK_IDS))
    assert all(l.startswith(allowed_rows) for l in rows)
    assert not any(l.startswith("| fake") for l in md.splitlines())
    assert max(len(l) for l in md.splitlines()) <= 700
    assert "](https://evil.test)" not in md and "<script>" not in md
    assert "‮" not in md and "⁦" not in md


# 10 ------------------------------------------------------------------------------------------------------
def test_same_inputs_give_byte_identical_markdown_and_json(env, tmp_path):
    labels, results = env
    fill(results, "before", successes=lambda h, t, r: (h, t, r) != ("a.test", "docs_api", 2))
    fill(results, "after")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for out, fmt in ((a, "markdown"), (b, "markdown")):
        rep.main(args(env, "--format", fmt, "--output", str(out / "r.md")))
    assert (a / "r.md").read_bytes() == (b / "r.md").read_bytes()
    for out in (a, b):
        rep.main(args(env, "--format", "json", "--output", str(out / "r.json")))
    assert (a / "r.json").read_bytes() == (b / "r.json").read_bytes()
    keys = list(json.loads((a / "r.json").read_text()))
    assert keys == sorted(keys) and "paired_comparison" in keys and keys[0] == "after"


# 11 ------------------------------------------------------------------------------------------------------
def test_provenance_lists_the_distinct_hashes_models_and_commits_of_each_snapshot(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    r = build(env)
    prov = r["before"]["provenance"]
    assert prov["criteria_sha256"] == [_CTX["criteria_sha"]] and prov["model"] == ["m"] and prov["git_head"] == ["h" * 40]
    assert len(prov["harness_sha256"]) == 1
    md = rep.to_markdown(r)
    assert "## Provenance" in md and _CTX["criteria_sha"] in md and "model: m" in md


@pytest.mark.parametrize("field", ["criteria_sha256", "harness_sha256", "model"])
def test_mixed_criteria_harness_or_model_inside_one_snapshot_is_a_data_issue(env, field):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True)
    put(results, "before", "a.test", "main_info", 2, True, config=cfg("before", "a.test", **{field: "other-" + "9" * 5}))
    r = build(env)
    assert any(field in p and "more than one value" in p for p in r["problems"])


def test_a_small_sample_is_labelled_as_indicative_and_no_significance_is_claimed(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    fill(results, "after", hosts=("a.test",))
    md = rep.to_markdown(build(env))
    assert "Small sample: treat differences as indicative only" in md
    assert "significant" not in md.lower() and "p-value" not in md.lower() and "confidence interval" not in md.lower()


def test_the_mcp_stub_is_stated_as_not_counted_even_when_no_stub_exists(env):
    r = build(env)
    assert any("MCP stub is not counted in task success (FR-6.4)" in x for x in r["limitations"])
    assert "MCP stub is not counted in task success (FR-6.4)" in rep.to_markdown(r)


# 12 ------------------------------------------------------------------------------------------------------
def test_an_empty_results_tree_gives_an_empty_report_and_exit_0(tmp_path, capsys):
    labels = tmp_path / "labels"
    write_label(labels, "a.test")
    code = rep.main(["--before", "before", "--after", "after", "--results", str(tmp_path / "missing"),
                     "--labels", str(labels)])
    out = capsys.readouterr().out
    assert code == 0 and "No results recorded yet" in out
    r = rep.build_report(tmp_path / "missing", labels, "before", "after", 3)
    assert r["before"]["valid_runs"] == 0 and r["before"]["success_rate_pct"] is None
    assert r["paired_comparison"]["valid_paired_runs"] == 0 and r["problems"] == []


def test_no_labels_and_no_results_is_still_not_a_traceback(tmp_path, capsys):
    code = rep.main(["--before", "before", "--after", "after", "--results", str(tmp_path / "r"),
                     "--labels", str(tmp_path / "l")])
    assert code == 0 and "No results recorded yet" in capsys.readouterr().out


def test_audit_accuracy_is_not_measured_without_labels_or_saved_audits():
    r = rep.build_report(Path("/nonexistent-results"), Path("/nonexistent-labels"), "before", "after")
    assert r["audit_check_accuracy"]["before"]["status"] == "not measured"
    assert r["audit_check_accuracy"]["after"]["status"] == "not measured"


# ===== Group A: the report must not overstate (plan 6 review: C1, C2, I2, I3 and related) =====================
def many_hosts(env, n):
    labels, _ = env
    hosts = [f"s{i}.test" for i in range(n)]
    for h in hosts:
        write_label(labels, h)
        _CTX["labels"][h] = hashlib.sha256((labels / f"{h}.json").read_bytes()).hexdigest()
    return hosts


def test_c1_the_planned_design_size_always_carries_the_small_sample_caveat_and_unit_counts(env):
    """5 sites x 3 tasks x 3 repeats is 45 runs: more than 30 runs but only 5 independent sites and 15 cells."""
    _, results = env
    hosts = many_hosts(env, 5)
    fill(results, "before", hosts=hosts, successes=lambda h, t, r: False)
    fill(results, "after", hosts=hosts, successes=lambda h, t, r: True)
    r = build(env)
    assert r["small_sample"]["is_small"] is True
    assert r["small_sample"]["independent_units"] == {"sites": 5, "cells": 15}
    md = rep.to_markdown(r)
    assert "Small sample: treat differences as indicative only" in md
    assert "5 sites, 15 site-task cells, 45 runs" in md
    assert "not independent samples" in md and "differ in difficulty" in md


def test_c1_the_caveat_goes_away_only_with_thirty_independent_sites_and_cells(env):
    _, results = env
    hosts = many_hosts(env, 30)
    fill(results, "before", hosts=hosts, repeats=1, successes=lambda h, t, r: False)
    fill(results, "after", hosts=hosts, repeats=1, successes=lambda h, t, r: True)
    r = build(env)
    assert r["small_sample"]["independent_units"] == {"sites": 30, "cells": 90}
    assert r["small_sample"]["is_small"] is False
    assert "Small sample" not in rep.to_markdown(r)
    # 29 sites, however many repeats, is still small
    put(results, "before", "s0.test", "main_info", 2, True)
    hosts29 = hosts[:29]
    r29 = rep.build_report(results, env[0], "before", "after", 3, criteria_path=criteria_of(env))
    assert r29["small_sample"]["is_small"] is False        # 30 labelled sites still have results
    for h in hosts[29:]:
        for task in TASK_IDS:
            (results / "after" / h / f"{task}-1.json").unlink()
    r29 = build(env)
    assert r29["paired_comparison"]["counts"]["sites"] == 29 and r29["small_sample"]["is_small"] is True


def test_c1_a_pooled_headline_is_never_shown_without_per_task_and_per_site_transitions(env):
    _, results = env
    fill(results, "before", hosts=("a.test", "b.test"),
         successes=lambda h, t, r: not (t == "main_info" and h == "a.test"))
    fill(results, "after", hosts=("a.test", "b.test"),
         successes=lambda h, t, r: not (t == "docs_api" and h == "b.test"))
    r = build(env)
    p = r["paired_comparison"]
    assert p["per_task"]["main_info"]["transitions"] == {"fail_to_pass": 3, "pass_to_fail": 0, "pass_to_pass": 3,
                                                          "fail_to_fail": 0}
    assert p["per_task"]["docs_api"]["transitions"] == {"fail_to_pass": 0, "pass_to_fail": 3, "pass_to_pass": 3,
                                                         "fail_to_fail": 0}
    assert p["per_site"]["a.test"]["transitions"]["fail_to_pass"] == 3
    assert p["per_site"]["b.test"]["transitions"]["pass_to_fail"] == 3
    assert r["before"]["per_task"]["main_info"] == {"valid_runs": 6, "successes": 3, "success_rate_pct": 50.0}
    md = rep.to_markdown(r)
    assert "### Per task" in md and "### Per site" in md and "| main_info |" in md and "| a.test |" in md


def test_c1_a_paired_set_with_fail_to_pass_and_pass_to_fail_in_different_counts_is_not_symmetric(env):
    """Kills a mutant that swaps the two transition labels."""
    _, results = env
    fill(results, "before", hosts=("a.test",), successes=lambda h, t, r: t == "docs_api")
    fill(results, "after", hosts=("a.test",), successes=lambda h, t, r: True)
    t = build(env)["paired_comparison"]["transitions"]
    assert t == {"fail_to_pass": 6, "pass_to_fail": 0, "pass_to_pass": 3, "fail_to_fail": 0}


# C2 -----------------------------------------------------------------------------------------------------------
def test_c2_a_run_that_was_not_preregistered_is_a_data_issue_exit_1_and_named(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True, config=cfg("before", "a.test", preregistered=False))
    r = build(env)
    assert any("before/a.test/main_info#1" in p and "not pre-registered" in p for p in r["problems"])
    assert r["before"]["provenance"]["preregistered"] == ["False"]
    assert rep.main(["--before", "before", "--after", "after", "--results", str(env[1]), "--labels", str(env[0]),
                     *extra(env)]) == 1


@pytest.mark.parametrize("field", ["criteria_sha256", "harness_sha256", "label_sha256", "model", "step_limit",
                                   "max_turns", "toolset_version", "snapshot"])
def test_c2_a_valid_run_missing_a_required_config_field_is_flagged_and_never_paired(env, field):
    """Absent must never equal absent."""
    _, results = env
    for snap in ("before", "after"):
        config = cfg(snap, "a.test")
        del config[field]
        put(results, snap, "a.test", "main_info", 1, True, config=config)
    r = build(env)
    assert r["paired_comparison"]["valid_paired_runs"] == 0
    assert any(f"missing config field" in p and field in p for p in r["problems"])


def test_c2_a_label_edited_after_the_runs_is_flagged(env):
    labels, results = env
    fill(results, "before", hosts=("a.test",))
    label = labels / "a.test.json"
    data = json.loads(label.read_text())
    data["tasks"]["main_info"]["urls"].append("https://a.test/changed")
    label.write_text(json.dumps(data))
    r = build(env)
    assert any("label hash" in p and "a.test" in p and "differs from the current" in p for p in r["problems"])
    assert r["paired_comparison"]["valid_paired_runs"] == 0


def test_c2_criteria_edited_after_the_runs_is_flagged_with_hash_prefixes(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    criteria_of(env).write_text(json.dumps({"version": 2, "harness_sha256": harness_sha256(), "note": "edited"}))
    r = build(env)
    flagged = [p for p in r["problems"] if "criteria hash" in p and "differs from the current" in p]
    assert flagged and _CTX["criteria_sha"][:8] in flagged[0]


def test_c2_a_run_whose_harness_differs_from_the_criteria_file_is_flagged(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True, config=cfg("before", "a.test", harness_sha256="e" * 64))
    r = build(env)
    assert any("harness hash" in p and "criteria" in p for p in r["problems"])


def test_c2_an_empty_git_head_is_a_warning_in_provenance_not_a_failure(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True, config=cfg("before", "a.test", git_head=""))
    (results / "after").mkdir()
    r = build(env)
    assert r["problems"] == [] and any("git_head" in w for w in r["warnings"])
    assert "## Warnings" in rep.to_markdown(r)
    assert rep.main(["--before", "before", "--after", "after", "--results", str(env[1]), "--labels", str(env[0]),
                     *extra(env)]) == 0


def test_c2_no_criteria_file_means_the_hash_check_cannot_run_and_says_so(env, tmp_path):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    r = rep.build_report(results, env[0], "before", "after", 3, criteria_path=tmp_path / "nope.json")
    assert any("criteria" in p and "could not be read" in p for p in r["problems"])


# I2 -----------------------------------------------------------------------------------------------------------
def answered(results, snapshot, model, success=True, host="a.test"):
    path = put(results, snapshot, host, "main_info", 1, success)
    data = json.loads(path.read_text())
    data["result"]["model"] = model
    path.write_text(json.dumps(data))


def test_i2_the_model_that_answered_is_in_provenance_and_in_the_pairing_rule(env):
    _, results = env
    answered(results, "before", "claude-A", False)
    answered(results, "after", "claude-B", True)
    r = build(env)
    assert r["before"]["provenance"]["answered_model"] == ["claude-A"]
    assert r["after"]["provenance"]["answered_model"] == ["claude-B"]
    p = r["paired_comparison"]
    assert p["valid_paired_runs"] == 0
    assert p["config_mismatches_excluded"] == [{"site": "a.test", "task": "main_info", "repeat": 1,
                                                "fields": ["result.model"]}]
    assert "answered_model: claude-A" in rep.to_markdown(r)


def test_i2_two_answering_models_inside_one_snapshot_is_a_data_issue(env):
    _, results = env
    answered(results, "before", "claude-A")
    path = put(results, "before", "a.test", "main_info", 2, True)
    data = json.loads(path.read_text())
    data["result"]["model"] = "claude-B"
    path.write_text(json.dumps(data))
    assert any("answered_model" in p and "more than one value" in p for p in build(env)["problems"])


# I3 -----------------------------------------------------------------------------------------------------------
def test_i3_a_run_file_moved_to_another_site_directory_is_not_counted(env):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, True)
    target = results / "before" / "b.test" / "docs_api-3.json"
    target.parent.mkdir(parents=True)
    path.rename(target)
    r = build(env)
    assert r["before"]["valid_runs"] == 0
    assert any("does not match its recorded site, task and repeat" in p for p in r["problems"])


@pytest.mark.parametrize("field,value", [("site", "b.test"), ("repeat", 2), ("task", "docs_api")])
def test_i3_editing_the_site_task_or_repeat_inside_a_file_is_not_silently_accepted(env, field, value):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, True)
    data = json.loads(path.read_text())
    data[field] = value
    if field == "task":
        data["result"]["task_id"] = value
    path.write_text(json.dumps(data))
    r = build(env)
    assert r["before"]["valid_runs"] == 0
    assert any("does not match its recorded site, task and repeat" in p for p in r["problems"])


# minors ----------------------------------------------------------------------------------------------------------
def test_deeply_nested_json_is_a_problem_not_a_traceback(env):
    labels, results = env
    put(results, "before", "a.test", "main_info", 1, True)
    (results / "before" / "a.test" / "docs_api-1.json").write_text("[" * 200000)
    (labels / "deep.test.json").write_text("[" * 200000)
    r = build(env)
    assert any("not readable" in p for p in r["problems"]) and any(p.startswith("label deep.test.json") for p in r["problems"])


def test_symlinks_inside_the_results_tree_are_ignored_and_reported(env, tmp_path):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, True)
    link = results / "before" / "a.test" / "main_info-2.json"
    link.symlink_to(path)
    r = build(env)
    assert r["before"]["valid_runs"] == 1
    assert any("symlink" in p and "main_info-2.json" in p for p in r["problems"])
    other = tmp_path / "elsewhere"
    other.mkdir()
    (results / "before" / "linked-dir").symlink_to(other, target_is_directory=True)
    assert any("symlink" in p and "linked-dir" in p for p in build(env)["problems"])


@pytest.mark.parametrize("value", [None, "true", 1, "missing"])
def test_a_result_whose_valid_flag_is_missing_or_not_a_boolean_is_a_data_issue(env, value):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, True)
    data = json.loads(path.read_text())
    if value == "missing":
        del data["result"]["valid"]
    else:
        data["result"]["valid"] = value
    path.write_text(json.dumps(data))
    r = build(env)
    assert r["before"]["valid_runs"] == 0 and r["before"]["invalid_runs"] == 0
    assert any("'valid' is missing or not true/false" in p for p in r["problems"])


def test_a_snapshot_name_with_no_directory_is_a_data_issue_unless_both_are_missing(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    r = rep.build_report(results, env[0], "before", "aftre", 3, criteria_path=criteria_of(env))
    assert any("no such snapshot" in p and "aftre" in p for p in r["problems"])
    both = rep.build_report(results, env[0], "nope1", "nope2", 3, criteria_path=criteria_of(env))
    assert both["problems"] == [] and both["results_recorded"] is False


def test_bare_urls_and_mentions_in_result_text_cannot_autolink_or_notify(env):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False, reason="see https://evil.test/x and www.evil.test @octocat")
    md = rep.to_markdown(build(env))
    line = next(l for l in md.splitlines() if "see" in l and "repeat 1" in l)
    assert "`https://evil.test/x`" in line and "`www.evil.test`" in line and "`@octocat`" in line
    stripped = line.replace("`https://evil.test/x`", "").replace("`www.evil.test`", "").replace("`@octocat`", "")
    assert "https://" not in stripped and "www." not in stripped and "@" not in stripped


def test_an_after_result_for_a_site_that_is_not_after_eligible_is_excluded_and_flagged(env, tmp_path):
    labels, results = env
    reg = tmp_path / "sites.json"
    reg.write_text(json.dumps({"version": 1, "sites": [
        {"host": "a.test", "owner_type": "team", "verification": "meta", "label": "a.test.json",
         "after_eligible": True, "after_method": "staging"},
        {"host": "b.test", "owner_type": "team", "verification": "meta", "label": "b.test.json"}]}))
    fill(results, "before", hosts=("a.test", "b.test"))
    fill(results, "after", hosts=("a.test", "b.test"))
    r = rep.build_report(results, labels, "before", "after", 3, criteria_path=criteria_of(env), sites_path=reg)
    assert r["paired_comparison"]["valid_paired_runs"] == 9              # a.test only
    assert any("b.test" in p and "after_eligible" in p for p in r["problems"])


def test_a_broken_registry_is_reported_not_raised(env, tmp_path):
    _, results = env
    reg = tmp_path / "sites.json"
    reg.write_text("[" * 200000)
    r = rep.build_report(results, env[0], "before", "after", 3, criteria_path=criteria_of(env), sites_path=reg)
    assert any("site registry" in p for p in r["problems"])


# Group B: --output safety (review finding I1) ------------------------------------------------------------
import subprocess

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
MARK = "<!-- agentready-report -->"


def git(repo: Path, *a):
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false",
                           "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *a],
                          check=True, capture_output=True, text=True, env=GIT_ENV)


def ran_ok(env, out, *more):
    return rep.main(args(env, "--output", str(out), *more))


def refused(env, out, *more):
    with pytest.raises(SystemExit):
        rep.main(args(env, "--output", str(out), *more))


def test_output_parent_directory_must_exist_and_is_never_created(env, tmp_path):
    refused(env, tmp_path / "missing" / "deeper" / "r.md")
    assert not (tmp_path / "missing").exists()


def test_output_may_not_be_a_directory(env, tmp_path):
    (tmp_path / "d.md").mkdir()
    refused(env, tmp_path / "d.md")


@pytest.mark.parametrize("name", ["notes.txt", "report", "x.py", "r.md.exe", "r.markdown", ".md"])
def test_output_must_be_a_markdown_or_json_file_name(env, tmp_path, name):
    refused(env, tmp_path / name)
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("name", ["README.md", "criteria.json", "sites.json", "pyproject.toml", "readme.md",
                                  "SITES.JSON"])
def test_output_may_not_replace_the_projects_own_files_even_by_name(env, tmp_path, name):
    target = tmp_path / "x" / name
    target.parent.mkdir()
    target.write_text("keep me")
    refused(env, target, "--force-overwrite")
    assert target.read_text() == "keep me"


def test_output_may_not_replace_the_criteria_file_the_report_was_given(env):
    before = criteria_of(env).read_bytes()
    refused(env, criteria_of(env), "--force-overwrite")
    assert criteria_of(env).read_bytes() == before


def test_output_may_not_be_inside_git_or_the_default_results_tree(env, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "benchmark" / "results").mkdir(parents=True)
    monkeypatch.setattr(rep, "REPO_ROOT", repo)
    for target in (repo / ".git" / "x.json", repo / "benchmark" / "results" / "evaluation.json"):
        refused(env, target, "--force-overwrite")
        assert not target.exists()


def test_output_may_not_overwrite_a_git_tracked_file_even_with_force(env, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "notes.md").write_text("tracked notes")
    git(repo, "add", "notes.md")
    git(repo, "commit", "-q", "-m", "x")
    refused(env, repo / "notes.md", "--force-overwrite")
    assert (repo / "notes.md").read_text() == "tracked notes"
    assert ran_ok(env, repo / "fresh.md") in (0, 1)          # an untracked new file in a repo is fine
    assert (repo / "fresh.md").read_text().startswith(MARK)


def test_output_will_not_overwrite_a_file_this_tool_did_not_write(env, tmp_path):
    for name, text in (("mine.md", "my own notes\n"), ("mine.json", '{"hello": 1}'), ("broken.json", "{oops")):
        target = tmp_path / name
        target.write_text(text)
        refused(env, target)
        assert target.read_text() == text
    forced = tmp_path / "mine.md"
    assert ran_ok(env, forced, "--force-overwrite") in (0, 1)
    assert forced.read_text().startswith(MARK)


def test_output_overwrites_its_own_earlier_reports_and_empty_files(env, tmp_path):
    md, js, empty = tmp_path / "r.md", tmp_path / "r.json", tmp_path / "e.md"
    empty.write_text("")
    for target, fmt in ((md, "markdown"), (js, "json"), (empty, "markdown")):
        assert ran_ok(env, target, "--format", fmt) in (0, 1)
        assert ran_ok(env, target, "--format", fmt) in (0, 1)         # the second run replaces the first
    assert md.read_text().splitlines()[0] == MARK
    assert json.loads(js.read_text())["generated_by"] == "app.runner.report"


def test_a_lone_surrogate_never_truncates_an_existing_report(env, tmp_path):
    _, results = env
    path = put(results, "before", "a.test", "main_info", 1, False)
    path.write_text(path.read_text().replace('"wrong_page"', '"\\ud800 boom"'))
    for fmt, name in (("markdown", "r.md"), ("json", "r.json")):
        out = tmp_path / name
        assert ran_ok(env, out, "--format", fmt) in (0, 1)
        first = out.read_text()
        assert first and "\ud800" not in first and "boom" in first
        assert ran_ok(env, out, "--format", fmt) in (0, 1)
        assert out.read_text() == first                             # rewriting is stable, never empty


def test_a_failed_write_leaves_the_old_report_and_no_temporary_file(env, tmp_path, monkeypatch):
    out = tmp_path / "r.md"
    assert ran_ok(env, out) in (0, 1)
    good = out.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(rep.os, "replace", boom)
    with pytest.raises((OSError, SystemExit)):
        ran_ok(env, out, "--format", "markdown")
    assert out.read_bytes() == good
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_the_report_is_written_through_a_temporary_file_in_the_same_directory(env, tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(rep.os, "replace", lambda s, d: (seen.append((Path(s), Path(d))), real(s, d))[1])
    out = tmp_path / "r.json"
    ran_ok(env, out, "--format", "json")
    assert len(seen) == 1 and seen[0][0].parent == out.parent and seen[0][1] == out and seen[0][0] != out


def test_the_writer_refuses_a_path_that_became_a_symlink_and_never_writes_through_it(tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("keep me")
    link = tmp_path / "link.md"
    link.symlink_to(victim)
    with pytest.raises(ValueError):
        rep._write_atomic(link, b"new")
    assert victim.read_text() == "keep me" and link.is_symlink()
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
