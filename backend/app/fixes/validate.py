"""Validators. Each returns a list of problems; an empty list means the output is valid."""
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import LLMS_MAX_BYTES

_LINK_LINE = re.compile(r"^- \[[^\[\]]+\]\((https?://[^)\s]+)\)$")
_ANY_LINK = re.compile(r"\]\(([^)]*)\)")
_BAD_CHAR = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def _on_host(url: str, origin: str) -> bool:
    try:
        return urlsplit(url).netloc.lower() == urlsplit(origin).netloc.lower() and urlsplit(url).scheme in ("http", "https")
    except ValueError:
        return False


def validate_llms_txt(text: str, origin: str) -> list[str]:
    problems = []
    if len(text.encode("utf-8")) > LLMS_MAX_BYTES:
        problems.append(f"larger than {LLMS_MAX_BYTES} bytes")
    if _BAD_CHAR.search(text):
        problems.append("contains a control character")
    lines = text.split("\n")
    if not lines[0].startswith("# ") or not lines[0][2:].strip():
        problems.append("the first line is not a '# ' title")
    if sum(1 for l in lines if l.startswith("# ")) > 1:
        problems.append("more than one '# ' title line")
    for target in _ANY_LINK.findall(text):
        if not _on_host(target, origin):
            problems.append("a link points off the verified site")
            break
    for number, line in enumerate(lines[1:], start=2):
        if not line or line.startswith("> ") or line == "## Pages":
            continue
        m = _LINK_LINE.match(line)
        if m and _on_host(m.group(1), origin):
            continue
        if not m:
            problems.append(f"line {number} is not a recognised llms.txt line")
            break
    return problems


def validate_json_ld_snippet(snippet: str) -> list[str]:
    if not snippet.strip():
        return ["empty snippet"]
    problems = []
    if len(re.findall(r"</script", snippet, flags=re.I)) != 1:
        problems.append("the snippet must contain exactly one script element")
    soup = BeautifulSoup(snippet, "html.parser")
    scripts = soup.find_all("script")
    ld = [s for s in scripts if (s.get("type") or "").lower() == "application/ld+json"]
    if len(scripts) != 1 or len(ld) != 1:
        problems.append("the snippet must be exactly one application/ld+json script")
        return problems
    for s in scripts:
        s.extract()
    if soup.get_text().strip() or soup.find():
        problems.append("there is content outside the script element")
    try:
        data = json.loads(ld[0].get_text())
    except ValueError:
        return problems + ["the JSON does not parse"]
    if not isinstance(data, dict):
        return problems + ["the JSON-LD must be an object"]
    context = data.get("@context")
    if not isinstance(context, str) or not (context == "https://schema.org" or context.startswith("https://schema.org/")):
        problems.append("@context must be a https://schema.org URL")
    if not isinstance(data.get("@type"), str) or not data["@type"].strip():
        problems.append("@type is missing")
    return problems
