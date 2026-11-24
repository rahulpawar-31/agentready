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
never modified. The only network use is `git clone` of the chosen source and `pip install`. POSIX only.
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
TRACKED_BAD = (re.compile(r"(^|/)__pycache__/"), re.compile(r"(^|/)\.venv/"), re.compile(r"(^|/)\.cache/"),
               re.compile(r"(^|/)\.env$"), re.compile(r"\.egg-info/"),
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
    """A reason the clone would not contain what the working tree has, or None."""
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
    rc4, remote = git("rev-parse", f"origin/{branch}")
    if rc4 != 0 or not remote:
        return f"the branch is not pushed: origin/{branch} does not exist locally (git push first)"
    if remote != head:
        return f"HEAD is not pushed: origin/{branch} is at {remote[:10]} but HEAD is at {head[:10]}"
    return None


def clone_commands(source: str, ref: str, dest: Path) -> list[list[str]]:
    return [["git", "clone", "--no-hardlinks", source, str(dest)],
            ["git", "-C", str(dest), "checkout", "--quiet", ref]]


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


def wait_ready(url: str, timeout: float, get=None, sleep=time.sleep, clock=time.monotonic) -> bool:
    def default_get(u):
        with urllib.request.urlopen(u, timeout=3) as r:
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
            with urllib.request.urlopen(req, timeout=20) as r:
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


def build_steps(args, tmp: Path, git, out=print) -> list[Step]:
    checkout = tmp / "checkout"
    venv = tmp / "venv"
    py = venv / "bin" / "python"
    backend = checkout / "backend"
    explicit_source = args.source is not None
    source = args.source or default_source(REPO_ROOT, git)
    rc, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    ref = args.ref or (branch if rc == 0 and branch != "HEAD" else git("rev-parse", "HEAD")[1])

    def check_pushed():
        if explicit_source or args.ref:
            return StepResult("PASS", f"source {source}, ref {ref}: not checked (given explicitly)")
        problem = pushed_problem(git)
        return StepResult("FAIL", problem) if problem else StepResult("PASS", f"{ref} is pushed and the tree is clean")

    def clone():
        for cmd in clone_commands(source, ref, checkout):
            rc, output = run_cmd(cmd)
            if rc != 0:
                return StepResult("FAIL", tail(output, 10, 400))
        return StepResult("PASS", f"{source} @ {ref}")

    def install():
        rc, output = run_cmd([sys.executable, "-m", "venv", str(venv)])
        if rc != 0:
            return StepResult("FAIL", tail(output, 10, 400))
        return cmd_step([str(py), "-m", "pip", "install", "-q", "-e", f"{backend}[dev,runner]"],
                        ok_detail="installed backend[dev,runner] into a new virtual environment", timeout=1800)

    def tests():
        env = {k: v for k, v in os.environ.items() if k not in ("AGENTREADY_LIVE", "ANTHROPIC_API_KEY", "AGENTREADY_SECRET")}
        env["AGENTREADY_DEV"] = "1"
        return cmd_step([str(py), "-m", "pytest", "-q"], cwd=backend, env=env)

    def node_tests():
        if args.skip_node:
            return StepResult("WARN", "skipped by --skip-node: the UI tests were not run")
        if not shutil.which("node"):
            return StepResult("WARN", "node is not installed: the UI tests were not run")
        files = sorted(glob.glob(str(backend / "tests" / "ui" / "*.test.mjs")))
        if not files:
            return StepResult("FAIL", "no UI test files found in the clone")
        rc, output = run_cmd(["node", "--test", *files], cwd=backend)
        summary = [line for line in output.splitlines() if line.startswith(("ℹ tests", "ℹ pass", "ℹ fail"))]
        return StepResult("PASS" if rc == 0 else "FAIL", " ".join(summary) if rc == 0 else tail(output, 25, 400))

    def server():
        port = pick_free_port()
        base = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen([str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=backend, env=server_env(os.environ, tmp / "cache"),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            if not wait_ready(f"{base}/ownership-token?domain=example.org", 60):
                proc.terminate()
                try:
                    log = proc.communicate(timeout=10)[0]
                except subprocess.TimeoutExpired:
                    proc.kill()
                    log = ""
                return StepResult("FAIL", "the server did not become ready: " + tail(log or "", 10, 400))
            results = smoke(real_http(base))
            for name, ok, detail in results:
                out(f"    {'ok  ' if ok else 'FAIL'} {name} ({detail})")
            bad = [name for name, ok, _ in results if not ok]
            return StepResult("FAIL", "failed: " + "; ".join(bad)) if bad else StepResult("PASS", f"{len(results)} checks")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def clean_tree():
        rc, status = make_git(checkout)("status", "--porcelain")
        if rc != 0:
            return StepResult("FAIL", "git status failed in the clone")
        if status:
            return StepResult("FAIL", "the run left the clone dirty: " + tail(status, 6, 300))
        rc, listing = make_git(checkout)("ls-files")
        bad = tracked_problems(listing.splitlines())
        return StepResult("FAIL", "tracked generated or secret files: " + ", ".join(bad[:5])) if bad else \
            StepResult("PASS", "the clone is clean and no generated or secret file is tracked")

    return [Step("pushed and clean", check_pushed), Step("clone", clone), Step("install", install),
            Step("python tests", tests), Step("ui tests", node_tests), Step("server smoke", server),
            Step("clean tree", clean_tree)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify a fresh clone of AgentReady installs, passes its tests and serves.")
    parser.add_argument("--source", help="path or URL to clone (default: this repository's GitHub origin, else the local path)")
    parser.add_argument("--ref", help="branch or commit to check out (default: the current branch, which must be pushed)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--skip-node", action="store_true", help="do not run the UI tests")
    args = parser.parse_args(argv)
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
