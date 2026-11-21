import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .models import Page

REDIRECTS = {301, 302, 303, 307, 308}
MAX_BODY_BYTES = 2_000_000
_V4_NOT_PUBLIC = tuple(ipaddress.ip_network(n) for n in ("192.0.0.0/24", "192.88.99.0/24"))
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPATIBLE = ipaddress.ip_network("::/96")
_GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")


def _normalised_path(url: str) -> str:
    """Lower-cased, percent-decoded path with '.', '..' and empty segments resolved."""
    path = urlparse(url).path
    for _ in range(3):                       # decode repeatedly so %252e cannot hide a dot
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    parts: list[str] = []
    for seg in path.lower().split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


def is_no_store(url: str) -> bool:
    """True for /.env, /.git/HEAD and anything under /.git/ (secrets may be in the body)."""
    path = _normalised_path(url)
    return path == "/.env" or path == "/.git" or path.startswith("/.git/")


def is_public(addr: str) -> bool:
    """Global unicast only: rejects private, loopback, link-local, reserved, multicast,
    unspecified, carrier-grade NAT, NAT64/v4-compatible (the embedded IPv4 is re-tested),
    site-local and anything outside 2000::/3 for IPv6, and anything that is not an IP address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64 or ip in _V4_COMPATIBLE:
            return is_public(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        elif ip not in _GLOBAL_UNICAST_V6:
            return False
    if ip.version == 4 and any(ip in n for n in _V4_NOT_PUBLIC):
        return False
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
    """Plain GET. Never follows redirects: the GuardedFetcher decides whether to. Ignores proxy
    environment variables, so the pinned address is really the one that is connected to."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(follow_redirects=False, timeout=15, trust_env=False)

    @property
    def follows_redirects(self) -> bool:
        return bool(self._client.follow_redirects)

    def get(self, url: str, *, rendered: bool = False, address: str | None = None) -> Page:
        try:
            if address:
                p = urlparse(url)
                ip = f"[{address}]" if ":" in address else address
                netloc = f"{ip}:{p.port}" if p.port else ip
                target = p._replace(netloc=netloc).geturl()
                kwargs = {"headers": {"Host": p.netloc}, "extensions": {"sni_hostname": p.hostname}}
            else:
                target, kwargs = url, {}
            with self._client.stream("GET", target, **kwargs) as r:
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BODY_BYTES:
                        break
                raw = b"".join(chunks)[:MAX_BODY_BYTES]
                status, headers = r.status_code, {k.lower(): v for k, v in r.headers.items()}
                charset = r.charset_encoding or "utf-8"
        except Exception as e:
            return Page(url, 0, {}, "", error=f"request error: {e.__class__.__name__}")
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return Page(url, status, headers, text)


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
            nxt = urljoin(url, location)
            if urlparse(url).scheme == "https" and urlparse(nxt).scheme == "http":
                return self._blocked(nxt, "blocked: https to http downgrade")
            url = nxt
        else:
            return self._blocked(url, f"blocked: more than {self._max} redirects")
        return page


class CachedFetcher:
    """Disk cache over any fetcher. Created lazily, never stores secrets or error pages."""

    def __init__(self, inner: Fetcher, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self.misses = 0

    def _path(self, url: str, rendered: bool) -> Path:
        key = hashlib.sha256(f"{int(rendered)}|{url}".encode()).hexdigest()
        return self._dir / f"{key}.json"

    def _load(self, path: Path) -> Page | None:
        try:
            return Page(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError, KeyError):
            return None                       # missing, half-written or corrupt: treat as a miss

    def _store(self, path: Path, page: Page) -> None:
        tmp = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(asdict(page)))
            os.replace(tmp, path)
            tmp = None
        except OSError:
            pass                              # a failed cache write must never break a fetch
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def get(self, url: str, *, rendered: bool = False) -> Page:
        if is_no_store(url):
            return self._inner.get(url, rendered=rendered)
        path = self._path(url, rendered)
        cached = self._load(path)
        if cached is not None:
            return cached
        page = self._inner.get(url, rendered=rendered)
        self.misses += 1
        cacheable = page.status != 0 and page.status != 429 and page.status < 500
        if cacheable and not is_no_store(page.url):
            self._store(path, page)
        return page
