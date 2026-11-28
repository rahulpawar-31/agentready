"""The approval queue: the only place an export is built.

States: proposed -> approved, proposed -> rejected, approved -> rejected (a revoked approval). A rejected fix can
not be approved again. Approval records the content hash and exports nothing; export re-checks everything."""
import hashlib

from .models import ExportBundle, Fix, FixKind, FixStatus
from .secrets import added_lines, find_secrets


class UnknownFix(KeyError):
    pass


class NotApproved(Exception):
    pass


class InvalidTransition(Exception):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summary(fix: Fix) -> dict:
    """What a reviewer sees: the diff and the status, with no separate `content` field. For a new file the diff
    already shows the whole file (review needs that); the export is the download or copy action, and it is the part
    that is only possible after approval."""
    return {"id": fix.id, "kind": fix.kind.value, "path": fix.path, "filename": fix.filename,
            "description": fix.description, "finding_id": fix.finding_id, "status": fix.status.value,
            "diff": fix.diff, "diff_kind": fix.diff_kind, "warning": fix.warning, "note": fix.note,
            "sha256": _sha(fix.content)}


class FixQueue:
    def __init__(self, fixes: list, validators: dict):
        self._fixes: dict = {}
        for fix in fixes:
            if fix.id in self._fixes:
                raise ValueError(f"duplicate fix id {fix.id}")
            self._fixes[fix.id] = fix
        self._validators = dict(validators)

    def list(self) -> list:
        return list(self._fixes.values())

    def get(self, fix_id: str) -> Fix:
        try:
            return self._fixes[fix_id]
        except KeyError:
            raise UnknownFix(fix_id) from None

    def approve(self, fix_id: str) -> Fix:
        fix = self.get(fix_id)
        if fix.status is FixStatus.APPROVED:
            return fix
        if fix.status is FixStatus.REJECTED:
            raise InvalidTransition("a rejected fix cannot be approved again; the only way back is a new audit")
        fix.status = FixStatus.APPROVED
        fix.approved_sha256 = _sha(fix.content)
        return fix

    def reject(self, fix_id: str) -> Fix:
        fix = self.get(fix_id)
        fix.status = FixStatus.REJECTED
        fix.approved_sha256 = ""
        return fix

    def export(self, fix_id: str) -> ExportBundle:
        fix = self.get(fix_id)
        if fix.status is not FixStatus.APPROVED:
            raise NotApproved("Approve this fix before exporting it")
        digest = _sha(fix.content)
        if not fix.approved_sha256 or digest != fix.approved_sha256:
            raise NotApproved("The fix changed after it was approved; approve it again")
        validator = self._validators.get(fix.kind)
        if validator is None:
            raise NotApproved("No validator is available for this fix")
        problems = validator(fix.content)
        if problems:
            raise NotApproved(f"The fix no longer passes validation: {problems[0]}")
        added = fix.added if fix.added is not None else added_lines(fix.diff)
        labels = find_secrets(fix.content) or find_secrets(fix.diff) or find_secrets(added)
        if labels:
            raise NotApproved(f"The fix contains a secret-like value ({labels[0]})")
        note = "Review before you deploy. AgentReady has not changed your site."
        if fix.kind is FixKind.JSON_LD:
            note += " Apply the snippet to your page template; the diff is against the HTML as fetched."
        return ExportBundle(fix.id, fix.filename, fix.content, fix.diff, digest, note)
