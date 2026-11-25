"""
Who may read a staged import: the branches a job touches and the caller's reach (SEC-IMPORT-01, review of PR #236).

Neutral ground for the import views and the services that ask the same question (IMPORT-TERMS-APPROVAL asks it of a
job's approver when the load is registered), so neither has to import the other.
"""
from __future__ import annotations

from .models import BulkImportJob
from .tenancy import has_company_wide_scope, visible_branches


def _job_branch_ids(job):
    """
    The branches a job touches, as its snapshot recorded them at preview.

    `None` when the snapshot cannot say — not a mapping, a branch map that is
    missing, empty or not a map, an id that is not a number. That FAILS CLOSED
    (review of PR #236): a job whose branches are unknown is a job of the whole
    company, and only somebody holding every branch reaches it. An empty set is
    a job that names no branch by design (a product load without old
    quantities, a load of pictures).
    """
    if job.import_type not in (BulkImportJob.UNITS, BulkImportJob.STOCK, BulkImportJob.PRODUCTS):
        return set()
    snapshot = job.mapping_snapshot
    if not isinstance(snapshot, dict):
        return None
    if job.import_type == BulkImportJob.UNITS:
        raw = snapshot.get('branch_ids') or []
        if not isinstance(raw, (list, tuple)):
            return None
    elif job.import_type == BulkImportJob.STOCK:
        # A stock load always names its branches: the preview refuses one that does not.
        raw = snapshot.get('branch_map')
        if not isinstance(raw, dict) or not raw:
            return None
        raw = raw.values()
    else:
        # MIGRATION-B — a historical load assigns old quantities to branches.
        raw = snapshot.get('quantity_branches') or {}
        if not isinstance(raw, dict):
            return None
        raw = raw.values()
    branch_ids = set()
    for value in raw:
        if isinstance(value, bool):
            return None
        try:
            branch_ids.add(int(value))
        except (TypeError, ValueError):
            return None
    return branch_ids


class _Reach:
    """
    The caller's branch scope inside one company, asked ONCE per request.

    A job exists for the caller only inside the branches they may operate in.
    A job of DEVICES carries their serials and IMEIs, row by row; a job of
    STOCK carries a branch's name, its shelf as it stood and who counted it; a
    historical PRODUCT job, the old quantities of each branch it was given.
    Those are readable elsewhere only inside the caller's branches, so a
    staged file is too (SEC-IMPORT-01): a job that touches a branch out of the
    caller's reach is not there for them — 404 on its detail, its report and
    its apply, absent from the history. A product job that names no branch is
    the catalogue's, and keeps the rule it always had.

    Both answers are computed lazily and kept, so the history asks them once
    for fifty jobs, not fifty times.
    """

    def __init__(self, user, company):
        self.user = user
        self.company = company
        self._company_wide = None
        self._reachable = None

    @property
    def company_wide(self) -> bool:
        if self._company_wide is None:
            self._company_wide = has_company_wide_scope(self.user, self.company)
        return self._company_wide

    @property
    def reachable(self) -> set:
        if self._reachable is None:
            self._reachable = set(visible_branches(self.user, self.company).values_list('pk', flat=True))
        return self._reachable

    def allows(self, job) -> bool:
        wanted = _job_branch_ids(job)
        if wanted is None:
            return self.company_wide
        if job.import_type == BulkImportJob.UNITS:
            # A device job that names no branch is its uploader's (the rule it always had).
            if not wanted:
                return job.created_by_id == self.user.pk
            return wanted <= self.reachable
        if not wanted:
            return True
        if self.company_wide:
            # The whole company, as before: a branch closed since the load does
            # not hide its history from somebody who holds every branch.
            return True
        return wanted <= self.reachable


def _job_within_reach(user, company, job) -> bool:
    """Whether `job` exists for `user` (see `_Reach`)."""
    return _Reach(user, company).allows(job)
