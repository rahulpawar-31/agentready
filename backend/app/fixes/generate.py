"""Deterministic fix generators. They read only the baseline pages the audit already fetched; there is no
network access and no model call. Every string taken from a site is untrusted: it is cleaned and clipped
before it reaches an output."""
import json
import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import (DESC_CLIP, FIELD_CLIP, LINK_TEXT_CLIP, LLMS_MAX_LINKS, NotEnoughContent)

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_SPACES = re.compile(r"\s+")
_MARKUP = re.compile(r"[\[\]()<>]")


def _clean(text, limit: int) -> str:
    """One line: control characters and runs of whitespace become single spaces, then clipped."""
    return _SPACES.sub(" ", _CONTROL.sub(" ", str(text or ""))).strip()[:limit].strip()


def _md_safe(text, limit: int) -> str:
    """Also drops the characters that could start a Markdown link or an HTML tag."""
    return _clean(_MARKUP.sub("", _clean(text, 10_000)), limit)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _title(soup) -> str:
    tag = soup.find("title")
    return tag.get_text() if tag else ""


def _description(soup) -> str:
    for attrs in ({"name": re.compile(r"^description$", re.I)}, {"property": re.compile(r"^og:description$", re.I)}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"]
    return ""


def _site_link(origin: str, href: str):
    """An absolute URL on the verified host without query or fragment, or None."""
    try:
        base = urlsplit(origin)
        p = urlsplit(urljoin(origin + "/", href.strip()))
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or p.netloc.lower() != base.netloc.lower():
        return None
    path = quote(p.path or "/", safe="/%:@!$&'*+,;=-._~")        # no spaces or parentheses in a Markdown link
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


def build_llms_txt(origin: str, home_html: str) -> str:
    soup = _soup(home_html)
    title = _md_safe(_title(soup), FIELD_CLIP)
    if not title:
        raise NotEnoughContent("the home page has no title")
    description = _md_safe(_description(soup), DESC_CLIP)
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        if len(links) >= LLMS_MAX_LINKS:
            break
        href = a["href"]
        url = _site_link(origin, href if isinstance(href, str) else "")
        text = _md_safe(a.get_text(), LINK_TEXT_CLIP)
        if url is None or not text or url in seen:
            continue
        seen.add(url)
        links.append(f"- [{text}]({url})")
    if not description and not links:
        raise NotEnoughContent("not enough content on the home page")
    lines = [f"# {title}"]
    if description:
        lines += ["", f"> {description}"]
    if links:
        lines += ["", "## Pages"] + links
    return "\n".join(lines) + "\n"


def build_json_ld(origin: str, home_html: str) -> dict:
    soup = _soup(home_html)
    name = _clean(_title(soup), FIELD_CLIP)
    if not name:
        raise NotEnoughContent("the home page has no title")
    data = {"@context": "https://schema.org", "@type": "WebSite", "name": name, "url": origin}
    description = _clean(_description(soup), DESC_CLIP)
    if description:
        data["description"] = description
    return data


def json_ld_snippet(data: dict) -> str:
    body = json.dumps(data, indent=2, ensure_ascii=False)
    for raw, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        body = body.replace(raw, escaped)
    return f'<script type="application/ld+json">\n{body}\n</script>\n'
