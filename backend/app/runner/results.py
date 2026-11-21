import json
import re
from dataclasses import asdict
from pathlib import Path

from .agent import RunResult
from .tasks import MAX_TURNS, STEP_LIMIT, TOOLSET_VERSION


def config_dict(*, model: str, snapshot: str, criteria_sha256: str) -> dict:
    return {"model": model, "snapshot": snapshot, "step_limit": STEP_LIMIT, "max_turns": MAX_TURNS,
            "toolset_version": TOOLSET_VERSION, "criteria_sha256": criteria_sha256,
            "rate_limit_seconds": 0.5, "request_budget": 60}


def _slug(host: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", host.lower())


def run_path(root, snapshot: str, host: str, task_id: str, repeat: int) -> Path:
    return Path(root) / snapshot / _slug(host) / f"{task_id}-{repeat}.json"


def save_run(root, snapshot: str, host: str, repeat: int, run: RunResult, config: dict) -> Path:
    path = run_path(root, snapshot, host, run.task_id, repeat)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"site": host, "task": run.task_id, "repeat": repeat, "result": asdict(run), "config": config}
    with open(path, "x") as fh:                      # "x": refuse to overwrite an existing run
        fh.write(json.dumps(payload, indent=2))
    return path


def load_runs(root, snapshot: str) -> list[dict]:
    base = Path(root) / snapshot
    return [json.loads(p.read_text()) for p in sorted(base.rglob("*.json"))] if base.exists() else []
