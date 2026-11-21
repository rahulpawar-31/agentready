from .models import Status


def _scored(findings):
    return [f for f in findings if f.status is not Status.UNREACHABLE]


def score(findings) -> int:
    fs = _scored(findings)
    total = sum(f.weight for f in fs)
    if not total:
        return 0
    return round(100 * sum(f.weight for f in fs if f.passed) / total)


def category_scores(findings) -> dict:
    groups: dict[str, list] = {}
    for f in _scored(findings):
        groups.setdefault(f.category.value, []).append(f)
    return {name: score(fs) for name, fs in groups.items()}


def is_scored(findings) -> bool:
    """True when at least one finding counts towards the score (reachable and weight > 0)."""
    return any(f.weight > 0 for f in _scored(findings))
