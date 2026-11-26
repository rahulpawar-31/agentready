#!/usr/bin/env python3
"""Verify that a fresh clone of AgentReady installs, passes its tests and serves its pages.

    python scripts/verify_checkout.py [--source <path-or-url>] [--ref <branch-or-sha>] [--keep] [--skip-node]

Steps (the first failure stops the run and the rest are reported as SKIP):
  1. the commit you are verifying is pushed and the working tree is clean (so the clone contains what you think);
  2. git clone and checkout of the ref into a temporary directory;
  3. a new virtual environment and `pip install -e backend[dev,runner]`;
  4. the Python tests;
  5. the web page's Node tests (WARN, not FAIL, when Node is not installed);
  6. the real server (random secret, no development flag) answers the smoke checks: the page and its security header, the
     ownership-token endpoint, a 405 on a wrong method, and the MCP endpoint (initialize and the three tool names);
  7. the clone is still clean after all that, and no generated or secret file is tracked.

Everything happens under a temporary directory that is removed at the end (--keep keeps it). The working repository is
never modified. The only network use is `git clone` of the chosen source, `git ls-remote` of the remote that is being
compared with, `pip install`, and the ownership-token smoke check's public-host DNS validation (no site HTTP fetch). POSIX only.

Safety of the arguments: the source and ref are refused when they start with '-', contain '::' (git transport helpers
such as ext:: and fd::) or control characters, or (for the ref) anything outside letters, digits and ._/@+-. Git runs
with `--` separators and only the https, ssh and file protocols allowed. Credentials inside a URL are removed from
everything that is printed. The clone must be at the commit being verified (the default: your HEAD, which the remote
must hold, asked with `git ls-remote`, not read from a possibly stale local tracking ref), and the server child must be
alive before and after the smoke checks, so a different process answering on the port cannot make the run pass.
"""
import argparse
import glob
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
GITHUB = re.compile(r"^(https://github\.com/|git@github\.com:)[\w.-]+/[\w.-]+?(\.git)?$")
INITIALIZE_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "verify-checkout", "version": "1"}}
EXPECTED_TOOLS = {"audit_site", "get_report", "propose_fixes"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PROTOCOL_FLAGS = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=user",
                  "-c", "protocol.ssh.allow=always"]
REF_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
CLONE_TIMEOUT = 300
TRACKED_BAD = (re.compile(r"(^|/)__pycache__/"), re.compile(r"(^|/)\.venv/"), re.compile(r"(^|/)\.cache/"),
               re.compile(r"(^|/)\.env(\.(?!example$|sample$|template$)[^/]+)?$"), re.compile(r"\.pem$"),
               re.compile(r"(^|/)id_(rsa|ed25519|ecdsa|dsa)"), re.compile(r"\.egg-info/"),
               re.compile(r"^benchmark/results/.*/\.[^/]*\.tmp$"))        # a leftover temp file from an interrupted write


@dataclass
class StepResult:
    status: str                  # PASS | FAIL | WARN | SKIP
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Step:
    name: str
    fn: Callable[[], StepResult]


# ---- git helpers ---------------------------------------------------------------------------------------------
def make_git(repo: Path):
    def git(*args):
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        return p.returncode, p.stdout.strip()
    return git


def default_source(repo_root: Path, git) -> str:
    """The GitHub origin if there is one (a clone from GitHub proves the pushed commit), else the local path."""
    rc, url = git("remote", "get-url", "origin")
    return url if rc == 0 and GITHUB.match(url) else str(repo_root)


def pushed_problem(git) -> str | None:
    """A reason the clone would not contain what the working tree has, or None. The REMOTE is asked (git ls-remote):
    a local remote-tracking ref can be stale after a force-push."""
    rc, status = git("status", "--porcelain")
    if rc != 0:
        return "git status failed: is this a git repository?"
    if status:
        return "the working tree has uncommitted changes, which a clone cannot contain: commit and push first"
    rc, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    rc2, head = git("rev-parse", "HEAD")
    if rc != 0 or rc2 != 0:
        return "could not read HEAD"
    if branch == "HEAD":
        rc3, containing = git("branch", "-r", "--contains", "HEAD")
        return None if rc3 == 0 and containing else "HEAD is not pushed to any remote branch"
    rc4, line = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if rc4 != 0:
        return ("the branch is not pushed, or the remote 'origin' could not be read (git ls-remote failed): "
                "push it and check the network")
    remote = line.split()[0] if line.strip() else ""
    if not remote:
        return f"the branch is not pushed: origin has no branch {branch} (git push first)"
    if remote != head:
        return f"HEAD is not pushed: origin/{branch} is at {remote[:10]} but HEAD is at {head[:10]}"
    return None


