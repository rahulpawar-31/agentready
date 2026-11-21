import re
from dataclasses import dataclass
from urllib.parse import urljoin

from .labels import TaskLabel, alias_key, fetch_key, host_key, normalise_url

MIN_QUOTE_CHARS = 8


@dataclass(frozen=True)
class Answer:
    url: str | None = None
    quote: str | None = None
    fields: tuple[str, ...] = ()
    none_exists: bool = False
    gave_up: str | None = None          # "blocked" | "not_found" | None


@dataclass(frozen=True)
class Verdict:
    success: bool
    reason: str


def _squash(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


_key = alias_key
_SEP = " \x00 "


def _shown(value) -> str:
    """Everything the agent was shown for one URL (a string, or a list of strings), squashed."""
    if isinstance(value, (list, tuple)):
        return _squash(_SEP.join(value))
    return _squash(value)


def judge(task_id: str, answer: Answer | None, label: TaskLabel, origin: str, seen: dict,
          visited=frozenset(), looked: bool = False) -> Verdict:
    """Pure. `seen` maps a normalised URL to the text the agent was shown for it; `visited` is the set
    of normalised URLs the tools actually returned; `looked` says that at least one request got a
    response. A page the agent never fetched cannot be the answer."""
    if answer is None:
        return Verdict(False, "no_answer")
    if answer.gave_up == "blocked":
        return Verdict(False, "blocked")
    none_claim = answer.none_exists or (answer.gave_up == "not_found" and task_id == "docs_api" and label.none_exists)
    if none_claim:
        ok = task_id == "docs_api" and label.none_exists and not answer.url
        if ok and not (looked or visited):
            return Verdict(False, "not_investigated")
        return Verdict(ok, "ok" if ok else "wrong_none_claim")
    if answer.gave_up or not answer.url:
        return Verdict(False, "no_answer")
    try:
        absolute = urljoin(origin + "/", answer.url.strip())
        if host_key(absolute) != host_key(origin):
            return Verdict(False, "off_host")
        key = normalise_url(absolute)
    except ValueError:
        return Verdict(False, "wrong_page")
    if key not in label.urls:
        return Verdict(False, "wrong_page")
    try:
        fetched_key = fetch_key(absolute)
    except ValueError:
        return Verdict(False, "wrong_page")
    if fetched_key not in visited:
        return Verdict(False, "not_visited")
    if task_id == "main_info":
        quote = _squash(answer.quote)
        if len(quote.replace(" ", "")) < MIN_QUOTE_CHARS:
            return Verdict(False, "no_quote")
        if quote not in _shown(seen.get(key)):
            return Verdict(False, "quote_not_on_page")
        if label.quote_pattern and not re.search(label.quote_pattern, quote, re.IGNORECASE):
            return Verdict(False, "quote_pattern_mismatch")
    elif task_id == "contact_form":
        given = {_key(f) for f in answer.fields}
        if not all(any(_key(alias) in given for alias in field) for field in label.required_fields):
            return Verdict(False, "missing_fields")
    return Verdict(True, "ok")
