import json
import re
import string
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..audit import parse_url
from .tasks import TASK_IDS


_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_DEFAULT_PORT = {"http": 80, "https": 443}
MAX_PATTERN_CHARS = 200


def _clean_escapes(text: str) -> str:
    """Decode percent-escapes of unreserved characters; upper-case the hex of every other escape."""
    def repl(m):
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()
    return re.sub(r"%([0-9A-Fa-f]{2})", repl, text)


def normalise_url(url: str) -> str:
    """host[:port] + path + ?query, so that the same page has one key.

    Scheme and fragment are ignored. The host is lower-cased, loses a trailing dot and a default port.
    Percent-escapes of unreserved characters are decoded, `//` is collapsed, `.` and `..` segments are
    resolved, a trailing slash is ignored (the root stays '/') and query parameters are sorted. Path
    parameters (`;v=1`) are kept. Raises ValueError for a URL that cannot be parsed."""
    p = urlsplit(url)
    host = (p.hostname or "").rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    port = p.port                                   # ValueError for an invalid port
    if port is not None and port != _DEFAULT_PORT.get(p.scheme.lower()):
        host = f"{host}:{port}"
    parts: list[str] = []
    for seg in _clean_escapes(p.path).split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    path = "/" + "/".join(parts)
    tokens = sorted(t for t in _clean_escapes(p.query).split("&") if t)
    query = "?" + "&".join(tokens) if tokens else ""
    return f"{host}{path}{query}"


def host_key(url: str) -> str:
    """The normalised host[:port] of a URL."""
    return normalise_url(url).split("/", 1)[0]


def alias_key(s: str) -> str:
    """How form-field names are compared: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass(frozen=True)
class TaskLabel:
    urls: tuple[str, ...] = ()
    none_exists: bool = False
    required_fields: tuple[tuple[str, ...], ...] = ()    # each field: a tuple of accepted names
    quote_pattern: str = ""                               # main_info: the quote must match this regex


@dataclass(frozen=True)
class SiteLabel:
    site: str
    host: str
    tasks: dict


def _urls(task_id: str, raw, origin: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(u, str) for u in raw):
        raise ValueError(f"{task_id}: urls must be a list of strings")
    out = []
    for u in raw:
        try:
            on_host = host_key(u) == host_key(origin)
            key = normalise_url(u)
        except ValueError:
            raise ValueError(f"{task_id}: a label URL could not be parsed")
        if not on_host:
            raise ValueError(f"{task_id}: a label URL is not on {urlsplit(origin).netloc}")
        out.append(key)
    return tuple(out)


def _fields(task_id: str, raw) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{task_id}: required_fields must be a non-empty list")
    out = []
    for f in raw:
        names = [f] if isinstance(f, str) else f
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValueError(f"{task_id}: each required field is a name or a list of accepted names")
        if not all(alias_key(n) for n in names):
            raise ValueError(f"{task_id}: every accepted name needs letters or digits (a name such as '???' matches nothing real)")
        out.append(tuple(n.strip() for n in names))
    return tuple(out)


def _pattern(task_id: str, raw) -> str:
    if raw is None or raw == "":
        return ""
    if task_id != "main_info":
        raise ValueError(f"{task_id}: quote_pattern is only allowed for main_info")
    if not isinstance(raw, str) or len(raw) > MAX_PATTERN_CHARS:
        raise ValueError(f"{task_id}: quote_pattern must be a string of at most {MAX_PATTERN_CHARS} characters")
    try:
        re.compile(raw)
    except re.error:
        raise ValueError(f"{task_id}: quote_pattern is not a valid regular expression")
    return raw


def _task_label(task_id: str, raw, origin: str) -> TaskLabel:
    if not isinstance(raw, dict):
        raise ValueError(f"{task_id}: expected an object")
    pattern = _pattern(task_id, raw["quote_pattern"]) if "quote_pattern" in raw else ""
    none_exists = raw.get("none_exists", False)
    if none_exists and task_id != "docs_api":
        raise ValueError(f"{task_id}: none_exists is only allowed for docs_api")
    if "required_fields" in raw and task_id != "contact_form":
        raise ValueError(f"{task_id}: required_fields is only allowed for contact_form")
    urls = _urls(task_id, raw.get("urls", []), origin)
    if none_exists and urls:
        raise ValueError(f"{task_id}: none_exists cannot be combined with urls")
    if not none_exists and not urls:
        raise ValueError(f"{task_id}: give at least one URL (or none_exists for docs_api)")
    fields = ()
    if task_id == "contact_form":
        if "required_fields" not in raw:
            raise ValueError("contact_form: required_fields is required")
        fields = _fields(task_id, raw["required_fields"])
    return TaskLabel(urls, bool(none_exists), fields, pattern)


def load_label(path, check_name: bool = False) -> SiteLabel:
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
    if check_name and Path(path).stem != re.sub(r"[^a-z0-9._-]", "_", ref.netloc.lower()):
        raise ValueError("The label file name must be the site's host, for example x.test.json "
                         "(a port becomes an underscore: x.test_8443.json)")
    return SiteLabel(ref.origin, ref.netloc, {t: _task_label(t, tasks[t], ref.origin) for t in TASK_IDS})
