import re
from dataclasses import dataclass

from ..fetch import Fetcher
from ..models import Category, Confidence, Finding, Page, Status


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clip(text, limit: int = 80) -> str:
    """Site-controlled strings go into evidence only after control characters are replaced and the
    length is capped."""
    return _CONTROL.sub(" ", str(text))[:limit]


@dataclass
class Site:
    base: str
    fetcher: Fetcher

    def url(self, path: str) -> str:
        return self.base.rstrip("/") + path

    def get(self, path: str, rendered: bool = False) -> Page:
        return self.fetcher.get(self.url(path), rendered=rendered)


def finding(check_id: str, category: Category, page: Page, *, passed: bool, weight: int,
            check: str, evidence: str, status: Status, confidence: Confidence) -> Finding:
    """Build a Finding. A page that could not be fetched, or answered with a server error, is reported
    as unreachable, never as a result."""
    if page.status == 0:
        reason = page.error or "request failed"
        return Finding(check_id, category, False, weight, check, f"Could not fetch: {reason}",
                       page.url, Confidence.LOW, Status.UNREACHABLE)
    if page.status >= 500:
        return Finding(check_id, category, False, weight, check, f"Server error {page.status}",
                       page.url, Confidence.LOW, Status.UNREACHABLE)
    return Finding(check_id, category, passed, weight, check, evidence, page.url, confidence, status)
