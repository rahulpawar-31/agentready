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
