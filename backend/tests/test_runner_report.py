"""Tests for app.runner.report (merged in PR #6 without tests). Fixtures are written through the real
results.save_run so the real file format is exercised."""
import json
import os
from pathlib import Path

import pytest

from app.runner import report as rep
from app.runner.agent import RunResult
from app.runner.results import config_dict, move_aside, save_run
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


def cfg(snapshot="before", **over) -> dict:
    base = config_dict(model="m", snapshot=snapshot, criteria_sha256="c" * 64, label_sha256="l" * 64,
                       git_head="h" * 40, preregistered=True, rate_limit_seconds=0.5, request_budget=60)
    base.update(over)
    return base


def run_result(task, success, valid=True, reason=None, error=""):
    return RunResult(task, success, reason or ("ok" if success else "wrong_page"), valid, None, 2, 3, error=error)


def put(results, snapshot, host, task, repeat, success, *, valid=True, config=None, invalid_file=False,
        reason=None, error=""):
    config = cfg(snapshot) if config is None else config
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


@pytest.fixture
def env(tmp_path):
    labels, results = tmp_path / "labels", tmp_path / "results"
    results.mkdir()
    for h in HOSTS:
        write_label(labels, h)
    return labels, results


def build(env, before="before", after="after", repeats=3):
    labels, results = env
    return rep.build_report(results, labels, before, after, repeats)


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
    fill(results, "after", hosts=("a.test",), successes=lambda h, t, r: r != 2, config=cfg("after"))
    p = build(env)["paired_comparison"]
    assert p["valid_paired_runs"] == 9                             # only a.test, 3 tasks x 3 repeats
    assert p["before_successes"] == 6 and p["after_successes"] == 6 and p["change_pp"] == 0.0
    assert p["transitions"] == {"fail_to_pass": 3, "pass_to_fail": 3, "pass_to_pass": 3, "fail_to_fail": 0}
    assert p["unpaired_before_runs"] == 9 and p["unpaired_after_runs"] == 0       # b.test is before-only


@pytest.mark.parametrize("field,value", [
    ("criteria_sha256", "x" * 64), ("harness_sha256", "y" * 64), ("label_sha256", "z" * 64), ("model", "other"),
    ("step_limit", 5), ("max_turns", 3), ("request_budget", 10), ("rate_limit_seconds", 1.0),
    ("toolset_version", "2"), ("preregistered", False),
])
def test_runs_with_a_different_configuration_are_never_paired(env, field, value):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False)
    put(results, "after", "a.test", "main_info", 1, True, config=cfg("after", **{field: value}))
    p = build(env)["paired_comparison"]
    assert p["valid_paired_runs"] == 0 and p["change_pp"] is None
    assert p["config_mismatches_excluded"] == [{"site": "a.test", "task": "main_info", "repeat": 1, "fields": [field]}]
    assert p["unpaired_before_runs"] == 0 and p["unpaired_after_runs"] == 0


def test_a_changed_git_head_alone_does_not_block_pairing(env):
    """The commit moves when a new label is committed between runs; the harness hash is what must match."""
    _, results = env
    put(results, "before", "a.test", "main_info", 1, False)
    put(results, "after", "a.test", "main_info", 1, True, config=cfg("after", git_head="9" * 40))
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
    r = rep.build_report(results, labels, "before", "after", 3)
    return r["problems"], rep.main(["--before", "before", "--after", "after", "--results", str(results),
                                    "--labels", str(labels)])


def test_a_result_for_a_site_with_no_label_is_a_problem_and_exit_1(env):
    problems, code = problems_for(env, lambda l, r: put(r, "before", "zzz.test", "main_info", 1, True))
    assert any("no committed site label" in p for p in problems) and code == 1


def test_a_malformed_result_file_is_a_problem_and_exit_1(env):
    def mutate(l, r):
        put(r, "before", "a.test", "main_info", 1, True)
        (r / "before" / "a.test" / "docs_api-1.json").write_text("{nope")
    problems, code = problems_for(env, mutate)
    assert any("not readable" in p for p in problems) and code == 1


