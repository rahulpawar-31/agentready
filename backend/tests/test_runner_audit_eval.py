import hashlib
import json

from app.runner import audit_cli
from app.runner.audit_cli import main as audit_main, report_path, save_audit
from app.runner.labels import AUDIT_CHECK_IDS
from app.runner.report import build_report, to_markdown
from app.models import Category, Confidence, Finding, Page, Report, Status


TASKS = {
    "main_info": {"urls": ["https://x.test/pricing"]},
    "contact_form": {"urls": ["https://x.test/contact"], "required_fields": ["email"]},
    "docs_api": {"none_exists": True},
}
AUDIT_EXPECTED = {check: True for check in AUDIT_CHECK_IDS}
AUDIT_EXPECTED.update({"sitemap": False, "json_ld": False})


def make_label(labels_dir, audit_checks=AUDIT_EXPECTED):
    labels_dir.mkdir(parents=True, exist_ok=True)
    path = labels_dir / "x.test.json"
    value = {"site": "https://x.test", "tasks": TASKS}
    if audit_checks is not None:
        value["audit_checks"] = audit_checks
    path.write_text(json.dumps(value))
    return path


def audit_findings():
    overrides = {
        "robots_ai": (False, "detected"),   # false positive: expected pass
        "sitemap": (False, "detected"),     # true positive: expected fail
        "json_ld": (True, "not detected"),  # false negative: expected fail
        "env_not_exposed": (False, "unreachable"),
    }
    return [
        {"check_id": check, "passed": overrides.get(check, (True, "not detected"))[0],
         "status": overrides.get(check, (True, "not detected"))[1],
         "url": "https://x.test/", "evidence": "fixture", "check": check,
         "confidence": "high", "weight": 1}
        for check in AUDIT_CHECK_IDS
    ]


def write_audit(results, snapshot, label_path, findings=None):
    digest = hashlib.sha256(label_path.read_bytes()).hexdigest()
    body = {"site": "x.test", "origin": "https://x.test", "snapshot": snapshot, "label_sha256": digest,
            "findings": findings if findings is not None else audit_findings()}
    target = report_path(results, snapshot, "x.test")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body))
    return target


def test_report_computes_per_check_accuracy_and_excludes_unreachable(tmp_path):
    label = make_label(tmp_path / "labels")
    results = tmp_path / "results"
    write_audit(results, "before", label)
    report = build_report(results, tmp_path / "labels", "before", "after")
    before = report["audit_check_accuracy"]["before"]
    assert before["status"] == "measured"
    assert before["sites_with_reports"] == 1
    assert before["checks"]["robots_ai"] == {
        "true_positive": 0, "false_positive": 1, "true_negative": 0, "false_negative": 0,
        "unreachable": 0, "missing": 0, "support": 1,
        "agreement_pct": 0.0, "false_positive_rate_pct": 100.0,
    }
    assert before["checks"]["sitemap"]["true_positive"] == 1
    assert before["checks"]["json_ld"]["false_negative"] == 1
    env = before["checks"]["env_not_exposed"]
    assert env["unreachable"] == 1 and env["support"] == 0
    assert env["agreement_pct"] is None and env["false_positive_rate_pct"] is None
    markdown = to_markdown(report)
    assert "False-positive rate" in markdown and "robots_ai" in markdown
    assert "Unreachable" in markdown


def test_missing_reports_and_missing_findings_are_explicit(tmp_path):
    make_label(tmp_path / "labels")
    report = build_report(tmp_path / "results", tmp_path / "labels", "before", "after")
    before = report["audit_check_accuracy"]["before"]
    assert before["status"] == "not measured"
    assert before["missing_reports"] == ["x.test"]
    assert before["checks"]["robots_ai"]["missing"] == 1

    label = make_label(tmp_path / "labels")
    write_audit(tmp_path / "results", "after", label, findings=[])
    after = build_report(tmp_path / "results", tmp_path / "labels", "before", "after")["audit_check_accuracy"]["after"]
    assert after["checks"]["robots_ai"]["missing"] == 1


