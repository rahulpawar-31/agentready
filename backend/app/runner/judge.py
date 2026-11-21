import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from .labels import TaskLabel, normalise_url

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


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def judge(task_id: str, answer: Answer | None, label: TaskLabel, origin: str, seen: dict) -> Verdict:
    if answer is None:
        return Verdict(False, "no_answer")
    if answer.gave_up == "blocked":
        return Verdict(False, "blocked")
    if answer.none_exists:
        ok = task_id == "docs_api" and label.none_exists and not answer.url
        return Verdict(ok, "ok" if ok else "wrong_none_claim")
    if answer.gave_up or not answer.url:
        return Verdict(False, "no_answer")
    absolute = urljoin(origin + "/", answer.url)
    host = urlparse(origin).netloc.lower()
    if urlparse(absolute).netloc.lower() != host:
        return Verdict(False, "off_host")
    key = normalise_url(absolute)
    if key not in label.urls:
        return Verdict(False, "wrong_page")
    if task_id == "main_info":
        quote = _squash(answer.quote)
        if len(quote.replace(" ", "")) < MIN_QUOTE_CHARS:
            return Verdict(False, "no_quote")
        if quote not in _squash(seen.get(key)):
            return Verdict(False, "quote_not_on_page")
    elif task_id == "contact_form":
        given = {_key(f) for f in answer.fields}
        if not all(any(_key(alias) in given for alias in field) for field in label.required_fields):
            return Verdict(False, "missing_fields")
    return Verdict(True, "ok")
