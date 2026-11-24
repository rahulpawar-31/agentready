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


# --- Group B of the Plan 3 review: provider formats, anywhere in a line, after decoding, labels only
def _g(n):                                      # filler that is not a secret-looking literal in this repo
    return ("a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0" * 3)[:n]


MORE_SAMPLES = {
    "stripe key": ["sk_" + "live_" + _g(24), "rk_" + "live_" + _g(24), "sk_" + "test_" + _g(24), "whsec_" + _g(24)],
    "google api key": ["AIza" + _g(35)],
    "twilio key": ["SK" + "0123456789abcdef" * 2],
    "sendgrid key": ["SG." + _g(22) + "." + _g(43)],
    "npm token": ["npm_" + _g(36)],
    "aws secret key": ["aws_secret_access_key = " + "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"],
    "secret assignment": ['{"password": "hunter22hunter"}', '{"client_secret": "abcd1234efgh"}', '{"apiKey": "abcd1234efgh5678"}',
                          "db_password=hunter22", "> API_KEY=abcd1234efgh5678", "# DB_PASSWORD=hunter22hunter",
                          "<p>STRIPE_SECRET_KEY=" + "whsec_" + _g(16) + "</p>", "Secret: hunter22hunter"],
    "password in url": ["redis://:pw0rd123@cache.internal:6379", "postgres://app:pw0rd123@db.internal/x"],
    "basic auth header": ["Authorization: Basic " + "dXNlcjpwYXNzd29yZDEyMw=="],
    "private key block": ["-----BEGIN " + "PGP PRIVATE KEY BLOCK-----"],
}


@pytest.mark.parametrize("label,text", [(l, t) for l, ts in MORE_SAMPLES.items() for t in ts])
def test_b_more_providers_and_prefixed_lines_are_found_and_the_value_is_never_returned(label, text):
    found = find_secrets("intro line\n" + text + "\nlast line")
    assert label in found, (label, found)
    assert text not in " ".join(found) and _g(16) not in " ".join(found) and "hunter22" not in " ".join(found)


def test_b_url_encoded_and_zero_width_split_secrets_are_found():
    key = "sk_" + "live_" + _g(24)
    assert "stripe key" in find_secrets(key.replace("_", "%5F"))
    assert "aws access key" in find_secrets("AKIA" + "​" + "ABCDEFGHIJKLMNOP")
    assert "aws access key" in find_secrets("A⁠K﻿IA" + "ABCDEFGHIJKLMNOP")


@pytest.mark.parametrize("text", [
    "The API key is optional; send your token in the header.",
    "Secret: handmade leather shoes", "Price key: see the table", "Monkey business: great fun",
    "Tokens are explained below. Keys to success", "token_count = 12", "Password rules: be sensible",
    "Visit https://x.test:8080/shop or mail us@x.test", "key: none", "A public anon token is not a secret here",
])
def test_b_ordinary_prose_is_not_flagged(text):
    assert find_secrets(text) == []


def test_b_scanning_a_large_page_is_fast():
    import time
    big = "x" * 500_000 + " <p>" * 5000
    started = time.perf_counter()
    assert find_secrets(big) == []
    assert time.perf_counter() - started < 5


def test_b_added_lines_ignores_headers_context_removed_and_re_added_page_lines():
    from app.fixes.secrets import added_lines
    old = "<p>old " + "AKIA" + "ABCDEFGHIJKLMNOP</p><head></head><body>rest</body>"
    new = "<p>old " + "AKIA" + "ABCDEFGHIJKLMNOP</p><head>\n<script>snippet</script>\n</head><body>rest</body>"
    diff = make_diff("index.html", old, new)
    assert "ABCDEFGHIJKLMNOP" in added_lines(diff)                              # without `old`, the fragments count
    text = added_lines(diff, old)
    assert "<script>snippet</script>" in text and "ABCDEFGHIJKLMNOP" not in text and "+++" not in text
    assert "</head><body>rest</body>" not in text
