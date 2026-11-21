import hashlib
import ipaddress
import json
import socket
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from .models import Page

REDIRECTS = {301, 302, 303, 307, 308}
NO_STORE = ("/.env", "/.git/HEAD")   # may contain secrets: never cache


def is_public(addr: str) -> bool:
    """Global unicast only: rejects private, loopback, link-local, reserved, multicast,
    unspecified and carrier-grade NAT addresses, and anything that is not an IP address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def address_key(addr: str) -> tuple[int, int]:
    """Canonical order for pinning: IPv4 before IPv6, then numeric value (so .9 sorts before .100)."""
    ip = ipaddress.ip_address(addr)
    return (ip.version, int(ip))


def default_resolver(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


class Fetcher(Protocol):
    def get(self, url: str, *, rendered: bool = False, address: str | None = None) -> Page: ...


class HttpxFetcher:
    """Plain GET. Never follows redirects: the GuardedFetcher decides whether to."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(follow_redirects=False, timeout=15)

    @property
    def follows_redirects(self) -> bool:
        return bool(self._client.follow_redirects)

    def get(self, url: str, *, rendered: bool = False, address: str | None = None) -> Page:
        try:
            if address:
                p = urlparse(url)
                ip = f"[{address}]" if ":" in address else address
                netloc = f"{ip}:{p.port}" if p.port else ip
                r = self._client.get(p._replace(netloc=netloc).geturl(),
                                     headers={"Host": p.netloc},
                                     extensions={"sni_hostname": p.hostname})
            else:
                r = self._client.get(url)
        except httpx.RequestError as e:
            return Page(url, 0, {}, "", error=f"network error: {e.__class__.__name__}")
        headers = {k.lower(): v for k, v in r.headers.items()}
        return Page(url, r.status_code, headers, r.text)


class GuardedFetcher:
    """Verified-host-only, redirect-checked, rate-limited, read-only fetching."""

    def __init__(self, plain: Fetcher, host: str, *, resolver=default_resolver,
                 min_interval: float = 0.5, max_redirects: int = 5, max_requests: int = 60,
                 clock=time.monotonic, sleep=time.sleep):
        if getattr(plain, "follows_redirects", False):
            raise ValueError("the plain fetcher must not follow redirects on its own")
        self._plain = plain
        self._resolve = resolver
        self.host = host.lower()
        self._min, self._max = min_interval, max_redirects
        self._budget, self._used = max_requests, 0
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None

    @property
    def requests_used(self) -> int:
        return self._used

    def _inside(self, url: str) -> bool:
        p = urlparse(url)
        return (p.scheme in ("http", "https") and p.username is None and p.password is None
                and p.netloc.lower() == self.host)

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    @staticmethod
    def _blocked(url: str, why: str) -> Page:
        return Page(url, 0, {}, "", error=why)

    def get(self, url: str, *, rendered: bool = False) -> Page:
        for _ in range(self._max + 1):
            if not self._inside(url):
                return self._blocked(url, f"blocked: {url} is outside the verified site {self.host}")
            if self._used >= self._budget:
                return self._blocked(url, f"blocked: request budget of {self._budget} used up")
            addrs = self._resolve(urlparse(url).hostname or "")
            if not addrs:
                return self._blocked(url, f"blocked: could not resolve {self.host}")
            if not all(is_public(a) for a in addrs):
                return self._blocked(url, f"blocked: {self.host} resolves to a non-public address")
            self._used += 1
            self._throttle()
            page = self._plain.get(url, address=min(addrs, key=address_key))
            if page.status not in REDIRECTS:
                break
            location = page.headers.get("location")
            if not location:
                return self._blocked(url, "blocked: redirect without a Location header")
            url = urljoin(url, location)
        else:
            return self._blocked(url, f"blocked: more than {self._max} redirects")
        return page


class CachedFetcher:
    def __init__(self, inner: Fetcher, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.misses = 0

    def _path(self, url: str, rendered: bool) -> Path:
        key = hashlib.sha256(f"{int(rendered)}|{url}".encode()).hexdigest()
        return self._dir / f"{key}.json"

    def get(self, url: str, *, rendered: bool = False) -> Page:
        if any(s in urlparse(url).path for s in NO_STORE):
            return self._inner.get(url, rendered=rendered)
        p = self._path(url, rendered)
        if p.exists():
            return Page(**json.loads(p.read_text()))
        page = self._inner.get(url, rendered=rendered)
        self.misses += 1
        p.write_text(json.dumps(asdict(page)))
        return page
