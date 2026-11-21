import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .audit import SiteRef, audit, parse_url
from .fetch import CachedFetcher, GuardedFetcher, HttpxFetcher, default_resolver, is_public
from .models import Report
from .ownership import default_txt_lookup, make_token, verify_ownership

# Fail closed: a guessable default secret would make every ownership token predictable.
SECRET = os.environ.get("AGENTREADY_SECRET")
if not SECRET:
    if os.environ.get("AGENTREADY_DEV") == "1":
        SECRET = "dev-secret"
    else:
        raise RuntimeError("Set AGENTREADY_SECRET (or AGENTREADY_DEV=1 for local development only)")

CACHE_DIR = Path(os.environ.get("AGENTREADY_CACHE", ".cache"))
GUARD_OPTIONS: dict = {}          # tests override this (for example to skip the rate-limit sleep)
MAX_REPORTS = 200
SNAPSHOT_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_SNAPSHOT = re.compile(SNAPSHOT_PATTERN)

app = FastAPI(title="AgentReady")
_reports: "OrderedDict[str, Report]" = OrderedDict()


def new_http_client():
    """Hook for tests: return an httpx.Client with a mock transport. None means the default client."""
    return None


def validate_snapshot(snapshot: str) -> str:
    if not isinstance(snapshot, str) or not _SNAPSHOT.match(snapshot):
        raise ValueError("snapshot must match [A-Za-z0-9_-]{1,64}")
    return snapshot


def cache_dir_for(host: str, snapshot: str) -> Path:
    """Directory names come from a hash of the validated host[:port] and a validated snapshot label,
    never from raw input, so they cannot traverse or collide."""
    validate_snapshot(snapshot)
    return CACHE_DIR / hashlib.sha256(host.lower().encode()).hexdigest()[:16] / snapshot


def build_probe_fetcher(host: str, resolver=default_resolver):
    """Guard for the ownership probe only. Creates no files."""
    return GuardedFetcher(HttpxFetcher(new_http_client()), host, resolver=resolver, **GUARD_OPTIONS)


def build_audit_fetcher(host: str, snapshot: str = "default", resolver=default_resolver):
    """Guard plus disk cache for the audit. Call only after control of the host is proven. Use a
    different `snapshot` for the before and after benchmark runs."""
    cache_dir = cache_dir_for(host, snapshot)
    guarded = GuardedFetcher(HttpxFetcher(new_http_client()), host, resolver=resolver, **GUARD_OPTIONS)
    return CachedFetcher(guarded, cache_dir)


def get_txt_lookup():
    return default_txt_lookup


def get_resolver():
    return default_resolver


class AuditRequest(BaseModel):
    url: str
    snapshot: str = Field(default="default", pattern=SNAPSHOT_PATTERN)


def _instructions(ref: SiteRef) -> dict:
    token = make_token(ref.netloc, SECRET)
    out = {
        "domain": ref.netloc,
        "token": token,
        "meta_tag": f'<meta name="agentready-verification" content="{token}">',
        "meta_tag_placement": "inside <head> on the home page",
    }
    if ref.dns_allowed:
        out["dns_txt_name"] = ref.dns_name
        out["dns_txt_value"] = f"agentready-verify={token}"
    else:
        out["dns_txt_name"] = None
        out["dns_txt_value"] = None
        out["dns_note"] = "DNS TXT verification is only available for sites on a default port; use the meta tag."
    return out


def _parse(url: str) -> SiteRef:
    try:
        return parse_url(url)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/ownership-token")
def ownership_token(domain: str):
    return _instructions(_parse(f"https://{domain}"))


@app.post("/audit")
def post_audit(req: AuditRequest, txt=Depends(get_txt_lookup), resolver=Depends(get_resolver)):
    ref = _parse(req.url)
    addrs = resolver(ref.hostname)
    if not addrs or not all(is_public(a) for a in addrs):
        raise HTTPException(422, "host does not resolve to a public address and cannot be audited")
    probe = build_probe_fetcher(ref.netloc, resolver=resolver)
    owner = verify_ownership(ref.netloc, make_token(ref.netloc, SECRET), probe, txt,
                             dns_name=ref.dns_name, allow_dns=ref.dns_allowed)
    if not owner.verified:
        raise HTTPException(403, _instructions(ref))
    fetcher = build_audit_fetcher(ref.netloc, req.snapshot, resolver=resolver)   # the cache is created only now
    report = audit(ref.origin, fetcher)
    rid = uuid4().hex
    _reports[rid] = report
    while len(_reports) > MAX_REPORTS:
        _reports.popitem(last=False)
    return {"id": rid, **asdict(report)}


@app.get("/report/{rid}")
def get_report(rid: str):
    if rid not in _reports:
        raise HTTPException(404, "Unknown report")
    return asdict(_reports[rid])
