"""Tests for the benchmark site registry and the offline pre-flight."""
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from app.runner import preflight as pf
from app.runner.preflight import Deps, load_sites, main, run_preflight

REPO = Path(__file__).resolve().parents[2]
SECRET_KEY = "sk-ant-SECRET-VALUE-1234567890"


def site(host="demo.example.org", **over):
    entry = {"host": host, "owner_type": "team", "verification": "meta", "label": f"{host}.json",
             "after_eligible": True, "after_method": "staging", "permission_note": "", "notes": ""}
    entry.update(over)
    return entry


def label_json(host):
    return json.dumps({"site": f"https://{host}", "tasks": {
        "main_info": {"urls": [f"https://{host}/p"]},
        "contact_form": {"urls": [f"https://{host}/c"], "required_fields": ["email"]},
        "docs_api": {"none_exists": True}}})


def make_repo(tmp_path, sites=None, *, labels=True, version=1, criteria=True):
    root = tmp_path / "repo"
    (root / "benchmark" / "labels").mkdir(parents=True)
    (root / "benchmark" / "results").mkdir()
    if criteria:
        shutil.copy(REPO / "benchmark" / "criteria.json", root / "benchmark" / "criteria.json")
    entries = [site()] if sites is None else sites
    (root / "benchmark" / "sites.json").write_text(json.dumps({"version": version, "sites": entries}))
    if labels:
        for e in entries:
            if isinstance(e, dict) and isinstance(e.get("host"), str):
                (root / "benchmark" / "labels" / f"{e['host']}.json").write_text(label_json(e["host"]))
    return root


class Fakes:
    def __init__(self, committed=None, sdk=True, verify=True):
        self.committed_problems = committed or {}
        self.sdk = sdk
        self.verify_result = verify
        self.verified = []

    def deps(self):
        def committed(paths):
            return [p for path in paths for p in self.committed_problems.get(Path(path).name, [])]

        def verify(ref):
            self.verified.append(ref.netloc)
            if isinstance(self.verify_result, Exception):
                raise self.verify_result
            return self.verify_result
        return Deps(committed=committed, sdk_installed=lambda: self.sdk, verify=verify)


def run(root, fakes=None, env=None, **kw):
    fakes = fakes or Fakes()
    env = {"ANTHROPIC_API_KEY": SECRET_KEY} if env is None else env
    return run_preflight(root, env, fakes.deps(), **kw)


def statuses(result):
    return {(c.id, c.site): c.status for c in result.checks}


# registry ------------------------------------------------------------------------------------------------
def write_registry(tmp_path, data):
    p = tmp_path / "sites.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


@pytest.mark.parametrize("entry,fragment", [
    (site(owner_type="friend"), "owner_type"),
    (site(owner_type="permission", permission_note="", after_eligible=False, after_method=""), "permission_note"),
    (site(owner_type="permission", permission_note="short", after_eligible=False, after_method=""), "at least 10"),
    (site(owner_type="permission", permission_note="Email from the owner dated 20 Nov", after_eligible=True), "change"),
    (site(verification="smoke"), "verification"),
    (site("demo.example.org:8443", verification="dns", label="demo.example.org_8443.json"), "port"),
    (site(label="sub/dir.json"), "label"),
    (site(label="../x.json"), "label"),
    (site(label="other.json"), "label"),
    (site(label=""), "label"),
    (site(after_eligible=True, after_method=""), "after_method"),
    (site(after_eligible=True, after_method="production"), "after_method"),
    (site(after_eligible="yes"), "after_eligible"),
    (site("Demo.Example.ORG"), "lower case"),
    (site("not a host"), "host"),
    (site("localhost", label="localhost.json"), "host"),
    (site("127.0.0.1", label="127.0.0.1.json"), "host"),
    (site("10.0.0.5", label="10.0.0.5.json"), "host"),
    (site("x.local", label="x.local.json"), "host"),
    (site(unexpected="x"), "unexpected"),
    ("not an object", "object"),
])
def test_each_registry_rule_reports_a_problem_and_never_raises(tmp_path, entry, fragment):
    sites, problems = load_sites(write_registry(tmp_path, {"version": 1, "sites": [entry]}))
    assert any(fragment in p for p in problems), problems
    assert sites == [] or problems


