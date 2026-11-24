"""Unit tests for scripts/verify_checkout.py: no network, no real clone, no real server."""
import importlib.util
import json
import socket
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_checkout.py"


@pytest.fixture(scope="module")
def vc():
    spec = importlib.util.spec_from_file_location("verify_checkout", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGit:
    """Maps a git argument tuple (without the leading 'git' and any '-C <dir>') to (returncode, stdout)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.answers.get(args, (1, ""))


def test_clone_commands_clone_without_hardlinks_then_check_out_the_ref(vc):
    cmds = vc.clone_commands("https://github.com/o/r.git", "feature", Path("/tmp/x/checkout"))
    assert cmds[0] == ["git", "clone", "--no-hardlinks", "https://github.com/o/r.git", "/tmp/x/checkout"]
    assert cmds[1] == ["git", "-C", "/tmp/x/checkout", "checkout", "--quiet", "feature"]


@pytest.mark.parametrize("origin,expected", [
    ("https://github.com/o/r.git", "https://github.com/o/r.git"),
    ("git@github.com:o/r.git", "git@github.com:o/r.git"),
    ("/some/local/path", "LOCAL"),
    ("", "LOCAL"),
    ("https://evil.test/o/r.git", "LOCAL"),
])
def test_default_source_is_the_github_origin_else_the_local_path(vc, origin, expected):
    git = FakeGit({("remote", "get-url", "origin"): (0 if origin else 1, origin)})
    got = vc.default_source(Path("/repo"), git)
    assert got == ("/repo" if expected == "LOCAL" else expected)


def test_a_head_that_is_not_pushed_is_refused_with_a_clear_message(vc):
    ok = FakeGit({("status", "--porcelain"): (0, ""), ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat"),
                  ("rev-parse", "HEAD"): (0, "a" * 40), ("rev-parse", "origin/feat"): (0, "a" * 40)})
    assert vc.pushed_problem(ok) is None
    ahead = FakeGit({("status", "--porcelain"): (0, ""), ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat"),
                     ("rev-parse", "HEAD"): (0, "b" * 40), ("rev-parse", "origin/feat"): (0, "a" * 40)})
    assert "not pushed" in vc.pushed_problem(ahead)
    no_remote = FakeGit({("status", "--porcelain"): (0, ""), ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat"),
                         ("rev-parse", "HEAD"): (0, "b" * 40)})
    assert "not pushed" in vc.pushed_problem(no_remote)
    dirty = FakeGit({("status", "--porcelain"): (0, " M README.md"), ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat"),
                     ("rev-parse", "HEAD"): (0, "a" * 40), ("rev-parse", "origin/feat"): (0, "a" * 40)})
    assert "uncommitted" in vc.pushed_problem(dirty)


def test_pick_free_port_returns_a_port_that_can_be_bound(vc):
    port = vc.pick_free_port()
    assert 1024 <= port <= 65535
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_wait_ready_succeeds_when_the_url_answers_and_times_out_cleanly_when_not(vc):
    clock = iter(range(0, 1000))
    calls = []

    def fake_get(url):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("not yet")
        return 200
    assert vc.wait_ready("http://127.0.0.1:1/x", 30, get=fake_get, sleep=lambda s: None, clock=lambda: next(clock)) is True
    clock = iter(range(0, 1000))

    def never(url):
        raise OSError("down")
    assert vc.wait_ready("http://127.0.0.1:1/x", 5, get=never, sleep=lambda s: None, clock=lambda: next(clock)) is False


def test_jsonrpc_builds_the_requests_the_smoke_test_sends(vc):
    body = vc.jsonrpc("tools/list", {}, 2)
    assert json.loads(body) == {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    init = json.loads(vc.jsonrpc("initialize", vc.INITIALIZE_PARAMS, 1))
    assert init["params"]["clientInfo"]["name"] == "verify-checkout" and "protocolVersion" in init["params"]


def test_parse_rpc_reads_plain_json_and_event_stream_bodies(vc):
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert vc.parse_rpc(json.dumps(payload)) == payload
    assert vc.parse_rpc("event: message\ndata: " + json.dumps(payload) + "\n\n") == payload
    assert vc.parse_rpc("not json") is None and vc.parse_rpc("") is None


CSP = "default-src 'none'"


def fake_http(overrides=None):
    overrides = overrides or {}
    tools = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": n} for n in ("audit_site", "get_report", "propose_fixes")]}}
    init = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "agentready"}}}
    table = {
        ("GET", "/"): (200, {"content-security-policy": CSP}, "<html><h1>AgentReady</h1></html>"),
        ("GET", "/ownership-token?domain=example.org"): (200, {}, json.dumps({"token": "t", "meta_tag": "<meta>"})),
        ("GET", "/audit"): (405, {"allow": "POST"}, "{}"),
        ("POST", "/mcp#1"): (200, {}, json.dumps(init)),
        ("POST", "/mcp#2"): (200, {}, json.dumps(tools)),
    }
    table.update(overrides)

    def http(method, path, headers=None, body=None):
        key = (method, path)
        if method == "POST" and body:
            key = (method, f"{path}#{json.loads(body)['id']}")
        return table[key]
    return http


def test_the_smoke_checks_all_pass_against_a_correct_server(vc):
    results = vc.smoke(fake_http())
    assert all(ok for _, ok, _ in results), results
    assert len(results) >= 5


@pytest.mark.parametrize("key,response,failing", [
    (("GET", "/"), (200, {}, "<h1>x</h1>"), "page"),
    (("GET", "/"), (500, {}, ""), "page"),
    (("GET", "/ownership-token?domain=example.org"), (200, {}, "{}"), "ownership"),
    (("GET", "/audit"), (200, {}, "{}"), "405"),
    (("POST", "/mcp#2"), (200, {}, json.dumps({"result": {"tools": [{"name": "approve_fix"}]}})), "tools"),
    (("POST", "/mcp#2"), (200, {}, json.dumps({"result": {"tools": [{"name": n} for n in (
        "audit_site", "get_report", "propose_fixes", "approve_fix")]}})), "tools"),
    (("POST", "/mcp#1"), (421, {}, "bad host"), "initialize"),
])
def test_each_smoke_check_fails_when_the_server_misbehaves(vc, key, response, failing):
    results = vc.smoke(fake_http({key: response}))
    bad = [name for name, ok, _ in results if not ok]
    assert bad and any(failing in name.lower() or failing in str(detail).lower() for name, ok, detail in results if not ok)


def test_the_results_table_shows_every_step_and_marks_failures(vc):
    rows = [("clone", vc.StepResult("PASS", "", 1.2)), ("tests", vc.StepResult("FAIL", "3 failed", 30.0)),
            ("server", vc.StepResult("SKIP", "not run", 0.0))]
    table = vc.format_table(rows)
    assert "clone" in table and "PASS" in table and "FAIL" in table and "3 failed" in table and "SKIP" in table
    assert vc.exit_code(rows) == 1
    assert vc.exit_code([("a", vc.StepResult("PASS", "", 0)), ("b", vc.StepResult("WARN", "node missing", 0))]) == 0


def test_a_failing_step_stops_the_run_and_the_rest_are_skipped(vc):
    ran = []

    def make(name, status):
        def fn():
            ran.append(name)
            return vc.StepResult(status, f"{name} detail", 0.0)
        return vc.Step(name, fn)
    rows = vc.run_steps([make("one", "PASS"), make("two", "FAIL"), make("three", "PASS")], out=lambda s: None)
    assert ran == ["one", "two"]
    assert [(n, r.status) for n, r in rows] == [("one", "PASS"), ("two", "FAIL"), ("three", "SKIP")]
    assert vc.exit_code(rows) == 1


def test_a_step_that_raises_is_a_failure_not_a_traceback(vc):
    def boom():
        raise RuntimeError("kaput")
    rows = vc.run_steps([vc.Step("x", boom)], out=lambda s: None)
    assert rows[0][1].status == "FAIL" and "RuntimeError" in rows[0][1].detail


def test_the_tracked_file_check_flags_generated_and_result_files(vc):
    clean = ["README.md", "backend/app/main.py", "benchmark/results/.gitkeep", "benchmark/sites.json"]
    assert vc.tracked_problems(clean) == []
    dirty = clean + ["backend/app/__pycache__/x.pyc", "backend/.venv/bin/python", "backend/.cache/a.json",
                     "benchmark/results/before/x/.main_info-1.json.123.tmp", ".env", "backend/x.egg-info/PKG-INFO"]
    problems = vc.tracked_problems(dirty)
    assert len(problems) == 6
    # published benchmark results are meant to be committed: they are not a problem
    assert vc.tracked_problems(["benchmark/results/before/x.test/main_info-1.json",
                                "benchmark/results/before/x.test/main_info-1.invalid-1.json"]) == []


def test_the_output_tail_keeps_the_end_and_bounds_the_size(vc):
    text = "\n".join(f"line {i}" for i in range(500))
    tail = vc.tail(text, lines=20, chars=300)
    assert tail.endswith("line 499") and len(tail) <= 300


def test_the_server_environment_has_a_random_secret_and_no_dev_flag(vc):
    env = vc.server_env({"PATH": "/bin", "AGENTREADY_DEV": "1", "ANTHROPIC_API_KEY": "sk-ant-x"}, Path("/tmp/cache"))
    assert env["AGENTREADY_SECRET"] and len(env["AGENTREADY_SECRET"]) >= 32 and "AGENTREADY_DEV" not in env
    assert "ANTHROPIC_API_KEY" not in env and env["AGENTREADY_CACHE"] == "/tmp/cache"
    assert vc.server_env({}, Path("/c"))["AGENTREADY_SECRET"] != env["AGENTREADY_SECRET"]


def test_the_script_never_touches_the_working_repository(vc):
    """Everything is created under a temporary directory; the helpers take an explicit destination."""
    text = SCRIPT.read_text()
    assert "tempfile.mkdtemp" in text and "shutil.rmtree" in text
    assert "--keep" in text


def test_cmd_step_reports_a_chosen_detail_on_success_and_the_output_tail_on_failure(vc):
    import sys
    ok = vc.cmd_step([sys.executable, "-c", "print('first'); print('[notice] pip upgrade')"], ok_detail="installed")
    assert (ok.status, ok.detail) == ("PASS", "installed")
    last = vc.cmd_step([sys.executable, "-c", "print('first'); print('last line')"])
    assert last.status == "PASS" and last.detail == "last line"
    bad = vc.cmd_step([sys.executable, "-c", "import sys; print('boom detail'); sys.exit(3)"], ok_detail="never shown")
    assert bad.status == "FAIL" and "boom detail" in bad.detail and "never shown" not in bad.detail
