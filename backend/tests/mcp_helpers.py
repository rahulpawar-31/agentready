"""Shared helpers for the MCP tests (a plain module, not a test file; it does not import any test module)."""
import asyncio
import json

from mcp.client import Client

from app import main
from tests.conftest import FakeFetcher, page, public_resolver

B = "https://x.test"
TEXT = "<p>" + "word " * 80 + "</p>"


def site_html(host="x.test", extra_head="", extra_body=""):
    token = main.make_token(host, main.SECRET)
    return ('<html>\n<head>\n<title>Acme Shoes</title>\n<meta name="description" content="Handmade shoes.">\n'
            f'<meta name="agentready-verification" content="{token}">\n{extra_head}</head>\n<body>\n'
            f'<a href="/shop">Shop</a>\n{TEXT}\n{extra_body}</body>\n</html>\n')


def pages_for(home=None, extra=None):
    pages = {f"{B}/": page(f"{B}/", home or site_html())}
    for path, body in (extra or {}).items():
        pages[f"{B}{path}"] = page(f"{B}{path}", body)
    return pages


def setup_site(monkeypatch, pages):
    """Fake the guarded fetchers, the TXT lookup and the resolver exactly as tests/test_api_fixes.py does."""
    fake = FakeFetcher(pages)
    monkeypatch.setattr(main, "build_probe_fetcher", lambda host, **kw: fake)
    monkeypatch.setattr(main, "build_audit_fetcher", lambda host, snapshot="default", **kw: fake)
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    main.app.dependency_overrides[main.get_resolver] = lambda: public_resolver
    return fake


def run(coro):
    return asyncio.run(coro)


def call(server, name, arguments=None):
    """Call a tool through the real MCP protocol, in memory. Returns the CallToolResult."""
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, arguments or {})
    return run(go())


def result_json(result):
    return json.loads(result.content[0].text)


def error_json(result):
    """The JSON body of a tool error. Our ToolError messages are JSON, and the SDK prefixes them with
    'Error executing tool <name>: ', so parse from the first brace."""
    assert result.is_error
    text = result.content[0].text
    return json.loads(text[text.index("{"):])


def walk_keys(obj):
    """Every dict key anywhere inside a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)