def test_a_valid_registry_loads_without_problems(tmp_path):
    entries = [site(), site("docs.example.net", owner_type="permission", verification="dns",
                            permission_note="Written permission from the owner, 20 Nov, to test and deploy a change",
                            after_eligible=True, after_method="live"),
               site("before-only.example.com", after_eligible=False, after_method="")]
    sites, problems = load_sites(write_registry(tmp_path, {"version": 1, "sites": entries}))
    assert problems == [] and [s.host for s in sites] == ["demo.example.org", "docs.example.net", "before-only.example.com"]
    assert sites[1].owner_type == "permission" and sites[2].after_eligible is False


def test_duplicate_hosts_and_too_many_sites_are_problems(tmp_path):
    _, problems = load_sites(write_registry(tmp_path, {"version": 1, "sites": [site(), site()]}))
    assert any("duplicate" in p for p in problems)
    many = [site(f"s{i}.example.org") for i in range(21)]
    _, problems = load_sites(write_registry(tmp_path, {"version": 1, "sites": many}))
    assert any("more than 20" in p for p in problems)


@pytest.mark.parametrize("text", ["{nope", "[]", '{"version": 2, "sites": []}', '{"version": 1}',
                                  '{"version": 1, "sites": "x"}', ""])
def test_a_broken_registry_file_is_a_problem_not_a_traceback(tmp_path, text):
    sites, problems = load_sites(write_registry(tmp_path, text))
    assert sites == [] and problems


def test_a_missing_registry_is_a_problem(tmp_path):
    sites, problems = load_sites(tmp_path / "nope.json")
    assert sites == [] and problems


def test_the_real_repo_example_and_registry_files_are_valid_with_the_right_shape():
    sites, problems = load_sites(REPO / "benchmark" / "sites.example.json")
    assert problems == [] and len(sites) >= 2
    assert all(s.host.endswith((".example.org", ".example.net", ".example.com")) for s in sites)
    real, problems = load_sites(REPO / "benchmark" / "sites.json")
    assert problems == []                      # whatever the owner has registered must be valid, however many sites
    assert "_comment" in json.loads((REPO / "benchmark" / "sites.example.json").read_text())


# pre-flight ----------------------------------------------------------------------------------------------
def test_an_empty_registry_blocks_with_a_clear_message_and_exit_1(tmp_path):
    root = make_repo(tmp_path, sites=[])
    r = run(root)
    assert statuses(r)[("site_count", "")] == "blocked" and r.blocked
    assert "no benchmark sites registered" in next(c.message for c in r.checks if c.id == "site_count")
    out = []
    assert main([], deps=Fakes().deps(), env={"ANTHROPIC_API_KEY": "k"}, out=out.append, repo_root=root) == 1


def test_one_valid_site_passes_with_warnings_only(tmp_path):
    root = make_repo(tmp_path)
    r = run(root)
    s = statuses(r)
    assert not r.blocked
    assert s[("criteria_committed", "")] == "ok" and s[("harness_hash", "")] == "ok"
    assert s[("api_key_present", "")] == "ok" and s[("sdk_installed", "")] == "ok"
    assert s[("site_count", "")] == "warn"                                       # 1 of the target 5
    assert s[("label_valid_and_committed", "demo.example.org")] == "ok"
    assert s[("label_names_match", "demo.example.org")] == "ok"
    assert s[("control_proven", "demo.example.org")] == "skipped"
    assert s[("results_dirs_free", "demo.example.org")] == "ok"
    assert s[("after_eligible_count", "")] == "ok"


def test_five_sites_meet_the_target_and_zero_eligible_sites_warn(tmp_path):
    entries = [site(f"s{i}.example.org", after_eligible=False, after_method="") for i in range(5)]
    r = run(make_repo(tmp_path, sites=entries))
    s = statuses(r)
    assert s[("site_count", "")] == "ok" and s[("after_eligible_count", "")] == "warn" and not r.blocked
    assert "before and after" in next(c.message for c in r.checks if c.id == "after_eligible_count")


def test_more_than_twenty_sites_block(tmp_path):
    entries = [site(f"s{i}.example.org") for i in range(21)]
    assert statuses(run(make_repo(tmp_path, sites=entries)))[("site_count", "")] == "blocked"


