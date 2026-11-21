import json
import re

from bs4 import BeautifulSoup

from ..models import Category, Confidence, Status
from .base import Site, finding

CAT = Category.DISCOVERABILITY
AI_BOTS = {"gptbot", "claudebot", "perplexitybot", "google-extended", "ccbot", "anthropic-ai", "claude-web"}
NON_GROUP = {"sitemap", "crawl-delay", "host"}     # these lines never end a User-agent group
_SITEMAP_LINE = re.compile(r"^\s*sitemap\s*:\s*\S", re.IGNORECASE)


def _blocked_bots(robots: str) -> list[str]:
    blocked: set[str] = set()
    agents: list[str] = []
    in_rules = False
    for raw in robots.lstrip("\ufeff").splitlines():
        line = raw.split("#")[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key in NON_GROUP:
            continue
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
        else:
            in_rules = True
            if key == "disallow" and value in ("/", "/*"):
                blocked.update(a for a in agents if a in AI_BOTS or a == "*")
    return sorted(blocked)


def _has_sitemap_line(robots: str) -> bool:
    return any(_SITEMAP_LINE.match(raw.split("#")[0]) for raw in robots.lstrip("\ufeff").splitlines())


def _is_json_ld(data) -> bool:
    items = data if isinstance(data, list) else [data]
    return any(isinstance(i, dict) and ("@type" in i or "@context" in i) for i in items)


def _valid_json_ld(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script"):
        if str(tag.get("type") or "").strip().lower() != "application/ld+json":
            continue
        try:
            data = json.loads(tag.get_text() or "")
        except (ValueError, RecursionError):
            continue
        if _is_json_ld(data):
            return True
    return False


def run(site: Site):
    robots = site.get("/robots.txt")
    has_robots = robots.status == 200
    blocked = _blocked_bots(robots.body) if has_robots else []
    if blocked:
        r = dict(passed=False, evidence="robots.txt disallows / for: " + ", ".join(blocked),
                 status=Status.DETECTED, confidence=Confidence.HIGH)
    elif has_robots:
        r = dict(passed=True,
                 evidence="robots.txt was read; no Disallow: / rule found for the listed AI crawlers ("
                          + ", ".join(sorted(AI_BOTS)) + ") or *",
                 status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM)
    else:
        r = dict(passed=True, evidence=f"/robots.txt returned {robots.status}, so it does not restrict crawlers",
                 status=Status.NOT_DETECTED, confidence=Confidence.MEDIUM)
    out = [finding("robots_ai", CAT, robots, weight=3,
                   check="GET /robots.txt and look for Disallow: / under an AI crawler or * agent", **r)]

    sm = site.get("/sitemap.xml")
    in_robots = has_robots and _has_sitemap_line(robots.body)
    sm_ok = sm.status == 200 and bool(re.search(r"<(urlset|sitemapindex)\b", sm.body, re.IGNORECASE))
    found = in_robots or sm_ok
    out.append(finding("sitemap", CAT, robots if in_robots else sm, passed=found, weight=1,
                       check="Look for a Sitemap: line in robots.txt, then GET /sitemap.xml",
                       evidence="Sitemap found" if found else
                       f"No Sitemap: line in robots.txt and /sitemap.xml returned {sm.status}; sitemaps at other paths are not checked",
                       status=Status.DETECTED if found else Status.NOT_DETECTED,
                       confidence=Confidence.HIGH if found else Confidence.MEDIUM))

    llms = site.get("/llms.txt")
    looks_html = llms.body.lstrip().startswith("<") or "text/html" in llms.headers.get("content-type", "").lower()
    llms_ok = llms.status == 200 and bool(llms.body.strip()) and not looks_html
    out.append(finding("llms_txt", CAT, llms, passed=llms_ok, weight=2,
                       check="GET /llms.txt and expect a 200 with non-empty text",
                       evidence=f"llms.txt present ({len(llms.body)} characters)" if llms_ok
                       else "/llms.txt returned an HTML page, not a text file" if llms.status == 200 and looks_html
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
