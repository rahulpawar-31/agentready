"""Validators. Each returns a list of problems; an empty list means the output is valid."""
import ast
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .generate import _tools_from_spec, build_mcp_stub, tools_from_stub
from .models import LLMS_MAX_BYTES, STUB_MAX_BYTES, NotEnoughContent

_LINK_LINE = re.compile(r"^- \[[^\[\]]+\]\((https?://[^)\s]+)\)$")
_ANY_LINK = re.compile(r"\]\(([^)]*)\)")
_BAD_CHAR = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def _on_host(url: str, origin: str) -> bool:
    try:
        got, want = urlsplit(url), urlsplit(origin)
        return got.netloc.lower() == want.netloc.lower() and got.scheme == want.scheme and got.scheme in ("http", "https")
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


_STUB_IMPORTS = {"json", "urllib.parse", "urllib.request"}
_STUB_FUNCTIONS = ("_text", "call_tool")
# Every call the stub may make. Anything else inside any function or at the top level is a problem.
_STUB_CALLS = {"isinstance", "str", "ValueError", "json.loads", "urllib.parse.quote", "urllib.parse.urlencode",
               "urllib.request.Request", "urllib.request.build_opener", "_NoRedirect", "_text", "arguments.items",
               "path.replace", "_OPENER.open", "response.read"}
_STUB_METHODS_ON_RESULTS = {"decode"}            # response.read(...).decode(...)
_STUB_BANNED_NAMES = {"os", "sys", "subprocess", "socket", "shutil", "open", "exec", "eval", "compile", "__import__",
                      "getattr", "setattr", "delattr", "globals", "locals", "vars", "input", "breakpoint"}
_STUB_VERBS = {"POST", "PUT", "DELETE", "PATCH", "CONNECT", "TRACE"}


def _dotted(node):
    """"a.b.c" for a chain of names and attributes, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _leftmost(node):
    while isinstance(node, ast.BinOp):
        node = node.left
    return node


def _inspect_stub_code(tree) -> list:
    """What the stub's code does, not only what it looks like: every function body is checked."""
    problems = []
    if sorted(n.name for n in tree.body if isinstance(n, ast.FunctionDef)) != sorted(_STUB_FUNCTIONS):
        problems.append("the stub must define exactly the functions _text and call_tool")
    top_level = {id(n) for n in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top_level:
            problems.append("an import inside a function or class is not allowed")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            allowed = (name in _STUB_CALLS) if name else (isinstance(node.func, ast.Attribute)
                                                          and node.func.attr in _STUB_METHODS_ON_RESULTS)
            if not allowed:
                problems.append(f"a call to {name or 'an expression'} is not allowed in the stub")
        elif isinstance(node, ast.Name) and node.id in _STUB_BANNED_NAMES:
            problems.append(f"the name {node.id} is not allowed in the stub")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append("a dunder attribute is not allowed in the stub")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.upper() in _STUB_VERBS:
            problems.append("the stub names an HTTP method other than GET")
    requests = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _dotted(n.func) == "urllib.request.Request"]
    shape_ok = (len(requests) == 1 and len(requests[0].args) == 1 and isinstance(requests[0].args[0], ast.Name)
                and requests[0].args[0].id == "url" and len(requests[0].keywords) == 1
                and requests[0].keywords[0].arg == "method" and isinstance(requests[0].keywords[0].value, ast.Constant)
                and requests[0].keywords[0].value.value == "GET")
    if not shape_ok:
        problems.append("the request must be urllib.request.Request(url, method=\"GET\") and nothing else")
    url_assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "url" for t in n.targets)]
    start = _leftmost(url_assignments[0].value) if len(url_assignments) == 1 else None
    if not (isinstance(start, ast.Name) and start.id == "BASE_URL"):
        problems.append("the request URL must start from BASE_URL")
    return list(dict.fromkeys(problems))


def validate_mcp_stub(source: str, spec: dict, origin: str) -> list[str]:
    problems = []
    if len(source.encode("utf-8")) > STUB_MAX_BYTES:
        problems.append(f"larger than {STUB_MAX_BYTES} bytes")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return problems + ["not valid Python"]
    no_redirect_classes = 0
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(a.name not in _STUB_IMPORTS for a in node.names):
                problems.append("imports a module outside json and urllib")
        elif isinstance(node, ast.Expr):
            if not isinstance(node.value, ast.Constant):
                problems.append("runs code at import time")
        elif isinstance(node, ast.ClassDef):
            no_redirect_classes += 1
            methods = [n for n in node.body if isinstance(n, ast.FunctionDef)]
            valid = (node.name == "_NoRedirect" and len(methods) == 1 and len(node.body) == 1
                     and methods[0].name == "redirect_request"
                     and len(methods[0].body) == 1 and isinstance(methods[0].body[0], ast.Return)
                     and isinstance(methods[0].body[0].value, ast.Constant)
                     and methods[0].body[0].value.value is None)
            if not valid:
                problems.append("the redirect handler must refuse redirects")
        elif not isinstance(node, (ast.Assign, ast.FunctionDef)):
            problems.append("a top-level statement other than an import, assignment or function")
    if no_redirect_classes != 1:
        problems.append("the stub must define exactly one no-redirect handler")
    problems += _inspect_stub_code(tree)
    base_url = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "BASE_URL"]
    if base_url != [origin]:
        problems.append("BASE_URL is not the verified origin")
    try:
        tools = tools_from_stub(source)
        expected = _tools_from_spec(spec)
    except ValueError as e:                     # NotEnoughContent is a ValueError too
        return problems + [str(e) or "cannot read the tool list"]
    for t in tools:
        if t.get("method") != "GET":
            problems.append("a tool is not a GET")
            break
    if any(not isinstance(t.get("name"), str) or not t["name"].isidentifier() for t in tools):
        problems.append("a tool name is not an identifier")
    if sorted((t.get("name"), t.get("path")) for t in tools) != sorted((t["name"], t["path"]) for t in expected):
        problems.append("the tools do not match the GET operations in the OpenAPI document")
    try:
        if source != build_mcp_stub(origin, spec):
            problems.append("the file differs from what the generator produces")
    except NotEnoughContent:
        pass
    return problems
