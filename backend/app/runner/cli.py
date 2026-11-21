import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..audit import SiteRef, parse_url
from ..fetch import is_public
from .agent import run_task
from .labels import load_label
from .llm import DEFAULT_MODEL, AnthropicClient, LLMClient
from .results import (config_dict, guard_settings, harness_sha256, is_valid_result, move_aside, run_path,
                      save_run)
from .tasks import MAX_TURNS, STEP_LIMIT, TASK_IDS, TASKS, TOOLSET_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_REPEATS = 10
AUTH_FAILURES = ("AuthenticationError", "PermissionDeniedError", "Error code: 401", "Error code: 403")


@dataclass
class Deps:
    verify: Callable[[SiteRef], bool]
    resolver: Callable[[str], list]
    make_fetcher: Callable[[str, str], object]       # (netloc, snapshot) -> a NEW guarded, cached fetcher
    make_llm: Callable[[], LLMClient]
    committed: Callable[[list], list]                # paths -> problems
    model: str
    git_head: Callable[[], str] = lambda: ""         # the commit the criteria and label were checked against


def _git(repo_root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--literal-pathspecs", "-C", str(repo_root), *args], capture_output=True, text=True)


def git_head(repo_root=REPO_ROOT) -> str:
    out = _git(repo_root, "rev-parse", "HEAD")
    return out.stdout.strip() if out.returncode == 0 else ""


def git_problems(paths, repo_root=REPO_ROOT) -> list[str]:
    """A file counts as committed only when its content, as it is on disk now, is byte for byte the
    blob in HEAD. That also catches staged-only changes, `assume-unchanged` edits and symlinks (git
    stores the link, the runner would read its target)."""
    problems = []
    for p in paths:
        rel = os.path.relpath(p, repo_root)
        name = f"{Path(p).name} (outside the repository)" if rel.startswith("..") else rel
        staged = _git(repo_root, "ls-files", "-s", "--", rel)
        entry = staged.stdout.split() if staged.returncode == 0 else []
        if not entry:
            problems.append(f"{name} is not committed")
            continue
        if entry[0] == "120000" or os.path.islink(p):
            problems.append(f"{name} is a symlink, which cannot be pre-registered")
            continue
        head = _git(repo_root, "rev-parse", f"HEAD:{rel}")
        if head.returncode != 0:
            problems.append(f"{name} is not committed")
            continue
        disk = _git(repo_root, "hash-object", "--", str(p))
        if disk.returncode != 0 or disk.stdout.strip() != head.stdout.strip():
            problems.append(f"{name} has uncommitted changes")
    return problems


def load_criteria(path) -> tuple[dict, str]:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        raise ValueError("The criteria file could not be read as JSON")
    for key, expected in (("toolset_version", TOOLSET_VERSION), ("step_limit", STEP_LIMIT), ("max_turns", MAX_TURNS),
                          ("harness_sha256", harness_sha256())):
        if not isinstance(data, dict) or data.get(key) != expected:
            raise ValueError(f"The criteria file's {key} does not match the code ({expected!r})")
    return data, hashlib.sha256(raw).hexdigest()


def default_deps() -> Deps:
    from .. import main as api                      # raises unless AGENTREADY_SECRET (or AGENTREADY_DEV=1) is set
    from ..fetch import default_resolver
    from ..ownership import default_txt_lookup, make_token, verify_ownership

    def verify(ref: SiteRef) -> bool:
        probe = api.build_probe_fetcher(ref.netloc, resolver=default_resolver)
        owner = verify_ownership(ref.netloc, make_token(ref.netloc, api.SECRET), probe, default_txt_lookup,
                                 dns_name=ref.dns_name, allow_dns=ref.dns_allowed)
        return owner.verified

    return Deps(verify=verify, resolver=default_resolver,
                make_fetcher=lambda netloc, snapshot: api.build_audit_fetcher(netloc, snapshot),
                make_llm=AnthropicClient, committed=git_problems, git_head=git_head,
                model=os.environ.get("AGENTREADY_MODEL", DEFAULT_MODEL))


def main(argv, deps: Deps | None = None, out=print, repo_root=REPO_ROOT) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.runner.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--snapshot", required=True)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--tasks", default=",".join(TASK_IDS))
    r.add_argument("--criteria", default=str(Path(repo_root) / "benchmark" / "criteria.json"))
    r.add_argument("--results", default=str(Path(repo_root) / "benchmark" / "results"))
    r.add_argument("--allow-uncommitted", action="store_true")
    args = ap.parse_args(argv)

    if not SNAPSHOT.match(args.snapshot):
        out("Refused: the snapshot name must match [A-Za-z0-9_-]{1,64}.")
        return 2
    tasks = list(dict.fromkeys(t for t in args.tasks.split(",") if t))        # no duplicates, order kept
    if not tasks or any(t not in TASKS for t in tasks):
        out("Refused: unknown task.")
        return 2
    if not 1 <= args.repeats <= MAX_REPEATS:
        out(f"Refused: repeats must be between 1 and {MAX_REPEATS}.")
        return 2
    try:
        _, sha = load_criteria(args.criteria)
        label = load_label(args.label, check_name=True)
    except ValueError as e:
        out(f"Refused: {e}")
        return 2
    deps = deps or default_deps()
    if not args.allow_uncommitted:
        problems = deps.committed([Path(args.criteria), Path(args.label)])
        if problems:
            out("Refused: the criteria and the label must be committed before any run (" + "; ".join(problems) + ").")
            return 2
    ref = parse_url(label.site)
    addrs = deps.resolver(ref.hostname)
    if not addrs or not all(is_public(a) for a in addrs):
        out("Refused: the host does not resolve to a public address, so it cannot be audited.")
        return 2
    planned = [(rep, t) for rep in range(1, args.repeats + 1) for t in tasks]
    todo = [(rep, t) for rep, t in planned if not is_valid_result(run_path(args.results, args.snapshot, ref.netloc, t, rep))]
    if not todo:
        out("Refused: every planned run already has a valid result in this snapshot. Results are never overwritten; "
            "use a new snapshot name for a new measurement.")
        return 2
    if not deps.verify(ref):
        out("Refused: control of the host is not proven. Add the ownership token first (see GET /ownership-token).")
        return 2
    try:
        llm = deps.make_llm()
    except Exception as e:
        out(f"Refused: the model client could not be created ({type(e).__name__}). Install the runner extra and set the key.")
        return 2

    kept = len(planned) - len(todo)
    if kept:
        out(f"Keeping {kept} valid result(s); rerunning {len(todo)}.")
    label_sha = hashlib.sha256(Path(args.label).read_bytes()).hexdigest()
    head = deps.git_head()
    prereg = not args.allow_uncommitted
    invalid = 0
    for rep, task_id in todo:
        existing = run_path(args.results, args.snapshot, ref.netloc, task_id, rep)
        if existing.exists():                                       # an invalid or unreadable earlier run
            out(f"Moved the invalid result aside: {move_aside(existing).name}")
        fetcher = deps.make_fetcher(ref.netloc, args.snapshot)       # a new guard instance and budget per run
        config = config_dict(model=deps.model, snapshot=args.snapshot, criteria_sha256=sha, label_sha256=label_sha,
                             git_head=head, preregistered=prereg, **guard_settings(fetcher))
        run = run_task(task=TASKS[task_id], label=label.tasks[task_id], origin=ref.origin, fetcher=fetcher, llm=llm)
        save_run(args.results, args.snapshot, ref.netloc, rep, run, config)
        invalid += 0 if run.valid else 1
        out(f"{ref.netloc} {task_id} #{rep}: {'pass' if run.success else 'FAIL'} ({run.reason}), "
            f"{run.steps_used} steps, {run.turns} turns" + ("" if run.valid else "  [INVALID: rerun]"))
        if run.reason == "llm_error" and any(m in run.error for m in AUTH_FAILURES):
            out("Stopping: the model API rejected the credentials (authentication failure). Check the key, then rerun "
                "in this snapshot; the remaining runs were not started.")
            break
    return 3 if invalid else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
