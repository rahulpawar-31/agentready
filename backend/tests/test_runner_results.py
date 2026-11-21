import json

import pytest

from app.runner.agent import RunResult
from app.runner.results import config_dict, load_runs, run_path, save_run

RUN = RunResult("main_info", True, "ok", True, {"url": "/p"}, 2, 3)
CFG = config_dict(model="m", snapshot="before", criteria_sha256="abc")


def test_config_records_everything_that_must_stay_fixed():
    assert CFG == {"model": "m", "snapshot": "before", "step_limit": 10, "max_turns": 24, "toolset_version": "1",
                   "criteria_sha256": "abc", "rate_limit_seconds": 0.5, "request_budget": 60}


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
