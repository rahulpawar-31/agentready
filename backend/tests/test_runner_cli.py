import hashlib
import json
import os
import subprocess
import sys

import pytest

from app.runner import cli
from app.runner.cli import Deps, git_problems, load_criteria, main
from app.runner.results import harness_sha256, load_invalid_runs, load_runs
from tests.conftest import FakeFetcher, page
from tests.runner_fakes import ScriptedLLM, tool

O = "https://x.test"
LABEL = {"site": O, "tasks": {
    "main_info": {"urls": [f"{O}/pricing"], "quote_pattern": r"plans start at \$19"},
    "contact_form": {"urls": [f"{O}/contact"], "required_fields": ["email"]},
    "docs_api": {"none_exists": True}}}
CRITERIA = {"version": 1, "toolset_version": "1", "step_limit": 10, "max_turns": 24,
            "harness_sha256": harness_sha256(), "tasks": {}}
PRICING = f"<p>Plans start at $19 per month.</p>"
CONTACT = '<form action="/s"><label for="e">Email</label><input id="e" name="email"></form>'


def workspace(tmp_path, criteria=CRITERIA):
    (tmp_path / "criteria.json").write_text(json.dumps(criteria))
    (tmp_path / "x.test.json").write_text(json.dumps(LABEL))
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
        self.verify_calls = 0
        self.llm = ScriptedLLM(script if script is not None else script_for_one_repeat())
        def verify(ref):
            self.verify_calls += 1
            return verified
        self.deps = Deps(verify=verify, git_head=lambda: "abc123", resolver=lambda h: list(addrs),
                         make_fetcher=self.make_fetcher, make_llm=lambda: self.llm,
                         committed=lambda paths: list(problems), model="test-model")

    def make_fetcher(self, netloc, snapshot):
        f = FakeFetcher({f"{O}/pricing": page(f"{O}/pricing", PRICING),
                         f"{O}/contact": page(f"{O}/contact", CONTACT)})
        self.fetchers.append((netloc, snapshot, f))
        return f


