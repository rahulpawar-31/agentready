"""Logging side effects of the MCP server (review finding I5).

The SDK calls `logging.basicConfig(level=INFO)` when a server is built. Under pytest the root logger already has
handlers, which hides that, so these tests run in a fresh interpreter, exactly like production."""
import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def run_script(source: str, tmp_path, extra_env=None):
    script = tmp_path / "probe.py"
    script.write_text(textwrap.dedent(source))
    import os
    env = {**os.environ, "AGENTREADY_DEV": "1", "PYTHONPATH": str(BACKEND), **(extra_env or {})}
    return subprocess.run([sys.executable, str(script)], cwd=BACKEND, env=env, capture_output=True, text=True,
                          timeout=120)


def test_building_the_server_leaves_the_root_logger_alone(tmp_path):
    out = run_script("""
        import json, logging
        root = logging.getLogger()
        before = [root.level, [type(h).__name__ for h in root.handlers]]
        from app import main
        from app.mcp_server import build_server
        build_server(main)
        after = [root.level, [type(h).__name__ for h in root.handlers]]
        print(json.dumps({"before": before, "after": after, "mcp": logging.getLogger("mcp").level}))
        """, tmp_path)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen["before"] == seen["after"] == [logging.WARNING, []]
    assert seen["mcp"] == logging.WARNING                                  # the SDK's own loggers stay quiet


def test_importing_the_app_and_starting_it_leaves_the_root_logger_alone(tmp_path):
    out = run_script("""
        import json, logging
        from fastapi.testclient import TestClient
        root = logging.getLogger()
        before = [root.level, len(root.handlers)]
        from app import main
        with TestClient(main.app):
            during = [root.level, len(root.handlers)]
        print(json.dumps({"before": before, "during": during}))
        """, tmp_path)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen["before"] == seen["during"] == [logging.WARNING, 0]


PROBE = """
    import asyncio, json, logging, os
    import httpx
    from mcp.client import Client
    from app import main
    from app.mcp_server import build_server

    HOSTILE = "SITE-CONTROLLED-password-Hunter2Hunter2"
    token = main.make_token("unverified.test", main.SECRET)
    open(os.environ["PROBE_OUT"], "w").write(json.dumps({"token": token, "hostile": HOSTILE}))
    good_token = main.make_token("x.test", main.SECRET)
    HOME = ("<html><head><title>Acme</title><meta name='agentready-verification' content='%s'></head>"
            "<body>" + "word " * 80 + "</body></html>") % good_token

    def handler(request):
        host = request.headers["host"]
        if host == "unverified.test":
            return httpx.Response(200, text="<html><body>nothing here</body></html>")
        if request.url.path == "/":
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
        if request.url.path == "/" + HOSTILE:
            return httpx.Response(404, text="gone")
        return httpx.Response(302, headers={"location": "/" + HOSTILE})

    main.new_http_client = lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    main.GUARD_OPTIONS["min_interval"] = 0
    main.CACHE_DIR = __import__("pathlib").Path(os.environ["PROBE_CACHE"])
    main.app.dependency_overrides[main.get_txt_lookup] = lambda: (lambda name: [])
    main.app.dependency_overrides[main.get_resolver] = lambda: (lambda host: ["93.184.216.34"])
    if os.environ.get("PROBE_ROOT_INFO") == "1":
        logging.getLogger().setLevel(logging.INFO)

    async def go():
        server = build_server(main, host_interval=0)
        if os.environ.get("PROBE_ROOT_INFO") == "1":
            logging.getLogger().setLevel(logging.INFO)          # an operator who turns INFO on for their own code
            logging.getLogger().addHandler(logging.StreamHandler())
        async with Client(server) as c:
            refused = await c.call_tool("audit_site", {"url": "https://unverified.test"})
            ok = await c.call_tool("audit_site", {"url": "https://x.test"})
            return refused.is_error, ok.is_error
    print("RESULT", asyncio.run(go()))
    """


@pytest.mark.parametrize("root_info", ["0", "1"])
def test_no_log_line_carries_the_ownership_token_or_text_the_site_controls(tmp_path, root_info):
    """A refused audit (the SDK logs a failed tool call, with our error JSON) and a successful audit of a site that
    redirects to a path it chose (httpx logs every request at INFO). Neither may reach the log, even when the
    operator has turned INFO on for the root logger."""
    out = run_script(PROBE, tmp_path, {"PROBE_OUT": str(tmp_path / "probe.json"), "PROBE_CACHE": str(tmp_path / "c"),
                                       "PROBE_ROOT_INFO": root_info})
    assert out.returncode == 0, out.stderr[-2000:]
    assert "RESULT (True, False)" in out.stdout                            # refused, then audited
    secrets = json.loads((tmp_path / "probe.json").read_text())
    logged = out.stdout.replace("RESULT (True, False)", "") + out.stderr
    assert secrets["token"] not in logged and secrets["hostile"] not in logged
