import difflib


def _lines(text: str) -> list[str]:
    return [line if line.endswith("\n") else line + "\n" for line in text.splitlines(keepends=True)]


def make_diff(path: str, old: str | None, new: str) -> str:
    """Unified diff of one file. `old=None` means the file does not exist yet (a new file)."""
    fromfile = "/dev/null" if old is None else f"a/{path}"
    out = list(difflib.unified_diff(_lines(old or ""), _lines(new), fromfile, f"b/{path}", lineterm=""))
    if not out:
        return ""
    return "\n".join(line.rstrip("\n") for line in out) + "\n"
