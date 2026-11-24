import json
from dataclasses import dataclass

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
        did not request, or could not reach (status 0), is simply absent."""
        pages = {}
        for path in BASELINE_PATHS:
            page = recorder.pages.get(origin.rstrip("/") + path)
            if page is not None and page.status != 0:
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
    """GET the five known paths through the audit's fetcher (cache hits after an audit). Only calls `get`."""
    pages = {}
    for path in BASELINE_PATHS:
        p = fetcher.get(origin.rstrip("/") + path)
        pages[path] = (p.status, p.body)
    return Baseline(origin, pages)


class RecordingFetcher:
    """Wraps the audit's fetcher and remembers the Page it returned for each URL. It only calls `inner.get`."""

    def __init__(self, inner):
        self._inner = inner
        self.pages: dict = {}

    def get(self, url: str, *, rendered: bool = False):
        page = self._inner.get(url, rendered=rendered)
        self.pages[url] = page
        return page
