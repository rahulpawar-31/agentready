import json
from dataclasses import dataclass

from ..fetch import is_no_store

SPEC_PATHS = ("/openapi.json", "/swagger.json", "/.well-known/openapi.json")
BASELINE_PATHS = ("/", "/llms.txt") + SPEC_PATHS


@dataclass(frozen=True)
class Baseline:
    """What the audit's snapshot saw: path -> (status, body). Fix generation reads only this."""
    origin: str
    pages: dict
    available: bool = True          # False when the baseline could not be built: no fix is proposed

    @classmethod
    def unavailable(cls, origin: str) -> "Baseline":
        return cls(origin, {}, available=False)

    @classmethod
    def from_recorder(cls, origin: str, recorder: "RecordingFetcher") -> "Baseline":
        """The pages the audit itself read, as the audit saw them. Nothing is requested again: a path the audit
        did not request, could not reach (status 0), or resolved to a sensitive path is absent."""
        pages = {}
        for path in BASELINE_PATHS:
            page = recorder.pages.get(origin.rstrip("/") + path)
            if page is not None and page.status != 0 and not is_no_store(page.url):
                pages[path] = (page.status, page.body)
        return cls(origin, pages)

    def body(self, path: str):
        status, body = self.pages.get(path, (0, ""))
        return body if status == 200 else None

    def spec(self):
        for path in SPEC_PATHS:
            body = self.body(path)
            if body is None:
                continue
            try:
                data = json.loads(body)
            except (ValueError, RecursionError):        # a document nested too deeply to parse is not a usable spec
                continue
            if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
                return path, data
        return None


def capture_baseline(origin: str, fetcher) -> Baseline:
    """GET the five known paths through the audit's fetcher, excluding sensitive final responses from the baseline."""
    pages = {}
    for path in BASELINE_PATHS:
        p = fetcher.get(origin.rstrip("/") + path)
        if not is_no_store(p.url):
            pages[path] = (p.status, p.body)
    return Baseline(origin, pages)


class RecordingFetcher:
    """Return pages to audit checks, retaining only non-sensitive responses for fix generation."""

    def __init__(self, inner):
        self._inner = inner
        self.pages: dict = {}

    def get(self, url: str, *, rendered: bool = False):
        page = self._inner.get(url, rendered=rendered)
        if is_no_store(url) or is_no_store(page.url):
            self.pages.pop(url, None)
        else:
            self.pages[url] = page
        return page