def test_a_missing_api_key_blocks_and_the_key_value_is_never_printed(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert statuses(run(root, env={}))[("api_key_present", "")] == "blocked"
    assert statuses(run(root, env={"ANTHROPIC_API_KEY": "   "}))[("api_key_present", "")] == "blocked"
    r = run(root)
    text, js = pf.to_text(r), json.dumps(pf.to_dict(r))
    assert SECRET_KEY not in text and SECRET_KEY not in js and "sk-ant" not in text + js
    code = main(["--format", "json"], deps=Fakes().deps(), env={"ANTHROPIC_API_KEY": SECRET_KEY}, repo_root=root)
    assert code == 0 and SECRET_KEY not in capsys.readouterr().out


def test_a_missing_sdk_blocks(tmp_path):
    assert statuses(run(make_repo(tmp_path), Fakes(sdk=False)))[("sdk_installed", "")] == "blocked"


def test_criteria_that_are_not_committed_or_do_not_match_the_harness_block(tmp_path):
    root = make_repo(tmp_path)
    r = run(root, Fakes(committed={"criteria.json": ["benchmark/criteria.json has uncommitted changes"]}))
    assert statuses(r)[("criteria_committed", "")] == "blocked"
    data = json.loads((root / "benchmark" / "criteria.json").read_text())
    data["harness_sha256"] = "0" * 64
    (root / "benchmark" / "criteria.json").write_text(json.dumps(data))
    assert statuses(run(root))[("harness_hash", "")] == "blocked"
    shutil.rmtree(root / "benchmark" / "criteria.json", ignore_errors=True)
    (root / "benchmark" / "criteria.json").unlink()
    assert statuses(run(root))[("harness_hash", "")] == "blocked"


def test_a_label_that_is_uncommitted_missing_or_invalid_blocks(tmp_path):
    root = make_repo(tmp_path)
    r = run(root, Fakes(committed={"demo.example.org.json": ["demo.example.org.json is not committed"]}))
    assert statuses(r)[("label_valid_and_committed", "demo.example.org")] == "blocked"
    assert "not committed" in next(c.message for c in r.checks if c.id == "label_valid_and_committed")
    (root / "benchmark" / "labels" / "demo.example.org.json").write_text("{}")
    assert statuses(run(root))[("label_valid_and_committed", "demo.example.org")] == "blocked"
    (root / "benchmark" / "labels" / "demo.example.org.json").unlink()
    assert statuses(run(root))[("label_valid_and_committed", "demo.example.org")] == "blocked"


def test_a_label_for_a_different_host_blocks(tmp_path):
    root = make_repo(tmp_path)
    (root / "benchmark" / "labels" / "demo.example.org.json").write_text(label_json("other.example.org"))
    s = statuses(run(root))
    # a label file named after one host but describing another is refused when it is read (the runner needs the name)
    assert s[("label_valid_and_committed", "demo.example.org")] == "blocked"
    assert s[("label_names_match", "demo.example.org")] == "skipped"


def test_check_control_calls_the_verifier_once_per_site_and_a_failure_blocks(tmp_path):
    entries = [site("a.example.org"), site("b.example.org")]
    root = make_repo(tmp_path, sites=entries)
    fakes = Fakes(verify=True)
    r = run(root, fakes, check_control=True)
    assert fakes.verified == ["a.example.org", "b.example.org"]
    assert {statuses(r)[("control_proven", h)] for h in ("a.example.org", "b.example.org")} == {"ok"}
    bad = Fakes(verify=False)
    r = run(root, bad, check_control=True)
    assert statuses(r)[("control_proven", "a.example.org")] == "blocked" and r.blocked
    boom = Fakes(verify=RuntimeError("secret detail /Users/x"))
    r = run(root, boom, check_control=True)
    assert statuses(r)[("control_proven", "a.example.org")] == "blocked"
    assert "secret detail" not in pf.to_text(r)


def test_without_check_control_the_verifier_is_never_called(tmp_path):
    fakes = Fakes()
    run(make_repo(tmp_path), fakes)
    assert fakes.verified == []


def test_existing_results_block_unless_allowed(tmp_path):
    root = make_repo(tmp_path)
    d = root / "benchmark" / "results" / "before" / "demo.example.org"
    d.mkdir(parents=True)
    valid_result(d / "main_info-1.json")
    r = run(root)
    assert statuses(r)[("results_dirs_free", "demo.example.org")] == "blocked"
    assert statuses(run(root, allow_existing=True))[("results_dirs_free", "demo.example.org")] == "warn"
    (d / "main_info-1.json").unlink()
    (d / "main_info-1.invalid-1.json").write_text("{}")                  # only moved-aside invalid runs: free
    assert statuses(run(root))[("results_dirs_free", "demo.example.org")] == "ok"
    ad = root / "benchmark" / "results" / "after" / "demo.example.org"
    ad.mkdir(parents=True)
    valid_result(ad / "docs_api-2.json")
    assert statuses(run(root))[("results_dirs_free", "demo.example.org")] == "blocked"


def test_a_permission_site_gets_a_reminder_and_a_before_only_site_says_so(tmp_path):
    entries = [site("p.example.org", owner_type="permission", permission_note="Written permission to test and deploy a change",
                    after_eligible=True, after_method="live"),
               site("b.example.org", after_eligible=False, after_method="")]
    s = statuses(run(make_repo(tmp_path, sites=entries)))
    assert s[("after_plan", "p.example.org")] == "warn"
    assert s[("after_plan", "b.example.org")] == "ok"


def test_json_and_text_agree_and_json_parses(tmp_path):
    r = run(make_repo(tmp_path))
    data = json.loads(json.dumps(pf.to_dict(r)))
    assert {(c["id"], c["site"]): c["status"] for c in data["checks"]} == statuses(r)
    assert data["blocked"] is False
    for c in r.checks:
        assert f"[{c.status}]" in pf.to_text(r)


def test_the_default_run_makes_no_network_request_and_writes_no_file(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    run(root)
    main([], deps=Fakes().deps(), env={"ANTHROPIC_API_KEY": "k"}, out=lambda s: None, repo_root=root)
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_the_cli_exit_codes_and_flags(tmp_path):
    root = make_repo(tmp_path)
    out = []
    assert main([], deps=Fakes().deps(), env={"ANTHROPIC_API_KEY": "k"}, out=out.append, repo_root=root) == 0
    assert any("[ok]" in line for line in out)
    assert main([], deps=Fakes().deps(), env={}, out=out.append, repo_root=root) == 1
    with pytest.raises(SystemExit):
        main(["--snapshot-before", "../x"], deps=Fakes().deps(), env={}, out=out.append, repo_root=root)
    with pytest.raises(SystemExit):
        main(["--snapshot-before", "same", "--snapshot-after", "SAME"], deps=Fakes().deps(), env={},
             out=out.append, repo_root=root)


def test_the_default_sdk_check_looks_for_the_anthropic_module(monkeypatch, tmp_path):
    d = pf.default_deps(tmp_path)
    looked = []
    monkeypatch.setattr(pf.importlib.util, "find_spec", lambda name: looked.append(name))     # returns None: absent
    assert d.sdk_installed() is False and looked == ["anthropic"]
    monkeypatch.setattr(pf.importlib.util, "find_spec", lambda name: object())
    assert d.sdk_installed() is True


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_the_committed_check_uses_the_repository_given_not_this_one(tmp_path):
    """default_deps(repo_root) must check files against the git repository at repo_root."""
    root = tmp_path / "other"
    root.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    _git(root, "config", "user.email", "t@example.org")
    _git(root, "config", "user.name", "t")
    (root / "f.json").write_text("{}")
    _git(root, "add", "f.json")
    _git(root, "commit", "-q", "-m", "x")
    deps = pf.default_deps(root)
    assert deps.committed([root / "f.json"]) == []
    (root / "f.json").write_text('{"changed": true}')
    assert any("uncommitted changes" in p for p in deps.committed([root / "f.json"]))
    (root / "new.json").write_text("{}")
    assert any("not committed" in p for p in deps.committed([root / "new.json"]))


def test_main_builds_its_default_dependencies_for_the_repository_it_was_given(tmp_path, monkeypatch):
    seen = {}
    real = pf.default_deps

    def spy(repo_root=pf.REPO_ROOT):
        seen["root"] = repo_root
        return real(repo_root)
    monkeypatch.setattr(pf, "default_deps", spy)
    root = make_repo(tmp_path)
    pf.main([], env={}, out=lambda s: None, repo_root=root)
    assert seen["root"] == root


# Group D: registry and pre-flight hardening (review findings I7 and minors) ---------------------------------
from app.runner import cli as runner_cli
from app.runner.labels import load_label


def problems_of(tmp_path, entry):
    return load_sites(write_registry(tmp_path, {"version": 1, "sites": [entry]}))


def valid_result(path: Path, valid=True):
    path.write_text(json.dumps({"result": {"valid": valid}}))


def test_a_run_file_that_is_not_valid_does_not_count_as_a_result(tmp_path):
    root = make_repo(tmp_path)
    d = root / "benchmark" / "results" / "before" / "demo.example.org"
    d.mkdir(parents=True)
    valid_result(d / "main_info-1.json", valid=False)                      # an invalid run that was not moved aside
    (d / "docs_api-1.json").write_text("{not json")
    assert statuses(run(root))[("results_dirs_free", "demo.example.org")] == "ok"
    valid_result(d / "contact_form-1.json", valid=True)
    r = run(root)
    assert statuses(r)[("results_dirs_free", "demo.example.org")] == "blocked"
    text = pf.to_text(r)
    assert "never overwritten" in text and "new snapshot name" in text and "--allow-existing" in text


@pytest.mark.parametrize("port", [":443", ":80"])
def test_an_explicit_default_port_is_refused_so_one_website_is_never_counted_twice(tmp_path, port):
    entries = [site("demo.example.org"), site("demo.example.org" + port, label=f"demo.example.org_{port[1:]}.json")]
    sites, problems = load_sites(write_registry(tmp_path, {"version": 1, "sites": entries}))
    assert [s.host for s in sites] == ["demo.example.org"]
    assert any("default port" in p for p in problems), problems


@pytest.mark.parametrize("host", ["example", "2130706433", "0x7f.1", "1.2.3.4.5", "8.8.8.8", "a.b.123", "-bad.example.org",
                                  "bad-.example.org", "a..example.org", "example.org."])
def test_host_names_must_be_real_public_names_with_a_non_numeric_last_label(tmp_path, host):
    sites, problems = problems_of(tmp_path, site(host, label=f"{host}.json", verification="meta"))
    assert sites == [] and any("host" in p for p in problems), (host, problems)


def test_ip_literals_are_refused_for_benchmark_sites_and_the_message_says_so(tmp_path):
    _, problems = problems_of(tmp_path, site("8.8.8.8", label="8.8.8.8.json"))
    assert any("IP address" in p for p in problems), problems


@pytest.mark.parametrize("note", ["xxxxxxxxxx", "ok ok", "Written OK from the owner", ""])
def test_a_permission_note_needs_ten_characters_and_three_words(tmp_path, note):
    _, problems = problems_of(tmp_path, site(owner_type="permission", permission_note=note, after_eligible=False,
                                             after_method=""))
    if note == "Written OK from the owner":
        assert problems == []
    else:
        assert any("permission_note" in p for p in problems), problems


@pytest.mark.parametrize("note,accepted", [
    ("Written permission from the owner to change and deploy the site", True),
    ("Email: permission granted to deploy a change on the staging copy", True),
    ("I may change this", False),
    ("no permission to change or deploy", False),
    ("They have not permitted any change or deploy", False),
    ("without permission to change anything", False),
    ("permission denied to change or deploy the site", False),
    ("we do not have permission to change the site", False),
    ("exchange rate permission note here", False),
    ("permission to test only, nothing to deploy here", False),
])
def test_after_eligible_permission_sites_need_affirmative_permission_to_change_or_deploy(tmp_path, note, accepted):
    sites, problems = problems_of(tmp_path, site(owner_type="permission", permission_note=note, after_eligible=True,
                                                 after_method="staging"))
    assert (problems == [] and len(sites) == 1) == accepted, (note, problems)


@pytest.mark.parametrize("owner,note,accepted", [
    ("permission", "Written permission from my employer to change and deploy the site", True),
    ("permission", "Written permission from the owner to change and deploy the site", False),   # no word "employer"
    ("permission", "Employer said yes by chat, permission to change and deploy", False),          # no word "written"
    ("team", "", False),
    ("team", "Employer written OK recorded in the shared drive folder", True),
])
def test_employer_sites_need_the_employers_written_ok_in_the_note(tmp_path, owner, note, accepted):
    entry = site("countrystatecity.example.org", owner_type=owner, permission_note=note,
                 label="countrystatecity.example.org.json", after_eligible=(owner == "permission"),
                 after_method="staging" if owner == "permission" else "")
    sites, problems = problems_of(tmp_path, entry)
    assert (problems == [] and len(sites) == 1) == accepted, (note, problems)
    other = site("www.MakeMySiteLive.example.org".lower(), owner_type="team", permission_note="",
                 label="www.makemysitelive.example.org.json", after_eligible=False, after_method="")
    assert any("employer" in p for p in problems_of(tmp_path, other)[1])


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 2])
def test_the_registry_version_must_be_the_integer_one(tmp_path, version):
    sites, problems = load_sites(write_registry(tmp_path, {"version": version, "sites": [site()]}))
    assert sites == [] and any("version" in p for p in problems)


