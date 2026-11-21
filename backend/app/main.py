import hashlib
import os
import re
import threading
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .audit import SiteRef, audit, parse_url
from .fixes.baseline import Baseline, capture_baseline
from .fixes.models import FixKind
from .fixes.propose import propose_fixes
from .fixes.queue import FixQueue, InvalidTransition, NotApproved, UnknownFix, summary
from .fixes.validate import validate_json_ld_snippet, validate_llms_txt, validate_mcp_stub
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
_baselines: "dict[str, Baseline]" = {}                 # what each audit's snapshot saw; evicted with its report
_proposals: "dict[str, tuple]" = {}                    # report id -> (FixQueue, skipped list); evicted with its report
_fix_lock = threading.Lock()                           # one lock for every fix operation (in-memory MVP store)


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
    baseline = capture_baseline(ref.origin, fetcher)   # cache hits: the pages the audit just read
    rid = uuid4().hex
    _reports[rid] = report
    _baselines[rid] = baseline
    while len(_reports) > MAX_REPORTS:
        old, _ = _reports.popitem(last=False)
        _baselines.pop(old, None)
        _proposals.pop(old, None)
    return {"id": rid, **asdict(report)}


@app.get("/report/{rid}")
def get_report(rid: str):
    if rid not in _reports:
        raise HTTPException(404, "Unknown report")
    return asdict(_reports[rid])


# --- Fixes (Plan 3). Nothing here makes a request to the site or writes to it. ---------------------------------

def _validators(baseline: Baseline) -> dict:
    origin, found = baseline.origin, baseline.spec()
    return {
        FixKind.LLMS_TXT: lambda content: validate_llms_txt(content, origin),
        FixKind.JSON_LD: validate_json_ld_snippet,
        FixKind.MCP_STUB: lambda content: (validate_mcp_stub(content, found[1], origin) if found
                                           else ["no OpenAPI document to check the stub against"]),
    }


def _fixes_body(rid: str) -> dict:
    queue, skipped = _proposals[rid]
    return {"fixes": [summary(f) for f in queue.list()],
            "skipped": [{"kind": s.kind.value, "reason": s.reason} for s in skipped]}


def _queue(rid: str) -> FixQueue:
    if rid not in _reports:
        raise HTTPException(404, "Unknown report")
    if rid not in _proposals:
        raise HTTPException(404, "No fixes proposed yet")
    return _proposals[rid][0]


@app.post("/report/{rid}/fixes")
def post_fixes(rid: str):
    with _fix_lock:
        if rid not in _reports:
            raise HTTPException(404, "Unknown report")
        if rid not in _proposals:                       # a second call never resets a decision
            baseline = _baselines[rid]
            proposal = propose_fixes(_reports[rid], baseline)
            _proposals[rid] = (FixQueue(proposal.fixes, _validators(baseline)), proposal.skipped)
        return _fixes_body(rid)


@app.get("/report/{rid}/fixes")
def get_fixes(rid: str):
    with _fix_lock:
        _queue(rid)
        return _fixes_body(rid)


def _decide(rid: str, fid: str, action: str) -> dict:
    with _fix_lock:
        queue = _queue(rid)
        try:
            return summary(getattr(queue, action)(fid))
        except UnknownFix:
            raise HTTPException(404, "Unknown fix")
        except InvalidTransition as e:
            raise HTTPException(409, str(e))


@app.post("/report/{rid}/fixes/{fid}/approve")
def approve_fix(rid: str, fid: str):
    return _decide(rid, fid, "approve")


@app.post("/report/{rid}/fixes/{fid}/reject")
def reject_fix(rid: str, fid: str):
    return _decide(rid, fid, "reject")


@app.get("/report/{rid}/fixes/{fid}/export")
def export_fix(rid: str, fid: str):
    with _fix_lock:
        queue = _queue(rid)
        try:
            bundle = queue.export(fid)
        except UnknownFix:
            raise HTTPException(404, "Unknown fix")
        except NotApproved as e:
            raise HTTPException(409, str(e))
        return {"filename": bundle.filename, "content": bundle.content, "diff": bundle.diff,
                "sha256": bundle.sha256, "note": bundle.note}
