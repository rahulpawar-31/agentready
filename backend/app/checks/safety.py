import re

from ..models import Category, Confidence, Status
from .base import Site, finding

CAT = Category.SAFETY
ENV_LINE = re.compile(r"^[A-Z][A-Z0-9_]*=", re.MULTILINE)
REQUIRED_HEADERS = ("strict-transport-security", "x-content-type-options", "content-security-policy")


def run(site: Site):
    env = site.get("/.env")
    env_leak = env.status == 200 and bool(ENV_LINE.search(env.body))
    git = site.get("/.git/HEAD")
    git_leak = git.status == 200 and git.body.startswith("ref:")
    home = site.get("/")
    missing = [h for h in REQUIRED_HEADERS if h not in home.headers]

    return [
        finding("env_not_exposed", CAT, env, passed=not env_leak, weight=5,
                check="GET /.env and look for KEY=value lines (contents are never stored)",
                evidence="Exposed path observed: /.env was publicly readable during this check" if env_leak else
                f"/.env returned {env.status} or no KEY=value lines. Other paths were not tested",
                status=Status.DETECTED if env_leak else Status.NOT_DETECTED,
                confidence=Confidence.HIGH if env_leak else Confidence.MEDIUM),
        finding("git_not_exposed", CAT, git, passed=not git_leak, weight=0,
                check="GET /.git/HEAD and look for a ref: line (observation only, not scored)",
                evidence="Readable /.git/HEAD detected. It is source-control metadata and only this file was requested, so exposure of the repository is not confirmed" if git_leak else
                f"/.git/HEAD returned {git.status} or no ref: line. Other paths were not tested",
                status=Status.DETECTED if git_leak else Status.NOT_DETECTED,
                confidence=Confidence.MEDIUM),
        finding("security_headers", CAT, home, passed=not missing, weight=2,
                check="GET / and check the response headers for HSTS, X-Content-Type-Options and CSP",
                evidence=("Missing headers: " + ", ".join(missing)) if missing else
                "All 3 security headers present on the home page response",
                status=Status.NOT_DETECTED if missing else Status.DETECTED,
                confidence=Confidence.HIGH),
    ]
