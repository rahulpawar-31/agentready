"""Group A of the Plan 3 review: fix generation never raises on legitimate or hostile input, never loses a report,
and never holds the global lock while it works."""
import json
import threading
import time

import pytest
from fastapi import HTTPException

from app import main
from app.fixes.baseline import Baseline
from app.fixes.generate import build_mcp_stub, tools_from_stub
from app.fixes.models import FixKind
from app.fixes.propose import propose_fixes
from app.models import Report
from tests.test_api_fixes import B, SPEC, _clean_stores, audited, client, pages_for, site_html  # noqa: F401

O = "https://x.test"
HEAD = "<html><head><title>T</title><meta name='description' content='d'></head><body>"
EMPTY = Report(O, 0, {}, [], 0)


def spec_with_type(t):
    return {"openapi": "3.1.0", "paths": {"/a": {"get": {"parameters": [
        {"name": "q", "in": "query", "schema": {"type": t}}]}}}}


@pytest.mark.parametrize("t,expected", [
    (["string", "null"], "string"), (["null", "integer"], "integer"), (["null"], "string"),
    ({"x": 1}, "string"), ([["string"]], "string"), (None, "string"), (7, "string"), ("number", "number"),
])
def test_c1_schema_type_may_be_a_list_a_dict_or_anything_else(t, expected):
    tools = tools_from_stub(build_mcp_stub(O, spec_with_type(t)))
    assert tools[0]["input_schema"]["properties"]["q"]["type"] == expected


def test_c1_baseline_spec_survives_a_json_document_nested_too_deeply_to_parse():
    deep = '{"openapi": "3.0.0", "x": ' + "[" * 100_000 + "]" * 100_000 + "}"
    assert Baseline(O, {"/openapi.json": (200, deep)}).spec() is None


def test_c1_api_returns_200_for_a_3_1_spec_and_for_a_deeply_nested_one(monkeypatch):
    spec = json.dumps(spec_with_type(["string", "null"]))
    c, _, rid, _ = audited(monkeypatch, extra={"/openapi.json": spec})
    r = c.post(f"/report/{rid}/fixes")
    assert r.status_code == 200 and "mcp_stub" in {f["id"] for f in r.json()["fixes"]}
    deep = '{"openapi": "3.0.0", "x": ' + "[" * 100_000 + "]" * 100_000 + "}"
    c, _, rid, _ = audited(monkeypatch, extra={"/openapi.json": deep})
    r = c.post(f"/report/{rid}/fixes")
    assert r.status_code == 200 and {s["kind"] for s in r.json()["skipped"]} == {"mcp_stub"}


def test_c1_a_crash_in_propose_becomes_three_generic_skips_and_retries_work(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)

    def boom(report, baseline):
        raise RuntimeError("boom with a secret AKIAABCDEFGHIJKLMNOP")

    monkeypatch.setattr(main, "propose_fixes", boom)
    r = c.post(f"/report/{rid}/fixes")
    assert r.status_code == 200 and r.json()["fixes"] == []
    assert {s["kind"] for s in r.json()["skipped"]} == {"llms_txt", "json_ld", "mcp_stub"}
    assert {s["reason"] for s in r.json()["skipped"]} == {"could not be generated"}
    assert "boom" not in r.text and "AKIA" not in r.text
    assert c.post(f"/report/{rid}/fixes").status_code == 200 and c.get(f"/report/{rid}/fixes").status_code == 200


def test_c1_nested_anchors_do_not_make_proposal_quadratic():
    html = HEAD + "<a href='/x'>link" * 20_000 + "</a>" * 20_000 + "</body></html>"
    started = time.perf_counter()
    p = propose_fixes(EMPTY, Baseline(O, {"/": (200, html)}))
    assert time.perf_counter() - started < 5
    assert [f.id for f in p.fixes] == ["llms_txt", "json_ld"] or p.skipped


@pytest.mark.parametrize("html", ["x" * 500_001, HEAD + "<p>" * 120_000])
def test_i5_pages_beyond_the_size_or_tag_cap_are_skipped(html):
    p = propose_fixes(EMPTY, Baseline(O, {"/": (200, html)}))
    reasons = {s.kind: s.reason for s in p.skipped}
    assert p.fixes == [] and reasons[FixKind.LLMS_TXT] == "page too large to process"
    assert reasons[FixKind.JSON_LD] == "page too large to process"


def test_a_baseline_failure_does_not_lose_the_report(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(main.Baseline, "from_recorder", classmethod(broken))
    c, _, rid, body = audited(monkeypatch)
    assert body["id"] == rid and c.get(f"/report/{rid}").status_code == 200
    r = c.post(f"/report/{rid}/fixes")
    assert r.status_code == 200 and r.json()["fixes"] == []
    assert {s["reason"] for s in r.json()["skipped"]} == {"baseline unavailable"}


def test_a_missing_baseline_is_a_404_not_a_500(monkeypatch):
    c, _, rid, _ = audited(monkeypatch)
    main._baselines.pop(rid)
    assert c.post(f"/report/{rid}/fixes").status_code == 404


def run_in_thread(fn, *args):
    out = {}

    def target():
        try:
            out["value"] = fn(*args)
        except BaseException as e:      # noqa: BLE001 - reported to the test
            out["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, out


def slow_for(monkeypatch, slow_report):
    started, release = threading.Event(), threading.Event()
    real = main.propose_fixes

    def slow(report, baseline):
        if report is slow_report:
            started.set()
            release.wait(10)
        return real(report, baseline)

    monkeypatch.setattr(main, "propose_fixes", slow)
    return started, release


def test_i5_a_slow_proposal_for_one_report_does_not_block_another(monkeypatch):
    c, _, first, _ = audited(monkeypatch)
    second = c.post("/audit", json={"url": B}).json()["id"]
    started, release = slow_for(monkeypatch, main._reports[first])
    slow, slow_out = run_in_thread(main.post_fixes, first)
    assert started.wait(5)
    fast, fast_out = run_in_thread(main.post_fixes, second)
    fast.join(5)
    assert not fast.is_alive() and "value" in fast_out          # finished while the slow one is still working
    reader, read_out = run_in_thread(main.get_report, second)
    reader.join(5)
    assert not reader.is_alive()
    release.set()
    slow.join(10)
    assert "value" in slow_out


def test_i5_a_report_evicted_during_a_proposal_leaves_no_orphan_queue(monkeypatch):
    monkeypatch.setattr(main, "MAX_REPORTS", 1)
    c, _, first, _ = audited(monkeypatch)
    started, release = slow_for(monkeypatch, main._reports[first])
    slow, out = run_in_thread(main.post_fixes, first)
    assert started.wait(5)
    c.post("/audit", json={"url": B})                            # evicts the first report while it is being proposed
    assert first not in main._reports
    release.set()
    slow.join(10)
    assert isinstance(out.get("error"), HTTPException) and out["error"].status_code == 404
    assert first not in main._proposals and first not in main._baselines
