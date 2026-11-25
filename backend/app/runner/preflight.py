"""Benchmark site registry and pre-flight: say exactly what is still missing before the benchmark can be run.

Run from ``backend`` with ``python -m app.runner.preflight`` (offline) or ``... --check-control`` (also performs the real
ownership verification for each listed site, the same one the audit and the runner use).

The pre-flight never prints a secret (it only checks that ANTHROPIC_API_KEY, or GEMINI_API_KEY for a gemini-... model, is set), never calls a model, never writes a
file and, unless --check-control is given, makes no network request.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..audit import SiteRef, parse_url
from ..fetch import is_public
from .cli import GIT_MISSING, SNAPSHOT, git_problems, load_criteria
from .gemini import MODEL_PATTERN as GEMINI_MODEL, min_interval_from_env
from .labels import load_label
from .llm import DEFAULT_MODEL
from .results import is_valid_result, run_path, valid_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_SITES = 20
TARGET_SITES = 5
OWNER_TYPES = ("team", "permission")
VERIFICATIONS = ("dns", "meta")
AFTER_METHODS = ("staging", "live")
ENTRY_KEYS = {"host", "owner_type", "verification", "label", "after_eligible", "after_method", "permission_note",
              "notes", "_comment"}
LABEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
DEFAULT_PORTS = (80, 443)          # the registry means https origins: the bare host is the only canonical spelling
EMPLOYER_SITES = re.compile(r"countrystatecity|makemysitelive", re.I)
# The permission note is the owner's own attestation. These checks only refuse a note that plainly says the opposite.
NEGATED_PERMISSION = re.compile(
    r"\b(no permission|not permitted|without permission|denied|refused|do not have|don't have|not allowed|"
    r"no written|test only|only (to )?test|nothing to (change|deploy)|no (change|deploy))\b", re.I)


@dataclass(frozen=True)
class Site:
    host: str
    owner_type: str
    verification: str
    label: str
    after_eligible: bool = False
    after_method: str = ""
    permission_note: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Check:
    id: str
    status: str                  # ok | blocked | warn | skipped
    message: str
    site: str = ""


@dataclass
class Preflight:
    checks: list = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(c.status == "blocked" for c in self.checks)


@dataclass
class Deps:
    committed: Callable[[list], list]            # paths -> problems (the same check the runner uses)
    sdk_installed: Callable[[], bool]
    verify: Callable[[SiteRef], bool]            # real ownership verification (only used with --check-control)


def _slug(host: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", host.lower())


def _short(value, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _site_problems(entry: dict, index: int) -> tuple[Site | None, list[str]]:
    where = f"sites[{index}]"
    problems: list[str] = []
    for key in sorted(set(entry) - ENTRY_KEYS):
        problems.append(f"{where}: unexpected field '{_short(key, 40)}'")
    host = entry.get("host")
    if not isinstance(host, str) or not host:
        return None, problems + [f"{where}: host is required (host or host:port)"]
    where = f"sites[{index}] {_short(host, 80)}"
    ref = None
    try:
        ref = parse_url("https://" + host)
    except ValueError as exc:
        problems.append(f"{where}: host is not valid ({exc})")
    if ref is not None:
        if ref.netloc != host:
            problems.append(f"{where}: write the host in lower case as host[:port], for example {ref.netloc}")
        name = ref.hostname
        if name == "localhost" or name.endswith(PRIVATE_SUFFIXES):
            problems.append(f"{where}: host {name} is not a public name and cannot be audited")
        if ref.is_ip:
            problems.append(f"{where}: IP addresses are not accepted for benchmark sites; register the public host name")
        else:
            last = name.rsplit(".", 1)[-1]
            if "." not in name or not re.search(r"[a-z]", last) or re.fullmatch(r"0x[0-9a-f]+", last):
                problems.append(f"{where}: host {name} must be a public host name with a dot and a non-numeric last "
                                f"label, for example demo.example.org")
        if ref.port in DEFAULT_PORTS:
            problems.append(f"{where}: remove the default port: the registry means https origins, so write the bare "
                            f"host (or a non-default port)")
    owner = entry.get("owner_type")
    if owner not in OWNER_TYPES:
        problems.append(f"{where}: owner_type must be one of {', '.join(OWNER_TYPES)}")
    verification = entry.get("verification")
    if verification not in VERIFICATIONS:
        problems.append(f"{where}: verification must be one of {', '.join(VERIFICATIONS)}")
    elif verification == "dns" and ref is not None and not ref.dns_allowed:
        problems.append(f"{where}: dns verification is not available for a host with a port or an IP address; use meta")
    note = entry.get("permission_note", "")
    if not isinstance(note, str):
        problems.append(f"{where}: permission_note must be a string")
        note = ""
    if "notes" in entry and not isinstance(entry["notes"], str):
        problems.append(f"{where}: notes must be text")
    if owner == "permission" and (len(note.strip()) < 10 or len(re.findall(r"\w+", note)) < 3):
        problems.append(f"{where}: a permission site needs a permission_note (at least 10 characters and 3 words) "
                        f"pointing to the written OK")
    if (EMPLOYER_SITES.search(host) or EMPLOYER_SITES.search(note)) and not (
            re.search(r"\bemployer\b", note, re.I) and re.search(r"\bwritten\b", note, re.I)):
        problems.append(f"{where}: an employer site (CountryStateCity or MakeMySiteLive) needs a permission_note that "
                        f"records the employer's written OK (the words \"employer\" and \"written\")")
    label = entry.get("label")
    expected_label = f"{_slug(host)}.json"
    if not isinstance(label, str) or not LABEL_NAME.match(label) or ".." in label:
        problems.append(f"{where}: label must be a file name such as {expected_label} inside benchmark/labels")
    elif label != expected_label:
        problems.append(f"{where}: label must be named {expected_label} (the runner requires the host name)")
    eligible = entry.get("after_eligible", False)
    if not isinstance(eligible, bool):
        problems.append(f"{where}: after_eligible must be true or false")
        eligible = False
    method = entry.get("after_method") or ""
    if eligible and method not in AFTER_METHODS:
        problems.append(f"{where}: after_method must be staging or live when after_eligible is true")
    if not eligible and method not in ("",) + AFTER_METHODS:
        problems.append(f"{where}: after_method must be staging or live")
    if eligible and owner == "permission" and not (
            re.search(r"\bpermission\b", note, re.I) and re.search(r"\b(change|deploy)\w*", note, re.I)
            and not NEGATED_PERMISSION.search(note)):
        problems.append(f"{where}: after_eligible for a permission site needs a permission_note that records "
                        f"permission to change or deploy, not only to test (the note is your own attestation; "
                        f"it is not verified)")
    if problems:
        return None, problems
    return Site(host, owner, verification, label, eligible, method, note.strip(), str(entry.get("notes") or "")), []


def load_sites(path) -> tuple[list[Site], list[str]]:
    """Read the registry. Problems are returned, never raised."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, RecursionError):                 # RecursionError: a deeply nested file
        return [], ["the site registry could not be read as JSON"]
    if not isinstance(data, dict):
        return [], ["the site registry must be a JSON object"]
    if type(data.get("version")) is not int or data.get("version") != 1:
        return [], ["the site registry needs \"version\": 1"]
    entries = data.get("sites")
    if not isinstance(entries, list):
        return [], ["the site registry needs a \"sites\" list"]
    sites: list[Site] = []
    problems: list[str] = []
    seen: set[str] = set()
    if len(entries) > MAX_SITES:
        problems.append(f"more than {MAX_SITES} sites are registered; the plan expands toward {MAX_SITES} only if "
                        f"authorization, labelling and caching stay manageable")
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"sites[{i}]: each site must be an object")
            continue
        site, entry_problems = _site_problems(entry, i)
        problems.extend(entry_problems)
        if site is None:
            continue
        if site.host in seen:
            problems.append(f"sites[{i}] {site.host}: duplicate host")
            continue
        seen.add(site.host)
        sites.append(site)
    return sites, problems


