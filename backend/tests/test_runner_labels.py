import copy
import json

import pytest

from app.runner.labels import load_label, normalise_url
from app.runner.tasks import STEP_LIMIT, TASK_IDS, TASKS, system_prompt

GOOD = {
    "site": "https://x.test",
    "tasks": {
        "main_info": {"urls": ["https://x.test/pricing"]},
        "contact_form": {"urls": ["https://x.test/contact"],
                         "required_fields": ["email", ["message", "your message"]]},
        "docs_api": {"none_exists": True},
    },
}


def write(tmp_path, data):
    p = tmp_path / "site.json"
    p.write_text(json.dumps(data))
    return p


def mutated(fn):
    data = copy.deepcopy(GOOD)
    fn(data)
    return data


def test_valid_label_loads_and_is_normalised(tmp_path):
    lab = load_label(write(tmp_path, GOOD))
    assert lab.host == "x.test" and lab.site == "https://x.test"
    assert lab.tasks["main_info"].urls == ("x.test/pricing",)
    assert lab.tasks["contact_form"].required_fields == (("email",), ("message", "your message"))
    assert lab.tasks["docs_api"].none_exists is True and lab.tasks["docs_api"].urls == ()


def test_normalise_url_ignores_scheme_fragment_trailing_slash_and_host_case():
    assert normalise_url("HTTP://X.test/docs/#intro") == normalise_url("https://x.test/docs")
    assert normalise_url("https://x.test") == normalise_url("https://x.test/")
    assert normalise_url("https://x.test/a?b=1") != normalise_url("https://x.test/a")


@pytest.mark.parametrize("change,needle", [
    (lambda d: d["tasks"].pop("main_info"), "main_info"),
    (lambda d: d["tasks"].update(extra={"urls": ["https://x.test/"]}), "extra"),
    (lambda d: d["tasks"]["main_info"].update(urls=["https://evil.test/pricing"]), "not on x.test"),
    (lambda d: d["tasks"]["main_info"].update(urls=[]), "at least one URL"),
    (lambda d: d["tasks"]["docs_api"].update(urls=["https://x.test/docs"]), "none_exists"),
    (lambda d: d["tasks"]["main_info"].update(none_exists=True), "none_exists"),
    (lambda d: d["tasks"]["contact_form"].pop("required_fields"), "required_fields"),
    (lambda d: d["tasks"]["main_info"].update(required_fields=["email"]), "required_fields"),
    (lambda d: d.update(site="https://user:pw@x.test"), "user information"),
    (lambda d: d.update(tasks=[]), "tasks"),
])
def test_invalid_labels_are_rejected_with_a_safe_message(tmp_path, change, needle):
    with pytest.raises(ValueError) as e:
        load_label(write(tmp_path, mutated(change)))
    assert needle in str(e.value)
    assert "pw" not in str(e.value)               # never echo credentials


def test_unreadable_or_non_json_label_is_a_value_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{nope")
    with pytest.raises(ValueError):
        load_label(p)
    with pytest.raises(ValueError):
        load_label(tmp_path / "missing.json")


def test_tasks_and_prompt():
    assert set(TASKS) == set(TASK_IDS) == {"main_info", "contact_form", "docs_api"}
    assert all("{site}" in t.instruction for t in TASKS.values())
    p = system_prompt("x.test", STEP_LIMIT)
    assert "x.test" in p and str(STEP_LIMIT) in p and "untrusted" in p
