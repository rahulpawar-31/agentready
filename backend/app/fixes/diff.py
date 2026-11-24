import difflib


def _lines(text: str) -> list[str]:
    """Split on "\\n" only, keeping it. Unicode separators, form feeds and a lone "\\r" are not line breaks to
    `patch`, so they must not be line breaks in the diff either."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])          # a last line without a newline
    return lines


def make_diff(path: str, old: str | None, new: str) -> str:
    """Unified diff of one file. `old=None` means the file does not exist yet (a new file). A last line without a
    newline is followed by the standard "\\ No newline at end of file" marker, so `patch` applies the diff."""
    fromfile = "/dev/null" if old is None else f"a/{path}"
    items = list(difflib.unified_diff(_lines(old or ""), _lines(new), fromfile, f"b/{path}", lineterm=""))
    if not items:
        return ""
    out = []
    for index, item in enumerate(items):
        if index < 2 or item.startswith("@@"):        # the two file headers and the hunk headers
            out.append(item + "\n")
        elif item.endswith("\n"):
            out.append(item)
        else:
            out.append(item + "\n\\ No newline at end of file\n")
    return "".join(out)