def _result_files(results: Path, snapshot: str, host: str) -> list[Path]:
    """Files that record a VALID run. Moved-aside runs (.invalid-<k>) and files that record an invalid run do not
    count: those are exactly what a rerun in the same snapshot redoes."""
    directory = run_path(results, snapshot, host, "x", 1).parent
    if not directory.is_dir():
        return []
    return [p for p in sorted(directory.glob("*.json"))
            if ".invalid-" not in p.name and not p.is_symlink() and is_valid_result(p)]


def _committed(deps, paths) -> list[str]:
    """deps.committed, but a missing git is one blocked message and not a traceback."""
    try:
        return list(deps.committed(paths))
    except (OSError, subprocess.SubprocessError):
        return [GIT_MISSING]


def run_preflight(repo_root, env, deps: Deps, *, check_control: bool = False, before: str = "before",
                  after: str = "after", allow_existing: bool = False) -> Preflight:
    root = Path(repo_root)
    bench = root / "benchmark"
    result = Preflight()

    def add(check_id, status, message, site=""):
        result.checks.append(Check(check_id, status, _short(message, 300), site))

    criteria = bench / "criteria.json"
    problems = _committed(deps, [criteria])
    add("criteria_committed", "blocked" if problems else "ok",
        "; ".join(problems) if problems else "benchmark/criteria.json is committed and unmodified")
    try:
        load_criteria(criteria)
        add("harness_hash", "ok", "the criteria file matches the code's current harness hash")
    except ValueError as exc:
        add("harness_hash", "blocked", f"{exc}. Regenerate and commit the criteria before any run")
    model = str(env.get("AGENTREADY_MODEL") or DEFAULT_MODEL).strip()
    gemini = model.startswith("gemini-")
    key_name = "GEMINI_API_KEY" if gemini else "ANTHROPIC_API_KEY"
    if gemini and not GEMINI_MODEL.fullmatch(model):
        add("model", "blocked", "AGENTREADY_MODEL is not a valid Gemini model name (for example gemini-3.5-flash-lite)")
    else:
        add("model", "ok", f"model: {_short(model, 80)} (every before and after run must use this same model)")
    key = env.get(key_name)
    if isinstance(key, str) and key.strip():
        add("api_key_present", "ok", f"{key_name} is set (its value is never shown)")
    else:
        add("api_key_present", "blocked", f"{key_name} is not set in the environment")
    if gemini:
        try:
            min_interval_from_env(env)
            add("sdk_installed", "ok", "a Gemini model needs no SDK (it is called over HTTPS with httpx)")
        except ValueError as exc:
            add("sdk_installed", "blocked", str(exc))
    elif deps.sdk_installed():
        add("sdk_installed", "ok", "the anthropic SDK is installed")
    else:
        add("sdk_installed", "blocked", "the anthropic SDK is not installed; run: pip install -e '.[runner]'")

    sites, registry_problems = load_sites(bench / "sites.json")
    registry_problems = registry_problems + _committed(deps, [bench / "sites.json"])
    if registry_problems:
        add("sites_registry", "blocked", "; ".join(registry_problems[:5]) +
            (f" (+{len(registry_problems) - 5} more)" if len(registry_problems) > 5 else ""))
    else:
        add("sites_registry", "ok", "benchmark/sites.json loads without problems")
    n = len(sites)
    if n == 0:
        add("site_count", "blocked", "no benchmark sites registered: add at least one to benchmark/sites.json "
            f"(the target is {TARGET_SITES})")
    elif n > MAX_SITES:
        add("site_count", "blocked", f"{n} sites registered; the maximum is {MAX_SITES}")
    elif n < TARGET_SITES:
        add("site_count", "warn", f"{n} of the target {TARGET_SITES} sites are registered")
    else:
        add("site_count", "ok", f"{n} sites registered (target {TARGET_SITES})")

    labels_dir = bench / "labels"
    results = bench / "results"
    for site in sites:
        label_path = labels_dir / site.label
        label = None
        if not label_path.is_file():
            add("label_valid_and_committed", "blocked", f"benchmark/labels/{site.label} does not exist", site.host)
        else:
            try:
                label = load_label(label_path, check_name=True)
                committed = _committed(deps, [label_path])
                if committed:
                    add("label_valid_and_committed", "blocked", "; ".join(committed), site.host)
                else:
                    add("label_valid_and_committed", "ok", f"{site.label} is valid, committed and unmodified", site.host)
            except ValueError as exc:
                add("label_valid_and_committed", "blocked", f"{site.label} is not a valid label: {exc}", site.host)
        if label is None:
            add("label_names_match", "skipped", "no valid label to compare", site.host)
        elif label.host != site.host:
            add("label_names_match", "blocked", f"the label is for {label.host}, not {site.host}", site.host)
        else:
            add("label_names_match", "ok", "the label's host equals the registered host", site.host)

        if not check_control:
            add("control_proven", "skipped", "not checked: run with --check-control to verify ownership", site.host)
        else:
            try:
                proven = deps.verify(parse_url("https://" + site.host))
            except Exception as exc:                     # noqa: BLE001 - the detail could carry anything
                add("control_proven", "blocked", f"the verification could not be run ({type(exc).__name__}); "
                    f"set AGENTREADY_SECRET and check the network", site.host)
            else:
                if proven:
                    add("control_proven", "ok", "control of the host is proven", site.host)
                else:
                    add("control_proven", "blocked", f"control is not proven: add the {site.verification} proof "
                        f"(GET /ownership-token?domain={site.host})", site.host)

        for snapshot, needed in ((before, True), (after, site.after_eligible)):
            if not needed:
                continue
            existing = _result_files(results, snapshot, site.host)
            if existing and allow_existing:
                add("results_dirs_free", "warn", f"snapshot '{snapshot}' already holds {len(existing)} valid result(s); "
                    f"they are kept and only missing runs are made", site.host)
            elif existing:
                add("results_dirs_free", "blocked", f"snapshot '{snapshot}' already holds {len(existing)} valid result(s) "
                    f"for this site. Valid results are never overwritten, so a rerun only makes the missing or invalid "
                    f"runs: pass --allow-existing to continue this snapshot, or use a new snapshot name for a new "
                    f"measurement", site.host)
        if not any(c.id == "results_dirs_free" and c.site == site.host for c in result.checks):
            add("results_dirs_free", "ok", "no earlier results in the planned snapshots", site.host)

        if not site.after_eligible:
            add("after_plan", "ok", "before-only: the site is reported but gets no after run", site.host)
        elif site.owner_type == "permission":
            add("after_plan", "warn", "confirm the written permission to change the site is on file (see permission_note) "
                f"before the after run; use --snapshot {after}", site.host)
        else:
            add("after_plan", "ok", f"after run: the owner deploys the approved files ({site.after_method}), then "
                f"re-audit and run with --snapshot {after}", site.host)

    eligible = sum(1 for s in sites if s.after_eligible)
    if eligible == 0:
        add("after_eligible_count", "warn", "no site can have an after run; the before and after comparison and the "
            "demo need at least one team-controlled site (or written permission to change one)")
    else:
        add("after_eligible_count", "ok", f"{eligible} site(s) can have an after run")
    return result