def test_duplicate_valid_runs_for_one_key_are_a_problem(env):
    def mutate(l, r):
        path = put(r, "before", "a.test", "main_info", 1, True)
        dup = path.with_name("main_info-1.copy.json")
        dup.write_text(path.read_text())
    problems, code = problems_for(env, mutate)
    assert any("duplicate valid result" in p for p in problems) and code == 1


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
    fill(results, "after", config=cfg("after"))
    before = snapshot_tree(results)
    out = tmp_path / "out" / "evaluation.json"
    code = rep.main(["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels),
                     "--format", "json", "--output", str(out)])
    assert code == 0 and snapshot_tree(results) == before and out.exists()
    rep.main(["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels)])
    assert snapshot_tree(results) == before


def args(env, *more):
    labels, results = env
    return ["--before", "before", "--after", "after", "--results", str(results), "--labels", str(labels), *more]


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
            rep.main(["--before", before, "--after", after, "--results", str(env[1]), "--labels", str(env[0])])


@pytest.mark.parametrize("name", ["", "a b", "../x", "x/y", "x" * 65, "ünï", "a.b"])
def test_snapshot_names_must_match_the_server_pattern(env, name):
    with pytest.raises(SystemExit):
        rep.main(["--before", name, "--after", "after", "--results", str(env[1]), "--labels", str(env[0])])


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
               "## Paired comparison", "## Sample size", "## Audit check accuracy", "## Limitations",
               "## Data issues"}
    assert set(headings) <= allowed and "## Injected heading" not in md
    rows = [l for l in md.splitlines() if l.startswith("|")]
    assert all(l.startswith(("| Site", "|---", "| a.test", "| b.test", "| c.test")) for l in rows)
    assert not any(l.startswith("| fake") for l in md.splitlines())
    assert max(len(l) for l in md.splitlines()) <= 700
    assert "](https://evil.test)" not in md and "<script>" not in md
    assert "‮" not in md and "⁦" not in md


# 10 ------------------------------------------------------------------------------------------------------
def test_same_inputs_give_byte_identical_markdown_and_json(env, tmp_path):
    labels, results = env
    fill(results, "before", successes=lambda h, t, r: (h, t, r) != ("a.test", "docs_api", 2))
    fill(results, "after", config=cfg("after"))
    a, b = tmp_path / "a", tmp_path / "b"
    for out, fmt in ((a, "markdown"), (b, "markdown")):
        rep.main(args(env, "--format", fmt, "--output", str(out / "r.md")))
    assert (a / "r.md").read_bytes() == (b / "r.md").read_bytes()
    for out in (a, b):
        rep.main(args(env, "--format", "json", "--output", str(out / "r.json")))
    assert (a / "r.json").read_bytes() == (b / "r.json").read_bytes()
    keys = list(json.loads((a / "r.json").read_text()))
    assert keys == sorted(keys) or "paired_comparison" in keys


# 11 ------------------------------------------------------------------------------------------------------
def test_provenance_lists_the_distinct_hashes_models_and_commits_of_each_snapshot(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    r = build(env)
    prov = r["before"]["provenance"]
    assert prov["criteria_sha256"] == ["c" * 64] and prov["model"] == ["m"] and prov["git_head"] == ["h" * 40]
    assert len(prov["harness_sha256"]) == 1
    md = rep.to_markdown(r)
    assert "## Provenance" in md and "c" * 64 in md and "model: m" in md


@pytest.mark.parametrize("field", ["criteria_sha256", "harness_sha256", "model"])
def test_mixed_criteria_harness_or_model_inside_one_snapshot_is_a_data_issue(env, field):
    _, results = env
    put(results, "before", "a.test", "main_info", 1, True)
    put(results, "before", "a.test", "main_info", 2, True, config=cfg("before", **{field: "other-" + "9" * 5}))
    r = build(env)
    assert any(field in p and "more than one value" in p for p in r["problems"])


def test_a_small_sample_is_labelled_as_indicative_and_no_significance_is_claimed(env):
    _, results = env
    fill(results, "before", hosts=("a.test",))
    fill(results, "after", hosts=("a.test",), config=cfg("after"))
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


def test_the_audit_check_accuracy_stays_not_measured():
    r = rep.build_report(Path("/nonexistent-results"), Path("/nonexistent-labels"), "before", "after")
    assert r["audit_check_accuracy"]["status"] == "not measured"
