"""Run and save a preregistered deterministic audit for the check-accuracy benchmark.

Run from ``backend`` with ``python -m app.runner.audit_cli --label ../benchmark/labels/example.test.json
--snapshot before``. Ownership must be proven before the audit fetches pages.
"""
import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from ..audit import audit, parse_url
from ..fetch import is_public
from .cli import SECRET_MISSING, REPO_ROOT, default_deps, git_problems
from .labels import load_label
from .results import valid_snapshot

SNAPSHOT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def report_path(root: Path, snapshot: str, host: str) -> Path:
    slug = re.sub(r"[^a-z0-9._-]", "_", host.lower())
    return Path(root) / "audits" / snapshot / f"{slug}.json"


def directory_problem(root, snapshot: str) -> str | None:
    """The audit directories must be real directories: a symlink would send the report somewhere else."""
    for part in (Path(root) / "audits", Path(root) / "audits" / snapshot):
        if os.path.islink(part):
            return f"{part.name} inside the results directory is a symlink"
    return None


def save_audit(root: Path, snapshot: str, site: str, label_sha256: str, report) -> Path:
    """Persist the redacted audit output immutably; audit run files are never overwritten."""
    path = report_path(root, snapshot, site)
    problem = directory_problem(root, snapshot)
    if problem:
        raise FileExistsError(problem)
    path.parent.mkdir(parents=True, exist_ok=True)
    problem = directory_problem(root, snapshot)                      # checked again after the directories exist
    if problem:
        raise FileExistsError(problem)
    report_data = asdict(report)
    origin = report_data.pop("site")
    payload = {"site": site, "origin": origin, "snapshot": snapshot,
               "label_sha256": label_sha256, **report_data}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "x", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def main(argv=None, *, deps=None, out=print, repo_root=REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.runner.audit_cli")
    parser.add_argument("--label", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--results", type=Path, default=Path(repo_root) / "benchmark" / "results")
    args = parser.parse_args(argv)
    if not valid_snapshot(args.snapshot):
        out("Refused: the snapshot name must match [A-Za-z0-9_-]{1,64} and may not be 'audits' (reserved).")
        return 2
    problem = directory_problem(args.results, args.snapshot)
    if problem:
        out(f"Refused: {problem}; no audit was run.")
        return 2
    try:
        label_path = Path(args.label)
        label_digest = hashlib.sha256(label_path.read_bytes()).hexdigest()
        label = load_label(label_path, check_name=True)
    except (OSError, ValueError) as exc:
        out(f"Refused: {exc}")
        return 2
    if label.audit_checks_for(args.snapshot) is None:
        out("Refused: commit complete audit_checks pass/fail labels for this snapshot before running this audit.")
        return 2
    problems = git_problems([label_path], repo_root=repo_root)
    if problems:
        out("Refused: the audit labels must be committed and unchanged before any run (" + "; ".join(problems) + ").")
        return 2
    if hashlib.sha256(label_path.read_bytes()).hexdigest() != label_digest:
        out("Refused: the site label changed while it was being read; no audit was run.")
        return 2
    target = report_path(args.results, args.snapshot, label.host)
    if target.exists():
        out("Refused: an audit report already exists for this site and snapshot; reports are never overwritten.")
        return 2
    try:
        ref = parse_url(label.site)
        deps = deps or default_deps()
    except RuntimeError as exc:
        out(SECRET_MISSING if "AGENTREADY_SECRET" in str(exc) else f"Refused: could not set up ({type(exc).__name__}).")
        return 2
    except Exception as exc:
        out(f"Audit failed ({type(exc).__name__}); no report was recorded.")
        return 2
    try:
        addrs = deps.resolver(ref.hostname)
        if not addrs or not all(is_public(address) for address in addrs):
            out("Refused: the host does not resolve to public addresses, so it cannot be audited.")
            return 2
        if not deps.verify(ref):
            out("Refused: control of the host is not proven; the audit did not fetch pages.")
            return 2
        fetcher = deps.make_fetcher(ref.netloc, args.snapshot)
        report = audit(ref.origin, fetcher)
        report.verification_request_log = getattr(deps, "verification_log", lambda: None)()
    except Exception as exc:  # no page or token text is included in exception output
        out(f"Audit failed ({type(exc).__name__}); no report was recorded.")
        return 2
    if hashlib.sha256(label_path.read_bytes()).hexdigest() != label_digest:
        out("Refused: the site label changed during the audit; no result was saved.")
        return 2
    try:
        path = save_audit(args.results, args.snapshot, label.host, label_digest, report)
    except FileExistsError:
        out("Refused: an audit report already exists for this site and snapshot; reports are never overwritten.")
        return 2
    out(f"Recorded {label.host} {args.snapshot}: {len(report.findings)} findings at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
