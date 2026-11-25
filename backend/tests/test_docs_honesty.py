"""The README and docs must stay honest: working links, no invented measurements, no claims beyond the PRD."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
DOCS = sorted((REPO / "docs").glob("*.md"))
ALL = [README, *DOCS]

BANNED = ["agents can use your site", "improves agent success", "proven to improve", "we guarantee", "guaranteed to", "100% secure",
          "fully secure", "no vulnerabilities", "production-ready", "production ready", "significant improvement",
          "statistically significant"]
EXAMPLE_BLOCK = re.compile(r"```[a-z]*\n<!-- example -->\n.*?```", re.S)
A1_QUOTE = "We want the tools that make agents first-class users"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_docs_exist():
    names = {p.name for p in DOCS}
    assert {"benchmark.md", "demo-script.md", "submission-checklist.md", "ui.md", "mcp.md", "fixes.md",
            "benchmark-report.md"} <= names


def test_every_relative_link_resolves_to_a_file():
    broken = []
    for path in ALL:
        for target in re.findall(r"\]\(([^)\s]+)\)", read(path)):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            file_part = target.split("#", 1)[0]
            if file_part and not (path.parent / file_part).resolve().exists():
                broken.append(f"{path.name}: {target}")
    assert broken == []


def test_banned_phrases_are_absent_everywhere():
    found = [f"{p.name}: {phrase}" for p in ALL for phrase in BANNED if phrase in read(p).lower()]
    assert found == []


def test_no_unlabelled_performance_percentages():
    """No '<n>%' within 40 characters of success/improvement/accuracy/score unless inside an example block."""
    pattern = re.compile(r"(success|improvement|accuracy|score)[^\n]{0,40}\d+(\.\d+)?\s*%|\d+(\.\d+)?\s*%[^\n]{0,40}(success|improvement|accuracy|score)",
                         re.I)
    hits = []
    for path in ALL:
        text = EXAMPLE_BLOCK.sub("", read(path))
        hits += [f"{path.name}: {m.group(0)}" for m in pattern.finditer(text)]
    assert hits == []


def test_the_readme_says_not_yet_measured_and_points_to_the_protocol():
    text = read(README)
    assert "Not yet measured" in text and "docs/benchmark.md" in text
    assert len(text.splitlines()) < 200


def test_the_a1_quotation_appears_once_with_its_recheck_note():
    text = read(README)
    assert text.count(A1_QUOTE) == 1
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if A1_QUOTE in line)
    assert any("Re-check" in line for line in lines[i + 1:i + 4])
    assert not any(A1_QUOTE in read(p) for p in DOCS)          # one place to remove if the live wording differs


def test_the_demo_script_has_no_percent_figures_and_no_invented_numbers():
    text = read(REPO / "docs" / "demo-script.md")
    assert not re.search(r"\d\s*%", text)
    assert "evaluation" in text.lower() and "real numbers" in text.lower()


def test_the_benchmark_protocol_covers_the_safety_and_honesty_rules():
    text = read(REPO / "docs" / "benchmark.md")
    for needle in ("--check-control", "--snapshot after", "permission_note", "MCP stub", "benchmark/sites.json",
                   "pre-registered", "invalid", "small sample", "outside"):
        assert needle.lower() in text.lower(), needle


def test_no_document_tells_the_reader_to_save_a_report_inside_the_results_tree():
    """The report command refuses that, because the file would be read back as a result."""
    for path in ALL:
        assert "benchmark/results/evaluation" not in read(path), path.name


def test_the_readme_links_every_document():
    text = read(README)
    missing = [p.name for p in DOCS if f"docs/{p.name}" not in text]
    assert missing == []


def test_the_submission_checklist_names_the_owner_only_items_and_the_dates():
    text = read(REPO / "docs" / "submission-checklist.md")
    for needle in ("ANTHROPIC_API_KEY", "written permission", "Re-check", "remove the quotation", "video",
                   "1 Dec", "5 Dec", "6 Dec", "verify_checkout", "- [ ]"):
        assert needle.lower() in text.lower(), needle


# ---- Group C: docs that must work as written, and stay inside the claim boundary ----------------------------
def chunks(text: str):
    """Blank-line separated chunks: a command and the exports it needs must sit in the same chunk."""
    return [c for c in re.split(r"\n\s*\n", text) if c.strip()]


def test_every_runner_command_block_states_the_exports_it_needs():
    """cli run and preflight need ANTHROPIC_API_KEY and the SAME AGENTREADY_SECRET the API used; audit_cli needs the
    secret. A block that runs them without the exports crashes in a fresh shell."""
    problems = []
    for path in (README, REPO / "docs" / "benchmark.md", REPO / "docs" / "benchmark-report.md"):
        for chunk in chunks(EXAMPLE_BLOCK.sub("", read(path))):
            if re.search(r"-m app\.runner\.(cli run|preflight)", chunk):
                for needle in ("export AGENTREADY_SECRET=", "export ANTHROPIC_API_KEY="):
                    if needle not in chunk:
                        problems.append(f"{path.name}: a runner block lacks `{needle}`: {chunk.strip()[:60]!r}")
            if re.search(r"-m app\.runner\.audit_cli", chunk) and "export AGENTREADY_SECRET=" not in chunk:
                problems.append(f"{path.name}: an audit_cli block lacks `export AGENTREADY_SECRET=`")
    assert problems == []


def test_the_docs_say_the_api_runs_in_a_separate_shell_with_the_same_secret():
    for path in (README, REPO / "docs" / "benchmark.md"):
        text = read(path).lower()
        assert "separate shell" in text and "same value" in text, path.name


def test_the_cost_line_counts_runs_and_model_calls_correctly():
    text = read(REPO / "docs" / "benchmark.md")
    assert "Model calls are about" not in text
    assert "runs = sites x 3 tasks x repeats" in text and "up to 24 model calls" in text


def timeline_rows():
    rows = []
    for line in read(REPO / "docs" / "demo-script.md").splitlines():
        m = re.match(r"\|\s*(\d+):(\d\d)\s*\|", line)
        if m:
            rows.append((int(m.group(1)) * 60 + int(m.group(2)), line))
    return rows


def test_the_demo_timeline_fits_three_minutes_and_no_segment_is_shorter_than_ten_seconds():
    rows = timeline_rows()
    starts = [t for t, _ in rows]
    assert starts == sorted(starts) and len(set(starts)) == len(starts) and starts[0] == 0
    assert starts[-1] == 180 and "end" in rows[-1][1].lower()          # the closing row is the 3:00 mark
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 10, gaps


def test_the_demo_does_not_wait_for_a_live_audit_on_the_mcp_segment():
    text = read(REPO / "docs" / "demo-script.md").lower()
    assert "already-run" in text or "already run" in text or "recorded beforehand" in text


NUMBER_CLAIMS = [r"\b\d+ of \d+ sites?\b", r"\bof 5 sites\b", r"\bscored \d+\s*/\s*100\b", r"\b\d+\s*/\s*100\b",
                 r"\bfaster\b", r"\btwo thirds\b", r"\bgot better\b", r"\bthree of five\b", r"\bimproved by\b"]


def test_no_document_states_a_result_in_words_or_as_a_fraction():
    hits = []
    for path in ALL:
        text = EXAMPLE_BLOCK.sub("", read(path))
        for pattern in NUMBER_CLAIMS:
            hits += [f"{path.name}: {m.group(0)}" for m in re.finditer(pattern, text, re.I)]
    assert hits == []


def test_the_submission_dates_match_the_prd_schedule_exactly():
    text = read(REPO / "docs" / "submission-checklist.md")
    lowered = text.lower()
    for needle in ("feature freeze **1 dec 2026**", "evaluation and polish 2 to 3 dec", "**4 dec**",
                   "submit on 5 dec**", "closes **6 dec 23:59**", "ist"):
        assert needle in lowered, needle
    assert "- [ ] submit on 5 dec." in lowered
    assert not re.search(r"\b(7|8|9) dec\b", lowered)


def test_no_document_claims_claude_code_integration_was_tested():
    hits = []
    for path in ALL:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", read(path)):
            if "claude code" in sentence.lower() and re.search(r"\b(tested|verified|works|working)\b", sentence, re.I):
                if not re.search(r"\b(not|never|untested|has not|hasn't)\b", sentence, re.I) and \
                        "official" not in sentence.lower():
                    hits.append(f"{path.name}: {sentence.strip()[:100]}")
    assert hits == []


def test_the_a1_quotation_is_described_as_transcribed_not_as_screenshot_checked():
    text = read(README)
    assert "transcribed from the Homingo problem statement" in text
    assert "checked against a screenshot" not in text.lower()


def test_the_benchmark_doc_covers_audit_accuracy_and_the_reserved_snapshot_name():
    text = read(REPO / "docs" / "benchmark.md")
    for needle in ("audit_cli", "audit_checks", "audits", "reserved", "small sample"):
        assert needle.lower() in text.lower(), needle
    assert "no hand-labelled expected outcomes" not in text.lower()
    assert "new file name" in text.lower()               # a report already committed cannot be overwritten in place


def test_the_benchmark_doc_states_the_registry_rules_and_that_the_note_is_an_attestation():
    text = read(REPO / "docs" / "benchmark.md").lower()
    for needle in ("attestation", "default port", "ip addresses", "three words", "employer", "unmodified"):
        assert needle in text, needle
