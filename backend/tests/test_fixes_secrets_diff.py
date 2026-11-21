import pytest

from app.fixes.diff import make_diff
from app.fixes.secrets import find_secrets

SAMPLES = {
    "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc",
    "aws access key": "id " + "AKIA" + "ABCDEFGHIJKLMNOP",
    "github token": "ghp_" + "a" * 36,
    "api key (sk-)": "sk-" + "a1B2" * 8,
    "slack token": "xoxb-" + "1234567890-abcdefghij",
    "jwt": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0." + "abcdef",
    "secret assignment": "DB_PASSWORD=hunter22",
    "bearer token": "Authorization: Bearer " + "a" * 24,
    "password in url": "https://user:pa55word@x.test/",
}


@pytest.mark.parametrize("label,text", SAMPLES.items())
def test_each_secret_kind_is_found_and_its_value_is_never_returned(label, text):
    found = find_secrets("prefix\n" + text + "\nsuffix")
    assert label in found
    joined = " ".join(found)
    assert text not in joined and "hunter22" not in joined and "pa55word" not in joined


def test_ordinary_site_text_has_no_findings():
    assert find_secrets("# Acme\n> Shoes. Price key: see the table.\n- [Shop](https://x.test/shop)") == []
    assert find_secrets("The API key is optional; send your token in the header.") == []


def test_findings_are_sorted_and_deduplicated():
    text = "DB_PASSWORD=hunter22\nAPI_TOKEN=abcdef123\n"
    assert find_secrets(text) == ["secret assignment"]
    assert find_secrets(SAMPLES["jwt"] + "\n" + SAMPLES["aws access key"]) == ["aws access key", "jwt"]


def test_new_file_diff_uses_dev_null():
    d = make_diff("llms.txt", None, "# Acme\n")
    lines = d.splitlines()
    assert lines[0] == "--- /dev/null" and lines[1] == "+++ b/llms.txt" and "+# Acme" in lines


def test_modified_file_diff_shows_removed_and_added_lines():
    d = make_diff("index.html", "<head>\n</head>\n", "<head>\n<x/>\n</head>\n")
    assert d.startswith("--- a/index.html\n+++ b/index.html") and "+<x/>" in d.splitlines()


def test_missing_trailing_newline_is_normalised():
    assert make_diff("a", None, "no newline").endswith("\n")
    assert make_diff("a", "same\n", "same\n") == ""
