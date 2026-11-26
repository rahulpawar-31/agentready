import hashlib
import hmac
from dataclasses import dataclass

import dns.resolver
from bs4 import BeautifulSoup

from .fetch import Fetcher


@dataclass(frozen=True)
class Ownership:
    verified: bool
    method: str


def make_token(domain: str, secret: str) -> str:
    return hmac.new(secret.encode(), domain.lower().encode(), hashlib.sha256).hexdigest()[:32]


def default_txt_lookup(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT")
    except Exception:
        return []
    return [b"".join(r.strings).decode(errors="replace") for r in answers]


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_ownership(domain, token, fetcher: Fetcher, txt_lookup=default_txt_lookup, *,
                     dns_name: str | None = None, allow_dns: bool = True, scheme: str = "https") -> Ownership:
    """`domain` is host[:port] (used for the root-page probe). `dns_name` is the TXT name to query
    (hostname only); `allow_dns` is False for non-default ports, where only the meta tag counts.
    `scheme` is the requested site's validated http(s) scheme."""
    expected = f"agentready-verify={token}"
    if allow_dns:
        for txt in txt_lookup(dns_name or f"_agentready.{domain}"):
            if _same(txt.strip(), expected):
                return Ownership(True, "dns")
    home = fetcher.get(f"{scheme}://{domain}/")
    if home.status == 200:
        soup = BeautifulSoup(home.body, "html.parser")
        for m in soup.find_all("meta", attrs={"name": "agentready-verification"}):
            if m.parent is not None and m.parent.name == "head" and _same(str(m.get("content", "")).strip(), token):
                return Ownership(True, "meta")
    return Ownership(False, "")
