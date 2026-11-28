import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from ..fetch import REQUEST_LOG_VERSION
from . import judge, llm, tasks, tools
from .agent import RunResult


SNAPSHOT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESERVED_SNAPSHOTS = frozenset({"audits"})      # results/audits/ holds the audit reports, never a task snapshot


def valid_snapshot(name) -> bool:
    """A snapshot name the whole toolchain accepts: the server's pattern (fullmatch: no trailing newline) and not a
    reserved directory name (compared case-insensitively, because macOS and Windows file systems are)."""
    return (isinstance(name, str) and SNAPSHOT_PATTERN.fullmatch(name) is not None
            and name.casefold() not in RESERVED_SNAPSHOTS)


def harness_sha256() -> str:
    """A hash of everything that shapes what the model sees and what counts as a result: the tool
    definitions and limits, the system prompt, the task wording, the judge's quote minimum, the
    step and turn limits and the per-turn token cap. Changing any of them changes the hash, so
    results from different harnesses cannot be mixed up by accident."""
    parts = {
        "toolset_version": tasks.TOOLSET_VERSION,
        "tool_specs": tools.TOOL_SPECS,
        "system_prompt": tasks.system_prompt("HOST", tasks.STEP_LIMIT),
        "tasks": {k: asdict(v) for k, v in sorted(tasks.TASKS.items())},
        "limits": {name: getattr(tools, name) for name in (
            "TEXT_CLIP", "JSONLD_CLIP", "LINK_LIMIT", "FORM_LIMIT", "FIELD_LIMIT", "JSONLD_LIMIT", "URL_CLIP",
            "KIND_CLIP", "RESULT_CAP", "ANSWER_CLIP", "ANSWER_FIELD_LIMIT")},
        "min_quote_chars": judge.MIN_QUOTE_CHARS,
        "judge_version": judge.JUDGE_VERSION,
        "request_log_version": REQUEST_LOG_VERSION,
        "fetch_policy_version": tools.FETCH_POLICY_VERSION,
        "step_limit": tasks.STEP_LIMIT,
        "max_turns": tasks.MAX_TURNS,
        "max_tokens": llm.MAX_TOKENS,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def guard_settings(fetcher) -> dict:
    """The rate limit and request budget of the real guard behind a (possibly wrapped) fetcher.
    Unknown values are None: they are never invented."""
    node, seen = fetcher, 0
    while node is not None and seen < 5:
        if hasattr(node, "_min") and hasattr(node, "_budget"):
            return {"rate_limit_seconds": node._min, "request_budget": node._budget}
        node, seen = getattr(node, "_inner", None), seen + 1
    return {"rate_limit_seconds": None, "request_budget": None}


def config_dict(*, model: str, snapshot: str, criteria_sha256: str, label_sha256: str = "", git_head: str = "",
                preregistered: bool = False, rate_limit_seconds: float | None = None,
                request_budget: int | None = None) -> dict:
    return {"model": model, "snapshot": snapshot, "step_limit": tasks.STEP_LIMIT, "max_turns": tasks.MAX_TURNS,
            "toolset_version": tasks.TOOLSET_VERSION, "criteria_sha256": criteria_sha256,
            "label_sha256": label_sha256, "git_head": git_head, "preregistered": preregistered,
            "harness_sha256": harness_sha256(),
            "rate_limit_seconds": rate_limit_seconds, "request_budget": request_budget}


def _slug(host: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", host.lower())


def site_slug(host: str) -> str:
    """The directory name a site's results are written under (public, so readers can check a file's location)."""
    return _slug(host)


def run_path(root, snapshot: str, host: str, task_id: str, repeat: int) -> Path:
    return Path(root) / snapshot / _slug(host) / f"{task_id}-{repeat}.json"


def save_run(root, snapshot: str, host: str, repeat: int, run: RunResult, config: dict) -> Path:
    """Write one result file atomically (a temp file, then a hard link): a crash leaves either the whole
    file or nothing, and an existing file is never overwritten (FileExistsError)."""
    path = run_path(root, snapshot, host, run.task_id, repeat)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"site": host, "task": run.task_id, "repeat": repeat, "result": asdict(run), "config": config}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "x") as fh:
            fh.write(json.dumps(payload, indent=2))
        os.link(tmp, path)                           # raises FileExistsError when the result already exists
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _is_invalid_file(p: Path) -> bool:
    return ".invalid-" in p.name


def _read(base: Path, p: Path, problems: list | None):
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except (OSError, ValueError, RecursionError):        # RecursionError: a hostile deeply nested file
        if problems is not None:
            problems.append(f"{p.relative_to(base).as_posix()} is not readable")
        return None


def read_run_files(root, snapshot: str, problems: list | None = None) -> list[tuple[Path, dict, bool]]:
    """Every readable result file of a snapshot as (path relative to the snapshot, data, is_invalid_file).
    Symlinks (files, directories or the snapshot itself) are never followed: each is skipped and described in
    `problems`. Unreadable files are skipped and described too. Nothing is written."""
    base = Path(root) / snapshot
    if base.is_symlink():
        if problems is not None:
            problems.append(f"{snapshot} is a symlink and was ignored")
        return []
    if not base.is_dir():
        return []
    out: list[tuple[Path, dict, bool]] = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if p.is_symlink():
            if problems is not None:
                problems.append(f"{rel.as_posix()} is a symlink and was ignored")
            continue
        if p.suffix != ".json" or p.name.startswith("."):
            continue
        data = _read(base, p, problems)
        if data is not None:
            out.append((rel, data, _is_invalid_file(p)))
    return out


def load_runs(root, snapshot: str, problems: list | None = None) -> list[dict]:
    """Every readable result of a snapshot except runs moved aside as invalid. Unreadable files are
    skipped and described in `problems` when a list is given."""
    base = Path(root) / snapshot
    if not base.exists():
        return []
    runs = (_read(base, p, problems) for p in sorted(base.rglob("*.json")) if not _is_invalid_file(p))
    return [r for r in runs if r is not None]


def load_invalid_runs(root, snapshot: str, problems: list[str] | None = None) -> list[dict]:
    """Runs that were invalid and moved aside for a rerun, reported separately from the results."""
    base = Path(root) / snapshot
    if not base.exists():
        return []
    runs = (_read(base, p, problems) for p in sorted(base.rglob("*.json")) if _is_invalid_file(p))
    return [r for r in runs if r is not None]


def is_valid_result(path: Path) -> bool:
    """True when the file exists, is readable and records a valid run."""
    try:
        data = json.loads(Path(path).read_text())
        return bool(data["result"]["valid"])
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def move_aside(path: Path) -> Path:
    """Rename an invalid result to '<name>.invalid-<k>.json' (the first free k). Nothing is deleted."""
    path = Path(path)
    k = 1
    while True:
        target = path.with_name(f"{path.stem}.invalid-{k}.json")
        if not target.exists():
            try:
                os.link(path, target)                # fails if the name was taken in the meantime
            except FileExistsError:
                k += 1
                continue
            path.unlink()
            return target
        k += 1
