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


def verify_ownership(domain, token, fetcher: Fetcher, txt_lookup=default_txt_lookup) -> Ownership:
    expected = f"agentready-verify={token}"
    for txt in txt_lookup(f"_agentready.{domain}"):
        if _same(txt, expected):
            return Ownership(True, "dns")
    home = fetcher.get(f"https://{domain}/")
    if home.status == 200:
        soup = BeautifulSoup(home.body, "html.parser")
        for m in soup.find_all("meta", attrs={"name": "agentready-verification"}):
            if _same(m.get("content", ""), token):
                return Ownership(True, "meta")
    return Ownership(False, "")
