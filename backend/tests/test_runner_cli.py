import json
import subprocess

import pytest

from app.runner import cli
from app.runner.cli import Deps, git_problems, load_criteria, main
from app.runner.results import load_runs
from tests.conftest import FakeFetcher, page
from tests.runner_fakes import ScriptedLLM, tool

O = "https://x.test"
LABEL = {"site": O, "tasks": {
    "main_info": {"urls": [f"{O}/pricing"]},
    "contact_form": {"urls": [f"{O}/contact"], "required_fields": ["email"]},
    "docs_api": {"none_exists": True}}}
CRITERIA = {"version": 1, "toolset_version": "1", "step_limit": 10, "max_turns": 24, "tasks": {}}
PRICING = f"<p>Plans start at $19 per month.</p>"
CONTACT = '<form action="/s"><label for="e">Email</label><input id="e" name="email"></form>'


def workspace(tmp_path, criteria=CRITERIA):
    (tmp_path / "criteria.json").write_text(json.dumps(criteria))
    (tmp_path / "label.json").write_text(json.dumps(LABEL))
    return tmp_path


def script_for_one_repeat():
    return [[tool("fetch_page", url="/pricing")],           # a page can only be the answer if it was actually fetched
            [tool("submit_answer", url="/pricing", quote="Plans start at $19 per month")],
            [tool("fetch_page", url="/contact")],
            [tool("submit_answer", url="/contact", fields=["email"])],
            [tool("read_llms_txt")],
            [tool("submit_answer", none_exists=True)]]


class Recorder:
    def __init__(self, *, verified=True, addrs=("93.184.216.34",), script=None, problems=()):
        self.fetchers = []
        self.llm = ScriptedLLM(script if script is not None else script_for_one_repeat())
        self.deps = Deps(verify=lambda ref: verified, resolver=lambda h: list(addrs),
                         make_fetcher=self.make_fetcher, make_llm=lambda: self.llm,
                         committed=lambda paths: list(problems), model="test-model")

    def make_fetcher(self, netloc, snapshot):
        f = FakeFetcher({f"{O}/pricing": page(f"{O}/pricing", PRICING),
                         f"{O}/contact": page(f"{O}/contact", CONTACT)})
        self.fetchers.append((netloc, snapshot, f))
        return f


def argv(ws, *extra, snapshot="before", repeats="1"):
    return ["run", "--label", str(ws / "label.json"), "--criteria", str(ws / "criteria.json"),
            "--results", str(ws / "results"), "--snapshot", snapshot, "--repeats", repeats, *extra]


def test_happy_path_runs_every_task_once_per_repeat_with_a_fresh_fetcher_each(tmp_path):
    ws, rec, out = workspace(tmp_path), Recorder(), []
    assert main(argv(ws), rec.deps, out.append, repo_root=ws) == 0
    runs = load_runs(ws / "results", "before")
    assert sorted(r["task"] for r in runs) == ["contact_form", "docs_api", "main_info"]
    assert all(r["result"]["success"] for r in runs)
    assert len(rec.fetchers) == 3 and len({id(f) for _, _, f in rec.fetchers}) == 3
    assert {s for _, s, _ in rec.fetchers} == {"before"} and {n for n, _, _ in rec.fetchers} == {"x.test"}
    assert runs[0]["config"]["model"] == "test-model" and len(runs[0]["config"]["criteria_sha256"]) == 64
    assert any("main_info" in line for line in out)


def test_three_repeats_make_nine_runs(tmp_path):
    ws, rec = workspace(tmp_path), Recorder(script=script_for_one_repeat() * 3)
    assert main(argv(ws, repeats="3"), rec.deps, lambda s: None, repo_root=ws) == 0
    assert len(load_runs(ws / "results", "before")) == 9


def test_tasks_option_limits_the_run(tmp_path):
    ws, rec = workspace(tmp_path), Recorder(script=[[tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]])
    assert main(argv(ws, "--tasks", "docs_api"), rec.deps, lambda s: None, repo_root=ws) == 0
    assert [r["task"] for r in load_runs(ws / "results", "before")] == ["docs_api"]


@pytest.mark.parametrize("kwargs,needle", [
    ({"verified": False}, "not proven"),
    ({"addrs": ("10.0.0.5",)}, "public"),
    ({"addrs": ()}, "public"),
    ({"problems": ("label.json is not committed",)}, "not committed"),
])
def test_refusals_run_nothing(tmp_path, kwargs, needle):
    ws, rec, out = workspace(tmp_path), Recorder(**kwargs), []
    assert main(argv(ws), rec.deps, out.append, repo_root=ws) == 2
    assert needle in " ".join(out) and rec.llm.calls == 0 and rec.fetchers == []
    assert load_runs(ws / "results", "before") == []


def test_allow_uncommitted_skips_the_git_check(tmp_path):
    ws, rec = workspace(tmp_path), Recorder(problems=("label.json is not committed",))
    assert main(argv(ws, "--allow-uncommitted"), rec.deps, lambda s: None, repo_root=ws) == 0


def test_criteria_must_match_the_code(tmp_path):
    ws = workspace(tmp_path, dict(CRITERIA, step_limit=7))
    out = []
    assert main(argv(ws), Recorder().deps, out.append, repo_root=ws) == 2 and "step_limit" in " ".join(out)
    with pytest.raises(ValueError):
        load_criteria(ws / "missing.json")


def test_bad_snapshot_and_bad_label_are_refused(tmp_path):
    ws = workspace(tmp_path)
    assert main(argv(ws, snapshot="../x"), Recorder().deps, lambda s: None, repo_root=ws) == 2
    (ws / "label.json").write_text("{nope")
    assert main(argv(ws), Recorder().deps, lambda s: None, repo_root=ws) == 2


def test_existing_result_files_are_never_overwritten(tmp_path):
    ws = workspace(tmp_path)
    assert main(argv(ws), Recorder().deps, lambda s: None, repo_root=ws) == 0
    rec = Recorder()
    assert main(argv(ws), rec.deps, lambda s: None, repo_root=ws) == 2 and rec.llm.calls == 0


def test_invalid_runs_give_exit_code_3(tmp_path):
    ws, rec = workspace(tmp_path), Recorder(script=[RuntimeError("rate limited"), [tool("fetch_page", url="/contact")],
                                                  [tool("submit_answer", url="/contact", fields=["email"])],
                                                  [tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]])
    assert main(argv(ws), rec.deps, lambda s: None, repo_root=ws) == 3
    runs = {r["task"]: r["result"] for r in load_runs(ws / "results", "before")}
    assert runs["main_info"]["valid"] is False and runs["main_info"]["reason"] == "llm_error"


def test_git_problems_detects_untracked_modified_and_clean_files(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "commit.gpgsign", "false")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "t")
    f = tmp_path / "criteria.json"
    f.write_text("{}")
    assert "not committed" in git_problems([f], repo_root=tmp_path)[0]
    git("add", "criteria.json")
    git("commit", "-q", "-m", "x")
    assert git_problems([f], repo_root=tmp_path) == []
    f.write_text('{"changed": 1}')
    assert "uncommitted changes" in git_problems([f], repo_root=tmp_path)[0]
