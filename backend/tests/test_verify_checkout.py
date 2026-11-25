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


def test_clone_commands_restrict_protocols_use_separators_and_check_out_the_ref(vc):
    cmds = vc.clone_commands("https://github.com/o/r.git", "feature", Path("/tmp/x/checkout"))
    assert cmds[0] == ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                       "-c", "protocol.file.allow=user", "-c", "protocol.ssh.allow=always",
                       "clone", "--no-hardlinks", "--", "https://github.com/o/r.git", "/tmp/x/checkout"]
    assert cmds[1] == ["git", "-C", "/tmp/x/checkout", "checkout", "--quiet", "feature", "--"]


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


def pushed_git(remote_line, status="", head="a" * 40, branch="feat", tracking=None):
    answers = {("status", "--porcelain"): (0, status), ("rev-parse", "--abbrev-ref", "HEAD"): (0, branch),
               ("rev-parse", "HEAD"): (0, head),
               ("ls-remote", "--heads", "origin", f"refs/heads/{branch}"): remote_line}
    if tracking:                                            # a stale local remote-tracking ref must never be trusted
        answers[("rev-parse", f"origin/{branch}")] = (0, tracking)
    return FakeGit(answers)


def test_a_head_that_is_not_pushed_is_refused_with_a_clear_message(vc):
    sha = "a" * 40
    assert vc.pushed_problem(pushed_git((0, f"{sha}\trefs/heads/feat"))) is None
    ahead = vc.pushed_problem(pushed_git((0, f"{'a' * 40}\trefs/heads/feat"), head="b" * 40))
    assert "not pushed" in ahead and "aaaaaaaaaa" in ahead and "bbbbbbbbbb" in ahead
    assert "not pushed" in vc.pushed_problem(pushed_git((0, "")))            # the remote has no such branch
    assert "not pushed" in vc.pushed_problem(pushed_git((1, "")))            # the remote could not be read
    assert "uncommitted" in vc.pushed_problem(pushed_git((0, f"{sha}\trefs/heads/feat"), status=" M README.md"))


def test_a_stale_remote_tracking_ref_is_never_trusted_over_the_real_remote(vc):
    """The remote was force-pushed: origin/feat locally still equals HEAD, but the remote moved on."""
    git = pushed_git((0, f"{'c' * 40}\trefs/heads/feat"), head="a" * 40, tracking="a" * 40)
    problem = vc.pushed_problem(git)
    assert problem and "not pushed" in problem and "cccccccccc" in problem


def test_the_cloned_commit_must_be_the_commit_being_verified(vc):
    assert vc.commit_problem("a" * 40, "a" * 40) is None
    assert vc.commit_problem("a" * 40, None) is None                         # nothing to compare with: not a failure
    problem = vc.commit_problem("b" * 40, "a" * 40)
    assert problem and "bbbbbbbbbb" in problem and "aaaaaaaaaa" in problem


def test_the_expected_commit_comes_from_head_a_full_sha_or_the_remote(vc):
    full = "d" * 40
    assert vc.expected_sha(FakeGit({}), "src", "main", local_head="a" * 40, explicit=False) == "a" * 40
    assert vc.expected_sha(FakeGit({}), "src", full, local_head="a" * 40, explicit=True) == full
    git = FakeGit({("ls-remote", "src", "refs/heads/feat"): (0, f"{'e' * 40}\trefs/heads/feat")})
    assert vc.expected_sha(git, "src", "feat", local_head="a" * 40, explicit=True) == "e" * 40
    assert vc.expected_sha(FakeGit({}), "src", "feat", local_head="a" * 40, explicit=True) is None


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



# ---- Group E: the review's findings on scripts/verify_checkout.py ---------------------------------------------
import http.server
import threading
from types import SimpleNamespace


