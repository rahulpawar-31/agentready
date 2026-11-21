import json

from bs4 import BeautifulSoup

from ..models import Category, Confidence, Status
from .base import Site, finding

CAT = Category.DISCOVERABILITY
AI_BOTS = {"gptbot", "claudebot", "perplexitybot", "google-extended", "ccbot"}


def _blocked_bots(robots: str) -> list[str]:
    blocked: set[str] = set()
    agents: list[str] = []
    in_rules = False
    for raw in robots.splitlines():
        line = raw.split("#")[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
        else:
            in_rules = True
            if key == "disallow" and value == "/":
                blocked.update(a for a in agents if a in AI_BOTS or a == "*")
    return sorted(blocked)


def _valid_json_ld(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            json.loads(tag.string or "")
            return True
        except ValueError:
            continue
    return False


def run(site: Site):
    robots = site.get("/robots.txt")
    has_robots = robots.status == 200
    blocked = _blocked_bots(robots.body) if has_robots else []
    if blocked:
        r = dict(passed=False, evidence="robots.txt disallows / for: " + ", ".join(blocked),
                 status=Status.DETECTED, confidence=Confidence.HIGH)
    elif has_robots:
        r = dict(passed=True, evidence="robots.txt was read; no rule blocks AI crawlers",
                 status=Status.NOT_DETECTED, confidence=Confidence.HIGH)
    else:
        r = dict(passed=True, evidence=f"/robots.txt returned {robots.status}, so it does not restrict crawlers",
                 status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM)
    out = [finding("robots_ai", CAT, robots, weight=3,
                   check="GET /robots.txt and look for Disallow: / under an AI crawler or * agent", **r)]

    sm = site.get("/sitemap.xml")
    in_robots = has_robots and "sitemap:" in robots.body.lower()
    found = in_robots or sm.status == 200
    out.append(finding("sitemap", CAT, sm, passed=found, weight=1,
                       check="Look for a Sitemap: line in robots.txt, then GET /sitemap.xml",
                       evidence="Sitemap found" if found else
                       f"No Sitemap: line in robots.txt and /sitemap.xml returned {sm.status}; sitemaps at other paths are not checked",
                       status=Status.DETECTED if found else Status.NOT_DETECTED,
                       confidence=Confidence.HIGH if found else Confidence.MEDIUM))

    llms = site.get("/llms.txt")
    llms_ok = llms.status == 200 and bool(llms.body.strip())
    out.append(finding("llms_txt", CAT, llms, passed=llms_ok, weight=2,
                       check="GET /llms.txt and expect a 200 with non-empty text",
                       evidence=f"llms.txt present ({len(llms.body)} characters)" if llms_ok
                       else f"/llms.txt returned {llms.status} or was empty",
                       status=Status.DETECTED if llms_ok else Status.NOT_DETECTED,
                       confidence=Confidence.HIGH))

    home = site.get("/")
    ld_ok = home.status == 200 and _valid_json_ld(home.body)
    out.append(finding("json_ld", CAT, home, passed=ld_ok, weight=2,
                       check="GET / and parse every application/ld+json script block",
                       evidence="Valid JSON-LD found in the home page HTML" if ld_ok else
                       "No valid JSON-LD in the home page HTML (home page only; script-injected JSON-LD is not visible)",
                       status=Status.DETECTED if ld_ok else Status.NOT_DETECTED,
                       confidence=Confidence.HIGH if ld_ok else Confidence.MEDIUM))
    return out