def argument_problem(source: str, ref: str) -> str | None:
    """Refuse a source or ref that git could read as an option or a transport helper."""
    if not isinstance(source, str) or not source.strip():
        return "the source is empty"
    if source.startswith("-"):
        return "the source must not start with '-' (git would read it as an option)"
    if "::" in source:
        return "the source must not use a transport helper such as ext:: or fd::"
    if any(ord(c) < 32 or ord(c) == 127 for c in source):
        return "the source must not contain control characters"
    if not isinstance(ref, str) or not REF_OK.match(ref) or ".." in ref:
        return "the ref may only contain letters, digits and ._/@+- and must not start with '-' or contain '..'"
    return None


def safe_url(text: str) -> str:
    """Remove credentials from any URL inside `text` (user:token@host)."""
    return re.sub(r"(?i)\b(https?|ssh|git)://[^/@\s'\"]+@", r"\1://", str(text))


def expected_sha(git, source: str, ref: str, local_head: str, explicit: bool) -> str | None:
    """The commit the clone must be at, or None when it cannot be known (then nothing is compared)."""
    if not explicit:
        return local_head
    if FULL_SHA.match(ref):
        return ref
    rc, out = git("ls-remote", source, f"refs/heads/{ref}")
    if rc == 0 and out.strip():
        return out.split()[0]
    return None


def commit_problem(cloned: str, expected: str | None) -> str | None:
    if expected is None or cloned == expected:
        return None
    return (f"the clone is at {cloned[:10]} but {expected[:10]} was expected "
            f"(the remote moved on, or the ref resolves to a different commit)")


def clone_commands(source: str, ref: str, dest: Path) -> list[list[str]]:
    return [["git", *PROTOCOL_FLAGS, "clone", "--no-hardlinks", "--", source, str(dest)],
            ["git", "-C", str(dest), "checkout", "--quiet", ref, "--"]]


def tracked_problems(files: list[str]) -> list[str]:
    return [f for f in files if any(p.search(f) for p in TRACKED_BAD)]


# ---- small utilities -----------------------------------------------------------------------------------------
def tail(text: str, lines: int = 20, chars: int = 600) -> str:
    out = "\n".join(text.strip().splitlines()[-lines:])
    return out if len(out) <= chars else out[-chars:]


def pick_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_in_use(port: int) -> bool:
    """True when something already accepts connections on 127.0.0.1:port."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# The loopback checks must never go through a proxy from the environment (a false failure), so every request uses an
# opener without proxy handlers.
def loopback_opener():
    """Built at call time (never at import), so that no proxy setting can reach it."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_ready(url: str, timeout: float, get=None, sleep=time.sleep, clock=time.monotonic) -> bool:
    def default_get(u):
        with loopback_opener().open(u, timeout=3) as r:
            return r.status
    get = get or default_get
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            if get(url) == 200:
                return True
        except (OSError, urllib.error.URLError):
            pass
        sleep(0.5)
    return False


def server_env(base: dict, cache_dir: Path) -> dict:
    env = {k: v for k, v in base.items()
           if k not in ("AGENTREADY_DEV", "AGENTREADY_SECRET", "AGENTREADY_LIVE", "ANTHROPIC_API_KEY")}
    env["AGENTREADY_SECRET"] = secrets.token_hex(32)
    env["AGENTREADY_CACHE"] = str(cache_dir)
    return env


def jsonrpc(method: str, params: dict, ident: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})


