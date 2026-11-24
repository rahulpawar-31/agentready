"""Deterministic fix generators. They read only the baseline pages the audit already fetched; there is no
network access and no model call. Every string taken from a site is untrusted: it is cleaned and clipped
before it reaches an output."""
import ast
import json
import re
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, CData, NavigableString, Tag

from .models import (DESC_CLIP, FIELD_CLIP, LINK_TEXT_CLIP, LLMS_MAX_LINKS, MCP_MAX_TOOLS, NotEnoughContent,
                     SecretInSiteText)
from .secrets import find_secrets

_SPACES = re.compile(r"\s+")
_MARKUP = re.compile(r"[\[\]()<>`*_!~]")                     # could start a link, an image, emphasis, code or a tag
# A bare web address or email in free text: Markdown renderers turn these into links, so they are not copied.
_BARE_LINK = re.compile(r"(?i)\b(?:https?://|www\.)\S*|\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")


def _plain(text) -> str:
    """Invisible and control characters out: format characters (zero-width, bidi, BOM) are removed, control
    characters and every kind of space become one ordinary space."""
    out = []
    for ch in str(text or "")[:20_000]:
        category = unicodedata.category(ch)
        if category == "Cf":
            continue
        out.append(" " if category in ("Cc", "Zl", "Zp", "Zs") else ch)
    return "".join(out)


def _clean(text, limit: int) -> str:
    """One line: invisible characters removed, runs of whitespace become single spaces, then clipped."""
    return _SPACES.sub(" ", _plain(text)).strip()[:limit].strip()


def _md_safe(text, limit: int) -> str:
    """For Markdown output: also drops bare links and emails and every character that could start a Markdown
    link, image, emphasis, code span or HTML tag."""
    return _clean(_MARKUP.sub("", _BARE_LINK.sub(" ", _clean(text, 10_000))), limit)


def _guard(raw) -> None:
    """Raise SecretInSiteText if a string taken from the site looks like a secret. It is checked as the site wrote
    it, before any clean-up that could change its shape."""
    if isinstance(raw, str):
        labels = find_secrets(raw)
        if labels:
            raise SecretInSiteText(labels[0])


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(tag, limit: int, max_nodes: int = 200) -> str:
    """The first `limit` characters of a tag's text, found by walking its children with a cap on the nodes visited.
    (BeautifulSoup's own text helpers cost time in proportion to how deeply a tag is nested, which hostile markup can
    exploit: here the work per tag is bounded.) Comments, scripts and styles are not text."""
    parts, total, visited = [], 0, 0
    stack = [iter(tag.contents)]
    while stack and total < limit and visited < max_nodes:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        visited += 1
        if type(node) in (NavigableString, CData):
            parts.append(str(node))
            total += len(node)
        elif isinstance(node, Tag) and node.name not in ("script", "style"):
            stack.append(iter(node.contents))
    return "".join(parts)[:limit]


def _title(soup) -> str:
    """The page title: the <title> inside <head>, else the first <title> that is not inside an inline <svg>."""
    head = soup.find("head")
    tag = head.find("title") if head else None
    if tag is None:
        tag = next((t for t in soup.find_all("title", limit=20) if t.find_parent("svg") is None), None)
    return _text(tag, 1000) if tag else ""


