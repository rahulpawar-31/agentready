import ipaddress
import re
from dataclasses import dataclass

from urllib.parse import urlparse

from .checks import discoverability, identity, operability, safety
from .checks.base import Site
from .fetch import Fetcher
from .models import Report, Status
from .scoring import category_scores, is_scored, score

CHECKS = (discoverability.run, operability.run, identity.run, safety.run)

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*\.?$")


@dataclass(frozen=True)
class SiteRef:
    """One normalised view of the site a request is about. Used by every endpoint."""
    scheme: str
    hostname: str          # lower-case, ASCII, no brackets
    port: int | None       # as given; None when absent
    is_ip: bool = False

    @property
    def netloc(self) -> str:
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{host}:{self.port}" if self.port is not None else host

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.netloc}"

    @property
    def dns_name(self) -> str:
        return f"_agentready.{self.hostname}"

    @property
    def dns_allowed(self) -> bool:
        """DNS TXT proves control of a name, not of a service on a port: default ports only."""
        return (not self.is_ip) and self.port in (None, 80, 443)


def parse_url(url: str) -> SiteRef:
    """Validate and normalise a site URL. Raises ValueError with a message safe to show (it never
    echoes the input, which may carry credentials)."""
    try:
        p = urlparse(url)
    except ValueError:
        raise ValueError("Not a valid http(s) URL")
    scheme = p.scheme.lower()
    if scheme not in ("http", "https") or not p.netloc:
        raise ValueError("Not an http(s) URL")
    if "@" in p.netloc:
        raise ValueError("URLs with user information are not supported")
    try:
        port = p.port
    except ValueError:
        raise ValueError("Invalid port")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    host = p.hostname or ""
    if not host:
        raise ValueError("Missing host name")
    if not host.isascii():
        raise ValueError("internationalised host names are not supported")
    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        if len(host) > 253 or not _HOSTNAME.match(host):
            raise ValueError("Invalid host name")
    return SiteRef(scheme, host, port, is_ip)


def origin(url: str) -> str:
    return parse_url(url).origin


def audit(url: str, fetcher: Fetcher) -> Report:
    site = Site(base=origin(url), fetcher=fetcher)
    findings = [f for check in CHECKS for f in check(site)]
    unscored = sum(1 for f in findings if f.status is Status.UNREACHABLE)
    return Report(site.base, score(findings), category_scores(findings), findings, unscored,
                  "scored" if is_scored(findings) else "not_scored")
