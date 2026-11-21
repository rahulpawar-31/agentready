import os
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .audit import audit, origin
from .fetch import CachedFetcher, GuardedFetcher, HttpxFetcher
from .models import Report
from .ownership import default_txt_lookup, make_token, verify_ownership

SECRET = os.environ.get("AGENTREADY_SECRET", "dev-secret")
CACHE_DIR = Path(os.environ.get("AGENTREADY_CACHE", ".cache"))

app = FastAPI(title="AgentReady")
_reports: dict[str, Report] = {}


def build_fetchers(host: str, snapshot: str = "default"):
    """Both fetchers are locked to the one verified host. Use a different `snapshot` label for the
    before and after benchmark runs so the after run never reuses cached before pages."""
    plain = GuardedFetcher(HttpxFetcher(), host)
    guarded = GuardedFetcher(HttpxFetcher(), host)
    return plain, CachedFetcher(guarded, CACHE_DIR / host.replace(":", "_") / snapshot)


def get_txt_lookup():
    return default_txt_lookup


class AuditRequest(BaseModel):
    url: str


def _instructions(domain: str) -> dict:
    token = make_token(domain, SECRET)
    return {
        "domain": domain,
        "token": token,
        "meta_tag": f'<meta name="agentready-verification" content="{token}">',
        "dns_txt_name": f"_agentready.{domain}",
        "dns_txt_value": f"agentready-verify={token}",
    }


@app.get("/ownership-token")
def ownership_token(domain: str):
    return _instructions(domain)


@app.post("/audit")
def post_audit(req: AuditRequest, txt=Depends(get_txt_lookup)):
    try:
        base = origin(req.url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    host = urlparse(base).netloc
    plain, audit_fetcher = build_fetchers(host)
    owner = verify_ownership(host, make_token(host, SECRET), plain, txt)
    if not owner.verified:
        raise HTTPException(403, _instructions(host))
    report = audit(base, audit_fetcher)
    rid = uuid4().hex
    _reports[rid] = report
    return {"id": rid, **asdict(report)}


@app.get("/report/{rid}")
def get_report(rid: str):
    if rid not in _reports:
        raise HTTPException(404, "Unknown report")
    return asdict(_reports[rid])