def _description(soup) -> str:
    for attrs in ({"name": re.compile(r"^description$", re.I)}, {"property": re.compile(r"^og:description$", re.I)}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"]
    return ""


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _site_link(origin: str, href: str):
    """An absolute URL on the verified site (same scheme, host and port; an explicit default port counts as the
    bare host) without query or fragment, or None. The link is built from the origin, never from the href."""
    try:
        base = urlsplit(origin)
        p = urlsplit(urljoin(origin + "/", href.strip()))
        same = (p.scheme == base.scheme and p.scheme in _DEFAULT_PORTS and p.hostname is not None
                and p.hostname == base.hostname
                and (p.port or _DEFAULT_PORTS[p.scheme]) == (base.port or _DEFAULT_PORTS[base.scheme]))
        if not same or p.username is not None or p.password is not None:
            return None
    except ValueError:
        return None
    path = quote(p.path or "/", safe="/%:@!$&'*+,;=-._~")        # no spaces or parentheses in a Markdown link
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


def build_llms_txt(origin: str, home_html: str) -> str:
    soup = _soup(home_html)
    raw_title, raw_description = _title(soup), _description(soup)
    _guard(raw_title)
    _guard(raw_description)
    title = _md_safe(raw_title, FIELD_CLIP)
    if not title:
        raise NotEnoughContent("the home page has no title")
    description = _md_safe(raw_description, DESC_CLIP)
    links, seen = [], set()
    for a in soup.find_all("a", href=True, limit=_MAX_ANCHORS):
        if len(links) >= LLMS_MAX_LINKS:
            break
        href = a["href"]
        url = _site_link(origin, href if isinstance(href, str) else "")
        raw_text = _text(a, 4 * LINK_TEXT_CLIP)
        text = _md_safe(raw_text, LINK_TEXT_CLIP)
        if url is None or not text or url in seen:
            continue
        if find_secrets(raw_text) or find_secrets(url):         # a link that carries a secret is left out
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
    raw_title, raw_description = _title(soup), _description(soup)
    _guard(raw_title)
    _guard(raw_description)
    name = _clean(raw_title, FIELD_CLIP)
    if not name:
        raise NotEnoughContent("the home page has no title")
    data = {"@context": "https://schema.org", "@type": "WebSite", "name": name, "url": origin}
    description = _clean(raw_description, DESC_CLIP)
    if description:
        data["description"] = description
    return data


def json_ld_snippet(data: dict) -> str:
    body = json.dumps(data, indent=2, ensure_ascii=False)
    for raw, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        body = body.replace(raw, escaped)
    return f'<script type="application/ld+json">\n{body}\n</script>\n'


# --- OpenAPI -> MCP stub ------------------------------------------------------------------------------------

_PATH_OK = re.compile(r"^/[A-Za-z0-9._~!$&'*+,;=:@%/{}-]*$")
_PARAM_OK = re.compile(r"^[A-Za-z0-9_.~-]{1,64}$")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_SCHEMA_TYPES = {"string", "integer", "number", "boolean"}
_MAX_ANCHORS = 2000                  # anchors examined per page: bounds the work on hostile markup


def _schema_type(schema) -> str:
    """The parameter type for the tool schema. OpenAPI 3.1 allows `type` to be a list such as ["string", "null"]:
    the first non-"null" string entry is used. Anything else is treated as a string."""
    value = schema.get("type") if isinstance(schema, dict) else None
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v != "null"), None)
    return value if isinstance(value, str) and value in _SCHEMA_TYPES else "string"

_STUB_HEAD = ('"""Read-only MCP tool stub generated by AgentReady from your OpenAPI document. Review it before use."""\n'
              "import json\nimport urllib.parse\nimport urllib.request\n\n"
              "class _NoRedirect(urllib.request.HTTPRedirectHandler):\n"
              "    def redirect_request(self, req, fp, code, msg, headers, newurl):\n"
              "        return None\n\n"
              "_OPENER = urllib.request.build_opener(_NoRedirect())\n\n")

_STUB_TAIL = '''

_BY_NAME = {tool["name"]: tool for tool in TOOLS}
MAX_BYTES = 1_000_000


def _text(value, label):
    """A path or query value: a string or a number. Lists, objects and null are refused."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, (str, int, float)):
        raise ValueError(label + " must be a string or a number")
    return str(value)


def call_tool(name, arguments):
    """Run one read-only GET against BASE_URL for the named tool and return the response body (at most 1 MB)."""
    tool = _BY_NAME[name]
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    path = tool["path"]
    for param in tool["path_params"]:
        if param not in arguments:
            raise ValueError("missing argument: " + param)
        value = _text(arguments[param], param)
        if value in (".", ".."):
            raise ValueError(param + " must not be . or ..")
        path = path.replace("{" + param + "}", urllib.parse.quote(value, safe=""))
    query = {key: _text(value, key) for key, value in arguments.items() if key in tool["query_params"]}
    url = BASE_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(url, method="GET")
    with _OPENER.open(request, timeout=15) as response:
        return response.read(MAX_BYTES).decode("utf-8", "replace")


# This file is a stub: wire TOOLS and call_tool into the MCP SDK of your choice. Review it before you use it.
'''


