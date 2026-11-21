from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    DISCOVERABILITY = "discoverability"
    OPERABILITY = "operability"
    IDENTITY = "identity"
    SAFETY = "safety"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not detected"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class Page:
    url: str
    status: int          # 0 = the request was not made or failed (see error)
    headers: dict
    body: str
    error: str = ""


@dataclass(frozen=True)
class Finding:
    check_id: str
    category: Category
    passed: bool
    weight: int
    check: str           # what was done
    evidence: str        # what was observed
    url: str
    confidence: Confidence
    status: Status


@dataclass
class Report:
    site: str
    score: int
    category_scores: dict
    findings: list
    unscored: int        # findings excluded from the score because the page was unreachable
    score_status: str = "scored"     # "not_scored" when nothing could be scored (then score is 0)
