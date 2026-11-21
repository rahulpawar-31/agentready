from urllib.parse import urlparse

from .checks import discoverability, identity, operability, safety
from .checks.base import Site
from .fetch import Fetcher
from .models import Report, Status
from .scoring import category_scores, score

CHECKS = (discoverability.run, operability.run, identity.run, safety.run)


def origin(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"Not an http(s) URL: {url}")
    return f"{p.scheme}://{p.netloc}"


def audit(url: str, fetcher: Fetcher) -> Report:
    site = Site(base=origin(url), fetcher=fetcher)
    findings = [f for check in CHECKS for f in check(site)]
    unscored = sum(1 for f in findings if f.status is Status.UNREACHABLE)
    return Report(site.base, score(findings), category_scores(findings), findings, unscored)