def to_dict(result: Preflight) -> dict:
    return {"blocked": result.blocked,
            "checks": [{"id": c.id, "site": c.site, "status": c.status, "message": c.message} for c in result.checks]}


def to_text(result: Preflight) -> str:
    lines = []
    for c in result.checks:
        where = f" ({c.site})" if c.site else ""
        lines.append(f"[{c.status}] {c.id}{where}: {c.message}")
    blocked = [c for c in result.checks if c.status == "blocked"]
    lines.append("")
    lines.append(f"Blocked: {len(blocked)} check(s) must be fixed before the benchmark can run." if blocked
                 else "Nothing blocks the benchmark. Warnings and skipped checks above still need a look.")
    return "\n".join(lines)


def default_deps(repo_root=REPO_ROOT) -> Deps:
    def verify(ref: SiteRef) -> bool:
        from . import cli                       # imports app.main, which needs AGENTREADY_SECRET
        return cli.default_deps().verify(ref)

    return Deps(committed=lambda paths: git_problems(paths, repo_root),
                sdk_installed=lambda: importlib.util.find_spec("anthropic") is not None, verify=verify)


def main(argv=None, deps: Deps | None = None, env=None, out=print, repo_root=REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.runner.preflight",
                                     description="Check what is still missing before the benchmark can run.")
    parser.add_argument("--check-control", action="store_true",
                        help="also verify ownership of each listed site (real DNS and one home-page request each)")
    parser.add_argument("--snapshot-before", default="before")
    parser.add_argument("--snapshot-after", default="after")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if not valid_snapshot(args.snapshot_before) or not valid_snapshot(args.snapshot_after):
        parser.error("snapshot names must match [A-Za-z0-9_-]{1,64}")
    if args.snapshot_before.casefold() == args.snapshot_after.casefold():
        parser.error("--snapshot-before and --snapshot-after must differ")
    result = run_preflight(repo_root, os.environ if env is None else env, deps or default_deps(repo_root),
                           check_control=args.check_control, before=args.snapshot_before, after=args.snapshot_after,
                           allow_existing=args.allow_existing)
    out(json.dumps(to_dict(result), indent=2) if args.format == "json" else to_text(result))
    return 1 if result.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