def test_changed_preregistered_label_excludes_audit_result_and_reports_problem(tmp_path):
    label = make_label(tmp_path / "labels")
    write_audit(tmp_path / "results", "before", label)
    edited = dict(AUDIT_EXPECTED, robots_ai=False)
    make_label(tmp_path / "labels", edited)
    report = build_report(tmp_path / "results", tmp_path / "labels", "before", "after")
    assert report["audit_check_accuracy"]["before"]["sites_with_reports"] == 0
    assert any("label changed" in problem for problem in report["problems"])


def test_task_run_with_a_changed_label_hash_is_flagged_and_never_paired(tmp_path):
    label = make_label(tmp_path / "labels")
    result_dir = tmp_path / "results" / "before" / "x.test"
    result_dir.mkdir(parents=True)
    row = {"site": "x.test", "task": "main_info", "repeat": 1,
           "result": {"valid": True, "success": True, "reason": "ok"},
           "config": {"label_sha256": "old-label-hash"}}
    (result_dir / "main_info-1.json").write_text(json.dumps(row))
    report = build_report(tmp_path / "results", tmp_path / "labels", "before", "after")
    # The detailed report retains the original run in the recorded snapshot totals, but marks it
    # flawed so it cannot contribute to the paired before/after estimate.
    assert report["before"]["valid_runs"] == 1
    assert any("label hash" in problem for problem in report["problems"]), report["problems"]


def test_audit_snapshot_save_is_immutable(tmp_path):
    report = Report("https://x.test", 100, {}, [Finding(
        "robots_ai", Category.DISCOVERABILITY, True, 1, "check", "evidence", "https://x.test/",
        Confidence.HIGH, Status.NOT_DETECTED)], 0)
    saved = save_audit(tmp_path, "before", "x.test", "abc", report)
    loaded = json.loads(saved.read_text())
    assert loaded["site"] == "x.test" and loaded["snapshot"] == "before"
    assert loaded["findings"][0]["status"] == "not detected"
    try:
        save_audit(tmp_path, "before", "x.test", "abc", report)
    except FileExistsError:
        pass
    else:
        raise AssertionError("audit save overwrote an existing snapshot")


def test_audit_cli_requires_preregistered_labels_before_verification(tmp_path):
    labels_dir = tmp_path / "benchmark" / "labels"
    label = make_label(labels_dir, audit_checks=None)
    calls = []

    class Deps:
        verify = staticmethod(lambda ref: calls.append("verify") or True)
        resolver = staticmethod(lambda host: calls.append("resolve") or ["93.184.216.34"])
        make_fetcher = staticmethod(lambda *args: calls.append("fetch") or object())

    output = []
    code = audit_main(["--label", str(label), "--snapshot", "before",
                       "--results", str(tmp_path / "results")], deps=Deps, out=output.append,
                      repo_root=tmp_path)
    assert code == 2 and not calls
    assert "audit_checks" in output[0]


def test_audit_cli_verifies_control_then_saves_one_guarded_snapshot(tmp_path, monkeypatch):
    labels_dir = tmp_path / "benchmark" / "labels"
    label = make_label(labels_dir)
    calls = []

    class FakeFetcher:
        def get(self, url, *, rendered=False):
            calls.append(url)
            return Page(url, 404, {}, "")

    class Deps:
        verify = staticmethod(lambda ref: calls.append(("verify", ref.netloc)) or True)
        resolver = staticmethod(lambda host: calls.append(("resolve", host)) or ["93.184.216.34"])
        make_fetcher = staticmethod(lambda host, snapshot: calls.append(("fetcher", host, snapshot)) or FakeFetcher())

    monkeypatch.setattr(audit_cli, "git_problems", lambda paths, repo_root: [])
    output = []
    results = tmp_path / "results"
    code = audit_main(["--label", str(label), "--snapshot", "before",
                       "--results", str(results)], deps=Deps, out=output.append, repo_root=tmp_path)
    assert code == 0
    saved = json.loads(report_path(results, "before", "x.test").read_text())
    assert saved["site"] == "x.test" and saved["origin"] == "https://x.test"
    assert len(saved["findings"]) == len(AUDIT_CHECK_IDS)
    assert calls[0] == ("resolve", "x.test") and calls[1] == ("verify", "x.test")
    assert all(url.startswith("https://x.test/") for url in calls if isinstance(url, str))

