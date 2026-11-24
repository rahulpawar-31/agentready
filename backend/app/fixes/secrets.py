"""Secret-like value scanner. It returns labels only: the matched text is never returned or logged.

Every pattern is bounded (no unbounded run in front of a keyword), so scanning a 500 KB page stays linear."""
import re
import unicodedata
from urllib.parse import unquote

_NAME = r"(?:password|passwd|secret|token|api_?key|access_?key|key|credential)"
_TAIL = r"[A-Za-z0-9_.-]{0,40}"
_I = re.IGNORECASE

_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws secret key", re.compile(r"aws_?secret_?access_?key[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{20,}", _I)),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("api key (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("stripe key", re.compile(r"(?:(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}|whsec_[A-Za-z0-9]{16,})")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("twilio key", re.compile(r"\bSK[0-9a-f]{32}\b")),
    ("sendgrid key", re.compile(r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("npm token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+")),
    # NAME=value anywhere in a line; NAME: value only when the value contains a digit (so prose such as
    # "Secret: handmade leather" is not flagged); and "name": "value" in JSON.
    ("secret assignment", re.compile(_NAME + _TAIL + r"[ \t]*=[ \t]*[\"']?[^\s\"'<>]{6,}", _I)),
    ("secret assignment", re.compile(_NAME + _TAIL + r"[ \t]*:[ \t]*[\"']?(?=[^\s\"'<>]*\d)[^\s\"'<>]{6,}", _I)),
    ("secret assignment", re.compile(r"[\"']" + _TAIL + _NAME + _TAIL + r"[\"']\s*:\s*[\"'][^\"'\s]{6,}[\"']", _I)),
    ("bearer token", re.compile(r"authorization\s*:\s*bearer\s+\S{16,}", _I)),
    ("basic auth header", re.compile(r"authorization\s*:\s*basic\s+[A-Za-z0-9+/=]{8,}", _I)),
    ("password in url", re.compile(r"://[^/\s:@]*:[^/\s@]+@")),
)


def _without_invisible(text: str) -> str:
    """Drop format characters (zero-width, bidi, BOM) that can split a secret to hide it from a scan."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def find_secrets(text: str) -> list[str]:
    """Labels of the secret-like patterns found in `text`, sorted and de-duplicated (never the values). The text
    is also checked URL-decoded and with invisible characters removed."""
    text = text if isinstance(text, str) else str(text)
    variants = {text, unquote(text)}
    variants |= {_without_invisible(v) for v in variants}
    return sorted({label for label, pattern in _PATTERNS if any(pattern.search(v) for v in variants)})


def added_lines(diff: str, old: str | None = None) -> str:
    """The text a unified diff adds (its `+` lines, without the file header), leaving out any line that is simply the
    existing page again. A one-line page that is split to insert a snippet re-adds its own text as `+` lines; that is
    the owner's page, not something AgentReady generated, so it is not counted when `old` is given."""
    lines = diff.split("\n")
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ "):
        lines = lines[2:]
    added = [line[1:] for line in lines if line.startswith("+")]
    if old:
        if len(added) <= 200:
            added = [line for line in added if line.strip() and line not in old]
        else:
            known = set(old.split("\n"))
            added = [line for line in added if line not in known]
    return "\n".join(added)
