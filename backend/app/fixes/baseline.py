import json
from dataclasses import dataclass

SPEC_PATHS = ("/openapi.json", "/swagger.json", "/.well-known/openapi.json")
BASELINE_PATHS = ("/", "/llms.txt") + SPEC_PATHS


@dataclass(frozen=True)
class Baseline:
    """What the audit's snapshot saw: path -> (status, body). Fix generation reads only this."""
    origin: str
    pages: dict

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
            except ValueError:
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