def argv(ws, *extra, snapshot="before", repeats="1"):
    return ["run", "--label", str(ws / "x.test.json"), "--criteria", str(ws / "criteria.json"),
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


def test_cli_saves_actual_guard_logs_and_counts_batch_verification_once(tmp_path):
    from app.fetch import GuardedFetcher
    ws, rec = workspace(tmp_path), Recorder()
    make_fake = rec.deps.make_fetcher
    rec.deps.make_fetcher = lambda host, snapshot: GuardedFetcher(
        make_fake(host, snapshot), host, resolver=rec.deps.resolver, min_interval=0)
    probe = [{"method": "GET", "url": O + "/", "address": "93.184.216.34",
              "status": 200, "query_redacted": False}]
    rec.deps.verification_log = lambda: probe
    assert main(argv(ws), rec.deps, lambda s: None, repo_root=ws) == 0
    runs = load_runs(ws / "results", "before")
    assert sum(len(row["result"]["verification_request_log"]) for row in runs) == 1
    assert sum(len(row["result"]["request_log"]) for row in runs) == 3
    assert all(row["result"]["request_log"][0]["address"] == "93.184.216.34" for row in runs)


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
    (ws / "x.test.json").write_text("{nope")
    assert main(argv(ws), Recorder().deps, lambda s: None, repo_root=ws) == 2


def test_existing_result_files_are_never_overwritten(tmp_path):
    ws = workspace(tmp_path)
    assert main(argv(ws), Recorder().deps, lambda s: None, repo_root=ws) == 0
    before = {p.name: p.read_text() for p in (ws / "results").rglob("*.json")}
    rec, out = Recorder(), []
    assert main(argv(ws), rec.deps, out.append, repo_root=ws) == 2 and rec.llm.calls == 0
    assert "already has a valid result" in " ".join(out)
    assert {p.name: p.read_text() for p in (ws / "results").rglob("*.json")} == before


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


# --- review fixes ------------------------------------------------------------------
def test_i4_duplicate_tasks_are_run_once(tmp_path):
    ws, rec = workspace(tmp_path), Recorder(script=[[tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]])
    assert main(argv(ws, "--tasks", "docs_api,docs_api"), rec.deps, lambda s: None, repo_root=ws) == 0
    assert len(load_runs(ws / "results", "before")) == 1 and rec.llm.calls == 2


def test_i4_a_rerun_in_the_same_snapshot_redoes_only_invalid_runs_and_keeps_the_old_file(tmp_path):
    ws = workspace(tmp_path)
    first = Recorder(script=[RuntimeError("rate limited")] + script_for_one_repeat()[2:])
    assert main(argv(ws), first.deps, lambda s: None, repo_root=ws) == 3
    d = ws / "results" / "before" / "x.test"
    kept = {n: (d / n).read_text() for n in ("contact_form-1.json", "docs_api-1.json")}
    second = Recorder(script=script_for_one_repeat()[:2])
    out = []
    assert main(argv(ws), second.deps, out.append, repo_root=ws) == 0
    assert second.llm.calls == 2 and len(second.fetchers) == 1               # only main_info ran again
    assert {n: (d / n).read_text() for n in kept} == kept                    # valid results untouched
    runs = {r["task"]: r["result"] for r in load_runs(ws / "results", "before")}
    assert runs["main_info"]["valid"] is True and runs["main_info"]["success"] is True
    old = load_invalid_runs(ws / "results", "before")
    assert len(old) == 1 and old[0]["result"]["reason"] == "llm_error"
    assert (d / "main_info-1.invalid-1.json").exists()
    assert any("kept" in line.lower() or "rerun" in line.lower() for line in out)


def test_i4_a_second_invalid_run_is_moved_aside_to_the_next_free_number(tmp_path):
    ws = workspace(tmp_path)
    for _ in range(2):
        rec = Recorder(script=[RuntimeError("down")] + script_for_one_repeat()[2:], )
        main(argv(ws, "--tasks", "main_info"), rec.deps, lambda s: None, repo_root=ws)
    third = Recorder(script=script_for_one_repeat()[:2])
    assert main(argv(ws, "--tasks", "main_info"), third.deps, lambda s: None, repo_root=ws) == 0
    names = sorted(p.name for p in (ws / "results" / "before" / "x.test").iterdir())
    assert names == ["main_info-1.invalid-1.json", "main_info-1.invalid-2.json", "main_info-1.json"]


def test_i4_an_unreadable_result_file_is_treated_as_invalid_and_rerun(tmp_path):
    ws = workspace(tmp_path)
    d = ws / "results" / "before" / "x.test"
    d.mkdir(parents=True)
    (d / "docs_api-1.json").write_text("")
    rec = Recorder(script=[[tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]])
    assert main(argv(ws, "--tasks", "docs_api"), rec.deps, lambda s: None, repo_root=ws) == 0
    assert (d / "docs_api-1.invalid-1.json").exists() and len(load_runs(ws / "results", "before")) == 1


def test_i4_the_loop_stops_after_the_first_authentication_failure(tmp_path):
    class AuthenticationError(Exception):
        pass
    ws = workspace(tmp_path)
    rec = Recorder(script=[AuthenticationError("Error code: 401 - invalid x-api-key")])
    out = []
    assert main(argv(ws, repeats="3"), rec.deps, out.append, repo_root=ws) == 3
    assert rec.llm.calls == 1 and len(list((ws / "results").rglob("*.json"))) == 1
    assert "authentication" in " ".join(out).lower()


def test_i4_a_model_client_that_cannot_be_created_is_a_clean_refusal(tmp_path):
    ws, rec, out = workspace(tmp_path), Recorder(), []
    def broken():
        raise ImportError("No module named 'anthropic'")
    rec.deps.make_llm = broken
    assert main(argv(ws), rec.deps, out.append, repo_root=ws) == 2 and "ImportError" in " ".join(out)
    assert list((ws / "results").rglob("*.json")) == []


def test_m5_the_results_check_happens_before_any_network_probe(tmp_path):
    ws = workspace(tmp_path)
    assert main(argv(ws), Recorder().deps, lambda s: None, repo_root=ws) == 0
    rec = Recorder()
    assert main(argv(ws), rec.deps, lambda s: None, repo_root=ws) == 2 and rec.verify_calls == 0


def test_m6_repeats_are_bounded(tmp_path):
    ws = workspace(tmp_path)
    for bad in ("0", "11", "-1", "1000000"):
        rec, out = Recorder(), []
        assert main(argv(ws, repeats=bad), rec.deps, out.append, repo_root=ws) == 2
        assert "repeats" in " ".join(out) and rec.llm.calls == 0


def test_m8_the_label_file_name_must_be_the_site_host(tmp_path):
    ws = workspace(tmp_path)
    (ws / "x.test.json").rename(ws / "something.json")
    out = []
    assert main(argv(ws)[:2] + [str(ws / "something.json")] + argv(ws)[3:], Recorder().deps, out.append, repo_root=ws) == 2
    assert "file name" in " ".join(out)


def test_i5_i6_the_config_pins_label_git_head_prereg_and_harness(tmp_path):
    ws, rec = workspace(tmp_path), Recorder()
    assert main(argv(ws), rec.deps, lambda s: None, repo_root=ws) == 0
    cfg = load_runs(ws / "results", "before")[0]["config"]
    assert cfg["label_sha256"] == hashlib.sha256((ws / "x.test.json").read_bytes()).hexdigest()
    assert cfg["git_head"] == "abc123" and cfg["preregistered"] is True
    assert cfg["harness_sha256"] == harness_sha256() and cfg["criteria_sha256"]
    (tmp_path / "b").mkdir()
    ws2, rec2 = workspace(tmp_path / "b"), Recorder(problems=("x is not committed",))
    assert main(argv(ws2, "--allow-uncommitted"), rec2.deps, lambda s: None, repo_root=ws2) == 0
    assert load_runs(ws2 / "results", "before")[0]["config"]["preregistered"] is False


def test_i6_the_guard_settings_in_the_config_come_from_the_real_guard(tmp_path):
    from app.fetch import CachedFetcher, GuardedFetcher
    from app.models import Page
    from tests.conftest import public_resolver

    class Plain:
        follows_redirects = False

        def get(self, url, *, rendered=False, address=None):
            return Page(url, 404, {}, "")
    ws = workspace(tmp_path)
    rec = Recorder(script=[[tool("read_llms_txt")], [tool("submit_answer", none_exists=True)]])
    rec.deps.make_fetcher = lambda netloc, snap: CachedFetcher(
        GuardedFetcher(Plain(), netloc, resolver=public_resolver, min_interval=0.25, max_requests=17), tmp_path / "cache")
    assert main(argv(ws, "--tasks", "docs_api"), rec.deps, lambda s: None, repo_root=ws) == 0
    cfg = load_runs(ws / "results", "before")[0]["config"]
    assert (cfg["rate_limit_seconds"], cfg["request_budget"]) == (0.25, 17)


def test_i6_criteria_must_carry_the_current_harness_hash(tmp_path):
    for bad in (dict(CRITERIA, harness_sha256="0" * 64), {k: v for k, v in CRITERIA.items() if k != "harness_sha256"}):
        ws, out = workspace(tmp_path, bad), []
        assert main(argv(ws), Recorder().deps, out.append, repo_root=ws) == 2 and "harness_sha256" in " ".join(out)


# --- pre-registration against a real git repository --------------------------------
def make_repo(tmp_path):
    def git(*a, check=True):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=check, capture_output=True, text=True)
    git("init", "-q")
    for k, v in (("commit.gpgsign", "false"), ("user.email", "t@t.test"), ("user.name", "t")):
        git("config", k, v)
    return git


