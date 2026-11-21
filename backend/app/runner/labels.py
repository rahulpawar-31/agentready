import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..audit import parse_url
from .tasks import TASK_IDS


def normalise_url(url: str) -> str:
    """host[:port] + path + ?query. Scheme and fragment are ignored, the host is lower-cased and a
    trailing slash is ignored (the root stays '/')."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    query = f"?{p.query}" if p.query else ""
    return f"{(p.netloc or '').lower()}{path}{query}"


@dataclass(frozen=True)
class TaskLabel:
    urls: tuple[str, ...] = ()
    none_exists: bool = False
    required_fields: tuple[tuple[str, ...], ...] = ()    # each field: a tuple of accepted names


@dataclass(frozen=True)
class SiteLabel:
    site: str
    host: str
    tasks: dict


def _urls(task_id: str, raw, host: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(u, str) for u in raw):
        raise ValueError(f"{task_id}: urls must be a list of strings")
    out = []
    for u in raw:
        if urlparse(u).netloc.lower() != host:
            raise ValueError(f"{task_id}: a label URL is not on {host}")
        out.append(normalise_url(u))
    return tuple(out)


def _fields(task_id: str, raw) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{task_id}: required_fields must be a non-empty list")
    out = []
    for f in raw:
        names = [f] if isinstance(f, str) else f
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValueError(f"{task_id}: each required field is a name or a list of accepted names")
        out.append(tuple(n.strip() for n in names))
    return tuple(out)


def _task_label(task_id: str, raw, host: str) -> TaskLabel:
    if not isinstance(raw, dict):
        raise ValueError(f"{task_id}: expected an object")
    none_exists = raw.get("none_exists", False)
    if none_exists and task_id != "docs_api":
        raise ValueError(f"{task_id}: none_exists is only allowed for docs_api")
    if "required_fields" in raw and task_id != "contact_form":
        raise ValueError(f"{task_id}: required_fields is only allowed for contact_form")
    urls = _urls(task_id, raw.get("urls", []), host)
    if none_exists and urls:
        raise ValueError(f"{task_id}: none_exists cannot be combined with urls")
    if not none_exists and not urls:
        raise ValueError(f"{task_id}: give at least one URL (or none_exists for docs_api)")
    fields = ()
    if task_id == "contact_form":
        if "required_fields" not in raw:
            raise ValueError("contact_form: required_fields is required")
        fields = _fields(task_id, raw["required_fields"])
    return TaskLabel(urls, bool(none_exists), fields)


def load_label(path) -> SiteLabel:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError("The label file could not be read as JSON")
    if not isinstance(data, dict) or not isinstance(data.get("site"), str):
        raise ValueError("The label needs a 'site' URL")
    ref = parse_url(data["site"])                       # ValueError messages never echo the input
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        raise ValueError("The label needs a 'tasks' object")
    unknown = set(tasks) - set(TASK_IDS)
    if unknown:
        raise ValueError(f"Unknown task(s): {', '.join(sorted(unknown))}")
    missing = [t for t in TASK_IDS if t not in tasks]
    if missing:
        raise ValueError(f"Missing task(s): {', '.join(missing)}")
    return SiteLabel(ref.origin, ref.netloc, {t: _task_label(t, tasks[t], ref.netloc) for t in TASK_IDS})
