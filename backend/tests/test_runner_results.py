import json

import pytest

from app.runner.agent import RunResult
from app.runner import results as results_mod
from app.runner.results import (config_dict, guard_settings, harness_sha256, load_invalid_runs, load_runs, run_path,
                                save_run)

RUN = RunResult("main_info", True, "ok", True, {"url": "/p"}, 2, 3)
CFG = config_dict(model="m", snapshot="before", criteria_sha256="abc")


def test_config_records_everything_that_must_stay_fixed():
    cfg = config_dict(model="m", snapshot="before", criteria_sha256="abc", label_sha256="def", git_head="1234",
                      preregistered=True, rate_limit_seconds=0.5, request_budget=60)
    assert cfg == {"model": "m", "snapshot": "before", "step_limit": 10, "max_turns": 24, "toolset_version": "1",
                   "criteria_sha256": "abc", "label_sha256": "def", "git_head": "1234", "preregistered": True,
                   "harness_sha256": harness_sha256(), "rate_limit_seconds": 0.5, "request_budget": 60}
    assert len(harness_sha256()) == 64
    assert CFG["preregistered"] is False and CFG["rate_limit_seconds"] is None      # unknown values are null, not invented


def test_i6_the_harness_hash_changes_when_anything_the_model_sees_changes(monkeypatch):
    from app.runner import llm, tasks, tools
    base = harness_sha256()
    assert harness_sha256() == base
    specs = [dict(t) for t in tools.TOOL_SPECS]
    specs[0] = dict(specs[0], description="changed")
    monkeypatch.setattr(tools, "TOOL_SPECS", specs)
    assert harness_sha256() != base
    monkeypatch.undo()
    monkeypatch.setattr(tools, "TEXT_CLIP", 5999)
    assert harness_sha256() != base
    monkeypatch.undo()
    monkeypatch.setattr(llm, "MAX_TOKENS", 2048)
    assert harness_sha256() != base
    monkeypatch.undo()
    monkeypatch.setitem(tasks.TASKS, "docs_api", tasks.Task("docs_api", "t", "different {site}"))
    assert harness_sha256() != base
    monkeypatch.undo()
    monkeypatch.setattr(tasks, "system_prompt", lambda host, n: "another prompt")
    assert harness_sha256() != base


def test_i6_guard_settings_are_read_from_the_real_guard_even_when_wrapped(tmp_path):
    from app.fetch import CachedFetcher, GuardedFetcher
    from app.models import Page
    from tests.conftest import public_resolver

    class Plain:
        follows_redirects = False

        def get(self, url, *, rendered=False, address=None):
            return Page(url, 200, {}, "")
    g = GuardedFetcher(Plain(), "x.test", resolver=public_resolver, min_interval=0.25, max_requests=7)
    assert guard_settings(CachedFetcher(g, tmp_path)) == {"rate_limit_seconds": 0.25, "request_budget": 7}
    assert guard_settings(object()) == {"rate_limit_seconds": None, "request_budget": None}


def test_run_path_slugs_the_host(tmp_path):
    p = run_path(tmp_path, "before", "X.test:8443", "main_info", 2)
    assert p == tmp_path / "before" / "x.test_8443" / "main_info-2.json"


def test_save_and_load_round_trip_and_never_overwrite(tmp_path):
    path = save_run(tmp_path, "before", "x.test", 1, RUN, CFG)
    data = json.loads(path.read_text())
    assert data["site"] == "x.test" and data["task"] == "main_info" and data["repeat"] == 1
    assert data["result"]["success"] is True and data["config"]["criteria_sha256"] == "abc"
    assert "api_key" not in path.read_text().lower()
    with pytest.raises(FileExistsError):
        save_run(tmp_path, "before", "x.test", 1, RUN, CFG)
    assert [r["task"] for r in load_runs(tmp_path, "before")] == ["main_info"]
    assert load_runs(tmp_path, "after") == []


def test_i4_a_failed_write_leaves_neither_a_result_nor_a_temp_file(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(results_mod.json, "dumps", boom)
    with pytest.raises(OSError):
        save_run(tmp_path, "before", "x.test", 1, RUN, CFG)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_i4_a_successful_write_leaves_no_temp_file(tmp_path):
    save_run(tmp_path, "before", "x.test", 1, RUN, CFG)
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["main_info-1.json"]


def test_i4_load_runs_skips_and_reports_unreadable_files_and_keeps_invalid_runs_apart(tmp_path):
    save_run(tmp_path, "before", "x.test", 1, RUN, CFG)
    d = tmp_path / "before" / "x.test"
    (d / "docs_api-1.json").write_text("")                                     # an empty file from a crash
    (d / "contact_form-1.json").write_text("{nope")
    (d / "main_info-1.invalid-1.json").write_text(json.dumps({"task": "main_info", "result": {"valid": False}}))
    problems = []
    assert [r["task"] for r in load_runs(tmp_path, "before", problems)] == ["main_info"]
    assert sorted(problems) == ["x.test/contact_form-1.json is not readable", "x.test/docs_api-1.json is not readable"]
    assert load_runs(tmp_path, "before") == load_runs(tmp_path, "before", [])      # the problems list is optional
    assert [r["task"] for r in load_invalid_runs(tmp_path, "before")] == ["main_info"]
