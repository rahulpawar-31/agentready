import pytest

from app.fixes.models import Fix, FixKind, FixStatus
from app.fixes.queue import FixQueue, InvalidTransition, NotApproved, UnknownFix, summary

SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"


def fix(kind=FixKind.LLMS_TXT, content="# Acme\n> hi\n", diff="--- /dev/null\n+++ b/llms.txt\n+# Acme\n"):
    return Fix(id=kind.value, kind=kind, path="/llms.txt", filename="llms.txt", description="d",
               content=content, diff=diff, finding_id="llms_txt")


def ok(_content):
    return []


def queue(*fixes, validators=None):
    return FixQueue(list(fixes) or [fix()], validators or {k: ok for k in FixKind})


def test_a_proposed_fix_cannot_be_exported():
    with pytest.raises(NotApproved):
        queue().export("llms_txt")


def test_approval_exports_nothing_and_the_summary_has_no_content_field():
    q = queue()
    result = q.approve("llms_txt")
    assert result.status is FixStatus.APPROVED
    s = summary(result)
    assert set(s) == {"id", "kind", "path", "filename", "description", "finding_id", "status", "diff", "diff_kind",
                     "warning", "note", "sha256"}
    assert s["status"] == "approved" and s["diff"] and len(s["sha256"]) == 64 and s["kind"] == "llms_txt"


def test_export_after_approval_returns_the_file_and_the_diff():
    q = queue()
    q.approve("llms_txt")
    b = q.export("llms_txt")
    assert (b.filename, b.content) == ("llms.txt", "# Acme\n> hi\n") and b.diff and len(b.sha256) == 64
    assert "has not changed your site" in b.note and "template" not in b.note
    assert b.fix_id == "llms_txt"


def test_the_json_ld_export_note_says_to_apply_the_snippet_to_the_template():
    q = queue(fix(FixKind.JSON_LD, content="<script>x</script>\n"))
    q.approve("json_ld")
    assert "template" in q.export("json_ld").note


def test_rejected_fixes_cannot_be_exported_or_reapproved():
    q = queue()
    q.reject("llms_txt")
    with pytest.raises(NotApproved):
        q.export("llms_txt")
    with pytest.raises(InvalidTransition):
        q.approve("llms_txt")
    assert q.reject("llms_txt").status is FixStatus.REJECTED        # rejecting again is a no-op


def test_revoking_an_approval_disables_export_and_clears_the_recorded_hash():
    q = queue()
    q.approve("llms_txt")
    q.reject("llms_txt")
    assert q.get("llms_txt").approved_sha256 == ""
    with pytest.raises(NotApproved):
        q.export("llms_txt")


def test_content_changed_after_approval_is_refused():
    q = queue()
    q.approve("llms_txt")
    q.get("llms_txt").content = "# Changed\n"
    with pytest.raises(NotApproved):
        q.export("llms_txt")


def test_export_revalidates_and_scans_content_and_diff_for_secrets():
    q = queue(fix(content=f"# Acme\n> key {SECRET}\n"))
    q.approve("llms_txt")
    with pytest.raises(NotApproved) as e:
        q.export("llms_txt")
    assert SECRET not in str(e.value)
    q_diff = queue(fix(diff=f"--- a/x\n+++ b/x\n+{SECRET}\n"))
    q_diff.approve("llms_txt")
    with pytest.raises(NotApproved):
        q_diff.export("llms_txt")
    q2 = queue(validators={FixKind.LLMS_TXT: lambda c: ["bad"]})
    q2.approve("llms_txt")
    with pytest.raises(NotApproved):
        q2.export("llms_txt")


def test_a_kind_with_no_validator_cannot_be_exported():
    q = FixQueue([fix()], {})
    q.approve("llms_txt")
    with pytest.raises(NotApproved):
        q.export("llms_txt")


def test_unknown_ids_idempotent_approval_and_listing_order():
    q = queue(fix(), fix(FixKind.JSON_LD, content="<script>x</script>\n"))
    for action in (q.approve, q.reject, q.export, q.get):
        with pytest.raises(UnknownFix):
            action("nope")
    q.approve("llms_txt")
    assert q.approve("llms_txt").status is FixStatus.APPROVED
    assert [f.id for f in q.list()] == ["llms_txt", "json_ld"]


def test_one_fixs_approval_does_not_approve_another():
    q = queue(fix(), fix(FixKind.JSON_LD, content="<script>x</script>\n"))
    q.approve("llms_txt")
    with pytest.raises(NotApproved):
        q.export("json_ld")


def test_duplicate_fix_ids_are_refused():
    with pytest.raises(ValueError):
        FixQueue([fix(), fix()], {})


def test_b_export_scans_the_content_and_all_diff_lines_including_context():
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJyb2xlIjoiYW5vbiJ9." + "c2lnbmF0dXJlMTIzNDU2"
    context = fix(content="# Acme\n> hi\n")
    context.diff = f"--- a/x\n+++ b/x\n@@ -1,2 +1,3 @@\n var k = '{jwt}';\n+# Acme\n </head>\n"
    context.added = "# Acme"
    q = queue(context)
    q.approve("llms_txt")
    with pytest.raises(NotApproved, match="secret-like"):
        q.export("llms_txt")
    added = fix(content="# Acme\n> hi\n")
    added.diff = f"--- a/x\n+++ b/x\n@@ -1,1 +1,2 @@\n+key {'AKIA' + 'ABCDEFGHIJKLMNOP'}\n"
    added.added = "key " + "AKIA" + "ABCDEFGHIJKLMNOP"
    q2 = queue(added)
    q2.approve("llms_txt")
    with pytest.raises(NotApproved):
        q2.export("llms_txt")
