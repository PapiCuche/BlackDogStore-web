"""
Internal document numbering — SaaS Phase 2E.

WHAT THIS REPLACES
------------------
`SalesNote.number` used to be allocated like this:

    last = SalesNote.objects.aggregate(Max('number'))['number__max']
    nxt  = int(last[len(prefix):]) + 1

Four things were wrong with it, and only the first is obvious:

  1. GLOBAL SCOPE. Company A issued NV-000001, company B took NV-000002, company
     A got NV-000003. Every tenant saw gaps caused by strangers.
  2. THE COUNTER WAS A PARSED STRING. Changing the prefix or the padding changed
     what "the last number" meant, so a configuration edit could hand out a
     number that had already been used.
  3. THE RACE WAS CAUGHT, NOT PREVENTED. Two concurrent issuances both read the
     same MAX and relied on a unique constraint to reject one of them with an
     IntegrityError — a 500 dressed as concurrency control.
  4. IT COULD NOT EXPRESS SERIES. One counter for the whole installation cannot
     represent "per branch", and there was nowhere to put the intent.

Now a counter is a row, a number is an integer, and allocation takes a lock.

THE THREE WORDS, KEPT APART
---------------------------
    InternalSequence.next_value   the next ordinal this series will hand out
    SalesNote.sequence_value      the ordinal a document received
    SalesNote.number              the string that was printed on it

The first moves. The second and third never do.

LOCK ORDER — Order, then Sequence
---------------------------------
Every path that issues a document locks the ORDER first and the SEQUENCE second.
Nothing anywhere locks them the other way round, which is what makes the
ordering worth stating: two issuances for different orders in the same series
queue on the sequence row, and two issuances for the same order queue on the
order row and then find the note already exists.

Locking `CompanySettings` instead of the series row — the tempting shortcut,
since the scope lives there — would serialise every branch of a company behind
every other and block the company's whole configuration for the duration of a
PDF number. See §85 of the phase brief.

THESE ARE INTERNAL NUMBERS
--------------------------
Not SUNAT series, not fiscal numbering, no tax validity. Nothing in this module
should ever be reused to mean otherwise without the fiscal work that implies.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .models import Branch, CompanySettings, InternalSequence

# What a company starts with. Configurable from the first moment — this is the
# value provisioning writes into the row, not a constant any allocation reads.
DEFAULT_PREFIX = 'NV-'
DEFAULT_PADDING = 6


class SequenceError(Exception):
    """A numbering rule was broken. Views map this to HTTP 400."""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def sequence_scope(company, document_type=InternalSequence.DOCUMENT_SALES_NOTE) -> str:
    """
    Whether `company` numbers per company or per branch.

    Read from `CompanySettings`, never inferred from which rows exist: a branch
    row lying around cannot tell us whether somebody chose branch scope or
    changed their mind and left it behind.
    """
    settings_row = getattr(company, 'settings', None)
    if settings_row is None:
        return CompanySettings.SEQUENCE_SCOPE_COMPANY
    return settings_row.sales_note_sequence_scope


def company_sequence(company, document_type=InternalSequence.DOCUMENT_SALES_NOTE):
    """The company-level series, or None. Always exists after provisioning."""
    if company is None:
        return None
    return InternalSequence.objects.filter(
        company=company, branch__isnull=True, document_type=document_type,
    ).first()


def ensure_company_sequence(
    company,
    document_type=InternalSequence.DOCUMENT_SALES_NOTE,
    *,
    prefix: str = DEFAULT_PREFIX,
    padding: int = DEFAULT_PADDING,
) -> InternalSequence:
    """
    The company-level series, created if absent. IDEMPOTENT.

    This row is load-bearing beyond its own counter: under branch scope it is the
    TEMPLATE new branch series copy their prefix and padding from, which is why
    every company has one regardless of the scope it uses. Putting those defaults
    in `CompanySettings` instead would create a second home for a value that is
    copied once and then drifts.
    """
    sequence, _created = InternalSequence.objects.get_or_create(
        company=company, branch=None, document_type=document_type,
        defaults={'prefix': prefix, 'padding': padding, 'next_value': 1},
    )
    return sequence


def ensure_branch_sequence(company, branch, document_type=InternalSequence.DOCUMENT_SALES_NOTE):
    """
    The series for one branch, created on demand from the company template.

    LAZY, AND THAT IS A CHOICE. Creating a row for every branch up front would
    leave a company that never switches to branch scope carrying one unused
    counter per shop, and would need a hook on branch creation that could be
    missed. Creating it at the moment of first use cannot be missed.

    The race it opens — two simultaneous first issuances in the same new branch —
    is closed by `unique_branch_sequence_per_document`: the loser gets an
    IntegrityError and re-reads the winner's row.

    New branch series start at 1. A branch's numbering is its own; continuing the
    company's count would make NV-000051 the first note of a shop that has issued
    none.
    """
    if branch is None:
        raise SequenceError('Se requiere una sucursal para una serie por sucursal.')
    if branch.company_id != company.pk:
        raise SequenceError('La sucursal no pertenece a esta empresa.')

    existing = InternalSequence.objects.filter(
        company=company, branch=branch, document_type=document_type,
    ).first()
    if existing is not None:
        return existing

    template = ensure_company_sequence(company, document_type)
    try:
        return InternalSequence.objects.create(
            company=company, branch=branch, document_type=document_type,
            prefix=template.prefix, padding=template.padding, next_value=1,
        )
    except IntegrityError:
        # Lost the race; the winner's row is the answer.
        return InternalSequence.objects.get(
            company=company, branch=branch, document_type=document_type,
        )


def resolve_sequence_for_order(
    order, document_type=InternalSequence.DOCUMENT_SALES_NOTE,
) -> InternalSequence:
    """
    Which series must number a document for `order`.

    THE BRANCH IS DERIVED, NEVER CHOSEN. It comes from
    `order.fulfillment_branch` — the branch that sold the order, decided at
    checkout in Phase 2D. Letting a caller name a branch would let somebody pick
    whichever series gives them the number they want.

    Under branch scope with an order that has no fulfillment branch (possible for
    orders that predate 2D), this falls back to the COMPANY series rather than
    refusing: the note must be issuable, and the company series is the one that
    order's numbering already belonged to.
    """
    company = order.company
    if company is None:
        raise SequenceError('El pedido no pertenece a ninguna empresa.')

    if sequence_scope(company, document_type) == CompanySettings.SEQUENCE_SCOPE_BRANCH:
        branch = order.fulfillment_branch
        if branch is not None:
            return ensure_branch_sequence(company, branch, document_type)

    return ensure_company_sequence(company, document_type)


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

def allocate(sequence: InternalSequence) -> tuple[int, str]:
    """
    Take the next ordinal from `sequence` and return `(value, formatted)`.

    MUST be called inside a transaction that will also write the document. The
    two belong together: if the document fails to save, the transaction rolls
    back and the number is NOT consumed. That is the right trade for an internal
    document — a gap in an internal series costs nothing, while handing out a
    number and losing the document that justified it is unexplainable later.

    The row is re-read under `select_for_update()` rather than trusted from the
    caller's instance, so two concurrent allocations serialise here instead of
    both reading the same value and colliding downstream.

    Only THIS row is locked. Another company's series, and another branch's
    series, are untouched.
    """
    if not transaction.get_connection().in_atomic_block:
        raise SequenceError(
            'allocate() debe llamarse dentro de una transacción: el número y el '
            'documento tienen que confirmarse o descartarse juntos.'
        )

    locked = InternalSequence.objects.select_for_update().get(pk=sequence.pk)
    if not locked.is_active:
        raise SequenceError(
            'La serie está desactivada y no puede emitir números nuevos.'
        )

    value = locked.next_value
    formatted = locked.format(value)

    locked.next_value = value + 1
    locked.save(update_fields=['next_value', 'updated_at'])

    return value, formatted


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def can_change_scope(company, document_type=InternalSequence.DOCUMENT_SALES_NOTE) -> bool:
    """
    Whether `company` may still switch between company and branch numbering.

    ONLY BEFORE THE FIRST DOCUMENT. Switching afterwards is not a data-integrity
    problem — the series constraint keeps every ordinal unique — but it is a
    legibility one: a company that has issued NV-000001..NV-000050 at company
    scope and then switches would see its next branch note numbered NV-000001
    again. Two documents of one business showing the same identifier is exactly
    what an internal correlativo exists to prevent, even though the database is
    perfectly happy.

    Migrating a scope properly means deciding what happens to the numbers already
    out there, and that decision needs a business answer this phase does not
    have. Blocked, and recorded as PENDING in docs/saas-multiempresa.md.
    """
    return not InternalSequence.objects.filter(
        company=company, document_type=document_type, next_value__gt=1,
    ).exists()


def can_edit_next_value(sequence: InternalSequence) -> bool:
    """
    Whether the counter may still be set by hand.

    ONLY BEFORE THE FIRST ISSUANCE — which is what makes the feature useful: a
    business migrating from another system starts at 5001 instead of 1. Once a
    number has been handed out, the counter is read-only, because moving it
    backwards re-issues identifiers that are already on documents somebody holds.
    """
    return not sequence.has_issued
