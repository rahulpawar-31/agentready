"""Secret-like value scanner. It returns labels only: the matched text is never returned or logged."""
import re

_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("api key (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+")),
    ("secret assignment", re.compile(r"(?m)^[ \t]*[A-Z][A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY)[A-Z0-9_]*[ \t]*[=:][ \t]*\S{6,}")),
    ("bearer token", re.compile(r"(?i)authorization:\s*bearer\s+\S{16,}")),
    ("password in url", re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
)


def find_secrets(text: str) -> list[str]:
    """Labels of the secret-like patterns found in `text`, sorted and de-duplicated (never the values)."""
    return sorted({label for label, pattern in _PATTERNS if pattern.search(text)})
