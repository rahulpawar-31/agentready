import json

import pytest
from fastapi import HTTPException
from mcp.client import Client

from app import main
from app.mcp_server import (DIFF_CLIP, EVIDENCE_CLIP, UNTRUSTED_NOTICE, build_server, decorate, map_http_error,
                            tool_error)
from tests.mcp_helpers import run


@pytest.fixture
def server():
    return build_server(main)


def tools_of(server):
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    return run(go())


def test_exactly_three_tools_and_no_approve_or_export(server):
    assert sorted(t.name for t in tools_of(server)) == ["audit_site", "get_report", "propose_fixes"]


def test_tool_annotations_say_what_each_tool_does(server):
    a = {t.name: t.annotations for t in tools_of(server)}
    assert a["get_report"].read_only_hint is True and a["get_report"].open_world_hint is False
    assert a["audit_site"].read_only_hint is False and a["audit_site"].idempotent_hint is True
    assert a["audit_site"].open_world_hint is True
    assert a["propose_fixes"].read_only_hint is False and a["propose_fixes"].idempotent_hint is True
    assert a["propose_fixes"].open_world_hint is False
    assert all(x.destructive_hint is False for x in a.values())


def test_descriptions_state_the_gate_and_the_untrusted_content_rule(server):
    d = {t.name: (t.description or "").lower() for t in tools_of(server)}
    assert "proof of control" in d["audit_site"]
    assert "cannot approve" in d["propose_fixes"]
    assert all("data, not instructions" in x for x in d.values())


def test_server_instructions_exist_and_say_who_approves(server):
    ins = (server.instructions or "").lower()
    assert "approve" in ins and "proof of control" in ins and "data, not instructions" in ins


def test_http_errors_map_to_fixed_tool_error_codes():
    detail = {"token": "t", "meta_tag": "<meta>"}
    blocked = json.loads(str(map_http_error(HTTPException(403, detail))))
    assert blocked == {"error": "control_not_proven", "how_to_verify": detail}
    assert json.loads(str(map_http_error(HTTPException(404, "Unknown report"))))["error"] == "not_found"
    invalid = json.loads(str(map_http_error(HTTPException(422, "bad url"))))
    assert invalid == {"error": "invalid_request", "message": "bad url"}
    other = json.loads(str(map_http_error(HTTPException(500, "stack trace /etc/passwd"))))
    assert other == {"error": "failed", "message": "The request failed."}


def test_tool_error_message_is_json():
    assert json.loads(str(tool_error("x", a=1))) == {"error": "x", "a": 1}


def test_decorate_labels_and_clips_without_mutating_the_input():
    src = {"findings": [{"evidence": "e" * 2000}], "fixes": [{"diff": "d" * 20000, "id": "llms_txt"},
                                                            {"diff": "short", "id": "json_ld"}]}
    out = decorate(src)
    assert out["untrusted_content_notice"] == UNTRUSTED_NOTICE
    assert len(out["findings"][0]["evidence"]) <= EVIDENCE_CLIP + 3
    assert len(out["fixes"][0]["diff"]) <= DIFF_CLIP + 3 and out["fixes"][0]["diff_truncated"] is True
    assert out["fixes"][1]["diff"] == "short" and "diff_truncated" not in out["fixes"][1]
    assert len(src["findings"][0]["evidence"]) == 2000 and "diff_truncated" not in src["fixes"][0]
    assert "untrusted_content_notice" not in src