def test_notes_must_be_text(tmp_path):
    _, problems = problems_of(tmp_path, site(notes={"a": 1}))
    assert any("notes" in p for p in problems)


def test_the_registry_file_must_be_committed_and_unmodified(tmp_path):
    root = make_repo(tmp_path)
    r = run(root, Fakes(committed={"sites.json": ["benchmark/sites.json has uncommitted changes"]}))
    assert statuses(r)[("sites_registry", "")] == "blocked" and r.blocked
    assert "uncommitted" in pf.to_text(r)
    assert statuses(run(root))[("sites_registry", "")] == "ok"


def test_a_missing_git_is_a_clean_blocked_check_not_a_traceback(tmp_path):
    root = make_repo(tmp_path)
    fakes = Fakes()
    real = fakes.deps()
    boom = Deps(committed=lambda paths: (_ for _ in ()).throw(FileNotFoundError("git")),
                sdk_installed=real.sdk_installed, verify=real.verify)
    r = run_preflight(root, {"ANTHROPIC_API_KEY": SECRET_KEY}, boom)
    s = statuses(r)
    assert s[("criteria_committed", "")] == "blocked" and r.blocked
    assert "git" in pf.to_text(r).lower() and "Traceback" not in pf.to_text(r)


def test_git_problems_reports_git_missing_instead_of_raising(tmp_path, monkeypatch):
    f = tmp_path / "x.json"
    f.write_text("{}")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    problems = runner_cli.git_problems([f], repo_root=tmp_path)
    assert len(problems) == 1 and "git" in problems[0].lower() and "could not be run" in problems[0]
    assert runner_cli.git_head(tmp_path) == ""


