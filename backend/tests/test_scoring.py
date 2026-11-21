from app.models import Category, Confidence, Finding, Status
from app.scoring import category_scores, score


def f(cat, passed, weight, status=Status.DETECTED):
    return Finding("id", cat, passed, weight, "c", "e", "u", Confidence.HIGH, status)


def test_score_is_weighted_pass_rate():
    assert score([f(Category.SAFETY, True, 3), f(Category.SAFETY, False, 1)]) == 75


def test_empty_findings_score_zero():
    assert score([]) == 0


def test_zero_weight_findings_are_shown_but_do_not_change_the_score():
    base = [f(Category.SAFETY, True, 3)]
    assert score(base + [f(Category.SAFETY, False, 0)]) == score(base) == 100


def test_unreachable_findings_are_not_scored():
    fs = [f(Category.SAFETY, True, 1), f(Category.SAFETY, False, 9, Status.UNREACHABLE)]
    assert score(fs) == 100


def test_category_scores_are_separate_and_skip_all_unreachable_categories():
    fs = [f(Category.SAFETY, True, 1), f(Category.IDENTITY, False, 1),
          f(Category.OPERABILITY, False, 1, Status.UNREACHABLE)]
    assert category_scores(fs) == {"safety": 100, "identity": 0}