def parse_rpc(text: str):
    """A JSON-RPC reply from a plain JSON body or a text/event-stream body; None when there is none."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    found = None
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                value = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(value, dict):
                found = value
    return found


def real_http(base: str):
    def http(method, path, headers=None, body=None):
        req = urllib.request.Request(base + path, method=method, headers=headers or {},
                                     data=body.encode() if isinstance(body, str) else body)
        try:
            with loopback_opener().open(req, timeout=20) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace")
    return http


def smoke(http) -> list[tuple[str, bool, str]]:
    """The checks the real server must pass. `http(method, path, headers=None, body=None) -> (status, headers, text)`."""
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:                                     # noqa: BLE001 - reported, never raised
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:120]}"
        results.append((name, bool(ok), detail))

    def page():
        status, headers, text = http("GET", "/")
        return (status == 200 and "content-security-policy" in headers and "<h1" in text,
                f"status {status}, CSP header {'present' if 'content-security-policy' in headers else 'missing'}")

    def ownership():
        status, _, text = http("GET", "/ownership-token?domain=example.org")
        data = json.loads(text) if status == 200 else {}
        return status == 200 and bool(data.get("token")), f"status {status}"

    def wrong_method():
        status, _, _ = http("GET", "/audit")
        return status == 405, f"status {status}"

    def initialize():
        status, _, text = http("POST", "/mcp", MCP_HEADERS, jsonrpc("initialize", INITIALIZE_PARAMS, 1))
        rpc = parse_rpc(text) or {}
        return status == 200 and "serverInfo" in rpc.get("result", {}), f"status {status}"

    def tools():
        status, _, text = http("POST", "/mcp", MCP_HEADERS, jsonrpc("tools/list", {}, 2))
        rpc = parse_rpc(text) or {}
        names = {t.get("name") for t in rpc.get("result", {}).get("tools", [])}
        return status == 200 and names == EXPECTED_TOOLS, f"status {status}, tools {sorted(n for n in names if n)}"

    check("page is served with its security header", page)
    check("ownership token endpoint answers", ownership)
    check("GET /audit gives 405", wrong_method)
    check("MCP initialize works", initialize)
    check("MCP lists exactly the three tools", tools)
    return results


# ---- the run -------------------------------------------------------------------------------------------------
def run_steps(steps: list[Step], out=print) -> list[tuple[str, StepResult]]:
    rows: list[tuple[str, StepResult]] = []
    failed = False
    for step in steps:
        if failed:
            rows.append((step.name, StepResult("SKIP", "not run: an earlier step failed")))
            continue
        start = time.monotonic()
        try:
            result = step.fn()
        except Exception as exc:                                     # noqa: BLE001
            result = StepResult("FAIL", f"{type(exc).__name__}: {str(exc)[:300]}")
        result.seconds = time.monotonic() - start
        rows.append((step.name, result))
        out(f"[{result.status}] {step.name} ({result.seconds:.1f}s)" + (f": {result.detail}" if result.detail else ""))
        failed = result.status == "FAIL"
    return rows


def exit_code(rows) -> int:
    return 1 if any(r.status == "FAIL" for _, r in rows) else 0


def format_table(rows) -> str:
    lines = ["| Step | Result | Seconds | Detail |", "|---|---|---:|---|"]
    for name, r in rows:
        detail = re.sub(r"\s+", " ", r.detail).strip()[:160].replace("|", "/")
        lines.append(f"| {name} | {r.status} | {r.seconds:.1f} | {detail} |")
    return "\n".join(lines)


def run_cmd(cmd, cwd=None, env=None, timeout=1200) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(map(str, cmd[:4]))}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def cmd_step(cmd, ok_detail: str | None = None, **kw) -> StepResult:
    """Run a command: PASS with `ok_detail` (or the last output line), FAIL with the tail of its output."""
    rc, output = run_cmd(cmd, **kw)
    if rc == 0:
        return StepResult("PASS", ok_detail if ok_detail is not None else tail(output, lines=1, chars=160))
    return StepResult("FAIL", tail(output, lines=25, chars=400))


def server_step(py: Path, backend: Path, cache_dir: Path, out=print, *, popen=subprocess.Popen,
                free_port=pick_free_port, port_in_use=port_in_use, wait=wait_ready, smoke_run=None) -> StepResult:
    """Start the real server on loopback, run the smoke checks, always stop it. The child must be alive before and after
    the checks: a different process answering on the port must not make the run pass."""
    port = free_port()
    if port_in_use(port):
        return StepResult("FAIL", f"something is already listening on 127.0.0.1:{port}; the checks would not be "
                                  "talking to the server under test")
    base = f"http://127.0.0.1:{port}"
    smoke_run = smoke_run or (lambda b: smoke(real_http(b)))
    proc = popen([str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                 cwd=backend, env=server_env(os.environ, cache_dir),
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait(f"{base}/ownership-token?domain=example.org", 60):
            proc.terminate()
            try:
                log = proc.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                log = ""
            return StepResult("FAIL", "the server did not become ready: " + tail(safe_url(log or ""), 10, 400))
        code = proc.poll()
        if code is not None:
            return StepResult("FAIL", f"the server exited (code {code}) before the checks, so another process may be "
                                      f"answering on the port")
        results = smoke_run(base)
        for name, ok, detail in results:
            out(f"    {'ok  ' if ok else 'FAIL'} {name} ({detail})")
        code = proc.poll()
        if code is not None:
            return StepResult("FAIL", f"the server exited (code {code}) during the checks, so the answers may have "
                                      f"come from another process")
        bad = [name for name, ok, _ in results if not ok]
        return StepResult("FAIL", "failed: " + "; ".join(bad)) if bad else StepResult("PASS", f"{len(results)} checks")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def clean_tree_step(git) -> StepResult:
    """`git` is bound to the clone. Untracked files count as dirty (git status --porcelain lists them)."""
    rc, status = git("status", "--porcelain")
    if rc != 0:
        return StepResult("FAIL", "git status failed in the clone")
    if status:
        return StepResult("FAIL", "the run left the clone dirty: " + tail(status, 6, 300))
    rc, listing = git("ls-files")
    bad = tracked_problems(listing.splitlines())
    return StepResult("FAIL", "tracked generated or secret files: " + ", ".join(bad[:5])) if bad else \
        StepResult("PASS", "the clone is clean and no generated or secret file is tracked")


def build_steps(args, tmp: Path, git, out=print) -> list[Step]:
    checkout = tmp / "checkout"
    venv = tmp / "venv"
    py = venv / "bin" / "python"
    backend = checkout / "backend"
    explicit_source = args.source is not None
    source = args.source or default_source(REPO_ROOT, git)
    rc, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    ref = args.ref or (branch if rc == 0 and branch != "HEAD" else git("rev-parse", "HEAD")[1])
    explicit = explicit_source or bool(args.ref)
    pip_timeout = getattr(args, "pip_timeout", 600)
    test_timeout = getattr(args, "test_timeout", 900)

    def check_pushed():
        problem = argument_problem(source, ref)
        if problem:
            return StepResult("FAIL", problem)
        if explicit:
            return StepResult("PASS", f"source {safe_url(source)}, ref {ref}: not checked (given explicitly)")
        problem = pushed_problem(git)
        return StepResult("FAIL", safe_url(problem)) if problem else StepResult("PASS", f"{ref} is pushed and the tree is clean")

    def clone():
        for cmd in clone_commands(source, ref, checkout):
            rc, output = run_cmd(cmd, timeout=CLONE_TIMEOUT)
            if rc != 0:
                return StepResult("FAIL", tail(safe_url(output), 10, 400))
        rc, cloned = make_git(checkout)("rev-parse", "HEAD")
        if rc != 0:
            return StepResult("FAIL", "could not read the commit of the clone")
        local_head = git("rev-parse", "HEAD")[1]
        expected = expected_sha(git, source, ref, local_head, explicit)
        problem = commit_problem(cloned, expected)
        if problem:
            return StepResult("FAIL", problem)
        note = "" if expected is not None else " (not compared: the expected commit could not be determined)"
        return StepResult("PASS", f"{safe_url(source)} @ {ref}, commit {cloned[:10]}{note}")

    def install():
        rc, output = run_cmd([sys.executable, "-m", "venv", str(venv)], timeout=CLONE_TIMEOUT)
        if rc != 0:
            return StepResult("FAIL", tail(output, 10, 400))
        return cmd_step([str(py), "-m", "pip", "install", "-q", "-e", f"{backend}[dev,runner]"],
                        ok_detail="installed backend[dev,runner] into a new virtual environment", timeout=pip_timeout)

    def tests():
        env = {k: v for k, v in os.environ.items() if k not in ("AGENTREADY_LIVE", "ANTHROPIC_API_KEY", "AGENTREADY_SECRET")}
        env["AGENTREADY_DEV"] = "1"
        return cmd_step([str(py), "-m", "pytest", "-q"], cwd=backend, env=env, timeout=test_timeout)

    def node_tests():
        if args.skip_node:
            return StepResult("WARN", "skipped by --skip-node: the UI tests were not run")
        if not shutil.which("node"):
            return StepResult("WARN", "node is not installed: the UI tests were not run")
        files = sorted(glob.glob(str(backend / "tests" / "ui" / "*.test.mjs")))
        if not files:
            return StepResult("FAIL", "no UI test files found in the clone")
        rc, output = run_cmd(["node", "--test", *files], cwd=backend, timeout=CLONE_TIMEOUT)
        summary = [line for line in output.splitlines() if line.startswith(("ℹ tests", "ℹ pass", "ℹ fail"))]
        return StepResult("PASS" if rc == 0 else "FAIL", " ".join(summary) if rc == 0 else tail(output, 25, 400))

    def server():
        return server_step(py, backend, tmp / "cache", out)

    def clean_tree():
        return clean_tree_step(make_git(checkout))

    return [Step("pushed and clean", check_pushed), Step("clone", clone), Step("install", install),
            Step("python tests", tests), Step("ui tests", node_tests), Step("server smoke", server),
            Step("clean tree", clean_tree)]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Verify a fresh clone of AgentReady installs, passes its tests and serves.")
    parser.add_argument("--source", help="path or URL to clone (default: this repository's GitHub origin, else the local path)")
    parser.add_argument("--ref", help="branch or commit to check out (default: the current branch, which must be pushed)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--skip-node", action="store_true", help="do not run the UI tests")
    parser.add_argument("--pip-timeout", type=int, default=600, help="seconds allowed for pip install (default 600)")
    parser.add_argument("--test-timeout", type=int, default=900, help="seconds allowed for the Python tests (default 900)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="agentready-verify-"))
    try:
        print(f"Working in {tmp}")
        rows = run_steps(build_steps(args, tmp, make_git(REPO_ROOT)))
        print()
        print(format_table(rows))
        code = exit_code(rows)
        print("\nResult: " + ("PASS" if code == 0 else "FAIL"))
        return code
    finally:
        if args.keep:
            print(f"Kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