def _tool_name(operation_id, path: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", operation_id).strip("_")[:64] if isinstance(operation_id, str) else ""
    if not name or name[0].isdigit():
        slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_") or "root"
        name = ("get_" + slug)[:64]
    return name


def _parameters(path_item: dict, operation: dict) -> list:
    found = []
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if isinstance(source, list):
            found += [p for p in source if isinstance(p, dict)]
    return found


def _tools_from_spec(spec) -> list:
    paths = spec.get("paths") if isinstance(spec, dict) else None
    if not isinstance(paths, dict):
        raise NotEnoughContent("the OpenAPI document has no paths")
    tools, used = [], set()
    for path, item in paths.items():
        if len(tools) >= MCP_MAX_TOOLS:
            break
        if not isinstance(path, str) or not _PATH_OK.match(path) or not isinstance(item, dict):
            continue
        if any(unquote(segment) in (".", "..") for segment in path.split("/")):     # no dot segments, even encoded
            continue
        operation = item.get("get")
        if not isinstance(operation, dict):
            continue
        for raw in (path, operation.get("operationId"), operation.get("summary"), operation.get("description")):
            _guard(raw)
        declared = {}
        for p in _parameters(item, operation):
            name, where = p.get("name"), p.get("in")
            _guard(name)
            if isinstance(name, str) and _PARAM_OK.match(name) and where in ("path", "query"):
                declared[(where, name)] = (_schema_type(p.get("schema")), p.get("required") is True)
        path_params = [n for n in dict.fromkeys(_PLACEHOLDER.findall(path)) if _PARAM_OK.match(n)]
        query_params = [n for (where, n) in declared if where == "query"]
        properties = {n: {"type": declared.get(("path", n), ("string", True))[0]} for n in path_params}
        properties.update({n: {"type": declared[("query", n)][0]} for n in query_params})
        required = path_params + [n for n in query_params if declared[("query", n)][1]]
        base = _tool_name(operation.get("operationId"), path)
        name, i = base, 2
        while name in used:
            name = f"{base[:60]}_{i}"
            i += 1
        used.add(name)
        description = operation.get("summary") if isinstance(operation.get("summary"), str) else operation.get("description")
        tools.append({"name": name, "description": _clean(description, DESC_CLIP) or path, "method": "GET", "path": path,
                      "path_params": path_params, "query_params": query_params,
                      "input_schema": {"type": "object", "properties": properties, "required": required}})
    if not tools:
        raise NotEnoughContent("the OpenAPI document has no GET operations")
    return tools


def build_mcp_stub(origin: str, spec: dict) -> str:
    """A read-only Python stub: one tool per GET operation. BASE_URL is always the verified origin, and spec
    text is embedded as an escaped string literal, never as code."""
    tools = _tools_from_spec(spec)
    return (_STUB_HEAD + f"BASE_URL = {json.dumps(origin)}\n"
            + f"TOOLS = json.loads({json.dumps(tools)!r})\n" + _STUB_TAIL)


def tools_from_stub(source: str) -> list:
    """Recover the TOOLS list from a stub without running it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        raise ValueError("not valid Python") from e
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "TOOLS"):
            call = node.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "loads"
                    and len(call.args) == 1 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                data = json.loads(call.args[0].value)
                if isinstance(data, list) and all(isinstance(t, dict) for t in data):
                    return data
            break
    raise ValueError("TOOLS is not a json.loads of a string literal")