def test_i5_a_committed_symlink_is_not_a_committed_label(tmp_path):
    git = make_repo(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text('{"edited": "after seeing results"}')
    link = tmp_path / "label.json"
    link.symlink_to(outside)
    git("add", "label.json")
    git("commit", "-q", "-m", "x")
    problems = git_problems([link], repo_root=tmp_path)
    assert problems and "symlink" in problems[0]


def test_i5_assume_unchanged_does_not_hide_an_edit(tmp_path):
    git = make_repo(tmp_path)
    f = tmp_path / "criteria.json"
    f.write_text("{}")
    git("add", "criteria.json")
    git("commit", "-q", "-m", "x")
    git("update-index", "--assume-unchanged", "criteria.json")
    f.write_text('{"edited": 1}')
    assert git_problems([f], repo_root=tmp_path) and "uncommitted" in git_problems([f], repo_root=tmp_path)[0]


def test_i5_a_staged_but_uncommitted_file_and_a_staged_edit_are_problems(tmp_path):
    git = make_repo(tmp_path)
    f = tmp_path / "criteria.json"
    f.write_text("{}")
    git("add", "criteria.json")
    assert "not committed" in git_problems([f], repo_root=tmp_path)[0]          # staged, never committed
    git("commit", "-q", "-m", "x")
    f.write_text('{"a": 1}')
    git("add", "criteria.json")
    assert "uncommitted" in git_problems([f], repo_root=tmp_path)[0]           # staged edit
    git("commit", "-q", "-m", "y")
    assert git_problems([f], repo_root=tmp_path) == []


def test_i5_a_glob_like_name_is_matched_literally(tmp_path):
    git = make_repo(tmp_path)
    (tmp_path / "criteria.json").write_text("{}")
    git("add", "criteria.json")
    git("commit", "-q", "-m", "x")
    star = tmp_path / "cri*.json"
    star.write_text("{}")
    assert "not committed" in git_problems([star], repo_root=tmp_path)[0]


def test_cosmetic_a_file_outside_the_repository_is_named_without_a_relative_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    msg = git_problems([outside], repo_root=repo)[0]
    assert "../" not in msg and "outside.json" in msg and "outside the repository" in msg


def test_the_committed_criteria_file_matches_the_code():
    criteria = cli.REPO_ROOT / "benchmark" / "criteria.json"
    data, sha = load_criteria(criteria)                       # raises if a constant or the harness hash differs
    assert data["harness_sha256"] == harness_sha256() and len(sha) == 64
    text = json.dumps(data["tasks"])
    assert "quote_pattern" in text and "required" in text


def test_snapshot_names_with_a_trailing_newline_or_the_reserved_name_audits_are_refused(tmp_path):
    ws = workspace(tmp_path)
    rec = Recorder()
    for bad in ("before\n", "audits", "audits\n", " before"):
        assert main(argv(ws, snapshot=bad), rec.deps, lambda s: None, repo_root=ws) == 2, repr(bad)
    assert rec.fetchers == [] and rec.verify_calls == 0 and not (ws / "results").exists()


def test_a_missing_agentready_secret_is_a_clean_refusal_not_a_traceback(tmp_path):
    """cli run builds the real dependencies, which import the API module: that refuses to start without a secret."""
    ws = workspace(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("AGENTREADY_SECRET", "AGENTREADY_DEV")}
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run([sys.executable, "-m", "app.runner.cli", *argv(ws)], cwd=backend, env={**env, "PYTHONPATH": backend},
                          capture_output=True, text=True, timeout=120)
    text = done.stdout + done.stderr
    assert done.returncode == 2, text
    assert "AGENTREADY_SECRET" in text and "Traceback" not in text


def test_the_audit_cli_also_refuses_cleanly_without_the_secret(tmp_path, monkeypatch):
    from app.runner import audit_cli

    def no_secret():
        raise RuntimeError("Set AGENTREADY_SECRET (or AGENTREADY_DEV=1 for local development only)")
    monkeypatch.setattr(audit_cli, "default_deps", no_secret)
    label = tmp_path / "x.test.json"
    label.write_text(json.dumps({**LABEL, "audit_checks": {"before": {
        c: True for c in __import__("app.runner.labels", fromlist=["x"]).AUDIT_CHECK_IDS}}}))
    monkeypatch.setattr(audit_cli, "git_problems", lambda paths, repo_root: [])
    lines = []
    code = audit_cli.main(["--label", str(label), "--snapshot", "before", "--results", str(tmp_path / "r")],
                          out=lines.append, repo_root=tmp_path)
    assert code == 2 and any("AGENTREADY_SECRET" in line for line in lines)


# ---- Gemini: a used-up quota stops the batch like a rejected key ----------------------------------------------
def test_the_loop_stops_after_a_rate_limit_or_quota_failure_and_says_how_to_resume(tmp_path):
    class GeminiRateLimitError(Exception):
        pass
    ws = workspace(tmp_path)
    rec = Recorder(script=[GeminiRateLimitError("Error code: 429 RESOURCE_EXHAUSTED: quota per day")])
    out = []
    assert main(argv(ws, repeats="3"), rec.deps, out.append, repo_root=ws) == 3
    assert rec.llm.calls == 1 and len(list((ws / "results").rglob("*.json"))) == 1
    text = " ".join(out).lower()
    assert "quota" in text and "rerun the same command" in text


def test_the_refusal_for_a_missing_client_names_both_keys(tmp_path):
    ws, rec, out = workspace(tmp_path), Recorder(), []
    def broken():
        raise RuntimeError("GEMINI_API_KEY is not set")
    rec.deps.make_llm = broken
    assert main(argv(ws), rec.deps, out.append, repo_root=ws) == 2
    text = " ".join(out)
    assert "GEMINI_API_KEY" in text and "ANTHROPIC_API_KEY" in text


def test_the_default_deps_build_the_client_through_the_factory(monkeypatch):
    from app.runner import cli
    from app.runner.llm import make_client
    monkeypatch.setenv("AGENTREADY_DEV", "1")
    assert cli.default_deps().make_llm is make_client


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_default_verification_uses_the_requested_scheme(monkeypatch, scheme):
    from app import main as api
    from app.audit import parse_url

    host = "x.test:8080"
    origin = f"{scheme}://{host}"
    token = api.make_token(host, api.SECRET)
    html = f'<html><head><meta name="agentready-verification" content="{token}"></head></html>'
    probe = FakeFetcher({f"{origin}/": page(f"{origin}/", html)})
    monkeypatch.setattr(api, "build_probe_fetcher", lambda host, **kwargs: probe)

    assert cli.default_deps().verify(parse_url(origin))
    assert probe.calls == [(f"{origin}/", False)]