def test_deeply_nested_json_is_a_refusal_not_a_crash_in_the_label_and_criteria_readers(tmp_path):
    label = tmp_path / "x.test.json"
    label.write_text("[" * 200000)
    with pytest.raises(ValueError):
        load_label(label, check_name=True)
    criteria = tmp_path / "criteria.json"
    criteria.write_text("[" * 200000)
    with pytest.raises(ValueError):
        runner_cli.load_criteria(criteria)


# ---- Gemini ----------------------------------------------------------------------------------------------------
GEMINI_KEY = "AIzaSyPREFLIGHTKEY0123456789abcdefghij"


def test_a_gemini_model_needs_gemini_api_key_not_the_anthropic_one_or_its_sdk(tmp_path):
    root = make_repo(tmp_path)
    fakes = Fakes()
    fakes.sdk = False
    r = run(root, fakes, env={"AGENTREADY_MODEL": "gemini-3.5-flash-lite", "ANTHROPIC_API_KEY": SECRET_KEY})
    s = statuses(r)
    assert s[("api_key_present", "")] == "blocked" and s[("sdk_installed", "")] == "ok"
    assert "GEMINI_API_KEY" in pf.to_text(r)
    r = run(root, fakes, env={"AGENTREADY_MODEL": "gemini-3.5-flash-lite", "GEMINI_API_KEY": GEMINI_KEY})
    assert statuses(r)[("api_key_present", "")] == "ok" and statuses(r)[("model", "")] == "ok"
    text = pf.to_text(r) + json.dumps(pf.to_dict(r))
    assert GEMINI_KEY not in text and "gemini-3.5-flash-lite" in text


def test_a_bad_gemini_model_name_or_interval_blocks(tmp_path):
    root = make_repo(tmp_path)
    env = {"AGENTREADY_MODEL": "gemini-../x", "GEMINI_API_KEY": GEMINI_KEY}
    assert statuses(run(root, env=env))[("model", "")] == "blocked"
    env = {"AGENTREADY_MODEL": "gemini-3.5-flash-lite", "GEMINI_API_KEY": GEMINI_KEY,
           "AGENTREADY_LLM_MIN_INTERVAL": "soon"}
    assert statuses(run(root, env=env))[("sdk_installed", "")] == "blocked"


def test_the_default_model_still_needs_the_anthropic_key(tmp_path):
    root = make_repo(tmp_path)
    r = run(root, env={"GEMINI_API_KEY": GEMINI_KEY})
    assert statuses(r)[("api_key_present", "")] == "blocked" and "ANTHROPIC_API_KEY" in pf.to_text(r)
