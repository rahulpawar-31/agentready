from dataclasses import dataclass, field
from enum import Enum

# Clip constants shared by the generators (see Plan 3, "Decisions made here" 7).
FIELD_CLIP = 200
DESC_CLIP = 300
LINK_TEXT_CLIP = 80
LLMS_MAX_LINKS = 10
LLMS_MAX_BYTES = 20_000
MCP_MAX_TOOLS = 20
STUB_MAX_BYTES = 60_000


class FixKind(str, Enum):
    LLMS_TXT = "llms_txt"
    JSON_LD = "json_ld"
    MCP_STUB = "mcp_stub"


class FixStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class NotEnoughContent(ValueError):
    """The site's own pages do not hold enough to build this output without inventing facts."""


class SecretInSiteText(ValueError):
    """Text taken from the site looks like a secret. Only the label is kept, never the value."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


@dataclass
class Fix:
    id: str
    kind: FixKind
    path: str            # the site path the file belongs at, or the page it patches
    filename: str        # download name
    description: str
    content: str
    diff: str
    finding_id: str
    status: FixStatus = FixStatus.PROPOSED
    approved_sha256: str = ""
    diff_kind: str = "full"      # "excerpt" when the page is too long to show whole
    warning: str = ""            # something the reviewer should know about the existing page
    note: str = ""               # context for the reviewer (for example, /llms.txt currently returns HTML)
    added: "str | None" = None   # text the diff adds that is not the existing page again; None = scan every + line


@dataclass(frozen=True)
class Skipped:
    kind: FixKind
    reason: str


@dataclass(frozen=True)
class Proposal:
    fixes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


@dataclass(frozen=True)
class ExportBundle:
    fix_id: str
    filename: str
    content: str
    diff: str
    sha256: str
    note: str