@pytest.mark.parametrize("source,ref", [
    ("--upload-pack=touch /tmp/x", "main"), ("-c core.sshCommand=y", "main"), ("ext::sh -c touch% /tmp/x", "main"),
    ("fd::17", "main"), ("", "main"), ("https://github.com/o/r.git\nx", "main"),
    ("https://github.com/o/r.git", "--upload-pack=touch /x"), ("https://github.com/o/r.git", "-c core.sshCommand=y"),
    ("https://github.com/o/r.git", ""), ("https://github.com/o/r.git", "a b"), ("https://github.com/o/r.git", "a..b"),
    ("https://github.com/o/r.git", "ext::x"),
])
def test_hostile_sources_and_refs_are_refused_before_git_runs(vc, source, ref):
    assert vc.argument_problem(source, ref)


@pytest.mark.parametrize("source,ref", [
    ("https://github.com/o/r.git", "main"), ("git@github.com:o/r.git", "feature/x-1"), ("/tmp/my repo", "a" * 40),
    ("/Users/me/Projects/agentready", "watcher/agentready-release-hardening"),
])
def test_ordinary_sources_and_refs_are_accepted(vc, source, ref):
    assert vc.argument_problem(source, ref) is None


def test_a_hostile_argument_fails_the_first_step_and_nothing_is_cloned(vc, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(vc, "run_cmd", lambda *a, **k: ran.append(a) or (0, ""))
    args = SimpleNamespace(source="--upload-pack=touch /tmp/x", ref="main", keep=False, skip_node=False,
                           pip_timeout=600, test_timeout=900)
    steps = vc.build_steps(args, tmp_path, FakeGit({}))
    first = steps[0].fn()
    assert first.status == "FAIL" and "source" in first.detail.lower()
    assert ran == []


def test_credentials_are_removed_from_anything_that_is_printed(vc):
    assert "tok3n" not in vc.safe_url("https://user:tok3n@github.com/o/r.git")
    assert vc.safe_url("git@github.com:o/r.git") == "git@github.com:o/r.git"
    text = vc.safe_url("fatal: unable to access 'https://u:p4ss@github.com/o/r/': The requested URL returned error: 403")
    assert "p4ss" not in text and "github.com/o/r" in text


def test_the_timeouts_default_to_600_and_900_and_can_be_changed(vc):
    d = vc.parse_args([])
    assert (d.pip_timeout, d.test_timeout) == (600, 900)
    c = vc.parse_args(["--pip-timeout", "30", "--test-timeout", "60"])
    assert (c.pip_timeout, c.test_timeout) == (30, 60)


def test_the_install_and_test_steps_pass_their_timeouts_to_the_commands(vc, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(vc, "run_cmd", lambda cmd, **k: (seen.setdefault(" ".join(map(str, cmd[1:4])), k.get("timeout")), (0, "ok"))[1])
    args = SimpleNamespace(source="/x", ref="main", keep=False, skip_node=True, pip_timeout=111, test_timeout=222)
    steps = {s.name: s for s in vc.build_steps(args, tmp_path, FakeGit({}))}
    steps["install"].fn()
    steps["python tests"].fn()
    assert 111 in seen.values() and 222 in seen.values()


def test_tracked_files_that_are_secrets_are_flagged_but_examples_are_not(vc):
    flagged = vc.tracked_problems([".env", ".env.local", "backend/.env.production", "keys/server.pem", "id_rsa",
                                   "home/id_rsa.pub", ".env.example", "backend/.env.sample", "README.md"])
    assert flagged == [".env", ".env.local", "backend/.env.production", "keys/server.pem", "id_rsa", "home/id_rsa.pub"]


class FakeProc:
    def __init__(self, polls):
        self.polls = list(polls)
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def communicate(self, timeout=None):
        return ("log line", "")


def run_server(vc, tmp_path, *, polls, smoke_results, ready=True, in_use=False):
    commands = []

    def popen(cmd, **kw):
        commands.append((cmd, kw))
        return proc

    proc = FakeProc(polls)
    result = vc.server_step(Path("/venv/bin/python"), tmp_path, tmp_path / "cache", print,
                            popen=popen, free_port=lambda: 45678, port_in_use=lambda port: in_use,
                            wait=lambda url, timeout: ready, smoke_run=lambda base: smoke_results)
    return result, commands, proc


GOOD = [("page", True, "ok"), ("mcp", True, "ok")]


def test_the_server_is_bound_to_loopback_only_and_stopped_afterwards(vc, tmp_path):
    result, commands, proc = run_server(vc, tmp_path, polls=[None], smoke_results=GOOD)
    cmd = commands[0][0]
    assert result.status == "PASS" and proc.terminated
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1" and "0.0.0.0" not in cmd and "::" not in cmd
    assert "AGENTREADY_DEV" not in commands[0][1]["env"] and len(commands[0][1]["env"]["AGENTREADY_SECRET"]) == 64


def test_a_failing_smoke_check_fails_the_step_and_still_stops_the_server(vc, tmp_path):
    result, _, proc = run_server(vc, tmp_path, polls=[None], smoke_results=[("page", False, "status 500")])
    assert result.status == "FAIL" and "page" in result.detail and proc.terminated


def test_a_server_that_exited_during_the_checks_is_a_failure_even_if_the_checks_passed(vc, tmp_path):
    """Another process on the port could have answered: the child must have been alive before and after."""
    result, _, _ = run_server(vc, tmp_path, polls=[None, 3], smoke_results=GOOD)
    assert result.status == "FAIL" and "exited" in result.detail
    dead, _, _ = run_server(vc, tmp_path, polls=[3], smoke_results=GOOD)
    assert dead.status == "FAIL" and "exited" in dead.detail


def test_a_port_that_is_already_listening_is_refused_and_no_server_is_started(vc, tmp_path):
    result, commands, _ = run_server(vc, tmp_path, polls=[None], smoke_results=GOOD, in_use=True)
    assert result.status == "FAIL" and "already" in result.detail and commands == []


def test_a_server_that_never_becomes_ready_fails_and_is_stopped(vc, tmp_path):
    result, _, proc = run_server(vc, tmp_path, polls=[None], smoke_results=GOOD, ready=False)
    assert result.status == "FAIL" and "ready" in result.detail and proc.terminated


def test_port_in_use_sees_a_real_listener(vc):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert vc.port_in_use(listener.getsockname()[1]) is True
    assert vc.port_in_use(vc.pick_free_port()) is False


def test_clean_tree_fails_on_a_dirty_tree_untracked_files_and_tracked_secrets(vc):
    clean = FakeGit({("status", "--porcelain"): (0, ""), ("ls-files",): (0, "README.md\nbackend/app/main.py")})
    assert vc.clean_tree_step(clean).status == "PASS"
    dirty = FakeGit({("status", "--porcelain"): (0, " M README.md"), ("ls-files",): (0, "README.md")})
    assert vc.clean_tree_step(dirty).status == "FAIL"
    untracked = FakeGit({("status", "--porcelain"): (0, "?? backend/.cache/x"), ("ls-files",): (0, "README.md")})
    r = vc.clean_tree_step(untracked)
    assert r.status == "FAIL" and "dirty" in r.detail
    secret = FakeGit({("status", "--porcelain"): (0, ""), ("ls-files",): (0, "README.md\n.env.local")})
    r = vc.clean_tree_step(secret)
    assert r.status == "FAIL" and ".env.local" in r.detail
    broken = FakeGit({("status", "--porcelain"): (128, "")})
    assert vc.clean_tree_step(broken).status == "FAIL"


class Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def test_a_proxy_environment_does_not_break_the_loopback_checks(vc, monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")              # a dead proxy
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert vc.wait_ready(base + "/", 5) is True
        status, _, text = vc.real_http(base)("GET", "/")
        assert status == 200 and text == "ok"
    finally:
        server.shutdown()
